"""Routes لنقطة البيع."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.blueprints.pos import pos_bp
from app.extensions import db
from app.models.party import Party, PartyType
from app.models.pos import POSSession, SessionStatus
from app.models.sales import PaymentMethod, SalesInvoice
from app.models.setting import get_setting
from app.services.pos import (
    POSError,
    close_session,
    current_open_session_for,
    open_session,
    session_summary,
)
from app.services.sales import InvoiceLineDraft, SalesError, create_cash_sale
from app.services.security import require_permission


# ============ نقطة الدخول ============

@pos_bp.route("/", methods=["GET"])
@login_required
@require_permission("pos.use")
def index():
    """يوجه للورديّة المفتوحة أو لشاشة الفتح."""
    open_sess = current_open_session_for(current_user.id)
    if open_sess is None:
        return redirect(url_for("pos.open"))
    return redirect(url_for("pos.terminal", session_id=open_sess.id))


# ============ فتح وردية ============

@pos_bp.route("/open", methods=["GET", "POST"])
@login_required
@require_permission("pos.use")
def open():
    # إذا كان له وردية مفتوحة، وجّهه لها
    existing = current_open_session_for(current_user.id)
    if existing is not None:
        return redirect(url_for("pos.terminal", session_id=existing.id))

    if request.method == "POST":
        try:
            opening = Decimal(request.form.get("opening_cash") or "0")
            notes = (request.form.get("notes") or "").strip() or None
            sess = open_session(
                cashier_id=current_user.id,
                opening_cash=opening,
                notes=notes,
            )
            db.session.commit()
            flash(f"تم فتح الوردية {sess.doc_number}.", "success")
            return redirect(url_for("pos.terminal", session_id=sess.id))
        except (POSError, ValueError) as e:
            db.session.rollback()
            flash(str(e), "danger")

    return render_template("pos/open.html")


# ============ شاشة البيع (Terminal) ============

@pos_bp.route("/session/<int:session_id>", methods=["GET"])
@login_required
@require_permission("pos.use")
def terminal(session_id):
    sess = db.session.get(POSSession, session_id) or abort(404)
    if sess.status != SessionStatus.OPEN:
        flash("الوردية مقفلة.", "warning")
        return redirect(url_for("pos.session_view", session_id=sess.id))
    # لو الوردية ليست للمستخدم الحالي، امنعه
    if sess.cashier_id != current_user.id and not current_user.can("pos.view_all"):
        abort(403)

    # عميل افتراضي "بيع نقدي عام" — نستخدم أول عميل بكود C-DEFAULT إن وُجد
    default_customer = _ensure_default_walkin_customer()

    tax_enabled = bool(get_setting("tax.enabled", False))
    tax_rate = get_setting("tax.default_rate", 0)

    return render_template(
        "pos/terminal.html",
        session=sess,
        default_customer=default_customer,
        tax_enabled=tax_enabled,
        tax_rate=tax_rate,
    )


# ============ إنشاء فاتورة POS (AJAX) ============

@pos_bp.route("/session/<int:session_id>/sale", methods=["POST"])
@login_required
@require_permission("pos.use")
def create_sale(session_id):
    sess = db.session.get(POSSession, session_id) or abort(404)
    if sess.status != SessionStatus.OPEN:
        return jsonify({"ok": False, "error": "الوردية مقفلة."}), 400
    if sess.cashier_id != current_user.id:
        return jsonify({"ok": False, "error": "غير مصرح."}), 403

    try:
        payload = request.get_json(silent=True) or request.form.to_dict(flat=False) or {}
        customer_id = int(payload.get("customer_id") or 0)
        if not customer_id:
            raise SalesError("اختر عميلًا.")

        method_str = (payload.get("payment_method") or "cash")
        try:
            method = PaymentMethod(method_str)
        except ValueError:
            method = PaymentMethod.CASH

        discount = Decimal(str(payload.get("discount_amount") or "0"))
        lines_payload = payload.get("lines") or []

        drafts = []
        for ln in lines_payload:
            drafts.append(InvoiceLineDraft(
                variant_id=int(ln["variant_id"]),
                qty=Decimal(str(ln.get("qty") or "0")),
                unit_price=Decimal(str(ln["unit_price"])) if ln.get("unit_price") not in (None, "") else None,
            ))

        invoice = create_cash_sale(
            customer_id=customer_id,
            invoice_date=date.today(),
            lines=drafts,
            discount_amount=discount,
            payment_method=method,
            pos_session_id=sess.id,
            user_id=current_user.id,
        )
        db.session.commit()

        return jsonify({
            "ok": True,
            "invoice_id": invoice.id,
            "doc_number": invoice.doc_number,
            "total": str(invoice.total),
            "receipt_url": url_for("pos.receipt", invoice_id=invoice.id),
        })
    except SalesError as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": f"خطأ داخلي: {e}"}), 500


# ============ إيصال طباعة ============

@pos_bp.route("/receipt/<int:invoice_id>", methods=["GET"])
@login_required
@require_permission("pos.use")
def receipt(invoice_id):
    inv = db.session.get(SalesInvoice, invoice_id) or abort(404)
    store_name = str(get_setting("store.name", "المتجر"))
    store_addr = str(get_setting("store.address", "") or "")
    store_phone = str(get_setting("store.phone", "") or "")
    tax_number = str(get_setting("store.tax_number", "") or "")
    return render_template(
        "pos/receipt.html",
        invoice=inv,
        store_name=store_name,
        store_addr=store_addr,
        store_phone=store_phone,
        tax_number=tax_number,
    )


# ============ قفل الوردية ============

@pos_bp.route("/session/<int:session_id>/close", methods=["GET", "POST"])
@login_required
@require_permission("pos.use")
def close(session_id):
    sess = db.session.get(POSSession, session_id) or abort(404)
    is_owner = sess.cashier_id == current_user.id
    if not is_owner and not current_user.can("pos.close_any"):
        abort(403)

    if sess.status != SessionStatus.OPEN:
        return redirect(url_for("pos.session_view", session_id=sess.id))

    summary = session_summary(sess.id)

    if request.method == "POST":
        try:
            actual = Decimal(request.form.get("closing_cash_actual") or "0")
            notes = (request.form.get("notes") or "").strip() or None
            close_session(
                session_id=sess.id,
                closing_cash_actual=actual,
                notes=notes,
                user_id=current_user.id,
            )
            db.session.commit()
            flash(f"تم قفل الوردية {sess.doc_number}.", "success")
            return redirect(url_for("pos.session_view", session_id=sess.id))
        except (POSError, ValueError) as e:
            db.session.rollback()
            flash(str(e), "danger")

    return render_template("pos/close.html", session=sess, summary=summary)


# ============ قائمة الورديات + عرض ============

@pos_bp.route("/sessions", methods=["GET"])
@login_required
@require_permission("pos.use")
def sessions_index():
    scope = request.args.get("scope") or ("all" if current_user.can("pos.view_all") else "mine")
    status_filter = request.args.get("status")

    q = db.session.query(POSSession).order_by(POSSession.id.desc())
    if scope == "mine":
        q = q.filter(POSSession.cashier_id == current_user.id)
    elif not current_user.can("pos.view_all"):
        # لا يستطيع رؤية كل الورديات، فرض mine
        q = q.filter(POSSession.cashier_id == current_user.id)

    if status_filter in {"open", "closed"}:
        q = q.filter(POSSession.status == SessionStatus(status_filter))

    sessions = q.limit(200).all()
    return render_template("pos/sessions_index.html", sessions=sessions,
                           scope=scope, status_filter=status_filter)


@pos_bp.route("/session/<int:session_id>/view", methods=["GET"])
@login_required
@require_permission("pos.use")
def session_view(session_id):
    sess = db.session.get(POSSession, session_id) or abort(404)
    if sess.cashier_id != current_user.id and not current_user.can("pos.view_all"):
        abort(403)
    summary = session_summary(sess.id)
    return render_template("pos/session_view.html", session=sess, summary=summary)


@pos_bp.route("/session/<int:session_id>/z-report", methods=["GET"])
@login_required
@require_permission("pos.use")
def session_close_receipt(session_id):
    """Z-Report حراري (80mm) لطباعة تسوية الوردية."""
    sess = db.session.get(POSSession, session_id) or abort(404)
    if sess.cashier_id != current_user.id and not current_user.can("pos.view_all"):
        abort(403)
    summary = session_summary(sess.id)
    return render_template(
        "pos/session_close_receipt.html",
        session=sess,
        summary=summary,
        store_name=str(get_setting("store.name", "دكتور بورسلين") or "دكتور بورسلين"),
        store_addr=str(get_setting("store.address", "") or ""),
        store_phone=str(get_setting("store.phone", "") or ""),
        tax_number=str(get_setting("store.tax_number", "") or ""),
    )


# ============ Helpers ============

def _ensure_default_walkin_customer() -> Party:
    """يُنشِئ (إن لم يوجد) عميلًا افتراضيًا للبيع النقدي السريع بدون بيانات."""
    from app.services.parties import create_party
    walkin = (
        db.session.query(Party)
        .filter_by(type=PartyType.CUSTOMER, code="C-WALKIN")
        .first()
    )
    if walkin is None:
        # نُنشِئ عميلًا بكود ثابت مميز (خارج ترقيم C-XXXXXX العادي)
        walkin = Party(
            type=PartyType.CUSTOMER,
            code="C-WALKIN",
            name_ar="عميل نقدي",
            is_active=True,
        )
        db.session.add(walkin)
        db.session.flush()
        # نفتح له حساب فرعي تحت 1200
        from app.models.account import Account
        parent = db.session.query(Account).filter_by(code="1200").one()
        account = Account(
            code="1200-C-WALKIN",
            name_ar="عميل نقدي (عام)",
            type=parent.type,
            parent_id=parent.id,
            party_id=walkin.id,
            is_postable=True,
            is_system=True,
            is_active=True,
        )
        db.session.add(account)
        db.session.commit()
    return walkin
