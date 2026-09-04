"""Routes لجداول سداد الموردين."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.blueprints.vendor_payments import vendor_payments_bp
from app.extensions import db
from app.models.purchases import PurchaseInvoice, PurchasePayment
from app.models.vendor_payment import (
    VendorPayment,
    VendorPaymentSchedule,
    VendorPaymentScheduleLine,
    VendorScheduleStatus,
)
from app.services.security import require_permission
from app.services.vendor_payments import (
    ScheduleLineDraft,
    VendorPaymentError,
    attach_payment_schedule,
    record_vendor_payment,
)


# ---------- قائمة الجداول ----------

@vendor_payments_bp.route("/", methods=["GET"])
@login_required
@require_permission("vendor_payments.view")
def index():
    schedules = (
        db.session.query(VendorPaymentSchedule)
        .order_by(VendorPaymentSchedule.id.desc())
        .limit(200)
        .all()
    )
    return render_template("vendor_payments/index.html", schedules=schedules)


# ---------- إرفاق جدول بفاتورة ----------

@vendor_payments_bp.route("/invoice/<int:invoice_id>/attach", methods=["GET", "POST"])
@login_required
@require_permission("vendor_payments.manage")
def attach(invoice_id):
    inv = db.session.get(PurchaseInvoice, invoice_id) or abort(404)
    if inv.payment_method != PurchasePayment.CREDIT:
        flash("لا يمكن إرفاق جدول إلا بفاتورة شراء آجلة (CREDIT).", "danger")
        return redirect(url_for("purchases.view", invoice_id=inv.id))

    existing = (
        db.session.query(VendorPaymentSchedule)
        .filter_by(purchase_invoice_id=inv.id)
        .first()
    )
    if existing:
        return redirect(url_for("vendor_payments.view", schedule_id=existing.id))

    if request.method == "POST":
        try:
            dates = request.form.getlist("line_due_date[]")
            amounts = request.form.getlist("line_amount[]")
            drafts = []
            for i, d in enumerate(dates):
                if not d:
                    continue
                amt = Decimal((amounts[i] if i < len(amounts) else "0") or "0")
                if amt <= 0:
                    continue
                drafts.append(ScheduleLineDraft(
                    due_date=datetime.strptime(d, "%Y-%m-%d").date(),
                    amount=amt,
                ))
            schedule = attach_payment_schedule(
                purchase_invoice_id=inv.id,
                lines=drafts,
                notes=(request.form.get("notes") or "").strip() or None,
                user_id=current_user.id,
            )
            db.session.commit()
            flash("تم إنشاء جدول السداد بنجاح.", "success")
            return redirect(url_for("vendor_payments.view", schedule_id=schedule.id))
        except (VendorPaymentError, ValueError) as e:
            db.session.rollback()
            flash(str(e), "danger")

    return render_template("vendor_payments/attach.html", invoice=inv,
                           today=date.today().isoformat())


# ---------- عرض جدول + سداد ----------

@vendor_payments_bp.route("/<int:schedule_id>", methods=["GET"])
@login_required
@require_permission("vendor_payments.view")
def view(schedule_id):
    schedule = db.session.get(VendorPaymentSchedule, schedule_id) or abort(404)
    return render_template("vendor_payments/view.html", schedule=schedule,
                           today=date.today().isoformat())


@vendor_payments_bp.route("/lines/<int:line_id>/pay", methods=["POST"])
@login_required
@require_permission("vendor_payments.pay")
def pay(line_id):
    line = db.session.get(VendorPaymentScheduleLine, line_id) or abort(404)
    schedule = line.schedule
    try:
        amount = Decimal(request.form.get("amount") or "0")
        method = request.form.get("method") or "cash"
        bank_id = request.form.get("bank_account_id", type=int)
        payment_date_str = request.form.get("payment_date") or date.today().isoformat()
        payment_date = datetime.strptime(payment_date_str, "%Y-%m-%d").date()
        memo = (request.form.get("memo") or "").strip() or None

        record_vendor_payment(
            schedule_line_id=line.id,
            amount=amount,
            payment_date=payment_date,
            method=method,
            bank_account_id=bank_id,
            memo=memo,
            user_id=current_user.id,
        )
        db.session.commit()
        flash(f"تم تسجيل السداد {amount} للدفعة {line.number}.", "success")
    except (VendorPaymentError, ValueError) as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(url_for("vendor_payments.view", schedule_id=schedule.id))
