"""نموذج الطلبات الأونلاين (Storefront Orders).

Phase 6: طلب أونلاين يُنشأ من المتجر الإلكتروني — يخصم المخزون فورًا لكنه لا يولد
قيود محاسبية حتى يصل لحالة "تم التسليم" (هذا مربوط في Phase 8 عبر الـ integration).

الحالات:
- PENDING: تم إنشاء الطلب، لم يبدأ التجهيز
- PROCESSING: قيد التجهيز
- SHIPPED: تم الشحن
- DELIVERED: تم التسليم → يُطلق hook لإنشاء SalesInvoice (Phase 8)
- CANCELLED: ملغى (يُعيد المخزون)
- RETURNED: مرتجع بعد التسليم

طرق الدفع الحالية: COD (الدفع عند الاستلام) فقط في Phase 6.
"""
from __future__ import annotations

import enum
from decimal import Decimal

from sqlalchemy import (
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


class OrderStatus(str, enum.Enum):
    PENDING = "pending"          # قيد المعالجة
    PROCESSING = "processing"    # قيد التجهيز
    SHIPPED = "shipped"          # تم الشحن
    DELIVERED = "delivered"      # تم التسليم (يُطلق قيد المحاسبة)
    CANCELLED = "cancelled"      # ملغى
    RETURNED = "returned"        # مرتجع


class OrderPaymentMethod(str, enum.Enum):
    COD = "cod"                  # الدفع عند الاستلام
    CARD = "card"                # بطاقة (Phase 8+)
    WALLET = "wallet"            # محفظة (Phase 8+)


class Order(db.Model, TimestampMixin):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)
    doc_number = Column(String(40), unique=True, nullable=False, index=True)  # ORD-2026-000001
    order_date = Column(Date, nullable=False, index=True)

    # لو العميل مسجّل مسبقًا نحفظ الربط، وإلا نحفظ بياناته كضيف
    customer_id = Column(Integer, ForeignKey("parties.id"), nullable=True, index=True)
    guest_name = Column(String(200), nullable=True)
    guest_phone = Column(String(32), nullable=True, index=True)
    guest_email = Column(String(160), nullable=True)

    # طريقة الدفع
    payment_method = Column(
        Enum(OrderPaymentMethod, name="order_payment",
             values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=OrderPaymentMethod.COD,
    )

    # عنوان الشحن
    shipping_address = Column(Text, nullable=False)
    shipping_city = Column(String(80), nullable=True)
    shipping_notes = Column(Text, nullable=True)

    # المبالغ (snapshots)
    subtotal = Column(MONEY, nullable=False, default=0)
    discount_amount = Column(MONEY, nullable=False, default=0)
    tax_rate = Column(MONEY, nullable=False, default=0)
    tax_amount = Column(MONEY, nullable=False, default=0)
    shipping_fee = Column(MONEY, nullable=False, default=0)
    total = Column(MONEY, nullable=False, default=0)

    status = Column(
        Enum(OrderStatus, name="order_status",
             values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=OrderStatus.PENDING, index=True,
    )

    # لو الطلب تحوّل لفاتورة (Phase 8), نحفظ الربط هنا
    sales_invoice_id = Column(Integer, ForeignKey("sales_invoices.id"), nullable=True)

    notes = Column(Text, nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)

    customer = relationship("Party", lazy="joined")
    sales_invoice = relationship("SalesInvoice")
    lines = relationship(
        "OrderLine", back_populates="order",
        cascade="all, delete-orphan", lazy="selectin",
        order_by="OrderLine.id",
    )

    @property
    def customer_display_name(self) -> str:
        if self.customer:
            return self.customer.name_ar
        return self.guest_name or "عميل نقدي"

    @property
    def customer_phone(self) -> str:
        if self.customer:
            return self.customer.phone or self.guest_phone or ""
        return self.guest_phone or ""

    def __repr__(self) -> str:
        return f"<Order {self.doc_number} {self.status.value} total={self.total}>"


class OrderLine(db.Model):
    __tablename__ = "order_lines"

    id = Column(Integer, primary_key=True)
    order_id = Column(
        Integer, ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    variant_id = Column(
        Integer, ForeignKey("product_variants.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )

    product_name = Column(String(240), nullable=False)
    sku = Column(String(80), nullable=False)
    qty = Column(QTY, nullable=False)
    unit_price = Column(MONEY, nullable=False)
    line_total = Column(MONEY, nullable=False)

    order = relationship("Order", back_populates="lines")
    variant = relationship("ProductVariant")
