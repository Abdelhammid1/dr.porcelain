"""علاقات بين المنتجات (Epic 5).

نوعان:
- `related`: منتجات ذات صلة (تظهر في تبويب مستقل)
- `frequently_bought`: عادةً يُشترى معه (يظهر بجوار زر الإضافة للسلة)
"""
from __future__ import annotations

import enum

from sqlalchemy import Column, Enum, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import relationship

from app.extensions import db
from app.models.base import TimestampMixin


class RelationType(enum.Enum):
    RELATED = "related"
    FREQUENTLY_BOUGHT = "frequently_bought"


class ProductRelation(db.Model, TimestampMixin):
    __tablename__ = "product_relations"
    __table_args__ = (
        UniqueConstraint("product_id", "related_product_id", "relation_type",
                         name="uq_product_relation"),
    )

    id = Column(Integer, primary_key=True)
    product_id = Column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    related_product_id = Column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    relation_type = Column(Enum(RelationType), nullable=False,
                           default=RelationType.RELATED, index=True)
    display_order = Column(Integer, nullable=False, default=0)

    product = relationship("Product", foreign_keys=[product_id],
                           back_populates="relations")
    related_product = relationship("Product", foreign_keys=[related_product_id])

    def __repr__(self) -> str:
        return (
            f"<ProductRelation {self.id} "
            f"{self.product_id}→{self.related_product_id} ({self.relation_type.value})>"
        )
