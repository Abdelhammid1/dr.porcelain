"""نموذج القيد اليومي (Journal Entry) وأسطره (Journal Lines).

قواعد صارمة:
- كل قيد يحتوي على سطرين على الأقل.
- مجموع المدين = مجموع الدائن (يُفرض في `app.services.ledger`).
- كل قيد مرتبط بمصدر: source_type + source_id (فاتورة بيع، شراء، تحصيل، يدوي…).
- القيد لا يُحذف — يُعكَس بقيد جديد يُنشِئ الحركة المعاكسة ويُشير للأصلي.
- كل قيد له حالة: DRAFT (نادرة، للتحضير) / POSTED (مرحّل نافذ) / REVERSED (معكوس).
"""
from __future__ import annotations

import enum
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.extensions import db
from app.models.base import TimestampMixin


# 3 خانات عشرية (Config.MONEY_DECIMAL_PLACES)
# نستخدم Numeric(18, 3) لدعم مبالغ حتى 999,999,999,999,999.999
MONEY_TYPE = Numeric(18, 3)


class JournalEntryStatus(str, enum.Enum):
    DRAFT = "draft"
    POSTED = "posted"
    REVERSED = "reversed"


class JournalSourceType(str, enum.Enum):
    """أنواع المصادر المسموح بها للقيد.

    استخدام Enum مقصود — يمنع أي مطوّر لاحقًا من إنشاء قيد بدون تصنيف مصدره.
    """

    MANUAL = "manual"                      # قيد يدوي من المحاسب
    SALES_INVOICE = "sales_invoice"        # فاتورة بيع
    SALES_RETURN = "sales_return"          # مرتجع بيع
    PURCHASE_INVOICE = "purchase_invoice"  # فاتورة شراء
    PURCHASE_RETURN = "purchase_return"    # مرتجع شراء
    CUSTOMER_RECEIPT = "customer_receipt"  # تحصيل من عميل
    VENDOR_PAYMENT = "vendor_payment"      # سداد لمورد
    INSTALLMENT = "installment"            # سداد قسط
    POS_SESSION = "pos_session"            # قيد فروقات صندوق POS
    INVENTORY_ADJ = "inventory_adjustment" # تسوية مخزون
    REVERSAL = "reversal"                  # قيد عكس لقيد سابق
    OPENING = "opening"                    # أرصدة افتتاحية


class JournalEntry(db.Model, TimestampMixin):
    __tablename__ = "journal_entries"

    id = Column(Integer, primary_key=True)
    doc_number = Column(String(40), unique=True, nullable=False, index=True)  # JE-2026-000001
    entry_date = Column(Date, nullable=False, index=True)

    memo = Column(String(500), nullable=True)  # ملاحظة/بيان القيد

    source_type = Column(
        Enum(JournalSourceType, name="journal_source_type",
             values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        index=True,
    )
    # source_id يُشير للسجل الأصلي (فاتورة، تحصيل…). لا نضع FK لأنه polymorphic.
    source_id = Column(Integer, nullable=True, index=True)

    status = Column(
        Enum(JournalEntryStatus, name="journal_entry_status",
             values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=JournalEntryStatus.POSTED,
        index=True,
    )

    # --- عكس القيد ---
    # لو هذا قيد عكس، يُشير للقيد الأصلي المعكوس.
    reversal_of_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True, index=True)
    # لو تم عكس هذا القيد، يُشير للقيد المعاكس المُنشَأ.
    reversed_by_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True, index=True)
    reversal_reason = Column(Text, nullable=True)
    reversed_at = Column(DateTime(timezone=True), nullable=True)

    # --- تتبع المستخدم ---
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    reversed_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # --- علاقات ---
    lines = relationship(
        "JournalLine",
        back_populates="entry",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    reversal_of = relationship(
        "JournalEntry",
        remote_side="JournalEntry.id",
        foreign_keys=[reversal_of_id],
        post_update=True,
    )
    created_by = relationship("User", foreign_keys=[created_by_id])
    reversed_by_user = relationship("User", foreign_keys=[reversed_by_user_id])

    # --- helpers ---

    @property
    def total_debit(self) -> Decimal:
        return sum((line.debit for line in self.lines), Decimal("0"))

    @property
    def total_credit(self) -> Decimal:
        return sum((line.credit for line in self.lines), Decimal("0"))

    @property
    def is_balanced(self) -> bool:
        return self.total_debit == self.total_credit

    def __repr__(self) -> str:
        return f"<JournalEntry {self.doc_number} {self.status.value}>"


class JournalLine(db.Model):
    __tablename__ = "journal_lines"
    __table_args__ = (
        # كل سطر إما مدين موجب ودائن صفر، أو العكس. لا يُسمح بجانبين موجبين.
        CheckConstraint(
            "(debit >= 0 AND credit >= 0) AND (debit = 0 OR credit = 0) AND (debit > 0 OR credit > 0)",
            name="ck_journal_line_side",
        ),
        db.Index("ix_journal_lines_account_entry", "account_id", "entry_id"),
    )

    id = Column(Integer, primary_key=True)
    entry_id = Column(
        Integer,
        ForeignKey("journal_entries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    account_id = Column(
        Integer,
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    debit = Column(MONEY_TYPE, nullable=False, default=0)
    credit = Column(MONEY_TYPE, nullable=False, default=0)
    memo = Column(String(300), nullable=True)

    # --- علاقات ---
    entry = relationship("JournalEntry", back_populates="lines")
    account = relationship("Account")

    def __repr__(self) -> str:
        side = f"D={self.debit}" if self.debit else f"C={self.credit}"
        return f"<JournalLine acc={self.account_id} {side}>"
