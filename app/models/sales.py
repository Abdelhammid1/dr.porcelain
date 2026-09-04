"""فواتير المبيعات ومرتجعاتها.

Phase 1: كاش فقط (دفع فوري لحظة الفاتورة). التقسيط في Phase 3.

نُخزِّن snapshot للتكلفة (unit_cost) في كل سطر عند البيع حتى لا يتأثر COGS
بتغيّر متوسط التكلفة لاحقًا.
"""
from __future__ import annotations

import enum
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.extensions import db
from app.models.base import TimestampMixin


MONEY = Numeric(18, 3)
QTY = Numeric(18, 3)


class InvoiceStatus(str, enum.Enum):
    POSTED = "posted"
    RETURNED = "returned"       # مرتجع كامل
    PARTIAL_RETURNED = "partial_returned"  # مرتجع جزئي
    CANCELLED = "cancelled"     # ملغاة قبل الترحيل (نادرة - Phase 1 لا يوجد draft)


class PaymentMethod(str, enum.Enum):
    CASH = "cash"           # نقدي (Phase 1)
    BANK = "bank"           # تحويل بنكي
    CARD = "card"           # بطاقة (Phase 2 POS)
    WALLET = "wallet"       # محفظة إلكترونية (Phase 2 POS)


class SalesInvoice(db.Model, TimestampMixin):
    __tablename__ = "sales_invoices"

    id = Column(Integer, primary_key=True)
    doc_number = Column(String(40), unique=True, nullable=False, index=True)  # INV-2026-000001
    invoice_date = Column(Date, nullable=False, index=True)

    customer_id = Column(Integer, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False, index=True)
    payment_method = Column(
        Enum(PaymentMethod, name="payment_method", values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=PaymentMethod.CASH,
    )

    # المبالغ (كلها snapshots للحفاظ على تاريخية الفاتورة)
    subtotal = Column(MONEY, nullable=False, default=0)      # مجموع أسعار البنود قبل الخصم
    discount_amount = Column(MONEY, nullable=False, default=0)  # خصم على مستوى الفاتورة
    tax_rate = Column(MONEY, nullable=False, default=0)      # نسبة الضريبة وقت الفاتورة
    tax_amount = Column(MONEY, nullable=False, default=0)    # قيمة الضريبة المحسوبة
    total = Column(MONEY, nullable=False, default=0)         # الإجمالي النهائي (المدفوع)

    status = Column(
        Enum(InvoiceStatus, name="invoice_status", values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=InvoiceStatus.POSTED, index=True,
    )

    notes = Column(Text, nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # لو الفاتورة من ورديّة POS، تُربط هنا (nullable — الفاتورة العادية لا تحتاج).
    pos_session_id = Column(Integer, ForeignKey("pos_sessions.id"), nullable=True, index=True)

    # لو الفاتورة تقسيط، تُربط بخطة (one-to-one عبر InstallmentPlan.sales_invoice_id).
    # نستعلم عن الخطة عبر backref لأن InstallmentPlan يحمل FK.

    # --- علاقات ---
    customer = relationship("Party", lazy="joined")
    lines = relationship(
        "SalesInvoiceLine", back_populates="invoice",
        cascade="all, delete-orphan", lazy="selectin",
        order_by="SalesInvoiceLine.id",
    )
    returns = relationship(
        "SalesReturn", back_populates="invoice",
        cascade="all, delete-orphan", lazy="selectin",
    )
    created_by = relationship("User")

    @property
    def total_returned_qty_per_line(self) -> dict[int, Decimal]:
        """كمية المرتجع لكل سطر (line_id → qty)."""
        result: dict[int, Decimal] = {}
        for r in self.returns:
            for rl in r.lines:
                result[rl.invoice_line_id] = result.get(rl.invoice_line_id, Decimal("0")) + Decimal(str(rl.qty))
        return result

    def line_returnable_qty(self, line: "SalesInvoiceLine") -> Decimal:
        returned = self.total_returned_qty_per_line.get(line.id, Decimal("0"))
        return Decimal(str(line.qty)) - returned

    def __repr__(self) -> str:
        return f"<SalesInvoice {self.doc_number} status={self.status.value} total={self.total}>"


class SalesInvoiceLine(db.Model):
    __tablename__ = "sales_invoice_lines"

    id = Column(Integer, primary_key=True)
    invoice_id = Column(
        Integer, ForeignKey("sales_invoices.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    variant_id = Column(
        Integer, ForeignKey("product_variants.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )

    # snapshots — لا تتغير مطلقًا بعد الحفظ
    product_name = Column(String(240), nullable=False)   # اسم المنتج + اللون وقت البيع
    sku = Column(String(80), nullable=False)
    qty = Column(QTY, nullable=False)
    unit_price = Column(MONEY, nullable=False)           # سعر البيع لكل وحدة
    line_total = Column(MONEY, nullable=False)           # qty × unit_price
    unit_cost = Column(MONEY, nullable=False)            # snapshot لمتوسط التكلفة وقت البيع (لـ COGS)

    invoice = relationship("SalesInvoice", back_populates="lines")
    variant = relationship("ProductVariant")

    def __repr__(self) -> str:
        return f"<SalesLine {self.sku} qty={self.qty} price={self.unit_price}>"


# ========================================================
# المرتجعات (Sales Returns) — تُنشأ في epic 1.4 US-1.4.3
# ========================================================

class SalesReturn(db.Model, TimestampMixin):
    __tablename__ = "sales_returns"

    id = Column(Integer, primary_key=True)
    doc_number = Column(String(40), unique=True, nullable=False, index=True)  # SR-2026-000001
    return_date = Column(Date, nullable=False, index=True)

    invoice_id = Column(
        Integer, ForeignKey("sales_invoices.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )

    reason = Column(Text, nullable=False)
    refund_amount = Column(MONEY, nullable=False, default=0)   # الإجمالي المُسترَد للعميل
    refund_tax = Column(MONEY, nullable=False, default=0)      # ضريبة المسترد
    is_full_return = Column(Boolean, nullable=False, default=False)

    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    invoice = relationship("SalesInvoice", back_populates="returns")
    lines = relationship(
        "SalesReturnLine", back_populates="return_doc",
        cascade="all, delete-orphan", lazy="selectin",
    )
    created_by = relationship("User")

    def __repr__(self) -> str:
        return f"<SalesReturn {self.doc_number} invoice={self.invoice_id}>"


class SalesReturnLine(db.Model):
    __tablename__ = "sales_return_lines"

    id = Column(Integer, primary_key=True)
    return_id = Column(
        Integer, ForeignKey("sales_returns.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    invoice_line_id = Column(
        Integer, ForeignKey("sales_invoice_lines.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    variant_id = Column(
        Integer, ForeignKey("product_variants.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    qty = Column(QTY, nullable=False)
    unit_price = Column(MONEY, nullable=False)    # نفس سعر السطر الأصلي
    unit_cost = Column(MONEY, nullable=False)     # نفس التكلفة الأصلية

    return_doc = relationship("SalesReturn", back_populates="lines")
    invoice_line = relationship("SalesInvoiceLine")
    variant = relationship("ProductVariant")
