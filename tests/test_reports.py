"""اختبارات التقارير — ميزان المراجعة وتوليد PDF."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.models.account import Account
from app.models.journal import JournalSourceType
from app.models.setting import set_setting
from app.services.ledger import LedgerLineDraft, post_journal_entry
from app.services.pdf import party_statement_pdf, trial_balance_pdf
from app.services.reports import trial_balance
from app.services.parties import create_party
from app.models.party import PartyType
from seeds.chart_of_accounts import seed_chart_of_accounts


@pytest.fixture()
def env(app):
    seed_chart_of_accounts(_db.session)
    _db.session.commit()
    yield


# ---------- Trial Balance ----------

class TestTrialBalance:
    def test_trial_balance_always_balanced(self, env):
        """أي مجموعة قيود متوازنة تنتج ميزان مراجعة متوازن (D=C)."""
        data = trial_balance()
        assert data["is_balanced"] is True
        assert data["totals"]["debit"] == data["totals"]["credit"]

    def test_new_entry_appears_in_trial_balance(self, env):
        cash = _db.session.query(Account).filter_by(code="1010").one()
        revenue = _db.session.query(Account).filter_by(code="4100").one()
        d_before, c_before = data_totals(cash.id, revenue.id)

        post_journal_entry(
            entry_date=date(2026, 1, 1),
            source_type=JournalSourceType.MANUAL,
            source_id=None, memo="اختبار",
            lines=[LedgerLineDraft(cash.id, debit=Decimal("500")),
                   LedgerLineDraft(revenue.id, credit=Decimal("500"))],
        )
        _db.session.commit()

        data = trial_balance()
        assert data["is_balanced"] is True
        codes = {r["account"].code for r in data["rows"]}
        assert "1010" in codes and "4100" in codes

    def test_date_filter_excludes_out_of_range_entries(self, env):
        """قيد بتاريخ 2030/12/1 يظهر خارج نطاق يناير 2027 فقط."""
        cash = _db.session.query(Account).filter_by(code="1010").one()
        revenue = _db.session.query(Account).filter_by(code="4100").one()
        far_date = date(2030, 12, 1)

        # نأخذ حركة الحساب قبل الاختبار في النطاق (يجب أن تكون صفر — تاريخ بعيد)
        data_in_range = trial_balance(date_from=date(2030, 12, 1), date_to=date(2030, 12, 31))
        cash_in_range = next((r["debit_movements"] for r in data_in_range["rows"] if r["account"].code == "1010"), Decimal("0"))

        post_journal_entry(
            entry_date=far_date,
            source_type=JournalSourceType.MANUAL, source_id=None, memo="مستقبل",
            lines=[LedgerLineDraft(cash.id, debit=Decimal("777")),
                   LedgerLineDraft(revenue.id, credit=Decimal("777"))],
        )
        _db.session.commit()

        # النطاق الآن يشمل القيد الجديد
        data_after = trial_balance(date_from=date(2030, 12, 1), date_to=date(2030, 12, 31))
        cash_after = next(r["debit_movements"] for r in data_after["rows"] if r["account"].code == "1010")
        assert cash_after - cash_in_range == Decimal("777")

        # نطاق قبل التاريخ لا يشمله
        data_before = trial_balance(date_from=date(2030, 1, 1), date_to=date(2030, 1, 31))
        cash_before = next((r["debit_movements"] for r in data_before["rows"] if r["account"].code == "1010"), Decimal("0"))
        # لم يزد بمقدار 777
        assert cash_before == Decimal("0") or cash_before < Decimal("777")


def data_totals(*account_ids):
    """Helper لقياس أرصدة حسابات معينة قبل/بعد اختبار."""
    from app.models.journal import JournalLine, JournalEntry, JournalEntryStatus
    from sqlalchemy import func
    q = (
        _db.session.query(
            func.coalesce(func.sum(JournalLine.debit), 0),
            func.coalesce(func.sum(JournalLine.credit), 0),
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .filter(JournalEntry.status == JournalEntryStatus.POSTED)
        .filter(JournalLine.account_id.in_(account_ids))
    )
    d, c = q.one()
    return Decimal(str(d)), Decimal(str(c))


# ---------- PDF ----------

class TestPDFGeneration:
    def test_trial_balance_pdf_produces_valid_bytes(self, env):
        cash = _db.session.query(Account).filter_by(code="1010").one()
        revenue = _db.session.query(Account).filter_by(code="4100").one()
        post_journal_entry(
            entry_date=date(2026, 1, 1),
            source_type=JournalSourceType.MANUAL, source_id=None, memo="اختبار",
            lines=[LedgerLineDraft(cash.id, debit=Decimal("100")),
                   LedgerLineDraft(revenue.id, credit=Decimal("100"))],
        )
        _db.session.commit()
        data = trial_balance()
        pdf = trial_balance_pdf(data, store_name="دكتور بورسلين", tax_number="123456789")
        # PDF يبدأ بـ %PDF-
        assert pdf.startswith(b"%PDF-"), "الناتج ليس PDF صالحًا"
        assert len(pdf) > 500

    def test_party_statement_pdf_produces_valid_bytes(self, env):
        from app.services.parties import party_statement

        c = create_party(type=PartyType.CUSTOMER, name_ar="عميل PDF")
        _db.session.commit()

        revenue = _db.session.query(Account).filter_by(code="4100").one()
        post_journal_entry(
            entry_date=date(2026, 1, 1),
            source_type=JournalSourceType.SALES_INVOICE, source_id=None, memo="بيع",
            lines=[LedgerLineDraft(c.account.id, debit=Decimal("100")),
                   LedgerLineDraft(revenue.id, credit=Decimal("100"))],
        )
        _db.session.commit()

        stmt = party_statement(c.id)
        pdf = party_statement_pdf(stmt, store_name="دكتور بورسلين")
        assert pdf.startswith(b"%PDF-")
        assert len(pdf) > 500
