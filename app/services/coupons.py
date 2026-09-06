"""خدمة أكواد الخصم (Ticket 2 Epic 2).

القواعد المحاسبية (حرجة):
- `validate_and_compute()` يُرجع dict فيه coupon + discount_amount المحسوب.
- `record_usage()` يُنشئ CouponUsage بعد إنشاء الطلب — يُستدعى داخل نفس transaction.
- الخصم ينتقل إلى `Order.discount_amount` ثم تلقائيًا إلى `SalesInvoice.discount_amount`
  عبر `order_accounting.py:120` (لا تعديل على الطبقة المحاسبية).

نتيجة: القيد المحاسبي = subtotal - discount_amount = المبلغ الفعلي المستحق (Golden Rule).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.extensions import db
from app.models.coupon import CouponUsage, DiscountCoupon, DiscountType


class CouponError(ValueError):
    pass


ZERO = Decimal("0")


def _as_dec(x) -> Decimal:
    if x in (None, ""):
        return ZERO
    return x if isinstance(x, Decimal) else Decimal(str(x))


def _parse_dt(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


# ============ CRUD (admin) ============

def create_coupon(*, code: str, discount_type, discount_value,
                  min_order_amount=None, max_uses=None,
                  max_uses_per_customer=1, valid_from=None,
                  valid_until=None, is_active=True) -> DiscountCoupon:
    code = (code or "").strip().upper()
    if not code:
        raise CouponError("كود الكوبون مطلوب.")
    if db.session.query(DiscountCoupon).filter_by(code=code).first() is not None:
        raise CouponError(f"الكود {code} مستخدم بالفعل.")

    if isinstance(discount_type, str):
        try:
            discount_type = DiscountType(discount_type)
        except ValueError:
            raise CouponError(f"نوع خصم غير معروف: {discount_type}")

    dv = _as_dec(discount_value)
    if dv <= 0:
        raise CouponError("قيمة الخصم يجب أن تكون أكبر من صفر.")
    if discount_type == DiscountType.PERCENTAGE and dv > 100:
        raise CouponError("نسبة الخصم لا يمكن أن تتجاوز 100%.")

    c = DiscountCoupon(
        code=code,
        discount_type=discount_type,
        discount_value=dv,
        min_order_amount=_as_dec(min_order_amount) if min_order_amount not in (None, "") else None,
        max_uses=int(max_uses) if max_uses not in (None, "") else None,
        max_uses_per_customer=int(max_uses_per_customer) if max_uses_per_customer not in (None, "") else None,
        valid_from=_parse_dt(valid_from),
        valid_until=_parse_dt(valid_until),
        is_active=bool(is_active),
    )
    db.session.add(c)
    db.session.flush()
    return c


def update_coupon(coupon_id: int, **kwargs) -> DiscountCoupon:
    c = db.session.get(DiscountCoupon, coupon_id)
    if c is None:
        raise CouponError("الكوبون غير موجود.")
    for key in ("min_order_amount", "max_uses", "max_uses_per_customer",
                "valid_from", "valid_until"):
        if key in kwargs:
            v = kwargs[key]
            if v in (None, ""):
                setattr(c, key, None)
            elif key in ("max_uses", "max_uses_per_customer"):
                setattr(c, key, int(v))
            elif key in ("valid_from", "valid_until"):
                setattr(c, key, _parse_dt(v))
            else:
                setattr(c, key, _as_dec(v))
    if "discount_value" in kwargs and kwargs["discount_value"] not in (None, ""):
        c.discount_value = _as_dec(kwargs["discount_value"])
    if "is_active" in kwargs:
        c.is_active = bool(kwargs["is_active"])
    db.session.flush()
    return c


# ============ Public validation (checkout) ============

def _customer_usage_count(coupon_id: int, customer_id: int | None) -> int:
    """عدد المرات التي استخدم فيها هذا العميل هذا الكوبون."""
    if customer_id is None:
        return 0
    return (
        db.session.query(CouponUsage)
        .filter_by(coupon_id=coupon_id, customer_id=customer_id)
        .count()
    )


def _total_usage_count(coupon_id: int) -> int:
    return db.session.query(CouponUsage).filter_by(coupon_id=coupon_id).count()


def validate_and_compute(*, code: str, subtotal, customer_id: int | None = None) -> dict:
    """يفحص كود الخصم على قيمة subtotal للعميل ويعيد dict فيه:
        {'coupon': DiscountCoupon, 'discount_amount': Decimal}
    ويرمي CouponError برسالة عربية واضحة عند أي فشل.
    """
    code = (code or "").strip().upper()
    if not code:
        raise CouponError("أدخل كود الخصم.")

    coupon = db.session.query(DiscountCoupon).filter_by(code=code).first()
    if coupon is None or not coupon.is_active:
        raise CouponError("كود الخصم غير صالح.")

    now = datetime.now(timezone.utc)
    if coupon.valid_from is not None:
        vf = coupon.valid_from
        if vf.tzinfo is None:
            vf = vf.replace(tzinfo=timezone.utc)
        if now < vf:
            raise CouponError("كود الخصم لم يبدأ بعد.")
    if coupon.valid_until is not None:
        vu = coupon.valid_until
        if vu.tzinfo is None:
            vu = vu.replace(tzinfo=timezone.utc)
        if now > vu:
            raise CouponError("انتهت صلاحية كود الخصم.")

    if coupon.max_uses is not None:
        if _total_usage_count(coupon.id) >= coupon.max_uses:
            raise CouponError("تم استخدام كود الخصم بالكامل.")

    if coupon.max_uses_per_customer is not None and customer_id is not None:
        if _customer_usage_count(coupon.id, customer_id) >= coupon.max_uses_per_customer:
            raise CouponError("سبق أن استخدمت هذا الكود.")

    sub = _as_dec(subtotal)
    if coupon.min_order_amount is not None and coupon.min_order_amount > 0:
        if sub < coupon.min_order_amount:
            raise CouponError(
                f"قيمة الطلب لا تكفي — الحد الأدنى {coupon.min_order_amount} ج.م."
            )

    # الحساب
    if coupon.discount_type == DiscountType.PERCENTAGE:
        discount = (sub * Decimal(str(coupon.discount_value)) / Decimal("100")).quantize(
            Decimal("0.001")
        )
    else:
        discount = _as_dec(coupon.discount_value).quantize(Decimal("0.001"))

    # لا نسمح للخصم أن يتجاوز subtotal
    if discount > sub:
        discount = sub

    return {"coupon": coupon, "discount_amount": discount}


def record_usage(*, coupon_id: int, order_id: int, customer_id: int | None,
                 discount_amount) -> CouponUsage:
    usage = CouponUsage(
        coupon_id=coupon_id,
        order_id=order_id,
        customer_id=customer_id,
        discount_amount=_as_dec(discount_amount),
    )
    db.session.add(usage)
    db.session.flush()
    return usage
