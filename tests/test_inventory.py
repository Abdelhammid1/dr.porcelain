"""اختبارات المخزون — خصوصًا معادلة متوسط التكلفة المرجّح."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.services.barcode import compute_ean13_checksum, next_internal_ean13
from app.services.inventory import (
    InventoryError,
    record_adjustment,
    record_opening,
    record_purchase,
    record_purchase_return,
    record_sale,
    record_sale_return,
)
from app.services.products import create_category, create_product


@pytest.fixture()
def cat(app):
    c = create_category(name_ar="أدوات مطبخ")
    _db.session.commit()
    yield c


@pytest.fixture()
def variant(cat):
    p = create_product(
        name_ar="طبق سيراميك",
        category_id=cat.id,
        default_price=Decimal("100"),
        variants=[{"variant_name": "افتراضي", "reorder_level": 5}],
    )
    _db.session.commit()
    yield p.variants[0]


# ---------- 1) الشراء + متوسط التكلفة ----------

class TestWeightedAverageCost:
    def test_first_purchase_sets_avg_cost(self, variant):
        record_purchase(variant_id=variant.id, qty=10, unit_cost=15,
                        move_date=date(2026, 1, 1))
        _db.session.commit()
        assert variant.stock_qty == Decimal("10.000")
        assert variant.avg_cost == Decimal("15.000")

    def test_second_purchase_recomputes_avg(self, variant):
        # شراء 10 @ 15 = 150
        record_purchase(variant_id=variant.id, qty=10, unit_cost=15,
                        move_date=date(2026, 1, 1))
        # شراء 5 @ 21 = 105
        # المتوسط الجديد = (150 + 105) / 15 = 255/15 = 17.000
        record_purchase(variant_id=variant.id, qty=5, unit_cost=21,
                        move_date=date(2026, 1, 2))
        _db.session.commit()
        assert variant.stock_qty == Decimal("15.000")
        assert variant.avg_cost == Decimal("17.000")

    def test_sale_does_not_change_avg_cost(self, variant):
        record_purchase(variant_id=variant.id, qty=10, unit_cost=15,
                        move_date=date(2026, 1, 1))
        _db.session.commit()
        record_sale(variant_id=variant.id, qty=4, move_date=date(2026, 1, 5))
        _db.session.commit()
        assert variant.stock_qty == Decimal("6.000")
        assert variant.avg_cost == Decimal("15.000")

    def test_sale_returns_cost_snapshot(self, variant):
        record_purchase(variant_id=variant.id, qty=10, unit_cost=15,
                        move_date=date(2026, 1, 1))
        _db.session.commit()
        m = record_sale(variant_id=variant.id, qty=4, move_date=date(2026, 1, 5))
        _db.session.commit()
        # snapshot للتكلفة يُحفظ في unit_cost على سطر الحركة
        assert m.unit_cost == Decimal("15.000")

    def test_multiple_purchases_then_sale_uses_latest_avg(self, variant):
        record_purchase(variant_id=variant.id, qty=10, unit_cost=10,
                        move_date=date(2026, 1, 1))
        record_purchase(variant_id=variant.id, qty=10, unit_cost=20,
                        move_date=date(2026, 1, 2))
        # avg = (100 + 200) / 20 = 15
        _db.session.commit()
        m = record_sale(variant_id=variant.id, qty=5, move_date=date(2026, 1, 3))
        _db.session.commit()
        assert m.unit_cost == Decimal("15.000")

    def test_sale_return_does_not_change_avg_cost(self, variant):
        record_purchase(variant_id=variant.id, qty=10, unit_cost=15,
                        move_date=date(2026, 1, 1))
        _db.session.commit()
        record_sale(variant_id=variant.id, qty=4, move_date=date(2026, 1, 5))
        _db.session.commit()
        record_sale_return(variant_id=variant.id, qty=2, unit_cost=Decimal("15"),
                           move_date=date(2026, 1, 10))
        _db.session.commit()
        assert variant.stock_qty == Decimal("8.000")
        assert variant.avg_cost == Decimal("15.000")


# ---------- 2) قواعد البيع ----------

class TestSaleRules:
    def test_sale_without_enough_stock_rejected(self, variant):
        record_purchase(variant_id=variant.id, qty=3, unit_cost=10,
                        move_date=date(2026, 1, 1))
        _db.session.commit()
        with pytest.raises(InventoryError, match="أقل من المطلوبة"):
            record_sale(variant_id=variant.id, qty=5, move_date=date(2026, 1, 2))

    def test_negative_qty_rejected(self, variant):
        with pytest.raises(InventoryError, match="أكبر من صفر"):
            record_purchase(variant_id=variant.id, qty=-1, unit_cost=10,
                            move_date=date(2026, 1, 1))
        with pytest.raises(InventoryError, match="أكبر من صفر"):
            record_sale(variant_id=variant.id, qty=0, move_date=date(2026, 1, 1))


# ---------- 3) التسويات ----------

class TestAdjustment:
    def test_positive_adjustment_increases_stock(self, variant):
        record_adjustment(variant_id=variant.id, delta_qty=5,
                          move_date=date(2026, 1, 1), reason="جرد اكتشاف زيادة")
        _db.session.commit()
        assert variant.stock_qty == Decimal("5.000")

    def test_negative_adjustment_below_zero_rejected(self, variant):
        with pytest.raises(InventoryError, match="سالبًا"):
            record_adjustment(variant_id=variant.id, delta_qty=-1,
                              move_date=date(2026, 1, 1), reason="فقد")

    def test_adjustment_requires_reason(self, variant):
        with pytest.raises(InventoryError, match="سبب"):
            record_adjustment(variant_id=variant.id, delta_qty=3,
                              move_date=date(2026, 1, 1), reason="")


# ---------- 4) الرصيد الافتتاحي ----------

class TestOpening:
    def test_opening_sets_qty_and_cost(self, variant):
        record_opening(variant_id=variant.id, qty=20, unit_cost=12,
                       move_date=date(2026, 1, 1))
        _db.session.commit()
        assert variant.stock_qty == Decimal("20.000")
        assert variant.avg_cost == Decimal("12.000")

    def test_opening_forbidden_if_stock_exists(self, variant):
        record_purchase(variant_id=variant.id, qty=1, unit_cost=1,
                        move_date=date(2026, 1, 1))
        _db.session.commit()
        with pytest.raises(InventoryError, match="صفرًا"):
            record_opening(variant_id=variant.id, qty=5, unit_cost=10,
                           move_date=date(2026, 1, 2))


# ---------- 5) الباركود EAN-13 ----------

class TestBarcode:
    @pytest.mark.parametrize("body,expected", [
        # حسابات EAN-13: sum(odd_pos * 1 + even_pos * 3) → checksum = (10 - sum%10) % 10
        ("200000000001", 5),
        ("400638133393", 1),   # مثال من المواصفة
        ("012345678901", 2),
    ])
    def test_checksum_matches_reference(self, body, expected):
        assert compute_ean13_checksum(body) == expected

    def test_generated_barcodes_are_unique_and_valid(self, app):
        codes = [next_internal_ean13() for _ in range(5)]
        _db.session.commit()
        assert len(set(codes)) == 5, "الباركودات المولّدة يجب أن تكون فريدة"
        for c in codes:
            assert len(c) == 13
            assert c.startswith("200")
            # نتحقق من checksum
            assert compute_ean13_checksum(c[:12]) == int(c[12])
