"""صور المنتجات (Epic 1).

كل صورة تُخزَّن كملف على القرص تحت `<UPLOAD_FOLDER>/products/<pid>/<uuid>.<ext>`
والـ `file_path` هنا مسار نسبي داخل `app/static/` (مثل `uploads/products/12/ab.jpg`)
حتى `url_for('static', filename=file_path)` يعمل مباشرة.
"""
from __future__ import annotations

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.extensions import db
from app.models.base import TimestampMixin


class ProductImage(db.Model, TimestampMixin):
    __tablename__ = "product_images"

    id = Column(Integer, primary_key=True)
    product_id = Column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    file_path = Column(String(500), nullable=False)
    display_order = Column(Integer, nullable=False, default=0)
    is_primary = Column(Boolean, nullable=False, default=False)

    product = relationship("Product", back_populates="images")

    def __repr__(self) -> str:
        return f"<ProductImage {self.id} p={self.product_id} primary={self.is_primary}>"
