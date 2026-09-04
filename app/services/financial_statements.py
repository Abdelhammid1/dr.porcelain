"""القوائم المالية — قائمة الدخل + الميزانية العمومية + التدفق النقدي.

كل الحسابات مبنية من القيود المرحّلة (POSTED) في `journal_lines`. الأرصدة تُقرأ
بحسب الجانب الطبيعي لكل حساب (asset/cost/expense: مدين؛ liability/equity/revenue: دائن).

- Income Statement: Revenue − COGS − Expenses = Net Profit  (لفترة محددة)
- Balance Sheet:    Assets = Liabilities + Equity + Net Profit  (لحظة محددة)
- Cash Flow:        حركة حسابات 1010 (نقدية) و 1020-* (بنوك) لفترة محددة
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import func

from app.extensions import db
from app.models.account import Account, AccountType
from app.models.journal import JournalEntry, JournalEntryStatus, JournalLine


ZERO = Decimal("0")


# =====================================================
# Helpers
# =====================================================

def _period_totals(account_ids: list[int], date_from: Optional[date], date_to: Optional[date]) -> dict[int, tuple[Decimal, Decimal]]:
    """يُرجِع dict لكل حساب: (total_debit, total_credit) خلال الفترة."""
    if not account_ids:
        return {}
    q = (
        db.session.query(
            JournalLine.account_id,
            func.coalesce(func.sum(JournalLine.debit), 0).label("d"),
            func.coalesce(func.sum(JournalLine.credit), 0).label("c"),
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .filter(JournalLine.account_id.in_(account_ids))
        .filter(JournalEntry.status == JournalEntryStatus.POSTED)
    )
    if date_from:
        q = q.filter(JournalEntry.entry_date >= date_from)
    if date_to:
        q = q.filter(JournalEntry.entry_date <= date_to)
    q = q.group_by(JournalLine.account_id)
    return {row.account_id: (Decimal(str(row.d)), Decimal(str(row.c))) for row in q.all()}


def _account_balance(acc: Account, totals: dict[int, tuple[Decimal, Decimal]]) -> Decimal:
    """رصيد صافٍ بالجانب الطبيعي."""
    d, c = totals.get(acc.id, (ZERO, ZERO))
    return (d - c) if acc.type.normal_side == "debit" else (c - d)


# =====================================================
# Income Statement — قائمة الدخل
# =====================================================

@dataclass
class IncomeLine:
    account: Account
    amount: Decimal


@dataclass
class IncomeStatement:
    date_from: date
    date_to: date
    revenues: list[IncomeLine] = field(default_factory=list)
    total_revenue: Decimal = ZERO
    costs: list[IncomeLine] = field(default_factory=list)
    total_cost: Decimal = ZERO
    gross_profit: Decimal = ZERO
    expenses: list[IncomeLine] = field(default_factory=list)
    total_expense: Decimal = ZERO
    net_profit: Decimal = ZERO


def income_statement(*, date_from: date, date_to: date) -> IncomeStatement:
    """قائمة دخل للفترة."""
    accounts = (
        db.session.query(Account)
        .filter(Account.is_postable == True)  # noqa: E712
        .filter(Account.type.in_([AccountType.REVENUE, AccountType.COST, AccountType.EXPENSE]))
        .order_by(Account.code)
        .all()
    )
    totals = _period_totals([a.id for a in accounts], date_from, date_to)

    stmt = IncomeStatement(date_from=date_from, date_to=date_to)
    for acc in accounts:
        amt = _account_balance(acc, totals)
        if amt == 0:
            continue
        line = IncomeLine(account=acc, amount=amt)
        if acc.type == AccountType.REVENUE:
            stmt.revenues.append(line)
            stmt.total_revenue += amt
        elif acc.type == AccountType.COST:
            stmt.costs.append(line)
            stmt.total_cost += amt
        else:  # EXPENSE
            stmt.expenses.append(line)
            stmt.total_expense += amt

    stmt.gross_profit = stmt.total_revenue - stmt.total_cost
    stmt.net_profit = stmt.gross_profit - stmt.total_expense
    return stmt


# =====================================================
# Balance Sheet — الميزانية العمومية
# =====================================================

@dataclass
class BalanceSheetLine:
    account: Account
    amount: Decimal


@dataclass
class BalanceSheet:
    as_of: date
    assets: list[BalanceSheetLine] = field(default_factory=list)
    total_assets: Decimal = ZERO
    liabilities: list[BalanceSheetLine] = field(default_factory=list)
    total_liabilities: Decimal = ZERO
    equity: list[BalanceSheetLine] = field(default_factory=list)
    total_equity: Decimal = ZERO
    period_net_profit: Decimal = ZERO   # صافي الربح المتراكم من التاريخ الافتراضي (بداية "التشغيل")
    total_liabilities_equity: Decimal = ZERO
    is_balanced: bool = False
    difference: Decimal = ZERO


def balance_sheet(*, as_of: date) -> BalanceSheet:
    """ميزانية عمومية اعتبارًا من `as_of`.

    - الأصول والالتزامات وحقوق الملكية: أرصدتها التراكمية حتى `as_of`
    - نضيف صافي الربح (Revenue - COGS - Expenses) للفترة من الأزل حتى `as_of`
      إلى حقوق الملكية — لأن هذه الأرباح لم تُقفَل بعد إلى حساب الأرباح المحتجزة.
    """
    accounts = (
        db.session.query(Account)
        .filter(Account.is_postable == True)  # noqa: E712
        .order_by(Account.code)
        .all()
    )
    totals = _period_totals(
        [a.id for a in accounts if a.type in (AccountType.ASSET, AccountType.LIABILITY, AccountType.EQUITY)],
        None, as_of,
    )

    bs = BalanceSheet(as_of=as_of)
    for acc in accounts:
        if acc.type == AccountType.ASSET:
            amt = _account_balance(acc, totals)
            if amt != 0:
                bs.assets.append(BalanceSheetLine(acc, amt))
                bs.total_assets += amt
        elif acc.type == AccountType.LIABILITY:
            amt = _account_balance(acc, totals)
            if amt != 0:
                bs.liabilities.append(BalanceSheetLine(acc, amt))
                bs.total_liabilities += amt
        elif acc.type == AccountType.EQUITY:
            amt = _account_balance(acc, totals)
            if amt != 0:
                bs.equity.append(BalanceSheetLine(acc, amt))
                bs.total_equity += amt

    # نحسب صافي الربح المتراكم منذ الأزل حتى as_of
    is_stmt = income_statement(date_from=date(1900, 1, 1), date_to=as_of)
    bs.period_net_profit = is_stmt.net_profit

    bs.total_liabilities_equity = bs.total_liabilities + bs.total_equity + bs.period_net_profit
    bs.difference = bs.total_assets - bs.total_liabilities_equity
    bs.is_balanced = abs(bs.difference) < Decimal("0.01")
    return bs


# =====================================================
# Cash Flow — التدفق النقدي (التشغيلي)
# =====================================================

@dataclass
class CashFlowMovement:
    date: date
    doc_number: str
    memo: str
    account_code: str
    inflow: Decimal
    outflow: Decimal
    running_balance: Decimal


@dataclass
class CashFlow:
    date_from: date
    date_to: date
    opening_balance: Decimal = ZERO
    total_inflow: Decimal = ZERO
    total_outflow: Decimal = ZERO
    closing_balance: Decimal = ZERO
    movements: list[CashFlowMovement] = field(default_factory=list)


def cash_flow(*, date_from: date, date_to: date) -> CashFlow:
    """تدفق نقدي تشغيلي — كل حركة على 1010 وأي حساب فرعي تحت 1020."""
    cash_accounts = (
        db.session.query(Account)
        .filter(
            (Account.code == "1010") |
            (Account.code.like("1020-%"))
        )
        .filter(Account.is_postable == True)  # noqa: E712
        .all()
    )
    ids = [a.id for a in cash_accounts]

    # الرصيد الافتتاحي — كل حركات النقدية قبل date_from
    opening_totals = _period_totals(ids, None, None)
    # نحن نحتاج فقط "قبل date_from" لذا نعيد بطريقة مختلفة
    opening_totals = {}
    q = (
        db.session.query(
            JournalLine.account_id,
            func.coalesce(func.sum(JournalLine.debit), 0).label("d"),
            func.coalesce(func.sum(JournalLine.credit), 0).label("c"),
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .filter(JournalLine.account_id.in_(ids))
        .filter(JournalEntry.status == JournalEntryStatus.POSTED)
        .filter(JournalEntry.entry_date < date_from)
        .group_by(JournalLine.account_id)
    )
    for row in q.all():
        opening_totals[row.account_id] = (Decimal(str(row.d)), Decimal(str(row.c)))

    opening_balance = ZERO
    for acc in cash_accounts:
        opening_balance += _account_balance(acc, opening_totals)

    # الحركات ضمن الفترة
    lines_q = (
        db.session.query(JournalLine, JournalEntry)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .filter(JournalLine.account_id.in_(ids))
        .filter(JournalEntry.status == JournalEntryStatus.POSTED)
        .filter(JournalEntry.entry_date >= date_from)
        .filter(JournalEntry.entry_date <= date_to)
        .order_by(JournalEntry.entry_date, JournalEntry.id, JournalLine.id)
    )
    running = opening_balance
    movements = []
    total_in = ZERO
    total_out = ZERO
    accounts_by_id = {a.id: a for a in cash_accounts}
    for line, entry in lines_q.all():
        d = Decimal(str(line.debit or 0))
        c = Decimal(str(line.credit or 0))
        inflow = d      # مدين على النقدية = دخول
        outflow = c     # دائن على النقدية = خروج
        running += (inflow - outflow)
        total_in += inflow
        total_out += outflow
        movements.append(CashFlowMovement(
            date=entry.entry_date,
            doc_number=entry.doc_number,
            memo=line.memo or entry.memo or "",
            account_code=accounts_by_id[line.account_id].code,
            inflow=inflow,
            outflow=outflow,
            running_balance=running,
        ))

    return CashFlow(
        date_from=date_from, date_to=date_to,
        opening_balance=opening_balance,
        total_inflow=total_in,
        total_outflow=total_out,
        closing_balance=running,
        movements=movements,
    )
