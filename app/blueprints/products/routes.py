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
from app.models.inventory import InventoryMovement
from app.models.product import Product, ProductVariant
from app.services.inventory import low_stock_variants
from app.services.products import (
    ProductError,
    add_variant,
    create_category,
    create_product,
    delete_category,
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
            create_category(
                name_ar=form.name_ar.data,
                parent_id=(form.parent_id.data or None),
                display_order=form.display_order.data or 0,
            )
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
            db.session.commit()
            flash("تم حفظ التعديلات.", "success")
            return redirect(url_for("products.categories_index"))
        except ProductError as e:
            db.session.rollback()
            flash(str(e), "danger")

    return render_template("products/categories/form.html", form=form, category=cat)


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
        # ابحث أيضًا في SKU/barcode للـ variants
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
        # المتغيرات — نستقبلها من الحقول الديناميكية variant_color[], variant_price[], إلخ
        variants = _parse_variants_from_request()
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
            )
            db.session.commit()
            flash(f"تم إنشاء المنتج بعدد {len(p.variants)} متغير.", "success")
            return redirect(url_for("products.view", product_id=p.id))
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
            )
            db.session.commit()
            flash("تم حفظ التعديلات.", "success")
            return redirect(url_for("products.view", product_id=p.id))
        except ProductError as e:
            db.session.rollback()
            flash(str(e), "danger")

    return render_template("products/form.html", form=form, product=p)


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
        # فقط التصنيفات اللي عمقها < MAX_DEPTH يمكن أن تكون آباء
        if c.depth < Category.MAX_DEPTH:
            choices.append((c.id, c.full_path_ar))
    form.parent_id.choices = choices


def _parse_variants_from_request() -> list[dict]:
    """يستقرئ حقول variant_* من الفورم لإنشاء قائمة variants."""
    colors = request.form.getlist("variant_color[]")
    prices = request.form.getlist("variant_price[]")
    reorders = request.form.getlist("variant_reorder[]")
    barcodes = request.form.getlist("variant_barcode[]")

    variants = []
    for i in range(len(colors)):
        color = (colors[i] or "").strip()
        # سطر فارغ تمامًا نتجاهله
        if not color and not (prices[i] if i < len(prices) else "") \
                     and not (barcodes[i] if i < len(barcodes) else ""):
            continue
        variants.append({
            "color": color or None,
            "variant_name": color or None,
            "price": (prices[i] if i < len(prices) else None) or None,
            "reorder_level": (reorders[i] if i < len(reorders) else None) or None,
            "barcode": (barcodes[i] if i < len(barcodes) else None) or None,
        })
    return variants or None
