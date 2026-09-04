"""اختبارات خدمة الأطراف (العملاء والموردين).

يغطي القواعد المهمة:
- عند إنشاء عميل/مورد يُنشَأ حساب فرعي تحت الأب الصحيح.
- الحساب الفرعي مرتبط بـ party_id ومسمى بشكل واضح.
- ترقيم الأطراف تسلسلي فريد.
- منع تكرار الهاتف لنفس النوع.
- منع الحذف عند وجود حركة.
- كشف الحساب يعطي رصيدًا جاريًا صحيحًا.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.models.account import Account
from app.models.journal import JournalSourceType
from app.models.party import Party, PartyType
from app.services.ledger import LedgerLineDraft, post_journal_entry
from app.services.parties import (
    PartyError,
    can_delete_party,
    create_party,
    delete_party,
    party_statement,
    update_party,
)
from seeds.chart_of_accounts import seed_chart_of_accounts


@pytest.fixture()
def seeded(app):
    seed_chart_of_accounts(_db.session)
    _db.session.commit()
    yield


# ---------- 1) الإنشاء + الحساب الفرعي التلقائي ----------

class TestPartyCreation:
    def test_customer_creation_opens_sub_account_under_1200(self, seeded):
        c = create_party(type=PartyType.CUSTOMER, name_ar="أحمد محمد", phone="01000000001")
        _db.session.commit()

        assert c.code.startswith("C-")
        assert c.account is not None
        assert c.account.parent.code == "1200"
        assert c.account.code.startswith("1200-C-")
        assert c.account.party_id == c.id
        assert c.account.is_postable is True
        # اسم الحساب يوضح الطرف وكوده
        assert c.name_ar in c.account.name_ar
        assert c.code in c.account.name_ar

    def test_vendor_creation_opens_sub_account_under_2100(self, seeded):
        v = create_party(type=PartyType.VENDOR, name_ar="مؤسسة الأمل للأدوات", phone="01000000002")
        _db.session.commit()

        assert v.code.startswith("V-")
        assert v.account is not None
        assert v.account.parent.code == "2100"
        assert v.account.code.startswith("2100-V-")
        assert v.account.party_id == v.id
        # المورد التزام → الحساب من نوع liability
        assert v.account.type.value == "liability"

    def test_customer_and_vendor_use_separate_sequences(self, seeded):
        c1 = create_party(type=PartyType.CUSTOMER, name_ar="عميل A")
        v1 = create_party(type=PartyType.VENDOR, name_ar="مورد A")
        c2 = create_party(type=PartyType.CUSTOMER, name_ar="عميل B")
        v2 = create_party(type=PartyType.VENDOR, name_ar="مورد B")
        _db.session.commit()

        # الأكواد بادئتها صحيحة
        assert c1.code.startswith("C-") and c2.code.startswith("C-")
        assert v1.code.startswith("V-") and v2.code.startswith("V-")

        # عدّاد كل نوع يسير بشكل منفصل — c2 يتبع c1 مباشرة، وليس تأثرًا بـ v1
        c1_num = int(c1.code.split("-")[1])
        c2_num = int(c2.code.split("-")[1])
        v1_num = int(v1.code.split("-")[1])
        v2_num = int(v2.code.split("-")[1])
        assert c2_num == c1_num + 1
        assert v2_num == v1_num + 1

    def test_empty_name_rejected(self, seeded):
        with pytest.raises(PartyError, match="اسم"):
            create_party(type=PartyType.CUSTOMER, name_ar="   ")

    def test_duplicate_phone_rejected_same_type(self, seeded):
        create_party(type=PartyType.CUSTOMER, name_ar="عميل A", phone="01000000010")
        _db.session.commit()
        with pytest.raises(PartyError, match="يوجد بالفعل"):
            create_party(type=PartyType.CUSTOMER, name_ar="عميل B", phone="01000000010")
        _db.session.rollback()

    def test_same_phone_allowed_across_types(self, seeded):
        """رقم واحد قد يكون لعميل ومورد في نفس الوقت (سيناريو تجاري وارد)."""
        create_party(type=PartyType.CUSTOMER, name_ar="ك", phone="01000000020")
        create_party(type=PartyType.VENDOR, name_ar="م", phone="01000000020")
        _db.session.commit()
        assert True  # لم يُرفَع خطأ


# ---------- 2) التعديل ----------

class TestPartyUpdate:
    def test_update_name_syncs_to_account_name(self, seeded):
        c = create_party(type=PartyType.CUSTOMER, name_ar="اسم قديم")
        _db.session.commit()
        old_account_code = c.account.code
        c = update_party(c.id, name_ar="اسم جديد")
        _db.session.commit()

        assert c.name_ar == "اسم جديد"
        assert "اسم جديد" in c.account.name_ar
        # كود الحساب لا يتغير
        assert c.account.code == old_account_code

    def test_deactivate_party_deactivates_account(self, seeded):
        c = create_party(type=PartyType.CUSTOMER, name_ar="ك")
        _db.session.commit()
        update_party(c.id, is_active=False)
        _db.session.commit()
        assert c.is_active is False
        assert c.account.is_active is False


# ---------- 3) الحذف (مسموح فقط بدون حركة) ----------

class TestPartyDeletion:
    def test_can_delete_when_no_movements(self, seeded):
        c = create_party(type=PartyType.CUSTOMER, name_ar="بدون حركة")
        _db.session.commit()
        ok, reason = can_delete_party(c.id)
        assert ok is True
        assert reason is None

        delete_party(c.id)
        _db.session.commit()
        assert _db.session.get(Party, c.id) is None

    def test_cannot_delete_when_has_movements(self, seeded):
        c = create_party(type=PartyType.CUSTOMER, name_ar="له حركة", phone="01098765432")
        _db.session.commit()
        # قيد على حسابه
        revenue = _db.session.query(Account).filter_by(code="4100").one()
        post_journal_entry(
            entry_date=date(2026, 1, 1),
            source_type=JournalSourceType.SALES_INVOICE,
            source_id=1,
            memo="بيع تجريبي",
            lines=[
                LedgerLineDraft(c.account.id, debit=Decimal("100")),
                LedgerLineDraft(revenue.id, credit=Decimal("100")),
            ],
        )
        _db.session.commit()

        ok, reason = can_delete_party(c.id)
        assert ok is False
        assert "حركة" in (reason or "")

        with pytest.raises(PartyError):
            delete_party(c.id)


# ---------- 4) كشف الحساب مع Running Balance ----------

class TestPartyStatement:
    def _post_receipt(self, cash_id, ar_id, amount, entry_date):
        """قيد تحصيل: مدين نقدية / دائن ذمم عميل."""
        post_journal_entry(
            entry_date=entry_date,
            source_type=JournalSourceType.CUSTOMER_RECEIPT,
            source_id=None,
            memo=f"تحصيل بتاريخ {entry_date}",
            lines=[
                LedgerLineDraft(cash_id, debit=Decimal(str(amount))),
                LedgerLineDraft(ar_id, credit=Decimal(str(amount))),
            ],
        )

    def _post_sale(self, ar_id, revenue_id, amount, entry_date):
        """قيد بيع: مدين ذمم عميل / دائن إيراد."""
        post_journal_entry(
            entry_date=entry_date,
            source_type=JournalSourceType.SALES_INVOICE,
            source_id=None,
            memo=f"بيع بتاريخ {entry_date}",
            lines=[
                LedgerLineDraft(ar_id, debit=Decimal(str(amount))),
                LedgerLineDraft(revenue_id, credit=Decimal(str(amount))),
            ],
        )

    def test_running_balance_correct_for_customer(self, seeded):
        c = create_party(type=PartyType.CUSTOMER, name_ar="عميل كشف")
        _db.session.commit()

        cash = _db.session.query(Account).filter_by(code="1010").one()
        revenue = _db.session.query(Account).filter_by(code="4100").one()

        self._post_sale(c.account.id, revenue.id, 1000, date(2026, 1, 5))
        self._post_receipt(cash.id, c.account.id, 300, date(2026, 1, 10))
        self._post_sale(c.account.id, revenue.id, 500, date(2026, 1, 15))
        _db.session.commit()

        stmt = party_statement(c.id)
        assert stmt["opening_balance"] == Decimal("0")
        assert len(stmt["movements"]) == 3

        # الحركات مرتبة بالتاريخ ومع رصيد جاري صحيح
        balances = [m["balance"] for m in stmt["movements"]]
        assert balances == [Decimal("1000"), Decimal("700"), Decimal("1200")]
        assert stmt["closing_balance"] == Decimal("1200")

    def test_date_filter_computes_opening_from_prior_movements(self, seeded):
        c = create_party(type=PartyType.CUSTOMER, name_ar="عميل تصفية")
        _db.session.commit()

        cash = _db.session.query(Account).filter_by(code="1010").one()
        revenue = _db.session.query(Account).filter_by(code="4100").one()

        self._post_sale(c.account.id, revenue.id, 500, date(2026, 1, 1))
        self._post_sale(c.account.id, revenue.id, 300, date(2026, 2, 1))
        self._post_receipt(cash.id, c.account.id, 200, date(2026, 3, 1))
        _db.session.commit()

        stmt = party_statement(c.id, date_from=date(2026, 2, 1), date_to=date(2026, 3, 31))
        # الرصيد الافتتاحي = مبيعات قبل 1/2 = 500
        assert stmt["opening_balance"] == Decimal("500")
        # حركتين في النطاق: +300 و -200 → الرصيد الجاري 800 ثم 600
        assert [m["balance"] for m in stmt["movements"]] == [Decimal("800"), Decimal("600")]
        assert stmt["closing_balance"] == Decimal("600")

    def test_vendor_statement_uses_credit_normal_side(self, seeded):
        """المورد التزام — الرصيد الطبيعي دائن، فيُحسب دائن - مدين."""
        v = create_party(type=PartyType.VENDOR, name_ar="مورد كشف")
        _db.session.commit()

        inventory = _db.session.query(Account).filter_by(code="1100").one()
        cash = _db.session.query(Account).filter_by(code="1010").one()

        # فاتورة شراء آجل: مدين مخزون / دائن مورد بـ 1000
        post_journal_entry(
            entry_date=date(2026, 1, 1),
            source_type=JournalSourceType.PURCHASE_INVOICE,
            source_id=None,
            memo="شراء",
            lines=[
                LedgerLineDraft(inventory.id, debit=Decimal("1000")),
                LedgerLineDraft(v.account.id, credit=Decimal("1000")),
            ],
        )
        # سداد جزئي: مدين مورد / دائن نقدية بـ 400
        post_journal_entry(
            entry_date=date(2026, 1, 20),
            source_type=JournalSourceType.VENDOR_PAYMENT,
            source_id=None,
            memo="سداد",
            lines=[
                LedgerLineDraft(v.account.id, debit=Decimal("400")),
                LedgerLineDraft(cash.id, credit=Decimal("400")),
            ],
        )
        _db.session.commit()

        stmt = party_statement(v.id)
        # الرصيد الجاري: بعد الشراء 1000 (دائن)، بعد السداد 600 (لا يزال دائنًا للمورد)
        assert [m["balance"] for m in stmt["movements"]] == [Decimal("1000"), Decimal("600")]
        assert stmt["closing_balance"] == Decimal("600")
