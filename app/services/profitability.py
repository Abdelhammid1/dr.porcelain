"""تقرير ربحية المنتجات — لكل منتج خلال فترة محددة.

- الإيراد = مجموع (qty × unit_price) من أسطر SalesInvoiceLine خلال الفترة
- التكلفة = مجموع (qty × unit_cost) — cost snapshot المحفوظ عند البيع
- الربح الإجمالي = الإيراد − التكلفة
- الهامش % = الربح ÷ الإيراد × 100

نطرح المرتجعات من الإيراد والتكلفة (SalesReturnLine).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func

from app.extensions import db
from app.models.product import Product, ProductVariant
from app.models.sales import (
    InvoiceStatus,
    SalesInvoice,
    SalesInvoiceLine,
    SalesReturn,
    SalesReturnLine,
)


ZERO = Decimal("0")


@dataclass
class ProductProfitRow:
    product: Product
    qty_sold: Decimal = ZERO
    revenue: Decimal = ZERO
    cost: Decimal = ZERO

    @property
    def gross_profit(self) -> Decimal:
        return self.revenue - self.cost

    @property
    def margin_percent(self) -> Decimal:
        if self.revenue == 0:
            return ZERO
        return (self.gross_profit / self.revenue) * Decimal("100")


@dataclass
class ProfitabilityReport:
    date_from: date
    date_to: date
    rows: list[ProductProfitRow]
    total_revenue: Decimal
    total_cost: Decimal

    @property
    def total_gross_profit(self) -> Decimal:
        return self.total_revenue - self.total_cost

    @property
    def total_margin_percent(self) -> Decimal:
        if self.total_revenue == 0:
            return ZERO
        return (self.total_gross_profit / self.total_revenue) * Decimal("100")


def product_profitability(*, date_from: date, date_to: date) -> ProfitabilityReport:
    # 1) المبيعات المرحّلة (نأخذ حتى الفواتير المرتجعة كليًا لأننا سنطرح المرتجعات لاحقًا)
    sales_q = (
        db.session.query(
            ProductVariant.product_id,
            func.coalesce(func.sum(SalesInvoiceLine.qty), 0),
            func.coalesce(func.sum(SalesInvoiceLine.qty * SalesInvoiceLine.unit_price), 0),
            func.coalesce(func.sum(SalesInvoiceLine.qty * SalesInvoiceLine.unit_cost), 0),
        )
        .join(SalesInvoiceLine, SalesInvoiceLine.variant_id == ProductVariant.id)
        .join(SalesInvoice, SalesInvoice.id == SalesInvoiceLine.invoice_id)
        .filter(SalesInvoice.invoice_date >= date_from)
        .filter(SalesInvoice.invoice_date <= date_to)
        .group_by(ProductVariant.product_id)
    )
    rows_by_product: dict[int, ProductProfitRow] = {}
    for prod_id, qty, revenue, cost in sales_q.all():
        product = db.session.get(Product, prod_id)
        if product is None:
            continue
        rows_by_product[prod_id] = ProductProfitRow(
            product=product,
            qty_sold=Decimal(str(qty)),
            revenue=Decimal(str(revenue)),
            cost=Decimal(str(cost)),
        )

    # 2) نطرح المرتجعات
    returns_q = (
        db.session.query(
            ProductVariant.product_id,
            func.coalesce(func.sum(SalesReturnLine.qty), 0),
            func.coalesce(func.sum(SalesReturnLine.qty * SalesReturnLine.unit_price), 0),
            func.coalesce(func.sum(SalesReturnLine.qty * SalesReturnLine.unit_cost), 0),
        )
        .join(SalesReturnLine, SalesReturnLine.variant_id == ProductVariant.id)
        .join(SalesReturn, SalesReturn.id == SalesReturnLine.return_id)
        .filter(SalesReturn.return_date >= date_from)
        .filter(SalesReturn.return_date <= date_to)
        .group_by(ProductVariant.product_id)
    )
    for prod_id, qty, revenue, cost in returns_q.all():
        row = rows_by_product.get(prod_id)
        if row is None:
            product = db.session.get(Product, prod_id)
            if product is None:
                continue
            row = ProductProfitRow(product=product)
            rows_by_product[prod_id] = row
        row.qty_sold -= Decimal(str(qty))
        row.revenue -= Decimal(str(revenue))
        row.cost -= Decimal(str(cost))

    rows = [r for r in rows_by_product.values() if r.qty_sold != 0 or r.revenue != 0]
    rows.sort(key=lambda r: r.revenue, reverse=True)

    total_revenue = sum((r.revenue for r in rows), ZERO)
    total_cost = sum((r.cost for r in rows), ZERO)

    return ProfitabilityReport(
        date_from=date_from,
        date_to=date_to,
        rows=rows,
        total_revenue=total_revenue,
        total_cost=total_cost,
    )
