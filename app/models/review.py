"""تقييمات المنتجات (Epic 6).

- كل تقييم مرتبط بعميل (Party.CUSTOMER) — لا نسمح بضيف حتى نتحقق من الشراء.
- unique per (product_id, customer_id) — عميل واحد يقيّم منتج مرة واحدة.
- is_approved=False افتراضيًا (يمر بمراجعة أدمن قبل الظهور للعموم).
"""
from __future__ import annotations

from sqlalchemy import Boolean, Column, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.extensions import db
from app.models.base import TimestampMixin


class ProductReview(db.Model, TimestampMixin):
    __tablename__ = "product_reviews"
    __table_args__ = (
        UniqueConstraint("product_id", "customer_id", name="uq_product_review"),
    )

    id = Column(Integer, primary_key=True)
    product_id = Column(
        Integer, ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    customer_id = Column(
        Integer, ForeignKey("parties.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    rating = Column(Integer, nullable=False)  # 1..5 (فحص في الخدمة)
    comment = Column(Text, nullable=True)
    is_approved = Column(Boolean, nullable=False, default=False, index=True)

    product = relationship("Product", back_populates="reviews")
    customer = relationship("Party", lazy="joined")

    def __repr__(self) -> str:
        return (
            f"<ProductReview {self.id} p={self.product_id} "
            f"c={self.customer_id} {self.rating}⭐ approved={self.is_approved}>"
        )
