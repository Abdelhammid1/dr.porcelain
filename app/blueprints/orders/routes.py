"""Routes لإدارة الطلبات الأونلاين (admin)."""
from __future__ import annotations

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from app.blueprints.orders import orders_bp
from app.extensions import db
from app.models.order import Order, OrderStatus
from app.models.party import Party
from app.services.orders import OrderError, transition_status
from app.services.security import require_permission


@orders_bp.route("/", methods=["GET"])
@login_required
@require_permission("orders.view")
def index():
    q = (request.args.get("q") or "").strip()
    status_filter = request.args.get("status")

    query = db.session.query(Order).order_by(Order.id.desc())
    if q:
        like = f"%{q}%"
        query = query.outerjoin(Party, Party.id == Order.customer_id).filter(or_(
            Order.doc_number.ilike(like),
            Order.guest_name.ilike(like),
            Order.guest_phone.ilike(like),
            Party.name_ar.ilike(like),
        ))
    if status_filter and status_filter in {s.value for s in OrderStatus}:
        query = query.filter(Order.status == OrderStatus(status_filter))

    orders = query.limit(200).all()

    # counters for the tab strip
    from sqlalchemy import func
    status_counts = dict(
        db.session.query(Order.status, func.count(Order.id))
        .group_by(Order.status).all()
    )

    return render_template("orders/index.html",
                           orders=orders, q=q, status_filter=status_filter,
                           status_counts=status_counts)


@orders_bp.route("/<int:order_id>", methods=["GET"])
@login_required
@require_permission("orders.view")
def view(order_id):
    order = db.session.get(Order, order_id) or abort(404)
    return render_template("orders/view.html", order=order)


@orders_bp.route("/<int:order_id>/status", methods=["POST"])
@login_required
@require_permission("orders.manage")
def change_status(order_id):
    order = db.session.get(Order, order_id) or abort(404)
    new_status_str = request.form.get("status")
    try:
        new_status = OrderStatus(new_status_str)
    except ValueError:
        flash("حالة غير صحيحة.", "danger")
        return redirect(url_for("orders.view", order_id=order.id))

    try:
        transition_status(order_id=order.id, new_status=new_status, user_id=current_user.id)
        db.session.commit()
        flash(f"تم تغيير الحالة إلى {new_status.value}.", "success")
    except OrderError as e:
        db.session.rollback()
        flash(str(e), "danger")

    return redirect(url_for("orders.view", order_id=order.id))
