"""نموذج رمز استرجاع كلمة المرور (Ticket 4 Epic 5).

- يُستخدَم لكل من الأدمن (user_id) والعميل (customer_id) — واحد منهما فقط.
- token عشوائي آمن (secrets.token_urlsafe) — unique.
- صلاحية قصيرة (30 دقيقة افتراضيًا).
- استخدام واحد فقط — بعد الاستخدام يُعلَّم used_at ولا يُقبَل مجددًا.
"""
from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.extensions import db
from app.models.base import TimestampMixin


class PasswordResetToken(db.Model, TimestampMixin):
    __tablename__ = "password_reset_tokens"
    __table_args__ = (
        CheckConstraint(
            "(user_id IS NOT NULL AND customer_id IS NULL) OR "
            "(user_id IS NULL AND customer_id IS NOT NULL)",
            name="ck_reset_token_one_of",
        ),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=True, index=True)
    customer_id = Column(Integer, ForeignKey("parties.id", ondelete="CASCADE"),
                         nullable=True, index=True)
    token = Column(String(120), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User")
    customer = relationship("Party")

    def __repr__(self) -> str:
        subject = f"user={self.user_id}" if self.user_id else f"customer={self.customer_id}"
        return f"<PasswordResetToken {subject} used={self.used_at is not None}>"
