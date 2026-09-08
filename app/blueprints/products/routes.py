"""Routes للمنتجات والتصنيفات والمخزون."""
from __future__ import annotations

from decimal import Decimal

from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from app.blueprints.products import products_bp
from app.blueprints.products.forms import CategoryForm, ProductForm
from app.extensions import db
from app.models.category import Category
from app.models.image import ProductImage
from app.models.inventory import InventoryMovement
from app.models.product import Product, ProductVariant
from app.models.product_relation import ProductRelation, RelationType
from app.services.inventory import low_stock_variants
from app.services.product_images import (
    ImageError,
    delete_category_image,
    delete_product_image,
    reorder_images,
    save_category_image,
    save_product_image,
    set_primary,
)
from app.services.products import (
    ProductError,
    add_related_product,
    add_variant,
    create_category,
    create_product,
    delete_category,
    delete_product,
    remove_related_product,
    set_product_composition,
    set_product_features,
    update_category,
    update_product,
    update_variant,
)
from app.services.security import require_permission


# ============================================================
# التصنيفات
# ============================================================

@products_bp.route("/categories", methods=["GET"])
@login_required
@require_permission("products.view")
def categories_index():
    roots = (
        db.session.query(Category)
        .filter_by(parent_id=None)
        .order_by(Category.display_order, Category.name_ar)
        .all()
    )
    return render_template("products/categories/index.html", roots=roots)


@products_bp.route("/categories/new", methods=["GET", "POST"])
@products_bp.route("/categories/<int:parent_id>/new", methods=["GET", "POST"])
@login_required
@require_permission("products.manage")
def categories_create(parent_id: int | None = None):
    form = CategoryForm()
    _populate_category_parent_choices(form, exclude_id=None, current_parent_id=parent_id)

    if request.method == "GET" and parent_id:
        form.parent_id.data = parent_id

    if form.validate_on_submit():
        try:
            cat = create_category(
                name_ar=form.name_ar.data,
                parent_id=(form.parent_id.data or None),
                display_order=form.display_order.data or 0,
            )
            # صورة التصنيف (اختياري — لو مرفوعة معها)
            uploaded = request.files.get("image")
            if uploaded and uploaded.filename:
                try:
                    save_category_image(cat.id, uploaded)
                except ImageError as ie:
                    # التصنيف اتعمل — الصورة فشلت. لا نرجّع؛ نعرض تحذير.
                    flash(f"تم إنشاء التصنيف بدون صورة: {ie}", "warning")
            db.session.commit()
            flash("تم إنشاء التصنيف.", "success")
            return redirect(url_for("products.categories_index"))
        except ProductError as e:
            db.session.rollback()
            flash(str(e), "danger")

    return render_template("products/categories/form.html", form=form, category=None)


@products_bp.route("/categories/<int:cat_id>/edit", methods=["GET", "POST"])
@login_required
@require_permission("products.manage")
def categories_edit(cat_id):
    cat = db.session.get(Category, cat_id) or abort(404)
    form = CategoryForm(obj=cat)
    _populate_category_parent_choices(form, exclude_id=cat.id, current_parent_id=cat.parent_id)

    if form.validate_on_submit():
        try:
            update_category(
                cat.id,
                name_ar=form.name_ar.data,
                display_order=form.display_order.data or 0,
                is_active=form.is_active.data,
            )
            # استبدال صورة التصنيف (اختياري)
            uploaded = request.files.get("image")
            if uploaded and uploaded.filename:
                try:
                    save_category_image(cat.id, uploaded)
                except ImageError as ie:
                    flash(f"تم حفظ التعديلات بدون تحديث الصورة: {ie}", "warning")
            db.session.commit()
            flash("تم حفظ التعديلات.", "success")
            return redirect(url_for("products.categories_index"))
        except ProductError as e:
            db.session.rollback()
            flash(str(e), "danger")

    return render_template("products/categories/form.html", form=form, category=cat)


@products_bp.route("/categories/<int:cat_id>/image/delete", methods=["POST"])
@login_required
@require_permission("products.manage")
def categories_delete_image(cat_id):
    """حذف صورة تصنيف واحدة (يفرّغ image_path + يمسح الملف من القرص)."""
    cat = db.session.get(Category, cat_id) or abort(404)
    try:
        delete_category_image(cat.id)
        db.session.commit()
        flash("تم حذف صورة التصنيف.", "success")
    except ImageError as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(url_for("products.categories_edit", cat_id=cat.id))


@products_bp.route("/categories/<int:cat_id>/delete", methods=["POST"])
@login_required
@require_permission("products.manage")
def categories_delete(cat_id):
    try:
        delete_category(cat_id)
        db.session.commit()
        flash("تم حذف التصنيف.", "success")
    except ProductError as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(url_for("products.categories_index"))


# ============================================================
# المنتجات
# ============================================================

@products_bp.route("/", methods=["GET"])
@login_required
@require_permission("products.view")
def index():
    q = (request.args.get("q") or "").strip()
    category_id = request.args.get("category", type=int)
    low_only = request.args.get("low_stock") == "1"

    query = db.session.query(Product)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Product.name_ar.ilike(like),
            Product.brand.ilike(like),
        ))
        variant_match = (
            db.session.query(ProductVariant.product_id)
            .filter(or_(ProductVariant.sku.ilike(like), ProductVariant.barcode.ilike(like)))
        )
        query = query.union(db.session.query(Product).filter(Product.id.in_(variant_match)))

    if category_id:
        query = query.filter(Product.category_id == category_id)

    products = query.order_by(Product.name_ar).all()

    if low_only:
        low_ids = {v.product_id for v in low_stock_variants()}
        products = [p for p in products if p.id in low_ids]

    categories = _flat_category_choices()
    return render_template(
        "products/index.html",
        products=products,
        q=q,
        category_id=category_id,
        low_only=low_only,
        categories=categories,
    )


@products_bp.route("/new", methods=["GET", "POST"])
@login_required
@require_permission("products.manage")
def create():
    form = ProductForm()
    form.category_id.choices = _flat_category_choices()

    if form.validate_on_submit():
        variants = _parse_variants_from_request()
        features = _parse_features_from_request()
        composition = _parse_composition_from_request()
        try:
            p = create_product(
                name_ar=form.name_ar.data,
                category_id=form.category_id.data,
                description=form.description.data,
                brand=form.brand.data,
                unit=form.unit.data,
                default_price=form.default_price.data or Decimal("0"),
                tax_rate_override=form.tax_rate_override.data,
                variants=variants,
                offer_ends_at=form.offer_ends_at.data,
                origin_country=form.origin_country.data,
                piece_count=form.piece_count.data,
            )
            # Epic 3 + Epic 4 — features + composition بعد إنشاء المنتج
            if features:
                set_product_features(p.id, features)
            if composition:
                set_product_composition(p.id, composition)
            db.session.commit()
            flash(f"تم إنشاء المنتج بعدد {len(p.variants)} متغير.", "success")
            return redirect(url_for("products.edit", product_id=p.id))
        except ProductError as e:
            db.session.rollback()
            flash(str(e), "danger")

    return render_template("products/form.html", form=form, product=None)


@products_bp.route("/<int:product_id>", methods=["GET"])
@login_required
@require_permission("products.view")
def view(product_id):
    p = db.session.get(Product, product_id) or abort(404)
    return render_template("products/view.html", product=p)


@products_bp.route("/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
@require_permission("products.manage")
def edit(product_id):
    p = db.session.get(Product, product_id) or abort(404)
    form = ProductForm(obj=p)
    form.category_id.choices = _flat_category_choices()

    if form.validate_on_submit():
        try:
            update_product(
                p.id,
                name_ar=form.name_ar.data,
                category_id=form.category_id.data,
                description=form.description.data,
                brand=form.brand.data,
                unit=form.unit.data,
                default_price=form.default_price.data,
                tax_rate_override=form.tax_rate_override.data or "",
                is_active=form.is_active.data,
                offer_ends_at=form.offer_ends_at.data if form.offer_ends_at.data else "",
                origin_country=form.origin_country.data or "",
                piece_count=form.piece_count.data if form.piece_count.data is not None else "",
            )
            # Epic 3 + Epic 4 — احفظ المميزات وتكوين الطقم لو أُرسلت
            features = _parse_features_from_request()
            composition = _parse_composition_from_request()
            set_product_features(p.id, features)
            set_product_composition(p.id, composition)
            db.session.commit()
            flash("تم حفظ التعديلات.", "success")
            return redirect(url_for("products.edit", product_id=p.id))
        except ProductError as e:
            db.session.rollback()
            flash(str(e), "danger")

    return render_template("products/form.html", form=form, product=p)


# ============================================================
# إيقاف تفعيل + حذف نهائي
# ============================================================

@products_bp.route("/<int:product_id>/toggle-active", methods=["POST"])
@login_required
@require_permission("products.manage")
def toggle_active(product_id):
    """يعكس is_active على المنتج — للإخفاء عن المتجر بدون حذف بيانات."""
    p = db.session.get(Product, product_id) or abort(404)
    try:
        update_product(p.id, is_active=not p.is_active)
        db.session.commit()
        flash(f"تم {'تفعيل' if p.is_active else 'إيقاف'} المنتج.", "success")
    except ProductError as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(request.referrer or url_for("products.index"))


@products_bp.route("/<int:product_id>/delete", methods=["POST"])
@login_required
@require_permission("products.manage")
def delete(product_id):
    """حذف نهائي — يُرفض لو المنتج له أي حركة محاسبية.

    عادةً يجب استخدام toggle_active للإيقاف بدلاً من الحذف.
    """
    p = db.session.get(Product, product_id) or abort(404)
    name = p.name_ar
    try:
        delete_product(p.id)
        db.session.commit()
        flash(f"تم حذف المنتج «{name}» نهائيًا.", "success")
        return redirect(url_for("products.index"))
    except ProductError as e:
        db.session.rollback()
        flash(str(e), "danger")
        return redirect(request.referrer or url_for("products.view", product_id=p.id))


# --------- إضافة متغير جديد لمنتج موجود ---------

@products_bp.route("/<int:product_id>/variants/new", methods=["POST"])
@login_required
@require_permission("products.manage")
def variants_add(product_id):
    p = db.session.get(Product, product_id) or abort(404)
    try:
        add_variant(
            p.id,
            color=request.form.get("color"),
            price=request.form.get("price") or None,
            compare_at_price=request.form.get("compare_at_price") or None,
            reorder_level=request.form.get("reorder_level") or None,
        )
        db.session.commit()
        flash("تم إضافة المتغير.", "success")
    except ProductError as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(url_for("products.view", product_id=p.id))


@products_bp.route("/variants/<int:variant_id>/edit", methods=["POST"])
@login_required
@require_permission("products.manage")
def variants_edit(variant_id):
    v = db.session.get(ProductVariant, variant_id) or abort(404)
    try:
        update_variant(
            v.id,
            sku=request.form.get("sku"),
            color=request.form.get("color"),
            barcode=request.form.get("barcode"),
            price=request.form.get("price"),
            compare_at_price=request.form.get("compare_at_price") if "compare_at_price" in request.form else None,
            reorder_level=request.form.get("reorder_level"),
            is_active=("is_active" in request.form),
        )
        db.session.commit()
        flash("تم حفظ المتغير.", "success")
    except ProductError as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(url_for("products.view", product_id=v.product_id))


# ============================================================
# Epic 1 — صور المنتجات
# ============================================================

@products_bp.route("/<int:product_id>/images/upload", methods=["POST"])
@login_required
@require_permission("products.manage")
def images_upload(product_id):
    p = db.session.get(Product, product_id) or abort(404)
    files = request.files.getlist("images")
    if not files or all(not f or not f.filename for f in files):
        flash("اختر صورة واحدة على الأقل.", "warning")
        return redirect(url_for("products.edit", product_id=p.id))

    saved = 0
    errors: list[str] = []
    for f in files:
        if not f or not f.filename:
            continue
        try:
            save_product_image(p.id, f)
            saved += 1
        except ImageError as e:
            errors.append(f"{f.filename}: {e}")

    if saved:
        db.session.commit()
        flash(f"تم رفع {saved} صورة.", "success")
    else:
        db.session.rollback()
    for msg in errors:
        flash(msg, "danger")

    return redirect(url_for("products.edit", product_id=p.id))


@products_bp.route("/images/<int:image_id>/delete", methods=["POST"])
@login_required
@require_permission("products.manage")
def images_delete(image_id):
    img = db.session.get(ProductImage, image_id) or abort(404)
    pid = img.product_id
    delete_product_image(image_id)
    db.session.commit()
    flash("تم حذف الصورة.", "success")
    return redirect(url_for("products.edit", product_id=pid))


@products_bp.route("/images/<int:image_id>/set-primary", methods=["POST"])
@login_required
@require_permission("products.manage")
def images_set_primary(image_id):
    img = db.session.get(ProductImage, image_id) or abort(404)
    try:
        set_primary(image_id)
        db.session.commit()
        flash("تم تعيين الصورة الرئيسية.", "success")
    except ImageError as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(url_for("products.edit", product_id=img.product_id))


@products_bp.route("/<int:product_id>/images/reorder", methods=["POST"])
@login_required
@require_permission("products.manage")
def images_reorder(product_id):
    p = db.session.get(Product, product_id) or abort(404)
    try:
        ordered_ids = [int(x) for x in request.form.getlist("ordered_ids[]")]
        reorder_images(p.id, ordered_ids)
        db.session.commit()
        return jsonify({"ok": True})
    except (ValueError, TypeError):
        db.session.rollback()
        return jsonify({"ok": False, "error": "Bad payload"}), 400


# ============================================================
# Epic 5 — علاقات بين المنتجات
# ============================================================

@products_bp.route("/<int:product_id>/relations/add", methods=["POST"])
@login_required
@require_permission("products.manage")
def relations_add(product_id):
    p = db.session.get(Product, product_id) or abort(404)
    related_id = request.form.get("related_product_id", type=int)
    rtype = request.form.get("relation_type") or "related"
    if not related_id:
        flash("اختر منتجًا للربط.", "warning")
        return redirect(url_for("products.edit", product_id=p.id))
    try:
        add_related_product(p.id, related_id, rtype)
        db.session.commit()
        flash("تم ربط المنتج.", "success")
    except ProductError as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(url_for("products.edit", product_id=p.id))


@products_bp.route("/relations/<int:relation_id>/delete", methods=["POST"])
@login_required
@require_permission("products.manage")
def relations_delete(relation_id):
    rel = db.session.get(ProductRelation, relation_id) or abort(404)
    pid = rel.product_id
    remove_related_product(relation_id)
    db.session.commit()
    flash("تم فك الربط.", "success")
    return redirect(url_for("products.edit", product_id=pid))


# ============================================================
# Ticket 3 Epic 6 — رصيد افتتاحي للمخزون
# ============================================================

@products_bp.route("/variants/<int:variant_id>/opening", methods=["POST"])
@login_required
@require_permission("inventory.adjust")
def variant_opening(variant_id):
    from datetime import date as _date
    from app.models.inventory import InventoryMovement
    from app.services.inventory import InventoryError, record_opening
    from app.services.ledger import LedgerLineDraft, post_journal_entry
    from app.models.journal import JournalSourceType
    from app.models.account import Account

    v = db.session.get(ProductVariant, variant_id) or abort(404)

    # منع الإدخال لو أي حركات سابقة أو رصيد > 0
    has_moves = db.session.query(InventoryMovement.id).filter_by(variant_id=v.id).first()
    if has_moves is not None or Decimal(str(v.stock_qty or 0)) != Decimal("0"):
        flash("لا يمكن إدخال رصيد افتتاحي لمنتج له حركات سابقة أو رصيد.", "danger")
        return redirect(url_for("products.view", product_id=v.product_id))

    try:
        qty = Decimal(str(request.form.get("qty") or "0"))
        unit_cost = Decimal(str(request.form.get("unit_cost") or "0"))
        record_opening(
            variant_id=v.id, qty=qty, unit_cost=unit_cost,
            move_date=_date.today(),
            user_id=current_user.id,
        )

        # قيد محاسبي مقابل: مدين المخزون 1100 / دائن الأرصدة الافتتاحية 3150
        total_value = (qty * unit_cost).quantize(Decimal("0.001"))
        if total_value > 0:
            inv_acc = db.session.query(Account).filter_by(code="1100").one()
            opening_acc = db.session.query(Account).filter_by(code="3150").one()
            post_journal_entry(
                entry_date=_date.today(),
                source_type=JournalSourceType.OPENING,
                source_id=v.id,
                memo=f"رصيد افتتاحي للمنتج {v.display_name}",
                lines=[
                    LedgerLineDraft(inv_acc.id, debit=total_value,
                                    memo=f"رصيد افتتاحي {v.sku}"),
                    LedgerLineDraft(opening_acc.id, credit=total_value,
                                    memo=f"رصيد افتتاحي {v.sku}"),
                ],
                user_id=current_user.id,
            )
        db.session.commit()
        flash(f"تم تسجيل الرصيد الافتتاحي: {qty} × {unit_cost}.", "success")
    except InventoryError as e:
        db.session.rollback()
        flash(str(e), "danger")
    except Exception as e:
        db.session.rollback()
        flash(f"تعذّر تسجيل الرصيد الافتتاحي: {e}", "danger")

    return redirect(url_for("products.view", product_id=v.product_id))


# ============================================================
# حركة المخزون
# ============================================================

@products_bp.route("/variants/<int:variant_id>/movements", methods=["GET"])
@login_required
@require_permission("inventory.view")
def variant_movements(variant_id):
    v = db.session.get(ProductVariant, variant_id) or abort(404)
    movements = (
        db.session.query(InventoryMovement)
        .filter_by(variant_id=v.id)
        .order_by(InventoryMovement.move_date.desc(), InventoryMovement.id.desc())
        .all()
    )
    return render_template("products/variant_movements.html", variant=v, movements=movements)


# ============================================================
# API — بحث سريع للاستخدام في الفواتير
# ============================================================

@products_bp.route("/api/search")
@login_required
@require_permission("products.view")
def api_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    like = f"%{q}%"
    variants = (
        db.session.query(ProductVariant)
        .join(Product)
        .filter(
            Product.is_active == True,  # noqa: E712
            ProductVariant.is_active == True,  # noqa: E712
        )
        .filter(or_(
            Product.name_ar.ilike(like),
            ProductVariant.sku.ilike(like),
            ProductVariant.barcode.ilike(like),
        ))
        .limit(20)
        .all()
    )
    return jsonify([
        {
            "id": v.id,
            "sku": v.sku,
            "barcode": v.barcode or "",
            "name": v.display_name,
            "price": str(v.price),
            "stock": str(v.stock_qty),
        }
        for v in variants
    ])


@products_bp.route("/api/search-products")
@login_required
@require_permission("products.view")
def api_search_products():
    """بحث يرجع منتجات (Product) بدل variants — يُستخدم في picker المنتجات ذات الصلة."""
    q = (request.args.get("q") or "").strip()
    exclude_id = request.args.get("exclude", type=int)
    if not q:
        return jsonify([])
    like = f"%{q}%"
    query = (
        db.session.query(Product)
        .filter(Product.is_active == True)  # noqa: E712
        .filter(or_(Product.name_ar.ilike(like), Product.brand.ilike(like)))
    )
    if exclude_id:
        query = query.filter(Product.id != exclude_id)
    products = query.limit(20).all()
    return jsonify([
        {"id": p.id, "name": p.name_ar, "brand": p.brand or "", "category": p.category.name_ar if p.category else ""}
        for p in products
    ])


@products_bp.route("/api/by-barcode/<code>")
@login_required
@require_permission("products.view")
def api_by_barcode(code):
    v = db.session.query(ProductVariant).filter_by(barcode=code, is_active=True).first()
    if v is None:
        return jsonify({"found": False}), 404
    return jsonify({
        "found": True,
        "id": v.id,
        "sku": v.sku,
        "name": v.display_name,
        "price": str(v.price),
        "stock": str(v.stock_qty),
    })


# ============================================================
# Helpers
# ============================================================

def _flat_category_choices() -> list[tuple[int, str]]:
    """قائمة مسطّحة للـ SelectField مع مسار كامل: 'أدوات مطبخ / سيراميك / أطباق'."""
    cats = db.session.query(Category).filter_by(is_active=True).order_by(Category.name_ar).all()
    choices = [(c.id, c.full_path_ar) for c in cats]
    return sorted(choices, key=lambda x: x[1])


def _populate_category_parent_choices(form, exclude_id: int | None, current_parent_id: int | None):
    """يملأ اختيارات parent_id مع استبعاد التصنيف نفسه (تجنب الحلقات)."""
    choices = [(0, "— بدون (تصنيف رئيسي) —")]
    cats = db.session.query(Category).order_by(Category.name_ar).all()
    for c in cats:
        if exclude_id and c.id == exclude_id:
            continue
        if c.depth < Category.MAX_DEPTH:
            choices.append((c.id, c.full_path_ar))
    form.parent_id.choices = choices


def _parse_variants_from_request() -> list[dict] | None:
    """يستقرئ حقول variant_* من الفورم لإنشاء قائمة variants."""
    colors = request.form.getlist("variant_color[]")
    prices = request.form.getlist("variant_price[]")
    compare_prices = request.form.getlist("variant_compare_at_price[]")
    reorders = request.form.getlist("variant_reorder[]")
    barcodes = request.form.getlist("variant_barcode[]")

    variants = []
    for i in range(len(colors)):
        color = (colors[i] or "").strip()
        price = (prices[i] if i < len(prices) else "") or ""
        barcode = (barcodes[i] if i < len(barcodes) else "") or ""
        if not color and not price and not barcode:
            continue  # سطر فارغ تمامًا
        variants.append({
            "color": color or None,
            "variant_name": color or None,
            "price": price or None,
            "compare_at_price": (compare_prices[i] if i < len(compare_prices) else None) or None,
            "reorder_level": (reorders[i] if i < len(reorders) else None) or None,
            "barcode": barcode or None,
        })
    return variants or None


def _parse_features_from_request() -> list[str]:
    """Epic 3 — يستقرئ feature_text[]."""
    texts = request.form.getlist("feature_text[]")
    return [t for t in (x.strip() for x in texts) if t]


def _parse_composition_from_request() -> list[dict]:
    """Epic 4 — يستقرئ composition_qty[] + composition_content[]."""
    qtys = request.form.getlist("composition_qty[]")
    contents = request.form.getlist("composition_content[]")
    result = []
    for i in range(len(contents)):
        name = (contents[i] or "").strip()
        raw_qty = (qtys[i] if i < len(qtys) else "").strip()
        if not name:
            continue
        result.append({"quantity": raw_qty or "1", "content_name_ar": name})
    return result
