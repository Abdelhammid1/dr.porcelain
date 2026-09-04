"""حركة المخزون — سجل كامل غير قابل للحذف لكل تغيير في رصيد المخزون.

كل حركة تُنشأ عبر `app.services.inventory` والتي تتولى:
- تحديث `stock_qty` و`avg_cost` على المتغير.
- ربط الحركة بمصدرها (فاتورة بيع/شراء/تسوية).

نحفظ snapshot للتكلفة (cost_at_move) عند البيع حتى لا يتغير COGS بعد ذلك
إذا تغيرت التكلفة المتوسطة في مشتريات لاحقة.
"""
from __future__ import annotations

import enum

from sqlalchemy import (
    Column,
    Date,
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


class MovementType(str, enum.Enum):
    PURCHASE = "purchase"                # شراء (+)
    PURCHASE_RETURN = "purchase_return"  # مرتجع شراء (-)
    SALE = "sale"                        # بيع (-)
    SALE_RETURN = "sale_return"          # مرتجع بيع (+)
    ADJUSTMENT_IN = "adjustment_in"      # تسوية موجبة (+)
    ADJUSTMENT_OUT = "adjustment_out"    # تسوية سالبة (-)
    OPENING = "opening"                  # رصيد افتتاحي (+)


class InventoryMovement(db.Model, TimestampMixin):
    __tablename__ = "inventory_movements"

    id = Column(Integer, primary_key=True)
    variant_id = Column(
        Integer, ForeignKey("product_variants.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    move_type = Column(
        Enum(MovementType, name="movement_type",
             values_callable=lambda x: [e.value for e in x]),
        nullable=False, index=True,
    )
    move_date = Column(Date, nullable=False, index=True)

    # كمية موجبة دائمًا — الاتجاه محدد بـ move_type.
    qty = Column(QTY, nullable=False)
    # تكلفة الوحدة وقت الحركة (لـ purchase: سعر الشراء؛ لـ sale: avg_cost وقت البيع).
    unit_cost = Column(MONEY, nullable=False, default=0)

    # ربط بالمصدر (فاتورة بيع، فاتورة شراء…)
    source_type = Column(String(40), nullable=True, index=True)
    source_id = Column(Integer, nullable=True, index=True)

    memo = Column(Text, nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    variant = relationship("ProductVariant")

    @property
    def signed_qty(self):
        """الكمية المُوقَّعة (+ للإضافات، - للسحب)."""
        outbound = {MovementType.SALE, MovementType.PURCHASE_RETURN, MovementType.ADJUSTMENT_OUT}
        return -self.qty if self.move_type in outbound else self.qty

    def __repr__(self) -> str:
        return f"<Move {self.move_type.value} variant={self.variant_id} qty={self.qty}>"
