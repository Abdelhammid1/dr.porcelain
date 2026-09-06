"""خدمة الطلبات الأونلاين — تُنشئ Order + تخصم المخزون (بدون قيود محاسبية في Phase 6).

قيد المحاسبة يأتي في Phase 8 عندما ينتقل الطلب لحالة DELIVERED.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Iterable

from app.extensions import db
from app.models.order import Order, OrderLine, OrderPaymentMethod, OrderStatus
from app.models.party import Party, PartyType
from app.models.product import ProductVariant
from app.models.setting import get_setting
from app.services.inventory import record_sale
from app.services.numbering import next_document_number


class OrderError(ValueError):
    pass


ZERO = Decimal("0")


@dataclass
class OrderLineDraft:
    variant_id: int
    qty: Decimal | float | str


def create_order(
    *,
    lines: Iterable[OrderLineDraft],
    guest_name: str,
    guest_phone: str,
    shipping_address: str,
    shipping_city: str | None = None,
    shipping_notes: str | None = None,
    guest_email: str | None = None,
    customer_id: int | None = None,
    payment_method: OrderPaymentMethod = OrderPaymentMethod.COD,
    notes: str | None = None,
    coupon_code: str | None = None,  # Ticket 2 Epic 2
) -> Order:
    """يُنشِئ طلبًا أونلاين ذريًا (فاتورة + خصم مخزون، بدون قيد محاسبي).

    - إن كان `customer_id` مُحدَّدًا، يُربط بالطرف الموجود
    - وإلا نحاول lookup بـ phone؛ إن وُجد نربط تلقائيًا (per Phase 8 rule)
    - وإلا نحفظ بيانات الضيف كما هي
    """
    guest_name = (guest_name or "").strip()
    guest_phone = (guest_phone or "").strip()
    if not guest_name:
        raise OrderError("الاسم مطلوب.")
    if not guest_phone:
        raise OrderError("رقم الهاتف مطلوب.")
    if not shipping_address or not shipping_address.strip():
        raise OrderError("عنوان الشحن مطلوب.")

    line_specs = list(lines)
    if not line_specs:
        raise OrderError("الطلب فارغ.")

    # ربط العميل: إن كان customer_id مُحدَّدًا نستخدمه، وإلا lookup بالهاتف
    linked_customer = None
    if customer_id:
        linked_customer = db.session.get(Party, customer_id)
        if linked_customer is None or linked_customer.type != PartyType.CUSTOMER:
            raise OrderError("العميل غير موجود.")
    else:
        existing = (
            db.session.query(Party)
            .filter_by(type=PartyType.CUSTOMER, phone=guest_phone, is_active=True)
            .first()
        )
        if existing is not None:
            linked_customer = existing

    # فحص الرصيد المتاح لكل بند + بناء قائمة resolved
    resolved: list[tuple[ProductVariant, Decimal, Decimal]] = []  # (variant, qty, price)
    subtotal = ZERO

    for spec in line_specs:
        variant = db.session.get(ProductVariant, spec.variant_id)
        if variant is None or not variant.is_active:
            raise OrderError(f"المنتج #{spec.variant_id} غير متاح.")
        qty = Decimal(str(spec.qty))
        if qty <= 0:
            raise OrderError(f"الكمية للمنتج {variant.sku} يجب أن تكون > صفر.")
        if Decimal(str(variant.stock_qty)) < qty:
            raise OrderError(
                f"الرصيد المتاح للمنتج {variant.display_name} "
                f"({variant.stock_qty}) أقل من المطلوب ({qty})."
            )
        price = Decimal(str(variant.price))
        resolved.append((variant, qty, price))
        subtotal += (qty * price).quantize(Decimal("0.001"))

    subtotal = subtotal.quantize(Decimal("0.001"))

    # الضريبة والشحن
    tax_enabled = bool(get_setting("tax.enabled", False))
    tax_rate = Decimal(str(get_setting("tax.default_rate", 0))) if tax_enabled else ZERO
    tax_amount = (subtotal * tax_rate / Decimal("100")).quantize(Decimal("0.001")) if tax_enabled else ZERO

    threshold = Decimal(str(get_setting("storefront.free_shipping_threshold", "0")))
    if threshold > 0 and subtotal >= threshold:
        shipping_fee = ZERO
    else:
        shipping_fee = Decimal(str(get_setting("storefront.shipping_fee", "0")))

    # Ticket 2 Epic 2 — تطبيق الكوبون (يمس المبلغ الفعلي المستحق)
    # القاعدة الذهبية: الخصم يقلل net_before_tax → subtotal في القيد لا يتغير
    # ولكن discount_amount المُخزَّن يُقلّل AR debit في نهاية القيد المحاسبي.
    coupon_id = None
    coupon_discount = ZERO
    if coupon_code:
        from app.services.coupons import CouponError, validate_and_compute
        try:
            info = validate_and_compute(
                code=coupon_code, subtotal=subtotal,
                customer_id=(linked_customer.id if linked_customer else None),
            )
            coupon_discount = info["discount_amount"]
            coupon_id = info["coupon"].id
        except CouponError as e:
            raise OrderError(str(e))

    net_before_tax = subtotal - coupon_discount
    # إعادة حساب الضريبة على المبلغ بعد الخصم
    if tax_enabled:
        tax_amount = (net_before_tax * tax_rate / Decimal("100")).quantize(Decimal("0.001"))
    total = (net_before_tax + tax_amount + shipping_fee).quantize(Decimal("0.001"))

    # إنشاء الطلب
    order = Order(
        doc_number=next_document_number("order"),
        order_date=date.today(),
        customer_id=(linked_customer.id if linked_customer else None),
        guest_name=guest_name,
        guest_phone=guest_phone,
        guest_email=(guest_email or None),
        payment_method=payment_method,
        shipping_address=shipping_address.strip(),
        shipping_city=(shipping_city or None),
        shipping_notes=(shipping_notes or None),
        subtotal=subtotal,
        discount_amount=coupon_discount,
        tax_rate=tax_rate,
        tax_amount=tax_amount,
        shipping_fee=shipping_fee,
        total=total,
        status=OrderStatus.PENDING,
        notes=(notes or None),
    )
    db.session.add(order)
    db.session.flush()

    # Ticket 2 Epic 2 — تسجيل استخدام الكوبون (بعد إنشاء الطلب لأنه FK)
    if coupon_id is not None and coupon_discount > 0:
        from app.services.coupons import record_usage
        record_usage(
            coupon_id=coupon_id,
            order_id=order.id,
            customer_id=(linked_customer.id if linked_customer else None),
            discount_amount=coupon_discount,
        )

    # إنشاء الأسطر + خصم المخزون (بدون قيود — Phase 8 تُنشئها لاحقًا)
    for variant, qty, price in resolved:
        record_sale(
            variant_id=variant.id,
            qty=qty,
            move_date=order.order_date,
            source_type="order",
            source_id=order.id,
            memo=f"طلب أونلاين {order.doc_number}",
        )
        db.session.add(OrderLine(
            order_id=order.id,
            variant_id=variant.id,
            product_name=variant.display_name,
            sku=variant.sku,
            qty=qty.quantize(Decimal("0.001")),
            unit_price=price,
            line_total=(qty * price).quantize(Decimal("0.001")),
        ))

    db.session.flush()
    return order


# ============ Status transitions ============

def transition_status(*, order_id: int, new_status: OrderStatus, user_id: int | None = None,
                      return_reason: str | None = None) -> Order:
    """ينقل الطلب لحالة جديدة.

    Phase 8 hooks:
    - DELIVERED  → يُنشئ SalesInvoice + 3 قيود محاسبية تلقائيًا
    - RETURNED   → يُنشئ سطر مرتجع + قيود عكسية + يُعيد المخزون
    - CANCELLED  → يُعيد المخزون (لا قيود)
    """
    order = db.session.get(Order, order_id)
    if order is None:
        raise OrderError("الطلب غير موجود.")

    if order.status == OrderStatus.DELIVERED and new_status not in (OrderStatus.RETURNED, OrderStatus.DELIVERED):
        raise OrderError("لا يمكن تغيير طلب مسلَّم إلا بمرتجع.")

    if new_status == OrderStatus.CANCELLED and order.status not in (OrderStatus.PENDING, OrderStatus.PROCESSING):
        raise OrderError("يمكن الإلغاء فقط قبل الشحن.")

    old_status = order.status
    order.status = new_status
    if new_status == OrderStatus.DELIVERED:
        order.delivered_at = datetime.now(timezone.utc)

    # Phase 8: hook DELIVERED — يُنشئ الفاتورة + القيود تلقائيًا
    if new_status == OrderStatus.DELIVERED and old_status != OrderStatus.DELIVERED:
        from app.services.order_accounting import create_invoice_from_delivered_order
        create_invoice_from_delivered_order(order_id=order.id, user_id=user_id)

    # Phase 8: hook RETURNED — عكس قيود الفاتورة + استرجاع المخزون
    if new_status == OrderStatus.RETURNED and old_status == OrderStatus.DELIVERED:
        from app.services.order_accounting import create_return_from_returned_order
        create_return_from_returned_order(
            order_id=order.id,
            reason=(return_reason or "مرتجع بعد التسليم"),
            user_id=user_id,
        )

    # Phase 6: إلغاء قبل التسليم يُعيد المخزون فقط (بلا قيود)
    if new_status == OrderStatus.CANCELLED and old_status != OrderStatus.CANCELLED:
        from app.services.inventory import record_sale_return
        for line in order.lines:
            record_sale_return(
                variant_id=line.variant_id,
                qty=Decimal(str(line.qty)),
                unit_cost=Decimal(str(line.variant.avg_cost or 0)),
                move_date=date.today(),
                source_type="order_cancel",
                source_id=order.id,
                user_id=user_id,
                memo=f"إلغاء طلب {order.doc_number}",
            )

    db.session.flush()
    return order
