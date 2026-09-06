"""خدمة استرجاع كلمة المرور (Ticket 4 Epic 5).

سياسات أمنية:
- unified success message من جانب الـ route (لا نكشف وجود إيميل).
- token عشوائي 32 بايت (secrets.token_urlsafe).
- صلاحية 30 دقيقة افتراضيًا.
- استخدام واحد فقط — بعد الاستخدام يُعلَّم used_at.
- عند التوليد نُبطل أي token سابق غير مُستخدَم لنفس الحساب (منع تراكم).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models.party import Party, PartyType
from app.models.password_reset import PasswordResetToken
from app.models.user import User


TOKEN_TTL_MINUTES = 30


class TokenError(ValueError):
    pass


def _generate(user_id: int | None = None,
              customer_id: int | None = None) -> PasswordResetToken:
    """ينشئ token جديد ويبطل أي سابق. الأدمن أو العميل — واحد فقط."""
    assert (user_id is None) != (customer_id is None), "user_id XOR customer_id"

    # أبطل السابق (نضع used_at=الآن حتى لا يُعاد استخدامه)
    now = datetime.now(timezone.utc)
    q = db.session.query(PasswordResetToken).filter(
        PasswordResetToken.used_at.is_(None)
    )
    if user_id is not None:
        q = q.filter(PasswordResetToken.user_id == user_id)
    else:
        q = q.filter(PasswordResetToken.customer_id == customer_id)
    q.update({"used_at": now}, synchronize_session=False)

    tok = PasswordResetToken(
        user_id=user_id,
        customer_id=customer_id,
        token=secrets.token_urlsafe(32),
        expires_at=now + timedelta(minutes=TOKEN_TTL_MINUTES),
    )
    db.session.add(tok)
    db.session.flush()
    return tok


def request_admin_reset(email: str) -> PasswordResetToken | None:
    """يبدأ عملية استرجاع لأدمن — يرجع token لو الإيميل موجود، وإلا None."""
    email = (email or "").strip().lower()
    if not email:
        return None
    user = (
        db.session.query(User)
        .filter(db.func.lower(User.email) == email, User.is_active == True)  # noqa: E712
        .first()
    )
    if user is None:
        return None
    return _generate(user_id=user.id)


def request_customer_reset(phone_or_email: str) -> PasswordResetToken | None:
    """يبدأ عملية استرجاع لعميل — يقبل هاتف أو إيميل."""
    val = (phone_or_email or "").strip()
    if not val:
        return None
    q = db.session.query(Party).filter(
        Party.type == PartyType.CUSTOMER,
        Party.is_active == True,  # noqa: E712
    )
    if "@" in val:
        party = q.filter(db.func.lower(Party.email) == val.lower()).first()
    else:
        party = q.filter(Party.phone == val).first()
    if party is None:
        return None
    return _generate(customer_id=party.id)


def _get_active(token_str: str) -> PasswordResetToken:
    tok = db.session.query(PasswordResetToken).filter_by(token=token_str).first()
    if tok is None:
        raise TokenError("رابط غير صالح.")
    if tok.used_at is not None:
        raise TokenError("سبق استخدام هذا الرابط.")
    exp = tok.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > exp:
        raise TokenError("انتهت صلاحية الرابط.")
    return tok


def validate_token(token_str: str) -> PasswordResetToken:
    return _get_active(token_str)


def consume_and_set_password(token_str: str, new_password: str) -> None:
    tok = _get_active(token_str)
    if not new_password or len(new_password) < 6:
        raise TokenError("كلمة المرور يجب أن تكون 6 أحرف على الأقل.")

    if tok.user_id is not None:
        user = db.session.get(User, tok.user_id)
        if user is None:
            raise TokenError("الحساب غير موجود.")
        user.set_password(new_password)
    elif tok.customer_id is not None:
        party = db.session.get(Party, tok.customer_id)
        if party is None:
            raise TokenError("الحساب غير موجود.")
        party.set_password(new_password)
    else:
        raise TokenError("token غير صالح البنية.")

    tok.used_at = datetime.now(timezone.utc)
    db.session.flush()
