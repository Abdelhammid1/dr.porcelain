"""ربط الطلبات الأونلاين بالمحرك المحاسبي (Phase 8).

عند وصول طلب أونلاين لحالة "تم التسليم" (DELIVERED):
1) نُنشئ SalesInvoice تُطابق الطلب (نفس بنود، نفس عميل، نفس مبالغ)
2) نستخدم unit_cost snapshots من InventoryMovements التي أُنشئت وقت الطلب
   (لأن avg_cost ربما اختلف بين تاريخ الطلب وتاريخ التسليم — القيمة الصحيحة
   محاسبيًا هي التي كانت وقت خروج البضاعة)
3) نُنشئ 3 قيود مطابقة لمنطق Epic 1.4:
     - قيد البيع: مدين AR / دائن Revenue + VAT
     - قيد التحصيل: مدين Cash / دائن AR  (Cash on Delivery)
     - قيد COGS: مدين COGS / دائن Inventory
4) نربط order.sales_invoice_id بالفاتورة الجديدة

**لا نُخصم المخزون مرة أخرى** — تم الخصم في Phase 6 عند إنشاء الطلب.

للمرتجعات بعد التسليم: نستخدم `create_sales_return` من services.sales مباشرة.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.inventory import InventoryMovement, MovementType
from app.models.journal import JournalSourceType
from app.models.order import Order, OrderStatus
from app.models.party import Party, PartyType
from app.models.sales import InvoiceStatus, PaymentMethod, SalesInvoice, SalesInvoiceLine
from app.services.ledger import LedgerLineDraft, post_journal_entry
from app.services.numbering import next_document_number


class OrderAccountingError(ValueError):
    pass


ZERO = Decimal("0")


def _ensure_walkin_customer() -> Party:
    """يعيد (أو ينشئ) عميل نقدي عام للطلبات الأونلاين بلا Party مرتبط."""
    from app.blueprints.pos.routes import _ensure_default_walkin_customer
    return _ensure_default_walkin_customer()


def _get_customer(order: Order) -> Party:
    """يُرجع العميل المرتبط بالطلب أو الـ walk-in لو ضيف."""
    if order.customer_id:
        p = db.session.get(Party, order.customer_id)
        if p and p.type == PartyType.CUSTOMER:
            return p
    return _ensure_walkin_customer()


def _customer_ar_account(customer: Party) -> Account:
    if customer.account is None:
        raise OrderAccountingError(
            f"العميل {customer.name_ar} ({customer.code}) ليس له حساب فرعي."
        )
    return customer.account


def _get_system_account(code: str) -> Account:
    acc = db.session.query(Account).filter_by(code=code).one_or_none()
    if acc is None:
        raise OrderAccountingError(f"الحساب النظامي {code} غير موجود.")
    return acc


def _line_cost_snapshot(order_id: int, variant_id: int) -> Decimal:
    """يستخرج تكلفة الوحدة الفعلية التي سُجّلت وقت إنشاء الطلب."""
    move = (
        db.session.query(InventoryMovement)
        .filter_by(
            source_type="order",
            source_id=order_id,
            variant_id=variant_id,
            move_type=MovementType.SALE,
        )
        .first()
    )
    if move is not None:
        return Decimal(str(move.unit_cost))
    # fallback: استخدم avg_cost الحالي (نادر — لو InventoryMovement اختفى)
    from app.models.product import ProductVariant
    v = db.session.get(ProductVariant, variant_id)
    return Decimal(str(v.avg_cost or 0)) if v else ZERO


# ============================================================
# 1) hook التسليم — يُنشئ الفاتورة + القيود
# ============================================================

def create_invoice_from_delivered_order(*, order_id: int, user_id: int | None = None) -> SalesInvoice:
    """يُنشئ SalesInvoice تلقائيًا من طلب تم تسليمه.

    يُستدعى تلقائيًا من `orders.transition_status` عندما يصبح status=DELIVERED.
    يمكن استدعاؤه يدويًا أيضًا (مثلاً لطلب قديم قبل تفعيل الـ hook).
    """
    order = db.session.get(Order, order_id)
    if order is None:
        raise OrderAccountingError("الطلب غير موجود.")
    if order.sales_invoice_id is not None:
        raise OrderAccountingError(
            f"الطلب {order.doc_number} له فاتورة مرتبطة بالفعل "
            f"(SalesInvoice #{order.sales_invoice_id})."
        )

    customer = _get_customer(order)
    ar_account = _customer_ar_account(customer)

    # 1) بناء الفاتورة
    invoice = SalesInvoice(
        doc_number=next_document_number("sales_invoice"),
        invoice_date=order.order_date,
        customer_id=customer.id,
        payment_method=PaymentMethod.CASH,  # COD → cash on delivery
        subtotal=Decimal(str(order.subtotal)),
        discount_amount=Decimal(str(order.discount_amount or 0)),
        tax_rate=Decimal(str(order.tax_rate or 0)),
        tax_amount=Decimal(str(order.tax_amount or 0)),
        total=Decimal(str(order.total)),
        status=InvoiceStatus.POSTED,
        notes=(
            f"فاتورة تلقائية من طلب أونلاين {order.doc_number}"
            + (f" — {order.notes}" if order.notes else "")
        ),
        created_by_id=user_id,
    )
    db.session.add(invoice)
    db.session.flush()

    # 2) بناء أسطر الفاتورة من أسطر الطلب + التكلفة الأصلية
    total_cost = ZERO
    for oline in order.lines:
        unit_cost = _line_cost_snapshot(order.id, oline.variant_id)
        db.session.add(SalesInvoiceLine(
            invoice_id=invoice.id,
            variant_id=oline.variant_id,
            product_name=oline.product_name,
            sku=oline.sku,
            qty=Decimal(str(oline.qty)),
            unit_price=Decimal(str(oline.unit_price)),
            line_total=Decimal(str(oline.line_total)),
            unit_cost=unit_cost,
        ))
        total_cost += (Decimal(str(oline.qty)) * unit_cost).quantize(Decimal("0.001"))

    db.session.flush()

    # 3) القيد الأول: البيع (AR / Revenue + VAT + شحن)
    revenue_account = _get_system_account("4100")
    vat_output_account = _get_system_account("2200") if invoice.tax_amount > 0 else None
    discount_account = _get_system_account("4120") if invoice.discount_amount > 0 else None
    other_income_account = _get_system_account("4900") if order.shipping_fee > 0 else None

    sale_lines: list[LedgerLineDraft] = [
        LedgerLineDraft(ar_account.id, debit=Decimal(str(order.total)),
                        memo=f"طلب أونلاين {order.doc_number}"),
        LedgerLineDraft(revenue_account.id, credit=Decimal(str(order.subtotal)),
                        memo=f"إيراد طلب {order.doc_number}"),
    ]
    if discount_account is not None:
        sale_lines.append(LedgerLineDraft(
            discount_account.id, debit=Decimal(str(order.discount_amount)),
            memo=f"خصم طلب {order.doc_number}",
        ))
    if vat_output_account is not None:
        sale_lines.append(LedgerLineDraft(
            vat_output_account.id, credit=Decimal(str(order.tax_amount)),
            memo=f"ضريبة مخرجات طلب {order.doc_number}",
        ))
    if other_income_account is not None:
        sale_lines.append(LedgerLineDraft(
            other_income_account.id, credit=Decimal(str(order.shipping_fee)),
            memo=f"رسوم شحن طلب {order.doc_number}",
        ))

    post_journal_entry(
        entry_date=order.delivered_at.date() if order.delivered_at else date.today(),
        source_type=JournalSourceType.SALES_INVOICE,
        source_id=invoice.id,
        memo=f"فاتورة بيع أونلاين {invoice.doc_number} — طلب {order.doc_number}",
        lines=sale_lines,
        user_id=user_id,
    )

    # 4) قيد التحصيل الفوري (COD = كاش يستلمه المندوب)
    if order.total > 0:
        cash_account = _get_system_account("1010")
        post_journal_entry(
            entry_date=order.delivered_at.date() if order.delivered_at else date.today(),
            source_type=JournalSourceType.CUSTOMER_RECEIPT,
            source_id=invoice.id,
            memo=f"تحصيل COD طلب {order.doc_number}",
            lines=[
                LedgerLineDraft(cash_account.id, debit=Decimal(str(order.total)),
                                memo=f"COD طلب {order.doc_number}"),
                LedgerLineDraft(ar_account.id, credit=Decimal(str(order.total)),
                                memo=f"سداد طلب {order.doc_number}"),
            ],
            user_id=user_id,
        )

    # 5) قيد COGS
    if total_cost > 0:
        cogs_account = _get_system_account("5100")
        inventory_account = _get_system_account("1100")
        post_journal_entry(
            entry_date=order.delivered_at.date() if order.delivered_at else date.today(),
            source_type=JournalSourceType.SALES_INVOICE,
            source_id=invoice.id,
            memo=f"COGS طلب {order.doc_number}",
            lines=[
                LedgerLineDraft(cogs_account.id, debit=total_cost,
                                memo=f"COGS طلب {order.doc_number}"),
                LedgerLineDraft(inventory_account.id, credit=total_cost,
                                memo=f"خصم مخزون طلب {order.doc_number}"),
            ],
            user_id=user_id,
        )

    # 6) ربط الطلب بالفاتورة
    order.sales_invoice_id = invoice.id
    db.session.flush()
    return invoice


# ============================================================
# 2) hook مرتجع بعد التسليم
# ============================================================

def create_return_from_returned_order(*, order_id: int, reason: str,
                                      user_id: int | None = None):
    """يُنشئ مرتجع بيع لطلب كان قد تم تسليمه — يعكس القيود ويعيد المخزون."""
    from app.services.sales import create_sales_return

    order = db.session.get(Order, order_id)
    if order is None:
        raise OrderAccountingError("الطلب غير موجود.")
    if order.sales_invoice_id is None:
        raise OrderAccountingError(
            "لا يمكن إرجاع طلب لم يُسلَّم بعد (لا توجد فاتورة مرتبطة)."
        )

    return create_sales_return(
        invoice_id=order.sales_invoice_id,
        return_date=date.today(),
        reason=reason or "مرتجع بعد التسليم من الطلب الأونلاين",
        lines=None,  # مرتجع كامل
        user_id=user_id,
    )
