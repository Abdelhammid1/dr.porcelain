"""Routes للمتجر الإلكتروني (Storefront) — Public routes، لا يحتاج تسجيل دخول."""
from __future__ import annotations

from decimal import Decimal

from flask import (
    abort, flash, jsonify, redirect, render_template, request, session, url_for,
)
from sqlalchemy import or_

from app.blueprints.storefront import storefront_bp
from app.extensions import db
from app.models.category import Category
from app.models.order import Order, OrderPaymentMethod
from app.models.product import Product, ProductVariant
from app.models.sales import InvoiceStatus, SalesInvoice, SalesInvoiceLine
from app.models.setting import get_setting
from app.services.cart import (
    add_item, build_cart_view, clear as clear_cart,
    items_count, remove_item, set_qty,
)
from app.services.customer_auth import (
    CustomerAuthError, current_customer, customer_required,
    login_customer, logout as customer_logout, register_customer,
)
from app.services.orders import OrderError, OrderLineDraft, create_order


# ============ Context: يوفّر counters لكل صفحات المتجر ============

@storefront_bp.app_context_processor
def inject_storefront_context():
    """يُتاح في كل قوالب المتجر."""
    try:
        roots = (
            db.session.query(Category)
            .filter_by(parent_id=None, is_active=True)
            .order_by(Category.display_order, Category.name_ar)
            .limit(10)
            .all()
        )
        return {
            "sf_cart_count": items_count(),
            "sf_root_categories": roots,
            "sf_current_customer": current_customer(),
            "sf_settings": {
                "announcement": str(get_setting("storefront.announcement", "")),
                "store_name": str(get_setting("store.name", "المتجر")),
                "store_phone": str(get_setting("store.phone", "")),
            },
        }
    except Exception:
        return {}


# ============ Homepage ============

@storefront_bp.route("/", methods=["GET"])
def home():
    if not bool(get_setting("storefront.enabled", True)):
        return render_template("storefront/disabled.html"), 503

    # الأقسام الجذرية للـ Mega menu وعرض "تسوق حسب الأقسام"
    root_categories = (
        db.session.query(Category)
        .filter_by(parent_id=None, is_active=True)
        .order_by(Category.display_order, Category.name_ar)
        .all()
    )

    # أعلى المنتجات مبيعًا (نأخذ آخر 30 يوم أو أعلى إيرادًا مطلقًا كـ fallback)
    best_sellers_ids = (
        db.session.query(
            SalesInvoiceLine.variant_id,
            db.func.sum(SalesInvoiceLine.qty).label("qty"),
        )
        .join(SalesInvoice, SalesInvoice.id == SalesInvoiceLine.invoice_id)
        .filter(SalesInvoice.status != InvoiceStatus.RETURNED)
        .group_by(SalesInvoiceLine.variant_id)
        .order_by(db.func.sum(SalesInvoiceLine.qty).desc())
        .limit(8)
        .all()
    )
    best_seller_variant_ids = [v[0] for v in best_sellers_ids]
    best_sellers = (
        db.session.query(ProductVariant)
        .filter(ProductVariant.id.in_(best_seller_variant_ids))
        .filter(ProductVariant.is_active == True)  # noqa: E712
        .all()
        if best_seller_variant_ids else []
    )

    # وصل حديثًا
    new_arrivals = (
        db.session.query(ProductVariant)
        .join(Product, Product.id == ProductVariant.product_id)
        .filter(Product.is_active == True)  # noqa: E712
        .filter(ProductVariant.is_active == True)  # noqa: E712
        .filter(ProductVariant.stock_qty > 0)
        .order_by(Product.id.desc())
        .limit(8)
        .all()
    )

    hero = {
        "title": str(get_setting("storefront.hero_title", "")),
        "subtitle": str(get_setting("storefront.hero_subtitle", "")),
        "cta": str(get_setting("storefront.hero_cta", "تسوق")),
    }

    return render_template(
        "storefront/home.html",
        root_categories=root_categories,
        best_sellers=best_sellers,
        new_arrivals=new_arrivals,
        hero=hero,
    )


# ============ Category page ============

@storefront_bp.route("/category/<int:category_id>", methods=["GET"])
def category(category_id):
    cat = db.session.get(Category, category_id) or abort(404)

    # نجمع كل التصنيفات الفرعية أيضًا
    all_ids = _descendant_category_ids(cat)

    q = (request.args.get("q") or "").strip()
    sort = request.args.get("sort") or "newest"
    price_min = request.args.get("price_min", type=float)
    price_max = request.args.get("price_max", type=float)

    query = (
        db.session.query(ProductVariant)
        .join(Product, Product.id == ProductVariant.product_id)
        .filter(Product.is_active == True)  # noqa: E712
        .filter(ProductVariant.is_active == True)  # noqa: E712
        .filter(Product.category_id.in_(all_ids))
    )
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Product.name_ar.ilike(like), ProductVariant.sku.ilike(like)))
    if price_min is not None:
        query = query.filter(ProductVariant.price >= price_min)
    if price_max is not None:
        query = query.filter(ProductVariant.price <= price_max)

    if sort == "price_asc":
        query = query.order_by(ProductVariant.price.asc())
    elif sort == "price_desc":
        query = query.order_by(ProductVariant.price.desc())
    elif sort == "best_selling":
        # (تبسيط: نستخدم الرصيد كإشارة عكسية — لاحقًا نُبنى على SalesInvoiceLine sum)
        query = query.order_by(ProductVariant.stock_qty.asc())
    else:  # newest
        query = query.order_by(Product.id.desc())

    variants = query.limit(60).all()
    return render_template(
        "storefront/category.html",
        category=cat, variants=variants,
        q=q, sort=sort,
        price_min=price_min, price_max=price_max,
    )


# ============ Product detail ============

@storefront_bp.route("/product/<int:product_id>", methods=["GET"])
def product(product_id):
    p = db.session.get(Product, product_id) or abort(404)
    if not p.is_active:
        abort(404)

    # منتجات ذات صلة من نفس التصنيف
    related = (
        db.session.query(ProductVariant)
        .join(Product, Product.id == ProductVariant.product_id)
        .filter(Product.category_id == p.category_id)
        .filter(Product.id != p.id)
        .filter(Product.is_active == True)  # noqa: E712
        .filter(ProductVariant.is_active == True)  # noqa: E712
        .limit(6)
        .all()
    )
    return render_template("storefront/product.html", product=p, related=related)


# ============ Cart routes ============

@storefront_bp.route("/cart", methods=["GET"])
def cart_view():
    view = build_cart_view()
    return render_template("storefront/cart.html", cart=view)


@storefront_bp.route("/cart/add", methods=["POST"])
def cart_add():
    variant_id = request.form.get("variant_id", type=int)
    qty = request.form.get("qty", type=int) or 1
    if variant_id:
        add_item(variant_id, qty=qty)
        flash("تمت الإضافة للسلة.", "success")
    return redirect(request.form.get("next") or url_for("storefront.cart_view"))


@storefront_bp.route("/cart/update", methods=["POST"])
def cart_update():
    for key, val in request.form.items():
        if key.startswith("qty_"):
            vid = int(key.replace("qty_", ""))
            set_qty(vid, int(val or "0"))
    return redirect(url_for("storefront.cart_view"))


@storefront_bp.route("/cart/remove/<int:variant_id>", methods=["POST"])
def cart_remove(variant_id):
    remove_item(variant_id)
    return redirect(url_for("storefront.cart_view"))


# ============ Checkout ============

@storefront_bp.route("/checkout", methods=["GET", "POST"])
def checkout():
    view = build_cart_view()
    if view.is_empty:
        flash("سلتك فارغة.", "warning")
        return redirect(url_for("storefront.home"))

    customer = current_customer()

    if request.method == "POST":
        try:
            drafts = [OrderLineDraft(variant_id=l.variant.id, qty=l.qty) for l in view.lines]
            order = create_order(
                lines=drafts,
                guest_name=(request.form.get("guest_name") or (customer.name_ar if customer else "")).strip(),
                guest_phone=(request.form.get("guest_phone") or (customer.phone if customer else "")).strip(),
                shipping_address=(request.form.get("shipping_address") or "").strip(),
                shipping_city=(request.form.get("shipping_city") or "").strip() or None,
                shipping_notes=(request.form.get("shipping_notes") or "").strip() or None,
                guest_email=(request.form.get("guest_email") or (customer.email if customer else "")).strip() or None,
                customer_id=(customer.id if customer else None),
                payment_method=OrderPaymentMethod.COD,
                notes=(request.form.get("notes") or "").strip() or None,
            )
            db.session.commit()
            clear_cart()
            return redirect(url_for("storefront.order_thanks", order_number=order.doc_number))
        except OrderError as e:
            db.session.rollback()
            flash(str(e), "danger")

    return render_template("storefront/checkout.html", cart=view, customer=customer)


@storefront_bp.route("/order/thanks/<order_number>", methods=["GET"])
def order_thanks(order_number):
    order = db.session.query(Order).filter_by(doc_number=order_number).first_or_404()
    return render_template("storefront/thanks.html", order=order)


# ==================================================================
# Customer Portal (Phase 7)
# ==================================================================

@storefront_bp.route("/account/register", methods=["GET", "POST"])
def account_register():
    if current_customer():
        return redirect(url_for("storefront.account_home"))

    if request.method == "POST":
        try:
            party = register_customer(
                name_ar=(request.form.get("name") or "").strip(),
                phone=(request.form.get("phone") or "").strip(),
                email=(request.form.get("email") or "").strip() or None,
                password=request.form.get("password") or "",
            )
            db.session.commit()
            # سجّل الدخول تلقائيًا بعد التسجيل
            login_customer(phone=party.phone, password=request.form.get("password"))
            db.session.commit()
            flash(f"مرحبًا {party.name_ar}! تم إنشاء حسابك.", "success")
            return redirect(url_for("storefront.account_home"))
        except CustomerAuthError as e:
            db.session.rollback()
            flash(str(e), "danger")

    return render_template("storefront/account_register.html")


@storefront_bp.route("/account/login", methods=["GET", "POST"])
def account_login():
    if current_customer():
        return redirect(url_for("storefront.account_home"))

    if request.method == "POST":
        try:
            party = login_customer(
                phone=(request.form.get("phone") or "").strip(),
                password=request.form.get("password") or "",
            )
            db.session.commit()
            flash(f"مرحبًا بعودتك يا {party.name_ar}!", "success")
            return redirect(url_for("storefront.account_home"))
        except CustomerAuthError as e:
            flash(str(e), "danger")

    return render_template("storefront/account_login.html")


@storefront_bp.route("/account/logout", methods=["GET", "POST"])
def account_logout():
    customer_logout()
    flash("تم تسجيل الخروج.", "info")
    return redirect(url_for("storefront.home"))


# ---------- Portal dashboard + tabs ----------

@storefront_bp.route("/account/", methods=["GET"])
@customer_required
def account_home():
    customer = current_customer()
    orders = (
        db.session.query(Order)
        .filter_by(customer_id=customer.id)
        .order_by(Order.id.desc())
        .limit(5)
        .all()
    )
    # الأقساط (Phase 3)
    from app.models.installment import InstallmentPlan, InstallmentPlanStatus
    active_plans = (
        db.session.query(InstallmentPlan)
        .filter_by(customer_id=customer.id, status=InstallmentPlanStatus.ACTIVE)
        .all()
    )
    # رصيد العميل (المديونية الحالية)
    balance = customer.account.compute_balance() if customer.account else Decimal("0")

    return render_template(
        "storefront/account_home.html",
        customer=customer,
        orders=orders,
        active_plans=active_plans,
        balance=balance,
    )


@storefront_bp.route("/account/orders", methods=["GET"])
@customer_required
def account_orders():
    customer = current_customer()
    orders = (
        db.session.query(Order)
        .filter_by(customer_id=customer.id)
        .order_by(Order.id.desc())
        .all()
    )
    return render_template("storefront/account_orders.html",
                           customer=customer, orders=orders)


@storefront_bp.route("/account/order/<order_number>", methods=["GET"])
@customer_required
def account_order_view(order_number):
    customer = current_customer()
    order = db.session.query(Order).filter_by(doc_number=order_number).first_or_404()
    if order.customer_id != customer.id:
        abort(403)
    return render_template("storefront/account_order_view.html",
                           customer=customer, order=order)


@storefront_bp.route("/account/installments", methods=["GET"])
@customer_required
def account_installments():
    customer = current_customer()
    from app.models.installment import InstallmentPlan
    plans = (
        db.session.query(InstallmentPlan)
        .filter_by(customer_id=customer.id)
        .order_by(InstallmentPlan.id.desc())
        .all()
    )
    return render_template("storefront/account_installments.html",
                           customer=customer, plans=plans)


@storefront_bp.route("/account/installments/<int:plan_id>", methods=["GET"])
@customer_required
def account_installment_view(plan_id):
    customer = current_customer()
    from app.models.installment import InstallmentPlan
    plan = db.session.get(InstallmentPlan, plan_id) or abort(404)
    if plan.customer_id != customer.id:
        abort(403)
    return render_template("storefront/account_installment_view.html",
                           customer=customer, plan=plan)


# ==================================================================
# Helpers
# ==================================================================

def _descendant_category_ids(cat: Category) -> list[int]:
    """يجمع id الحالي + كل الفروع الفرعية."""
    ids = [cat.id]
    stack = list(cat.children)
    while stack:
        c = stack.pop()
        ids.append(c.id)
        stack.extend(c.children)
    return ids
