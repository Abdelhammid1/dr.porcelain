"""سلة تسوق مبنية على جلسة المتصفح (Flask session).

البنية: session['cart'] = {'variant_id': qty, ...}
       session['cart_coupon'] = 'CODE'  (Ticket 2 Epic 2)

نقرأ بيانات المنتجات (الاسم، السعر، الرصيد) من قاعدة البيانات عند الحاجة فقط.
لا نُنشئ صفوف DB للسلة — الطلب فقط هو ما يُحفظ.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from flask import session

from app.extensions import db
from app.models.product import ProductVariant
from app.models.setting import get_setting


CART_KEY = "cart"
COUPON_KEY = "cart_coupon"
ZERO = Decimal("0")


@dataclass
class CartLine:
    variant: ProductVariant
    qty: Decimal
    unit_price: Decimal

    @property
    def line_total(self) -> Decimal:
        return (self.qty * self.unit_price).quantize(Decimal("0.001"))


@dataclass
class CartView:
    lines: list[CartLine] = field(default_factory=list)
    subtotal: Decimal = ZERO
    tax_rate: Decimal = ZERO
    tax_amount: Decimal = ZERO
    shipping_fee: Decimal = ZERO
    total: Decimal = ZERO
    free_shipping_reached: bool = False
    # Ticket 2 Epic 2 — الكوبون المُطبَّق حاليًا (لو صالح)
    coupon_code: str | None = None
    coupon_id: int | None = None
    discount_amount: Decimal = ZERO

    @property
    def items_count(self) -> int:
        return sum(int(l.qty) for l in self.lines)

    @property
    def is_empty(self) -> bool:
        return not self.lines


# ============ Manipulation ============

def _get_raw() -> dict[str, int]:
    return session.get(CART_KEY, {})


def _save_raw(cart: dict[str, int]) -> None:
    session[CART_KEY] = cart
    session.modified = True


def add_item(variant_id: int, qty: int = 1) -> None:
    if qty <= 0:
        return
    cart = _get_raw()
    key = str(variant_id)
    cart[key] = cart.get(key, 0) + int(qty)
    _save_raw(cart)


def set_qty(variant_id: int, qty: int) -> None:
    cart = _get_raw()
    key = str(variant_id)
    if qty <= 0:
        cart.pop(key, None)
    else:
        cart[key] = int(qty)
    _save_raw(cart)


def remove_item(variant_id: int) -> None:
    cart = _get_raw()
    cart.pop(str(variant_id), None)
    _save_raw(cart)


def clear() -> None:
    session.pop(CART_KEY, None)
    session.pop(COUPON_KEY, None)
    session.modified = True


def items_count() -> int:
    """للعرض في الهيدر — عدد القطع في السلة."""
    return sum(int(v) for v in _get_raw().values())


# ============ Coupons (Ticket 2 Epic 2) ============

def set_coupon(code: str) -> None:
    session[COUPON_KEY] = (code or "").strip().upper()
    session.modified = True


def get_coupon() -> str | None:
    return session.get(COUPON_KEY)


def clear_coupon() -> None:
    session.pop(COUPON_KEY, None)
    session.modified = True


# ============ Rendering ============

def build_cart_view(customer_id: int | None = None) -> CartView:
    """يبني CartView مع أسعار وحسابات جاهزة للعرض.

    إن كان هناك كوبون مُطبَّق في الجلسة، نُعيد التحقق منه في كل رندر ونطبّق الخصم.
    لو الكوبون أصبح غير صالح (منتهي، مستخدم بالكامل) نُزيله من الجلسة تلقائيًا.
    """
    view = CartView()
    raw = _get_raw()
    if not raw:
        clear_coupon()
        return view

    variant_ids = [int(k) for k in raw.keys()]
    variants = {
        v.id: v
        for v in db.session.query(ProductVariant)
        .filter(ProductVariant.id.in_(variant_ids))
        .filter(ProductVariant.is_active == True)  # noqa: E712
        .all()
    }

    # لو منتج اختفى/تعطّل، نُزيله من السلة تلقائيًا
    dirty = False
    for vid in list(raw.keys()):
        if int(vid) not in variants:
            raw.pop(vid, None)
            dirty = True
    if dirty:
        _save_raw(raw)

    for vid_str, qty in raw.items():
        v = variants.get(int(vid_str))
        if v is None:
            continue
        stock = Decimal(str(v.stock_qty or 0))
        eff_qty = min(Decimal(str(qty)), stock) if stock > 0 else Decimal("0")
        if eff_qty <= 0:
            continue
        view.lines.append(CartLine(
            variant=v,
            qty=eff_qty,
            unit_price=Decimal(str(v.price)),
        ))
        view.subtotal += Decimal(str(eff_qty * Decimal(str(v.price))))

    view.subtotal = view.subtotal.quantize(Decimal("0.001"))

    # Ticket 2 Epic 2 — كوبون خصم (لو موجود وصالح)
    coupon_code = get_coupon()
    if coupon_code and view.subtotal > 0:
        from app.services.coupons import CouponError, validate_and_compute
        try:
            info = validate_and_compute(
                code=coupon_code, subtotal=view.subtotal, customer_id=customer_id,
            )
            view.coupon_code = info["coupon"].code
            view.coupon_id = info["coupon"].id
            view.discount_amount = info["discount_amount"]
        except CouponError:
            # كوبون تعطّل بعد وضعه في السلة → إزالته بهدوء
            clear_coupon()

    # net قبل الضريبة (بعد الخصم)
    net_before_tax = view.subtotal - view.discount_amount

    # الضريبة (على المبلغ بعد الخصم)
    if bool(get_setting("tax.enabled", False)):
        view.tax_rate = Decimal(str(get_setting("tax.default_rate", 0)))
        view.tax_amount = (net_before_tax * view.tax_rate / Decimal("100")).quantize(Decimal("0.001"))

    # الشحن (على subtotal الأصلي — القرار التجاري المعتاد)
    threshold = Decimal(str(get_setting("storefront.free_shipping_threshold", "0")))
    if view.subtotal >= threshold and threshold > 0:
        view.shipping_fee = ZERO
        view.free_shipping_reached = True
    else:
        view.shipping_fee = Decimal(str(get_setting("storefront.shipping_fee", "0")))

    view.total = (net_before_tax + view.tax_amount + view.shipping_fee).quantize(Decimal("0.001"))
    return view
