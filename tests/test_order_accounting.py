"""اختبارات Phase 8 — ربط الطلبات الأونلاين بالمحرك المحاسبي."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.models.account import Account
from app.models.journal import JournalEntry, JournalSourceType
from app.models.order import OrderStatus
from app.models.party import PartyType
from app.models.sales import InvoiceStatus, SalesInvoice
from app.models.setting import set_setting
from app.services.inventory import record_purchase
from app.services.orders import OrderLineDraft, create_order, transition_status
from app.services.parties import create_party
from app.services.products import create_category, create_product
from seeds.chart_of_accounts import seed_chart_of_accounts


@pytest.fixture()
def env(app):
    seed_chart_of_accounts(_db.session)
    set_setting("tax.enabled", "false")
    set_setting("storefront.shipping_fee", "0.000")  # نبسط للاختبار
    set_setting("storefront.free_shipping_threshold", "0.000")
    _db.session.commit()

    tag = uuid.uuid4().hex[:8]
    customer = create_party(type=PartyType.CUSTOMER, name_ar=f"c{tag}", phone=f"010{tag}")
    cat = create_category(name_ar=f"c{tag}")
    product = create_product(
        name_ar=f"p{tag}", category_id=cat.id, default_price=Decimal("100"),
        variants=[{"variant_name": "افتراضي"}],
    )
    variant = product.variants[0]
    record_purchase(variant_id=variant.id, qty=50, unit_cost=40, move_date=date(2026, 1, 1))
    _db.session.commit()
    yield {"customer": customer, "variant": variant, "product": product}


# ---------- 1) hook التسليم ----------

class TestDeliveryHook:
    def test_delivered_order_creates_sales_invoice(self, env):
        order = create_order(
            lines=[OrderLineDraft(env["variant"].id, qty=3)],
            guest_name=env["customer"].name_ar,
            guest_phone=env["customer"].phone,
            shipping_address="عنوان",
            customer_id=env["customer"].id,
        )
        _db.session.commit()

        assert order.sales_invoice_id is None

        transition_status(order_id=order.id, new_status=OrderStatus.PROCESSING)
        transition_status(order_id=order.id, new_status=OrderStatus.SHIPPED)
        transition_status(order_id=order.id, new_status=OrderStatus.DELIVERED)
        _db.session.commit()

        assert order.sales_invoice_id is not None
        assert order.status == OrderStatus.DELIVERED

        invoice = _db.session.get(SalesInvoice, order.sales_invoice_id)
        assert invoice is not None
        assert invoice.doc_number.startswith("INV-")
        assert invoice.status == InvoiceStatus.POSTED
        assert invoice.total == Decimal(str(order.total))
        assert len(invoice.lines) == len(order.lines)

    def test_customer_ar_nets_to_zero_after_delivery(self, env):
        """قيد البيع + قيد التحصيل (COD) → صافي على AR = 0."""
        customer = env["customer"]
        ar_before = customer.account.compute_balance()

        order = create_order(
            lines=[OrderLineDraft(env["variant"].id, qty=2)],
            guest_name=customer.name_ar,
            guest_phone=customer.phone,
            shipping_address="عنوان",
            customer_id=customer.id,
        )
        _db.session.commit()
        transition_status(order_id=order.id, new_status=OrderStatus.PROCESSING)
        transition_status(order_id=order.id, new_status=OrderStatus.SHIPPED)
        transition_status(order_id=order.id, new_status=OrderStatus.DELIVERED)
        _db.session.commit()

        # AR بعد التسليم = قبل التسليم (لا رصيد مستحق)
        assert customer.account.compute_balance() == ar_before

    def test_cash_account_increased_by_order_total(self, env):
        cash = _db.session.query(Account).filter_by(code="1010").one()
        cash_before = cash.compute_balance()

        order = create_order(
            lines=[OrderLineDraft(env["variant"].id, qty=2)],
            guest_name=env["customer"].name_ar,
            guest_phone=env["customer"].phone,
            shipping_address="عنوان",
            customer_id=env["customer"].id,
        )
        _db.session.commit()
        transition_status(order_id=order.id, new_status=OrderStatus.PROCESSING)
        transition_status(order_id=order.id, new_status=OrderStatus.SHIPPED)
        transition_status(order_id=order.id, new_status=OrderStatus.DELIVERED)
        _db.session.commit()

        assert cash.compute_balance() == cash_before + Decimal(str(order.total))

    def test_stock_not_double_deducted(self, env):
        """المخزون خُصم في Phase 6 عند إنشاء الطلب — لا يُخصم مرة أخرى في Phase 8."""
        variant = env["variant"]
        stock_before = Decimal(str(variant.stock_qty))

        order = create_order(
            lines=[OrderLineDraft(variant.id, qty=4)],
            guest_name=env["customer"].name_ar,
            guest_phone=env["customer"].phone,
            shipping_address="عنوان",
            customer_id=env["customer"].id,
        )
        _db.session.commit()
        # بعد إنشاء الطلب — نقص 4
        assert variant.stock_qty == stock_before - Decimal("4")

        transition_status(order_id=order.id, new_status=OrderStatus.PROCESSING)
        transition_status(order_id=order.id, new_status=OrderStatus.SHIPPED)
        transition_status(order_id=order.id, new_status=OrderStatus.DELIVERED)
        _db.session.commit()

        # بعد التسليم — نفس الرصيد (لا خصم إضافي)
        assert variant.stock_qty == stock_before - Decimal("4")

    def test_cogs_uses_snapshot_from_order_time(self, env):
        """التكلفة في قيد COGS = التكلفة وقت الطلب، وليس التكلفة الحالية."""
        variant = env["variant"]

        order = create_order(
            lines=[OrderLineDraft(variant.id, qty=2)],
            guest_name=env["customer"].name_ar,
            guest_phone=env["customer"].phone,
            shipping_address="عنوان",
            customer_id=env["customer"].id,
        )
        _db.session.commit()

        # بعد الطلب، شراء إضافي بسعر مختلف يغيّر avg_cost
        record_purchase(variant_id=variant.id, qty=10, unit_cost=Decimal("100"),
                        move_date=date(2026, 3, 1))
        _db.session.commit()
        new_avg = Decimal(str(variant.avg_cost))
        assert new_avg != Decimal("40"), "avg_cost يجب أن يتغير بعد الشراء الجديد"

        transition_status(order_id=order.id, new_status=OrderStatus.PROCESSING)
        transition_status(order_id=order.id, new_status=OrderStatus.SHIPPED)
        transition_status(order_id=order.id, new_status=OrderStatus.DELIVERED)
        _db.session.commit()

        invoice = _db.session.get(SalesInvoice, order.sales_invoice_id)
        line = invoice.lines[0]
        # unit_cost في السطر = 40 (وقت الطلب) وليس new_avg
        assert line.unit_cost == Decimal("40.000")

    def test_cannot_deliver_twice(self, env):
        from app.services.orders import OrderError
        order = create_order(
            lines=[OrderLineDraft(env["variant"].id, qty=1)],
            guest_name="ك", guest_phone="011",
            shipping_address="عنوان", customer_id=env["customer"].id,
        )
        _db.session.commit()
        transition_status(order_id=order.id, new_status=OrderStatus.PROCESSING)
        transition_status(order_id=order.id, new_status=OrderStatus.SHIPPED)
        transition_status(order_id=order.id, new_status=OrderStatus.DELIVERED)
        _db.session.commit()

        with pytest.raises(OrderError, match="مسلَّم"):
            transition_status(order_id=order.id, new_status=OrderStatus.PROCESSING)


# ---------- 2) hook المرتجع بعد التسليم ----------

class TestReturnHook:
    def test_returned_order_reverses_journals(self, env):
        variant = env["variant"]
        customer = env["customer"]

        order = create_order(
            lines=[OrderLineDraft(variant.id, qty=3)],
            guest_name=customer.name_ar,
            guest_phone=customer.phone,
            shipping_address="عنوان",
            customer_id=customer.id,
        )
        _db.session.commit()
        transition_status(order_id=order.id, new_status=OrderStatus.PROCESSING)
        transition_status(order_id=order.id, new_status=OrderStatus.SHIPPED)
        transition_status(order_id=order.id, new_status=OrderStatus.DELIVERED)
        _db.session.commit()

        # الرصيد بعد التسليم
        ar_after_delivery = customer.account.compute_balance()
        cash_after_delivery = _db.session.query(Account).filter_by(code="1010").one().compute_balance()

        # مرتجع
        transition_status(order_id=order.id, new_status=OrderStatus.RETURNED,
                          return_reason="لم يعجبه")
        _db.session.commit()

        # AR بقي كما هو (لأنه صفر أصلاً بعد COD)
        assert customer.account.compute_balance() == ar_after_delivery
        # النقدية نقصت بقيمة الطلب (الرد للعميل)
        cash_after_return = _db.session.query(Account).filter_by(code="1010").one().compute_balance()
        assert cash_after_return == cash_after_delivery - Decimal(str(order.total))
        # الفاتورة تحوّلت لحالة RETURNED
        _db.session.refresh(order.sales_invoice)
        assert order.sales_invoice.status == InvoiceStatus.RETURNED
