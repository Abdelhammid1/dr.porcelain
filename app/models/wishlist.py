"""قائمة المفضلة — Ticket 2 Epic 4.

- يتطلب تسجيل دخول عميل (customer_required).
- unique per (customer_id, product_id) — لا نُكرر منتجًا في نفس القائمة.
"""
from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import relationship

from app.extensions import db
from app.models.base import TimestampMixin


class WishlistItem(db.Model, TimestampMixin):
    __tablename__ = "wishlist_items"
    __table_args__ = (
        UniqueConstraint("customer_id", "product_id", name="uq_wishlist_item"),
    )

    id = Column(Integer, primary_key=True)
    customer_id = Column(
        Integer, ForeignKey("parties.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    product_id = Column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    customer = relationship("Party")
    product = relationship("Product", lazy="joined")
