"""اختبارات الطلبات الأونلاين + السلة."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.models.order import OrderPaymentMethod, OrderStatus
from app.models.party import PartyType
from app.models.setting import set_setting
from app.services.inventory import record_purchase
from app.services.orders import (
    OrderError,
    OrderLineDraft,
    create_order,
    transition_status,
)
from app.services.parties import create_party
from app.services.products import create_category, create_product
from seeds.chart_of_accounts import seed_chart_of_accounts


@pytest.fixture()
def env(app):
    seed_chart_of_accounts(_db.session)
    set_setting("tax.enabled", "false")
    set_setting("storefront.shipping_fee", "40.000")
    set_setting("storefront.free_shipping_threshold", "500.000")
    _db.session.commit()

    tag = uuid.uuid4().hex[:8]
    cat = create_category(name_ar=f"c{tag}")
    product = create_product(
        name_ar=f"p{tag}", category_id=cat.id, default_price=Decimal("100"),
        variants=[{"variant_name": "افتراضي"}],
    )
    variant = product.variants[0]
    record_purchase(variant_id=variant.id, qty=100, unit_cost=50, move_date=date(2026, 1, 1))
    _db.session.commit()
    yield {"variant": variant, "product": product}


# ---------- 1) إنشاء الطلب ----------

class TestCreateOrder:
    def test_guest_order_creates_pending(self, env):
        order = create_order(
            lines=[OrderLineDraft(env["variant"].id, qty=2)],
            guest_name="ضيف اختبار",
            guest_phone="01098765432",
            shipping_address="القاهرة - مدينة نصر",
        )
        _db.session.commit()

        assert order.doc_number.startswith("ORD-")
        assert order.status == OrderStatus.PENDING
        assert order.customer_id is None
        assert order.guest_name == "ضيف اختبار"
        assert order.guest_phone == "01098765432"
        assert order.subtotal == Decimal("200.000")
        # الشحن — 200 < 500 → شحن 40
        assert order.shipping_fee == Decimal("40.000")
        assert order.total == Decimal("240.000")
        assert len(order.lines) == 1
        assert order.payment_method == OrderPaymentMethod.COD

    def test_stock_deducted(self, env):
        variant = env["variant"]
        stock_before = Decimal(str(variant.stock_qty))
        create_order(
            lines=[OrderLineDraft(variant.id, qty=5)],
            guest_name="ك", guest_phone="011",
            shipping_address="عنوان",
        )
        _db.session.commit()
        assert variant.stock_qty == stock_before - Decimal("5")

    def test_no_journal_created(self, env):
        """في Phase 6 الطلب لا يولد قيود محاسبية."""
        from app.models.journal import JournalEntry
        max_je_before = _db.session.query(_db.func.coalesce(_db.func.max(JournalEntry.id), 0)).scalar() or 0

        create_order(
            lines=[OrderLineDraft(env["variant"].id, qty=1)],
            guest_name="ك", guest_phone="011",
            shipping_address="عنوان",
        )
        _db.session.commit()

        # لا يجب إنشاء أي قيد جديد نتيجة لهذا الطلب
        max_je_after = _db.session.query(_db.func.coalesce(_db.func.max(JournalEntry.id), 0)).scalar() or 0
        assert max_je_after == max_je_before, "لا يجب إنشاء قيود قبل التسليم"

    def test_phone_lookup_auto_links_customer(self, env):
        """لو الهاتف يطابق عميلًا مسجّلًا، الطلب يُربط بحسابه تلقائيًا."""
        existing = create_party(
            type=PartyType.CUSTOMER,
            name_ar="عميل موجود مسبقًا",
            phone="01011111111",
        )
        _db.session.commit()

        order = create_order(
            lines=[OrderLineDraft(env["variant"].id, qty=1)],
            guest_name="أي اسم",
            guest_phone="01011111111",
            shipping_address="عنوان",
        )
        _db.session.commit()
        assert order.customer_id == existing.id

    def test_free_shipping_above_threshold(self, env):
        order = create_order(
            lines=[OrderLineDraft(env["variant"].id, qty=6, )],  # 6 × 100 = 600 > 500
            guest_name="ك", guest_phone="012",
            shipping_address="عنوان",
        )
        _db.session.commit()
        assert order.shipping_fee == Decimal("0")
        assert order.total == order.subtotal + order.tax_amount

    def test_missing_name_rejected(self, env):
        with pytest.raises(OrderError, match="الاسم"):
            create_order(
                lines=[OrderLineDraft(env["variant"].id, qty=1)],
                guest_name="",
                guest_phone="011",
                shipping_address="عنوان",
            )

    def test_over_stock_rejected(self, env):
        with pytest.raises(OrderError, match="أقل"):
            create_order(
                lines=[OrderLineDraft(env["variant"].id, qty=1000)],
                guest_name="ك", guest_phone="011",
                shipping_address="عنوان",
            )


# ---------- 2) الانتقال بين الحالات ----------

class TestStatusTransitions:
    def test_move_through_normal_flow(self, env):
        order = create_order(
            lines=[OrderLineDraft(env["variant"].id, qty=1)],
            guest_name="ك", guest_phone="011",
            shipping_address="عنوان",
        )
        _db.session.commit()

        for s in (OrderStatus.PROCESSING, OrderStatus.SHIPPED, OrderStatus.DELIVERED):
            transition_status(order_id=order.id, new_status=s)
            _db.session.commit()
        assert order.status == OrderStatus.DELIVERED
        assert order.delivered_at is not None

    def test_cancel_restores_stock(self, env):
        variant = env["variant"]
        stock_before = Decimal(str(variant.stock_qty))
        order = create_order(
            lines=[OrderLineDraft(variant.id, qty=3)],
            guest_name="ك", guest_phone="011",
            shipping_address="عنوان",
        )
        _db.session.commit()
        assert variant.stock_qty == stock_before - Decimal("3")

        transition_status(order_id=order.id, new_status=OrderStatus.CANCELLED)
        _db.session.commit()
        assert order.status == OrderStatus.CANCELLED
        assert variant.stock_qty == stock_before

    def test_cannot_cancel_after_shipping(self, env):
        order = create_order(
            lines=[OrderLineDraft(env["variant"].id, qty=1)],
            guest_name="ك", guest_phone="011",
            shipping_address="عنوان",
        )
        _db.session.commit()
        transition_status(order_id=order.id, new_status=OrderStatus.PROCESSING)
        transition_status(order_id=order.id, new_status=OrderStatus.SHIPPED)
        _db.session.commit()
        with pytest.raises(OrderError, match="الإلغاء"):
            transition_status(order_id=order.id, new_status=OrderStatus.CANCELLED)
