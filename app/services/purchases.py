"""خدمة فواتير المشتريات — 3 طرق دفع.

القيود لكل طريقة (حسب معايير القبول في الوثيقة):

CASH  (نقدي فوري):    مدين 1100 (المخزون) + 1300 (VAT مدخلات) / دائن 1010 (نقدية)
BANK  (بنك فوري):     مدين 1100 + 1300 / دائن 1020-xxx (البنك المُختار)
CREDIT (آجل بالكامل): مدين 1100 + 1300 / دائن 2100-VVV (حساب المورد الفرعي)

- الشحن (freight) يُضاف على تكلفة المخزون (Capitalized) — يُوزَّع نسبيًا على الأسطر.
- الخصم (لو موجود) يخفّض تكلفة المخزون — Contra إلى حساب 5120 خصم مكتسب.
- كل عملية شراء تُعيد حساب متوسط التكلفة (Weighted Average) عبر record_purchase().
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from app.extensions import db
from app.models.account import Account
from app.models.journal import JournalSourceType
from app.models.party import Party, PartyType
from app.models.product import ProductVariant
from app.models.purchases import (
    PurchaseInvoice,
    PurchaseInvoiceLine,
    PurchasePayment,
    PurchaseReturn,
    PurchaseReturnLine,
    PurchaseStatus,
)
from app.models.setting import get_setting
from app.services.inventory import record_purchase, record_purchase_return
from app.services.ledger import LedgerLineDraft, post_journal_entry
from app.services.numbering import next_document_number


class PurchaseError(ValueError):
    pass


ZERO = Decimal("0")


@dataclass
class PurchaseLineDraft:
    variant_id: int
    qty: Decimal | float | str
    unit_cost: Decimal | float | str


# ==============================
# إنشاء فاتورة شراء
# ==============================

def create_purchase_invoice(
    *,
    vendor_id: int,
    invoice_date: date,
    payment_method: PurchasePayment,
    lines: Iterable[PurchaseLineDraft],
    discount_amount: Decimal | float | str = 0,
    freight: Decimal | float | str = 0,
    vendor_ref: str | None = None,
    bank_account_id: int | None = None,
    notes: str | None = None,
    user_id: int | None = None,
) -> PurchaseInvoice:
    vendor = _get_active_vendor(vendor_id)
    vendor_account = _vendor_ap_account(vendor)

    line_specs = list(lines)
    if not line_specs:
        raise PurchaseError("الفاتورة تحتاج سطرًا واحدًا على الأقل.")

    discount_amount = _as_dec(discount_amount)
    freight = _as_dec(freight)
    if discount_amount < 0 or freight < 0:
        raise PurchaseError("الخصم والشحن لا يمكن أن يكونا سالبين.")

    # --- 1) بناء الفاتورة والأسطر ---
    invoice = PurchaseInvoice(
        doc_number=next_document_number("purchase_invoice"),
        invoice_date=invoice_date,
        vendor_id=vendor.id,
        payment_method=payment_method,
        vendor_ref=(vendor_ref or None),
        status=PurchaseStatus.POSTED,
        notes=(notes or None),
        created_by_id=user_id,
    )
    db.session.add(invoice)
    db.session.flush()

    resolved_lines: list[tuple[ProductVariant, Decimal, Decimal]] = []
    subtotal = ZERO

    for spec in line_specs:
        variant = db.session.get(ProductVariant, spec.variant_id)
        if variant is None or not variant.is_active:
            raise PurchaseError(f"المنتج #{spec.variant_id} غير موجود أو موقوف.")
        qty = _as_dec(spec.qty)
        unit_cost = _as_dec(spec.unit_cost)
        if qty <= 0 or unit_cost < 0:
            raise PurchaseError(f"قيم غير صحيحة للمنتج {variant.sku}.")
        subtotal += _q(qty * unit_cost)
        resolved_lines.append((variant, qty, unit_cost))

    if discount_amount > subtotal:
        raise PurchaseError(f"الخصم ({discount_amount}) أكبر من الإجمالي الفرعي ({subtotal}).")

    # --- 2) الضريبة ---
    tax_enabled = bool(get_setting("tax.enabled", False))
    tax_rate = _as_dec(get_setting("tax.default_rate", 0)) if tax_enabled else ZERO
    net_before_tax = _q(subtotal - discount_amount + freight)  # الشحن يدخل في الوعاء الضريبي
    tax_amount = _q(net_before_tax * tax_rate / Decimal("100")) if tax_enabled else ZERO
    total = _q(net_before_tax + tax_amount)

    invoice.subtotal = _q(subtotal)
    invoice.discount_amount = _q(discount_amount)
    invoice.freight = _q(freight)
    invoice.tax_rate = tax_rate
    invoice.tax_amount = tax_amount
    invoice.total = total

    # --- 3) توزيع الشحن والخصم نسبيًا على الأسطر لتحديث تكلفة المخزون الفعلية ---
    # كل سطر تكلفته الفعلية = (unit_cost * qty * ratio_after_discount_and_freight)
    net_cost_before_alloc = subtotal - discount_amount + freight  # ما يدخل المخزون فعلاً
    for variant, qty, unit_cost in resolved_lines:
        line_subtotal = _q(qty * unit_cost)
        ratio = line_subtotal / subtotal if subtotal > 0 else Decimal("0")
        allocated_cost = _q(net_cost_before_alloc * ratio)
        effective_unit_cost = _q(allocated_cost / qty) if qty > 0 else unit_cost

        # سطر الفاتورة يحفظ التكلفة المُعلَنة (unit_cost الأصلية) — للأرشيف الحرفي.
        db.session.add(PurchaseInvoiceLine(
            invoice_id=invoice.id,
            variant_id=variant.id,
            product_name=variant.display_name,
            sku=variant.sku,
            qty=_q(qty),
            unit_cost=_q(unit_cost),
            line_total=line_subtotal,
        ))

        # حركة المخزون تستخدم التكلفة الفعلية (بعد توزيع الشحن والخصم) لضبط avg_cost بدقة.
        record_purchase(
            variant_id=variant.id,
            qty=qty,
            unit_cost=effective_unit_cost,
            move_date=invoice_date,
            source_type="purchase_invoice",
            source_id=invoice.id,
            user_id=user_id,
            memo=f"شراء بموجب فاتورة {invoice.doc_number}",
        )

    db.session.flush()

    # --- 4) القيد المحاسبي ---
    inventory_acc = _get_system_account("1100")
    vat_input_acc = _get_system_account("1300") if tax_enabled and tax_amount > 0 else None
    discount_acc = _get_system_account("5120") if discount_amount > 0 else None

    # جانب المدين: المخزون (بعد توزيع الشحن — قيمة net_cost_before_alloc) + الضريبة
    lines_ledger: list[LedgerLineDraft] = [
        LedgerLineDraft(inventory_acc.id, debit=_q(net_cost_before_alloc),
                        memo=f"فاتورة شراء {invoice.doc_number}"),
    ]
    if vat_input_acc is not None:
        lines_ledger.append(LedgerLineDraft(
            vat_input_acc.id, debit=tax_amount,
            memo=f"ضريبة مدخلات فاتورة {invoice.doc_number}"
        ))

    # لو فيه خصم مكتسب، نُظهره كإيراد Contra COGS (دائن 5120).
    # لكن لأن الخصم يقلل تكلفة المخزون بالفعل (net_cost_before_alloc = subtotal - discount + freight)،
    # فإن قيد المخزون الآن أقل بمقدار الخصم. لتحقيق التوازن نضيف خصم مكتسب كدائن
    # وحساب موازِن. الطريقة الأصح: لا نُخفّض المخزون بالخصم، بل نسجل الخصم كدائن مستقل.
    # سنعيد بناء القيد بالطريقة الأصح: المخزون يأخذ subtotal+freight، والخصم يظهر كدائن.

    lines_ledger = []
    lines_ledger.append(LedgerLineDraft(
        inventory_acc.id, debit=_q(subtotal + freight),
        memo=f"مخزون شراء {invoice.doc_number}"
    ))
    if vat_input_acc is not None:
        lines_ledger.append(LedgerLineDraft(
            vat_input_acc.id, debit=tax_amount,
            memo=f"ضريبة مدخلات فاتورة {invoice.doc_number}"
        ))
    if discount_acc is not None:
        lines_ledger.append(LedgerLineDraft(
            discount_acc.id, credit=discount_amount,
            memo=f"خصم مكتسب فاتورة {invoice.doc_number}"
        ))

    # جانب الدائن: الحساب الذي يُسدَّد منه
    credit_account = _resolve_credit_account(payment_method, vendor_account, bank_account_id, invoice)
    lines_ledger.append(LedgerLineDraft(
        credit_account.id, credit=total,
        memo=f"سداد/التزام فاتورة {invoice.doc_number}"
    ))

    post_journal_entry(
        entry_date=invoice_date,
        source_type=JournalSourceType.PURCHASE_INVOICE,
        source_id=invoice.id,
        memo=f"فاتورة شراء {invoice.doc_number} — {vendor.name_ar}",
        lines=lines_ledger,
        user_id=user_id,
    )

    db.session.flush()
    return invoice


# ==============================
# مرتجع مشتريات
# ==============================

@dataclass
class PurchaseReturnLineDraft:
    invoice_line_id: int
    qty: Decimal | float | str


def create_purchase_return(
    *,
    invoice_id: int,
    return_date: date,
    reason: str,
    lines: Iterable[PurchaseReturnLineDraft] | None = None,
    user_id: int | None = None,
) -> PurchaseReturn:
    reason = (reason or "").strip()
    if not reason:
        raise PurchaseError("سبب المرتجع مطلوب.")

    invoice = db.session.get(PurchaseInvoice, invoice_id)
    if invoice is None:
        raise PurchaseError("الفاتورة غير موجودة.")
    if invoice.status == PurchaseStatus.RETURNED:
        raise PurchaseError(f"الفاتورة {invoice.doc_number} مرتجعة بالكامل بالفعل.")

    returnable = {ln.id: invoice.line_returnable_qty(ln) for ln in invoice.lines}
    if lines is None:
        return_specs = [
            PurchaseReturnLineDraft(invoice_line_id=lid, qty=q)
            for lid, q in returnable.items() if q > 0
        ]
    else:
        return_specs = list(lines)

    if not return_specs:
        raise PurchaseError("لا توجد كميات قابلة للمرتجع.")

    ret = PurchaseReturn(
        doc_number=next_document_number("purchase_return"),
        return_date=return_date,
        invoice_id=invoice.id,
        reason=reason,
        created_by_id=user_id,
    )
    db.session.add(ret)
    db.session.flush()

    total_refund_ex_tax = ZERO
    invoice_line_map = {ln.id: ln for ln in invoice.lines}

    for spec in return_specs:
        line = invoice_line_map.get(spec.invoice_line_id)
        if line is None:
            raise PurchaseError(f"السطر #{spec.invoice_line_id} لا يخص هذه الفاتورة.")
        qty = _as_dec(spec.qty)
        if qty <= 0:
            continue
        max_qty = returnable.get(line.id, ZERO)
        if qty > max_qty:
            raise PurchaseError(
                f"الكمية {qty} أكبر من المتبقي القابل للمرتجع ({max_qty}) للسطر {line.sku}."
            )

        record_purchase_return(
            variant_id=line.variant_id,
            qty=qty,
            unit_cost=Decimal(str(line.unit_cost)),
            move_date=return_date,
            source_type="purchase_return",
            source_id=ret.id,
            user_id=user_id,
            memo=f"مرتجع {ret.doc_number} — {reason}",
        )

        db.session.add(PurchaseReturnLine(
            return_id=ret.id,
            invoice_line_id=line.id,
            variant_id=line.variant_id,
            qty=_q(qty),
            unit_cost=Decimal(str(line.unit_cost)),
        ))

        total_refund_ex_tax += _q(qty * Decimal(str(line.unit_cost)))

    if total_refund_ex_tax <= 0:
        raise PurchaseError("لم يتم إدخال أي كمية للمرتجع.")

    # توزيع نسبي للضريبة/الخصم/الشحن
    if invoice.subtotal > 0:
        ratio = total_refund_ex_tax / Decimal(str(invoice.subtotal))
    else:
        ratio = Decimal("0")

    refund_tax = _q(Decimal(str(invoice.tax_amount)) * ratio)
    refund_discount = _q(Decimal(str(invoice.discount_amount)) * ratio)
    refund_freight = _q(Decimal(str(invoice.freight)) * ratio)
    net_refund_ex_tax = _q(total_refund_ex_tax - refund_discount + refund_freight)
    total_refund = _q(net_refund_ex_tax + refund_tax)

    ret.refund_amount = total_refund
    ret.refund_tax = refund_tax

    # --- القيد العكسي ---
    inventory_acc = _get_system_account("1100")
    vat_input_acc = _get_system_account("1300")
    discount_acc = _get_system_account("5120")
    vendor_account = _vendor_ap_account(invoice.vendor)

    lines_ledger: list[LedgerLineDraft] = [
        LedgerLineDraft(inventory_acc.id, credit=_q(total_refund_ex_tax + refund_freight),
                        memo=f"مرتجع مخزون {ret.doc_number}"),
    ]
    if refund_tax > 0:
        lines_ledger.append(LedgerLineDraft(
            vat_input_acc.id, credit=refund_tax,
            memo=f"عكس ضريبة مرتجع {ret.doc_number}"
        ))
    if refund_discount > 0:
        lines_ledger.append(LedgerLineDraft(
            discount_acc.id, debit=refund_discount,
            memo=f"عكس خصم مكتسب مرتجع {ret.doc_number}"
        ))

    # جانب المدين النهائي حسب طريقة الدفع
    credit_side_account = _resolve_credit_account(
        invoice.payment_method, vendor_account,
        invoice.bank_account_id, invoice
    )
    lines_ledger.append(LedgerLineDraft(
        credit_side_account.id, debit=total_refund,
        memo=f"استرداد نقدي/تخفيض ذمة مرتجع {ret.doc_number}"
    ))

    post_journal_entry(
        entry_date=return_date,
        source_type=JournalSourceType.PURCHASE_RETURN,
        source_id=ret.id,
        memo=f"مرتجع شراء {ret.doc_number} — {invoice.doc_number}",
        lines=lines_ledger,
        user_id=user_id,
    )

    # تحديث الحالة
    db.session.flush()
    db.session.refresh(invoice, ["returns"])
    still_returnable = sum(
        (invoice.line_returnable_qty(ln) for ln in invoice.lines), ZERO
    )
    if still_returnable <= 0:
        invoice.status = PurchaseStatus.RETURNED
        ret.is_full_return = True
    else:
        invoice.status = PurchaseStatus.PARTIAL_RETURNED

    db.session.flush()
    return ret


# ---------- Helpers ----------

def _get_active_vendor(vendor_id: int) -> Party:
    v = db.session.get(Party, vendor_id)
    if v is None or v.type != PartyType.VENDOR:
        raise PurchaseError("المورد غير موجود.")
    if not v.is_active:
        raise PurchaseError(f"المورد {v.name_ar} موقوف.")
    return v


def _vendor_ap_account(vendor: Party) -> Account:
    if vendor.account is None:
        raise PurchaseError(f"المورد {vendor.name_ar} ({vendor.code}) ليس له حساب فرعي.")
    return vendor.account


def _get_system_account(code: str) -> Account:
    acc = db.session.query(Account).filter_by(code=code).one_or_none()
    if acc is None:
        raise PurchaseError(f"الحساب النظامي {code} غير موجود.")
    if not acc.is_postable:
        raise PurchaseError(f"الحساب {code} غير قابل للترحيل المباشر.")
    return acc


def _resolve_credit_account(
    payment_method: PurchasePayment,
    vendor_account: Account,
    bank_account_id: int | None,
    invoice: PurchaseInvoice,
) -> Account:
    if payment_method == PurchasePayment.CASH:
        return _get_system_account("1010")
    if payment_method == PurchasePayment.BANK:
        if bank_account_id:
            acc = db.session.get(Account, bank_account_id)
            if acc is None or not acc.is_postable:
                raise PurchaseError("حساب البنك المحدد غير صالح.")
            # نُخزِّن الاختيار على الفاتورة
            invoice.bank_account_id = acc.id
            return acc
        # لم يُحدَّد بنك — نأخذ أول حساب فرعي تحت 1020
        parent = db.session.query(Account).filter_by(code="1020").one()
        child = (
            db.session.query(Account)
            .filter_by(parent_id=parent.id, is_active=True, is_postable=True)
            .order_by(Account.code)
            .first()
        )
        if child is None:
            raise PurchaseError("لا يوجد حساب بنك فرعي — أضف حسابًا تحت 1020 أولاً.")
        invoice.bank_account_id = child.id
        return child
    # CREDIT
    return vendor_account


def _as_dec(x) -> Decimal:
    if x is None or x == "":
        return ZERO
    return x if isinstance(x, Decimal) else Decimal(str(x))


def _q(v: Decimal, places: int = 3) -> Decimal:
    return v.quantize(Decimal(10) ** -places)
