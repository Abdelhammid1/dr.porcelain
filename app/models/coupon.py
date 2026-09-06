"""أكواد الخصم (كوبونات) — Ticket 2 Epic 2.

الأنواع:
- percentage: نسبة مئوية من subtotal (مثال 10 = 10%)
- fixed_amount: مبلغ ثابت بالجنيه

قواعد التطبيق:
- code (index, unique) — case-insensitive في الفحص
- valid_from / valid_until — نافذة زمنية اختيارية
- min_order_amount — حد أدنى لقيمة الطلب
- max_uses (اختياري) — إجمالي الاستخدام الكلي عبر كل العملاء
- max_uses_per_customer (default=1) — لنفس العميل

**قاعدة ذهبية:** الخصم الناتج يتحفظ في `Order.discount_amount` ثم ينتقل تلقائيًا
لـ `SalesInvoice.discount_amount` (لا نلمس طبقة `sales.py` أو `order_accounting.py`).
"""
from __future__ import annotations

import enum
from decimal import Decimal

from sqlalchemy import (
    Boolean, Column, DateTime, Enum, ForeignKey, Integer, Numeric, String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.extensions import db
from app.models.base import TimestampMixin


MONEY = Numeric(18, 3)


class DiscountType(str, enum.Enum):
    PERCENTAGE = "percentage"
    FIXED_AMOUNT = "fixed_amount"


class DiscountCoupon(db.Model, TimestampMixin):
    __tablename__ = "discount_coupons"

    id = Column(Integer, primary_key=True)
    code = Column(String(60), unique=True, nullable=False, index=True)

    discount_type = Column(
        Enum(DiscountType, name="discount_type",
             values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=DiscountType.PERCENTAGE,
    )
    discount_value = Column(MONEY, nullable=False, default=0)

    min_order_amount = Column(MONEY, nullable=True)
    max_uses = Column(Integer, nullable=True)  # None = بدون سقف كلي
    max_uses_per_customer = Column(Integer, nullable=True, default=1)

    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_until = Column(DateTime(timezone=True), nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)

    usages = relationship("CouponUsage", back_populates="coupon",
                          cascade="all, delete-orphan", lazy="selectin")

    def __repr__(self) -> str:
        return f"<Coupon {self.code} {self.discount_type.value}={self.discount_value}>"


class CouponUsage(db.Model, TimestampMixin):
    """سجل واحد لكل استخدام فعلي لكوبون على طلب."""
    __tablename__ = "coupon_usages"
    __table_args__ = (
        UniqueConstraint("coupon_id", "order_id", name="uq_coupon_order"),
    )

    id = Column(Integer, primary_key=True)
    coupon_id = Column(
        Integer, ForeignKey("discount_coupons.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    order_id = Column(
        Integer, ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    customer_id = Column(
        Integer, ForeignKey("parties.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    discount_amount = Column(MONEY, nullable=False, default=0)

    coupon = relationship("DiscountCoupon", back_populates="usages")
    order = relationship("Order")
    customer = relationship("Party")
