"""Admin routes لسجل القيود والقيد اليدوي وعكس القيود (Ticket 3 Epic 5)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.blueprints.journal import journal_bp
from app.extensions import db
from app.models.account import Account
from app.models.journal import JournalEntry, JournalEntryStatus, JournalSourceType
from app.services.ledger import LedgerError, LedgerLineDraft, post_journal_entry, reverse_entry
from app.services.security import require_permission


# ============ سجل القيود العام ============

@journal_bp.route("/", methods=["GET"])
@login_required
@require_permission("journal.view")
def index():
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    source_type = request.args.get("source_type")

    q = db.session.query(JournalEntry)
    if date_from:
        q = q.filter(JournalEntry.entry_date >= date_from)
    if date_to:
        q = q.filter(JournalEntry.entry_date <= date_to)
    if source_type:
        try:
            q = q.filter(JournalEntry.source_type == JournalSourceType(source_type))
        except ValueError:
            pass
    entries = q.order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc()).limit(200).all()
    source_types = list(JournalSourceType)
    return render_template("journal/index.html",
                           entries=entries, source_types=source_types,
                           date_from=date_from, date_to=date_to,
                           selected_source=source_type)


@journal_bp.route("/<int:entry_id>", methods=["GET"])
@login_required
@require_permission("journal.view")
def view(entry_id):
    entry = db.session.get(JournalEntry, entry_id) or abort(404)
    return render_template("journal/view.html", entry=entry)


# ============ قيد يدوي ============

@journal_bp.route("/new", methods=["GET", "POST"])
@login_required
@require_permission("journal.create")
def create():
    accounts = (
        db.session.query(Account)
        .filter_by(is_active=True, is_postable=True)
        .order_by(Account.code)
        .all()
    )
    if request.method == "POST":
        entry_date_str = request.form.get("entry_date") or date.today().isoformat()
        memo = (request.form.get("memo") or "").strip()
        try:
            entry_date = datetime.strptime(entry_date_str, "%Y-%m-%d").date()
        except ValueError:
            flash("تاريخ غير صحيح.", "danger")
            return redirect(url_for("journal.create"))

        # اقرأ سطور الفورم — line_account[], line_debit[], line_credit[], line_memo[]
        acc_ids = request.form.getlist("line_account[]")
        debits = request.form.getlist("line_debit[]")
        credits = request.form.getlist("line_credit[]")
        memos = request.form.getlist("line_memo[]")

        lines: list[LedgerLineDraft] = []
        for i in range(len(acc_ids)):
            aid = acc_ids[i]
            if not aid:
                continue
            d = Decimal(str(debits[i] or "0")) if i < len(debits) else Decimal("0")
            c = Decimal(str(credits[i] or "0")) if i < len(credits) else Decimal("0")
            if d == 0 and c == 0:
                continue
            lines.append(LedgerLineDraft(
                account_id=int(aid), debit=d, credit=c,
                memo=(memos[i] if i < len(memos) else None),
            ))

        try:
            post_journal_entry(
                entry_date=entry_date,
                source_type=JournalSourceType.MANUAL,
                source_id=None,
                memo=memo or "قيد يدوي",
                lines=lines,
                user_id=current_user.id,
            )
            db.session.commit()
            flash("تم إنشاء القيد.", "success")
            return redirect(url_for("journal.index"))
        except LedgerError as e:
            db.session.rollback()
            flash(str(e), "danger")

    return render_template("journal/create.html", accounts=accounts)


@journal_bp.route("/<int:entry_id>/reverse", methods=["POST"])
@login_required
@require_permission("journal.reverse")
def reverse(entry_id):
    entry = db.session.get(JournalEntry, entry_id) or abort(404)
    reason = (request.form.get("reason") or "").strip()
    try:
        reverse_entry(entry_id=entry.id, reason=reason, user_id=current_user.id)
        db.session.commit()
        flash(f"تم عكس القيد {entry.doc_number}.", "success")
    except LedgerError as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(url_for("journal.view", entry_id=entry.id))


# ============ قوالب سريعة ============

QUICK_TEMPLATES = {
    "add_capital": {
        "label_ar": "إضافة رأس مال",
        "memo_ar": "إضافة رأس مال",
        "lines": [
            {"account_code": "1010", "side": "debit",  "label": "النقدية/البنك (المستلم)"},
            {"account_code": "3100", "side": "credit", "label": "رأس المال"},
        ],
    },
    "owner_drawing": {
        "label_ar": "سحب من رأس المال",
        "memo_ar": "مسحوبات صاحب المتجر",
        "lines": [
            {"account_code": "3300", "side": "debit",  "label": "مسحوبات صاحب المتجر"},
            {"account_code": "1010", "side": "credit", "label": "النقدية/البنك"},
        ],
    },
    "deposit_received": {
        "label_ar": "أمانة مستلمة",
        "memo_ar": "أمانة مستلمة",
        "lines": [
            {"account_code": "1010", "side": "debit",  "label": "النقدية/البنك"},
            {"account_code": "3400", "side": "credit", "label": "أمانات"},
        ],
    },
    "short_loan": {
        "label_ar": "قرض قصير الأجل",
        "memo_ar": "استلام قرض قصير الأجل",
        "lines": [
            {"account_code": "1010", "side": "debit",  "label": "النقدية/البنك"},
            {"account_code": "2400", "side": "credit", "label": "قروض قصيرة الأجل"},
        ],
    },
    "long_loan": {
        "label_ar": "قرض طويل الأجل",
        "memo_ar": "استلام قرض طويل الأجل",
        "lines": [
            {"account_code": "1010", "side": "debit",  "label": "النقدية/البنك"},
            {"account_code": "2500", "side": "credit", "label": "قروض طويلة الأجل"},
        ],
    },
}


@journal_bp.route("/quick/<template_key>", methods=["GET"])
@login_required
@require_permission("journal.create")
def quick_template(template_key):
    """يفتح شاشة القيد اليدوي مع الأسطر مملوءة بحسب القالب."""
    tpl = QUICK_TEMPLATES.get(template_key)
    if not tpl:
        abort(404)
    accounts = (
        db.session.query(Account)
        .filter_by(is_active=True, is_postable=True)
        .order_by(Account.code)
        .all()
    )
    # حل الحسابات بالأكواد
    by_code = {a.code: a for a in db.session.query(Account).all()}
    prefilled = []
    for ln in tpl["lines"]:
        acc = by_code.get(ln["account_code"])
        if acc is None:
            continue
        prefilled.append({
            "account_id": acc.id,
            "account_label": f"{acc.code} - {acc.name_ar}",
            "debit_readonly": False,
            "credit_readonly": False,
            "side": ln["side"],
            "memo": ln["label"],
        })
    return render_template("journal/create.html",
                           accounts=accounts,
                           template=tpl,
                           prefilled=prefilled)
