"""تقارير مالية أساسية — Trial Balance + كشوف الحسابات."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import func

from app.extensions import db
from app.models.account import Account, AccountType
from app.models.journal import JournalEntry, JournalEntryStatus, JournalLine


def trial_balance(
    *,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    include_zero_balance: bool = False,
) -> dict:
    """يُرجِع ميزان مراجعة لكل الحسابات القابلة للترحيل ضمن النطاق.

    الشكل:
        {
          "rows": [
              {"account": Account, "debit_movements", "credit_movements",
               "closing_debit", "closing_credit"}
          ],
          "totals": {"debit": Decimal, "credit": Decimal},
          "date_from": date, "date_to": date, "is_balanced": bool
        }
    """
    query = (
        db.session.query(
            JournalLine.account_id,
            func.coalesce(func.sum(JournalLine.debit), 0).label("total_debit"),
            func.coalesce(func.sum(JournalLine.credit), 0).label("total_credit"),
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .filter(JournalEntry.status == JournalEntryStatus.POSTED)
    )
    if date_from is not None:
        query = query.filter(JournalEntry.entry_date >= date_from)
    if date_to is not None:
        query = query.filter(JournalEntry.entry_date <= date_to)
    totals_by_account = {
        row.account_id: (Decimal(str(row.total_debit)), Decimal(str(row.total_credit)))
        for row in query.group_by(JournalLine.account_id).all()
    }

    # نأخذ فقط الحسابات القابلة للترحيل مرتبة بالكود
    accounts = (
        db.session.query(Account)
        .filter(Account.is_postable == True)  # noqa: E712
        .order_by(Account.code)
        .all()
    )

    rows = []
    total_debit = Decimal("0")
    total_credit = Decimal("0")

    for acc in accounts:
        debit_mv, credit_mv = totals_by_account.get(acc.id, (Decimal("0"), Decimal("0")))
        # الرصيد الختامي: بحسب الجانب الطبيعي
        balance = (debit_mv - credit_mv) if acc.type.normal_side == "debit" \
                  else (credit_mv - debit_mv)
        # نعرضه في العمود الطبيعي وإن كان سالبًا في العمود المعاكس
        closing_debit = Decimal("0")
        closing_credit = Decimal("0")
        if acc.type.normal_side == "debit":
            if balance >= 0:
                closing_debit = balance
            else:
                closing_credit = -balance
        else:
            if balance >= 0:
                closing_credit = balance
            else:
                closing_debit = -balance

        if not include_zero_balance and debit_mv == 0 and credit_mv == 0 and closing_debit == 0 and closing_credit == 0:
            continue

        rows.append({
            "account": acc,
            "debit_movements": debit_mv,
            "credit_movements": credit_mv,
            "closing_debit": closing_debit,
            "closing_credit": closing_credit,
        })
        total_debit += closing_debit
        total_credit += closing_credit

    return {
        "rows": rows,
        "totals": {"debit": total_debit, "credit": total_credit},
        "date_from": date_from,
        "date_to": date_to,
        "is_balanced": total_debit == total_credit,
    }


def party_statement_data(
    party_id: int,
    *,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> dict:
    """يُرجِع نفس بيانات party_statement — واجهة موحدة للتقارير."""
    from app.services.parties import party_statement
    return party_statement(party_id, date_from=date_from, date_to=date_to)
