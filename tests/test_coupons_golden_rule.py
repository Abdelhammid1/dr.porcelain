"""اختبارات كوبونات الخصم (Ticket 2 Epic 2) — يشمل حراسة القاعدة الذهبية.

القاعدة: قيمة الكوبون تُخفَض من subtotal على مستوى الطلب، وتنتقل تلقائيًا
إلى SalesInvoice.discount_amount عبر order_accounting.py:120، فينعكس القيد
المحاسبي على المبلغ الصافي (المدفوع فعليًا بعد الخصم).

الاختبار الرئيسي: كوبون 10% على طلب 100 → القيد يعكس 90 (وليس 100).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.models.account import Account
from app.models.coupon import DiscountCoupon, DiscountType, CouponUsage
from app.models.journal import JournalEntry, JournalSourceType
from app.models.order import OrderStatus
from app.models.party import PartyType
from app.models.sales import SalesInvoice
from app.models.setting import set_setting
from app.services.coupons import (
    CouponError, create_coupon, record_usage, validate_and_compute,
)
from app.services.inventory import record_purchase
from app.services.orders import OrderLineDraft, create_order, transition_status
from app.services.parties import create_party
from app.services.products import create_category, create_product
from seeds.chart_of_accounts import seed_chart_of_accounts


@pytest.fixture()
def env(app):
    seed_chart_of_accounts(_db.session)
    set_setting("tax.enabled", "false")
    set_setting("storefront.shipping_fee", "0.000")
    set_setting("storefront.free_shipping_threshold", "0.000")
    _db.session.commit()

    tag = uuid.uuid4().hex[:8]
    customer = create_party(type=PartyType.CUSTOMER, name_ar=f"c{tag}", phone=f"010{tag}")
    other = create_party(type=PartyType.CUSTOMER, name_ar=f"o{tag}", phone=f"011{tag}")
    cat = create_category(name_ar=f"c{tag}")
    product = create_product(
        name_ar=f"p{tag}", category_id=cat.id, default_price=Decimal("50"),
        variants=[{"variant_name": "افتراضي"}],
    )
    variant = product.variants[0]
    record_purchase(variant_id=variant.id, qty=50, unit_cost=Decimal("30"),
                    move_date=date(2026, 1, 1))
    _db.session.commit()
    yield {"customer": customer, "other": other, "variant": variant, "product": product}


# ============ Validation ============

class TestValidation:
    def test_valid_percentage_coupon(self, env):
        create_coupon(code="save10", discount_type="percentage", discount_value=10)
        _db.session.commit()
        info = validate_and_compute(code="SAVE10", subtotal=Decimal("100"))
        assert info["coupon"].code == "SAVE10"
        assert info["discount_amount"] == Decimal("10.000")

    def test_valid_fixed_coupon(self, env):
        create_coupon(code="off20", discount_type="fixed_amount", discount_value=20)
        _db.session.commit()
        info = validate_and_compute(code="OFF20", subtotal=Decimal("100"))
        assert info["discount_amount"] == Decimal("20.000")

    def test_unknown_code_rejected(self, env):
        with pytest.raises(CouponError, match="غير صالح"):
            validate_and_compute(code="NOPE", subtotal=Decimal("100"))

    def test_inactive_coupon_rejected(self, env):
        c = create_coupon(code="OFF", discount_type="percentage", discount_value=10)
        c.is_active = False
        _db.session.commit()
        with pytest.raises(CouponError, match="غير صالح"):
            validate_and_compute(code="OFF", subtotal=Decimal("100"))

    def test_expired_coupon_rejected(self, env):
        c = create_coupon(code="OLD", discount_type="percentage", discount_value=10)
        c.valid_until = datetime.now(timezone.utc) - timedelta(days=1)
        _db.session.commit()
        with pytest.raises(CouponError, match="انتهت"):
            validate_and_compute(code="OLD", subtotal=Decimal("100"))

    def test_below_min_order_rejected(self, env):
        create_coupon(code="BIG", discount_type="percentage", discount_value=10,
                      min_order_amount=Decimal("200"))
        _db.session.commit()
        with pytest.raises(CouponError, match="الحد الأدنى"):
            validate_and_compute(code="BIG", subtotal=Decimal("100"))

    def test_max_uses_total_enforced(self, env):
        c = create_coupon(code="LIMIT", discount_type="percentage",
                          discount_value=10, max_uses=1)
        _db.session.commit()
        # اصنع طلبًا لاستخدام الكوبون
        order = create_order(
            lines=[OrderLineDraft(variant_id=env["variant"].id, qty=1)],
            guest_name=env["customer"].name_ar, guest_phone=env["customer"].phone,
            shipping_address="عنوان", customer_id=env["customer"].id,
            coupon_code="LIMIT",
        )
        _db.session.commit()
        # المحاولة التانية ترفض
        with pytest.raises(CouponError, match="بالكامل"):
            validate_and_compute(code="LIMIT", subtotal=Decimal("100"),
                                 customer_id=env["other"].id)

    def test_per_customer_limit_enforced(self, env):
        create_coupon(code="ONCE", discount_type="percentage",
                      discount_value=10, max_uses_per_customer=1)
        _db.session.commit()
        create_order(
            lines=[OrderLineDraft(variant_id=env["variant"].id, qty=1)],
            guest_name=env["customer"].name_ar, guest_phone=env["customer"].phone,
            shipping_address="عنوان", customer_id=env["customer"].id,
            coupon_code="ONCE",
        )
        _db.session.commit()
        with pytest.raises(CouponError, match="سبق"):
            validate_and_compute(code="ONCE", subtotal=Decimal("100"),
                                 customer_id=env["customer"].id)
        # لكن عميل آخر يقدر يستخدمه
        info = validate_and_compute(code="ONCE", subtotal=Decimal("100"),
                                    customer_id=env["other"].id)
        assert info["discount_amount"] == Decimal("10.000")

    def test_duplicate_code_rejected_on_create(self, env):
        create_coupon(code="DUP", discount_type="percentage", discount_value=5)
        _db.session.commit()
        with pytest.raises(CouponError, match="مستخدم"):
            create_coupon(code="dup", discount_type="percentage", discount_value=10)


# ============ Golden Rule ============

class TestGoldenRuleCouponAccounting:
    """كوبون خصم يعكس المبلغ الصافي في القيد المحاسبي."""

    def test_order_stores_discount_from_coupon(self, env):
        code = "SAVE" + uuid.uuid4().hex[:6].upper()
        create_coupon(code=code, discount_type="percentage", discount_value=10)
        _db.session.commit()

        order = create_order(
            lines=[OrderLineDraft(variant_id=env["variant"].id, qty=2)],  # 2×50=100
            guest_name=env["customer"].name_ar,
            guest_phone=env["customer"].phone,
            shipping_address="عنوان",
            customer_id=env["customer"].id,
            coupon_code=code,
        )
        _db.session.commit()

        # subtotal يبقى الأصلي، discount_amount = 10% منه
        assert order.subtotal == Decimal("100.000")
        assert order.discount_amount == Decimal("10.000")
        assert order.total == Decimal("90.000")  # subtotal - discount + shipping(0)

        # OrderLine snapshot uses actual variant.price (not modified)
        assert order.lines[0].unit_price == Decimal("50.000")

    def test_coupon_usage_recorded(self, env):
        create_coupon(code="TRACK", discount_type="fixed_amount", discount_value=15)
        _db.session.commit()

        order = create_order(
            lines=[OrderLineDraft(variant_id=env["variant"].id, qty=2)],
            guest_name=env["customer"].name_ar, guest_phone=env["customer"].phone,
            shipping_address="عنوان", customer_id=env["customer"].id,
            coupon_code="TRACK",
        )
        _db.session.commit()

        usage = _db.session.query(CouponUsage).filter_by(order_id=order.id).one()
        assert usage.customer_id == env["customer"].id
        assert usage.discount_amount == Decimal("15.000")

    def test_delivered_invoice_reflects_discount_in_journal(self, env):
        """المفتاح: بعد تسليم الطلب، القيد المحاسبي يستخدم المبلغ الصافي."""
        create_coupon(code="NET", discount_type="percentage", discount_value=10)
        _db.session.commit()

        order = create_order(
            lines=[OrderLineDraft(variant_id=env["variant"].id, qty=2)],  # 100
            guest_name=env["customer"].name_ar, guest_phone=env["customer"].phone,
            shipping_address="عنوان", customer_id=env["customer"].id,
            coupon_code="NET",
        )
        _db.session.commit()
        assert order.discount_amount == Decimal("10.000")
        assert order.total == Decimal("90.000")

        transition_status(order_id=order.id, new_status=OrderStatus.PROCESSING)
        transition_status(order_id=order.id, new_status=OrderStatus.SHIPPED)
        transition_status(order_id=order.id, new_status=OrderStatus.DELIVERED)
        _db.session.commit()

        # SalesInvoice المُنشأة تحمل نفس discount_amount
        invoice = _db.session.get(SalesInvoice, order.sales_invoice_id)
        assert invoice.subtotal == Decimal("100.000")
        assert invoice.discount_amount == Decimal("10.000")
        assert invoice.total == Decimal("90.000")

        # قيد التحصيل COD: كاش يستلمه المندوب = 90 (وليس 100)
        cash_acc = _db.session.query(Account).filter_by(code="1010").one()
        cash_entries = (
            _db.session.query(JournalEntry)
            .filter(JournalEntry.source_id == invoice.id)
            .filter(JournalEntry.source_type == JournalSourceType.CUSTOMER_RECEIPT)
            .all()
        )
        assert len(cash_entries) == 1
        cash_line = next(l for l in cash_entries[0].lines if l.account_id == cash_acc.id)
        assert cash_line.debit == Decimal("90.000"), \
            "المفروض النقدية المستلمة = total (بعد الخصم) = 90، مش 100"

        # كل القيود متوازنة
        all_entries = (
            _db.session.query(JournalEntry)
            .filter(JournalEntry.source_id == invoice.id)
            .all()
        )
        for e in all_entries:
            assert e.is_balanced, f"قيد غير متوازن: {e.doc_number}"

    def test_invalid_coupon_at_order_raises(self, env):
        from app.services.orders import OrderError
        with pytest.raises(OrderError, match="غير صالح"):
            create_order(
                lines=[OrderLineDraft(variant_id=env["variant"].id, qty=1)],
                guest_name=env["customer"].name_ar, guest_phone=env["customer"].phone,
                shipping_address="عنوان", customer_id=env["customer"].id,
                coupon_code="DOES_NOT_EXIST",
            )
