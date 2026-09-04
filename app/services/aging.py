"""تقارير أعمار الديون — AP (موردون) و AR (عملاء).

**البنية:**
- لكل طرف: نجمع الأسطر غير المسدَّدة كليًا (من الجداول لو موجودة، وإلا من تاريخ الفاتورة)
  ونُصنِّفها حسب عمر التأخر (days_late) في 4 خانات:
      Not-yet-due  (لم يحن موعدها)
      0-30 days    (متأخر حتى 30 يوم)
      31-60 days   (متأخر 31-60 يوم)
      60+ days     (متأخر أكثر من 60 يوم)

- الطرف يظهر فقط لو له رصيد != 0.

المصادر:
- AP: VendorPaymentScheduleLine غير المسدَّدة + فواتير CREDIT بدون جدول (نأخذ تاريخ الفاتورة كـ due_date)
- AR: InstallmentScheduleLine غير المسدَّدة + رصيد العملاء من الفواتير الآجلة العادية
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Iterable

from sqlalchemy import and_

from app.extensions import db
from app.models.installment import (
    InstallmentLineStatus,
    InstallmentPlan,
    InstallmentPlanStatus,
    InstallmentScheduleLine,
)
from app.models.party import Party, PartyType
from app.models.purchases import PurchaseInvoice, PurchasePayment, PurchaseStatus
from app.models.vendor_payment import (
    VendorPaymentSchedule,
    VendorPaymentScheduleLine,
    VendorScheduleLineStatus,
    VendorScheduleStatus,
)


ZERO = Decimal("0")


@dataclass
class AgingBucket:
    """4 خانات لعرض عمر الدين."""
    not_yet_due: Decimal = ZERO
    days_0_30: Decimal = ZERO
    days_31_60: Decimal = ZERO
    days_over_60: Decimal = ZERO

    @property
    def total(self) -> Decimal:
        return self.not_yet_due + self.days_0_30 + self.days_31_60 + self.days_over_60


@dataclass
class AgingRow:
    party: Party
    bucket: AgingBucket = field(default_factory=AgingBucket)
    # تفاصيل الأسطر الفردية للاطلاع (اختياري)
    lines: list[dict] = field(default_factory=list)


def _classify_by_age(bucket: AgingBucket, amount: Decimal, due_date: date, today: date) -> str:
    """يضيف amount للخانة المناسبة، يُرجِع اسم الخانة (للتفصيل)."""
    days_late = (today - due_date).days
    if days_late < 0:
        bucket.not_yet_due += amount
        return "not_yet_due"
    if days_late <= 30:
        bucket.days_0_30 += amount
        return "days_0_30"
    if days_late <= 60:
        bucket.days_31_60 += amount
        return "days_31_60"
    bucket.days_over_60 += amount
    return "days_over_60"


# =====================================================
# AP Aging — أعمار ديون الموردين
# =====================================================

def ap_aging(*, as_of: date | None = None) -> dict:
    """يحسب أعمار الديون لكل مورد."""
    today = as_of or date.today()

    rows: dict[int, AgingRow] = {}

    # 1) الأسطر من جداول السداد النشطة
    lines = (
        db.session.query(VendorPaymentScheduleLine, VendorPaymentSchedule)
        .join(VendorPaymentSchedule, VendorPaymentSchedule.id == VendorPaymentScheduleLine.schedule_id)
        .filter(VendorPaymentSchedule.status == VendorScheduleStatus.ACTIVE)
        .filter(VendorPaymentScheduleLine.status.in_([
            VendorScheduleLineStatus.PENDING,
            VendorScheduleLineStatus.PARTIAL,
            VendorScheduleLineStatus.OVERDUE,
        ]))
        .all()
    )
    for line, schedule in lines:
        vendor = schedule.vendor
        row = rows.setdefault(vendor.id, AgingRow(party=vendor))
        remaining = line.remaining
        if remaining <= 0:
            continue
        bucket_name = _classify_by_age(row.bucket, remaining, line.due_date, today)
        row.lines.append({
            "source": "schedule",
            "invoice_number": schedule.purchase_invoice.doc_number,
            "invoice_id": schedule.purchase_invoice_id,
            "due_date": line.due_date,
            "amount": remaining,
            "bucket": bucket_name,
            "days_late": max(0, (today - line.due_date).days),
        })

    # 2) فواتير CREDIT بدون جدول (نأخذ invoice_date كـ due_date)
    credit_invoices = (
        db.session.query(PurchaseInvoice)
        .filter(PurchaseInvoice.payment_method == PurchasePayment.CREDIT)
        .filter(PurchaseInvoice.status != PurchaseStatus.RETURNED)
        .all()
    )
    for inv in credit_invoices:
        # نتخطى لو فيه جدول
        if any(s.purchase_invoice_id == inv.id for s in db.session.query(VendorPaymentSchedule).all()):
            continue
        # الرصيد المتبقي = إجمالي الفاتورة - المرتجعات (نبسطها للآن: إجمالي الفاتورة كمقدار مستحق)
        # الأصح: نحسب من رصيد المورد لكن الفاتورة الواحدة قد لا تعكس رصيد كل مورد
        remaining = Decimal(str(inv.total))
        # نُنقص المرتجعات المرتبطة
        for ret in inv.returns:
            remaining -= Decimal(str(ret.refund_amount or 0))
        if remaining <= 0:
            continue

        vendor = inv.vendor
        row = rows.setdefault(vendor.id, AgingRow(party=vendor))
        bucket_name = _classify_by_age(row.bucket, remaining, inv.invoice_date, today)
        row.lines.append({
            "source": "invoice",
            "invoice_number": inv.doc_number,
            "invoice_id": inv.id,
            "due_date": inv.invoice_date,
            "amount": remaining,
            "bucket": bucket_name,
            "days_late": max(0, (today - inv.invoice_date).days),
        })

    # نستبعد الصفوف الفارغة (رصيد 0)
    rows_list = [r for r in rows.values() if r.bucket.total > 0]
    rows_list.sort(key=lambda r: r.bucket.total, reverse=True)

    totals = AgingBucket()
    for r in rows_list:
        totals.not_yet_due += r.bucket.not_yet_due
        totals.days_0_30 += r.bucket.days_0_30
        totals.days_31_60 += r.bucket.days_31_60
        totals.days_over_60 += r.bucket.days_over_60

    return {
        "as_of": today,
        "rows": rows_list,
        "totals": totals,
    }


# =====================================================
# AR Aging — أعمار ديون العملاء
# =====================================================

def ar_aging(*, as_of: date | None = None) -> dict:
    """يحسب أعمار الديون لكل عميل — يستخدم أسطر التقسيط كمصدر أساسي.

    ملاحظة: العملاء يشترون كاش بشكل رئيسي، فأول ما يظهر رصيد على عميل هو
    غالبًا من خطة تقسيط. لو كان هناك رصيد بدون خطة نُدرجه كسطر عام.
    """
    today = as_of or date.today()

    rows: dict[int, AgingRow] = {}

    # 1) أسطر التقسيط النشطة غير المسدَّدة كليًا
    lines = (
        db.session.query(InstallmentScheduleLine, InstallmentPlan)
        .join(InstallmentPlan, InstallmentPlan.id == InstallmentScheduleLine.plan_id)
        .filter(InstallmentPlan.status == InstallmentPlanStatus.ACTIVE)
        .filter(InstallmentScheduleLine.status.in_([
            InstallmentLineStatus.PENDING,
            InstallmentLineStatus.PARTIAL,
            InstallmentLineStatus.OVERDUE,
        ]))
        .all()
    )
    for line, plan in lines:
        customer = plan.customer
        row = rows.setdefault(customer.id, AgingRow(party=customer))
        remaining = line.remaining
        if remaining <= 0:
            continue
        bucket_name = _classify_by_age(row.bucket, remaining, line.due_date, today)
        row.lines.append({
            "source": "installment",
            "plan_number": plan.doc_number,
            "plan_id": plan.id,
            "installment_no": line.number,
            "due_date": line.due_date,
            "amount": remaining,
            "bucket": bucket_name,
            "days_late": max(0, (today - line.due_date).days),
        })

    # 2) رصيد العملاء الآخر (بدون خطة تقسيط) — نُقارن رصيد العميل بمجموع مسطور التقسيط
    all_customers_with_balance = (
        db.session.query(Party)
        .filter(Party.type == PartyType.CUSTOMER)
        .all()
    )
    for cust in all_customers_with_balance:
        if cust.account is None:
            continue
        balance = cust.account.compute_balance()
        if balance <= 0:
            continue

        # ما تم إدراجه من التقسيط لهذا العميل
        already_included = sum(
            (Decimal(str(ln["amount"])) for r in rows.values() if r.party.id == cust.id
             for ln in r.lines if ln["source"] == "installment"),
            ZERO,
        )
        extra = balance - already_included
        if extra <= Decimal("0.001"):  # صفر تقريبًا
            continue

        # نستخدم "as_of" كـ due_date (كأنه مستحق حاليًا)
        row = rows.setdefault(cust.id, AgingRow(party=cust))
        bucket_name = _classify_by_age(row.bucket, extra, today, today)
        row.lines.append({
            "source": "on_account",
            "plan_number": None,
            "plan_id": None,
            "installment_no": None,
            "due_date": today,
            "amount": extra,
            "bucket": bucket_name,
            "days_late": 0,
        })

    rows_list = [r for r in rows.values() if r.bucket.total > 0]
    rows_list.sort(key=lambda r: r.bucket.total, reverse=True)

    totals = AgingBucket()
    for r in rows_list:
        totals.not_yet_due += r.bucket.not_yet_due
        totals.days_0_30 += r.bucket.days_0_30
        totals.days_31_60 += r.bucket.days_31_60
        totals.days_over_60 += r.bucket.days_over_60

    return {
        "as_of": today,
        "rows": rows_list,
        "totals": totals,
    }
