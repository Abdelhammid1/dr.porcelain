"""خدمة لوحة المعلومات — تجميع أرقام اليوم/الأسبوع/الشهر لعرض الصفحة الرئيسية."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func

from app.extensions import db
from app.models.installment import InstallmentLineStatus, InstallmentScheduleLine
from app.models.party import Party, PartyType
from app.models.product import Product, ProductVariant
from app.models.sales import InvoiceStatus, SalesInvoice, SalesInvoiceLine


ZERO = Decimal("0")


@dataclass
class SalesBucket:
    total: Decimal = ZERO
    invoices_count: int = 0
    online_total: Decimal = ZERO      # طلبات المتجر الإلكتروني (Phase 8 — الآن = 0)
    pos_total: Decimal = ZERO         # مبيعات POS


@dataclass
class TopProduct:
    product: Product
    qty_sold: Decimal
    revenue: Decimal


@dataclass
class DashboardData:
    today: SalesBucket = field(default_factory=SalesBucket)
    week: SalesBucket = field(default_factory=SalesBucket)
    month: SalesBucket = field(default_factory=SalesBucket)
    net_profit_month: Decimal = ZERO
    total_customers: int = 0
    total_products: int = 0
    total_active_variants: int = 0
    low_stock_variants: list[ProductVariant] = field(default_factory=list)
    top_products_month: list[TopProduct] = field(default_factory=list)
    due_today_installments: int = 0
    overdue_installments: int = 0
    generated_at: datetime = field(default_factory=datetime.now)


def _sales_bucket(from_date: date, to_date: date) -> SalesBucket:
    q = (
        db.session.query(SalesInvoice)
        .filter(SalesInvoice.invoice_date >= from_date)
        .filter(SalesInvoice.invoice_date <= to_date)
        .filter(SalesInvoice.status != InvoiceStatus.RETURNED)
        .all()
    )
    b = SalesBucket()
    for inv in q:
        amt = Decimal(str(inv.total))
        b.total += amt
        b.invoices_count += 1
        if inv.pos_session_id is not None:
            b.pos_total += amt
        # online (Phase 8) — لاحقًا نُميّز عبر source_channel
    return b


def get_dashboard_data(*, today: date | None = None) -> DashboardData:
    from app.services.financial_statements import income_statement
    from app.services.inventory import low_stock_variants

    today = today or date.today()
    week_start = today - timedelta(days=today.weekday())  # الاثنين
    month_start = today.replace(day=1)

    d = DashboardData()
    d.today = _sales_bucket(today, today)
    d.week = _sales_bucket(week_start, today)
    d.month = _sales_bucket(month_start, today)

    # صافي ربح الشهر التقريبي
    is_stmt = income_statement(date_from=month_start, date_to=today)
    d.net_profit_month = is_stmt.net_profit

    # عدّاد كيانات
    d.total_customers = (
        db.session.query(func.count(Party.id))
        .filter(Party.type == PartyType.CUSTOMER, Party.is_active == True)  # noqa: E712
        .scalar() or 0
    )
    d.total_products = db.session.query(func.count(Product.id)).filter(
        Product.is_active == True  # noqa: E712
    ).scalar() or 0
    d.total_active_variants = db.session.query(func.count(ProductVariant.id)).filter(
        ProductVariant.is_active == True  # noqa: E712
    ).scalar() or 0

    d.low_stock_variants = low_stock_variants(limit=10)

    # أعلى منتجات مبيعًا في الشهر
    top_q = (
        db.session.query(
            ProductVariant.product_id,
            func.coalesce(func.sum(SalesInvoiceLine.qty), 0),
            func.coalesce(func.sum(SalesInvoiceLine.qty * SalesInvoiceLine.unit_price), 0),
        )
        .join(SalesInvoiceLine, SalesInvoiceLine.variant_id == ProductVariant.id)
        .join(SalesInvoice, SalesInvoice.id == SalesInvoiceLine.invoice_id)
        .filter(SalesInvoice.invoice_date >= month_start)
        .filter(SalesInvoice.invoice_date <= today)
        .filter(SalesInvoice.status != InvoiceStatus.RETURNED)
        .group_by(ProductVariant.product_id)
        .order_by(func.sum(SalesInvoiceLine.qty * SalesInvoiceLine.unit_price).desc())
        .limit(5)
    )
    for prod_id, qty, revenue in top_q.all():
        product = db.session.get(Product, prod_id)
        if product is None:
            continue
        d.top_products_month.append(TopProduct(
            product=product,
            qty_sold=Decimal(str(qty)),
            revenue=Decimal(str(revenue)),
        ))

    # الأقساط
    d.due_today_installments = (
        db.session.query(func.count(InstallmentScheduleLine.id))
        .filter(InstallmentScheduleLine.due_date == today)
        .filter(InstallmentScheduleLine.status.in_([
            InstallmentLineStatus.PENDING, InstallmentLineStatus.PARTIAL,
        ]))
        .scalar() or 0
    )
    d.overdue_installments = (
        db.session.query(func.count(InstallmentScheduleLine.id))
        .filter(InstallmentScheduleLine.status == InstallmentLineStatus.OVERDUE)
        .scalar() or 0
    )

    return d
