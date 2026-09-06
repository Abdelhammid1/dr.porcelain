from datetime import date, timedelta
from decimal import Decimal

from flask import jsonify, redirect, render_template, url_for
from flask_login import current_user, login_required

from app.blueprints.main import main_bp
from app.extensions import db
from app.services.dashboard import get_dashboard_data


@main_bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return redirect(url_for("auth.login"))


@main_bp.route("/dashboard")
@login_required
def dashboard():
    data = get_dashboard_data()
    return render_template("main/dashboard.html", data=data)


# ============================================================
# Ticket 3 Epic 9 — Cron: تذكيرات الأقساط اليومية
# ============================================================
# نقطة الوصول العامة (يُستدعى من أي جدولة خارجية cron/systemd/GitHub Actions).
# ينتج قائمة الأقساط التي تستحق خلال reminder_days_before يوم.
# لو reminders_enabled=False → يرجع {'enabled': false} ولا يعمل شيء.

@main_bp.route("/cron/installment-reminders", methods=["GET", "POST"])
def cron_installment_reminders():
    from app.models.installment import (
        InstallmentPlan, InstallmentPlanStatus,
        InstallmentScheduleLine, InstallmentLineStatus,
    )
    from app.models.setting import get_setting

    if not bool(get_setting("installments.reminders_enabled", False)):
        return jsonify({"enabled": False, "reminders": []})

    days_before = int(get_setting("installments.reminder_days_before", 3) or 3)
    today = date.today()
    threshold = today + timedelta(days=days_before)

    lines = (
        db.session.query(InstallmentScheduleLine)
        .join(InstallmentPlan, InstallmentPlan.id == InstallmentScheduleLine.plan_id)
        .filter(InstallmentScheduleLine.status == InstallmentLineStatus.PENDING)
        .filter(InstallmentScheduleLine.due_date >= today)
        .filter(InstallmentScheduleLine.due_date <= threshold)
        .filter(InstallmentPlan.status == InstallmentPlanStatus.ACTIVE)
        .order_by(InstallmentScheduleLine.due_date)
        .all()
    )

    reminders = []
    for ln in lines:
        plan = ln.plan
        customer = plan.customer if plan else None
        reminders.append({
            "plan_number": plan.doc_number if plan else None,
            "customer_id": customer.id if customer else None,
            "customer_name": customer.name_ar if customer else None,
            "customer_phone": customer.phone if customer else None,
            "line_number": ln.line_number,
            "due_date": ln.due_date.isoformat(),
            "amount": str(ln.amount),
            "days_until_due": (ln.due_date - today).days,
        })
    return jsonify({
        "enabled": True,
        "as_of": today.isoformat(),
        "days_before": days_before,
        "count": len(reminders),
        "reminders": reminders,
    })
