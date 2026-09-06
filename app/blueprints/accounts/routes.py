"""Admin routes لدليل الحسابات (Ticket 3 Epic 3)."""
from __future__ import annotations

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import login_required

from app.blueprints.accounts import accounts_bp
from app.extensions import db
from app.models.account import Account, AccountType
from app.services.security import require_permission


@accounts_bp.route("/", methods=["GET"])
@login_required
@require_permission("accounts.view")
def index():
    # كل الحسابات — نبنيها كشجرة في القالب
    all_accounts = (
        db.session.query(Account)
        .order_by(Account.code)
        .all()
    )
    # الرصيد لكل postable
    balances = {}
    for a in all_accounts:
        if a.is_postable:
            try:
                balances[a.id] = a.compute_balance()
            except Exception:
                balances[a.id] = None
    roots = [a for a in all_accounts if a.parent_id is None]
    return render_template("accounts/index.html",
                           roots=roots, all_accounts=all_accounts,
                           balances=balances,
                           account_types=list(AccountType))


@accounts_bp.route("/new", methods=["POST"])
@login_required
@require_permission("accounts.manage")
def create():
    code = (request.form.get("code") or "").strip()
    name_ar = (request.form.get("name_ar") or "").strip()
    parent_id = request.form.get("parent_id", type=int)
    is_postable = "is_postable" in request.form

    if not code or not name_ar or not parent_id:
        flash("الكود والاسم والحساب الأب مطلوبون.", "danger")
        return redirect(url_for("accounts.index"))
    parent = db.session.get(Account, parent_id)
    if parent is None:
        flash("الحساب الأب غير موجود.", "danger")
        return redirect(url_for("accounts.index"))
    if db.session.query(Account).filter_by(code=code).first():
        flash(f"الكود {code} مستخدم.", "danger")
        return redirect(url_for("accounts.index"))

    a = Account(
        code=code, name_ar=name_ar, type=parent.type,
        parent_id=parent.id, is_postable=is_postable,
        is_system=False, is_active=True,
    )
    db.session.add(a)
    db.session.commit()
    flash("تم إنشاء الحساب.", "success")
    return redirect(url_for("accounts.index"))


@accounts_bp.route("/<int:account_id>/rename", methods=["POST"])
@login_required
@require_permission("accounts.manage")
def rename(account_id):
    a = db.session.get(Account, account_id) or abort(404)
    new_name = (request.form.get("name_ar") or "").strip()
    if new_name:
        a.name_ar = new_name
        db.session.commit()
        flash("تم التعديل.", "success")
    return redirect(url_for("accounts.index"))


@accounts_bp.route("/<int:account_id>/toggle", methods=["POST"])
@login_required
@require_permission("accounts.manage")
def toggle(account_id):
    a = db.session.get(Account, account_id) or abort(404)
    if a.is_system and a.is_active:
        # الحسابات النظامية يمكن تعطيلها لكن لا حذفها
        pass
    a.is_active = not a.is_active
    db.session.commit()
    flash(f"تم {'تفعيل' if a.is_active else 'تعطيل'} الحساب.", "info")
    return redirect(url_for("accounts.index"))
