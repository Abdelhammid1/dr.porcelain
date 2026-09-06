"""نقاط الولاء (Ticket 3 Epic 10).

سجل موحّد للنقاط المكتسبة والمستبدلة لكل عميل. الرصيد الحالي = SUM(points).

**ملاحظة معمارية:** نقاط الولاء عرض فقط — لا تُنشئ قيدًا محاسبيًا مستقلاً.
لو تم استبدال النقاط في checkout، تُطبَّق كخصم عادي في `Order.discount_amount`
(بنفس مسار الكوبونات) — لا تسرب لطبقة `sales.py`.
"""
from __future__ import annotations

import enum

from sqlalchemy import Column, Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import relationship

from app.extensions import db
from app.models.base import TimestampMixin


class LoyaltyTxnType(str, enum.Enum):
    EARN = "earn"       # مكتسبة من بيع
    REDEEM = "redeem"   # مستبدلة كخصم
    ADJUST = "adjust"   # تعديل يدوي من الأدمن


class LoyaltyPointsLedger(db.Model, TimestampMixin):
    __tablename__ = "loyalty_points_ledger"

    id = Column(Integer, primary_key=True)
    customer_id = Column(
        Integer, ForeignKey("parties.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # موجب = زيادة، سالب = خصم (استبدال أو تعديل سالب)
    points = Column(Numeric(18, 3), nullable=False, default=0)
    transaction_type = Column(
        Enum(LoyaltyTxnType, name="loyalty_txn_type",
             values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=LoyaltyTxnType.EARN,
    )
    # مصادر مرجعية (اختيارية)
    reference_order_id = Column(Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True)
    reference_invoice_id = Column(
        Integer, ForeignKey("sales_invoices.id", ondelete="SET NULL"),
        nullable=True,
    )
    memo = Column(String(300), nullable=True)

    customer = relationship("Party")
    order = relationship("Order")
    invoice = relationship("SalesInvoice")

    def __repr__(self) -> str:
        return f"<Loyalty {self.customer_id} {self.transaction_type.value} {self.points}>"
