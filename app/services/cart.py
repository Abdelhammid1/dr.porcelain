"""سلة تسوق مبنية على جلسة المتصفح (Flask session).

البنية: session['cart'] = {'variant_id': qty, ...}

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
    session.modified = True


def items_count() -> int:
    """للعرض في الهيدر — عدد القطع في السلة."""
    return sum(int(v) for v in _get_raw().values())


# ============ Rendering ============

def build_cart_view() -> CartView:
    """يبني CartView مع أسعار وحسابات جاهزة للعرض."""
    view = CartView()
    raw = _get_raw()
    if not raw:
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
        # نحدّ الكمية بالمخزون المتاح
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

    # الضريبة
    if bool(get_setting("tax.enabled", False)):
        view.tax_rate = Decimal(str(get_setting("tax.default_rate", 0)))
        view.tax_amount = (view.subtotal * view.tax_rate / Decimal("100")).quantize(Decimal("0.001"))

    # الشحن
    threshold = Decimal(str(get_setting("storefront.free_shipping_threshold", "0")))
    if view.subtotal >= threshold and threshold > 0:
        view.shipping_fee = ZERO
        view.free_shipping_reached = True
    else:
        view.shipping_fee = Decimal(str(get_setting("storefront.shipping_fee", "0")))

    view.total = (view.subtotal + view.tax_amount + view.shipping_fee).quantize(Decimal("0.001"))
    return view
