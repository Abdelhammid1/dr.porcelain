"""Routes لفواتير المبيعات والمرتجعات."""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from app.blueprints.sales import sales_bp
from app.extensions import db
from app.models.journal import JournalEntry
from app.models.party import Party, PartyType
from app.models.sales import (
    InvoiceStatus,
    PaymentMethod,
    SalesInvoice,
    SalesReturn,
)
from app.models.setting import get_setting
from app.services.sales import (
    InvoiceLineDraft,
    ReturnLineDraft,
    SalesError,
    create_cash_sale,
    create_sales_return,
)
from app.services.security import require_permission


# ============================================================
# قائمة الفواتير
# ============================================================

@sales_bp.route("/", methods=["GET"])
@login_required
@require_permission("sales.view")
def index():
    q = (request.args.get("q") or "").strip()
    status_filter = request.args.get("status")

    query = db.session.query(SalesInvoice).order_by(SalesInvoice.id.desc())
    if q:
        like = f"%{q}%"
        query = (
            query.join(Party, Party.id == SalesInvoice.customer_id)
            .filter(or_(
                SalesInvoice.doc_number.ilike(like),
                Party.name_ar.ilike(like),
                Party.phone.ilike(like),
            ))
        )
    if status_filter and status_filter in {s.value for s in InvoiceStatus}:
        query = query.filter(SalesInvoice.status == InvoiceStatus(status_filter))

    invoices = query.limit(200).all()
    return render_template(
        "sales/index.html",
        invoices=invoices,
        q=q,
        status_filter=status_filter,
    )


# ============================================================
# إنشاء فاتورة جديدة
# ============================================================

@sales_bp.route("/new", methods=["GET", "POST"])
@login_required
@require_permission("sales.create")
def create():
    if request.method == "POST":
        try:
            invoice = _handle_create_submit()
            db.session.commit()
            flash(f"تم إنشاء الفاتورة {invoice.doc_number} بإجمالي {invoice.total}.", "success")
            return redirect(url_for("sales.view", invoice_id=invoice.id))
        except SalesError as e:
            db.session.rollback()
            flash(str(e), "danger")

    return render_template(
        "sales/form.html",
        today=date.today().isoformat(),
        tax_enabled=bool(get_setting("tax.enabled", False)),
        tax_rate=str(get_setting("tax.default_rate", 0)),
    )


def _handle_create_submit() -> SalesInvoice:
    customer_id = request.form.get("customer_id", type=int)
    if not customer_id:
        raise SalesError("اختر عميلًا.")

    invoice_date_str = request.form.get("invoice_date") or date.today().isoformat()
    try:
        invoice_date = datetime.strptime(invoice_date_str, "%Y-%m-%d").date()
    except ValueError:
        raise SalesError("تاريخ الفاتورة غير صحيح.")

    discount_str = (request.form.get("discount_amount") or "0").strip()
    discount = Decimal(discount_str or "0")

    method_str = request.form.get("payment_method") or "cash"
    try:
        method = PaymentMethod(method_str)
    except ValueError:
        method = PaymentMethod.CASH

    notes = (request.form.get("notes") or "").strip() or None

    variant_ids = request.form.getlist("line_variant_id[]")
    qtys = request.form.getlist("line_qty[]")
    prices = request.form.getlist("line_price[]")

    drafts: list[InvoiceLineDraft] = []
    for i, vid in enumerate(variant_ids):
        if not vid:
            continue
        try:
            variant_id = int(vid)
        except ValueError:
            continue
        qty_str = (qtys[i] if i < len(qtys) else "0").strip() or "0"
        price_str = (prices[i] if i < len(prices) else "").strip()
        drafts.append(InvoiceLineDraft(
            variant_id=variant_id,
            qty=Decimal(qty_str) if qty_str else Decimal("0"),
            unit_price=Decimal(price_str) if price_str else None,
        ))

    return create_cash_sale(
        customer_id=customer_id,
        invoice_date=invoice_date,
        lines=drafts,
        discount_amount=discount,
        notes=notes,
        user_id=current_user.id,
        payment_method=method,
    )


# ============================================================
# عرض فاتورة
# ============================================================

@sales_bp.route("/<int:invoice_id>", methods=["GET"])
@login_required
@require_permission("sales.view")
def view(invoice_id):
    inv = db.session.get(SalesInvoice, invoice_id) or abort(404)

    related_entries = (
        db.session.query(JournalEntry)
        .filter(JournalEntry.source_id == inv.id)
        .filter(JournalEntry.source_type.in_(["sales_invoice", "customer_receipt"]))
        .order_by(JournalEntry.id)
        .all()
    )
    return render_template(
        "sales/view.html",
        invoice=inv,
        related_entries=related_entries,
    )


# ============================================================
# مرتجع
# ============================================================

@sales_bp.route("/<int:invoice_id>/return", methods=["GET", "POST"])
@login_required
@require_permission("sales.return")
def return_form(invoice_id):
    inv = db.session.get(SalesInvoice, invoice_id) or abort(404)

    if inv.status == InvoiceStatus.RETURNED:
        flash("الفاتورة مرتجعة بالكامل بالفعل.", "warning")
        return redirect(url_for("sales.view", invoice_id=inv.id))

    if request.method == "POST":
        try:
            reason = (request.form.get("reason") or "").strip()
            return_date_str = request.form.get("return_date") or date.today().isoformat()
            return_date = datetime.strptime(return_date_str, "%Y-%m-%d").date()

            # هل مرتجع كامل أم جزئي؟
            is_full = request.form.get("full_return") == "1"

            if is_full:
                ret = create_sales_return(
                    invoice_id=inv.id,
                    return_date=return_date,
                    reason=reason,
                    user_id=current_user.id,
                )
            else:
                # نبني قائمة أسطر بكميات > 0
                line_ids = request.form.getlist("return_line_id[]")
                qtys = request.form.getlist("return_qty[]")
                drafts = []
                for i, lid in enumerate(line_ids):
                    if not lid:
                        continue
                    qty_str = (qtys[i] if i < len(qtys) else "0").strip() or "0"
                    qty = Decimal(qty_str)
                    if qty > 0:
                        drafts.append(ReturnLineDraft(
                            invoice_line_id=int(lid),
                            qty=qty,
                        ))
                if not drafts:
                    raise SalesError("حدد كميات المرتجع.")
                ret = create_sales_return(
                    invoice_id=inv.id,
                    return_date=return_date,
                    reason=reason,
                    lines=drafts,
                    user_id=current_user.id,
                )
            db.session.commit()
            flash(f"تم إنشاء مرتجع {ret.doc_number}.", "success")
            return redirect(url_for("sales.view", invoice_id=inv.id))
        except SalesError as e:
            db.session.rollback()
            flash(str(e), "danger")

    return render_template("sales/return_form.html", invoice=inv, today=date.today().isoformat())


# ============================================================
# عرض مرتجع
# ============================================================

@sales_bp.route("/returns/<int:return_id>", methods=["GET"])
@login_required
@require_permission("sales.view")
def return_view(return_id):
    ret = db.session.get(SalesReturn, return_id) or abort(404)
    return render_template("sales/return_view.html", ret=ret)
