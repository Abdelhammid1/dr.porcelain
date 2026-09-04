"""اختبارات فواتير المبيعات — القيود الذرية، المرتجعات، معايير القبول."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.models.account import Account
from app.models.journal import JournalEntry, JournalEntryStatus, JournalSourceType
from app.models.party import PartyType
from app.models.sales import InvoiceStatus, SalesInvoice
from app.models.setting import set_setting
from app.services.inventory import record_purchase
from app.services.parties import create_party
from app.services.products import create_category, create_product
from app.services.sales import (
    InvoiceLineDraft,
    ReturnLineDraft,
    SalesError,
    create_cash_sale,
    create_sales_return,
)
from seeds.chart_of_accounts import seed_chart_of_accounts


# ---------- Fixtures ----------

@pytest.fixture()
def env(app):
    seed_chart_of_accounts(_db.session)
    _db.session.commit()

    # عميل + منتج بكمية مخزون + بنك فرعي (لسيناريو BANK لو احتجناه)
    customer = create_party(type=PartyType.CUSTOMER, name_ar="عميل اختبار")
    cat = create_category(name_ar="مطبخ")
    product = create_product(
        name_ar="طبق سيراميك",
        category_id=cat.id,
        default_price=Decimal("50"),
        variants=[{"variant_name": "افتراضي"}],
    )
    variant = product.variants[0]
    # نشتري 20 قطعة بـ 30 (avg_cost = 30)
    record_purchase(variant_id=variant.id, qty=20, unit_cost=30, move_date=date(2026, 1, 1))
    _db.session.commit()
    yield {"customer": customer, "product": product, "variant": variant}


# ---------- 1) فاتورة كاش بسيطة (لا ضريبة، لا خصم) ----------

class TestSimpleCashSale:
    def test_creates_invoice_lines_and_journals(self, env):
        inv = create_cash_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=env["variant"].id, qty=3, unit_price=50)],
        )
        _db.session.commit()

        assert inv.doc_number.startswith("INV-")
        assert inv.status == InvoiceStatus.POSTED
        assert inv.subtotal == Decimal("150.000")
        assert inv.discount_amount == Decimal("0.000")
        assert inv.tax_amount == Decimal("0.000")
        assert inv.total == Decimal("150.000")
        assert len(inv.lines) == 1
        line = inv.lines[0]
        assert line.qty == Decimal("3.000")
        assert line.unit_price == Decimal("50.000")
        assert line.line_total == Decimal("150.000")
        assert line.unit_cost == Decimal("30.000")  # snapshot من avg_cost

    def test_stock_is_deducted(self, env):
        variant = env["variant"]
        stock_before = Decimal(str(variant.stock_qty))
        create_cash_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=variant.id, qty=5)],
        )
        _db.session.commit()
        assert variant.stock_qty == stock_before - Decimal("5")

    def test_three_journals_created(self, env):
        inv = create_cash_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=env["variant"].id, qty=2, unit_price=50)],
        )
        _db.session.commit()

        # القيود المرتبطة بالفاتورة (source_id=inv.id)
        entries = (
            _db.session.query(JournalEntry)
            .filter(JournalEntry.source_id == inv.id)
            .all()
        )
        # 3 قيود على الأقل: sale, receipt, cogs
        source_types = [e.source_type.value for e in entries]
        assert "sales_invoice" in source_types
        assert "customer_receipt" in source_types
        # COGS يستخدم source_type='sales_invoice' أيضًا
        assert source_types.count("sales_invoice") == 2
        assert source_types.count("customer_receipt") == 1

        # كلها متوازنة
        for e in entries:
            assert e.is_balanced, f"غير متوازن: {e.doc_number}"

    def test_customer_ar_ends_at_zero_for_cash_sale(self, env):
        """رصيد العميل قبل وبعد فاتورة كاش يجب أن يكون صفرًا."""
        customer = env["customer"]
        assert customer.account.compute_balance() == Decimal("0")

        create_cash_sale(
            customer_id=customer.id,
            invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=env["variant"].id, qty=1, unit_price=50)],
        )
        _db.session.commit()

        assert customer.account.compute_balance() == Decimal("0")


# ---------- 2) الضريبة والخصم ----------

class TestTaxAndDiscount:
    def test_tax_applied_when_enabled(self, env):
        set_setting("tax.enabled", "true")
        set_setting("tax.default_rate", "14.000")
        _db.session.commit()

        inv = create_cash_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=env["variant"].id, qty=1, unit_price=100)],
        )
        _db.session.commit()

        # 100 + 14% = 114
        assert inv.subtotal == Decimal("100.000")
        assert inv.tax_amount == Decimal("14.000")
        assert inv.total == Decimal("114.000")

    def test_tax_zero_when_disabled(self, env):
        set_setting("tax.enabled", "false")
        _db.session.commit()

        inv = create_cash_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=env["variant"].id, qty=1, unit_price=100)],
        )
        _db.session.commit()
        assert inv.tax_amount == Decimal("0.000")
        assert inv.total == Decimal("100.000")

    def test_discount_applied(self, env):
        set_setting("tax.enabled", "false")
        _db.session.commit()

        inv = create_cash_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=env["variant"].id, qty=1, unit_price=100)],
            discount_amount=Decimal("20"),
        )
        _db.session.commit()
        assert inv.subtotal == Decimal("100.000")
        assert inv.discount_amount == Decimal("20.000")
        assert inv.total == Decimal("80.000")

    def test_discount_and_tax_combined(self, env):
        set_setting("tax.enabled", "true")
        set_setting("tax.default_rate", "14.000")
        _db.session.commit()

        inv = create_cash_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=env["variant"].id, qty=1, unit_price=100)],
            discount_amount=Decimal("10"),
        )
        _db.session.commit()
        # (100 - 10) * 1.14 = 102.6
        assert inv.subtotal == Decimal("100.000")
        assert inv.discount_amount == Decimal("10.000")
        assert inv.tax_amount == Decimal("12.600")
        assert inv.total == Decimal("102.600")

    def test_discount_greater_than_subtotal_rejected(self, env):
        with pytest.raises(SalesError, match="الخصم"):
            create_cash_sale(
                customer_id=env["customer"].id,
                invoice_date=date(2026, 2, 1),
                lines=[InvoiceLineDraft(variant_id=env["variant"].id, qty=1, unit_price=50)],
                discount_amount=Decimal("100"),
            )


# ---------- 3) قواعد الرفض والذرية ----------

class TestValidation:
    def test_empty_lines_rejected(self, env):
        with pytest.raises(SalesError, match="سطرًا"):
            create_cash_sale(
                customer_id=env["customer"].id,
                invoice_date=date(2026, 2, 1),
                lines=[],
            )

    def test_inactive_customer_rejected(self, env):
        env["customer"].is_active = False
        _db.session.flush()
        with pytest.raises(SalesError, match="موقوف"):
            create_cash_sale(
                customer_id=env["customer"].id,
                invoice_date=date(2026, 2, 1),
                lines=[InvoiceLineDraft(variant_id=env["variant"].id, qty=1)],
            )
        _db.session.rollback()

    def test_over_stock_rejected(self, env):
        with pytest.raises(SalesError, match="أقل من المطلوب"):
            create_cash_sale(
                customer_id=env["customer"].id,
                invoice_date=date(2026, 2, 1),
                lines=[InvoiceLineDraft(variant_id=env["variant"].id, qty=1000)],
            )

    def test_atomicity_on_failure(self, env):
        """محاولة فاتورة بسطر صحيح وسطر خاطئ → لا يُنشأ أي شيء."""
        variant = env["variant"]
        stock_before = Decimal(str(variant.stock_qty))
        try:
            create_cash_sale(
                customer_id=env["customer"].id,
                invoice_date=date(2026, 2, 1),
                lines=[
                    InvoiceLineDraft(variant_id=variant.id, qty=1),
                    InvoiceLineDraft(variant_id=variant.id, qty=99999),  # يفشل
                ],
            )
        except SalesError:
            _db.session.rollback()
        # المخزون لم يتغير
        _db.session.refresh(variant)
        assert variant.stock_qty == stock_before


# ---------- 4) المرتجعات ----------

class TestReturns:
    def test_full_return_reverses_everything(self, env):
        set_setting("tax.enabled", "false")
        _db.session.commit()

        variant = env["variant"]
        customer = env["customer"]
        stock_before = Decimal(str(variant.stock_qty))

        inv = create_cash_sale(
            customer_id=customer.id,
            invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=variant.id, qty=5, unit_price=50)],
        )
        _db.session.commit()

        # الرصيد بعد البيع: نُقص 5 (250 EGP total)
        assert variant.stock_qty == stock_before - Decimal("5")

        ret = create_sales_return(
            invoice_id=inv.id,
            return_date=date(2026, 2, 2),
            reason="عيب في المنتج",
        )
        _db.session.commit()

        assert ret.doc_number.startswith("SR-")
        assert ret.is_full_return is True
        assert ret.refund_amount == Decimal("250.000")

        _db.session.refresh(inv)
        assert inv.status == InvoiceStatus.RETURNED

        # المخزون عاد للأصل
        _db.session.refresh(variant)
        assert variant.stock_qty == stock_before

        # رصيد العميل رجع صفر (كان صفر بعد الكاش، رجع صفر بعد المرتجع)
        assert customer.account.compute_balance() == Decimal("0")

        # القيود المرتبطة تحديدًا بهذه الفاتورة والمرتجع كلها متوازنة
        from app.models.journal import JournalEntry
        related = (
            _db.session.query(JournalEntry)
            .filter(JournalEntry.source_id.in_([inv.id, ret.id]))
            .all()
        )
        for e in related:
            assert e.is_balanced, f"غير متوازن: {e.doc_number}"

    def test_partial_return_creates_partial_status(self, env):
        set_setting("tax.enabled", "false")
        set_setting("returns.allow_partial", "true")
        _db.session.commit()

        variant = env["variant"]
        inv = create_cash_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=variant.id, qty=10, unit_price=50)],
        )
        _db.session.commit()

        create_sales_return(
            invoice_id=inv.id,
            return_date=date(2026, 2, 3),
            reason="ارتجاع 3 قطع",
            lines=[ReturnLineDraft(invoice_line_id=inv.lines[0].id, qty=3)],
        )
        _db.session.commit()

        _db.session.refresh(inv)
        assert inv.status == InvoiceStatus.PARTIAL_RETURNED
        assert inv.line_returnable_qty(inv.lines[0]) == Decimal("7")

    def test_cannot_return_more_than_remaining(self, env):
        set_setting("tax.enabled", "false")
        _db.session.commit()

        variant = env["variant"]
        inv = create_cash_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=variant.id, qty=4, unit_price=50)],
        )
        _db.session.commit()

        with pytest.raises(SalesError, match="المتبقي"):
            create_sales_return(
                invoice_id=inv.id,
                return_date=date(2026, 2, 3),
                reason="محاولة تجاوز الكمية",
                lines=[ReturnLineDraft(invoice_line_id=inv.lines[0].id, qty=10)],
            )

    def test_reason_required_for_return(self, env):
        variant = env["variant"]
        inv = create_cash_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=variant.id, qty=1)],
        )
        _db.session.commit()
        with pytest.raises(SalesError, match="سبب"):
            create_sales_return(
                invoice_id=inv.id, return_date=date(2026, 2, 3), reason="   ",
            )

    def test_cannot_return_fully_returned_invoice(self, env):
        variant = env["variant"]
        inv = create_cash_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=variant.id, qty=2)],
        )
        _db.session.commit()
        create_sales_return(
            invoice_id=inv.id, return_date=date(2026, 2, 3),
            reason="ok",
        )
        _db.session.commit()
        with pytest.raises(SalesError, match="مرتجعة"):
            create_sales_return(
                invoice_id=inv.id, return_date=date(2026, 2, 4),
                reason="مرة تانية",
            )

    def test_return_window_enforced(self, env):
        set_setting("returns.window_days", "7")
        _db.session.commit()

        variant = env["variant"]
        inv = create_cash_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=variant.id, qty=1)],
        )
        _db.session.commit()

        # 10 أيام بعد الفاتورة - يفوت المدة
        with pytest.raises(SalesError, match="مدة"):
            create_sales_return(
                invoice_id=inv.id, return_date=date(2026, 2, 11),
                reason="بعد المدة",
            )

        # داخل المدة يمر
        create_sales_return(
            invoice_id=inv.id, return_date=date(2026, 2, 5),
            reason="داخل المدة",
        )
        _db.session.commit()
