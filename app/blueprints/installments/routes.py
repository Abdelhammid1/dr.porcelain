"""Routes للبيع بالتقسيط."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from app.blueprints.installments import installments_bp
from app.extensions import db
from app.models.installment import (
    InstallmentFrequency,
    InstallmentLineStatus,
    InstallmentPlan,
    InstallmentPlanStatus,
    InstallmentScheduleLine,
)
from app.models.party import Party, PartyType
from app.models.setting import get_setting
from app.services.installments import (
    InstallmentError,
    InvoiceLineDraft,
    collect_installment_payment,
    create_installment_sale,
    get_due_today_lines,
    get_overdue_lines,
)
from app.services.security import require_permission


# ============ قائمة الخطط ============

@installments_bp.route("/", methods=["GET"])
@login_required
@require_permission("installments.view")
def index():
    q = (request.args.get("q") or "").strip()
    status_filter = request.args.get("status")
    only_overdue = request.args.get("overdue") == "1"

    query = db.session.query(InstallmentPlan).order_by(InstallmentPlan.id.desc())
    if q:
        like = f"%{q}%"
        query = (
            query.join(Party, Party.id == InstallmentPlan.customer_id)
            .filter(or_(
                InstallmentPlan.doc_number.ilike(like),
                Party.name_ar.ilike(like),
                Party.phone.ilike(like),
            ))
        )
    if status_filter and status_filter in {s.value for s in InstallmentPlanStatus}:
        query = query.filter(InstallmentPlan.status == InstallmentPlanStatus(status_filter))

    plans = query.limit(200).all()
    if only_overdue:
        plans = [p for p in plans if p.overdue_lines]

    # ملخّص أعلى الصفحة
    overdue_lines = get_overdue_lines(limit=100)
    due_today = get_due_today_lines()
    total_active = db.session.query(InstallmentPlan).filter_by(status=InstallmentPlanStatus.ACTIVE).count()

    return render_template(
        "installments/index.html",
        plans=plans,
        q=q,
        status_filter=status_filter,
        only_overdue=only_overdue,
        overdue_count=len(overdue_lines),
        due_today_count=len(due_today),
        total_active=total_active,
    )


# ============ إنشاء بيع بالتقسيط ============

@installments_bp.route("/new", methods=["GET", "POST"])
@login_required
@require_permission("installments.create")
def create():
    if request.method == "POST":
        try:
            plan = _handle_create()
            db.session.commit()
            flash(
                f"تم إنشاء خطة تقسيط {plan.doc_number} "
                f"بإجمالي {plan.total_amount} على {plan.installments_count} أقساط.",
                "success",
            )
            return redirect(url_for("installments.view", plan_id=plan.id))
        except InstallmentError as e:
            db.session.rollback()
            flash(str(e), "danger")

    return render_template(
        "installments/form.html",
        today=date.today().isoformat(),
        tax_enabled=bool(get_setting("tax.enabled", False)),
        tax_rate=str(get_setting("tax.default_rate", 0)),
    )


def _handle_create() -> InstallmentPlan:
    customer_id = request.form.get("customer_id", type=int)
    if not customer_id:
        raise InstallmentError("اختر عميلًا.")

    invoice_date_str = request.form.get("invoice_date") or date.today().isoformat()
    invoice_date = datetime.strptime(invoice_date_str, "%Y-%m-%d").date()

    start_date_str = (request.form.get("start_date") or "").strip()
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date() if start_date_str else None

    freq_str = request.form.get("frequency") or "monthly"
    try:
        frequency = InstallmentFrequency(freq_str)
    except ValueError:
        frequency = InstallmentFrequency.MONTHLY

    installments_count = int(request.form.get("installments_count") or 1)
    down_payment = Decimal(request.form.get("down_payment") or "0")
    discount = Decimal(request.form.get("discount_amount") or "0")
    notes = (request.form.get("notes") or "").strip() or None

    variant_ids = request.form.getlist("line_variant_id[]")
    qtys = request.form.getlist("line_qty[]")
    prices = request.form.getlist("line_price[]")

    drafts = []
    for i, vid in enumerate(variant_ids):
        if not vid:
            continue
        qty_str = (qtys[i] if i < len(qtys) else "0") or "0"
        price_str = (prices[i] if i < len(prices) else "").strip()
        drafts.append(InvoiceLineDraft(
            variant_id=int(vid),
            qty=Decimal(qty_str),
            unit_price=Decimal(price_str) if price_str else None,
        ))

    return create_installment_sale(
        customer_id=customer_id,
        invoice_date=invoice_date,
        lines=drafts,
        installments_count=installments_count,
        frequency=frequency,
        down_payment=down_payment,
        start_date=start_date,
        discount_amount=discount,
        notes=notes,
        user_id=current_user.id,
    )


# ============ عرض خطة + جدول الأقساط ============

@installments_bp.route("/<int:plan_id>", methods=["GET"])
@login_required
@require_permission("installments.view")
def view(plan_id):
    plan = db.session.get(InstallmentPlan, plan_id) or abort(404)
    return render_template("installments/view.html", plan=plan)


# ============ تحصيل قسط ============

@installments_bp.route("/lines/<int:line_id>/collect", methods=["POST"])
@login_required
@require_permission("installments.collect")
def collect(line_id):
    line = db.session.get(InstallmentScheduleLine, line_id) or abort(404)
    plan = line.plan

    try:
        amount = Decimal(request.form.get("amount") or "0")
        method = request.form.get("method") or "cash"
        bank_id = request.form.get("bank_account_id", type=int)
        collection_date_str = request.form.get("collection_date") or date.today().isoformat()
        collection_date = datetime.strptime(collection_date_str, "%Y-%m-%d").date()
        memo = (request.form.get("memo") or "").strip() or None

        collect_installment_payment(
            schedule_line_id=line.id,
            amount=amount,
            collection_date=collection_date,
            method=method,
            bank_account_id=bank_id,
            memo=memo,
            user_id=current_user.id,
        )
        db.session.commit()
        flash(f"تم تسجيل تحصيل {amount} للقسط رقم {line.number}.", "success")
    except (InstallmentError, ValueError) as e:
        db.session.rollback()
        flash(str(e), "danger")

    return redirect(url_for("installments.view", plan_id=plan.id))


# ============ لوحة المتأخرات (لكل الأقساط) ============

@installments_bp.route("/overdue", methods=["GET"])
@login_required
@require_permission("installments.view")
def overdue():
    lines = get_overdue_lines()
    return render_template("installments/overdue.html", lines=lines)
