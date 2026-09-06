"""تنبيهات "أعلمني لما يتوفر" (Ticket 4 Epic 6).

- يشتغل حتى للضيوف (email فقط، customer_id nullable لو مسجّل).
- notified_at nullable — بعد الإرسال يُعلَّم فلا يتكرر لنفس التوفر.
- يُعاد التنبيه بعد نفاد مخزون جديد → صف جديد ينُشأ.
"""
from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.extensions import db
from app.models.base import TimestampMixin


class StockAlert(db.Model, TimestampMixin):
    __tablename__ = "stock_alerts"

    id = Column(Integer, primary_key=True)
    variant_id = Column(
        Integer, ForeignKey("product_variants.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    email = Column(String(160), nullable=False, index=True)
    customer_id = Column(
        Integer, ForeignKey("parties.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    notified_at = Column(DateTime(timezone=True), nullable=True, index=True)

    variant = relationship("ProductVariant")
    customer = relationship("Party")

    def __repr__(self) -> str:
        return f"<StockAlert v={self.variant_id} {self.email} sent={self.notified_at is not None}>"
