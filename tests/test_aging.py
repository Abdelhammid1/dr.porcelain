"""اختبارات تقارير أعمار الديون (AP + AR)."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.models.account import Account, AccountType
from app.models.installment import InstallmentFrequency
from app.models.party import PartyType
from app.models.purchases import PurchasePayment
from app.models.setting import set_setting
from app.services.aging import ap_aging, ar_aging
from app.services.installments import (
    InvoiceLineDraft,
    create_installment_sale,
)
from app.services.parties import create_party
from app.services.products import create_category, create_product
from app.services.inventory import record_purchase
from app.services.purchases import PurchaseLineDraft, create_purchase_invoice
from app.services.vendor_payments import (
    ScheduleLineDraft,
    attach_payment_schedule,
    record_vendor_payment,
)
from seeds.chart_of_accounts import seed_chart_of_accounts


@pytest.fixture()
def env(app):
    seed_chart_of_accounts(_db.session)
    set_setting("tax.enabled", "false")
    _db.session.commit()

    tag = uuid.uuid4().hex[:8]
    customer = create_party(type=PartyType.CUSTOMER, name_ar=f"cust {tag}", phone=f"010{tag}")
    vendor = create_party(type=PartyType.VENDOR, name_ar=f"vend {tag}", phone=f"011{tag}")
    cat = create_category(name_ar=f"c{tag}")
    product = create_product(
        name_ar=f"p{tag}", category_id=cat.id, default_price=Decimal("100"),
        variants=[{"variant_name": "افتراضي"}],
    )
    variant = product.variants[0]
    record_purchase(variant_id=variant.id, qty=100, unit_cost=50, move_date=date(2026, 1, 1))
    _db.session.commit()
    yield {"customer": customer, "vendor": vendor, "variant": variant}


# ---------- AP Aging ----------

class TestAPAging:
    def test_lines_bucketed_by_age(self, env):
        # فاتورة شراء آجلة بجدول من 3 أسطر بتواريخ متفرقة
        inv = create_purchase_invoice(
            vendor_id=env["vendor"].id,
            invoice_date=date(2026, 1, 1),
            payment_method=PurchasePayment.CREDIT,
            lines=[PurchaseLineDraft(env["variant"].id, qty=10, unit_cost=100)],  # 1000
        )
        _db.session.commit()
        attach_payment_schedule(
            purchase_invoice_id=inv.id,
            lines=[
                ScheduleLineDraft(due_date=date(2026, 3, 20), amount=Decimal("300")),  # 10 days late (as of 3/30)
                ScheduleLineDraft(due_date=date(2026, 2, 25), amount=Decimal("400")),  # 33 days late
                ScheduleLineDraft(due_date=date(2026, 1, 10), amount=Decimal("300")),  # 79 days late
            ],
        )
        _db.session.commit()

        report = ap_aging(as_of=date(2026, 3, 30))
        # سطر واحد للمورد
        vendor_rows = [r for r in report["rows"] if r.party.id == env["vendor"].id]
        assert len(vendor_rows) == 1
        b = vendor_rows[0].bucket
        assert b.not_yet_due == Decimal("0")
        assert b.days_0_30 == Decimal("300.000")    # 3/20 late by 10 days
        assert b.days_31_60 == Decimal("400.000")   # 2/25 late by 33 days
        assert b.days_over_60 == Decimal("300.000") # 1/10 late by 79 days
        assert b.total == Decimal("1000.000")

    def test_paid_amount_excluded(self, env):
        inv = create_purchase_invoice(
            vendor_id=env["vendor"].id,
            invoice_date=date(2026, 1, 1),
            payment_method=PurchasePayment.CREDIT,
            lines=[PurchaseLineDraft(env["variant"].id, qty=5, unit_cost=100)],  # 500
        )
        _db.session.commit()
        sched = attach_payment_schedule(
            purchase_invoice_id=inv.id,
            lines=[
                ScheduleLineDraft(due_date=date(2026, 2, 1), amount=Decimal("200")),
                ScheduleLineDraft(due_date=date(2026, 3, 1), amount=Decimal("300")),
            ],
        )
        _db.session.commit()

        # سدِّد أول قسط
        record_vendor_payment(
            schedule_line_id=sched.lines[0].id, amount=Decimal("200"),
            payment_date=date(2026, 2, 5),
        )
        _db.session.commit()

        report = ap_aging(as_of=date(2026, 4, 1))
        row = next(r for r in report["rows"] if r.party.id == env["vendor"].id)
        # فقط 300 المتبقية من القسط الثاني (متأخر شهر واحد)
        assert row.bucket.total == Decimal("300.000")

    def test_not_yet_due_bucket_for_future_dates(self, env):
        inv = create_purchase_invoice(
            vendor_id=env["vendor"].id,
            invoice_date=date(2026, 1, 1),
            payment_method=PurchasePayment.CREDIT,
            lines=[PurchaseLineDraft(env["variant"].id, qty=5, unit_cost=100)],
        )
        _db.session.commit()
        attach_payment_schedule(
            purchase_invoice_id=inv.id,
            lines=[ScheduleLineDraft(due_date=date(2026, 6, 1), amount=Decimal("500"))],
        )
        _db.session.commit()

        report = ap_aging(as_of=date(2026, 3, 1))  # قبل التاريخ
        row = next(r for r in report["rows"] if r.party.id == env["vendor"].id)
        assert row.bucket.not_yet_due == Decimal("500.000")
        assert row.bucket.days_0_30 == Decimal("0")


# ---------- AR Aging ----------

class TestARAging:
    def test_installment_lines_bucketed(self, env):
        # خطة تقسيط 3 أشهر × 100
        plan = create_installment_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 1, 1),
            lines=[InvoiceLineDraft(env["variant"].id, qty=1, unit_price=300)],
            installments_count=3,
            frequency=InstallmentFrequency.MONTHLY,
        )
        _db.session.commit()
        # الأقساط: 2/1, 3/1, 4/1

        report = ar_aging(as_of=date(2026, 3, 15))
        row = next(r for r in report["rows"] if r.party.id == env["customer"].id)
        # 2/1 → متأخر 42 يوم (31-60)
        # 3/1 → متأخر 14 يوم (0-30)
        # 4/1 → لم يحن (not_yet_due)
        assert row.bucket.days_31_60 == Decimal("100.000")
        assert row.bucket.days_0_30 == Decimal("100.000")
        assert row.bucket.not_yet_due == Decimal("100.000")

    def test_paid_installments_excluded(self, env):
        from app.services.installments import collect_installment_payment
        plan = create_installment_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 1, 1),
            lines=[InvoiceLineDraft(env["variant"].id, qty=1, unit_price=300)],
            installments_count=3,
        )
        _db.session.commit()
        collect_installment_payment(
            schedule_line_id=plan.schedule[0].id, amount=Decimal("100"),
            collection_date=date(2026, 2, 5),
        )
        _db.session.commit()

        report = ar_aging(as_of=date(2026, 3, 15))
        row = next(r for r in report["rows"] if r.party.id == env["customer"].id)
        # يبقى قسطان × 100 (الأول مدفوع بالكامل)
        assert row.bucket.total == Decimal("200.000")
