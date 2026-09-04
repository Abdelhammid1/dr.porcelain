"""نموذج ورديات نقطة البيع (POS Sessions).

كل كاشير يفتح وردية جديدة عند بدء البيع، ويقفلها في نهاية الشيفت. النظام يتتبع:
- الرصيد النقدي الافتتاحي (opening_cash) — عهدة الكاشير في بداية الوردية
- كل المبيعات النقدية خلال الوردية (لحساب closing_cash_expected)
- الرصيد النقدي الفعلي في الدرج عند القفل (closing_cash_actual)
- الفرق (إن وُجد) يُقيَّد تلقائيًا على 6500 مقابل 1030-XXX

كل كاشير له حساب فرعي تلقائي تحت 1030 (عهدة الكاشير) بكود مثل 1030-U-000001.
"""
from __future__ import annotations

import enum
from decimal import Decimal

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.extensions import db
from app.models.base import TimestampMixin


MONEY = Numeric(18, 3)


class SessionStatus(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"


class POSSession(db.Model, TimestampMixin):
    __tablename__ = "pos_sessions"
    __table_args__ = (
        # لا يمكن أن يكون للكاشير أكثر من وردية مفتوحة في نفس الوقت
        db.Index("ix_pos_sessions_cashier_status", "cashier_id", "status"),
    )

    id = Column(Integer, primary_key=True)
    doc_number = Column(String(40), unique=True, nullable=False, index=True)  # POS-2026-000001

    cashier_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    custody_account_id = Column(Integer, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False)

    status = Column(
        Enum(SessionStatus, name="pos_session_status",
             values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=SessionStatus.OPEN, index=True,
    )

    # النقدية
    opening_cash = Column(MONEY, nullable=False, default=0)
    closing_cash_expected = Column(MONEY, nullable=True)  # يُحسب تلقائيًا وقت القفل
    closing_cash_actual = Column(MONEY, nullable=True)    # يُدخله الكاشير
    difference = Column(MONEY, nullable=True)             # actual - expected (سالب = عجز، موجب = زيادة)

    opened_at = Column(DateTime(timezone=True), nullable=False)
    closed_at = Column(DateTime(timezone=True), nullable=True)

    notes_open = Column(Text, nullable=True)
    notes_close = Column(Text, nullable=True)

    cashier = relationship("User", foreign_keys=[cashier_id])
    custody_account = relationship("Account", foreign_keys=[custody_account_id])

    # فواتير مباعة في هذه الوردية (تربط من SalesInvoice.pos_session_id)
    sales = relationship(
        "SalesInvoice",
        primaryjoin="SalesInvoice.pos_session_id == POSSession.id",
        foreign_keys="SalesInvoice.pos_session_id",
        viewonly=True,
        order_by="SalesInvoice.id",
    )

    @property
    def is_open(self) -> bool:
        return self.status == SessionStatus.OPEN

    def __repr__(self) -> str:
        return f"<POSSession {self.doc_number} cashier={self.cashier_id} {self.status.value}>"
