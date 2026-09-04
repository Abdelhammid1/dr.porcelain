"""نموذج الأدوار والصلاحيات — قابل للتعديل من الواجهة.

كل صلاحية = code فريد (مثل: "invoices.create"، "accounts.manage") + label عربي.
كل دور = مجموعة صلاحيات.
"""
from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.extensions import db
from app.models.base import TimestampMixin


class Permission(db.Model, TimestampMixin):
    __tablename__ = "permissions"

    id = Column(Integer, primary_key=True)
    code = Column(String(80), unique=True, nullable=False, index=True)
    label_ar = Column(String(160), nullable=False)
    group_ar = Column(String(80), nullable=False)  # لتجميع الصلاحيات في الواجهة
    description = Column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<Permission {self.code}>"


class Role(db.Model, TimestampMixin):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True)
    code = Column(String(40), unique=True, nullable=False, index=True)
    name_ar = Column(String(80), nullable=False)
    description = Column(Text, nullable=True)
    is_system = Column(db.Boolean, nullable=False, default=False)  # لا يُحذف/يعدَّل الكود

    permissions = relationship(
        "Permission",
        secondary="role_permissions",
        backref="roles",
        lazy="selectin",
    )

    def has_permission(self, code: str) -> bool:
        return any(p.code == code for p in self.permissions)

    def __repr__(self) -> str:
        return f"<Role {self.code}>"


class RolePermission(db.Model):
    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),
    )

    id = Column(Integer, primary_key=True)
    role_id = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)
    permission_id = Column(
        Integer, ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False
    )
