"""اختبارات البيع بالتقسيط — جدول السداد، التحصيلات، المتأخرات."""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.models.account import Account
from app.models.installment import (
    InstallmentFrequency,
    InstallmentLineStatus,
    InstallmentPlanStatus,
)
from app.models.party import PartyType
from app.models.setting import set_setting
from app.services.installments import (
    InstallmentError,
    InvoiceLineDraft,
    collect_installment_payment,
    create_installment_sale,
    get_due_today_lines,
    get_overdue_lines,
    mark_overdue_installments,
)
from app.services.inventory import record_purchase
from app.services.parties import create_party
from app.services.products import create_category, create_product
from seeds.chart_of_accounts import seed_chart_of_accounts


@pytest.fixture()
def env(app):
    seed_chart_of_accounts(_db.session)
    set_setting("tax.enabled", "false")
    _db.session.commit()

    tag = uuid.uuid4().hex[:8]
    customer = create_party(type=PartyType.CUSTOMER, name_ar=f"عميل {tag}", phone=f"010{tag}")
    cat = create_category(name_ar=f"cat-{tag}")
    product = create_product(
        name_ar=f"طبق {tag}", category_id=cat.id, default_price=Decimal("100"),
        variants=[{"variant_name": "افتراضي"}],
    )
    variant = product.variants[0]
    record_purchase(variant_id=variant.id, qty=50, unit_cost=40, move_date=date(2026, 1, 1))
    _db.session.commit()
    yield {"customer": customer, "variant": variant}


# ---------- 1) بناء الخطة ----------

class TestCreateInstallmentSale:
    def test_creates_plan_with_schedule(self, env):
        plan = create_installment_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 3, 1),
            lines=[InvoiceLineDraft(env["variant"].id, qty=1, unit_price=1200)],
            installments_count=6,
            frequency=InstallmentFrequency.MONTHLY,
            down_payment=Decimal("0"),
        )
        _db.session.commit()

        assert plan.doc_number.startswith("INST-")
        assert plan.status == InstallmentPlanStatus.ACTIVE
        assert plan.total_amount == Decimal("1200.000")
        assert plan.financed_amount == Decimal("1200.000")
        assert plan.installments_count == 6
        assert len(plan.schedule) == 6

        # كل قسط = 200
        assert all(l.amount == Decimal("200.000") for l in plan.schedule)
        # الأرقام من 1 إلى 6
        assert [l.number for l in plan.schedule] == [1, 2, 3, 4, 5, 6]
        # تاريخ أول قسط = تاريخ الفاتورة + شهر
        assert plan.schedule[0].due_date == date(2026, 4, 1)

    def test_down_payment_creates_cash_journal_and_reduces_financed(self, env):
        plan = create_installment_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 3, 1),
            lines=[InvoiceLineDraft(env["variant"].id, qty=1, unit_price=1000)],
            installments_count=5,
            down_payment=Decimal("500"),
        )
        _db.session.commit()

        assert plan.total_amount == Decimal("1000.000")
        assert plan.down_payment == Decimal("500.000")
        assert plan.financed_amount == Decimal("500.000")
        # كل قسط 100
        assert all(l.amount == Decimal("100.000") for l in plan.schedule)

    def test_stock_delivered_immediately_regardless_of_down_payment(self, env):
        variant = env["variant"]
        stock_before = Decimal(str(variant.stock_qty))
        create_installment_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 3, 1),
            lines=[InvoiceLineDraft(variant.id, qty=3, unit_price=100)],
            installments_count=3,
            down_payment=Decimal("0"),  # لا يوجد مقدم — لكن يجب أن يخرج المخزون
        )
        _db.session.commit()
        assert variant.stock_qty == stock_before - Decimal("3")

    def test_customer_ar_balance_equals_financed_amount(self, env):
        customer = env["customer"]
        assert customer.account.compute_balance() == Decimal("0")

        create_installment_sale(
            customer_id=customer.id,
            invoice_date=date(2026, 3, 1),
            lines=[InvoiceLineDraft(env["variant"].id, qty=1, unit_price=1200)],
            installments_count=6,
            down_payment=Decimal("200"),
        )
        _db.session.commit()

        # AR = 1200 - 200 (المقدم مسدَّد كاش) = 1000
        assert customer.account.compute_balance() == Decimal("1000.000")

    def test_rounding_absorbed_by_last_installment(self, env):
        plan = create_installment_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 3, 1),
            lines=[InvoiceLineDraft(env["variant"].id, qty=1, unit_price=100)],
            installments_count=3,  # 100 / 3 = 33.333 x 3 = 99.999 → آخر قسط 33.334
        )
        _db.session.commit()

        first_two_sum = plan.schedule[0].amount + plan.schedule[1].amount
        assert plan.schedule[0].amount == plan.schedule[1].amount == Decimal("33.333")
        # آخر قسط يمتص الفرق (33.334)
        assert plan.schedule[2].amount == Decimal("33.334")
        # المجموع الكلي = المبلغ الممول بالضبط
        total = sum((l.amount for l in plan.schedule), Decimal("0"))
        assert total == plan.financed_amount

    def test_weekly_frequency_spaces_by_7_days(self, env):
        plan = create_installment_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 3, 1),
            lines=[InvoiceLineDraft(env["variant"].id, qty=1, unit_price=400)],
            installments_count=4,
            frequency=InstallmentFrequency.WEEKLY,
        )
        _db.session.commit()

        assert plan.schedule[0].due_date == date(2026, 3, 8)
        assert plan.schedule[1].due_date == date(2026, 3, 15)
        assert plan.schedule[2].due_date == date(2026, 3, 22)
        assert plan.schedule[3].due_date == date(2026, 3, 29)

    def test_down_payment_equal_to_total_completes_plan(self, env):
        plan = create_installment_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 3, 1),
            lines=[InvoiceLineDraft(env["variant"].id, qty=1, unit_price=500)],
            installments_count=5,
            down_payment=Decimal("500"),  # كامل المبلغ
        )
        _db.session.commit()
        assert plan.status == InstallmentPlanStatus.COMPLETED
        assert plan.financed_amount == Decimal("0")

    def test_down_payment_greater_than_total_rejected(self, env):
        with pytest.raises(InstallmentError, match="أكبر"):
            create_installment_sale(
                customer_id=env["customer"].id,
                invoice_date=date(2026, 3, 1),
                lines=[InvoiceLineDraft(env["variant"].id, qty=1, unit_price=100)],
                installments_count=3,
                down_payment=Decimal("500"),
            )

    def test_zero_installments_rejected(self, env):
        with pytest.raises(InstallmentError):
            create_installment_sale(
                customer_id=env["customer"].id,
                invoice_date=date(2026, 3, 1),
                lines=[InvoiceLineDraft(env["variant"].id, qty=1, unit_price=100)],
                installments_count=0,
            )


# ---------- 2) تحصيل الأقساط ----------

class TestCollectPayment:
    def test_full_payment_marks_paid(self, env):
        plan = create_installment_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 3, 1),
            lines=[InvoiceLineDraft(env["variant"].id, qty=1, unit_price=600)],
            installments_count=3,  # 200 each
        )
        _db.session.commit()

        first = plan.schedule[0]
        assert first.status == InstallmentLineStatus.PENDING

        collect_installment_payment(
            schedule_line_id=first.id,
            amount=Decimal("200"),
            collection_date=date(2026, 4, 1),
            method="cash",
        )
        _db.session.commit()

        _db.session.refresh(first)
        assert first.status == InstallmentLineStatus.PAID
        assert first.paid_amount == Decimal("200.000")
        assert first.paid_at is not None

    def test_partial_payment_marks_partial(self, env):
        plan = create_installment_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 3, 1),
            lines=[InvoiceLineDraft(env["variant"].id, qty=1, unit_price=600)],
            installments_count=3,
        )
        _db.session.commit()

        first = plan.schedule[0]
        collect_installment_payment(
            schedule_line_id=first.id,
            amount=Decimal("50"),
            collection_date=date(2026, 4, 1),
        )
        _db.session.commit()

        assert first.status == InstallmentLineStatus.PARTIAL
        assert first.paid_amount == Decimal("50.000")
        assert first.remaining == Decimal("150.000")

    def test_multiple_partial_payments_add_up_to_paid(self, env):
        plan = create_installment_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 3, 1),
            lines=[InvoiceLineDraft(env["variant"].id, qty=1, unit_price=300)],
            installments_count=3,  # 100 each
        )
        _db.session.commit()

        first = plan.schedule[0]
        collect_installment_payment(schedule_line_id=first.id, amount=Decimal("40"),
                                    collection_date=date(2026, 4, 1))
        _db.session.commit()
        collect_installment_payment(schedule_line_id=first.id, amount=Decimal("60"),
                                    collection_date=date(2026, 4, 2))
        _db.session.commit()
        _db.session.refresh(first)

        assert first.status == InstallmentLineStatus.PAID
        assert first.paid_amount == Decimal("100.000")

    def test_over_payment_rejected(self, env):
        plan = create_installment_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 3, 1),
            lines=[InvoiceLineDraft(env["variant"].id, qty=1, unit_price=300)],
            installments_count=3,
        )
        _db.session.commit()
        with pytest.raises(InstallmentError, match="أكبر"):
            collect_installment_payment(
                schedule_line_id=plan.schedule[0].id,
                amount=Decimal("200"),  # القسط 100 فقط
                collection_date=date(2026, 4, 1),
            )

    def test_paying_all_installments_completes_plan(self, env):
        plan = create_installment_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 3, 1),
            lines=[InvoiceLineDraft(env["variant"].id, qty=1, unit_price=300)],
            installments_count=3,
        )
        _db.session.commit()

        for line in plan.schedule:
            collect_installment_payment(
                schedule_line_id=line.id, amount=Decimal(str(line.amount)),
                collection_date=date(2026, 4, 1),
            )
        _db.session.commit()

        _db.session.refresh(plan)
        assert plan.status == InstallmentPlanStatus.COMPLETED
        assert env["customer"].account.compute_balance() == Decimal("0")

    def test_payment_creates_correct_journal(self, env):
        plan = create_installment_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 3, 1),
            lines=[InvoiceLineDraft(env["variant"].id, qty=1, unit_price=300)],
            installments_count=3,
        )
        _db.session.commit()

        cash = _db.session.query(Account).filter_by(code="1010").one()
        cash_before = cash.compute_balance()

        collect_installment_payment(
            schedule_line_id=plan.schedule[0].id, amount=Decimal("100"),
            collection_date=date(2026, 4, 1), method="cash",
        )
        _db.session.commit()

        # النقدية زادت 100، ورصيد العميل قلّ 100 (كان 300 يبقى 200)
        assert cash.compute_balance() == cash_before + Decimal("100")
        assert env["customer"].account.compute_balance() == Decimal("200.000")


# ---------- 3) رصد المتأخرات ----------

class TestOverdueDetection:
    def test_marks_lines_past_due_date(self, env):
        plan = create_installment_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 3, 1),
            lines=[InvoiceLineDraft(env["variant"].id, qty=1, unit_price=300)],
            installments_count=3,
            start_date=date(2026, 3, 15),
        )
        _db.session.commit()

        # نُشغِّل الرصد كأننا في 1 مايو — كل الأقساط في الماضي
        mark_overdue_installments(today=date(2026, 7, 1))
        _db.session.commit()

        # كل الـ 3 أقساط لهذه الخطة أصبحت متأخرة
        for line in plan.schedule:
            assert line.status == InstallmentLineStatus.OVERDUE

    def test_ignores_paid_lines(self, env):
        plan = create_installment_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 3, 1),
            lines=[InvoiceLineDraft(env["variant"].id, qty=1, unit_price=300)],
            installments_count=3,
            start_date=date(2026, 3, 15),
        )
        _db.session.commit()

        # سدِّد أول قسط
        collect_installment_payment(
            schedule_line_id=plan.schedule[0].id, amount=Decimal("100"),
            collection_date=date(2026, 3, 20),
        )
        _db.session.commit()

        mark_overdue_installments(today=date(2026, 7, 1))
        _db.session.commit()

        # القسط الأول لا يُعتَبر متأخرًا (لهذه الخطة تحديدًا)
        assert plan.schedule[0].status == InstallmentLineStatus.PAID
        assert plan.schedule[1].status == InstallmentLineStatus.OVERDUE
        assert plan.schedule[2].status == InstallmentLineStatus.OVERDUE

    def test_partial_lines_also_flip_to_overdue(self, env):
        plan = create_installment_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 3, 1),
            lines=[InvoiceLineDraft(env["variant"].id, qty=1, unit_price=300)],
            installments_count=3,
            start_date=date(2026, 3, 15),
        )
        _db.session.commit()

        # سدِّد جزءًا من أول قسط
        collect_installment_payment(
            schedule_line_id=plan.schedule[0].id, amount=Decimal("50"),
            collection_date=date(2026, 3, 20),
        )
        _db.session.commit()
        assert plan.schedule[0].status == InstallmentLineStatus.PARTIAL

        mark_overdue_installments(today=date(2026, 7, 1))
        _db.session.commit()

        # القسط الجزئي أصبح متأخرًا
        assert plan.schedule[0].status == InstallmentLineStatus.OVERDUE

    def test_get_due_today_returns_matching_lines(self, env):
        plan = create_installment_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 3, 1),
            lines=[InvoiceLineDraft(env["variant"].id, qty=1, unit_price=300)],
            installments_count=3,
            start_date=date(2026, 4, 15),
        )
        _db.session.commit()

        due = get_due_today_lines(today=date(2026, 4, 15))
        # قسط واحد فقط مستحق في هذا التاريخ
        assert any(l.id == plan.schedule[0].id for l in due)


# ============================================================
# Thermal receipt — يعمل على أي SalesInvoice (POS, تقسيط, يدوي)
# ============================================================

class TestUniversalThermalReceipt:
    """
    Regression: الإيصال الحراري كان مقصور على POS. الآن /sales/<id>/receipt
    شغال لأي فاتورة — بيعرض 'مقدم' + 'متبقي' للتقسيط و'عميل نقدي' للـ walk-in.
    """

    def test_installment_invoice_exposes_plan_backref(self, env):
        """SalesInvoice.installment_plan backref يشتغل — عشان القالب يقدر يميز."""
        plan = create_installment_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 3, 1),
            lines=[InvoiceLineDraft(env["variant"].id, qty=1, unit_price=1200)],
            installments_count=3,
            frequency=InstallmentFrequency.MONTHLY,
            down_payment=Decimal("300"),
        )
        _db.session.commit()

        inv = plan.sales_invoice
        # backref المهم للقالب
        assert inv.installment_plan is not None
        assert inv.installment_plan.id == plan.id
        assert inv.installment_plan.down_payment == Decimal("300.000")
        assert inv.installment_plan.financed_amount == Decimal("900.000")
        # للفاتورة النقدية العادية source_order يجب أن يكون None
        assert inv.source_order is None
