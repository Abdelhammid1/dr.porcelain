"""اختبارات مراجعات المنتجات (Epic 6)."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.models.party import PartyType
from app.models.review import ProductReview
from app.services.inventory import record_purchase
from app.services.parties import create_party
from app.services.products import create_category, create_product
from app.services.reviews import (
    ReviewError,
    approve_review,
    average_rating,
    customer_has_purchased_product,
    reject_review,
    submit_review,
)
from app.services.sales import InvoiceLineDraft, create_cash_sale
from seeds.chart_of_accounts import seed_chart_of_accounts


@pytest.fixture()
def env(app):
    seed_chart_of_accounts(_db.session)
    _db.session.commit()

    tag = uuid.uuid4().hex[:8]
    buyer = create_party(type=PartyType.CUSTOMER, name_ar=f"buyer{tag}", phone=f"010{tag}")
    other = create_party(type=PartyType.CUSTOMER, name_ar=f"other{tag}", phone=f"011{tag}")

    cat = create_category(name_ar=f"c{tag}")
    product = create_product(
        name_ar=f"p{tag}",
        category_id=cat.id,
        default_price=Decimal("50"),
        variants=[{"variant_name": "افتراضي"}],
    )
    variant = product.variants[0]
    record_purchase(variant_id=variant.id, qty=20, unit_cost=Decimal("30"),
                    move_date=date(2026, 1, 1))
    _db.session.commit()

    # اجعل buyer يشتري المنتج فعليًا (فاتورة مسجّلة)
    create_cash_sale(
        customer_id=buyer.id,
        invoice_date=date(2026, 2, 1),
        lines=[InvoiceLineDraft(variant_id=variant.id, qty=1, unit_price=50)],
    )
    _db.session.commit()

    yield {"buyer": buyer, "other": other, "product": product, "variant": variant}


class TestPurchaseEligibility:
    def test_buyer_can_review(self, env):
        assert customer_has_purchased_product(env["buyer"].id, env["product"].id) is True

    def test_non_buyer_cannot_review(self, env):
        assert customer_has_purchased_product(env["other"].id, env["product"].id) is False


class TestSubmitReview:
    def test_buyer_can_submit_review(self, env):
        r = submit_review(customer_id=env["buyer"].id, product_id=env["product"].id,
                          rating=5, comment="ممتاز جدًا")
        _db.session.commit()
        assert r.id is not None
        assert r.rating == 5
        assert r.comment == "ممتاز جدًا"
        assert r.is_approved is False  # يبدأ قيد المراجعة

    def test_non_buyer_gets_review_error(self, env):
        with pytest.raises(ReviewError, match="لم تشتره"):
            submit_review(customer_id=env["other"].id, product_id=env["product"].id,
                          rating=5)

    def test_rejects_invalid_rating(self, env):
        with pytest.raises(ReviewError):
            submit_review(customer_id=env["buyer"].id, product_id=env["product"].id,
                          rating=0)
        with pytest.raises(ReviewError):
            submit_review(customer_id=env["buyer"].id, product_id=env["product"].id,
                          rating=6)
        with pytest.raises(ReviewError):
            submit_review(customer_id=env["buyer"].id, product_id=env["product"].id,
                          rating="مش رقم")

    def test_prevents_duplicate_review(self, env):
        submit_review(customer_id=env["buyer"].id, product_id=env["product"].id, rating=4)
        _db.session.commit()
        with pytest.raises(ReviewError, match="سبق"):
            submit_review(customer_id=env["buyer"].id, product_id=env["product"].id, rating=5)


class TestModeration:
    def test_approve_makes_visible(self, env):
        r = submit_review(customer_id=env["buyer"].id, product_id=env["product"].id, rating=4)
        _db.session.commit()
        assert env["product"].approved_reviews == []

        approve_review(r.id)
        _db.session.commit()
        _db.session.refresh(env["product"])
        assert r in env["product"].approved_reviews

    def test_reject_deletes(self, env):
        r = submit_review(customer_id=env["buyer"].id, product_id=env["product"].id, rating=3)
        _db.session.commit()
        rid = r.id
        reject_review(rid)
        _db.session.commit()
        assert _db.session.get(ProductReview, rid) is None


class TestAverageRating:
    def test_zero_when_no_approved(self, env):
        avg, cnt = average_rating(env["product"].id)
        assert cnt == 0
        assert avg == Decimal("0")

    def test_only_counts_approved(self, env):
        r = submit_review(customer_id=env["buyer"].id, product_id=env["product"].id, rating=5)
        _db.session.commit()
        # قبل الموافقة → 0
        avg, cnt = average_rating(env["product"].id)
        assert cnt == 0

        approve_review(r.id)
        _db.session.commit()
        avg, cnt = average_rating(env["product"].id)
        assert cnt == 1
        assert avg == Decimal("5.0")

    def test_product_properties_reflect_approved(self, env):
        r = submit_review(customer_id=env["buyer"].id, product_id=env["product"].id, rating=4)
        _db.session.commit()
        _db.session.refresh(env["product"])
        # قبل الموافقة
        assert env["product"].review_count == 0
        assert env["product"].avg_rating == Decimal("0")

        approve_review(r.id)
        _db.session.commit()
        _db.session.refresh(env["product"])
        assert env["product"].review_count == 1
        assert env["product"].avg_rating == Decimal("4.0")
