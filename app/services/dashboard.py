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
class DailyPoint:
    """نقطة على مخطط الأسبوع — يوم واحد."""
    day: date
    label_ar: str
    sales: Decimal = ZERO
    profit: Decimal = ZERO  # تقريبي = المبيعات × هامش الشهر


@dataclass
class RecentActivity:
    """سطر في سجل العمليات الفورية."""
    kind: str              # 'invoice' | 'installment' | 'stock' | 'pos_close'
    icon: str              # اسم Material Symbols
    title: str             # عنوان قصير
    subtitle: str          # سطر ثانوي
    amount: Decimal | None = None
    amount_note: str | None = None
    when: datetime | None = None
    tone: str = 'secondary'  # 'secondary' | 'tertiary' | 'error'


@dataclass
class RecentTransaction:
    """سطر في جدول أحدث المعاملات."""
    invoice: SalesInvoice
    channel_icon: str
    channel_label: str
    payment_icon: str
    payment_label: str
    payment_tone: str = 'secondary'


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
    weekly_chart: list[DailyPoint] = field(default_factory=list)
    recent_activity: list[RecentActivity] = field(default_factory=list)
    recent_transactions: list[RecentTransaction] = field(default_factory=list)
    cash_on_hand: Decimal = ZERO
    yesterday_total: Decimal = ZERO
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

    # مبيعات أمس (للمقارنة مع اليوم في KPI الأول)
    yesterday = today - timedelta(days=1)
    d.yesterday_total = _sales_bucket(yesterday, yesterday).total

    # مخطط الأسبوع — آخر 7 أيام (بما فيها اليوم)
    _weekday_ar = ['الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت', 'الأحد']
    _margin = (d.net_profit_month / d.month.total) if d.month.total > ZERO else Decimal('0.28')
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        day_bucket = _sales_bucket(day, day)
        d.weekly_chart.append(DailyPoint(
            day=day,
            label_ar=_weekday_ar[day.weekday()] + (' (اليوم)' if i == 0 else ''),
            sales=day_bucket.total,
            profit=(day_bucket.total * _margin).quantize(Decimal('0.001')),
        ))

    # سجل العمليات الفورية — آخر فواتير + تحصيلات أقساط + آخر وردية POS مقفلة
    from app.models.installment import InstallmentCollection
    from app.models.pos import POSSession, SessionStatus

    recent_invoices = (
        db.session.query(SalesInvoice)
        .order_by(SalesInvoice.created_at.desc())
        .limit(2).all()
    )
    for inv in recent_invoices:
        cust_name = inv.customer.name_ar if inv.customer else 'عميل نقدي'
        d.recent_activity.append(RecentActivity(
            kind='invoice', icon='receipt', tone='secondary',
            title=f"فاتورة #{inv.doc_number}",
            subtitle=f"العميل: {cust_name}",
            amount=Decimal(str(inv.total)),
            when=inv.created_at,
        ))

    recent_pmts = (
        db.session.query(InstallmentCollection)
        .order_by(InstallmentCollection.created_at.desc())
        .limit(1).all()
    )
    for p in recent_pmts:
        plan_num = p.plan.doc_number if p.plan else '—'
        d.recent_activity.append(RecentActivity(
            kind='installment', icon='account_balance_wallet', tone='tertiary',
            title=f"سداد قسط — خطة #{plan_num}",
            subtitle=f"طريقة: {'نقدي' if p.method == 'cash' else 'بنكي'}",
            amount=Decimal(str(p.amount)),
            when=p.created_at,
        ))

    recent_closed_shift = (
        db.session.query(POSSession)
        .filter(POSSession.status == SessionStatus.CLOSED)
        .order_by(POSSession.closed_at.desc().nullslast())
        .limit(1).first()
    )
    if recent_closed_shift and recent_closed_shift.closed_at:
        d.recent_activity.append(RecentActivity(
            kind='pos_close', icon='point_of_sale', tone='secondary',
            title=f"إغلاق وردية POS #{recent_closed_shift.id}",
            subtitle="تم ترحيل النقدية ومطابقة العهدة",
            amount=Decimal(str(recent_closed_shift.closing_cash_expected or 0)),
            when=recent_closed_shift.closed_at,
        ))

    # أحدث المعاملات — آخر 5 فواتير بيع
    latest_5 = (
        db.session.query(SalesInvoice)
        .order_by(SalesInvoice.created_at.desc())
        .limit(5).all()
    )
    for inv in latest_5:
        if inv.pos_session_id:
            ch_icon, ch_label = 'storefront', 'نقطة البيع'
        else:
            ch_icon, ch_label = 'store', 'المعرض الرئيسي'
        pm = getattr(inv.payment_method, 'value', inv.payment_method) or 'cash'
        pm = str(pm).lower()
        if pm == 'cash':
            pay_icon, pay_label, pay_tone = 'payments', 'نقداً', 'secondary'
        elif pm in ('bank', 'transfer', 'card'):
            pay_icon, pay_label, pay_tone = 'credit_card', 'تحويل بنكي', 'secondary'
        elif pm in ('credit', 'on_credit'):
            pay_icon, pay_label, pay_tone = 'schedule', 'آجل', 'tertiary'
        else:
            pay_icon, pay_label, pay_tone = 'payments', pm, 'secondary'
        d.recent_transactions.append(RecentTransaction(
            invoice=inv,
            channel_icon=ch_icon, channel_label=ch_label,
            payment_icon=pay_icon, payment_label=pay_label, payment_tone=pay_tone,
        ))

    # النقدية والبنوك — مجموع أرصدة الخزينة والبنوك والعهد (1010, 1020, 1030)
    from app.models.account import Account
    from app.models.journal import JournalLine, JournalEntry, JournalEntryStatus
    _cash_codes = ('1010', '1020', '1030')
    cash_accounts = db.session.query(Account).filter(Account.code.in_(_cash_codes)).all()
    total_cash = ZERO
    for acc in cash_accounts:
        dr = (
            db.session.query(func.coalesce(func.sum(JournalLine.debit), 0))
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .filter(JournalLine.account_id == acc.id)
            .filter(JournalEntry.status == JournalEntryStatus.POSTED)
            .scalar() or 0
        )
        cr = (
            db.session.query(func.coalesce(func.sum(JournalLine.credit), 0))
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .filter(JournalLine.account_id == acc.id)
            .filter(JournalEntry.status == JournalEntryStatus.POSTED)
            .scalar() or 0
        )
        total_cash += Decimal(str(dr)) - Decimal(str(cr))
    d.cash_on_hand = total_cash

    return d
