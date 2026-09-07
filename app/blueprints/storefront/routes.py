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
from app.models.product_extras import ProductCompositionLine
from app.models.product_relation import RelationType
from app.models.sales import InvoiceStatus, SalesInvoice, SalesInvoiceLine
from app.models.setting import get_setting
from app.services.coupons import CouponError, validate_and_compute
from app.services.products import list_related_products
from app.services.reviews import (
    ReviewError, customer_has_purchased_product, submit_review,
)
from app.services import wishlist as wishlist_service
from app.services.cart import (
    add_item, build_cart_view, clear as clear_cart, clear_coupon,
    get_coupon, items_count, remove_item, set_coupon, set_qty,
)
from app.services import password_reset as pwd_reset
from app.services import stock_alerts as stock_alert_service
from app.services.email import send_password_reset
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
        cust = current_customer()
        return {
            "sf_cart_count": items_count(),
            "sf_root_categories": roots,
            "sf_current_customer": cust,
            "sf_wishlist_count": wishlist_service.count(cust.id) if cust else 0,
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
    # Epic 7 — فلاتر متعددة (multi-select) للبراند واللون
    selected_brands = [b for b in request.args.getlist("brand") if b]
    selected_colors = [c for c in request.args.getlist("color") if c]

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
    if selected_brands:
        query = query.filter(Product.brand.in_(selected_brands))
    if selected_colors:
        query = query.filter(ProductVariant.color.in_(selected_colors))

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

    # Facets: قائمة البراندات المتاحة داخل التصنيف (لا نفرض فلترة brand/color نفسها)
    available_brands = [
        b[0] for b in (
            db.session.query(Product.brand)
            .filter(Product.is_active == True)  # noqa: E712
            .filter(Product.category_id.in_(all_ids))
            .filter(Product.brand.isnot(None))
            .distinct()
            .order_by(Product.brand)
            .all()
        )
        if b[0]
    ]
    available_colors = [
        c[0] for c in (
            db.session.query(ProductVariant.color)
            .join(Product, Product.id == ProductVariant.product_id)
            .filter(Product.is_active == True)  # noqa: E712
            .filter(ProductVariant.is_active == True)  # noqa: E712
            .filter(Product.category_id.in_(all_ids))
            .filter(ProductVariant.color.isnot(None))
            .distinct()
            .order_by(ProductVariant.color)
            .all()
        )
        if c[0]
    ]

    return render_template(
        "storefront/category.html",
        category=cat, variants=variants,
        q=q, sort=sort,
        price_min=price_min, price_max=price_max,
        selected_brands=selected_brands, selected_colors=selected_colors,
        available_brands=available_brands, available_colors=available_colors,
    )


# ============ Product detail ============

@storefront_bp.route("/product/<int:product_id>", methods=["GET"])
def product(product_id):
    p = db.session.get(Product, product_id) or abort(404)
    if not p.is_active:
        abort(404)

    # Epic 5 — علاقات يدوية أولاً، ثم fallback لنفس التصنيف
    related_manual = list_related_products(p.id, RelationType.RELATED)
    frequently_bought = list_related_products(p.id, RelationType.FREQUENTLY_BOUGHT)

    if related_manual:
        # حوّل المنتجات لـ default_variant للعرض في كارت
        related = [prod.default_variant for prod in related_manual if prod.default_variant]
    else:
        # fallback على السلوك الحالي — منتجات من نفس التصنيف
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

    frequently = [
        prod.default_variant for prod in frequently_bought if prod.default_variant
    ]

    # Epic 6 — هل العميل الحالي مؤهل لكتابة مراجعة؟
    customer = current_customer()
    can_review = False
    already_reviewed = False
    if customer:
        can_review = customer_has_purchased_product(customer.id, p.id)
        already_reviewed = any(
            r.customer_id == customer.id for r in p.reviews
        )

    return render_template(
        "storefront/product.html",
        product=p,
        related=related,
        frequently=frequently,
        can_review=can_review,
        already_reviewed=already_reviewed,
    )


# ============ Epic 6 — إرسال مراجعة من العميل ============

@storefront_bp.route("/account/product/<int:product_id>/review", methods=["POST"])
@customer_required
def submit_product_review(product_id):
    customer = current_customer()
    try:
        submit_review(
            customer_id=customer.id,
            product_id=product_id,
            rating=request.form.get("rating"),
            comment=request.form.get("comment"),
        )
        db.session.commit()
        flash("تم إرسال تقييمك — سيظهر بعد الموافقة عليه من الإدارة.", "success")
    except ReviewError as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(url_for("storefront.product", product_id=product_id))


# ============ Cart routes ============

@storefront_bp.route("/cart", methods=["GET"])
def cart_view():
    customer = current_customer()
    view = build_cart_view(customer_id=customer.id if customer else None)
    return render_template("storefront/cart.html", cart=view)


# Ticket 2 Epic 2 — تطبيق/إلغاء كوبون على السلة
@storefront_bp.route("/cart/coupon/apply", methods=["POST"])
def cart_apply_coupon():
    code = (request.form.get("code") or "").strip().upper()
    if not code:
        flash("أدخل كود الخصم.", "warning")
        return redirect(url_for("storefront.cart_view"))

    customer = current_customer()
    view = build_cart_view(customer_id=customer.id if customer else None)
    try:
        validate_and_compute(
            code=code, subtotal=view.subtotal,
            customer_id=customer.id if customer else None,
        )
        set_coupon(code)
        flash(f"تم تطبيق كود الخصم {code}.", "success")
    except CouponError as e:
        flash(str(e), "danger")
    return redirect(url_for("storefront.cart_view"))


@storefront_bp.route("/cart/coupon/remove", methods=["POST"])
def cart_remove_coupon():
    clear_coupon()
    flash("تمت إزالة كود الخصم.", "info")
    return redirect(url_for("storefront.cart_view"))


@storefront_bp.route("/cart/coupon/validate", methods=["POST"])
def cart_validate_coupon():
    """AJAX validator — يُرجع JSON بحالة الكوبون ومبلغ الخصم."""
    code = (request.form.get("code") or "").strip().upper()
    if not code:
        return jsonify({"ok": False, "error": "أدخل كود الخصم."}), 400
    customer = current_customer()
    view = build_cart_view(customer_id=customer.id if customer else None)
    try:
        info = validate_and_compute(
            code=code, subtotal=view.subtotal,
            customer_id=customer.id if customer else None,
        )
        return jsonify({
            "ok": True,
            "code": info["coupon"].code,
            "discount_amount": str(info["discount_amount"]),
        })
    except CouponError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


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


@storefront_bp.route("/cart/remove-all", methods=["POST"])
def cart_remove_all():
    """يفرغ السلة بالكامل — يستخدمها زر «تفريغ السلة» في cart.html."""
    clear_cart()
    clear_coupon()
    flash("تم تفريغ السلة.", "info")
    return redirect(url_for("storefront.cart_view"))


# ============ Checkout ============

@storefront_bp.route("/checkout", methods=["GET", "POST"])
def checkout():
    customer = current_customer()
    view = build_cart_view(customer_id=customer.id if customer else None)
    if view.is_empty:
        flash("سلتك فارغة.", "warning")
        return redirect(url_for("storefront.home"))

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
                coupon_code=get_coupon(),  # Ticket 2 Epic 2
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


# ============ Ticket 4 Epic 3 — إلغاء الطلب ذاتيًا ============

@storefront_bp.route("/account/order/<order_number>/cancel", methods=["POST"])
@customer_required
def account_order_cancel(order_number):
    from app.models.order import OrderStatus
    from app.services.orders import OrderError, transition_status
    from app.services.notifications import create as create_notification

    customer = current_customer()
    order = db.session.query(Order).filter_by(doc_number=order_number).first_or_404()
    if order.customer_id != customer.id:
        abort(403)

    if order.status not in (OrderStatus.PENDING, OrderStatus.PROCESSING):
        flash("لا يمكن إلغاء طلب تم شحنه. تواصل مع خدمة العملاء.", "warning")
        return redirect(url_for("storefront.account_order_view",
                                 order_number=order.doc_number))

    try:
        transition_status(order_id=order.id, new_status=OrderStatus.CANCELLED)
        # إشعار داخلي للأدمن
        try:
            create_notification(
                title=f"إلغاء عميل لطلب {order.doc_number}",
                body=f"العميل {customer.name_ar} ألغى طلبه.",
                link=f"/orders/{order.id}",
                notification_type="order_cancelled",
            )
        except Exception:
            pass
        db.session.commit()
        flash("تم إلغاء الطلب واسترجاع المخزون.", "success")
    except OrderError as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(url_for("storefront.account_order_view",
                             order_number=order.doc_number))


# ============ Ticket 4 Epic 4 — تحميل PDF للفاتورة ============

@storefront_bp.route("/account/order/<order_number>/invoice.pdf", methods=["GET"])
@customer_required
def account_order_pdf(order_number):
    from flask import send_file
    from app.services.invoice_pdf import order_invoice_pdf
    customer = current_customer()
    order = db.session.query(Order).filter_by(doc_number=order_number).first_or_404()
    if order.customer_id != customer.id:
        abort(403)
    data = order_invoice_pdf(order)
    return send_file(
        __import__("io").BytesIO(data),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"invoice_{order.doc_number}.pdf",
    )


# ============ Ticket 4 Epic 7 — تتبع الطلب كضيف ============

@storefront_bp.route("/track-order", methods=["GET", "POST"])
def track_order():
    if request.method == "POST":
        order_number = (request.form.get("order_number") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        if not order_number or not phone:
            flash("رقم الطلب ورقم الهاتف مطلوبان.", "warning")
            return render_template("storefront/track_order.html")

        order = db.session.query(Order).filter_by(doc_number=order_number).first()
        # فحص الهاتف — يقارن مع guest_phone أو phone العميل المرتبط
        matched = False
        if order is not None:
            actual_phone = order.guest_phone or (
                order.customer.phone if order.customer else None
            )
            if actual_phone and actual_phone.strip() == phone:
                matched = True
        if not matched:
            # رسالة عامة (منع تسريب)
            flash("البيانات غير مطابقة. تأكد من رقم الطلب والهاتف.", "danger")
            return render_template("storefront/track_order.html")

        return render_template("storefront/track_order_result.html", order=order)

    return render_template("storefront/track_order.html")


# ============ Ticket 4 Epic 5 — نسيان كلمة المرور (عميل) ============

@storefront_bp.route("/account/forgot-password", methods=["GET", "POST"])
def account_forgot_password():
    if request.method == "POST":
        value = (request.form.get("phone_or_email") or "").strip()
        try:
            token = pwd_reset.request_customer_reset(value)
            if token is not None and token.customer and token.customer.email:
                try:
                    reset_link = url_for("storefront.account_reset_password",
                                          token=token.token, _external=True)
                    send_password_reset(to_email=token.customer.email, reset_link=reset_link)
                except Exception:
                    pass
                db.session.commit()
        except Exception:
            db.session.rollback()
        # رسالة موحّدة
        flash("لو الحساب موجود، هيوصلك لينك استرجاع كلمة المرور.", "info")
        return redirect(url_for("storefront.account_login"))
    return render_template("storefront/account_forgot_password.html")


@storefront_bp.route("/account/reset-password/<token>", methods=["GET", "POST"])
def account_reset_password(token):
    try:
        tok = pwd_reset.validate_token(token)
        if tok.user_id is not None:
            raise pwd_reset.TokenError("رابط غير صالح.")
    except pwd_reset.TokenError as e:
        flash(str(e) + " اطلب لينك جديد.", "danger")
        return redirect(url_for("storefront.account_forgot_password"))

    if request.method == "POST":
        new_password = request.form.get("new_password") or ""
        confirm = request.form.get("confirm") or ""
        if new_password != confirm:
            flash("كلمتا المرور لا تتطابقان.", "danger")
            return render_template("storefront/account_reset_password.html", token=token)
        try:
            pwd_reset.consume_and_set_password(token, new_password)
            db.session.commit()
            flash("تم تحديث كلمة المرور. سجّل الدخول الآن.", "success")
            return redirect(url_for("storefront.account_login"))
        except pwd_reset.TokenError as e:
            db.session.rollback()
            flash(str(e), "danger")

    return render_template("storefront/account_reset_password.html", token=token)


# ============ Ticket 4 Epic 6 — أعلمني لما يتوفر ============

@storefront_bp.route("/product/<int:product_id>/notify-when-available", methods=["POST"])
def notify_when_available(product_id):
    from app.models.product import ProductVariant
    variant_id = request.form.get("variant_id", type=int)
    email = (request.form.get("email") or "").strip()
    customer = current_customer()
    if customer and not email:
        email = customer.email or ""

    if not variant_id:
        flash("اختر متغيرًا.", "warning")
        return redirect(url_for("storefront.product", product_id=product_id))
    try:
        stock_alert_service.subscribe(
            variant_id=variant_id, email=email,
            customer_id=customer.id if customer else None,
        )
        db.session.commit()
        flash("تم تسجيل تنبيهك — سنُخبرك عند توفر المنتج.", "success")
    except stock_alert_service.AlertError as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(url_for("storefront.product", product_id=product_id))


@storefront_bp.route("/account/loyalty", methods=["GET"])
@customer_required
def account_loyalty():
    """Ticket 3 Epic 10 — نقاط الولاء للعميل."""
    from app.services import loyalty as loyalty_service
    customer = current_customer()
    bal = loyalty_service.balance(customer.id)
    txns = loyalty_service.list_transactions(customer.id, limit=50)
    return render_template("storefront/account_loyalty.html",
                           customer=customer, balance=bal, txns=txns)


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
# Ticket 2 Epic 1 — بحث المنتجات في المتجر
# ==================================================================

@storefront_bp.route("/search", methods=["GET"])
def search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return render_template("storefront/search.html", q="", variants=[],
                               popular_categories=_popular_categories())

    like = f"%{q}%"
    # Product-level matches: name, description, brand
    product_ids_via_product = (
        db.session.query(Product.id)
        .filter(Product.is_active == True)  # noqa: E712
        .filter(or_(
            Product.name_ar.ilike(like),
            Product.description.ilike(like),
            Product.brand.ilike(like),
        ))
    )
    # Ticket 2 Epic 1 (ملاحظة خاصة): بحث في محتويات تكوين الطقم (Epic 4 من التذكرة السابقة)
    product_ids_via_composition = (
        db.session.query(ProductCompositionLine.product_id)
        .filter(ProductCompositionLine.content_name_ar.ilike(like))
    )
    matched_ids = {row[0] for row in product_ids_via_product.all()}
    matched_ids.update(row[0] for row in product_ids_via_composition.all())

    if not matched_ids:
        return render_template(
            "storefront/search.html",
            q=q, variants=[],
            popular_categories=_popular_categories(),
        )

    variants = (
        db.session.query(ProductVariant)
        .join(Product, Product.id == ProductVariant.product_id)
        .filter(Product.is_active == True)  # noqa: E712
        .filter(ProductVariant.is_active == True)  # noqa: E712
        .filter(Product.id.in_(matched_ids))
        .order_by(Product.id.desc())
        .limit(60)
        .all()
    )
    return render_template(
        "storefront/search.html",
        q=q, variants=variants,
        popular_categories=_popular_categories() if not variants else [],
    )


# ==================================================================
# Ticket 2 Epic 4 — Wishlist
# ==================================================================

@storefront_bp.route("/wishlist/toggle/<int:product_id>", methods=["POST"])
def wishlist_toggle(product_id):
    """يُضيف/يُزيل منتجًا من المفضلة. لو الضيف، يُوجّه لتسجيل الدخول ثم يعيده."""
    customer = current_customer()
    if customer is None:
        flash("سجّل الدخول لإضافة منتجات لقائمة المفضلة.", "warning")
        # هنا نُخزن رابط الرجوع في جلسة بسيطة
        session["next_after_login"] = request.referrer or url_for(
            "storefront.product", product_id=product_id
        )
        return redirect(url_for("storefront.account_login"))

    if wishlist_service.contains(customer.id, product_id):
        wishlist_service.remove(customer.id, product_id)
        db.session.commit()
        flash("تمت الإزالة من المفضلة.", "info")
    else:
        try:
            wishlist_service.add(customer.id, product_id)
            db.session.commit()
            flash("تمت الإضافة إلى المفضلة.", "success")
        except wishlist_service.WishlistError as e:
            db.session.rollback()
            flash(str(e), "danger")
    return redirect(request.referrer or url_for("storefront.product", product_id=product_id))


@storefront_bp.route("/account/wishlist", methods=["GET"])
@customer_required
def account_wishlist():
    customer = current_customer()
    items = wishlist_service.list_items(customer.id)
    return render_template("storefront/account_wishlist.html",
                           customer=customer, items=items)


@storefront_bp.route("/account/wishlist/move-to-cart/<int:product_id>", methods=["POST"])
@customer_required
def wishlist_move_to_cart(product_id):
    """ينقل منتجًا من المفضلة إلى السلة (يستخدم أول متغير نشط)."""
    customer = current_customer()
    product = db.session.get(Product, product_id) or abort(404)
    variant = next((v for v in product.variants if v.is_active and v.stock_qty > 0), None)
    if variant is None:
        flash("المنتج غير متاح حاليًا.", "warning")
    else:
        add_item(variant.id, qty=1)
        wishlist_service.remove(customer.id, product_id)
        db.session.commit()
        flash("تم النقل للسلة.", "success")
    return redirect(url_for("storefront.account_wishlist"))


# ==================================================================
# Helpers
# ==================================================================

def _popular_categories() -> list:
    return (
        db.session.query(Category)
        .filter_by(parent_id=None, is_active=True)
        .order_by(Category.display_order, Category.name_ar)
        .limit(6)
        .all()
    )


def _descendant_category_ids(cat: Category) -> list[int]:
    """يجمع id الحالي + كل الفروع الفرعية."""
    ids = [cat.id]
    stack = list(cat.children)
    while stack:
        c = stack.pop()
        ids.append(c.id)
        stack.extend(c.children)
    return ids
