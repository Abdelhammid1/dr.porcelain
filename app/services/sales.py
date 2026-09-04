"""خدمة فواتير المبيعات — نقطة الإنشاء الوحيدة.

كل عملية بيع كاش تولّد داخل Transaction واحدة (Atomicity):

1) الفاتورة نفسها (SalesInvoice + Lines) بأرقامها الـ snapshot.
2) حركات المخزون (record_sale لكل سطر) — يخصم الرصيد ويحفظ cost snapshot.
3) قيد البيع:
     مدين  1200-CUST (ذمم العميل)  = total
     دائن  4100 (إيرادات المبيعات)   = subtotal - discount
     دائن  4120 (خصم مسموح به)      لا... الخصم يُخصَم من الإيراد مباشرةً (يوضّح في التصميم)
     دائن  2200 (VAT مخرجات)        = tax_amount
4) قيد التحصيل الفوري (لأن كاش):
     مدين  1010 (نقدية)  = total
     دائن  1200-CUST     = total
5) قيد COGS:
     مدين  5100 (COGS)   = Σ(qty × unit_cost)
     دائن  1100 (المخزون) = Σ(qty × unit_cost)

قواعد التحقق:
- عميل مطلوب وله account مرتبط.
- الفاتورة تحتوي على سطر واحد على الأقل.
- كل سطر: qty > 0، unit_price >= 0.
- الرصيد المتاح للمنتج ≥ الكمية المطلوبة.
- discount_amount ≥ 0 و ≤ subtotal.
- أي فشل جزئي → rollback كامل عبر SQLAlchemy transaction.

مرتجعات (create_sales_return):
- تُنشئ قيود عكسية موازية.
- تُعيد الكمية للمخزون بنفس التكلفة الأصلية.
- تُعيد رصيد العميل للحالة الأصلية ثم تُصدِر تحصيلًا سالبًا (رد نقدي).
- الاسترجاع الجزئي مدعوم (سطر أو كمية أقل).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from flask import current_app

from app.extensions import db
from app.models.account import Account
from app.models.journal import JournalSourceType
from app.models.party import Party, PartyType
from app.models.product import ProductVariant
from app.models.sales import (
    InvoiceStatus,
    PaymentMethod,
    SalesInvoice,
    SalesInvoiceLine,
    SalesReturn,
    SalesReturnLine,
)
from app.models.setting import get_setting
from app.services.inventory import record_sale, record_sale_return
from app.services.ledger import LedgerLineDraft, post_journal_entry, reverse_entry
from app.services.numbering import next_document_number


class SalesError(ValueError):
    pass


ZERO = Decimal("0")


@dataclass
class InvoiceLineDraft:
    """سطر فاتورة قبل الحفظ — يأتي من الفورم أو الـ API."""
    variant_id: int
    qty: Decimal | float | str
    unit_price: Decimal | float | str | None = None  # لو None نستخدم سعر المتغير


# =====================================================
# 1) إنشاء فاتورة بيع كاش
# =====================================================

def create_cash_sale(
    *,
    customer_id: int,
    invoice_date: date,
    lines: Iterable[InvoiceLineDraft],
    discount_amount: Decimal | float | str = 0,
    notes: str | None = None,
    user_id: int | None = None,
    payment_method: PaymentMethod = PaymentMethod.CASH,
    pos_session_id: int | None = None,
) -> SalesInvoice:
    """إنشاء فاتورة بيع كاش كاملة (فاتورة + مخزون + 3 قيود).

    لو `pos_session_id` مُحدَّد والدفع نقدي، تُقيَّد النقدية على حساب عهدة الكاشير
    (1030-XXX) بدلاً من الصندوق الرئيسي (1010).
    """

    customer = _get_active_customer(customer_id)
    ar_account = _customer_ar_account(customer)

    # التحقق من الوردية إن وُجدت
    pos_session = None
    if pos_session_id is not None:
        from app.models.pos import POSSession, SessionStatus
        pos_session = db.session.get(POSSession, pos_session_id)
        if pos_session is None:
            raise SalesError("الوردية المشار إليها غير موجودة.")
        if pos_session.status != SessionStatus.OPEN:
            raise SalesError(f"الوردية {pos_session.doc_number} مقفلة — لا يمكن البيع بها.")

    line_specs = list(lines)
    if not line_specs:
        raise SalesError("الفاتورة تحتاج سطرًا واحدًا على الأقل.")

    discount_amount = _as_dec(discount_amount)
    if discount_amount < 0:
        raise SalesError("الخصم لا يمكن أن يكون سالبًا.")

    # --- 1) بناء الفاتورة (بدون قيود ولا حركات بعد) ---
    invoice = SalesInvoice(
        doc_number=next_document_number("sales_invoice"),
        invoice_date=invoice_date,
        customer_id=customer.id,
        payment_method=payment_method,
        status=InvoiceStatus.POSTED,
        notes=(notes or None),
        created_by_id=user_id,
        pos_session_id=pos_session.id if pos_session else None,
    )
    db.session.add(invoice)
    db.session.flush()  # نحتاج invoice.id

    subtotal = ZERO
    total_cost = ZERO
    resolved_lines: list[tuple[ProductVariant, Decimal, Decimal]] = []  # (variant, qty, unit_price)

    for spec in line_specs:
        variant = db.session.get(ProductVariant, spec.variant_id)
        if variant is None or not variant.is_active:
            raise SalesError(f"المنتج المطلوب #{spec.variant_id} غير موجود أو موقوف.")

        qty = _as_dec(spec.qty)
        if qty <= 0:
            raise SalesError(f"الكمية للمنتج {variant.sku} يجب أن تكون أكبر من صفر.")

        price = _as_dec(spec.unit_price if spec.unit_price not in (None, "") else variant.price)
        if price < 0:
            raise SalesError(f"سعر البيع للمنتج {variant.sku} لا يمكن أن يكون سالبًا.")

        # فحص الرصيد (المخزون يُخصم في record_sale لكن نُبكر الرفض)
        if Decimal(str(variant.stock_qty)) < qty:
            raise SalesError(
                f"الرصيد المتاح للمنتج {variant.display_name} ({variant.stock_qty}) "
                f"أقل من المطلوب ({qty})."
            )

        line_total = _q(qty * price)
        subtotal += line_total
        resolved_lines.append((variant, qty, price))

    if discount_amount > subtotal:
        raise SalesError(f"الخصم ({discount_amount}) أكبر من الإجمالي الفرعي ({subtotal}).")

    # --- 2) حساب الضريبة ---
    tax_enabled = bool(get_setting("tax.enabled", False))
    tax_rate = _as_dec(get_setting("tax.default_rate", 0)) if tax_enabled else ZERO
    net_before_tax = _q(subtotal - discount_amount)
    tax_amount = _q(net_before_tax * tax_rate / Decimal("100")) if tax_enabled else ZERO
    total = _q(net_before_tax + tax_amount)

    invoice.subtotal = _q(subtotal)
    invoice.discount_amount = _q(discount_amount)
    invoice.tax_rate = tax_rate
    invoice.tax_amount = tax_amount
    invoice.total = total

    # --- 3) إنشاء أسطر الفاتورة + خصم المخزون ---
    for variant, qty, unit_price in resolved_lines:
        # سجل حركة البيع (يخصم الرصيد ويحفظ cost snapshot)
        move = record_sale(
            variant_id=variant.id,
            qty=qty,
            move_date=invoice_date,
            source_type="sales_invoice",
            source_id=invoice.id,
            user_id=user_id,
            memo=f"بيع بموجب فاتورة {invoice.doc_number}",
        )
        line = SalesInvoiceLine(
            invoice_id=invoice.id,
            variant_id=variant.id,
            product_name=variant.display_name,
            sku=variant.sku,
            qty=_q(qty),
            unit_price=_q(unit_price),
            line_total=_q(qty * unit_price),
            unit_cost=move.unit_cost,  # snapshot من حركة المخزون
        )
        db.session.add(line)
        total_cost += _q(qty * move.unit_cost)

    db.session.flush()

    # --- 4) القيد الأول: بيع (AR / إيراد + ضريبة) ---
    revenue_account = _get_system_account("4100")
    vat_output_account = _get_system_account("2200") if tax_enabled and tax_amount > 0 else None
    discount_account = _get_system_account("4120") if discount_amount > 0 else None

    sale_lines: list[LedgerLineDraft] = [
        LedgerLineDraft(ar_account.id, debit=total, memo=f"فاتورة {invoice.doc_number}"),
    ]
    # الإيراد الصافي = subtotal - discount؛ نُسجّل الإيراد الكامل ونضع الخصم كحساب مدين
    # حتى يظهر الخصم بشكل مستقل في التقارير (Contra Revenue).
    sale_lines.append(
        LedgerLineDraft(revenue_account.id, credit=_q(subtotal),
                        memo=f"إيراد فاتورة {invoice.doc_number}")
    )
    if discount_account is not None:
        # الخصم يُخصم من الإيراد (Contra Revenue) — نسجله كمدين على 4120
        # الترتيب المحاسبي: بدلاً من "دائن على 4100 = subtotal - discount"،
        # نسجل "دائن 4100 = subtotal" و "مدين 4120 = discount"
        # الأثر النهائي متطابق لكن يحفظ تفصيل الخصم للتقارير.
        sale_lines.append(
            LedgerLineDraft(discount_account.id, debit=discount_amount,
                            memo=f"خصم على فاتورة {invoice.doc_number}")
        )
    if vat_output_account is not None:
        sale_lines.append(
            LedgerLineDraft(vat_output_account.id, credit=tax_amount,
                            memo=f"ضريبة مخرجات فاتورة {invoice.doc_number}")
        )

    post_journal_entry(
        entry_date=invoice_date,
        source_type=JournalSourceType.SALES_INVOICE,
        source_id=invoice.id,
        memo=f"فاتورة بيع {invoice.doc_number} — {customer.name_ar}",
        lines=sale_lines,
        user_id=user_id,
    )

    # --- 5) قيد التحصيل الفوري (كاش) ---
    if total > 0:
        cash_or_bank = _cash_or_bank_account(payment_method, pos_session=pos_session)
        post_journal_entry(
            entry_date=invoice_date,
            source_type=JournalSourceType.CUSTOMER_RECEIPT,
            source_id=invoice.id,
            memo=f"تحصيل فاتورة {invoice.doc_number}",
            lines=[
                LedgerLineDraft(cash_or_bank.id, debit=total,
                                memo=f"تحصيل فاتورة {invoice.doc_number}"),
                LedgerLineDraft(ar_account.id, credit=total,
                                memo=f"سداد فاتورة {invoice.doc_number}"),
            ],
            user_id=user_id,
        )

    # --- 6) قيد COGS ---
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

    db.session.flush()
    return invoice


# =====================================================
# 2) إنشاء مرتجع بيع (كامل أو جزئي)
# =====================================================

@dataclass
class ReturnLineDraft:
    invoice_line_id: int
    qty: Decimal | float | str


def create_sales_return(
    *,
    invoice_id: int,
    return_date: date,
    reason: str,
    lines: Iterable[ReturnLineDraft] | None = None,
    user_id: int | None = None,
) -> SalesReturn:
    """يُنشِئ مرتجعًا للفاتورة — إذا `lines=None` يُعامَل كمرتجع كامل.

    - يُنشِئ سطر مرتجع لكل بند بكمية معتبرة.
    - يُعيد الكمية للمخزون بنفس تكلفة السطر الأصلي.
    - يُنشِئ قيود عكسية (Revenue+VAT+Cash+COGS) بنفس المنطق المعاكس.
    - يُحدِّث حالة الفاتورة (returned / partial_returned).
    """
    reason = (reason or "").strip()
    if not reason:
        raise SalesError("سبب المرتجع مطلوب.")

    invoice = db.session.get(SalesInvoice, invoice_id)
    if invoice is None:
        raise SalesError("الفاتورة غير موجودة.")
    if invoice.status == InvoiceStatus.RETURNED:
        raise SalesError(f"الفاتورة {invoice.doc_number} مرتجعة بالكامل بالفعل.")

    # فحص إعداد المدة المسموح بها للمرتجع
    window_days = int(get_setting("returns.window_days", 14) or 0)
    if window_days > 0:
        diff = (return_date - invoice.invoice_date).days
        if diff > window_days:
            raise SalesError(
                f"انتهت مدة المرتجع ({window_days} يوم). مرت {diff} يوم على الفاتورة."
            )

    allow_partial = bool(get_setting("returns.allow_partial", True))

    # لو lines=None → مرتجع كامل بكل الكميات المتاحة
    returnable = {ln.id: invoice.line_returnable_qty(ln) for ln in invoice.lines}

    if lines is None:
        return_specs = [
            ReturnLineDraft(invoice_line_id=ln_id, qty=q)
            for ln_id, q in returnable.items() if q > 0
        ]
        is_full_intent = True
    else:
        return_specs = list(lines)
        is_full_intent = False
        if not allow_partial:
            # لا نسمح إلا بمرتجع كامل — يجب أن تكون كل الكميات = الأصلية
            for spec in return_specs:
                if _as_dec(spec.qty) != returnable.get(spec.invoice_line_id, ZERO):
                    raise SalesError("الاسترجاع الجزئي معطّل من الإعدادات.")

    if not return_specs:
        raise SalesError("لا توجد كميات قابلة للمرتجع.")

    # --- 1) إنشاء السطر السجل السطري ---
    ret = SalesReturn(
        doc_number=next_document_number("sales_return"),
        return_date=return_date,
        invoice_id=invoice.id,
        reason=reason,
        created_by_id=user_id,
        is_full_return=False,  # نُحدد لاحقًا بعد التحقق
    )
    db.session.add(ret)
    db.session.flush()

    total_refund_ex_tax = ZERO
    total_refund_cost = ZERO
    total_refund_discount = ZERO  # حصة المرتجع من الخصم

    invoice_line_map = {ln.id: ln for ln in invoice.lines}

    for spec in return_specs:
        line = invoice_line_map.get(spec.invoice_line_id)
        if line is None:
            raise SalesError(f"السطر #{spec.invoice_line_id} لا يخص هذه الفاتورة.")

        qty = _as_dec(spec.qty)
        if qty <= 0:
            continue
        max_qty = returnable.get(line.id, ZERO)
        if qty > max_qty:
            raise SalesError(
                f"الكمية {qty} أكبر من المتبقي القابل للمرتجع ({max_qty}) للسطر {line.sku}."
            )

        # ارجاع الكمية للمخزون
        record_sale_return(
            variant_id=line.variant_id,
            qty=qty,
            unit_cost=Decimal(str(line.unit_cost)),
            move_date=return_date,
            source_type="sales_return",
            source_id=ret.id,
            user_id=user_id,
            memo=f"مرتجع {ret.doc_number} — {reason}",
        )

        db.session.add(SalesReturnLine(
            return_id=ret.id,
            invoice_line_id=line.id,
            variant_id=line.variant_id,
            qty=_q(qty),
            unit_price=Decimal(str(line.unit_price)),
            unit_cost=Decimal(str(line.unit_cost)),
        ))

        line_ratio = qty / Decimal(str(line.qty))
        total_refund_ex_tax += _q(qty * Decimal(str(line.unit_price)))
        total_refund_cost += _q(qty * Decimal(str(line.unit_cost)))

    if total_refund_ex_tax <= 0:
        raise SalesError("لم يتم إدخال أي كمية للمرتجع.")

    # نسبة المرتجع من إجمالي الفاتورة (لتوزيع الخصم والضريبة)
    if invoice.subtotal > 0:
        return_ratio = total_refund_ex_tax / Decimal(str(invoice.subtotal))
    else:
        return_ratio = Decimal("0")

    refund_discount = _q(Decimal(str(invoice.discount_amount)) * return_ratio)
    net_refund_ex_tax = _q(total_refund_ex_tax - refund_discount)
    refund_tax = _q(Decimal(str(invoice.tax_amount)) * return_ratio)
    total_refund = _q(net_refund_ex_tax + refund_tax)

    ret.refund_amount = total_refund
    ret.refund_tax = refund_tax

    # --- 2) القيود العكسية ---
    customer_ar = _customer_ar_account(invoice.customer)
    revenue_account = _get_system_account("4100")
    discount_account = _get_system_account("4120")
    vat_output_account = _get_system_account("2200")
    cogs_account = _get_system_account("5100")
    inventory_account = _get_system_account("1100")
    # لو المرتجع لفاتورة POS، نُعيد النقدية من عهدة الكاشير (الوردية لا تزال مفتوحة عادةً؛
    # إن كانت مقفلة نستخدم الصندوق الرئيسي — المُرتجع الآن يُخصم من الصندوق).
    original_pos_session = None
    if invoice.pos_session_id is not None:
        from app.models.pos import POSSession, SessionStatus
        original_pos_session = db.session.get(POSSession, invoice.pos_session_id)
        if original_pos_session and original_pos_session.status != SessionStatus.OPEN:
            original_pos_session = None  # الوردية مقفلة → نستخدم الصندوق
    cash_account = _cash_or_bank_account(invoice.payment_method, pos_session=original_pos_session)

    # عكس قيد البيع: مدين 4100 (يخفض الإيراد) / دائن 1200-CUST
    sale_reverse: list[LedgerLineDraft] = [
        LedgerLineDraft(revenue_account.id, debit=_q(total_refund_ex_tax),
                        memo=f"مرتجع {ret.doc_number}"),
    ]
    if refund_discount > 0:
        # عكس الخصم: كان مدين 4120، يصبح دائن 4120
        sale_reverse.append(LedgerLineDraft(
            discount_account.id, credit=refund_discount,
            memo=f"عكس خصم مرتجع {ret.doc_number}"
        ))
    if refund_tax > 0:
        sale_reverse.append(LedgerLineDraft(
            vat_output_account.id, debit=refund_tax,
            memo=f"عكس ضريبة مرتجع {ret.doc_number}"
        ))
    sale_reverse.append(LedgerLineDraft(
        customer_ar.id, credit=total_refund,
        memo=f"مرتجع فاتورة {invoice.doc_number}"
    ))

    post_journal_entry(
        entry_date=return_date,
        source_type=JournalSourceType.SALES_RETURN,
        source_id=ret.id,
        memo=f"مرتجع بيع {ret.doc_number} — {invoice.doc_number}",
        lines=sale_reverse,
        user_id=user_id,
    )

    # عكس التحصيل: مدين 1200-CUST / دائن 1010 (رد نقدي)
    if total_refund > 0:
        post_journal_entry(
            entry_date=return_date,
            source_type=JournalSourceType.CUSTOMER_RECEIPT,
            source_id=ret.id,
            memo=f"رد نقدي مرتجع {ret.doc_number}",
            lines=[
                LedgerLineDraft(customer_ar.id, debit=total_refund,
                                memo=f"رد نقدي مرتجع {ret.doc_number}"),
                LedgerLineDraft(cash_account.id, credit=total_refund,
                                memo=f"رد نقدي مرتجع {ret.doc_number}"),
            ],
            user_id=user_id,
        )

    # عكس COGS: مدين 1100 / دائن 5100
    if total_refund_cost > 0:
        post_journal_entry(
            entry_date=return_date,
            source_type=JournalSourceType.SALES_RETURN,
            source_id=ret.id,
            memo=f"عودة تكلفة مرتجع {ret.doc_number}",
            lines=[
                LedgerLineDraft(inventory_account.id, debit=total_refund_cost,
                                memo=f"عودة مخزون مرتجع {ret.doc_number}"),
                LedgerLineDraft(cogs_account.id, credit=total_refund_cost,
                                memo=f"عكس COGS مرتجع {ret.doc_number}"),
            ],
            user_id=user_id,
        )

    # --- 3) تحديث حالة الفاتورة ---
    db.session.flush()
    # نُجدِّد علاقة returns لأن ret الجديد قد لا يظهر في invoice.returns
    db.session.refresh(invoice, ["returns"])

    still_returnable = sum(
        (invoice.line_returnable_qty(ln) for ln in invoice.lines), ZERO
    )
    if still_returnable <= 0:
        invoice.status = InvoiceStatus.RETURNED
        ret.is_full_return = True
    else:
        invoice.status = InvoiceStatus.PARTIAL_RETURNED

    db.session.flush()
    return ret


# =====================================================
# Helpers
# =====================================================

def _get_active_customer(customer_id: int) -> Party:
    p = db.session.get(Party, customer_id)
    if p is None or p.type != PartyType.CUSTOMER:
        raise SalesError("العميل غير موجود.")
    if not p.is_active:
        raise SalesError(f"العميل {p.name_ar} موقوف.")
    return p


def _customer_ar_account(customer: Party) -> Account:
    if customer.account is None:
        raise SalesError(
            f"العميل {customer.name_ar} ({customer.code}) ليس له حساب فرعي — أعد إنشاءه."
        )
    return customer.account


def _get_system_account(code: str) -> Account:
    acc = db.session.query(Account).filter_by(code=code).one_or_none()
    if acc is None:
        raise SalesError(f"الحساب النظامي {code} غير موجود — شغّل init-coa.")
    if not acc.is_postable:
        raise SalesError(f"الحساب {code} غير قابل للترحيل المباشر.")
    return acc


def _cash_or_bank_account(method: PaymentMethod, pos_session=None) -> Account:
    """يُرجِع الحساب الذي يُقيَّد فيه التحصيل حسب طريقة الدفع.

    - CASH داخل POS → عهدة الكاشير (1030-XXX) من session.custody_account
    - CASH عادي → الصندوق الرئيسي (1010)
    - BANK → أول حساب بنك فرعي تحت 1020
    - CARD/WALLET → عهدة الكاشير لو داخل POS، وإلا الصندوق (يمكن توسيعه لاحقًا لحساب مستقل)
    """
    if method == PaymentMethod.CASH:
        if pos_session is not None:
            return pos_session.custody_account
        return _get_system_account("1010")
    if method in (PaymentMethod.CARD, PaymentMethod.WALLET):
        # مؤقتًا: نعامله كنقدية داخل الوردية (يمكن ربطه بحساب بطاقة/محفظة مستقل لاحقًا)
        if pos_session is not None:
            return pos_session.custody_account
        return _get_system_account("1010")
    # للبنك: نأخذ أول حساب فرعي تحت 1020 (سنسمح باختيار حساب بنك محدد لاحقًا).
    bank_parent = db.session.query(Account).filter_by(code="1020").one_or_none()
    if bank_parent is None:
        raise SalesError("حساب البنك 1020 غير موجود.")
    child = (
        db.session.query(Account)
        .filter_by(parent_id=bank_parent.id, is_active=True, is_postable=True)
        .order_by(Account.code)
        .first()
    )
    if child is None:
        raise SalesError("لا يوجد حساب بنك فرعي مُعدّ — أضف حساب بنك من دليل الحسابات.")
    return child


def _as_dec(x) -> Decimal:
    if x is None or x == "":
        return ZERO
    return x if isinstance(x, Decimal) else Decimal(str(x))


def _q(v: Decimal, places: int = 3) -> Decimal:
    q = Decimal(10) ** -places
    return v.quantize(q)
