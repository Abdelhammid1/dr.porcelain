"""Notification model (Ticket 4 Epic 2).

نموذج عام (generic) قابل للاستخدام لأي نوع من الأحداث:
- new_order، low_stock، overdue_installment، order_cancelled، إلخ.

`user_id` nullable → لو null الإشعار عام لكل الأدمن؛ لو مُعيَّن فقط لهذا المستخدم.
"""
from __future__ import annotations

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.extensions import db
from app.models.base import TimestampMixin


class Notification(db.Model, TimestampMixin):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=True, index=True)  # None = لكل الأدمن
    title = Column(String(240), nullable=False)
    body = Column(Text, nullable=True)
    link = Column(String(500), nullable=True)
    notification_type = Column(String(60), nullable=False, index=True,
                                default="general")
    is_read = Column(Boolean, nullable=False, default=False, index=True)

    user = relationship("User")

    def __repr__(self) -> str:
        return f"<Notification {self.id} {self.notification_type}>"
