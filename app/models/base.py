"""Mixins مشتركة للـ Models."""
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer
from sqlalchemy.sql import func

from app.extensions import db


class TimestampMixin:
    """يضيف حقول created_at و updated_at لأي Model."""

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AuditMixin(TimestampMixin):
    """يضيف تتبع أي مستخدم أنشأ/عدّل السجل."""

    @classmethod
    def _audit_columns(cls):
        return {
            "created_by_id": Column(Integer, db.ForeignKey("users.id"), nullable=True),
            "updated_by_id": Column(Integer, db.ForeignKey("users.id"), nullable=True),
        }
