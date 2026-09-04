"""مصادقة عميل المتجر (منفصلة عن Flask-Login للأدمن).

نستخدم Flask session تحت المفتاح `customer_id` لتتبّع تسجيل الدخول.
تسجيل الدخول بالهاتف + كلمة المرور (كلمة المرور تُخزَّن مع Party.password_hash).

- register: يخلق حسابًا جديدًا للعميل — إن كان هناك Party بنفس الهاتف
  (أُنشئ سابقًا من الأدمن أو من طلب سابق) نُحدّث password_hash فقط بدل الإنشاء الجديد.
- login: يتحقق من الرقم + كلمة السر.
- logout: يمحو المفتاح من الجلسة.
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import wraps
from typing import Optional

from flask import flash, redirect, session, url_for
from werkzeug.wrappers import Response

from app.extensions import db
from app.models.account import Account, AccountType
from app.models.party import Party, PartyType
from app.services.parties import _next_party_code  # نُعيد استخدامها لتوليد كود العميل


SESSION_KEY = "customer_id"


class CustomerAuthError(ValueError):
    pass


# ============ Session helpers ============

def current_customer() -> Optional[Party]:
    """يُرجِع كائن العميل الحالي أو None لو لم يسجل الدخول."""
    cid = session.get(SESSION_KEY)
    if not cid:
        return None
    party = db.session.get(Party, cid)
    if party is None or party.type != PartyType.CUSTOMER or not party.is_active:
        session.pop(SESSION_KEY, None)
        return None
    return party


def _login(party: Party) -> None:
    session[SESSION_KEY] = party.id
    session.modified = True
    party.last_portal_login_at = datetime.now(timezone.utc)


def logout() -> None:
    session.pop(SESSION_KEY, None)
    session.modified = True


# ============ Register + login ============

def register_customer(*, name_ar: str, phone: str, password: str,
                      email: str | None = None) -> Party:
    """يُنشئ حساب عميل جديد أو يُفعّل حسابًا موجودًا بنفس الهاتف.

    - لو Party.customer موجود بنفس phone: نحدّث الاسم والبريد ونضبط password.
      (يفيد لو العميل أنشئ من الأدمن أو من طلب ضيف سابق.)
    - وإلا نُنشئ Party جديد + حساب فرعي.
    """
    name_ar = (name_ar or "").strip()
    phone = (phone or "").strip()
    password = password or ""

    if not name_ar:
        raise CustomerAuthError("الاسم مطلوب.")
    if not phone:
        raise CustomerAuthError("رقم الهاتف مطلوب.")
    if len(password) < 6:
        raise CustomerAuthError("كلمة المرور يجب أن تكون 6 أحرف على الأقل.")

    existing = (
        db.session.query(Party)
        .filter_by(type=PartyType.CUSTOMER, phone=phone)
        .first()
    )
    if existing is not None:
        if existing.has_portal_account:
            raise CustomerAuthError(
                "يوجد حساب مسبق بهذا الرقم — سجّل الدخول بدلاً من إنشاء حساب."
            )
        # نُنشِّط حسابه بتحديد كلمة السر
        if name_ar and existing.name_ar != name_ar:
            existing.name_ar = name_ar
        if email and not existing.email:
            existing.email = email.strip()
        if not existing.is_active:
            existing.is_active = True
        existing.set_password(password)
        db.session.flush()
        return existing

    # عميل جديد تمامًا — نُنشِئ Party + حساب فرعي (نفس منطق services.parties)
    code = _next_party_code(PartyType.CUSTOMER)
    party = Party(
        type=PartyType.CUSTOMER,
        code=code,
        name_ar=name_ar,
        phone=phone,
        email=(email or None),
        is_active=True,
    )
    party.set_password(password)
    db.session.add(party)
    db.session.flush()

    # فتح حساب فرعي تحت 1200
    parent = db.session.query(Account).filter_by(code="1200").one_or_none()
    if parent is None:
        raise CustomerAuthError("الحساب الأب 1200 غير موجود — شغّل init-coa.")
    account = Account(
        code=f"1200-{party.code}",
        name_ar=f"{party.name_ar} ({party.code})",
        type=parent.type,
        parent_id=parent.id,
        party_id=party.id,
        is_postable=True,
        is_system=False,
        is_active=True,
    )
    db.session.add(account)
    db.session.flush()
    return party


def login_customer(*, phone: str, password: str) -> Party:
    """يتحقق من بيانات الدخول ويُسجِّل العميل في الجلسة."""
    phone = (phone or "").strip()
    if not phone or not password:
        raise CustomerAuthError("الهاتف وكلمة المرور مطلوبان.")

    party = (
        db.session.query(Party)
        .filter_by(type=PartyType.CUSTOMER, phone=phone, is_active=True)
        .first()
    )
    if party is None or not party.check_password(password):
        raise CustomerAuthError("بيانات الدخول غير صحيحة.")

    _login(party)
    return party


# ============ Decorator ============

def customer_required(view):
    """يمنع الوصول لو العميل غير مسجّل الدخول (يوجّه لصفحة الدخول)."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_customer() is None:
            flash("سجّل الدخول للوصول لهذه الصفحة.", "warning")
            return redirect(url_for("storefront.account_login"))
        return view(*args, **kwargs)
    return wrapped
