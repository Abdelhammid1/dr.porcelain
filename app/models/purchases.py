"""فواتير المشتريات — تشبه المبيعات لكن بإتجاه معاكس (المخزون يزيد + متوسط التكلفة يُعاد حسابه).

Phase 1: 3 طرق دفع (نقدي فوري / بنك فوري / آجل بالكامل).
Phase 4 يُضاف: جدول دفعات مرن (Vendor Payment Schedule).

المرتجعات: تقليل رصيد المخزون + سداد مورد / تخفيض ذمة عليه.
"""
from __future__ import annotations

import enum
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Column,
    Date,
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


class PurchasePayment(str, enum.Enum):
    CASH = "cash"           # نقدي فوري
    BANK = "bank"           # بنك فوري
    CREDIT = "credit"       # آجل بالكامل


class PurchaseStatus(str, enum.Enum):
    POSTED = "posted"
    RETURNED = "returned"
    PARTIAL_RETURNED = "partial_returned"


class PurchaseInvoice(db.Model, TimestampMixin):
    __tablename__ = "purchase_invoices"

    id = Column(Integer, primary_key=True)
    doc_number = Column(String(40), unique=True, nullable=False, index=True)  # PB-2026-000001
    invoice_date = Column(Date, nullable=False, index=True)

    vendor_id = Column(Integer, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False, index=True)
    payment_method = Column(
        Enum(PurchasePayment, name="purchase_payment", values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=PurchasePayment.CREDIT,
    )

    # حساب البنك المستخدم (فقط عند payment_method=BANK) - fk اختياري لحساب فرعي من 1020
    bank_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)

    # المبالغ
    subtotal = Column(MONEY, nullable=False, default=0)
    discount_amount = Column(MONEY, nullable=False, default=0)
    tax_rate = Column(MONEY, nullable=False, default=0)
    tax_amount = Column(MONEY, nullable=False, default=0)
    freight = Column(MONEY, nullable=False, default=0)  # شحن — يضاف على تكلفة المخزون
    total = Column(MONEY, nullable=False, default=0)

    # رقم فاتورة المورد الأصلي (المطبوعة من عندهم — للأرشفة)
    vendor_ref = Column(String(80), nullable=True)

    status = Column(
        Enum(PurchaseStatus, name="purchase_status", values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=PurchaseStatus.POSTED, index=True,
    )

    notes = Column(Text, nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    vendor = relationship("Party", lazy="joined")
    bank_account = relationship("Account", foreign_keys=[bank_account_id])
    lines = relationship(
        "PurchaseInvoiceLine", back_populates="invoice",
        cascade="all, delete-orphan", lazy="selectin",
        order_by="PurchaseInvoiceLine.id",
    )
    returns = relationship(
        "PurchaseReturn", back_populates="invoice",
        cascade="all, delete-orphan", lazy="selectin",
    )
    created_by = relationship("User")

    @property
    def total_returned_qty_per_line(self) -> dict[int, Decimal]:
        result: dict[int, Decimal] = {}
        for r in self.returns:
            for rl in r.lines:
                result[rl.invoice_line_id] = result.get(rl.invoice_line_id, Decimal("0")) + Decimal(str(rl.qty))
        return result

    def line_returnable_qty(self, line: "PurchaseInvoiceLine") -> Decimal:
        returned = self.total_returned_qty_per_line.get(line.id, Decimal("0"))
        return Decimal(str(line.qty)) - returned

    def __repr__(self) -> str:
        return f"<PurchaseInvoice {self.doc_number} {self.payment_method.value} total={self.total}>"


class PurchaseInvoiceLine(db.Model):
    __tablename__ = "purchase_invoice_lines"

    id = Column(Integer, primary_key=True)
    invoice_id = Column(
        Integer, ForeignKey("purchase_invoices.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    variant_id = Column(
        Integer, ForeignKey("product_variants.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )

    product_name = Column(String(240), nullable=False)
    sku = Column(String(80), nullable=False)
    qty = Column(QTY, nullable=False)
    unit_cost = Column(MONEY, nullable=False)   # سعر التكلفة على الفاتورة
    line_total = Column(MONEY, nullable=False)  # qty × unit_cost

    invoice = relationship("PurchaseInvoice", back_populates="lines")
    variant = relationship("ProductVariant")


# ========================================================
# المرتجعات (Purchase Returns)
# ========================================================

class PurchaseReturn(db.Model, TimestampMixin):
    __tablename__ = "purchase_returns"

    id = Column(Integer, primary_key=True)
    doc_number = Column(String(40), unique=True, nullable=False, index=True)  # PR-2026-000001
    return_date = Column(Date, nullable=False, index=True)

    invoice_id = Column(
        Integer, ForeignKey("purchase_invoices.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    reason = Column(Text, nullable=False)
    refund_amount = Column(MONEY, nullable=False, default=0)
    refund_tax = Column(MONEY, nullable=False, default=0)
    is_full_return = Column(Boolean, nullable=False, default=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    invoice = relationship("PurchaseInvoice", back_populates="returns")
    lines = relationship(
        "PurchaseReturnLine", back_populates="return_doc",
        cascade="all, delete-orphan", lazy="selectin",
    )
    created_by = relationship("User")


class PurchaseReturnLine(db.Model):
    __tablename__ = "purchase_return_lines"

    id = Column(Integer, primary_key=True)
    return_id = Column(Integer, ForeignKey("purchase_returns.id", ondelete="CASCADE"), nullable=False, index=True)
    invoice_line_id = Column(Integer, ForeignKey("purchase_invoice_lines.id", ondelete="RESTRICT"), nullable=False, index=True)
    variant_id = Column(Integer, ForeignKey("product_variants.id", ondelete="RESTRICT"), nullable=False, index=True)
    qty = Column(QTY, nullable=False)
    unit_cost = Column(MONEY, nullable=False)

    return_doc = relationship("PurchaseReturn", back_populates="lines")
    invoice_line = relationship("PurchaseInvoiceLine")
    variant = relationship("ProductVariant")
