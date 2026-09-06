"""Routes للإشعارات الداخلية (Ticket 4 Epic 2)."""
from __future__ import annotations

from flask import jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.blueprints.notifications import notifications_bp
from app.extensions import db
from app.services import notifications as notif_service


@notifications_bp.route("/", methods=["GET"])
@login_required
def index():
    filter_type = request.args.get("type")
    unread_only = request.args.get("unread") == "1"
    items = notif_service.visible_for(current_user.id, unread_only=unread_only, limit=100)
    if filter_type:
        items = [n for n in items if n.notification_type == filter_type]
    types = sorted({n.notification_type for n in notif_service.visible_for(current_user.id, limit=200)})
    return render_template("notifications/index.html",
                           items=items, filter_type=filter_type,
                           types=types, unread_only=unread_only)


@notifications_bp.route("/api/summary", methods=["GET"])
@login_required
def api_summary():
    """JSON خفيف للـ polling من الجرس (كل 30 ثانية)."""
    unread = notif_service.count_unread_for(current_user.id)
    latest = notif_service.visible_for(current_user.id, unread_only=False, limit=8)
    return jsonify({
        "unread_count": unread,
        "latest": [{
            "id": n.id, "title": n.title, "body": n.body or "",
            "link": n.link or "",
            "type": n.notification_type,
            "is_read": n.is_read,
        } for n in latest],
    })


@notifications_bp.route("/<int:notif_id>/mark-read", methods=["POST"])
@login_required
def mark_read(notif_id):
    notif_service.mark_read(notif_id)
    db.session.commit()
    return redirect(request.referrer or url_for("notifications.index"))


@notifications_bp.route("/mark-all-read", methods=["POST"])
@login_required
def mark_all():
    notif_service.mark_all_read(current_user.id)
    db.session.commit()
    return redirect(request.referrer or url_for("notifications.index"))
