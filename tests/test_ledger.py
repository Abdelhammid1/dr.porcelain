"""اختبارات محرك دفتر الأستاذ — القواعد الصارمة اللي لا تُكسَر أبدًا.

ملحوظة: الـ app fixture يفتح app_context لكل الجلسة، لذا لا نحتاج نستخدم
`with app.app_context()` داخل الاختبارات الفردية (يُنشِئ session منفصل).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.models.account import Account, AccountType
from app.models.journal import JournalEntry, JournalEntryStatus, JournalSourceType
from app.services.ledger import LedgerError, LedgerLineDraft, post_journal_entry, reverse_entry
from app.services.numbering import next_document_number
from seeds.chart_of_accounts import seed_chart_of_accounts


# ---------- Fixtures ----------

@pytest.fixture()
def seeded_coa(app):
    """يحقن دليل الحسابات كاملًا قبل الاختبار (idempotent)."""
    seed_chart_of_accounts(_db.session)
    _db.session.commit()
    yield


@pytest.fixture()
def cash_account(seeded_coa) -> Account:
    return _db.session.query(Account).filter_by(code="1010").one()


@pytest.fixture()
def sales_revenue_account(seeded_coa) -> Account:
    return _db.session.query(Account).filter_by(code="4100").one()


@pytest.fixture()
def cogs_account(seeded_coa) -> Account:
    return _db.session.query(Account).filter_by(code="5100").one()


@pytest.fixture()
def inventory_account(seeded_coa) -> Account:
    return _db.session.query(Account).filter_by(code="1100").one()


# ---------- 1) قواعد التوازن ----------

class TestBalancedEntries:
    def test_balanced_entry_is_posted(self, cash_account, sales_revenue_account):
        entry = post_journal_entry(
            entry_date=date(2026, 1, 1),
            source_type=JournalSourceType.MANUAL,
            source_id=None,
            memo="اختبار قيد متوازن",
            lines=[
                LedgerLineDraft(cash_account.id, debit=Decimal("100.000")),
                LedgerLineDraft(sales_revenue_account.id, credit=Decimal("100.000")),
            ],
        )
        _db.session.commit()
        assert entry.status == JournalEntryStatus.POSTED
        assert entry.total_debit == Decimal("100.000")
        assert entry.total_credit == Decimal("100.000")
        assert entry.is_balanced
        assert entry.doc_number.startswith("JE-")

    def test_unbalanced_entry_is_rejected(self, cash_account, sales_revenue_account):
        with pytest.raises(LedgerError, match="غير متوازن"):
            post_journal_entry(
                entry_date=date(2026, 1, 1),
                source_type=JournalSourceType.MANUAL,
                source_id=None,
                memo="قيد غير متوازن",
                lines=[
                    LedgerLineDraft(cash_account.id, debit=Decimal("100.000")),
                    LedgerLineDraft(sales_revenue_account.id, credit=Decimal("50.000")),
                ],
            )

    def test_less_than_two_lines_rejected(self, cash_account):
        with pytest.raises(LedgerError, match="سطرين على الأقل"):
            post_journal_entry(
                entry_date=date(2026, 1, 1),
                source_type=JournalSourceType.MANUAL,
                source_id=None,
                memo="سطر واحد",
                lines=[LedgerLineDraft(cash_account.id, debit=Decimal("100.000"))],
            )

    def test_zero_amount_rejected(self, cash_account, sales_revenue_account):
        with pytest.raises(LedgerError, match="صفر"):
            post_journal_entry(
                entry_date=date(2026, 1, 1),
                source_type=JournalSourceType.MANUAL,
                source_id=None,
                memo="سطر بصفر",
                lines=[
                    LedgerLineDraft(cash_account.id, debit=Decimal("0"), credit=Decimal("0")),
                    LedgerLineDraft(sales_revenue_account.id, credit=Decimal("0")),
                ],
            )

    def test_both_sides_positive_rejected(self, cash_account, sales_revenue_account):
        with pytest.raises(LedgerError, match="مدينًا ودائنًا"):
            post_journal_entry(
                entry_date=date(2026, 1, 1),
                source_type=JournalSourceType.MANUAL,
                source_id=None,
                memo="سطر مدين ودائن معًا",
                lines=[
                    LedgerLineDraft(cash_account.id, debit=Decimal("100"), credit=Decimal("50")),
                    LedgerLineDraft(sales_revenue_account.id, credit=Decimal("50")),
                ],
            )

    def test_negative_amount_rejected(self, cash_account, sales_revenue_account):
        with pytest.raises(LedgerError, match="سالبة"):
            post_journal_entry(
                entry_date=date(2026, 1, 1),
                source_type=JournalSourceType.MANUAL,
                source_id=None,
                memo="سالب",
                lines=[
                    LedgerLineDraft(cash_account.id, debit=Decimal("-10")),
                    LedgerLineDraft(sales_revenue_account.id, credit=Decimal("-10")),
                ],
            )


# ---------- 2) الترحيل على حسابات محظورة ----------

class TestControlAccountPosting:
    def test_cannot_post_to_control_account(self, cash_account, seeded_coa):
        # 4000 (الإيرادات) is_postable=False
        parent = _db.session.query(Account).filter_by(code="4000").one()
        with pytest.raises(LedgerError, match="حساب أب"):
            post_journal_entry(
                entry_date=date(2026, 1, 1),
                source_type=JournalSourceType.MANUAL,
                source_id=None,
                memo="ترحيل على حساب أب",
                lines=[
                    LedgerLineDraft(cash_account.id, debit=Decimal("100")),
                    LedgerLineDraft(parent.id, credit=Decimal("100")),
                ],
            )

    def test_cannot_post_to_inactive_account(self, cash_account, sales_revenue_account):
        sales_revenue_account.is_active = False
        _db.session.flush()
        with pytest.raises(LedgerError, match="غير نشط"):
            post_journal_entry(
                entry_date=date(2026, 1, 1),
                source_type=JournalSourceType.MANUAL,
                source_id=None,
                memo="حساب موقوف",
                lines=[
                    LedgerLineDraft(cash_account.id, debit=Decimal("100")),
                    LedgerLineDraft(sales_revenue_account.id, credit=Decimal("100")),
                ],
            )
        _db.session.rollback()  # نعيد الحساب لحالته الأصلية للاختبارات التالية


# ---------- 3) عكس القيود ----------

class TestReversal:
    def test_reverse_creates_opposite_entry(self, cash_account, sales_revenue_account):
        original = post_journal_entry(
            entry_date=date(2026, 2, 1),
            source_type=JournalSourceType.MANUAL,
            source_id=None,
            memo="أصلي",
            lines=[
                LedgerLineDraft(cash_account.id, debit=Decimal("500")),
                LedgerLineDraft(sales_revenue_account.id, credit=Decimal("500")),
            ],
        )
        _db.session.commit()

        reversal = reverse_entry(
            entry_id=original.id,
            reason="خطأ في الإدخال",
            user_id=None,
            entry_date=date(2026, 2, 2),
        )
        _db.session.commit()

        _db.session.refresh(original)
        assert original.status == JournalEntryStatus.REVERSED
        assert original.reversed_by_id == reversal.id
        assert original.reversal_reason == "خطأ في الإدخال"

        assert reversal.source_type == JournalSourceType.REVERSAL
        assert reversal.reversal_of_id == original.id
        assert reversal.is_balanced
        # الأطراف مبدّلة (كل ما هو مدين أصبح دائن والعكس)
        orig_pairs = {(l.account_id, l.debit, l.credit) for l in original.lines}
        rev_pairs = {(l.account_id, l.credit, l.debit) for l in reversal.lines}
        assert orig_pairs == rev_pairs

    def test_cannot_reverse_without_reason(self, cash_account, sales_revenue_account):
        entry = post_journal_entry(
            entry_date=date(2026, 2, 1),
            source_type=JournalSourceType.MANUAL,
            source_id=None,
            memo="أصلي",
            lines=[
                LedgerLineDraft(cash_account.id, debit=Decimal("100")),
                LedgerLineDraft(sales_revenue_account.id, credit=Decimal("100")),
            ],
        )
        _db.session.commit()
        with pytest.raises(LedgerError, match="سبب"):
            reverse_entry(entry_id=entry.id, reason="   ", user_id=None)

    def test_cannot_reverse_twice(self, cash_account, sales_revenue_account):
        entry = post_journal_entry(
            entry_date=date(2026, 2, 1),
            source_type=JournalSourceType.MANUAL,
            source_id=None,
            memo="أصلي",
            lines=[
                LedgerLineDraft(cash_account.id, debit=Decimal("100")),
                LedgerLineDraft(sales_revenue_account.id, credit=Decimal("100")),
            ],
        )
        _db.session.commit()
        reverse_entry(entry_id=entry.id, reason="خطأ", user_id=None)
        _db.session.commit()
        with pytest.raises(LedgerError, match="معكوس بالفعل"):
            reverse_entry(entry_id=entry.id, reason="خطأ آخر", user_id=None)


# ---------- 4) الترقيم ----------

class TestNumbering:
    def test_sequential_and_no_duplicates(self, app):
        n1 = next_document_number("journal_entry")
        n2 = next_document_number("journal_entry")
        n3 = next_document_number("journal_entry")
        _db.session.commit()
        assert n1 != n2 != n3
        year = n1.split("-")[1]
        # الأرقام تُشكَّل بالتسلسل — ما نضمنش تبدأ من 000001 لو تشغيل تاني
        num1 = int(n1.split("-")[-1])
        num2 = int(n2.split("-")[-1])
        num3 = int(n3.split("-")[-1])
        assert num2 == num1 + 1
        assert num3 == num2 + 1

    def test_prefix_per_doc_type(self, app):
        inv = next_document_number("sales_invoice")
        pb = next_document_number("purchase_invoice")
        je = next_document_number("journal_entry")
        _db.session.commit()
        assert inv.startswith("INV-")
        assert pb.startswith("PB-")
        assert je.startswith("JE-")

    def test_unknown_doc_type_raises(self, app):
        from app.services.numbering import NumberingError
        with pytest.raises(NumberingError):
            next_document_number("nonexistent_doc_type")


# ---------- 5) رصيد الحساب من القيود ----------

class TestAccountBalance:
    def test_balance_computed_from_posted_entries(
        self, cash_account, sales_revenue_account
    ):
        # نمسك الرصيد الحالي قبل الاختبار (اختبارات سابقة قد ضافت قيود)
        cash_before = cash_account.compute_balance()
        rev_before = sales_revenue_account.compute_balance()

        post_journal_entry(
            entry_date=date(2026, 1, 1),
            source_type=JournalSourceType.MANUAL,
            source_id=None,
            memo="بيع 1",
            lines=[
                LedgerLineDraft(cash_account.id, debit=Decimal("300.000")),
                LedgerLineDraft(sales_revenue_account.id, credit=Decimal("300.000")),
            ],
        )
        post_journal_entry(
            entry_date=date(2026, 1, 2),
            source_type=JournalSourceType.MANUAL,
            source_id=None,
            memo="بيع 2",
            lines=[
                LedgerLineDraft(cash_account.id, debit=Decimal("200.000")),
                LedgerLineDraft(sales_revenue_account.id, credit=Decimal("200.000")),
            ],
        )
        _db.session.commit()

        assert cash_account.compute_balance() == cash_before + Decimal("500.000")
        assert sales_revenue_account.compute_balance() == rev_before + Decimal("500.000")

    def test_reversed_entry_pair_nets_to_zero_in_balance(
        self, cash_account, sales_revenue_account
    ):
        """قيد أصلي (REVERSED) + قيد عكسه (POSTED) يجب أن يتساويا إلى صفر.

        بمعنى: الرصيد قبل الترحيل = الرصيد بعد ترحيل الأصلي وعكسه.
        القيد الأصلي بيتحول لـ REVERSED لكن لازم يفضل جزء من الحساب، وإلا
        هيبقى العكس محسوب لوحده → ازدواج غلط في الأثر (بدل ما يكون الصافي = 0).
        """
        cash_before = cash_account.compute_balance()

        e = post_journal_entry(
            entry_date=date(2026, 1, 1),
            source_type=JournalSourceType.MANUAL,
            source_id=None,
            memo="بيع",
            lines=[
                LedgerLineDraft(cash_account.id, debit=Decimal("100")),
                LedgerLineDraft(sales_revenue_account.id, credit=Decimal("100")),
            ],
        )
        _db.session.commit()
        assert cash_account.compute_balance() == cash_before + Decimal("100")

        reverse_entry(entry_id=e.id, reason="تصحيح", user_id=None)
        _db.session.commit()
        # الأصلي (REVERSED) + العكس (POSTED) = صفر → الرصيد ما اتغيرش أساسًا
        assert cash_account.compute_balance() == cash_before


# ---------- 6) ميزان المراجعة: مدين = دائن دائمًا ----------

class TestTrialBalance:
    def _totals(self):
        from app.models.journal import JournalLine
        d = _db.session.query(_db.func.coalesce(_db.func.sum(JournalLine.debit), 0)).scalar()
        c = _db.session.query(_db.func.coalesce(_db.func.sum(JournalLine.credit), 0)).scalar()
        return Decimal(str(d)), Decimal(str(c))

    def test_balanced_entries_preserve_trial_balance(
        self, cash_account, sales_revenue_account, cogs_account, inventory_account
    ):
        """أي مجموعة قيود متوازنة يجب أن تُبقي مجموع المدين = مجموع الدائن."""
        d_before, c_before = self._totals()

        post_journal_entry(
            entry_date=date(2026, 3, 1),
            source_type=JournalSourceType.SALES_INVOICE,
            source_id=999,
            memo="بيع",
            lines=[
                LedgerLineDraft(cash_account.id, debit=Decimal("1000")),
                LedgerLineDraft(sales_revenue_account.id, credit=Decimal("1000")),
            ],
        )
        post_journal_entry(
            entry_date=date(2026, 3, 1),
            source_type=JournalSourceType.SALES_INVOICE,
            source_id=999,
            memo="تكلفة",
            lines=[
                LedgerLineDraft(cogs_account.id, debit=Decimal("600")),
                LedgerLineDraft(inventory_account.id, credit=Decimal("600")),
            ],
        )
        _db.session.commit()

        d_after, c_after = self._totals()
        # الدلتا متساوية (كل قيد متوازن)
        assert (d_after - d_before) == (c_after - c_before)
        assert (d_after - d_before) == Decimal("1600")
