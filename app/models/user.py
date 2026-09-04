"""نموذج المستخدم — مرتبط بالأدوار عبر Many-to-One (مستخدم = دور واحد أساسي).

ملحوظة: نحتفظ بتصميم بسيط الآن (دور واحد لكل مستخدم). لو المالك احتاج أدوارًا
متعددة لنفس المستخدم لاحقًا، نغيّرها لعلاقة Many-to-Many بجدول users_roles.
"""
from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from werkzeug.security import check_password_hash, generate_password_hash
from flask_login import UserMixin

from app.extensions import db
from app.models.base import TimestampMixin


class User(db.Model, TimestampMixin, UserMixin):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    full_name = Column(String(160), nullable=False)
    email = Column(String(160), unique=True, nullable=True)
    phone = Column(String(32), nullable=True)
    password_hash = Column(String(255), nullable=False)

    role_id = Column(Integer, ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False)
    role = relationship("Role", lazy="joined")

    is_active = Column(Boolean, nullable=False, default=True)
    last_login_at = Column(DateTime(timezone=True), nullable=True)

    # -------- كلمة المرور --------
    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

    # -------- الصلاحيات --------
    def can(self, permission_code: str) -> bool:
        if not self.is_active:
            return False
        # مالك النظام له كل الصلاحيات دومًا
        if self.role and self.role.code == "owner":
            return True
        return bool(self.role and self.role.has_permission(permission_code))

    @property
    def is_owner(self) -> bool:
        return bool(self.role and self.role.code == "owner")

    def __repr__(self) -> str:
        return f"<User {self.username}>"
