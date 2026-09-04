"""تصنيفات المنتجات — شجرة بثلاث مستويات (رئيسي / فرعي / فرعي فرعي).

نستخدم adjacency list (parent_id). المستوى محسوب من الجذر ومحدود بـ 3 عبر service.
"""
from __future__ import annotations

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.extensions import db
from app.models.base import TimestampMixin


class Category(db.Model, TimestampMixin):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True)
    name_ar = Column(String(160), nullable=False, index=True)
    slug = Column(String(160), unique=True, nullable=True, index=True)  # يُولَّد للـ Storefront لاحقًا
    parent_id = Column(Integer, ForeignKey("categories.id", ondelete="RESTRICT"), nullable=True, index=True)
    display_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)

    parent = relationship("Category", remote_side="Category.id", backref="children")

    MAX_DEPTH = 3

    @property
    def depth(self) -> int:
        """عمق التصنيف من الجذر (1 للجذر، 2, 3)."""
        d = 1
        node = self.parent
        while node is not None:
            d += 1
            node = node.parent
        return d

    @property
    def full_path_ar(self) -> str:
        """المسار الكامل: `أدوات مطبخ / سيراميك / أطباق`."""
        parts = []
        node = self
        while node is not None:
            parts.append(node.name_ar)
            node = node.parent
        return " / ".join(reversed(parts))

    def __repr__(self) -> str:
        return f"<Category {self.id} {self.name_ar}>"
