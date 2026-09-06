"""نقاط الولاء (Ticket 3 Epic 10).

- award_points_for_order(): تُستدعى عند DELIVERED (يستدعيها order_accounting.py)
- balance(): مجموع النقاط لعميل
- earn/redeem/adjust: يدوي

**لا تسرب محاسبي:** النقاط عرض بحت، والاستبدال (لو تم) يذهب لـ Order.discount_amount
بنفس منطق الكوبونات — لا نلمس sales.py.
"""
from __future__ import annotations

from decimal import Decimal

from app.extensions import db
from app.models.loyalty import LoyaltyPointsLedger, LoyaltyTxnType
from app.models.setting import get_setting


ZERO = Decimal("0")


def _enabled() -> bool:
    return bool(get_setting("loyalty.enabled", False))


def balance(customer_id: int) -> Decimal:
    total = (
        db.session.query(db.func.coalesce(db.func.sum(LoyaltyPointsLedger.points), 0))
        .filter_by(customer_id=customer_id)
        .scalar()
    )
    return Decimal(str(total or 0))


def award_points_for_order(*, customer_id: int, order_id: int,
                            order_total: Decimal) -> LoyaltyPointsLedger | None:
    """يمنح نقاطًا عند تسليم طلب أونلاين (يُستدعى من hook Phase 8)."""
    if not _enabled() or customer_id is None:
        return None
    rate = Decimal(str(get_setting("loyalty.points_per_currency_unit", 1) or 1))
    points = (Decimal(str(order_total or 0)) * rate).quantize(Decimal("0.001"))
    if points <= 0:
        return None
    # منع التكرار — لو نقاط مكتسبة على نفس الطلب موجودة بالفعل
    existing = (
        db.session.query(LoyaltyPointsLedger)
        .filter_by(customer_id=customer_id, reference_order_id=order_id,
                   transaction_type=LoyaltyTxnType.EARN)
        .first()
    )
    if existing is not None:
        return existing
    entry = LoyaltyPointsLedger(
        customer_id=customer_id,
        points=points,
        transaction_type=LoyaltyTxnType.EARN,
        reference_order_id=order_id,
        memo=f"نقاط مكتسبة على طلب {order_id}",
    )
    db.session.add(entry)
    db.session.flush()
    return entry


def list_transactions(customer_id: int, limit: int = 50):
    return (
        db.session.query(LoyaltyPointsLedger)
        .filter_by(customer_id=customer_id)
        .order_by(LoyaltyPointsLedger.id.desc())
        .limit(limit)
        .all()
    )


def adjust(*, customer_id: int, delta_points: Decimal, memo: str,
           user_id: int | None = None) -> LoyaltyPointsLedger:
    if not memo:
        raise ValueError("سبب التعديل مطلوب.")
    entry = LoyaltyPointsLedger(
        customer_id=customer_id,
        points=Decimal(str(delta_points)),
        transaction_type=LoyaltyTxnType.ADJUST,
        memo=memo,
    )
    db.session.add(entry)
    db.session.flush()
    return entry
