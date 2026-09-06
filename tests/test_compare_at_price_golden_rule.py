"""القاعدة الذهبية — `compare_at_price` عرض فقط.

الاختبار: عند وجود سعر مقارن (SKU على عرض)، القيود المحاسبية يجب أن تستخدم
`ProductVariant.price` (السعر الفعلي المدفوع) وليس `compare_at_price`.

هذا الاختبار حارس: أي regression في `services/sales.py:145` أو
`services/orders.py:97` أو `services/order_accounting.py:138` سيكسر الاختبار.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
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
from app.services.sales import InvoiceLineDraft, create_cash_sale
from seeds.chart_of_accounts import seed_chart_of_accounts


@pytest.fixture()
def env_with_sale(app):
    """منتج بسعر=40 و compare_at_price=80 + مخزون 50 قطعة."""
    seed_chart_of_accounts(_db.session)
    set_setting("tax.enabled", "false")
    set_setting("storefront.shipping_fee", "0.000")
    set_setting("storefront.free_shipping_threshold", "0.000")
    _db.session.commit()

    tag = uuid.uuid4().hex[:8]
    customer = create_party(type=PartyType.CUSTOMER, name_ar=f"c{tag}", phone=f"010{tag}")
    cat = create_category(name_ar=f"c{tag}")
    product = create_product(
        name_ar=f"p{tag}",
        category_id=cat.id,
        default_price=Decimal("40"),
        variants=[{
            "variant_name": "افتراضي",
            "price": Decimal("40"),
            "compare_at_price": Decimal("80"),  # عرض فقط
        }],
    )
    variant = product.variants[0]
    assert variant.compare_at_price == Decimal("80.000")
    assert variant.is_on_sale is True

    record_purchase(variant_id=variant.id, qty=50, unit_cost=Decimal("30"),
                    move_date=date(2026, 1, 1))
    _db.session.commit()

    yield {"customer": customer, "variant": variant, "product": product}


class TestGoldenRuleCashSale:
    """المسار المباشر: create_cash_sale (POS أو فاتورة يدوية)."""

    def test_invoice_line_uses_price_not_compare_at(self, env_with_sale):
        variant = env_with_sale["variant"]
        inv = create_cash_sale(
            customer_id=env_with_sale["customer"].id,
            invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=variant.id, qty=2)],
        )
        _db.session.commit()

        line = inv.lines[0]
        # القاعدة الذهبية: unit_price هو price الفعلي، ليس compare_at_price
        assert line.unit_price == Decimal("40.000"), \
            "unit_price يجب أن يكون price (40)، ليس compare_at_price (80)"
        assert line.unit_price != Decimal("80.000")
        # line_total = qty × price
        assert line.line_total == Decimal("80.000"), \
            "line_total يجب أن يكون 2×40=80، ليس 2×80=160"
        assert inv.subtotal == Decimal("80.000")
        assert inv.total == Decimal("80.000")

    def test_revenue_journal_reflects_paid_price(self, env_with_sale):
        variant = env_with_sale["variant"]
        inv = create_cash_sale(
            customer_id=env_with_sale["customer"].id,
            invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=variant.id, qty=2)],
        )
        _db.session.commit()

        revenue_acc = _db.session.query(Account).filter_by(code="4100").one()
        # القيد على 4100 يجب أن يعكس السعر المدفوع (80)، ليس السعر المقارن (160)
        sale_entry = (
            _db.session.query(JournalEntry)
            .filter_by(source_id=inv.id, source_type=JournalSourceType.SALES_INVOICE)
            .filter(JournalEntry.memo.like("فاتورة بيع%"))
            .first()
        )
        assert sale_entry is not None
        revenue_line = next(l for l in sale_entry.lines if l.account_id == revenue_acc.id)
        assert revenue_line.credit == Decimal("80.000"), \
            "إيراد 4100 = 80 (2×price)، ليس 160 (2×compare_at_price)"

    def test_all_journals_balanced(self, env_with_sale):
        variant = env_with_sale["variant"]
        inv = create_cash_sale(
            customer_id=env_with_sale["customer"].id,
            invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=variant.id, qty=2)],
        )
        _db.session.commit()

        entries = (
            _db.session.query(JournalEntry)
            .filter(JournalEntry.source_id == inv.id)
            .all()
        )
        assert len(entries) >= 3
        for e in entries:
            assert e.is_balanced, (
                f"قيد غير متوازن: {e.doc_number} — القاعدة الذهبية مكسورة"
            )


class TestGoldenRuleStorefrontOrder:
    """المسار الأونلاين: create_order → DELIVERED → SalesInvoice تلقائيًا."""

    def test_order_line_and_invoice_use_price_not_compare_at(self, env_with_sale):
        variant = env_with_sale["variant"]
        customer = env_with_sale["customer"]

        order = create_order(
            lines=[OrderLineDraft(variant_id=variant.id, qty=2)],
            guest_name=customer.name_ar,
            guest_phone=customer.phone,
            shipping_address="عنوان اختبار",
            customer_id=customer.id,
        )
        _db.session.commit()

        # OrderLine snapshot يستخدم price، ليس compare_at_price
        assert order.lines[0].unit_price == Decimal("40.000")
        assert order.lines[0].line_total == Decimal("80.000")

        transition_status(order_id=order.id, new_status=OrderStatus.PROCESSING)
        transition_status(order_id=order.id, new_status=OrderStatus.SHIPPED)
        transition_status(order_id=order.id, new_status=OrderStatus.DELIVERED)
        _db.session.commit()

        # SalesInvoice المُنشأة تلقائيًا تستخدم نفس السعر
        invoice = _db.session.get(SalesInvoice, order.sales_invoice_id)
        assert invoice is not None
        assert invoice.lines[0].unit_price == Decimal("40.000")
        assert invoice.subtotal == Decimal("80.000")

    def test_storefront_revenue_journal_balanced_at_paid_price(self, env_with_sale):
        variant = env_with_sale["variant"]
        customer = env_with_sale["customer"]

        order = create_order(
            lines=[OrderLineDraft(variant_id=variant.id, qty=2)],
            guest_name=customer.name_ar,
            guest_phone=customer.phone,
            shipping_address="عنوان اختبار",
            customer_id=customer.id,
        )
        _db.session.commit()
        transition_status(order_id=order.id, new_status=OrderStatus.PROCESSING)
        transition_status(order_id=order.id, new_status=OrderStatus.SHIPPED)
        transition_status(order_id=order.id, new_status=OrderStatus.DELIVERED)
        _db.session.commit()

        # كل قيود الفاتورة المُتولّدة عن الطلب متوازنة وتستخدم price=40
        invoice = _db.session.get(SalesInvoice, order.sales_invoice_id)
        entries = (
            _db.session.query(JournalEntry)
            .filter(JournalEntry.source_id == invoice.id)
            .all()
        )
        revenue_acc = _db.session.query(Account).filter_by(code="4100").one()
        sale_entry = next(
            e for e in entries
            if e.source_type == JournalSourceType.SALES_INVOICE
            and any(l.account_id == revenue_acc.id for l in e.lines)
        )
        revenue_line = next(l for l in sale_entry.lines if l.account_id == revenue_acc.id)
        assert revenue_line.credit == Decimal("80.000")
        for e in entries:
            assert e.is_balanced


class TestVariantSaleProperties:
    """اختبارات الـ properties المضافة على ProductVariant."""

    def test_is_on_sale_true_when_compare_gt_price(self, env_with_sale):
        v = env_with_sale["variant"]
        assert v.is_on_sale is True
        assert v.savings_amount == Decimal("40.000")
        assert v.discount_percent == Decimal("50.0")

    def test_is_on_sale_false_without_compare(self, env_with_sale):
        v = env_with_sale["variant"]
        v.compare_at_price = None
        _db.session.flush()
        assert v.is_on_sale is False
        assert v.savings_amount == Decimal("0")
        assert v.discount_percent == Decimal("0")

    def test_is_on_sale_false_when_compare_le_price(self, env_with_sale):
        v = env_with_sale["variant"]
        v.compare_at_price = Decimal("40")  # equal → not on sale
        _db.session.flush()
        assert v.is_on_sale is False


class TestOfferIsLive:
    def test_offer_is_live_in_future(self, env_with_sale):
        from datetime import timedelta
        p = env_with_sale["product"]
        p.offer_ends_at = datetime.now(timezone.utc) + timedelta(days=1)
        _db.session.flush()
        assert p.offer_is_live is True

    def test_offer_not_live_in_past(self, env_with_sale):
        from datetime import timedelta
        p = env_with_sale["product"]
        p.offer_ends_at = datetime.now(timezone.utc) - timedelta(days=1)
        _db.session.flush()
        assert p.offer_is_live is False

    def test_offer_not_live_when_none(self, env_with_sale):
        p = env_with_sale["product"]
        p.offer_ends_at = None
        _db.session.flush()
        assert p.offer_is_live is False
