"""خدمة جداول سداد الموردين + تسجيل الدفعات.

القواعد:
- الجدول يُرفَق بفاتورة شراء آجلة (CREDIT) — لا يُفعَّل مع نقدي/بنك فوري.
- مجموع أسطر الجدول = إجمالي الفاتورة (نتحقق قبل الحفظ).
- كل دفعة تُنشِئ قيد: مدين 2100-VVV (حساب المورد) / دائن 1010 أو 1020.
- حالة السطر: paid لو كامل، partial لو أقل.
- بعد آخر دفعة تُكمل كل الأسطر → status = completed.
- رصد المتأخرات: يشبه التقسيط — كل سطر تجاوز due_date ولم يُسدَّد كليًا → overdue.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Iterable

from app.extensions import db
from app.models.account import Account
from app.models.journal import JournalSourceType
from app.models.party import Party, PartyType
from app.models.purchases import PurchaseInvoice, PurchasePayment
from app.models.vendor_payment import (
    VendorPayment,
    VendorPaymentSchedule,
    VendorPaymentScheduleLine,
    VendorScheduleLineStatus,
    VendorScheduleStatus,
)
from app.services.ledger import LedgerLineDraft, post_journal_entry
from app.services.numbering import next_document_number


class VendorPaymentError(ValueError):
    pass


ZERO = Decimal("0")


@dataclass
class ScheduleLineDraft:
    due_date: date
    amount: Decimal | float | str


# =====================================================
# 1) إرفاق جدول سداد بفاتورة شراء آجلة
# =====================================================

def attach_payment_schedule(
    *,
    purchase_invoice_id: int,
    lines: Iterable[ScheduleLineDraft],
    notes: str | None = None,
    user_id: int | None = None,
) -> VendorPaymentSchedule:
    """يُنشِئ جدول سداد لفاتورة شراء آجلة قائمة.

    - الفاتورة لازم تكون CREDIT (لا معنى لجدول لفاتورة كاش/بنك فوري).
    - مجموع الأسطر يجب أن يساوي إجمالي الفاتورة بالضبط.
    - لا نُغيِّر أي قيد — قيد الشراء الأصلي (D 1100 / C 2100-VVV) لا يزال قائمًا.
    """
    invoice = db.session.get(PurchaseInvoice, purchase_invoice_id)
    if invoice is None:
        raise VendorPaymentError("فاتورة الشراء غير موجودة.")

    if invoice.payment_method != PurchasePayment.CREDIT:
        raise VendorPaymentError(
            "لا يمكن إرفاق جدول سداد إلا بفاتورة شراء آجلة (CREDIT)."
        )

    # لا نسمح بأكثر من جدول لنفس الفاتورة
    existing = (
        db.session.query(VendorPaymentSchedule)
        .filter_by(purchase_invoice_id=invoice.id)
        .first()
    )
    if existing is not None:
        raise VendorPaymentError(
            f"يوجد جدول سداد سابق ({existing.id}) لهذه الفاتورة."
        )

    line_specs = list(lines)
    if not line_specs:
        raise VendorPaymentError("الجدول يحتاج سطرًا واحدًا على الأقل.")

    total_from_lines = ZERO
    for spec in line_specs:
        amt = _as_dec(spec.amount)
        if amt <= 0:
            raise VendorPaymentError("مبلغ كل سطر يجب أن يكون > صفر.")
        total_from_lines += _q(amt)

    invoice_total = Decimal(str(invoice.total))
    if total_from_lines != invoice_total:
        raise VendorPaymentError(
            f"مجموع أسطر الجدول ({total_from_lines}) لا يساوي إجمالي الفاتورة ({invoice_total})."
        )

    schedule = VendorPaymentSchedule(
        purchase_invoice_id=invoice.id,
        vendor_id=invoice.vendor_id,
        total_amount=invoice_total,
        status=VendorScheduleStatus.ACTIVE,
        notes=(notes or None),
        created_by_id=user_id,
    )
    db.session.add(schedule)
    db.session.flush()

    # نُرتِّب الأسطر بالتاريخ لضمان تسلسل منطقي
    for i, spec in enumerate(sorted(line_specs, key=lambda s: s.due_date), start=1):
        db.session.add(VendorPaymentScheduleLine(
            schedule_id=schedule.id,
            number=i,
            due_date=spec.due_date,
            amount=_q(_as_dec(spec.amount)),
            paid_amount=ZERO,
            status=VendorScheduleLineStatus.PENDING,
        ))

    db.session.flush()
    return schedule


# =====================================================
# 2) تسجيل سداد لسطر جدول
# =====================================================

def record_vendor_payment(
    *,
    schedule_line_id: int,
    amount: Decimal | float | str,
    payment_date: date,
    method: str = "cash",
    bank_account_id: int | None = None,
    memo: str | None = None,
    user_id: int | None = None,
) -> VendorPayment:
    """يُطبِّق مبلغ سداد على سطر جدول محدد ويُنشِئ قيد الدفع."""

    line = db.session.get(VendorPaymentScheduleLine, schedule_line_id)
    if line is None:
        raise VendorPaymentError("سطر الجدول غير موجود.")

    schedule = line.schedule
    if schedule.status != VendorScheduleStatus.ACTIVE:
        raise VendorPaymentError(f"الجدول ليس نشطًا (الحالة: {schedule.status.value}).")

    amount = _as_dec(amount)
    if amount <= 0:
        raise VendorPaymentError("مبلغ السداد يجب أن يكون > صفر.")

    remaining = line.remaining
    if amount > remaining:
        raise VendorPaymentError(
            f"مبلغ السداد ({amount}) أكبر من المتبقي على السطر ({remaining})."
        )

    credit_side = _resolve_cash_or_bank(method, bank_account_id)
    vendor_account = _vendor_ap_account(schedule.vendor)

    # القيد: مدين 2100-VVV / دائن 1010 أو 1020
    post_journal_entry(
        entry_date=payment_date,
        source_type=JournalSourceType.VENDOR_PAYMENT,
        source_id=schedule.id,
        memo=(
            f"سداد للمورد {schedule.vendor.name_ar} — "
            f"دفعة {line.number} من فاتورة {schedule.purchase_invoice.doc_number}"
        ),
        lines=[
            LedgerLineDraft(vendor_account.id, debit=amount,
                            memo=f"سداد دفعة {line.number} - "
                                 f"{schedule.purchase_invoice.doc_number}"),
            LedgerLineDraft(credit_side.id, credit=amount,
                            memo=f"سداد للمورد {schedule.vendor.name_ar}"),
        ],
        user_id=user_id,
    )

    # تحديث السطر
    line.paid_amount = _q(Decimal(str(line.paid_amount or 0)) + amount)
    if line.paid_amount >= Decimal(str(line.amount)):
        line.status = VendorScheduleLineStatus.PAID
        line.paid_at = datetime.now(timezone.utc)
    else:
        line.status = VendorScheduleLineStatus.PARTIAL

    # تسجيل الدفعة
    payment = VendorPayment(
        doc_number=next_document_number("vendor_payment"),
        vendor_id=schedule.vendor_id,
        schedule_id=schedule.id,
        schedule_line_id=line.id,
        purchase_invoice_id=schedule.purchase_invoice_id,
        payment_date=payment_date,
        amount=_q(amount),
        method=method,
        bank_account_id=(bank_account_id if method == "bank" else None),
        memo=memo,
        created_by_id=user_id,
    )
    db.session.add(payment)
    db.session.flush()

    # الجدول يكتمل لو كل الأسطر PAID
    all_paid = all(l.status == VendorScheduleLineStatus.PAID for l in schedule.lines)
    if all_paid:
        schedule.status = VendorScheduleStatus.COMPLETED

    db.session.flush()
    return payment


# =====================================================
# 3) رصد المتأخرات
# =====================================================

def mark_overdue_vendor_lines(*, today: date | None = None) -> int:
    today = today or date.today()
    lines = (
        db.session.query(VendorPaymentScheduleLine)
        .filter(VendorPaymentScheduleLine.due_date < today)
        .filter(VendorPaymentScheduleLine.status.in_([
            VendorScheduleLineStatus.PENDING, VendorScheduleLineStatus.PARTIAL,
        ]))
        .all()
    )
    for l in lines:
        l.status = VendorScheduleLineStatus.OVERDUE
    db.session.flush()
    return len(lines)


# =====================================================
# Helpers
# =====================================================

def _vendor_ap_account(vendor: Party) -> Account:
    if vendor is None or vendor.type != PartyType.VENDOR:
        raise VendorPaymentError("مورد غير صحيح.")
    if vendor.account is None:
        raise VendorPaymentError(f"المورد {vendor.name_ar} ليس له حساب فرعي.")
    return vendor.account


def _resolve_cash_or_bank(method: str, bank_account_id: int | None) -> Account:
    method = (method or "cash").lower()
    if method == "cash":
        acc = db.session.query(Account).filter_by(code="1010").one_or_none()
        if acc is None:
            raise VendorPaymentError("حساب النقدية 1010 غير موجود.")
        return acc
    if method == "bank":
        if bank_account_id:
            acc = db.session.get(Account, bank_account_id)
            if acc is None or not acc.is_postable:
                raise VendorPaymentError("حساب البنك المحدد غير صالح.")
            return acc
        parent = db.session.query(Account).filter_by(code="1020").one_or_none()
        if parent is None:
            raise VendorPaymentError("حساب البنك الأب 1020 غير موجود.")
        child = (
            db.session.query(Account)
            .filter_by(parent_id=parent.id, is_active=True, is_postable=True)
            .order_by(Account.code)
            .first()
        )
        if child is None:
            raise VendorPaymentError("لا يوجد حساب بنك فرعي — أضف واحدًا أولاً.")
        return child
    raise VendorPaymentError(f"طريقة سداد غير مدعومة: {method}")


def _as_dec(x) -> Decimal:
    if x is None or x == "":
        return ZERO
    return x if isinstance(x, Decimal) else Decimal(str(x))


def _q(v: Decimal, places: int = 3) -> Decimal:
    return v.quantize(Decimal(10) ** -places)
