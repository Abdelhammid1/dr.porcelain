"""مواصفات المنتج (Epic 3) + تكوين الطقم (Epic 4).

- ProductFeature: نقطة نصية قصيرة (bullet) تظهر تحت المواصفات في صفحة المنتج.
- ProductCompositionLine: عنصر واحد من طقم — الكمية + وصف المحتوى.
"""
from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.extensions import db
from app.models.base import TimestampMixin


class ProductFeature(db.Model, TimestampMixin):
    __tablename__ = "product_features"

    id = Column(Integer, primary_key=True)
    product_id = Column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    text = Column(String(500), nullable=False)
    display_order = Column(Integer, nullable=False, default=0)

    product = relationship("Product", back_populates="features")

    def __repr__(self) -> str:
        return f"<ProductFeature {self.id} p={self.product_id}>"


class ProductCompositionLine(db.Model, TimestampMixin):
    """سطر واحد من تكوين طقم — مثال: (٦ × طبق تقديم كبير)."""
    __tablename__ = "product_composition_lines"

    id = Column(Integer, primary_key=True)
    product_id = Column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    quantity = Column(Integer, nullable=False, default=1)
    content_name_ar = Column(String(1000), nullable=False)
    display_order = Column(Integer, nullable=False, default=0)

    product = relationship("Product", back_populates="composition")

    def __repr__(self) -> str:
        return f"<ProductCompositionLine {self.id} {self.quantity}×{self.content_name_ar}>"
