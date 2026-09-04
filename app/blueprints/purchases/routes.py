"""Routes لفواتير المشتريات."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from app.blueprints.purchases import purchases_bp
from app.extensions import db
from app.models.account import Account
from app.models.journal import JournalEntry
from app.models.party import Party
from app.models.purchases import (
    PurchaseInvoice,
    PurchasePayment,
    PurchaseReturn,
    PurchaseStatus,
)
from app.models.setting import get_setting
from app.services.purchases import (
    PurchaseError,
    PurchaseLineDraft,
    PurchaseReturnLineDraft,
    create_purchase_invoice,
    create_purchase_return,
)
from app.services.security import require_permission


@purchases_bp.route("/", methods=["GET"])
@login_required
@require_permission("purchases.view")
def index():
    q = (request.args.get("q") or "").strip()
    status_filter = request.args.get("status")

    query = db.session.query(PurchaseInvoice).order_by(PurchaseInvoice.id.desc())
    if q:
        like = f"%{q}%"
        query = (
            query.join(Party, Party.id == PurchaseInvoice.vendor_id)
            .filter(or_(
                PurchaseInvoice.doc_number.ilike(like),
                PurchaseInvoice.vendor_ref.ilike(like),
                Party.name_ar.ilike(like),
            ))
        )
    if status_filter and status_filter in {s.value for s in PurchaseStatus}:
        query = query.filter(PurchaseInvoice.status == PurchaseStatus(status_filter))

    invoices = query.limit(200).all()
    return render_template("purchases/index.html", invoices=invoices, q=q, status_filter=status_filter)


@purchases_bp.route("/new", methods=["GET", "POST"])
@login_required
@require_permission("purchases.create")
def create():
    # جلب حسابات البنك المتاحة
    bank_parent = db.session.query(Account).filter_by(code="1020").one_or_none()
    bank_accounts = []
    if bank_parent:
        bank_accounts = (
            db.session.query(Account)
            .filter_by(parent_id=bank_parent.id, is_active=True, is_postable=True)
            .order_by(Account.code)
            .all()
        )

    if request.method == "POST":
        try:
            invoice = _handle_create()
            db.session.commit()
            flash(f"تم إنشاء فاتورة الشراء {invoice.doc_number}.", "success")
            return redirect(url_for("purchases.view", invoice_id=invoice.id))
        except PurchaseError as e:
            db.session.rollback()
            flash(str(e), "danger")

    return render_template(
        "purchases/form.html",
        today=date.today().isoformat(),
        tax_enabled=bool(get_setting("tax.enabled", False)),
        tax_rate=str(get_setting("tax.default_rate", 0)),
        bank_accounts=bank_accounts,
    )


def _handle_create() -> PurchaseInvoice:
    vendor_id = request.form.get("vendor_id", type=int)
    if not vendor_id:
        raise PurchaseError("اختر موردًا.")

    method_str = request.form.get("payment_method") or "credit"
    try:
        method = PurchasePayment(method_str)
    except ValueError:
        method = PurchasePayment.CREDIT

    invoice_date_str = request.form.get("invoice_date") or date.today().isoformat()
    invoice_date = datetime.strptime(invoice_date_str, "%Y-%m-%d").date()

    variant_ids = request.form.getlist("line_variant_id[]")
    qtys = request.form.getlist("line_qty[]")
    costs = request.form.getlist("line_cost[]")

    drafts = []
    for i, vid in enumerate(variant_ids):
        if not vid:
            continue
        drafts.append(PurchaseLineDraft(
            variant_id=int(vid),
            qty=Decimal((qtys[i] if i < len(qtys) else "0") or "0"),
            unit_cost=Decimal((costs[i] if i < len(costs) else "0") or "0"),
        ))

    return create_purchase_invoice(
        vendor_id=vendor_id,
        invoice_date=invoice_date,
        payment_method=method,
        lines=drafts,
        discount_amount=Decimal(request.form.get("discount_amount") or "0"),
        freight=Decimal(request.form.get("freight") or "0"),
        vendor_ref=(request.form.get("vendor_ref") or "").strip() or None,
        bank_account_id=request.form.get("bank_account_id", type=int),
        notes=(request.form.get("notes") or "").strip() or None,
        user_id=current_user.id,
    )


@purchases_bp.route("/<int:invoice_id>", methods=["GET"])
@login_required
@require_permission("purchases.view")
def view(invoice_id):
    inv = db.session.get(PurchaseInvoice, invoice_id) or abort(404)
    related_entries = (
        db.session.query(JournalEntry)
        .filter(JournalEntry.source_id == inv.id)
        .filter(JournalEntry.source_type == "purchase_invoice")
        .order_by(JournalEntry.id)
        .all()
    )
    return render_template("purchases/view.html", invoice=inv, related_entries=related_entries)


@purchases_bp.route("/<int:invoice_id>/return", methods=["GET", "POST"])
@login_required
@require_permission("purchases.return")
def return_form(invoice_id):
    inv = db.session.get(PurchaseInvoice, invoice_id) or abort(404)
    if inv.status == PurchaseStatus.RETURNED:
        flash("الفاتورة مرتجعة بالكامل بالفعل.", "warning")
        return redirect(url_for("purchases.view", invoice_id=inv.id))

    if request.method == "POST":
        try:
            reason = (request.form.get("reason") or "").strip()
            return_date_str = request.form.get("return_date") or date.today().isoformat()
            return_date = datetime.strptime(return_date_str, "%Y-%m-%d").date()
            is_full = request.form.get("full_return") == "1"
            if is_full:
                ret = create_purchase_return(
                    invoice_id=inv.id, return_date=return_date,
                    reason=reason, user_id=current_user.id,
                )
            else:
                line_ids = request.form.getlist("return_line_id[]")
                qtys = request.form.getlist("return_qty[]")
                drafts = []
                for i, lid in enumerate(line_ids):
                    if not lid:
                        continue
                    qty = Decimal((qtys[i] if i < len(qtys) else "0") or "0")
                    if qty > 0:
                        drafts.append(PurchaseReturnLineDraft(int(lid), qty))
                if not drafts:
                    raise PurchaseError("حدد كميات المرتجع.")
                ret = create_purchase_return(
                    invoice_id=inv.id, return_date=return_date,
                    reason=reason, lines=drafts, user_id=current_user.id,
                )
            db.session.commit()
            flash(f"تم إنشاء مرتجع {ret.doc_number}.", "success")
            return redirect(url_for("purchases.view", invoice_id=inv.id))
        except PurchaseError as e:
            db.session.rollback()
            flash(str(e), "danger")

    return render_template("purchases/return_form.html", invoice=inv, today=date.today().isoformat())


@purchases_bp.route("/returns/<int:return_id>", methods=["GET"])
@login_required
@require_permission("purchases.view")
def return_view(return_id):
    ret = db.session.get(PurchaseReturn, return_id) or abort(404)
    return render_template("purchases/return_view.html", ret=ret)
