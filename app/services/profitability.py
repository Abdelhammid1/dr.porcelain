"""تقرير ربحية المنتجات — لكل منتج خلال فترة محددة.

الإيراد لكل منتج = (qty × unit_price) بعد توزيع خصم الفاتورة على السطر
نسبيًا (خصم الكاشير في POS، أو خصم كوبون على مستوى الفاتورة).

مثال: فاتورة إجماليها الخام 32,500 وعليها خصم 1,000. النسبة الفعّالة =
31,500 / 32,500 ≈ 0.9692. أي سطر إيراده = qty × unit_price × 0.9692.
كده الإيراد في تقرير الربحية يوافق قائمة الدخل (بعد الخصم) بدل ما يبقى
جامد على السعر قبل الخصم — ده كان بق يخلي أرقام الربحية غلط.

- التكلفة = qty × unit_cost (snapshot وقت البيع — الخصم مش بيمس التكلفة)
- الربح الإجمالي = الإيراد − التكلفة
- الهامش % = الربح ÷ الإيراد × 100

المرتجعات: تُطرَح من الإيراد والتكلفة بنفس آلية التوزيع النسبي (نستخدم
نفس نسبة فاتورة المرتجع الأصلية عشان الأرقام تفضل متزنة).
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


def _invoice_effective_ratios(invoice_ids: list[int]) -> dict[int, Decimal]:
    """يحسب لكل فاتورة نسبة الإيراد الصافي إلى الخام:
        ratio = (Σ(qty×unit_price) - discount_amount) / Σ(qty×unit_price)
    لو الفاتورة إجماليها الخام صفر أو الخصم = 0 → النسبة = 1.
    """
    if not invoice_ids:
        return {}

    # مجموع الخام لكل فاتورة
    gross_by_inv = dict(
        db.session.query(
            SalesInvoiceLine.invoice_id,
            func.coalesce(func.sum(SalesInvoiceLine.qty * SalesInvoiceLine.unit_price), 0),
        )
        .filter(SalesInvoiceLine.invoice_id.in_(invoice_ids))
        .group_by(SalesInvoiceLine.invoice_id)
        .all()
    )

    ratios: dict[int, Decimal] = {}
    for inv_id, gross in gross_by_inv.items():
        gross_d = Decimal(str(gross))
        inv = db.session.get(SalesInvoice, inv_id)
        if inv is None or gross_d <= ZERO:
            ratios[inv_id] = Decimal("1")
            continue
        discount = Decimal(str(inv.discount_amount or 0))
        if discount <= ZERO:
            ratios[inv_id] = Decimal("1")
        else:
            net = gross_d - discount
            if net < ZERO:
                net = ZERO  # حماية من الخصم اللي يتخطى الخام (لا يفترض يحصل)
            ratios[inv_id] = (net / gross_d)
    return ratios


def product_profitability(*, date_from: date, date_to: date) -> ProfitabilityReport:
    # 1) نجيب معرّفات الفواتير في الفترة
    invoice_ids = [
        row[0]
        for row in (
            db.session.query(SalesInvoice.id)
            .filter(SalesInvoice.invoice_date >= date_from)
            .filter(SalesInvoice.invoice_date <= date_to)
            .all()
        )
    ]

    # 2) نسبة الإيراد الصافي إلى الخام لكل فاتورة (لتوزيع خصم الفاتورة على السطر)
    inv_ratios = _invoice_effective_ratios(invoice_ids)

    rows_by_product: dict[int, ProductProfitRow] = {}

    # 3) لكل سطر بيع: قم بحساب الإيراد الفعّال = qty × unit_price × ratio
    if invoice_ids:
        sales_lines = (
            db.session.query(
                ProductVariant.product_id,
                SalesInvoiceLine.invoice_id,
                SalesInvoiceLine.qty,
                SalesInvoiceLine.unit_price,
                SalesInvoiceLine.unit_cost,
            )
            .join(SalesInvoiceLine, SalesInvoiceLine.variant_id == ProductVariant.id)
            .filter(SalesInvoiceLine.invoice_id.in_(invoice_ids))
            .all()
        )
        for prod_id, inv_id, qty, unit_price, unit_cost in sales_lines:
            qty_d = Decimal(str(qty))
            up = Decimal(str(unit_price))
            uc = Decimal(str(unit_cost))
            ratio = inv_ratios.get(inv_id, Decimal("1"))
            line_revenue = qty_d * up * ratio
            line_cost = qty_d * uc
            row = rows_by_product.get(prod_id)
            if row is None:
                product = db.session.get(Product, prod_id)
                if product is None:
                    continue
                row = ProductProfitRow(product=product)
                rows_by_product[prod_id] = row
            row.qty_sold += qty_d
            row.revenue += line_revenue
            row.cost += line_cost

    # 4) نطرح المرتجعات بنفس آلية التوزيع النسبي — بنستخدم نسبة فاتورة المرتجع الأصلي
    ret_lines = (
        db.session.query(
            ProductVariant.product_id,
            SalesReturnLine.qty,
            SalesReturnLine.unit_price,
            SalesReturnLine.unit_cost,
            SalesReturn.invoice_id,
        )
        .join(SalesReturnLine, SalesReturnLine.variant_id == ProductVariant.id)
        .join(SalesReturn, SalesReturn.id == SalesReturnLine.return_id)
        .filter(SalesReturn.return_date >= date_from)
        .filter(SalesReturn.return_date <= date_to)
        .all()
    )

    # نسب فواتير المرتجعات لو مش موجودة في dictionary
    extra_inv_ids = list({inv_id for *_ , inv_id in ret_lines if inv_id not in inv_ratios})
    if extra_inv_ids:
        inv_ratios.update(_invoice_effective_ratios(extra_inv_ids))

    for prod_id, qty, unit_price, unit_cost, inv_id in ret_lines:
        qty_d = Decimal(str(qty))
        up = Decimal(str(unit_price))
        uc = Decimal(str(unit_cost))
        ratio = inv_ratios.get(inv_id, Decimal("1"))
        line_revenue = qty_d * up * ratio
        line_cost = qty_d * uc
        row = rows_by_product.get(prod_id)
        if row is None:
            product = db.session.get(Product, prod_id)
            if product is None:
                continue
            row = ProductProfitRow(product=product)
            rows_by_product[prod_id] = row
        row.qty_sold -= qty_d
        row.revenue -= line_revenue
        row.cost -= line_cost

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
