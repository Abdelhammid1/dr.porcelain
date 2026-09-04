"""اختبارات ورديات نقطة البيع — فتح/قفل/فروقات/تكامل مع البيع."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.models.account import Account
from app.models.party import PartyType
from app.models.pos import POSSession, SessionStatus
from app.models.role import Role
from app.models.sales import PaymentMethod
from app.models.setting import set_setting
from app.models.user import User
from app.services.parties import create_party
from app.services.pos import (
    POSError,
    close_session,
    current_open_session_for,
    open_session,
    session_summary,
)
from app.services.products import create_category, create_product
from app.services.inventory import record_purchase
from app.services.sales import InvoiceLineDraft, create_cash_sale
from seeds.chart_of_accounts import seed_chart_of_accounts


@pytest.fixture()
def env(app):
    seed_chart_of_accounts(_db.session)
    # نحتاج دور cashier ومستخدمًا للاختبار
    role = _db.session.query(Role).filter_by(code="cashier").one_or_none()
    if role is None:
        role = Role(code="cashier", name_ar="كاشير", is_system=True)
        _db.session.add(role)
    _db.session.commit()

    tag = uuid.uuid4().hex[:8]
    cashier = User(
        username=f"cashier_{tag}",
        full_name=f"كاشير {tag}",
        role_id=role.id,
        is_active=True,
    )
    cashier.set_password("cashier123")
    _db.session.add(cashier)

    customer = create_party(type=PartyType.CUSTOMER, name_ar=f"عميل {tag}")
    cat = create_category(name_ar=f"cat-{tag}")
    product = create_product(
        name_ar=f"منتج {tag}", category_id=cat.id, default_price=Decimal("50"),
        variants=[{"variant_name": "افتراضي"}],
    )
    variant = product.variants[0]
    record_purchase(variant_id=variant.id, qty=100, unit_cost=30, move_date=date(2026, 1, 1))
    _db.session.commit()

    yield {"cashier": cashier, "customer": customer, "variant": variant}


# ---------- 1) فتح الوردية ----------

class TestOpenSession:
    def test_open_creates_custody_account(self, env):
        session = open_session(cashier_id=env["cashier"].id, opening_cash=Decimal("500"))
        _db.session.commit()

        assert session.status == SessionStatus.OPEN
        assert session.doc_number.startswith("POS-")
        assert session.opening_cash == Decimal("500.000")
        # حساب العهدة أُنشِئ تحت 1030
        assert session.custody_account is not None
        assert session.custody_account.parent.code == "1030"
        # الرصيد الافتتاحي محوّل من 1010 إلى العهدة
        assert session.custody_account.compute_balance() == Decimal("500.000")

    def test_zero_opening_cash_creates_no_transfer_journal(self, env):
        cash = _db.session.query(Account).filter_by(code="1010").one()
        cash_before = cash.compute_balance()

        session = open_session(cashier_id=env["cashier"].id, opening_cash=Decimal("0"))
        _db.session.commit()

        assert session.opening_cash == Decimal("0.000")
        assert cash.compute_balance() == cash_before  # لم يتحرك
        assert session.custody_account.compute_balance() == Decimal("0")

    def test_cannot_open_second_session_while_open(self, env):
        open_session(cashier_id=env["cashier"].id, opening_cash=Decimal("100"))
        _db.session.commit()
        with pytest.raises(POSError, match="مفتوحة بالفعل"):
            open_session(cashier_id=env["cashier"].id, opening_cash=Decimal("50"))

    def test_reopening_reuses_same_custody_account(self, env):
        s1 = open_session(cashier_id=env["cashier"].id, opening_cash=Decimal("100"))
        _db.session.commit()
        close_session(session_id=s1.id, closing_cash_actual=Decimal("100"))
        _db.session.commit()

        s2 = open_session(cashier_id=env["cashier"].id, opening_cash=Decimal("200"))
        _db.session.commit()
        assert s2.custody_account_id == s1.custody_account_id


# ---------- 2) البيع داخل الوردية ----------

class TestPOSSale:
    def test_cash_sale_debits_custody_not_main_cash(self, env):
        set_setting("tax.enabled", "false")
        _db.session.commit()

        session = open_session(cashier_id=env["cashier"].id, opening_cash=Decimal("0"))
        _db.session.commit()

        custody_before = session.custody_account.compute_balance()
        cash = _db.session.query(Account).filter_by(code="1010").one()
        main_cash_before = cash.compute_balance()

        inv = create_cash_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=env["variant"].id, qty=2, unit_price=50)],
            pos_session_id=session.id,
            payment_method=PaymentMethod.CASH,
        )
        _db.session.commit()

        assert inv.pos_session_id == session.id
        # عهدة الكاشير زادت 100
        assert session.custody_account.compute_balance() == custody_before + Decimal("100")
        # الصندوق الرئيسي لم يتحرك
        assert cash.compute_balance() == main_cash_before

    def test_sales_outside_session_still_use_main_cash(self, env):
        set_setting("tax.enabled", "false")
        _db.session.commit()

        cash = _db.session.query(Account).filter_by(code="1010").one()
        before = cash.compute_balance()

        create_cash_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=env["variant"].id, qty=1, unit_price=50)],
        )
        _db.session.commit()
        assert cash.compute_balance() == before + Decimal("50")


# ---------- 3) قفل الوردية والفروقات ----------

class TestCloseSession:
    def test_close_matches_expected_no_discrepancy_entry(self, env):
        set_setting("tax.enabled", "false")
        _db.session.commit()

        session = open_session(cashier_id=env["cashier"].id, opening_cash=Decimal("200"))
        _db.session.commit()

        create_cash_sale(
            customer_id=env["customer"].id, invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=env["variant"].id, qty=2, unit_price=50)],
            pos_session_id=session.id,
        )
        _db.session.commit()

        # المتوقع الآن = 200 (افتتاحي) + 100 (بيع) = 300
        # نُغلِق بمبلغ فعلي = 300 (مطابق)
        close_session(session_id=session.id, closing_cash_actual=Decimal("300"))
        _db.session.commit()

        assert session.status == SessionStatus.CLOSED
        assert session.closing_cash_expected == Decimal("300.000")
        assert session.closing_cash_actual == Decimal("300.000")
        assert session.difference == Decimal("0.000")
        # عهدة الكاشير رجعت صفر بعد التسليم للصندوق الرئيسي
        assert session.custody_account.compute_balance() == Decimal("0")

    def test_close_with_shortage_debits_discrepancy(self, env):
        set_setting("tax.enabled", "false")
        _db.session.commit()

        session = open_session(cashier_id=env["cashier"].id, opening_cash=Decimal("100"))
        _db.session.commit()

        create_cash_sale(
            customer_id=env["customer"].id, invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=env["variant"].id, qty=1, unit_price=50)],
            pos_session_id=session.id,
        )
        _db.session.commit()

        # المتوقع = 150، الفعلي = 140 → عجز 10
        close_session(session_id=session.id, closing_cash_actual=Decimal("140"))
        _db.session.commit()

        assert session.closing_cash_expected == Decimal("150.000")
        assert session.closing_cash_actual == Decimal("140.000")
        assert session.difference == Decimal("-10.000")

        # حساب فروقات صندوق 6500 له 10 مدين إضافية
        disc = _db.session.query(Account).filter_by(code="6500").one()
        # ملاحظة: 6500 مصروف — الرصيد الطبيعي مدين، لذا الرصيد الآن ≥ 10
        assert disc.compute_balance() >= Decimal("10")

    def test_close_with_surplus_credits_discrepancy(self, env):
        set_setting("tax.enabled", "false")
        _db.session.commit()

        session = open_session(cashier_id=env["cashier"].id, opening_cash=Decimal("100"))
        _db.session.commit()

        create_cash_sale(
            customer_id=env["customer"].id, invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=env["variant"].id, qty=1, unit_price=50)],
            pos_session_id=session.id,
        )
        _db.session.commit()

        # المتوقع = 150، الفعلي = 155 → زيادة 5
        close_session(session_id=session.id, closing_cash_actual=Decimal("155"))
        _db.session.commit()

        assert session.difference == Decimal("5.000")
        assert session.custody_account.compute_balance() == Decimal("0")

    def test_cannot_close_already_closed(self, env):
        session = open_session(cashier_id=env["cashier"].id, opening_cash=Decimal("100"))
        _db.session.commit()
        close_session(session_id=session.id, closing_cash_actual=Decimal("100"))
        _db.session.commit()
        with pytest.raises(POSError, match="مقفلة"):
            close_session(session_id=session.id, closing_cash_actual=Decimal("100"))


# ---------- 4) ملخّص الوردية ----------

class TestSessionSummary:
    def test_summary_counts_sales_by_method(self, env):
        set_setting("tax.enabled", "false")
        _db.session.commit()

        session = open_session(cashier_id=env["cashier"].id, opening_cash=Decimal("0"))
        _db.session.commit()

        create_cash_sale(
            customer_id=env["customer"].id, invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=env["variant"].id, qty=1, unit_price=50)],
            pos_session_id=session.id, payment_method=PaymentMethod.CASH,
        )
        create_cash_sale(
            customer_id=env["customer"].id, invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=env["variant"].id, qty=1, unit_price=50)],
            pos_session_id=session.id, payment_method=PaymentMethod.CARD,
        )
        _db.session.commit()

        summary = session_summary(session.id)
        assert summary["invoices_count"] == 2
        assert summary["total_sales"] == Decimal("100")
        assert summary["cash_sales"] == Decimal("50")
        assert summary["card_sales"] == Decimal("50")

    def test_current_open_session_lookup(self, env):
        assert current_open_session_for(env["cashier"].id) is None
        s = open_session(cashier_id=env["cashier"].id, opening_cash=Decimal("0"))
        _db.session.commit()
        assert current_open_session_for(env["cashier"].id).id == s.id
        close_session(session_id=s.id, closing_cash_actual=Decimal("0"))
        _db.session.commit()
        assert current_open_session_for(env["cashier"].id) is None
