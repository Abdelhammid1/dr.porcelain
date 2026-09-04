"""خدمة إدارة المنتجات والمتغيرات."""
from __future__ import annotations

from decimal import Decimal

from app.extensions import db
from app.models.category import Category
from app.models.product import Product, ProductVariant
from app.models.setting import get_setting
from app.services.barcode import next_internal_ean13


class ProductError(ValueError):
    pass


def _as_dec(x, default="0") -> Decimal:
    if x is None or x == "":
        return Decimal(default)
    return x if isinstance(x, Decimal) else Decimal(str(x))


# ============ التصنيفات ============

def create_category(*, name_ar: str, parent_id: int | None = None, display_order: int = 0) -> Category:
    name_ar = (name_ar or "").strip()
    if not name_ar:
        raise ProductError("اسم التصنيف مطلوب.")

    parent = None
    if parent_id is not None:
        parent = db.session.get(Category, parent_id)
        if parent is None:
            raise ProductError("التصنيف الأب غير موجود.")
        if parent.depth >= Category.MAX_DEPTH:
            raise ProductError(
                f"لا يمكن إضافة تصنيف تحت هذا المستوى — الحد الأقصى {Category.MAX_DEPTH} مستويات."
            )

    cat = Category(
        name_ar=name_ar,
        parent_id=parent.id if parent else None,
        display_order=display_order,
        is_active=True,
    )
    db.session.add(cat)
    db.session.flush()
    return cat


def update_category(cat_id: int, *, name_ar: str | None = None, display_order: int | None = None,
                    is_active: bool | None = None) -> Category:
    cat = db.session.get(Category, cat_id)
    if cat is None:
        raise ProductError("التصنيف غير موجود.")
    if name_ar is not None:
        name_ar = name_ar.strip()
        if not name_ar:
            raise ProductError("الاسم لا يمكن أن يكون فارغًا.")
        cat.name_ar = name_ar
    if display_order is not None:
        cat.display_order = int(display_order)
    if is_active is not None:
        cat.is_active = bool(is_active)
    db.session.flush()
    return cat


def delete_category(cat_id: int) -> None:
    cat = db.session.get(Category, cat_id)
    if cat is None:
        return
    # ممنوع الحذف لو له أبناء أو منتجات
    if cat.children:
        raise ProductError("لا يمكن حذف تصنيف له تصنيفات فرعية.")
    if db.session.query(Product.id).filter_by(category_id=cat.id).first() is not None:
        raise ProductError("لا يمكن حذف تصنيف له منتجات — انقل المنتجات أولاً.")
    db.session.delete(cat)
    db.session.flush()


# ============ المنتجات ============

def create_product(
    *,
    name_ar: str,
    category_id: int,
    description: str | None = None,
    brand: str | None = None,
    unit: str | None = "قطعة",
    default_price: Decimal | float | str | None = 0,
    tax_rate_override: Decimal | float | str | None = None,
    variants: list[dict] | None = None,
) -> Product:
    """يُنشِئ منتجًا مع متغيراته.

    variants: قائمة قواميس بالمفاتيح المتاحة:
        - sku (اختياري — يُولَّد لو غاب)
        - color (اختياري)
        - variant_name (اختياري — الافتراضي "افتراضي")
        - barcode (اختياري — يُولَّد EAN-13 لو غاب وإعداد المتجر يسمح)
        - price (اختياري — الافتراضي default_price)
        - reorder_level (اختياري — الافتراضي من الإعدادات)
    لو variants=None نُنشِئ متغيرًا واحدًا افتراضيًا.
    """
    name_ar = (name_ar or "").strip()
    if not name_ar:
        raise ProductError("اسم المنتج مطلوب.")

    category = db.session.get(Category, category_id)
    if category is None:
        raise ProductError("التصنيف غير موجود.")

    default_price_dec = _as_dec(default_price)

    product = Product(
        name_ar=name_ar,
        description=(description or None),
        category_id=category.id,
        brand=(brand or None),
        unit=(unit or "قطعة"),
        default_price=default_price_dec,
        tax_rate_override=_as_dec(tax_rate_override) if tax_rate_override not in (None, "") else None,
        is_active=True,
    )
    db.session.add(product)
    db.session.flush()

    variant_specs = variants or [{"variant_name": "افتراضي"}]
    default_reorder = int(get_setting("products.low_stock_default", 5) or 0)
    autogen_barcode = bool(get_setting("products.autogenerate_barcode", True))

    used_skus: set[str] = set()
    for idx, spec in enumerate(variant_specs, start=1):
        sku = (spec.get("sku") or "").strip() or _generate_sku(product.id, idx, spec.get("color"))
        if sku in used_skus:
            raise ProductError(f"SKU مكرر داخل نفس المنتج: {sku}")
        used_skus.add(sku)

        # فحص عدم التكرار عبر النظام
        if db.session.query(ProductVariant.id).filter_by(sku=sku).first() is not None:
            raise ProductError(f"SKU مستخدم بالفعل: {sku}")

        barcode = (spec.get("barcode") or "").strip() or None
        if barcode is None and autogen_barcode:
            barcode = next_internal_ean13()
        elif barcode is not None:
            if db.session.query(ProductVariant.id).filter_by(barcode=barcode).first() is not None:
                raise ProductError(f"الباركود {barcode} مستخدم بالفعل.")

        price = _as_dec(spec.get("price"), str(default_price_dec))
        reorder_level = _as_dec(spec.get("reorder_level"), str(default_reorder))

        db.session.add(ProductVariant(
            product_id=product.id,
            sku=sku,
            barcode=barcode,
            color=(spec.get("color") or None),
            variant_name=(spec.get("variant_name") or ("افتراضي" if len(variant_specs) == 1 else None)),
            price=price,
            avg_cost=Decimal("0"),
            stock_qty=Decimal("0"),
            reorder_level=reorder_level,
            is_active=True,
        ))

    db.session.flush()
    return product


def update_product(
    product_id: int,
    *,
    name_ar: str | None = None,
    category_id: int | None = None,
    description: str | None = None,
    brand: str | None = None,
    unit: str | None = None,
    default_price: Decimal | float | str | None = None,
    tax_rate_override: Decimal | float | str | None = None,
    is_active: bool | None = None,
) -> Product:
    p = db.session.get(Product, product_id)
    if p is None:
        raise ProductError("المنتج غير موجود.")

    if name_ar is not None:
        name_ar = name_ar.strip()
        if not name_ar:
            raise ProductError("الاسم مطلوب.")
        p.name_ar = name_ar
    if category_id is not None:
        cat = db.session.get(Category, category_id)
        if cat is None:
            raise ProductError("التصنيف غير موجود.")
        p.category_id = cat.id
    if description is not None:
        p.description = description.strip() or None
    if brand is not None:
        p.brand = brand.strip() or None
    if unit is not None:
        p.unit = unit.strip() or "قطعة"
    if default_price is not None:
        p.default_price = _as_dec(default_price)
    if tax_rate_override is not None:
        p.tax_rate_override = _as_dec(tax_rate_override) if tax_rate_override != "" else None
    if is_active is not None:
        p.is_active = bool(is_active)
    db.session.flush()
    return p


# ============ المتغيرات ============

def add_variant(
    product_id: int,
    *,
    sku: str | None = None,
    color: str | None = None,
    variant_name: str | None = None,
    barcode: str | None = None,
    price: Decimal | float | str | None = None,
    reorder_level: Decimal | float | str | None = None,
) -> ProductVariant:
    p = db.session.get(Product, product_id)
    if p is None:
        raise ProductError("المنتج غير موجود.")

    resolved_sku = (sku or "").strip() or _generate_sku(product_id, len(p.variants) + 1, color)
    if db.session.query(ProductVariant.id).filter_by(sku=resolved_sku).first() is not None:
        raise ProductError(f"SKU مستخدم بالفعل: {resolved_sku}")

    resolved_barcode = (barcode or "").strip() or None
    if resolved_barcode is None and bool(get_setting("products.autogenerate_barcode", True)):
        resolved_barcode = next_internal_ean13()
    elif resolved_barcode is not None:
        if db.session.query(ProductVariant.id).filter_by(barcode=resolved_barcode).first() is not None:
            raise ProductError(f"الباركود {resolved_barcode} مستخدم بالفعل.")

    v = ProductVariant(
        product_id=product_id,
        sku=resolved_sku,
        barcode=resolved_barcode,
        color=(color or None),
        variant_name=variant_name,
        price=_as_dec(price, str(p.default_price)),
        avg_cost=Decimal("0"),
        stock_qty=Decimal("0"),
        reorder_level=_as_dec(reorder_level, str(get_setting("products.low_stock_default", 5) or 0)),
        is_active=True,
    )
    db.session.add(v)
    db.session.flush()
    return v


def update_variant(
    variant_id: int,
    *,
    sku: str | None = None,
    color: str | None = None,
    variant_name: str | None = None,
    barcode: str | None = None,
    price: Decimal | float | str | None = None,
    reorder_level: Decimal | float | str | None = None,
    is_active: bool | None = None,
) -> ProductVariant:
    v = db.session.get(ProductVariant, variant_id)
    if v is None:
        raise ProductError("المتغير غير موجود.")

    if sku is not None:
        new_sku = sku.strip()
        if new_sku != v.sku:
            if db.session.query(ProductVariant.id).filter(
                ProductVariant.sku == new_sku, ProductVariant.id != v.id
            ).first() is not None:
                raise ProductError(f"SKU مستخدم بالفعل: {new_sku}")
            v.sku = new_sku
    if color is not None:
        v.color = color.strip() or None
    if variant_name is not None:
        v.variant_name = variant_name.strip() or None
    if barcode is not None:
        new_bc = barcode.strip() or None
        if new_bc and new_bc != v.barcode:
            if db.session.query(ProductVariant.id).filter(
                ProductVariant.barcode == new_bc, ProductVariant.id != v.id
            ).first() is not None:
                raise ProductError(f"الباركود {new_bc} مستخدم بالفعل.")
        v.barcode = new_bc
    if price is not None:
        v.price = _as_dec(price)
    if reorder_level is not None:
        v.reorder_level = _as_dec(reorder_level)
    if is_active is not None:
        v.is_active = bool(is_active)
    db.session.flush()
    return v


# ============ Helpers ============

def _generate_sku(product_id: int, variant_index: int, color: str | None) -> str:
    """كود SKU افتراضي: P{product_id}-{index}[-{color_slug}]."""
    slug = ""
    if color:
        slug = "-" + "".join(ch for ch in color.strip().replace(" ", "-") if ch.isalnum() or ch in "-_")[:20]
    return f"P{product_id:06d}-{variant_index:02d}{slug}"
