"""خدمة البيع بالتقسيط — إنشاء الخطة + التحصيلات + رصد المتأخرات.

قواعد أساسية (من الوثيقة):
- عند البيع بالتقسيط:
    * الفاتورة تُسلَّم فورًا (المخزون يخرج) بغض النظر عن المقدم.
    * قيد البيع: مدين 1200-CUST (بقيمة المتبقي بعد المقدم) / دائن 4100 + 2200
    * لو مقدم > 0: قيد مدين 1010 نقدية / دائن 1200-CUST
    * قيد COGS: مدين 5100 / دائن 1100 بقيمة التكلفة
- عند تحصيل قسط:
    * مدين 1010/1020 / دائن 1200-CUST
    * سطر القسط: paid لو كامل، partial لو أقل
- رصد المتأخرات:
    * أي قسط تجاوز due_date ولم يُسدَّد بالكامل → status = OVERDUE
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

from app.extensions import db
from app.models.account import Account
from app.models.installment import (
    InstallmentCollection,
    InstallmentFrequency,
    InstallmentLineStatus,
    InstallmentPlan,
    InstallmentPlanStatus,
    InstallmentScheduleLine,
)
from app.models.journal import JournalSourceType
from app.models.party import Party, PartyType
from app.models.product import ProductVariant
from app.models.sales import InvoiceStatus, PaymentMethod, SalesInvoice, SalesInvoiceLine
from app.models.setting import get_setting
from app.services.inventory import record_sale
from app.services.ledger import LedgerLineDraft, post_journal_entry
from app.services.numbering import next_document_number


class InstallmentError(ValueError):
    pass


ZERO = Decimal("0")


@dataclass
class InvoiceLineDraft:
    variant_id: int
    qty: Decimal | float | str
    unit_price: Decimal | float | str | None = None


# ===================================================================
# 1) إنشاء خطة تقسيط (يبني فاتورة + خطة + جدول + قيود دفعة واحدة)
# ===================================================================

def create_installment_sale(
    *,
    customer_id: int,
    invoice_date: date,
    lines: Iterable[InvoiceLineDraft],
    installments_count: int,
    frequency: InstallmentFrequency = InstallmentFrequency.MONTHLY,
    down_payment: Decimal | float | str = 0,
    start_date: date | None = None,
    discount_amount: Decimal | float | str = 0,
    notes: str | None = None,
    user_id: int | None = None,
) -> InstallmentPlan:
    """إنشاء بيع بالتقسيط كامل — فاتورة + خطة + جدول أقساط + قيود."""

    customer = _get_active_customer(customer_id)
    ar_account = _customer_ar_account(customer)

    line_specs = list(lines)
    if not line_specs:
        raise InstallmentError("الفاتورة تحتاج سطرًا واحدًا على الأقل.")

    installments_count = int(installments_count)
    if installments_count < 1:
        raise InstallmentError("عدد الأقساط يجب أن يكون 1 على الأقل.")

    down_payment = _as_dec(down_payment)
    if down_payment < 0:
        raise InstallmentError("المقدم لا يمكن أن يكون سالبًا.")

    discount_amount = _as_dec(discount_amount)
    if discount_amount < 0:
        raise InstallmentError("الخصم لا يمكن أن يكون سالبًا.")

    # 1) بناء الفاتورة والتحقق من الأسطر (نفس منطق cash sale ما عدا قيد التحصيل)
    invoice = SalesInvoice(
        doc_number=next_document_number("sales_invoice"),
        invoice_date=invoice_date,
        customer_id=customer.id,
        payment_method=PaymentMethod.CASH,  # المقدم كاش لو موجود
        status=InvoiceStatus.POSTED,
        notes=(notes or None),
        created_by_id=user_id,
    )
    db.session.add(invoice)
    db.session.flush()

    subtotal = ZERO
    total_cost = ZERO
    resolved_lines: list[tuple[ProductVariant, Decimal, Decimal]] = []

    for spec in line_specs:
        variant = db.session.get(ProductVariant, spec.variant_id)
        if variant is None or not variant.is_active:
            raise InstallmentError(f"المنتج #{spec.variant_id} غير موجود أو موقوف.")
        qty = _as_dec(spec.qty)
        if qty <= 0:
            raise InstallmentError(f"الكمية للمنتج {variant.sku} يجب أن تكون > صفر.")
        price = _as_dec(spec.unit_price if spec.unit_price not in (None, "") else variant.price)
        if price < 0:
            raise InstallmentError(f"سعر البيع للمنتج {variant.sku} لا يمكن أن يكون سالبًا.")
        if Decimal(str(variant.stock_qty)) < qty:
            raise InstallmentError(
                f"الرصيد المتاح للمنتج {variant.display_name} ({variant.stock_qty}) "
                f"أقل من المطلوب ({qty})."
            )
        subtotal += _q(qty * price)
        resolved_lines.append((variant, qty, price))

    if discount_amount > subtotal:
        raise InstallmentError(f"الخصم ({discount_amount}) أكبر من الإجمالي الفرعي ({subtotal}).")

    # الضريبة
    tax_enabled = bool(get_setting("tax.enabled", False))
    tax_rate = _as_dec(get_setting("tax.default_rate", 0)) if tax_enabled else ZERO
    net_before_tax = _q(subtotal - discount_amount)
    tax_amount = _q(net_before_tax * tax_rate / Decimal("100")) if tax_enabled else ZERO
    total = _q(net_before_tax + tax_amount)

    if down_payment > total:
        raise InstallmentError(
            f"المقدم ({down_payment}) أكبر من إجمالي الفاتورة ({total})."
        )

    invoice.subtotal = _q(subtotal)
    invoice.discount_amount = _q(discount_amount)
    invoice.tax_rate = tax_rate
    invoice.tax_amount = tax_amount
    invoice.total = total

    # 2) إنشاء أسطر الفاتورة + خصم المخزون (تسليم فوري)
    for variant, qty, unit_price in resolved_lines:
        move = record_sale(
            variant_id=variant.id,
            qty=qty,
            move_date=invoice_date,
            source_type="sales_invoice",
            source_id=invoice.id,
            user_id=user_id,
            memo=f"بيع تقسيط بموجب فاتورة {invoice.doc_number}",
        )
        line = SalesInvoiceLine(
            invoice_id=invoice.id,
            variant_id=variant.id,
            product_name=variant.display_name,
            sku=variant.sku,
            qty=_q(qty),
            unit_price=_q(unit_price),
            line_total=_q(qty * unit_price),
            unit_cost=move.unit_cost,
        )
        db.session.add(line)
        total_cost += _q(qty * move.unit_cost)

    db.session.flush()

    # 3) قيد البيع (نفس منطق cash sale لكن بدون قيد تحصيل)
    revenue_account = _get_system_account("4100")
    vat_output_account = _get_system_account("2200") if tax_enabled and tax_amount > 0 else None
    discount_account = _get_system_account("4120") if discount_amount > 0 else None

    sale_lines: list[LedgerLineDraft] = [
        LedgerLineDraft(ar_account.id, debit=total, memo=f"فاتورة تقسيط {invoice.doc_number}"),
        LedgerLineDraft(revenue_account.id, credit=_q(subtotal),
                        memo=f"إيراد فاتورة {invoice.doc_number}"),
    ]
    if discount_account is not None:
        sale_lines.append(LedgerLineDraft(discount_account.id, debit=discount_amount,
                                          memo=f"خصم فاتورة {invoice.doc_number}"))
    if vat_output_account is not None:
        sale_lines.append(LedgerLineDraft(vat_output_account.id, credit=tax_amount,
                                          memo=f"ضريبة مخرجات فاتورة {invoice.doc_number}"))

    post_journal_entry(
        entry_date=invoice_date,
        source_type=JournalSourceType.SALES_INVOICE,
        source_id=invoice.id,
        memo=f"فاتورة بيع بالتقسيط {invoice.doc_number} — {customer.name_ar}",
        lines=sale_lines,
        user_id=user_id,
    )

    # 4) قيد المقدم (لو > 0) — كاش من العميل
    if down_payment > 0:
        cash = _get_system_account("1010")
        post_journal_entry(
            entry_date=invoice_date,
            source_type=JournalSourceType.CUSTOMER_RECEIPT,
            source_id=invoice.id,
            memo=f"مقدم فاتورة تقسيط {invoice.doc_number}",
            lines=[
                LedgerLineDraft(cash.id, debit=down_payment,
                                memo=f"مقدم فاتورة {invoice.doc_number}"),
                LedgerLineDraft(ar_account.id, credit=down_payment,
                                memo=f"مقدم فاتورة {invoice.doc_number}"),
            ],
            user_id=user_id,
        )

    # 5) قيد COGS
    if total_cost > 0:
        cogs_account = _get_system_account("5100")
        inventory_account = _get_system_account("1100")
        post_journal_entry(
            entry_date=invoice_date,
            source_type=JournalSourceType.SALES_INVOICE,
            source_id=invoice.id,
            memo=f"تكلفة بضاعة مباعة فاتورة {invoice.doc_number}",
            lines=[
                LedgerLineDraft(cogs_account.id, debit=total_cost,
                                memo=f"COGS فاتورة {invoice.doc_number}"),
                LedgerLineDraft(inventory_account.id, credit=total_cost,
                                memo=f"خصم مخزون فاتورة {invoice.doc_number}"),
            ],
            user_id=user_id,
        )

    # 6) إنشاء الخطة + جدول الأقساط
    financed = _q(total - down_payment)
    plan = InstallmentPlan(
        doc_number=next_document_number("installment_plan"),
        sales_invoice_id=invoice.id,
        customer_id=customer.id,
        total_amount=total,
        down_payment=_q(down_payment),
        financed_amount=financed,
        installments_count=installments_count,
        frequency=frequency,
        start_date=(start_date or _default_start_date(invoice_date, frequency)),
        status=InstallmentPlanStatus.ACTIVE,
        notes=notes,
        created_by_id=user_id,
    )
    db.session.add(plan)
    db.session.flush()

    # نبني الجدول: نُوزّع المبلغ الممول بالتساوي، ونضيف فرق التقريب لآخر قسط
    per_installment = _q(financed / Decimal(installments_count)) if financed > 0 else ZERO
    remaining_alloc = financed
    current_due = plan.start_date

    for i in range(1, installments_count + 1):
        if i < installments_count:
            amount = per_installment
        else:
            amount = _q(remaining_alloc)  # آخر قسط يمتص فرق التقريب
        db.session.add(InstallmentScheduleLine(
            plan_id=plan.id,
            number=i,
            due_date=current_due,
            amount=amount,
            paid_amount=ZERO,
            status=InstallmentLineStatus.PENDING,
        ))
        remaining_alloc -= amount
        current_due = _next_due_date(current_due, frequency)

    # لو الممول = 0 (المقدم = الإجمالي كامل): الخطة تُعلَّم مكتملة فورًا
    if financed <= 0:
        plan.status = InstallmentPlanStatus.COMPLETED

    db.session.flush()
    return plan


# ===================================================================
# 2) تحصيل قسط (كامل أو جزئي)
# ===================================================================

def collect_installment_payment(
    *,
    schedule_line_id: int,
    amount: Decimal | float | str,
    collection_date: date,
    method: str = "cash",
    bank_account_id: int | None = None,
    memo: str | None = None,
    user_id: int | None = None,
) -> InstallmentCollection:
    """يُطبِّق مبلغ سداد على قسط محدد ويُنشِئ قيد التحصيل."""

    line = db.session.get(InstallmentScheduleLine, schedule_line_id)
    if line is None:
        raise InstallmentError("القسط غير موجود.")

    plan = line.plan
    if plan.status != InstallmentPlanStatus.ACTIVE:
        raise InstallmentError(f"الخطة {plan.doc_number} ليست نشطة.")

    amount = _as_dec(amount)
    if amount <= 0:
        raise InstallmentError("مبلغ السداد يجب أن يكون > صفر.")

    remaining = line.remaining
    if amount > remaining:
        raise InstallmentError(
            f"مبلغ السداد ({amount}) أكبر من المتبقي على القسط ({remaining})."
        )

    # اختيار الحساب الدائن (النقدية أو البنك)
    method = (method or "cash").lower()
    if method == "cash":
        credit_side = _get_system_account("1010")
    elif method == "bank":
        if bank_account_id:
            credit_side = db.session.get(Account, bank_account_id)
            if credit_side is None or not credit_side.is_postable:
                raise InstallmentError("حساب البنك المحدد غير صالح.")
        else:
            parent = db.session.query(Account).filter_by(code="1020").one_or_none()
            if parent is None:
                raise InstallmentError("حساب البنك 1020 غير موجود.")
            child = (
                db.session.query(Account)
                .filter_by(parent_id=parent.id, is_active=True, is_postable=True)
                .order_by(Account.code)
                .first()
            )
            if child is None:
                raise InstallmentError("لا يوجد حساب بنك فرعي — أضف واحدًا أولاً.")
            credit_side = child
    else:
        raise InstallmentError(f"طريقة سداد غير مدعومة: {method}")

    customer_ar = _customer_ar_account(plan.customer)

    # القيد
    post_journal_entry(
        entry_date=collection_date,
        source_type=JournalSourceType.INSTALLMENT,
        source_id=plan.id,
        memo=f"تحصيل قسط {line.number}/{plan.installments_count} — خطة {plan.doc_number}",
        lines=[
            LedgerLineDraft(credit_side.id, debit=amount,
                            memo=f"تحصيل قسط {line.number} خطة {plan.doc_number}"),
            LedgerLineDraft(customer_ar.id, credit=amount,
                            memo=f"سداد قسط {line.number} من {plan.customer.name_ar}"),
        ],
        user_id=user_id,
    )

    # تحديث السطر
    line.paid_amount = _q(Decimal(str(line.paid_amount or 0)) + amount)
    if line.paid_amount >= Decimal(str(line.amount)):
        line.status = InstallmentLineStatus.PAID
        line.paid_at = datetime.now(timezone.utc)
    else:
        line.status = InstallmentLineStatus.PARTIAL

    # تسجيل التحصيل
    collection = InstallmentCollection(
        doc_number=next_document_number("customer_receipt"),
        plan_id=plan.id,
        schedule_line_id=line.id,
        collection_date=collection_date,
        amount=_q(amount),
        method=method,
        bank_account_id=(bank_account_id if method == "bank" else None),
        memo=memo,
        created_by_id=user_id,
    )
    db.session.add(collection)

    # لو كل الأقساط سُدَّت بالكامل → الخطة تكتمل
    db.session.flush()
    all_paid = all(l.status == InstallmentLineStatus.PAID for l in plan.schedule)
    if all_paid:
        plan.status = InstallmentPlanStatus.COMPLETED

    db.session.flush()
    return collection


# ===================================================================
# 3) رصد المتأخرات
# ===================================================================

def mark_overdue_installments(*, today: date | None = None) -> int:
    """يُحوِّل كل قسط تجاوز due_date ولم يُسدَّد بالكامل إلى OVERDUE.

    يُرجِع عدد الأسطر التي تم تحديثها.
    """
    today = today or date.today()
    lines = (
        db.session.query(InstallmentScheduleLine)
        .filter(InstallmentScheduleLine.due_date < today)
        .filter(InstallmentScheduleLine.status.in_([
            InstallmentLineStatus.PENDING, InstallmentLineStatus.PARTIAL,
        ]))
        .all()
    )
    for line in lines:
        line.status = InstallmentLineStatus.OVERDUE
    db.session.flush()
    return len(lines)


def get_overdue_lines(*, limit: int | None = None) -> list[InstallmentScheduleLine]:
    q = (
        db.session.query(InstallmentScheduleLine)
        .filter(InstallmentScheduleLine.status == InstallmentLineStatus.OVERDUE)
        .order_by(InstallmentScheduleLine.due_date)
    )
    if limit:
        q = q.limit(limit)
    return q.all()


def get_due_today_lines(*, today: date | None = None) -> list[InstallmentScheduleLine]:
    today = today or date.today()
    return (
        db.session.query(InstallmentScheduleLine)
        .filter(InstallmentScheduleLine.due_date == today)
        .filter(InstallmentScheduleLine.status.in_([
            InstallmentLineStatus.PENDING, InstallmentLineStatus.PARTIAL,
        ]))
        .order_by(InstallmentScheduleLine.due_date)
        .all()
    )


# ===================================================================
# Helpers
# ===================================================================

def _get_active_customer(customer_id: int) -> Party:
    p = db.session.get(Party, customer_id)
    if p is None or p.type != PartyType.CUSTOMER:
        raise InstallmentError("العميل غير موجود.")
    if not p.is_active:
        raise InstallmentError(f"العميل {p.name_ar} موقوف.")
    return p


def _customer_ar_account(customer: Party) -> Account:
    if customer.account is None:
        raise InstallmentError(
            f"العميل {customer.name_ar} ({customer.code}) ليس له حساب فرعي."
        )
    return customer.account


def _get_system_account(code: str) -> Account:
    acc = db.session.query(Account).filter_by(code=code).one_or_none()
    if acc is None:
        raise InstallmentError(f"الحساب النظامي {code} غير موجود.")
    if not acc.is_postable:
        raise InstallmentError(f"الحساب {code} غير قابل للترحيل المباشر.")
    return acc


def _next_due_date(current: date, frequency: InstallmentFrequency) -> date:
    if frequency == InstallmentFrequency.WEEKLY:
        return current + timedelta(days=7)
    if frequency == InstallmentFrequency.BIWEEKLY:
        return current + timedelta(days=14)
    # monthly
    return _add_month(current)


def _add_month(d: date) -> date:
    """يضيف شهرًا مع التعامل مع نهاية الشهر (31/12 → 31/1 = 31 يناير)."""
    year = d.year + (1 if d.month == 12 else 0)
    month = 1 if d.month == 12 else d.month + 1
    # نقصر اليوم على آخر يوم من الشهر التالي
    import calendar
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _default_start_date(invoice_date: date, frequency: InstallmentFrequency) -> date:
    """أول قسط بعد فترة واحدة من تاريخ الفاتورة."""
    return _next_due_date(invoice_date, frequency)


def _as_dec(x) -> Decimal:
    if x is None or x == "":
        return ZERO
    return x if isinstance(x, Decimal) else Decimal(str(x))


def _q(v: Decimal, places: int = 3) -> Decimal:
    return v.quantize(Decimal(10) ** -places)
