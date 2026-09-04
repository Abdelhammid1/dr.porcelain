"""Decorators للتحكم في الصلاحيات على مستوى الـ Routes."""
from __future__ import annotations

from functools import wraps

from flask import abort
from flask_login import current_user


def require_permission(*codes: str):
    """يمنع الوصول للـ view إن لم يكن المستخدم يملك أيًا من الأكواد المطلوبة."""

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if not any(current_user.can(code) for code in codes):
                abort(403)
            return view(*args, **kwargs)

        return wrapper

    return decorator


def require_owner(view):
    """للعمليات الحساسة (إدارة الأدوار، حذف مستخدم…)."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)
        if not current_user.is_owner:
            abort(403)
        return view(*args, **kwargs)

    return wrapper
