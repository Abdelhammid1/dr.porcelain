"""خدمة المخزون — حركات + متوسط تكلفة مرجّح.

النقطة الوحيدة التي تُعدِّل رصيد المخزون. أي تعديل يمر عبر:
- `record_purchase()`  → يزيد الرصيد ويُعيد حساب avg_cost.
- `record_sale()`      → يخصم الرصيد ويرجع cost snapshot لـ COGS.
- `record_purchase_return()` / `record_sale_return()` → عكس الحركة.
- `record_adjustment()` → تسويات يدوية.
- `record_opening()`   → رصيد افتتاحي مع تكلفة.

الاستدعاء يُنفَّذ داخل transaction خارجية (لا نعمل commit).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.inventory import InventoryMovement, MovementType
from app.models.product import ProductVariant


class InventoryError(ValueError):
    pass


ZERO = Decimal("0")


def _as_dec(x) -> Decimal:
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


def _quantize(v: Decimal, places: int = 3) -> Decimal:
    q = Decimal(10) ** -places
    return v.quantize(q)


# ---------- شراء ----------

def record_purchase(
    *,
    variant_id: int,
    qty: Decimal | float | str,
    unit_cost: Decimal | float | str,
    move_date: date,
    source_type: str | None = None,
    source_id: int | None = None,
    user_id: int | None = None,
    memo: str | None = None,
) -> InventoryMovement:
    """تسجيل شراء — يزيد الرصيد ويُعيد حساب متوسط التكلفة.

    معادلة المتوسط المرجّح:
        avg_new = (qty_old * avg_old + qty_in * cost_in) / (qty_old + qty_in)
    """
    qty = _as_dec(qty)
    unit_cost = _as_dec(unit_cost)
    if qty <= 0:
        raise InventoryError("الكمية يجب أن تكون أكبر من صفر.")
    if unit_cost < 0:
        raise InventoryError("سعر التكلفة لا يمكن أن يكون سالبًا.")

    v = _get_active_variant(variant_id)

    old_qty = _as_dec(v.stock_qty or 0)
    old_avg = _as_dec(v.avg_cost or 0)
    new_qty = old_qty + qty
    if new_qty <= 0:
        # حالة نظرية — مستحيلة رياضيًا لأن qty > 0 وold_qty يمكن أن يكون سالبًا فقط
        # في حالات فساد بيانات. نحمي أنفسنا.
        new_avg = unit_cost
    else:
        new_avg = _quantize((old_qty * old_avg + qty * unit_cost) / new_qty)

    v.stock_qty = _quantize(new_qty)
    v.avg_cost = new_avg

    move = InventoryMovement(
        variant_id=v.id,
        move_type=MovementType.PURCHASE,
        move_date=move_date,
        qty=_quantize(qty),
        unit_cost=_quantize(unit_cost),
        source_type=source_type,
        source_id=source_id,
        memo=memo,
        created_by_id=user_id,
    )
    db.session.add(move)
    db.session.flush()

    # Ticket 4 Epic 6 — لو الرصيد ارتفع من 0 لأكثر من 0، أطلق تنبيه توفر
    if old_qty <= ZERO and new_qty > ZERO:
        try:
            from app.services.stock_alerts import notify_available
            notify_available(v.id)
        except Exception:
            pass

    return move


# ---------- بيع ----------

def record_sale(
    *,
    variant_id: int,
    qty: Decimal | float | str,
    move_date: date,
    source_type: str | None = None,
    source_id: int | None = None,
    user_id: int | None = None,
    memo: str | None = None,
    allow_negative_stock: bool = False,
) -> InventoryMovement:
    """تسجيل بيع — يخصم الرصيد ويحفظ snapshot للتكلفة الحالية.

    التكلفة المحفوظة تُستخدَم في قيد COGS (fixed at the moment of sale).
    """
    qty = _as_dec(qty)
    if qty <= 0:
        raise InventoryError("الكمية يجب أن تكون أكبر من صفر.")

    v = _get_active_variant(variant_id)

    available = _as_dec(v.stock_qty or 0)
    if not allow_negative_stock and qty > available:
        raise InventoryError(
            f"الكمية المتاحة ({available}) أقل من المطلوبة ({qty}) للمنتج {v.sku}."
        )

    cost_snapshot = _as_dec(v.avg_cost or 0)
    v.stock_qty = _quantize(available - qty)
    # نلاحظ: avg_cost لا يتغير عند البيع.

    move = InventoryMovement(
        variant_id=v.id,
        move_type=MovementType.SALE,
        move_date=move_date,
        qty=_quantize(qty),
        unit_cost=cost_snapshot,   # snapshot
        source_type=source_type,
        source_id=source_id,
        memo=memo,
        created_by_id=user_id,
    )
    db.session.add(move)
    db.session.flush()
    return move


# ---------- عكس الحركات (استرجاعات) ----------

def record_sale_return(
    *,
    variant_id: int,
    qty: Decimal | float | str,
    unit_cost: Decimal | float | str,  # نفس التكلفة المحفوظة في سطر البيع الأصلي
    move_date: date,
    source_type: str | None = None,
    source_id: int | None = None,
    user_id: int | None = None,
    memo: str | None = None,
) -> InventoryMovement:
    """يُعيد الكمية للمخزون بنفس تكلفة سطر البيع الأصلي (لا يُعدِّل avg_cost)."""
    qty = _as_dec(qty)
    if qty <= 0:
        raise InventoryError("الكمية يجب أن تكون أكبر من صفر.")

    v = _get_active_variant(variant_id)
    v.stock_qty = _quantize(_as_dec(v.stock_qty or 0) + qty)
    # avg_cost لا يتغير — الوحدة تعود بتكلفتها الأصلية

    move = InventoryMovement(
        variant_id=v.id,
        move_type=MovementType.SALE_RETURN,
        move_date=move_date,
        qty=_quantize(qty),
        unit_cost=_quantize(_as_dec(unit_cost)),
        source_type=source_type,
        source_id=source_id,
        memo=memo,
        created_by_id=user_id,
    )
    db.session.add(move)
    db.session.flush()
    return move


def record_purchase_return(
    *,
    variant_id: int,
    qty: Decimal | float | str,
    unit_cost: Decimal | float | str,
    move_date: date,
    source_type: str | None = None,
    source_id: int | None = None,
    user_id: int | None = None,
    memo: str | None = None,
) -> InventoryMovement:
    """يخصم الكمية من المخزون بتكلفة سطر الشراء الأصلي."""
    qty = _as_dec(qty)
    if qty <= 0:
        raise InventoryError("الكمية يجب أن تكون أكبر من صفر.")

    v = _get_active_variant(variant_id)
    available = _as_dec(v.stock_qty or 0)
    if qty > available:
        raise InventoryError(
            f"لا يمكن استرجاع {qty} — الرصيد الحالي {available}."
        )
    v.stock_qty = _quantize(available - qty)

    move = InventoryMovement(
        variant_id=v.id,
        move_type=MovementType.PURCHASE_RETURN,
        move_date=move_date,
        qty=_quantize(qty),
        unit_cost=_quantize(_as_dec(unit_cost)),
        source_type=source_type,
        source_id=source_id,
        memo=memo,
        created_by_id=user_id,
    )
    db.session.add(move)
    db.session.flush()
    return move


# ---------- تسوية يدوية ----------

def record_adjustment(
    *,
    variant_id: int,
    delta_qty: Decimal | float | str,   # موجب = زيادة، سالب = نقص
    move_date: date,
    reason: str,
    user_id: int | None = None,
) -> InventoryMovement:
    """تسوية جرد يدوية — تُغير الرصيد فقط، لا تُعدِّل avg_cost.

    القيد المحاسبي المقابل يُنشأ من الاستدعاء (خارج هذه الدالة).
    """
    delta = _as_dec(delta_qty)
    if delta == 0:
        raise InventoryError("الفرق لا يمكن أن يكون صفرًا في التسوية.")
    if not (reason or "").strip():
        raise InventoryError("سبب التسوية مطلوب.")

    v = _get_active_variant(variant_id)
    current = _as_dec(v.stock_qty or 0)
    new_qty = current + delta
    if new_qty < 0:
        raise InventoryError(f"الرصيد سيصبح سالبًا ({new_qty}) — غير مسموح.")

    v.stock_qty = _quantize(new_qty)

    move = InventoryMovement(
        variant_id=v.id,
        move_type=MovementType.ADJUSTMENT_IN if delta > 0 else MovementType.ADJUSTMENT_OUT,
        move_date=move_date,
        qty=_quantize(abs(delta)),
        unit_cost=_as_dec(v.avg_cost or 0),
        source_type="inventory_adjustment",
        memo=reason,
        created_by_id=user_id,
    )
    db.session.add(move)
    db.session.flush()
    return move


def record_opening(
    *,
    variant_id: int,
    qty: Decimal | float | str,
    unit_cost: Decimal | float | str,
    move_date: date,
    user_id: int | None = None,
) -> InventoryMovement:
    """رصيد افتتاحي — يضبط الكمية والتكلفة المتوسطة مرة واحدة."""
    qty = _as_dec(qty)
    unit_cost = _as_dec(unit_cost)
    if qty < 0 or unit_cost < 0:
        raise InventoryError("قيم الرصيد الافتتاحي يجب أن تكون >= 0.")

    v = _get_active_variant(variant_id)
    if _as_dec(v.stock_qty or 0) != ZERO:
        raise InventoryError(
            "الرصيد الافتتاحي يمكن تسجيله فقط عندما يكون رصيد المتغير صفرًا."
        )
    v.stock_qty = _quantize(qty)
    v.avg_cost = _quantize(unit_cost)

    move = InventoryMovement(
        variant_id=v.id,
        move_type=MovementType.OPENING,
        move_date=move_date,
        qty=_quantize(qty),
        unit_cost=_quantize(unit_cost),
        source_type="opening",
        created_by_id=user_id,
    )
    db.session.add(move)
    db.session.flush()
    return move


# ---------- Helpers ----------

def _get_active_variant(variant_id: int) -> ProductVariant:
    v = db.session.get(ProductVariant, variant_id)
    if v is None:
        raise InventoryError(f"المتغير #{variant_id} غير موجود.")
    if not v.is_active:
        raise InventoryError(f"المتغير {v.sku} موقوف.")
    return v


# ---------- استعلامات مساعدة ----------

def low_stock_variants(limit: int | None = None):
    """يُرجِع قائمة المتغيرات التي رصيدها ≤ reorder_level (وreorder_level > 0)."""
    from sqlalchemy import and_
    q = (
        db.session.query(ProductVariant)
        .filter(ProductVariant.is_active == True)  # noqa: E712
        .filter(and_(
            ProductVariant.reorder_level > 0,
            ProductVariant.stock_qty <= ProductVariant.reorder_level,
        ))
        .order_by(ProductVariant.stock_qty)
    )
    if limit:
        q = q.limit(limit)
    return q.all()
