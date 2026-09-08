"""اختبارات إضافات المنتج (Epics 1, 3, 4, 5)."""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.models.image import ProductImage
from app.models.product_extras import ProductCompositionLine, ProductFeature
from app.models.product_relation import ProductRelation, RelationType
from app.services.products import (
    ProductError,
    add_related_product,
    create_category,
    create_product,
    list_related_products,
    remove_related_product,
    set_product_composition,
    set_product_features,
)


@pytest.fixture()
def env(app):
    tag = uuid.uuid4().hex[:8]
    cat = create_category(name_ar=f"c{tag}")
    p1 = create_product(name_ar=f"p1-{tag}", category_id=cat.id,
                        default_price=Decimal("10"))
    p2 = create_product(name_ar=f"p2-{tag}", category_id=cat.id,
                        default_price=Decimal("20"))
    p3 = create_product(name_ar=f"p3-{tag}", category_id=cat.id,
                        default_price=Decimal("30"))
    _db.session.commit()
    yield {"cat": cat, "p1": p1, "p2": p2, "p3": p3}


# ---------------- Epic 3 — Features ----------------

class TestProductFeatures:
    def test_set_and_reset_features(self, env):
        set_product_features(env["p1"].id, ["مقاوم للحرارة", "آمن غسالة الأطباق"])
        _db.session.commit()
        _db.session.refresh(env["p1"])
        assert len(env["p1"].features) == 2
        assert env["p1"].features[0].text == "مقاوم للحرارة"
        assert env["p1"].features[0].display_order == 0

        # الاستدعاء التاني يستبدل — idempotent
        set_product_features(env["p1"].id, ["مقاوم للكسر"])
        _db.session.commit()
        _db.session.refresh(env["p1"])
        assert len(env["p1"].features) == 1
        assert env["p1"].features[0].text == "مقاوم للكسر"

    def test_empty_strings_ignored(self, env):
        set_product_features(env["p1"].id, ["", "   ", "ممتاز", ""])
        _db.session.commit()
        _db.session.refresh(env["p1"])
        assert len(env["p1"].features) == 1
        assert env["p1"].features[0].text == "ممتاز"


# ---------------- Epic 4 — Composition ----------------

class TestComposition:
    def test_set_composition_lines(self, env):
        set_product_composition(env["p1"].id, [
            {"quantity": 6, "content_name_ar": "طبق تقديم كبير"},
            {"quantity": 6, "content_name_ar": "طبق تقديم صغير"},
        ])
        _db.session.commit()
        _db.session.refresh(env["p1"])
        assert len(env["p1"].composition) == 2
        assert env["p1"].composition[0].quantity == 6
        assert env["p1"].composition[0].content_name_ar == "طبق تقديم كبير"

    def test_invalid_qty_rejected(self, env):
        with pytest.raises(ProductError, match="الكمية"):
            set_product_composition(env["p1"].id, [
                {"quantity": 0, "content_name_ar": "طبق"},
            ])

    def test_replacement_is_idempotent(self, env):
        set_product_composition(env["p1"].id, [
            {"quantity": 1, "content_name_ar": "أ"},
            {"quantity": 2, "content_name_ar": "ب"},
        ])
        _db.session.commit()
        set_product_composition(env["p1"].id, [
            {"quantity": 3, "content_name_ar": "ج"},
        ])
        _db.session.commit()
        _db.session.refresh(env["p1"])
        assert len(env["p1"].composition) == 1
        assert env["p1"].composition[0].content_name_ar == "ج"


# ---------------- Epic 5 — Relations ----------------

class TestRelations:
    def test_add_and_list_relations(self, env):
        add_related_product(env["p1"].id, env["p2"].id, RelationType.RELATED)
        add_related_product(env["p1"].id, env["p3"].id, RelationType.RELATED)
        _db.session.commit()
        rels = list_related_products(env["p1"].id, RelationType.RELATED)
        assert len(rels) == 2
        assert env["p2"] in rels
        assert env["p3"] in rels

    def test_self_relation_rejected(self, env):
        with pytest.raises(ProductError, match="بنفسه"):
            add_related_product(env["p1"].id, env["p1"].id)

    def test_duplicate_returns_existing(self, env):
        r1 = add_related_product(env["p1"].id, env["p2"].id, RelationType.RELATED)
        _db.session.commit()
        r2 = add_related_product(env["p1"].id, env["p2"].id, RelationType.RELATED)
        assert r1.id == r2.id

    def test_relation_types_are_independent(self, env):
        add_related_product(env["p1"].id, env["p2"].id, RelationType.RELATED)
        add_related_product(env["p1"].id, env["p2"].id, RelationType.FREQUENTLY_BOUGHT)
        _db.session.commit()
        assert len(list_related_products(env["p1"].id, RelationType.RELATED)) == 1
        assert len(list_related_products(env["p1"].id, RelationType.FREQUENTLY_BOUGHT)) == 1

    def test_string_relation_type_accepted(self, env):
        add_related_product(env["p1"].id, env["p2"].id, "related")
        _db.session.commit()
        assert len(list_related_products(env["p1"].id, "related")) == 1

    def test_remove_relation(self, env):
        rel = add_related_product(env["p1"].id, env["p2"].id)
        _db.session.commit()
        remove_related_product(rel.id)
        _db.session.commit()
        assert list_related_products(env["p1"].id) == []


# ---------------- Epic 1 — Product images (integration) ----------------

class TestProductImages:
    def test_first_image_is_primary(self, app, env):
        """محاكاة إضافة سجل ProductImage بدون رفع فعلي — نختبر منطق primary_image."""
        img1 = ProductImage(product_id=env["p1"].id, file_path="fake1.jpg",
                            display_order=0, is_primary=True)
        img2 = ProductImage(product_id=env["p1"].id, file_path="fake2.jpg",
                            display_order=1, is_primary=False)
        _db.session.add_all([img1, img2])
        _db.session.commit()
        _db.session.refresh(env["p1"])

        assert env["p1"].primary_image.id == img1.id
        assert len(env["p1"].images) == 2

    def test_primary_image_none_when_no_images(self, env):
        assert env["p1"].primary_image is None

    def test_primary_falls_back_to_first_when_none_marked(self, app, env):
        img1 = ProductImage(product_id=env["p1"].id, file_path="a.jpg",
                            display_order=0, is_primary=False)
        img2 = ProductImage(product_id=env["p1"].id, file_path="b.jpg",
                            display_order=1, is_primary=False)
        _db.session.add_all([img1, img2])
        _db.session.commit()
        _db.session.refresh(env["p1"])

        # لا يوجد primary صراحة → يرجع أول واحدة (display_order=0)
        assert env["p1"].primary_image.id == img1.id


# ---------------- Category images ----------------

class TestCategoryImages:
    """
    Category images ticket — Category جديد له عمود image_path يقبل صورة
    اختيارية عند الإنشاء أو التعديل، ويظهر في قوائم الأدمن والمتجر
    بدلاً من الأيقونة العامة.
    """

    def test_image_path_column_defaults_to_none(self, app):
        from app.models.category import Category
        from app.services.products import create_category

        cat = create_category(name_ar=f"tst-{uuid.uuid4().hex[:8]}")
        _db.session.commit()

        # التصنيف الجديد بلا صورة افتراضيًا → عمود nullable
        assert cat.image_path is None

    def test_image_path_can_be_set_directly(self, app):
        from app.services.products import create_category

        cat = create_category(name_ar=f"tst-{uuid.uuid4().hex[:8]}")
        cat.image_path = "uploads/categories/1/abc.jpg"
        _db.session.commit()
        _db.session.refresh(cat)

        assert cat.image_path == "uploads/categories/1/abc.jpg"

    def test_delete_category_image_service_clears_path(self, app):
        """delete_category_image يفرّغ العمود حتى لو الملف الأصلي مفقود."""
        from app.services.products import create_category
        from app.services.product_images import delete_category_image

        cat = create_category(name_ar=f"tst-{uuid.uuid4().hex[:8]}")
        cat.image_path = "uploads/categories/999/missing.jpg"
        _db.session.commit()

        delete_category_image(cat.id)
        _db.session.commit()
        _db.session.refresh(cat)

        assert cat.image_path is None
