"""اختبارات جداول سداد الموردين."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.models.account import Account, AccountType
from app.models.party import PartyType
from app.models.purchases import PurchasePayment
from app.models.setting import set_setting
from app.models.vendor_payment import (
    VendorScheduleLineStatus,
    VendorScheduleStatus,
)
from app.services.parties import create_party
from app.services.products import create_category, create_product
from app.services.purchases import PurchaseLineDraft, create_purchase_invoice
from app.services.vendor_payments import (
    ScheduleLineDraft,
    VendorPaymentError,
    attach_payment_schedule,
    mark_overdue_vendor_lines,
    record_vendor_payment,
)
from seeds.chart_of_accounts import seed_chart_of_accounts


@pytest.fixture()
def env(app):
    seed_chart_of_accounts(_db.session)
    set_setting("tax.enabled", "false")

    # حساب بنك فرعي
    bank_parent = _db.session.query(Account).filter_by(code="1020").one()
    if not _db.session.query(Account).filter_by(parent_id=bank_parent.id).first():
        _db.session.add(Account(
            code="1020-001", name_ar="بنك مصر",
            type=AccountType.ASSET, parent_id=bank_parent.id,
            is_postable=True, is_active=True, is_system=False,
        ))
    _db.session.commit()

    tag = uuid.uuid4().hex[:8]
    vendor = create_party(type=PartyType.VENDOR, name_ar=f"مورد {tag}", phone=f"010{tag}")
    cat = create_category(name_ar=f"cat-{tag}")
    product = create_product(
        name_ar=f"منتج {tag}", category_id=cat.id, default_price=Decimal("100"),
        variants=[{"variant_name": "افتراضي"}],
    )
    variant = product.variants[0]
    _db.session.commit()
    yield {"vendor": vendor, "variant": variant}


def _make_credit_invoice(env, total_lines: int = 1, qty_per_line: int = 10, unit_cost: int = 100):
    """يُنشِئ فاتورة شراء آجلة بقيمة (qty × cost × total_lines)."""
    lines = [PurchaseLineDraft(env["variant"].id, qty=qty_per_line, unit_cost=unit_cost)
             for _ in range(total_lines)]
    inv = create_purchase_invoice(
        vendor_id=env["vendor"].id,
        invoice_date=date(2026, 1, 1),
        payment_method=PurchasePayment.CREDIT,
        lines=lines,
    )
    _db.session.commit()
    return inv


# ---------- 1) بناء الجدول ----------

class TestAttachSchedule:
    def test_creates_schedule_with_unequal_lines(self, env):
        inv = _make_credit_invoice(env, unit_cost=100, qty_per_line=10)  # total = 1000
        schedule = attach_payment_schedule(
            purchase_invoice_id=inv.id,
            lines=[
                ScheduleLineDraft(due_date=date(2026, 2, 1),  amount=Decimal("300")),
                ScheduleLineDraft(due_date=date(2026, 3, 15), amount=Decimal("400")),
                ScheduleLineDraft(due_date=date(2026, 5, 1),  amount=Decimal("300")),
            ],
        )
        _db.session.commit()

        assert schedule.status == VendorScheduleStatus.ACTIVE
        assert schedule.total_amount == Decimal("1000.000")
        assert len(schedule.lines) == 3
        # مرتبة بالتاريخ
        assert [l.due_date for l in schedule.lines] == [
            date(2026, 2, 1), date(2026, 3, 15), date(2026, 5, 1),
        ]
        assert [l.amount for l in schedule.lines] == [
            Decimal("300.000"), Decimal("400.000"), Decimal("300.000"),
        ]

    def test_schedule_sum_must_match_invoice_total(self, env):
        inv = _make_credit_invoice(env, unit_cost=100, qty_per_line=10)  # 1000
        with pytest.raises(VendorPaymentError, match="لا يساوي"):
            attach_payment_schedule(
                purchase_invoice_id=inv.id,
                lines=[
                    ScheduleLineDraft(due_date=date(2026, 2, 1), amount=Decimal("500")),
                    ScheduleLineDraft(due_date=date(2026, 3, 1), amount=Decimal("400")),  # 900 ≠ 1000
                ],
            )

    def test_cannot_attach_schedule_to_cash_invoice(self, env):
        # فاتورة كاش فورية
        inv = create_purchase_invoice(
            vendor_id=env["vendor"].id,
            invoice_date=date(2026, 1, 1),
            payment_method=PurchasePayment.CASH,
            lines=[PurchaseLineDraft(env["variant"].id, qty=1, unit_cost=100)],
        )
        _db.session.commit()
        with pytest.raises(VendorPaymentError, match="آجلة"):
            attach_payment_schedule(
                purchase_invoice_id=inv.id,
                lines=[ScheduleLineDraft(due_date=date(2026, 2, 1), amount=Decimal("100"))],
            )

    def test_cannot_attach_two_schedules_to_same_invoice(self, env):
        inv = _make_credit_invoice(env, unit_cost=100, qty_per_line=10)
        attach_payment_schedule(
            purchase_invoice_id=inv.id,
            lines=[ScheduleLineDraft(due_date=date(2026, 2, 1), amount=Decimal("1000"))],
        )
        _db.session.commit()
        with pytest.raises(VendorPaymentError, match="سابق"):
            attach_payment_schedule(
                purchase_invoice_id=inv.id,
                lines=[ScheduleLineDraft(due_date=date(2026, 3, 1), amount=Decimal("1000"))],
            )


# ---------- 2) تسجيل السداد ----------

class TestRecordPayment:
    def test_full_payment_marks_paid(self, env):
        inv = _make_credit_invoice(env, unit_cost=100, qty_per_line=5)  # 500
        sched = attach_payment_schedule(
            purchase_invoice_id=inv.id,
            lines=[
                ScheduleLineDraft(due_date=date(2026, 2, 1), amount=Decimal("200")),
                ScheduleLineDraft(due_date=date(2026, 3, 1), amount=Decimal("300")),
            ],
        )
        _db.session.commit()

        vendor_balance_before = env["vendor"].account.compute_balance()
        assert vendor_balance_before == Decimal("500.000")  # AP دائن

        record_vendor_payment(
            schedule_line_id=sched.lines[0].id,
            amount=Decimal("200"),
            payment_date=date(2026, 2, 5),
            method="cash",
        )
        _db.session.commit()

        assert sched.lines[0].status == VendorScheduleLineStatus.PAID
        assert sched.lines[0].paid_amount == Decimal("200.000")
        # رصيد المورد نقص 200 (بقي 300)
        assert env["vendor"].account.compute_balance() == Decimal("300.000")

    def test_partial_payment_marks_partial(self, env):
        inv = _make_credit_invoice(env, unit_cost=100, qty_per_line=5)
        sched = attach_payment_schedule(
            purchase_invoice_id=inv.id,
            lines=[ScheduleLineDraft(due_date=date(2026, 2, 1), amount=Decimal("500"))],
        )
        _db.session.commit()

        record_vendor_payment(
            schedule_line_id=sched.lines[0].id,
            amount=Decimal("150"),
            payment_date=date(2026, 2, 5),
        )
        _db.session.commit()
        assert sched.lines[0].status == VendorScheduleLineStatus.PARTIAL
        assert sched.lines[0].remaining == Decimal("350.000")

    def test_over_payment_rejected(self, env):
        inv = _make_credit_invoice(env, unit_cost=100, qty_per_line=5)
        sched = attach_payment_schedule(
            purchase_invoice_id=inv.id,
            lines=[ScheduleLineDraft(due_date=date(2026, 2, 1), amount=Decimal("500"))],
        )
        _db.session.commit()
        with pytest.raises(VendorPaymentError, match="أكبر"):
            record_vendor_payment(
                schedule_line_id=sched.lines[0].id,
                amount=Decimal("600"),
                payment_date=date(2026, 2, 5),
            )

    def test_completing_all_lines_marks_schedule_completed(self, env):
        inv = _make_credit_invoice(env, unit_cost=100, qty_per_line=5)
        sched = attach_payment_schedule(
            purchase_invoice_id=inv.id,
            lines=[
                ScheduleLineDraft(due_date=date(2026, 2, 1), amount=Decimal("200")),
                ScheduleLineDraft(due_date=date(2026, 3, 1), amount=Decimal("300")),
            ],
        )
        _db.session.commit()
        for line in sched.lines:
            record_vendor_payment(
                schedule_line_id=line.id,
                amount=Decimal(str(line.amount)),
                payment_date=date(2026, 3, 5),
            )
        _db.session.commit()
        assert sched.status == VendorScheduleStatus.COMPLETED
        assert env["vendor"].account.compute_balance() == Decimal("0.000")

    def test_bank_payment_uses_bank_account(self, env):
        inv = _make_credit_invoice(env, unit_cost=100, qty_per_line=5)
        sched = attach_payment_schedule(
            purchase_invoice_id=inv.id,
            lines=[ScheduleLineDraft(due_date=date(2026, 2, 1), amount=Decimal("500"))],
        )
        _db.session.commit()

        bank_before = _db.session.query(Account).filter_by(code="1020-001").one().compute_balance()

        record_vendor_payment(
            schedule_line_id=sched.lines[0].id,
            amount=Decimal("500"),
            payment_date=date(2026, 2, 5),
            method="bank",
        )
        _db.session.commit()

        bank_after = _db.session.query(Account).filter_by(code="1020-001").one().compute_balance()
        # البنك أصل — رصيده الطبيعي مدين، السحب يقلله
        assert bank_after == bank_before - Decimal("500.000")


# ---------- 3) رصد التأخر ----------

class TestOverdueDetection:
    def test_marks_past_due_lines_overdue(self, env):
        inv = _make_credit_invoice(env, unit_cost=100, qty_per_line=5)
        sched = attach_payment_schedule(
            purchase_invoice_id=inv.id,
            lines=[
                ScheduleLineDraft(due_date=date(2026, 2, 1), amount=Decimal("200")),
                ScheduleLineDraft(due_date=date(2026, 6, 1), amount=Decimal("300")),
            ],
        )
        _db.session.commit()

        mark_overdue_vendor_lines(today=date(2026, 4, 1))
        _db.session.commit()

        # القسط الأول متأخر (فبراير < أبريل)، الثاني لم يحن (يونيو > أبريل)
        assert sched.lines[0].status == VendorScheduleLineStatus.OVERDUE
        assert sched.lines[1].status == VendorScheduleLineStatus.PENDING
