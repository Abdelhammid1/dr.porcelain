"""Routes للعملاء والموردين — نفس الكود بتغيير `type` عبر الـ URL."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from app.blueprints.parties import parties_bp
from app.blueprints.parties.forms import PartyForm
from app.extensions import db
from app.models.party import Party, PartyType
from app.services.parties import (
    PartyError,
    can_delete_party,
    create_party,
    delete_party,
    party_statement,
    update_party,
)
from app.services.security import require_permission


# ---------- Helpers ----------

def _resolve_type(type_str: str) -> PartyType:
    """يحوّل customer/vendor في الـ URL إلى PartyType."""
    try:
        return PartyType(type_str)
    except ValueError:
        abort(404)


def _type_labels(t: PartyType) -> dict[str, str]:
    if t == PartyType.CUSTOMER:
        return {"singular": "عميل", "plural": "العملاء", "new": "عميل جديد"}
    return {"singular": "مورد", "plural": "الموردون", "new": "مورد جديد"}


# ============= 1) قائمة الأطراف =============

@parties_bp.route("/<type_str>/", methods=["GET"])
@login_required
@require_permission("parties.view")
def index(type_str):
    party_type = _resolve_type(type_str)
    labels = _type_labels(party_type)
    q = (request.args.get("q") or "").strip()

    query = db.session.query(Party).filter_by(type=party_type)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Party.name_ar.ilike(like),
            Party.phone.ilike(like),
            Party.code.ilike(like),
        ))
    parties = query.order_by(Party.name_ar).all()

    return render_template(
        "parties/index.html",
        parties=parties,
        party_type=party_type,
        labels=labels,
        q=q,
    )


# ============= 2) إنشاء طرف =============

@parties_bp.route("/<type_str>/new", methods=["GET", "POST"])
@login_required
@require_permission("parties.manage")
def create(type_str):
    party_type = _resolve_type(type_str)
    labels = _type_labels(party_type)
    form = PartyForm()

    if form.validate_on_submit():
        try:
            party = create_party(
                type=party_type,
                name_ar=form.name_ar.data,
                phone=form.phone.data,
                email=form.email.data,
                address=form.address.data,
                tax_number=form.tax_number.data,
                notes=form.notes.data,
            )
            db.session.commit()
            flash(f"تم إنشاء {labels['singular']} برقم {party.code} وفتح حساب فرعي {party.account.code}.", "success")
            return redirect(url_for("parties.view", type_str=party_type.value, party_id=party.id))
        except PartyError as e:
            db.session.rollback()
            flash(str(e), "danger")

    return render_template(
        "parties/form.html",
        form=form,
        party=None,
        party_type=party_type,
        labels=labels,
        mode="create",
    )


# ============= 3) عرض/تعديل طرف =============

@parties_bp.route("/<type_str>/<int:party_id>", methods=["GET"])
@login_required
@require_permission("parties.view")
def view(type_str, party_id):
    party_type = _resolve_type(type_str)
    party = _get_or_404(party_id, party_type)
    labels = _type_labels(party_type)

    balance = party.account.compute_balance() if party.account else 0
    return render_template(
        "parties/view.html",
        party=party,
        party_type=party_type,
        labels=labels,
        balance=balance,
    )


@parties_bp.route("/<type_str>/<int:party_id>/edit", methods=["GET", "POST"])
@login_required
@require_permission("parties.manage")
def edit(type_str, party_id):
    party_type = _resolve_type(type_str)
    party = _get_or_404(party_id, party_type)
    labels = _type_labels(party_type)

    form = PartyForm(obj=party)
    if form.validate_on_submit():
        try:
            update_party(
                party.id,
                name_ar=form.name_ar.data,
                phone=form.phone.data,
                email=form.email.data,
                address=form.address.data,
                tax_number=form.tax_number.data,
                notes=form.notes.data,
                is_active=form.is_active.data,
            )
            db.session.commit()
            flash("تم حفظ التعديلات.", "success")
            return redirect(url_for("parties.view", type_str=party_type.value, party_id=party.id))
        except PartyError as e:
            db.session.rollback()
            flash(str(e), "danger")

    return render_template(
        "parties/form.html",
        form=form,
        party=party,
        party_type=party_type,
        labels=labels,
        mode="edit",
    )


@parties_bp.route("/<type_str>/<int:party_id>/delete", methods=["POST"])
@login_required
@require_permission("parties.manage")
def delete(type_str, party_id):
    party_type = _resolve_type(type_str)
    party = _get_or_404(party_id, party_type)

    try:
        delete_party(party.id)
        db.session.commit()
        flash(f"تم حذف الطرف نهائيًا.", "success")
    except PartyError as e:
        db.session.rollback()
        flash(str(e), "danger")
        return redirect(url_for("parties.view", type_str=party_type.value, party_id=party.id))

    return redirect(url_for("parties.index", type_str=party_type.value))


# ============= 4) كشف الحساب =============

@parties_bp.route("/<type_str>/<int:party_id>/statement", methods=["GET"])
@login_required
@require_permission("parties.view")
def statement(type_str, party_id):
    party_type = _resolve_type(type_str)
    party = _get_or_404(party_id, party_type)
    labels = _type_labels(party_type)

    date_from = _parse_date(request.args.get("from"))
    date_to = _parse_date(request.args.get("to"))

    stmt = party_statement(party.id, date_from=date_from, date_to=date_to)
    return render_template(
        "parties/statement.html",
        party=party,
        party_type=party_type,
        labels=labels,
        stmt=stmt,
        date_from=date_from,
        date_to=date_to,
    )


# ============= 5) API — بحث سريع (للاستخدام في فورمات الفواتير لاحقًا) =============

@parties_bp.route("/api/search")
@login_required
@require_permission("parties.view")
def api_search():
    type_str = request.args.get("type")
    q = (request.args.get("q") or "").strip()
    if not type_str:
        return jsonify([])
    try:
        party_type = PartyType(type_str)
    except ValueError:
        return jsonify([])

    query = db.session.query(Party).filter_by(type=party_type, is_active=True)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Party.name_ar.ilike(like),
            Party.phone.ilike(like),
            Party.code.ilike(like),
        ))
    results = query.order_by(Party.name_ar).limit(20).all()
    return jsonify([
        {"id": p.id, "code": p.code, "name_ar": p.name_ar, "phone": p.phone or ""}
        for p in results
    ])


# ---------- Helpers ----------

def _get_or_404(party_id: int, expected_type: PartyType) -> Party:
    party = db.session.get(Party, party_id)
    if party is None or party.type != expected_type:
        abort(404)
    return party


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
