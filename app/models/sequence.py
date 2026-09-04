"""ترقيم المستندات المتسلسل مع دعم التصفير السنوي وأمان التزامن.

كل صف = (doc_type, year). عند طلب رقم جديد نستخدم SELECT ... FOR UPDATE
لضمان عدم تكرار الرقم عند إنشاء عمليتين في نفس اللحظة.

الاستخدام عبر: `app.services.numbering.next_document_number(doc_type)`.
"""
from __future__ import annotations

from sqlalchemy import Column, Integer, String, UniqueConstraint

from app.extensions import db
from app.models.base import TimestampMixin


class NumberSequence(db.Model, TimestampMixin):
    __tablename__ = "number_sequences"
    __table_args__ = (
        UniqueConstraint("doc_type", "year", name="uq_seq_type_year"),
    )

    id = Column(Integer, primary_key=True)
    doc_type = Column(String(40), nullable=False, index=True)  # sales_invoice, journal_entry, ...
    year = Column(Integer, nullable=False)  # 2026
    last_number = Column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        return f"<Sequence {self.doc_type}/{self.year}: {self.last_number}>"
