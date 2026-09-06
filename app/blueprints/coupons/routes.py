"""Admin routes لإدارة أكواد الخصم (Ticket 2 Epic 2)."""
from __future__ import annotations

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import login_required

from app.blueprints.coupons import coupons_bp
from app.extensions import db
from app.models.coupon import CouponUsage, DiscountCoupon, DiscountType
from app.services.coupons import CouponError, create_coupon, update_coupon
from app.services.security import require_permission


@coupons_bp.route("/", methods=["GET"])
@login_required
@require_permission("coupons.view")
def index():
    coupons = db.session.query(DiscountCoupon).order_by(DiscountCoupon.id.desc()).all()
    # عدد استخدامات كل كوبون
    usage_counts = {
        c.id: db.session.query(CouponUsage).filter_by(coupon_id=c.id).count()
        for c in coupons
    }
    return render_template("coupons/index.html",
                           coupons=coupons, usage_counts=usage_counts)


@coupons_bp.route("/new", methods=["GET", "POST"])
@login_required
@require_permission("coupons.manage")
def create():
    if request.method == "POST":
        try:
            create_coupon(
                code=request.form.get("code"),
                discount_type=request.form.get("discount_type") or "percentage",
                discount_value=request.form.get("discount_value"),
                min_order_amount=request.form.get("min_order_amount"),
                max_uses=request.form.get("max_uses"),
                max_uses_per_customer=request.form.get("max_uses_per_customer") or 1,
                valid_from=request.form.get("valid_from"),
                valid_until=request.form.get("valid_until"),
                is_active=("is_active" in request.form),
            )
            db.session.commit()
            flash("تم إنشاء الكوبون.", "success")
            return redirect(url_for("coupons.index"))
        except CouponError as e:
            db.session.rollback()
            flash(str(e), "danger")
    return render_template("coupons/form.html", coupon=None)


@coupons_bp.route("/<int:coupon_id>/edit", methods=["GET", "POST"])
@login_required
@require_permission("coupons.manage")
def edit(coupon_id):
    c = db.session.get(DiscountCoupon, coupon_id) or abort(404)
    if request.method == "POST":
        try:
            update_coupon(
                c.id,
                discount_value=request.form.get("discount_value"),
                min_order_amount=request.form.get("min_order_amount"),
                max_uses=request.form.get("max_uses"),
                max_uses_per_customer=request.form.get("max_uses_per_customer") or 1,
                valid_from=request.form.get("valid_from"),
                valid_until=request.form.get("valid_until"),
                is_active=("is_active" in request.form),
            )
            db.session.commit()
            flash("تم حفظ الكوبون.", "success")
            return redirect(url_for("coupons.index"))
        except CouponError as e:
            db.session.rollback()
            flash(str(e), "danger")
    usages = (
        db.session.query(CouponUsage)
        .filter_by(coupon_id=c.id)
        .order_by(CouponUsage.id.desc())
        .all()
    )
    return render_template("coupons/form.html", coupon=c, usages=usages)


@coupons_bp.route("/<int:coupon_id>/toggle", methods=["POST"])
@login_required
@require_permission("coupons.manage")
def toggle(coupon_id):
    c = db.session.get(DiscountCoupon, coupon_id) or abort(404)
    c.is_active = not c.is_active
    db.session.commit()
    flash(("تم تفعيل الكوبون." if c.is_active else "تم تعطيل الكوبون."), "info")
    return redirect(url_for("coupons.index"))
