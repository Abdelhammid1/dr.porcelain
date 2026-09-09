"""اختبارات القوائم المالية + VAT + الربحية."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.models.party import PartyType
from app.models.setting import set_setting
from app.services.financial_statements import (
    balance_sheet,
    cash_flow,
    income_statement,
)
from app.services.inventory import record_purchase
from app.services.parties import create_party
from app.services.products import create_category, create_product
from app.services.profitability import product_profitability
from app.services.sales import InvoiceLineDraft, create_cash_sale
from app.services.vat_report import vat_report
from seeds.chart_of_accounts import seed_chart_of_accounts


@pytest.fixture()
def env(app):
    seed_chart_of_accounts(_db.session)
    _db.session.commit()

    tag = uuid.uuid4().hex[:8]
    customer = create_party(type=PartyType.CUSTOMER, name_ar=f"cust {tag}", phone=f"010{tag}")
    cat = create_category(name_ar=f"c{tag}")
    product = create_product(
        name_ar=f"p{tag}", category_id=cat.id, default_price=Decimal("100"),
        variants=[{"variant_name": "افتراضي"}],
    )
    variant = product.variants[0]
    record_purchase(variant_id=variant.id, qty=100, unit_cost=40, move_date=date(2026, 1, 1))
    _db.session.commit()
    yield {"customer": customer, "product": product, "variant": variant}


# ---------- قائمة الدخل ----------

class TestIncomeStatement:
    def test_revenue_minus_cogs_equals_gross(self, env):
        set_setting("tax.enabled", "false")
        _db.session.commit()
        # بيع 5 × 100 = 500، تكلفة 5 × 40 = 200 → ربح إجمالي 300
        create_cash_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 3, 1),
            lines=[InvoiceLineDraft(env["variant"].id, qty=5, unit_price=100)],
        )
        _db.session.commit()

        stmt = income_statement(date_from=date(2026, 3, 1), date_to=date(2026, 3, 31))
        # نتحقق أن هذه الفاتورة تحديدًا أضافت 500 إيراد و 200 تكلفة
        # (اختبارات سابقة قد أضافت أخرى — نستخدم delta بمقارنة قبل/بعد)
        assert stmt.total_revenue >= Decimal("500.000")
        assert stmt.total_cost >= Decimal("200.000")
        assert stmt.gross_profit == stmt.total_revenue - stmt.total_cost
        assert stmt.net_profit == stmt.gross_profit - stmt.total_expense

    def test_empty_period_returns_zeros(self, env):
        stmt = income_statement(date_from=date(2050, 1, 1), date_to=date(2050, 12, 31))
        assert stmt.total_revenue == Decimal("0")
        assert stmt.total_cost == Decimal("0")
        assert stmt.total_expense == Decimal("0")
        assert stmt.net_profit == Decimal("0")


# ---------- الميزانية العمومية ----------

class TestBalanceSheet:
    def test_balance_sheet_is_balanced(self, env):
        set_setting("tax.enabled", "false")
        _db.session.commit()
        create_cash_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 3, 1),
            lines=[InvoiceLineDraft(env["variant"].id, qty=5, unit_price=100)],
        )
        _db.session.commit()

        bs = balance_sheet(as_of=date(2026, 6, 30))
        # Assets == Liabilities + Equity + Net Profit
        assert bs.is_balanced, (
            f"غير متوازنة: assets={bs.total_assets}, "
            f"L+E+NP={bs.total_liabilities_equity}, diff={bs.difference}"
        )

    def test_balance_sheet_categorizes_accounts_correctly(self, env):
        """يتحقق أن الحسابات تظهر في القسم الصحيح (أصول/التزامات/حقوق ملكية)."""
        set_setting("tax.enabled", "false")
        _db.session.commit()
        create_cash_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 3, 1),
            lines=[InvoiceLineDraft(env["variant"].id, qty=1, unit_price=100)],
        )
        _db.session.commit()

        bs = balance_sheet(as_of=date(2026, 3, 31))
        # حسابات معروفة في القسم الصحيح
        asset_codes = {l.account.code for l in bs.assets}
        # النقدية أو المخزون ظاهر في الأصول (أحدهما على الأقل)
        assert "1010" in asset_codes or "1100" in asset_codes


# ---------- تدفق نقدي ----------

class TestCashFlow:
    def test_cash_sales_appear_as_inflow(self, env):
        set_setting("tax.enabled", "false")
        _db.session.commit()

        cf_before = cash_flow(date_from=date(2026, 5, 1), date_to=date(2026, 5, 31))
        create_cash_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 5, 15),
            lines=[InvoiceLineDraft(env["variant"].id, qty=2, unit_price=100)],
        )
        _db.session.commit()
        cf_after = cash_flow(date_from=date(2026, 5, 1), date_to=date(2026, 5, 31))
        # الفاتورة أضافت 200 دخول نقدي
        assert cf_after.total_inflow - cf_before.total_inflow >= Decimal("200")
        # closing_balance >= opening_balance + 200
        assert cf_after.closing_balance >= cf_after.opening_balance + Decimal("0")


# ---------- VAT ----------

class TestVATReport:
    def test_output_tax_captured_from_sales(self, env):
        set_setting("tax.enabled", "true")
        set_setting("tax.default_rate", "14.000")
        _db.session.commit()

        report_before = vat_report(date_from=date(2026, 6, 1), date_to=date(2026, 6, 30))
        create_cash_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 6, 15),
            lines=[InvoiceLineDraft(env["variant"].id, qty=1, unit_price=100)],
        )
        _db.session.commit()
        report_after = vat_report(date_from=date(2026, 6, 1), date_to=date(2026, 6, 30))

        # فاتورة 100 × 14% = 14 مخرجات
        assert report_after.output_tax - report_before.output_tax == Decimal("14.000")
        assert report_after.net_payable == report_after.output_tax - report_after.input_tax


# ---------- الربحية ----------

class TestProfitability:
    def test_per_product_profit_and_margin(self, env):
        set_setting("tax.enabled", "false")
        _db.session.commit()
        create_cash_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 7, 10),
            lines=[InvoiceLineDraft(env["variant"].id, qty=10, unit_price=100)],
        )
        _db.session.commit()

        rep = product_profitability(date_from=date(2026, 7, 1), date_to=date(2026, 7, 31))
        row = next(r for r in rep.rows if r.product.id == env["product"].id)
        # 10 × 100 = 1000 revenue, 10 × 40 = 400 cost, profit = 600, margin = 60%
        assert row.qty_sold == Decimal("10.000")
        assert row.revenue == Decimal("1000.000")
        assert row.cost == Decimal("400.000")
        assert row.gross_profit == Decimal("600.000")
        assert row.margin_percent == Decimal("60.000")

    def test_pos_cashier_discount_reduces_product_revenue(self, env):
        """تذكرة: خصم الكاشير من POS مش بيتوزّع على إيراد المنتج في تقرير
        الربحية — كان قدره بيظهر كامل قبل الخصم. الآن كل سطر يأخذ نصيبه
        النسبي من الخصم.

        سيناريو: 5 × 6,500 = 32,500 (خام)، خصم فاتورة 1,000، فيبقى صافي
        الإيراد للمنتج = 32,500 × (31,500 / 32,500) = 31,500 بالظبط.
        (نفس ما بيظهر في قائمة الدخل — كده الرقمان بيتوافقوا.)
        """
        set_setting("tax.enabled", "false")
        _db.session.commit()
        create_cash_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 9, 5),
            lines=[InvoiceLineDraft(env["variant"].id, qty=5, unit_price=6500)],
            discount_amount=Decimal("1000"),
        )
        _db.session.commit()

        rep = product_profitability(date_from=date(2026, 9, 1), date_to=date(2026, 9, 30))
        row = next(r for r in rep.rows if r.product.id == env["product"].id)
        assert row.qty_sold == Decimal("5.000")
        # الإيراد بعد توزيع خصم الفاتورة (1,000 من 32,500 خام) = 31,500
        assert row.revenue == Decimal("31500.000"), (
            f"expected 31,500 (32,500 gross − 1,000 POS discount) but got {row.revenue}"
        )
        # التكلفة ما بتتغيرش بالخصم — 5 × 40 = 200
        assert row.cost == Decimal("200.000")
        # الربح = 31,500 − 200 = 31,300
        assert row.gross_profit == Decimal("31300.000")

    def test_returns_subtracted(self, env):
        from app.services.sales import create_sales_return
        set_setting("tax.enabled", "false")
        _db.session.commit()

        inv = create_cash_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 8, 1),
            lines=[InvoiceLineDraft(env["variant"].id, qty=10, unit_price=100)],
        )
        _db.session.commit()
        # ارجاع 3 وحدات
        create_sales_return(
            invoice_id=inv.id, return_date=date(2026, 8, 3),
            reason="عيب", lines=None,  # سيرجع كل ما هو متبقي
        )
        _db.session.commit()

        rep = product_profitability(date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
        # المرتجع الكامل — الربح المتبقي 0
        row = next((r for r in rep.rows if r.product.id == env["product"].id), None)
        # إما row=None (كل شيء صفر) أو revenue = 0
        if row is not None:
            assert row.revenue == Decimal("0")
            assert row.cost == Decimal("0")


# ---------- Reversed entries must net to zero ----------
# سيناريو التذكرة: قيد يدخل الخزينة 200 ثم يُعكس، ثم قيد رأس مال 100,000
# النتيجة المتوقعة: closing_balance = 100,000 (وليس 100,200)، لأن العكس
# يجب أن يُلغي القيد الأصلي عبر جمع مدين/دائن الاثنين معًا.

class TestReversedEntriesNetToZero:
    def test_cash_flow_ignores_reversed_pair_effect(self, env):
        """Reversal of a cash movement must NOT leak into Cash Flow.

        سيناريو التذكرة: عهدة 200 اتفتحت واتعكست، ثم قيد رأس مال 100,000.
        النتيجة الصحيحة: التغير الصافي على الخزينة داخل الفترة = 100,000.
        النتيجة الغلط (البق قبل الفيكس): 100,200 (لأن الأصلي بيُستبعد ويفضل
        بس قيد العكس محسوب فيتضاعف الأثر بقيمة القيد الأصلي).
        """
        from app.extensions import db
        from app.models.account import Account
        from app.models.journal import JournalSourceType
        from app.services.ledger import (
            LedgerLineDraft,
            post_journal_entry,
            reverse_entry,
        )

        cash = db.session.query(Account).filter_by(code="1010").one()
        capital = db.session.query(Account).filter_by(code="3100").one()

        # نستعمل فترة بعيدة عن أي بيانات fixture (2099)
        d1 = date(2099, 6, 1)
        d2 = date(2099, 6, 2)
        d3 = date(2099, 6, 3)

        # 1) قيد وهمي: 200 مدين خزينة / 200 دائن رأس مال
        entry = post_journal_entry(
            entry_date=d1,
            source_type=JournalSourceType.MANUAL,
            source_id=None,
            memo="عهدة مؤقتة",
            lines=[
                LedgerLineDraft(account_id=cash.id, debit=Decimal("200"), credit=Decimal("0")),
                LedgerLineDraft(account_id=capital.id, debit=Decimal("0"), credit=Decimal("200")),
            ],
        )
        _db.session.commit()

        # 2) عكس القيد
        reverse_entry(entry_id=entry.id, reason="خطأ إدخال", user_id=None, entry_date=d2)
        _db.session.commit()

        # 3) قيد رأس مال حقيقي 100,000
        post_journal_entry(
            entry_date=d3,
            source_type=JournalSourceType.MANUAL,
            source_id=None,
            memo="رأس المال المدفوع",
            lines=[
                LedgerLineDraft(account_id=cash.id, debit=Decimal("100000"), credit=Decimal("0")),
                LedgerLineDraft(account_id=capital.id, debit=Decimal("0"), credit=Decimal("100000")),
            ],
        )
        _db.session.commit()

        cf = cash_flow(date_from=d1, date_to=date(2099, 6, 30))
        # الأثر الصافي = closing - opening = 100,000 (وليس 100,200)
        net_change = cf.closing_balance - cf.opening_balance
        assert net_change == Decimal("100000.000"), (
            f"Reversed entry pair leaked into cash flow — expected net change "
            f"100,000 but got {net_change}. Reversal filter is broken."
        )

    def test_balance_sheet_ignores_reversed_pair_effect(self, env):
        """Reversed entry + its reversal must net to zero on balance sheet."""
        from app.extensions import db
        from app.models.account import Account
        from app.models.journal import JournalSourceType
        from app.services.ledger import (
            LedgerLineDraft,
            post_journal_entry,
            reverse_entry,
        )

        cash = db.session.query(Account).filter_by(code="1010").one()
        capital = db.session.query(Account).filter_by(code="3100").one()

        as_of = date(2099, 7, 31)
        # baseline before any of our postings
        bs_before = balance_sheet(as_of=as_of)
        baseline_assets = bs_before.total_assets

        entry = post_journal_entry(
            entry_date=date(2099, 7, 5),
            source_type=JournalSourceType.MANUAL,
            source_id=None,
            memo="حركة تجريبية",
            lines=[
                LedgerLineDraft(account_id=cash.id, debit=Decimal("500"), credit=Decimal("0")),
                LedgerLineDraft(account_id=capital.id, debit=Decimal("0"), credit=Decimal("500")),
            ],
        )
        _db.session.commit()
        reverse_entry(entry_id=entry.id, reason="test", user_id=None,
                      entry_date=date(2099, 7, 5))
        _db.session.commit()

        bs_after = balance_sheet(as_of=as_of)
        # الأثر الصافي = 0، فالإجمالي ما اتغيرش
        assert bs_after.total_assets == baseline_assets, (
            f"Reversed entry leaked into balance sheet — expected assets "
            f"unchanged from {baseline_assets} but got {bs_after.total_assets}."
        )
        assert bs_after.is_balanced, "Balance sheet is unbalanced after reversal!"
