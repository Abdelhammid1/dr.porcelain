"""اختبارات Wishlist والبحث (Ticket 2 Epics 1 + 4)."""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.models.party import PartyType
from app.models.wishlist import WishlistItem
from app.services.parties import create_party
from app.services.products import (
    create_category, create_product, set_product_composition,
)
from app.services import wishlist as wishlist_service


@pytest.fixture()
def env(app):
    tag = uuid.uuid4().hex[:8]
    customer = create_party(type=PartyType.CUSTOMER, name_ar=f"c{tag}", phone=f"010{tag}")
    other = create_party(type=PartyType.CUSTOMER, name_ar=f"o{tag}", phone=f"011{tag}")
    cat = create_category(name_ar=f"cat{tag}")
    p1 = create_product(name_ar=f"طقم شاي فاخر {tag}", category_id=cat.id,
                        default_price=Decimal("100"), brand="سيراميك")
    p2 = create_product(name_ar=f"كوب قهوة {tag}", category_id=cat.id,
                        default_price=Decimal("30"), brand="بورسلين",
                        description="كوب رائع لتقديم القهوة")
    _db.session.commit()
    # p1 عبارة عن طقم فيه حلة
    set_product_composition(p1.id, [
        {"quantity": 1, "content_name_ar": "إبريق شاي"},
        {"quantity": 6, "content_name_ar": "حلة شاي صغيرة"},
    ])
    _db.session.commit()
    yield {"customer": customer, "other": other, "p1": p1, "p2": p2, "cat": cat}


# =============== Wishlist ===============

class TestWishlistService:
    def test_add_and_remove(self, env):
        wishlist_service.add(env["customer"].id, env["p1"].id)
        _db.session.commit()
        assert wishlist_service.contains(env["customer"].id, env["p1"].id) is True
        assert wishlist_service.count(env["customer"].id) == 1

        wishlist_service.remove(env["customer"].id, env["p1"].id)
        _db.session.commit()
        assert wishlist_service.contains(env["customer"].id, env["p1"].id) is False
        assert wishlist_service.count(env["customer"].id) == 0

    def test_duplicate_add_returns_existing(self, env):
        w1 = wishlist_service.add(env["customer"].id, env["p1"].id)
        _db.session.commit()
        w2 = wishlist_service.add(env["customer"].id, env["p1"].id)
        assert w1.id == w2.id
        assert wishlist_service.count(env["customer"].id) == 1

    def test_customers_have_separate_wishlists(self, env):
        wishlist_service.add(env["customer"].id, env["p1"].id)
        wishlist_service.add(env["other"].id, env["p2"].id)
        _db.session.commit()
        assert wishlist_service.count(env["customer"].id) == 1
        assert wishlist_service.count(env["other"].id) == 1
        assert wishlist_service.contains(env["customer"].id, env["p2"].id) is False

    def test_count_zero_for_missing_customer(self, env):
        assert wishlist_service.count(None) == 0
        assert wishlist_service.contains(None, env["p1"].id) is False


# =============== Search (via HTTP) ===============

class TestStorefrontSearch:
    def test_search_finds_by_name(self, env, client):
        r = client.get("/shop/search?q=طقم")
        assert r.status_code == 200
        # اسم p1 يحتوي "طقم"
        assert env["p1"].name_ar.encode() in r.data

    def test_search_finds_by_brand(self, env, client):
        r = client.get("/shop/search?q=بورسلين")
        assert r.status_code == 200
        assert env["p2"].name_ar.encode() in r.data

    def test_search_finds_by_description(self, env, client):
        r = client.get("/shop/search?q=القهوة")
        assert r.status_code == 200
        assert env["p2"].name_ar.encode() in r.data

    def test_search_finds_by_composition_content(self, env, client):
        """Ticket 2 Epic 1 (ملاحظة خاصة): بحث في تكوين الطقم."""
        # "حلة" موجودة في composition لـ p1 وليس في name_ar
        r = client.get("/shop/search?q=حلة")
        assert r.status_code == 200
        assert env["p1"].name_ar.encode() in r.data

    def test_search_empty_shows_message(self, env, client):
        r = client.get("/shop/search?q=xyznotfound12345")
        assert r.status_code == 200
        assert "مفيش نتائج".encode() in r.data
