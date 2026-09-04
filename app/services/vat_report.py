"""تقرير ضريبة القيمة المضافة."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func

from app.extensions import db
from app.models.account import Account
from app.models.journal import JournalEntry, JournalEntryStatus, JournalLine


ZERO = Decimal("0")


@dataclass
class VATReport:
    date_from: date
    date_to: date
    output_tax: Decimal = ZERO   # 2200 credits − debits (زيادة الالتزام)
    input_tax: Decimal = ZERO    # 1300 debits − credits (زيادة الأصل)
    net_payable: Decimal = ZERO  # output − input


def vat_report(*, date_from: date, date_to: date) -> VATReport:
    r = VATReport(date_from=date_from, date_to=date_to)

    accounts_by_code = {
        a.code: a
        for a in db.session.query(Account).filter(Account.code.in_(["2200", "1300"])).all()
    }
    if not accounts_by_code:
        return r

    for code in ("2200", "1300"):
        acc = accounts_by_code.get(code)
        if acc is None:
            continue
        row = (
            db.session.query(
                func.coalesce(func.sum(JournalLine.debit), 0),
                func.coalesce(func.sum(JournalLine.credit), 0),
            )
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .filter(JournalLine.account_id == acc.id)
            .filter(JournalEntry.status == JournalEntryStatus.POSTED)
            .filter(JournalEntry.entry_date >= date_from)
            .filter(JournalEntry.entry_date <= date_to)
            .one()
        )
        d = Decimal(str(row[0]))
        c = Decimal(str(row[1]))
        if code == "2200":  # مخرجات (التزام: دائن − مدين)
            r.output_tax = c - d
        else:  # 1300 مدخلات (أصل: مدين − دائن)
            r.input_tax = d - c

    r.net_payable = r.output_tax - r.input_tax
    return r
