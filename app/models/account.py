"""نموذج دليل الحسابات (Chart of Accounts).

القواعد:
- كل حساب له `code` فريد نصي (يسمح بأكواد مثل 1010, 1020-001, 1200-CUST-000123)
- `type` نوع الحساب المحاسبي (أصول/التزامات/حقوق ملكية/إيرادات/تكلفة/مصروفات)
  ولا يتغير بعد الإنشاء.
- الحسابات هرمية عبر `parent_id`. الحسابات الفرعية تحمل أرصدة فعلية،
  والحسابات الأب (Control Accounts) تُجمَّع تلقائيًا في التقارير.
- الحسابات المرتبطة بأطراف (عملاء/موردين) تحمل `party_id` وتُنشَأ تلقائيًا.
- كل حساب `is_system=True` لا يُحذف من الواجهة.
- الرصيد لا يُخزَّن هنا — يُحسَب من `journal_lines` عند الطلب أو يُخزَّن كـ cache
  في جدول مستقل لاحقًا لو احتجنا سرعة.
"""
from __future__ import annotations

import enum
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Column,
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


class AccountType(str, enum.Enum):
    """أنواع الحسابات — التسمية الإنجليزية للكود والعربية للعرض."""

    ASSET = "asset"           # أصل
    LIABILITY = "liability"   # التزام
    EQUITY = "equity"         # حق ملكية
    REVENUE = "revenue"       # إيراد
    COST = "cost"             # تكلفة (تكلفة البضاعة المباعة)
    EXPENSE = "expense"       # مصروف

    @property
    def label_ar(self) -> str:
        return ACCOUNT_TYPE_LABELS[self]

    @property
    def normal_side(self) -> str:
        """الجانب الطبيعي لرصيد الحساب (debit/credit)."""
        return "debit" if self in (AccountType.ASSET, AccountType.COST, AccountType.EXPENSE) else "credit"


ACCOUNT_TYPE_LABELS = {
    AccountType.ASSET: "أصول",
    AccountType.LIABILITY: "التزامات",
    AccountType.EQUITY: "حقوق ملكية",
    AccountType.REVENUE: "إيرادات",
    AccountType.COST: "تكلفة",
    AccountType.EXPENSE: "مصروفات",
}


class Account(db.Model, TimestampMixin):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True)
    code = Column(String(40), unique=True, nullable=False, index=True)
    name_ar = Column(String(160), nullable=False)
    type = Column(
        Enum(AccountType, name="account_type", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        index=True,
    )
    parent_id = Column(
        Integer,
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    # لو الحساب الفرعي مرتبط بطرف (عميل/مورد) نحفظ الربط هنا.
    # party_id يشير لـ parties.id ولكن نتركه كـ Integer الآن ونضيف FK لاحقًا لتجنب دائرة استيراد.
    party_id = Column(Integer, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=True, index=True)

    # حسابات النظام (من دليل الحسابات الافتراضي) لا يُسمح بحذفها من الواجهة.
    is_system = Column(Boolean, nullable=False, default=False)
    # هل يُقبل استخدام هذا الحساب في القيود؟ الحسابات الأب المجمّعة عادةً is_postable=False.
    is_postable = Column(Boolean, nullable=False, default=True)
    # هل الحساب نشط؟ إلغاء التنشيط يمنع اختياره في القيود الجديدة دون حذف السجل.
    is_active = Column(Boolean, nullable=False, default=True)

    description = Column(Text, nullable=True)

    # --- علاقات ---
    parent = relationship("Account", remote_side="Account.id", backref="children")

    __table_args__ = (
        db.Index("ix_accounts_type_active", "type", "is_active"),
    )

    # --- helpers ---

    @property
    def is_control(self) -> bool:
        """حساب أب (Control) = عنده أبناء."""
        return len(self.children) > 0

    @property
    def full_path(self) -> str:
        """مسار الحساب من الجذر: `1200 - ذمم عملاء / 1200-001 - أحمد محمد`."""
        parts = []
        node = self
        while node is not None:
            parts.append(f"{node.code} - {node.name_ar}")
            node = node.parent
        return " / ".join(reversed(parts))

    def compute_balance(self, as_of=None) -> Decimal:
        """يحسب رصيد الحساب من القيود المرحّلة (posted) فقط.

        as_of: تاريخ اختياري. الرصيد بالجانب الطبيعي للحساب:
        - أصول/تكلفة/مصروفات: مدين - دائن
        - التزامات/حقوق ملكية/إيرادات: دائن - مدين
        """
        from app.models.journal import JournalEntry, JournalEntryStatus, JournalLine

        q = (
            db.session.query(
                db.func.coalesce(db.func.sum(JournalLine.debit), 0).label("d"),
                db.func.coalesce(db.func.sum(JournalLine.credit), 0).label("c"),
            )
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .filter(JournalLine.account_id == self.id)
            .filter(JournalEntry.status.in_([JournalEntryStatus.POSTED, JournalEntryStatus.REVERSED]))
        )
        if as_of is not None:
            q = q.filter(JournalEntry.entry_date <= as_of)
        row = q.one()
        d = Decimal(row.d or 0)
        c = Decimal(row.c or 0)
        return (d - c) if self.type.normal_side == "debit" else (c - d)

    def __repr__(self) -> str:
        return f"<Account {self.code} {self.name_ar}>"
