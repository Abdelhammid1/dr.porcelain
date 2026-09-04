"""Core Ledger Service — النقطة الوحيدة لإنشاء أي قيد يومي في النظام.

قواعد صارمة يُفرَض التزامها هنا وقبل الوصول لقاعدة البيانات:
1. سطرين على الأقل.
2. مجموع المدين = مجموع الدائن بالضبط (Decimal، ليس float).
3. كل سطر إما مدين أو دائن (لا الاثنين معًا).
4. كل مبلغ > 0.
5. كل حساب موجود، نشط، ومسموح فيه الترحيل (is_postable=True).
6. القيد لا يُحذف أبدًا — يُعكَس بـ reverse_entry().

الاستخدام العادي:
    from app.services.ledger import post_journal_entry, LedgerLineDraft

    entry = post_journal_entry(
        entry_date=date.today(),
        source_type=JournalSourceType.SALES_INVOICE,
        source_id=invoice.id,
        memo="فاتورة بيع #INV-2026-000001",
        lines=[
            LedgerLineDraft(account_id=ar_account_id, debit=Decimal("1140.000"), credit=Decimal("0")),
            LedgerLineDraft(account_id=revenue_id,    debit=Decimal("0"),      credit=Decimal("1000.000")),
            LedgerLineDraft(account_id=vat_out_id,    debit=Decimal("0"),      credit=Decimal("140.000")),
        ],
        user_id=current_user.id,
    )

كل هذه العملية تُنفَّذ داخل session واحدة يستخدمها المستدعي داخل Transaction ذرية.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Sequence

from app.extensions import db
from app.models.account import Account
from app.models.journal import (
    JournalEntry,
    JournalEntryStatus,
    JournalLine,
    JournalSourceType,
)
from app.services.numbering import next_document_number


class LedgerError(ValueError):
    """أي انتهاك لقواعد الدفتر يُرفَع كـ LedgerError."""


@dataclass
class LedgerLineDraft:
    account_id: int
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    memo: str | None = None

    def __post_init__(self):
        # نُحوّل أي int/float/str إلى Decimal تلقائيًا لكن نمنع float الصريح
        # (المستدعي لازم يستخدم Decimal للأمان).
        if not isinstance(self.debit, Decimal):
            self.debit = Decimal(str(self.debit))
        if not isinstance(self.credit, Decimal):
            self.credit = Decimal(str(self.credit))


def post_journal_entry(
    *,
    entry_date: date,
    source_type: JournalSourceType,
    source_id: int | None,
    memo: str | None,
    lines: Sequence[LedgerLineDraft],
    user_id: int | None = None,
    doc_number: str | None = None,
) -> JournalEntry:
    """يُنشِئ ويُرحِّل قيدًا يوميًا. يرفع LedgerError عند أي انتهاك.

    ملاحظة: هذا الاستدعاء لا يعمل commit — الاستدعاء يجب أن يتم داخل
    ``with db.session.begin():`` أو نُدعى من داخل service آخر يتحكم في المعاملة.
    """

    _validate_lines(lines)

    # 1) نتحقّق من كل الحسابات قبل أي كتابة على قاعدة البيانات (تجنب autoflush يترك حالة جزئية).
    account_ids = [ld.account_id for ld in lines]
    accounts = {
        a.id: a
        for a in db.session.query(Account).filter(Account.id.in_(account_ids)).all()
    }
    missing = set(account_ids) - set(accounts.keys())
    if missing:
        raise LedgerError(f"حسابات غير موجودة: {sorted(missing)}")

    for idx, ld in enumerate(lines, start=1):
        acc = accounts[ld.account_id]
        if not acc.is_active:
            raise LedgerError(f"الحساب {acc.code} غير نشط ولا يُقبل ترحيل قيود عليه.")
        if not acc.is_postable:
            raise LedgerError(
                f"الحساب {acc.code} حساب أب (Control) لا يُقبل الترحيل المباشر عليه."
            )

    # 2) كل الفحوصات نجحت — الآن نأخذ رقم المستند ونُنشِئ القيد وأسطره كاملةً.
    if doc_number is None:
        doc_number = next_document_number("journal_entry")

    entry = JournalEntry(
        doc_number=doc_number,
        entry_date=entry_date,
        memo=memo,
        source_type=source_type,
        source_id=source_id,
        status=JournalEntryStatus.POSTED,
        created_by_id=user_id,
    )
    db.session.add(entry)

    for ld in lines:
        entry.lines.append(
            JournalLine(
                account_id=ld.account_id,
                debit=ld.debit,
                credit=ld.credit,
                memo=ld.memo,
            )
        )

    db.session.flush()  # يفعّل CHECK constraints قبل الخروج من الـ service
    return entry


def reverse_entry(
    *,
    entry_id: int,
    reason: str,
    user_id: int | None,
    entry_date: date | None = None,
) -> JournalEntry:
    """يُنشِئ قيدًا معاكسًا للقيد المُشار إليه.

    - يُبقي القيد الأصلي في السجل (لا حذف).
    - يُعلِّم الأصلي بحالة REVERSED ويربطه بالقيد الجديد.
    - قيد العكس يأخذ رقم مستند جديد ومصدر REVERSAL.
    - يُطلَب سبب نصي إجباري.
    """
    reason = (reason or "").strip()
    if not reason:
        raise LedgerError("سبب العكس مطلوب.")

    original = db.session.get(JournalEntry, entry_id)
    if original is None:
        raise LedgerError(f"القيد {entry_id} غير موجود.")

    if original.status == JournalEntryStatus.REVERSED:
        raise LedgerError(f"القيد {original.doc_number} معكوس بالفعل.")
    if original.status != JournalEntryStatus.POSTED:
        raise LedgerError(f"لا يمكن عكس قيد بحالة {original.status.value}.")

    # نُنشِئ الأسطر المعاكسة (نبدّل مدين↔دائن)
    reversed_lines = [
        LedgerLineDraft(
            account_id=ln.account_id,
            debit=ln.credit,
            credit=ln.debit,
            memo=f"عكس: {ln.memo or ''}".strip(),
        )
        for ln in original.lines
    ]

    reversal = post_journal_entry(
        entry_date=entry_date or date.today(),
        source_type=JournalSourceType.REVERSAL,
        source_id=original.id,
        memo=f"عكس القيد {original.doc_number} — {reason}",
        lines=reversed_lines,
        user_id=user_id,
    )
    reversal.reversal_of_id = original.id

    original.status = JournalEntryStatus.REVERSED
    original.reversed_by_id = reversal.id
    original.reversal_reason = reason
    original.reversed_at = datetime.now(timezone.utc)
    original.reversed_by_user_id = user_id

    db.session.flush()
    return reversal


# ---------- فحوصات داخلية ----------

def _validate_lines(lines: Sequence[LedgerLineDraft]) -> None:
    if len(lines) < 2:
        raise LedgerError("القيد يحتاج سطرين على الأقل.")

    total_debit = Decimal("0")
    total_credit = Decimal("0")

    for idx, ld in enumerate(lines, start=1):
        if ld.debit < 0 or ld.credit < 0:
            raise LedgerError(f"سطر {idx}: مبالغ سالبة غير مسموحة.")
        if ld.debit > 0 and ld.credit > 0:
            raise LedgerError(
                f"سطر {idx}: لا يمكن أن يكون السطر مدينًا ودائنًا في نفس الوقت."
            )
        if ld.debit == 0 and ld.credit == 0:
            raise LedgerError(f"سطر {idx}: مبلغ صفر غير مسموح.")
        total_debit += ld.debit
        total_credit += ld.credit

    if total_debit != total_credit:
        raise LedgerError(
            f"القيد غير متوازن: مجموع المدين {total_debit} ≠ مجموع الدائن {total_credit}."
        )
