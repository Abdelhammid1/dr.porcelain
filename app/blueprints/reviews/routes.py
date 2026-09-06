"""Admin routes لإدارة مراجعات المنتجات (Epic 6)."""
from __future__ import annotations

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import login_required

from app.blueprints.reviews import reviews_bp
from app.extensions import db
from app.models.review import ProductReview
from app.services.reviews import ReviewError, approve_review, reject_review
from app.services.security import require_permission


@reviews_bp.route("/", methods=["GET"])
@login_required
@require_permission("reviews.view")
def index():
    """قائمة كل المراجعات مع فلترة (كل / قيد المراجعة / معتمدة)."""
    status_filter = (request.args.get("status") or "pending").strip()
    query = db.session.query(ProductReview)
    if status_filter == "approved":
        query = query.filter_by(is_approved=True)
    elif status_filter == "pending":
        query = query.filter_by(is_approved=False)
    # "all" → بدون فلتر
    reviews = query.order_by(ProductReview.created_at.desc()).all()
    counts = {
        "pending": db.session.query(ProductReview).filter_by(is_approved=False).count(),
        "approved": db.session.query(ProductReview).filter_by(is_approved=True).count(),
        "all": db.session.query(ProductReview).count(),
    }
    return render_template("reviews/index.html",
                           reviews=reviews,
                           status_filter=status_filter,
                           counts=counts)


@reviews_bp.route("/<int:review_id>/approve", methods=["POST"])
@login_required
@require_permission("reviews.moderate")
def approve(review_id):
    try:
        approve_review(review_id)
        db.session.commit()
        flash("تم اعتماد المراجعة.", "success")
    except ReviewError as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(request.referrer or url_for("reviews.index"))


@reviews_bp.route("/<int:review_id>/reject", methods=["POST"])
@login_required
@require_permission("reviews.moderate")
def reject(review_id):
    try:
        reject_review(review_id)
        db.session.commit()
        flash("تم حذف المراجعة.", "success")
    except ReviewError as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(request.referrer or url_for("reviews.index"))
