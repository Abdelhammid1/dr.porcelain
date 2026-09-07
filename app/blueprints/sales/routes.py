"""Routes لفواتير المبيعات والمرتجعات."""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

from flask import abort, current_app, flash, jsonify, redirect, render_template, request, url_for
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
    record_customer_receipt,
)
from app.services.security import require_permission


# ============================================================
# قائمة الفواتير
# ============================================================

# ============================================================
# Ticket 3 Epic 7 — الفواتير غير المحصّلة (آجل)
# ============================================================

@sales_bp.route("/unpaid", methods=["GET"])
@login_required
@require_permission("sales.view")
def unpaid():
    """الفواتير الآجلة (ON_CREDIT) التي لم تُحصَّل بالكامل بعد."""
    try:
        invoices = (
            db.session.query(SalesInvoice)
            .filter(SalesInvoice.payment_method == PaymentMethod.ON_CREDIT)
            .filter(SalesInvoice.status.in_([
                InvoiceStatus.POSTED, InvoiceStatus.PARTIAL_RETURNED,
            ]))
            .order_by(SalesInvoice.invoice_date, SalesInvoice.id)
            .all()
        )
        # نُصفّي الفواتير المدفوعة بالكامل حتى لو ON_CREDIT — نلف كل حساب في
        # try عشان لو amount_due على فاتورة واحدة اترفع لأي سبب (بيانات
        # قديمة/مهجورة، Column مش موجود بعد تغيير schema، إلخ) نبقى صفّرنا
        # الفاتورة دي فقط بدل ما تسقط الصفحة كلها بـ 500.
        unpaid_only: list[SalesInvoice] = []
        for inv in invoices:
            try:
                if inv.amount_due > 0:
                    unpaid_only.append(inv)
            except Exception:
                # سطر واحد لا يُسقِط الصفحة — يظهر في السجل بس نسمح للباقي بالعرض
                current_app.logger.exception(
                    "sales.unpaid: failed to compute amount_due for invoice id=%s",
                    getattr(inv, "id", "?"),
                )
                continue
    except Exception:
        current_app.logger.exception("sales.unpaid: DB query failed")
        flash("تعذّر تحميل الفواتير غير المحصّلة — تحقق من السجل.", "danger")
        unpaid_only = []
    return render_template("sales/unpaid.html", invoices=unpaid_only)


@sales_bp.route("/<int:invoice_id>/collect", methods=["POST"])
@login_required
@require_permission("collect.customer")
def collect(invoice_id):
    invoice = db.session.get(SalesInvoice, invoice_id) or abort(404)
    amount = request.form.get("amount")
    method_str = request.form.get("payment_method") or "cash"
    try:
        method = PaymentMethod(method_str)
    except ValueError:
        method = PaymentMethod.CASH
    try:
        record_customer_receipt(
            invoice_id=invoice.id,
            amount=Decimal(str(amount or "0")),
            receipt_date=date.today(),
            payment_method=method,
            memo=request.form.get("memo") or None,
            user_id=current_user.id,
        )
        db.session.commit()
        flash(f"تم تحصيل {amount} من الفاتورة {invoice.doc_number}.", "success")
    except SalesError as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(url_for("sales.view", invoice_id=invoice.id))


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


@sales_bp.route("/<int:invoice_id>/receipt", methods=["GET"])
@login_required
@require_permission("sales.view")
def thermal_receipt(invoice_id):
    """إيصال حراري 80mm عام لأي فاتورة بيع — POS، تقسيط، أونلاين، أو يدوي.

    نفس القالب المستخدم في pos.receipt، لكن يعمل مع أي SalesInvoice بغض
    النظر عن مصدرها. الفروق التي يعرضها القالب:
      - المصدر (POS / أونلاين / تقسيط / يدوي)
      - العميل: 'عميل نقدي' لو walk-in، وإلا الاسم الفعلي
      - التقسيط: مقدم + متبقي + عدد الأقساط
      - الآجل: مدفوع + متبقي
    """
    inv = db.session.get(SalesInvoice, invoice_id) or abort(404)
    return render_template(
        "pos/receipt.html",
        invoice=inv,
        store_name=str(get_setting("store.name", "دكتور بورسلين") or "دكتور بورسلين"),
        store_addr=str(get_setting("store.address", "") or ""),
        store_phone=str(get_setting("store.phone", "") or ""),
        store_cr=str(get_setting("store.commercial_reg", "") or ""),
        tax_number=str(get_setting("store.tax_number", "") or ""),
        tax_enabled=bool(get_setting("tax.enabled", False)),
        tax_rate=str(get_setting("tax.default_rate", "14")),
    )


@sales_bp.route("/returns/<int:return_id>/receipt", methods=["GET"])
@login_required
@require_permission("sales.view")
def return_receipt(return_id):
    """إيصال مرتجع حراري (80mm) — طباعة مباشرة."""
    ret = db.session.get(SalesReturn, return_id) or abort(404)
    return render_template(
        "sales/return_receipt.html",
        ret=ret,
        store_name=str(get_setting("store.name", "دكتور بورسلين") or "دكتور بورسلين"),
        store_addr=str(get_setting("store.address", "") or ""),
        store_phone=str(get_setting("store.phone", "") or ""),
        tax_number=str(get_setting("store.tax_number", "") or ""),
    )
