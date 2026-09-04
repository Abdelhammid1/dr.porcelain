"""اختبارات فواتير المشتريات — 3 طرق دفع + مرتجع + متوسط التكلفة."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.models.account import Account, AccountType
from app.models.journal import JournalEntry, JournalSourceType
from app.models.party import PartyType
from app.models.purchases import PurchasePayment, PurchaseStatus
from app.models.setting import set_setting
from app.services.parties import create_party
from app.services.products import create_category, create_product
from app.services.purchases import (
    PurchaseError,
    PurchaseLineDraft,
    PurchaseReturnLineDraft,
    create_purchase_invoice,
    create_purchase_return,
)
from seeds.chart_of_accounts import seed_chart_of_accounts


@pytest.fixture()
def env(app):
    seed_chart_of_accounts(_db.session)
    _db.session.commit()

    # حساب بنك فرعي تحت 1020 (idempotent)
    bank_parent = _db.session.query(Account).filter_by(code="1020").one()
    if not _db.session.query(Account).filter_by(parent_id=bank_parent.id).first():
        _db.session.add(Account(
            code="1020-001", name_ar="بنك مصر - حساب رئيسي",
            type=AccountType.ASSET, parent_id=bank_parent.id,
            is_postable=True, is_active=True, is_system=False,
        ))
        _db.session.commit()

    # كل اختبار يحصل على مورد ومنتج جديد (بمعرّف فريد يعتمد على uuid)
    import uuid
    tag = uuid.uuid4().hex[:8]
    vendor = create_party(type=PartyType.VENDOR, name_ar=f"مورد {tag}", phone=f"010{tag}")
    cat_name = f"مطبخ-{tag}"
    cat = create_category(name_ar=cat_name)
    product = create_product(
        name_ar=f"منتج {tag}", category_id=cat.id, default_price=Decimal("50"),
        variants=[{"variant_name": "افتراضي"}],
    )
    variant = product.variants[0]
    _db.session.commit()
    yield {"vendor": vendor, "variant": variant}


# ---------- 1) الطرق الثلاث ----------

class TestPaymentMethods:
    def test_cash_purchase_credits_cash_account(self, env):
        set_setting("tax.enabled", "false")
        _db.session.commit()

        inv = create_purchase_invoice(
            vendor_id=env["vendor"].id,
            invoice_date=date(2026, 1, 1),
            payment_method=PurchasePayment.CASH,
            lines=[PurchaseLineDraft(variant_id=env["variant"].id, qty=10, unit_cost=15)],
        )
        _db.session.commit()

        assert inv.total == Decimal("150.000")
        # القيد: مدين 1100 = 150 / دائن 1010 = 150
        entries = (
            _db.session.query(JournalEntry)
            .filter_by(source_id=inv.id, source_type=JournalSourceType.PURCHASE_INVOICE)
            .all()
        )
        assert len(entries) == 1
        lines_by_code = {ln.account.code: (ln.debit, ln.credit) for ln in entries[0].lines}
        assert lines_by_code["1100"][0] == Decimal("150.000")  # inventory debit
        assert lines_by_code["1010"][1] == Decimal("150.000")  # cash credit
        # لا شيء على المورد
        assert env["vendor"].account.compute_balance() == Decimal("0")

    def test_bank_purchase_credits_bank_account(self, env):
        set_setting("tax.enabled", "false")
        _db.session.commit()

        inv = create_purchase_invoice(
            vendor_id=env["vendor"].id,
            invoice_date=date(2026, 1, 1),
            payment_method=PurchasePayment.BANK,
            lines=[PurchaseLineDraft(variant_id=env["variant"].id, qty=5, unit_cost=20)],
        )
        _db.session.commit()

        assert inv.total == Decimal("100.000")
        assert inv.bank_account_id is not None
        entries = (
            _db.session.query(JournalEntry)
            .filter_by(source_id=inv.id, source_type=JournalSourceType.PURCHASE_INVOICE)
            .all()
        )
        codes = [ln.account.code for e in entries for ln in e.lines]
        assert any(c.startswith("1020-") for c in codes)
        # لا شيء على المورد ولا النقدية
        assert env["vendor"].account.compute_balance() == Decimal("0")

    def test_credit_purchase_credits_vendor_ap(self, env):
        set_setting("tax.enabled", "false")
        _db.session.commit()

        vendor = env["vendor"]
        inv = create_purchase_invoice(
            vendor_id=vendor.id,
            invoice_date=date(2026, 1, 1),
            payment_method=PurchasePayment.CREDIT,
            lines=[PurchaseLineDraft(variant_id=env["variant"].id, qty=10, unit_cost=30)],
        )
        _db.session.commit()

        assert inv.total == Decimal("300.000")
        # رصيد المورد الآن دائن 300 (نحن مدينون له)
        assert vendor.account.compute_balance() == Decimal("300.000")


# ---------- 2) الأثر على متوسط التكلفة والمخزون ----------

class TestInventoryImpact:
    def test_stock_increases_and_avg_cost_recomputed(self, env):
        set_setting("tax.enabled", "false")
        _db.session.commit()

        variant = env["variant"]
        # شراء أول: 10 @ 20
        create_purchase_invoice(
            vendor_id=env["vendor"].id,
            invoice_date=date(2026, 1, 1),
            payment_method=PurchasePayment.CASH,
            lines=[PurchaseLineDraft(variant_id=variant.id, qty=10, unit_cost=20)],
        )
        _db.session.commit()
        assert variant.stock_qty == Decimal("10.000")
        assert variant.avg_cost == Decimal("20.000")

        # شراء ثاني: 10 @ 30 → avg = (200+300)/20 = 25
        create_purchase_invoice(
            vendor_id=env["vendor"].id,
            invoice_date=date(2026, 1, 2),
            payment_method=PurchasePayment.CASH,
            lines=[PurchaseLineDraft(variant_id=variant.id, qty=10, unit_cost=30)],
        )
        _db.session.commit()
        assert variant.stock_qty == Decimal("20.000")
        assert variant.avg_cost == Decimal("25.000")

    def test_freight_capitalizes_into_avg_cost(self, env):
        """شراء 10 @ 20 = 200 + شحن 50 → avg cost = 250/10 = 25"""
        set_setting("tax.enabled", "false")
        _db.session.commit()

        variant = env["variant"]
        create_purchase_invoice(
            vendor_id=env["vendor"].id,
            invoice_date=date(2026, 1, 1),
            payment_method=PurchasePayment.CASH,
            lines=[PurchaseLineDraft(variant_id=variant.id, qty=10, unit_cost=20)],
            freight=Decimal("50"),
        )
        _db.session.commit()
        assert variant.avg_cost == Decimal("25.000")


# ---------- 3) الضريبة والخصم ----------

class TestTaxAndDiscount:
    def test_vat_input_recorded_when_enabled(self, env):
        set_setting("tax.enabled", "true")
        set_setting("tax.default_rate", "14.000")
        _db.session.commit()

        inv = create_purchase_invoice(
            vendor_id=env["vendor"].id,
            invoice_date=date(2026, 1, 1),
            payment_method=PurchasePayment.CREDIT,
            lines=[PurchaseLineDraft(variant_id=env["variant"].id, qty=1, unit_cost=100)],
        )
        _db.session.commit()

        assert inv.tax_amount == Decimal("14.000")
        assert inv.total == Decimal("114.000")

        entries = (
            _db.session.query(JournalEntry)
            .filter_by(source_id=inv.id, source_type=JournalSourceType.PURCHASE_INVOICE)
            .all()
        )
        codes = [ln.account.code for e in entries for ln in e.lines]
        assert "1300" in codes  # VAT input

    def test_discount_credited_to_5120(self, env):
        set_setting("tax.enabled", "false")
        _db.session.commit()

        inv = create_purchase_invoice(
            vendor_id=env["vendor"].id,
            invoice_date=date(2026, 1, 1),
            payment_method=PurchasePayment.CREDIT,
            lines=[PurchaseLineDraft(variant_id=env["variant"].id, qty=10, unit_cost=20)],
            discount_amount=Decimal("30"),
        )
        _db.session.commit()

        # total = 200 - 30 = 170 (بدون ضريبة)
        assert inv.total == Decimal("170.000")

        entries = (
            _db.session.query(JournalEntry)
            .filter_by(source_id=inv.id, source_type=JournalSourceType.PURCHASE_INVOICE)
            .all()
        )
        codes = [ln.account.code for e in entries for ln in e.lines]
        assert "5120" in codes  # خصم مكتسب


# ---------- 4) التحقق ----------

class TestValidation:
    def test_empty_lines_rejected(self, env):
        with pytest.raises(PurchaseError, match="سطرًا"):
            create_purchase_invoice(
                vendor_id=env["vendor"].id, invoice_date=date(2026, 1, 1),
                payment_method=PurchasePayment.CASH, lines=[],
            )

    def test_discount_greater_than_subtotal_rejected(self, env):
        with pytest.raises(PurchaseError, match="الخصم"):
            create_purchase_invoice(
                vendor_id=env["vendor"].id, invoice_date=date(2026, 1, 1),
                payment_method=PurchasePayment.CASH,
                lines=[PurchaseLineDraft(variant_id=env["variant"].id, qty=1, unit_cost=10)],
                discount_amount=Decimal("100"),
            )


# ---------- 5) المرتجعات ----------

class TestPurchaseReturns:
    def test_full_return_reverses_everything(self, env):
        set_setting("tax.enabled", "false")
        _db.session.commit()

        variant = env["variant"]
        vendor = env["vendor"]
        inv = create_purchase_invoice(
            vendor_id=vendor.id,
            invoice_date=date(2026, 1, 1),
            payment_method=PurchasePayment.CREDIT,
            lines=[PurchaseLineDraft(variant_id=variant.id, qty=5, unit_cost=20)],
        )
        _db.session.commit()

        assert variant.stock_qty == Decimal("5.000")
        assert vendor.account.compute_balance() == Decimal("100.000")

        ret = create_purchase_return(
            invoice_id=inv.id,
            return_date=date(2026, 1, 5),
            reason="عيب في البضاعة",
        )
        _db.session.commit()
        _db.session.refresh(inv)

        assert inv.status == PurchaseStatus.RETURNED
        assert ret.is_full_return is True
        # المخزون رجع صفر
        assert variant.stock_qty == Decimal("0.000")
        # رصيد المورد رجع صفر (لم يعد له علينا شيء)
        assert vendor.account.compute_balance() == Decimal("0")

    def test_partial_return_updates_status(self, env):
        set_setting("tax.enabled", "false")
        _db.session.commit()

        variant = env["variant"]
        inv = create_purchase_invoice(
            vendor_id=env["vendor"].id, invoice_date=date(2026, 1, 1),
            payment_method=PurchasePayment.CREDIT,
            lines=[PurchaseLineDraft(variant_id=variant.id, qty=10, unit_cost=20)],
        )
        _db.session.commit()

        create_purchase_return(
            invoice_id=inv.id, return_date=date(2026, 1, 5), reason="جزء تالف",
            lines=[PurchaseReturnLineDraft(invoice_line_id=inv.lines[0].id, qty=3)],
        )
        _db.session.commit()
        _db.session.refresh(inv)

        assert inv.status == PurchaseStatus.PARTIAL_RETURNED
        assert variant.stock_qty == Decimal("7.000")
