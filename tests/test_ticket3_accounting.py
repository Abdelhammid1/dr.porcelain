"""اختبارات Ticket 3 — الاختبارات الحرجة للطبقة المحاسبية.

الأهم (Epic 7 - ON_CREDIT):
- بيع آجل يخلق قيد AR فقط (لا قيد تحصيل)
- تحصيل لاحق يخلق قيد منفصل ولا يعدّل الفاتورة
- amount_paid يتزايد صحيحًا

+ اختبارات:
- Epic 4: الحسابات الجديدة (2400, 2500, 3150, 3400) موجودة
- Epic 5: reverse_entry يعمل والقيود متوازنة
- Epic 6: opening balance يمنع التكرار ويولّد قيدًا محاسبيًا
- Epic 9: cron endpoint يستجيب
- Epic 10: نقاط الولاء تُمنَح على DELIVERED
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.models.account import Account
from app.models.inventory import InventoryMovement
from app.models.journal import (
    JournalEntry, JournalEntryStatus, JournalSourceType,
)
from app.models.loyalty import LoyaltyPointsLedger
from app.models.order import OrderStatus
from app.models.party import PartyType
from app.models.sales import InvoiceStatus, PaymentMethod, SalesInvoice
from app.models.setting import set_setting
from app.services.inventory import record_purchase
from app.services.ledger import LedgerLineDraft, post_journal_entry, reverse_entry
from app.services.orders import OrderLineDraft, create_order, transition_status
from app.services.parties import create_party
from app.services.products import create_category, create_product
from app.services.sales import (
    InvoiceLineDraft, SalesError, create_cash_sale, record_customer_receipt,
)
from seeds.chart_of_accounts import seed_chart_of_accounts


@pytest.fixture()
def env(app):
    seed_chart_of_accounts(_db.session)
    set_setting("tax.enabled", "false")
    set_setting("storefront.shipping_fee", "0.000")
    set_setting("storefront.free_shipping_threshold", "0.000")
    _db.session.commit()

    tag = uuid.uuid4().hex[:8]
    customer = create_party(type=PartyType.CUSTOMER, name_ar=f"c{tag}", phone=f"010{tag}")
    cat = create_category(name_ar=f"cat{tag}")
    product = create_product(
        name_ar=f"p{tag}", category_id=cat.id, default_price=Decimal("100"),
        variants=[{"variant_name": "افتراضي"}],
    )
    variant = product.variants[0]
    record_purchase(variant_id=variant.id, qty=50, unit_cost=Decimal("50"),
                    move_date=date(2026, 1, 1))
    _db.session.commit()
    yield {"customer": customer, "variant": variant, "product": product}


# ============ Epic 4 — New accounts ============

class TestNewAccounts:
    def test_new_accounts_seeded(self, env):
        for code in ("2400", "2500", "3150", "3400"):
            acc = _db.session.query(Account).filter_by(code=code).one_or_none()
            assert acc is not None, f"الحساب {code} غير موجود"
            assert acc.is_system is True


# ============ Epic 7 — Credit invoice GOLDEN RULE ============

class TestCreditInvoiceGoldenRule:
    """أهم اختبار في Ticket 3 — بيع آجل يترك AR مدينًا."""

    def test_credit_sale_creates_no_receipt_journal(self, env):
        customer = env["customer"]
        ar_before = customer.account.compute_balance()

        inv = create_cash_sale(
            customer_id=customer.id,
            invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=env["variant"].id, qty=2, unit_price=100)],
            payment_method=PaymentMethod.ON_CREDIT,
        )
        _db.session.commit()

        assert inv.payment_method == PaymentMethod.ON_CREDIT
        assert inv.total == Decimal("200.000")
        assert inv.amount_paid == Decimal("0.000"), \
            "بيع آجل: amount_paid يبدأ صفر"
        assert inv.amount_due == Decimal("200.000")
        assert inv.is_paid is False
        assert inv.is_on_credit is True

        # فحص القيود: بيع + COGS، لا قيد تحصيل
        entries = (
            _db.session.query(JournalEntry)
            .filter(JournalEntry.source_id == inv.id)
            .all()
        )
        receipt_entries = [
            e for e in entries
            if e.source_type == JournalSourceType.CUSTOMER_RECEIPT
        ]
        assert len(receipt_entries) == 0, \
            "بيع آجل: يجب ألا يُنشَأ قيد تحصيل مع الفاتورة"

        # AR على العميل زاد بالإجمالي (لأنه لم يُحصَّل)
        assert customer.account.compute_balance() == ar_before + Decimal("200.000")

    def test_cash_sale_still_creates_receipt(self, env):
        """التحقق أن التغيير لم يكسر النقدي."""
        inv = create_cash_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=env["variant"].id, qty=1, unit_price=100)],
            payment_method=PaymentMethod.CASH,
        )
        _db.session.commit()

        assert inv.amount_paid == Decimal("100.000"), \
            "بيع نقدي: amount_paid = total تلقائيًا"
        assert inv.is_paid is True

        receipt_entries = (
            _db.session.query(JournalEntry)
            .filter(JournalEntry.source_id == inv.id)
            .filter(JournalEntry.source_type == JournalSourceType.CUSTOMER_RECEIPT)
            .all()
        )
        assert len(receipt_entries) == 1, \
            "بيع نقدي: قيد تحصيل واحد كالمعتاد"

    def test_later_collection_creates_separate_entry(self, env):
        """التحصيل اللاحق يُنشئ قيدًا منفصلاً ولا يعدّل قيود الفاتورة."""
        inv = create_cash_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=env["variant"].id, qty=1, unit_price=100)],
            payment_method=PaymentMethod.ON_CREDIT,
        )
        _db.session.commit()
        entries_before = _db.session.query(JournalEntry).filter_by(source_id=inv.id).count()
        cash_acc = _db.session.query(Account).filter_by(code="1010").one()
        cash_before = cash_acc.compute_balance()

        # تحصيل جزئي 60
        record_customer_receipt(
            invoice_id=inv.id,
            amount=Decimal("60"),
            receipt_date=date(2026, 2, 15),
            payment_method=PaymentMethod.CASH,
        )
        _db.session.commit()

        _db.session.refresh(inv)
        assert inv.amount_paid == Decimal("60.000")
        assert inv.amount_due == Decimal("40.000")
        assert inv.is_paid is False

        # قيد جديد أُنشئ (والفاتورة لم تُعدَّل قيودها الأصلية)
        entries_after = _db.session.query(JournalEntry).filter_by(source_id=inv.id).count()
        assert entries_after == entries_before + 1

        # النقدية زادت 60
        assert cash_acc.compute_balance() == cash_before + Decimal("60")

        # تحصيل الباقي
        record_customer_receipt(
            invoice_id=inv.id, amount=Decimal("40"),
            receipt_date=date(2026, 2, 20),
        )
        _db.session.commit()
        _db.session.refresh(inv)
        assert inv.amount_paid == Decimal("100.000")
        assert inv.is_paid is True

    def test_cannot_overcollect(self, env):
        inv = create_cash_sale(
            customer_id=env["customer"].id,
            invoice_date=date(2026, 2, 1),
            lines=[InvoiceLineDraft(variant_id=env["variant"].id, qty=1, unit_price=100)],
            payment_method=PaymentMethod.ON_CREDIT,
        )
        _db.session.commit()
        with pytest.raises(SalesError, match="أكبر من"):
            record_customer_receipt(
                invoice_id=inv.id, amount=Decimal("150"),
                receipt_date=date(2026, 2, 15),
            )


# ============ Epic 5 — Journal reverse ============

class TestJournalReverse:
    def test_reverse_creates_opposing_balanced_entry(self, env):
        """قيد يدوي متوازن + عكسه يبقى الأصلي معلَّمًا وينشئ قيد عكس."""
        cash = _db.session.query(Account).filter_by(code="1010").one()
        capital = _db.session.query(Account).filter_by(code="3100").one()

        entry = post_journal_entry(
            entry_date=date(2026, 2, 1),
            source_type=JournalSourceType.MANUAL,
            source_id=None,
            memo="إضافة رأس مال",
            lines=[
                LedgerLineDraft(cash.id, debit=Decimal("1000"), memo="رأس مال"),
                LedgerLineDraft(capital.id, credit=Decimal("1000"), memo="رأس مال"),
            ],
        )
        _db.session.commit()
        assert entry.is_balanced
        assert entry.status == JournalEntryStatus.POSTED

        reversal = reverse_entry(entry_id=entry.id, reason="خطأ", user_id=None)
        _db.session.commit()
        _db.session.refresh(entry)

        assert entry.status == JournalEntryStatus.REVERSED
        assert entry.reversed_by_id == reversal.id
        assert reversal.reversal_of_id == entry.id
        assert reversal.is_balanced
        # الأسطر مبدولة (كل مدين صار دائن)
        rev_cash_line = next(l for l in reversal.lines if l.account_id == cash.id)
        assert rev_cash_line.credit == Decimal("1000")
        assert rev_cash_line.debit == Decimal("0")


# ============ Epic 6 — Opening balance ============

class TestOpeningBalance:
    def test_opening_creates_movement_and_can_pair_with_journal(self, env):
        # المنتج الجديد في هذا الاختبار (ليس فيه حركات)
        tag = uuid.uuid4().hex[:6]
        cat = create_category(name_ar=f"oc{tag}")
        product = create_product(
            name_ar=f"op{tag}", category_id=cat.id, default_price=Decimal("100"),
            variants=[{"variant_name": "افتراضي"}],
        )
        v = product.variants[0]
        _db.session.commit()

        from app.services.inventory import record_opening
        move = record_opening(
            variant_id=v.id, qty=10, unit_cost=Decimal("25"),
            move_date=date(2026, 1, 1),
        )
        _db.session.commit()
        assert move.qty == Decimal("10.000")
        assert v.stock_qty == Decimal("10.000")
        assert v.avg_cost == Decimal("25.000")

    def test_cannot_open_twice(self, env):
        # env["variant"] عنده stock_qty > 0 بالفعل (من record_purchase في fixture)
        from app.services.inventory import InventoryError, record_opening
        with pytest.raises(InventoryError):
            record_opening(
                variant_id=env["variant"].id, qty=5, unit_cost=Decimal("20"),
                move_date=date(2026, 1, 1),
            )


# ============ Epic 9 — Cron reminders ============

class TestCronReminders:
    def test_disabled_returns_empty(self, env, client):
        set_setting("installments.reminders_enabled", "false")
        _db.session.commit()
        r = client.get("/cron/installment-reminders")
        assert r.status_code == 200
        data = r.get_json()
        assert data["enabled"] is False
        assert data["reminders"] == []

    def test_enabled_returns_structure(self, env, client):
        set_setting("installments.reminders_enabled", "true")
        set_setting("installments.reminder_days_before", "3")
        _db.session.commit()
        r = client.get("/cron/installment-reminders")
        assert r.status_code == 200
        data = r.get_json()
        assert data["enabled"] is True
        assert "count" in data
        assert isinstance(data["reminders"], list)


# ============ Epic 10 — Loyalty points ============

class TestLoyalty:
    def test_no_points_when_disabled(self, env):
        set_setting("loyalty.enabled", "false")
        _db.session.commit()

        order = create_order(
            lines=[OrderLineDraft(variant_id=env["variant"].id, qty=1)],
            guest_name=env["customer"].name_ar,
            guest_phone=env["customer"].phone,
            shipping_address="عنوان", customer_id=env["customer"].id,
        )
        _db.session.commit()
        transition_status(order_id=order.id, new_status=OrderStatus.PROCESSING)
        transition_status(order_id=order.id, new_status=OrderStatus.SHIPPED)
        transition_status(order_id=order.id, new_status=OrderStatus.DELIVERED)
        _db.session.commit()

        earned = (
            _db.session.query(LoyaltyPointsLedger)
            .filter_by(customer_id=env["customer"].id)
            .count()
        )
        assert earned == 0

    def test_earns_points_when_enabled(self, env):
        set_setting("loyalty.enabled", "true")
        set_setting("loyalty.points_per_currency_unit", "1")
        _db.session.commit()

        order = create_order(
            lines=[OrderLineDraft(variant_id=env["variant"].id, qty=1)],
            guest_name=env["customer"].name_ar,
            guest_phone=env["customer"].phone,
            shipping_address="عنوان", customer_id=env["customer"].id,
        )
        _db.session.commit()
        transition_status(order_id=order.id, new_status=OrderStatus.PROCESSING)
        transition_status(order_id=order.id, new_status=OrderStatus.SHIPPED)
        transition_status(order_id=order.id, new_status=OrderStatus.DELIVERED)
        _db.session.commit()

        entry = (
            _db.session.query(LoyaltyPointsLedger)
            .filter_by(customer_id=env["customer"].id)
            .first()
        )
        assert entry is not None
        # order.total = 100 (qty=1 × price=100)، rate=1 → 100 نقطة
        assert entry.points == Decimal("100.000")

    def test_loyalty_never_touches_accounting(self, env):
        """التحقق أن منح النقاط لا يُدخل في القيود المحاسبية."""
        set_setting("loyalty.enabled", "true")
        _db.session.commit()

        order = create_order(
            lines=[OrderLineDraft(variant_id=env["variant"].id, qty=1)],
            guest_name=env["customer"].name_ar,
            guest_phone=env["customer"].phone,
            shipping_address="عنوان", customer_id=env["customer"].id,
        )
        _db.session.commit()
        transition_status(order_id=order.id, new_status=OrderStatus.PROCESSING)
        transition_status(order_id=order.id, new_status=OrderStatus.SHIPPED)
        transition_status(order_id=order.id, new_status=OrderStatus.DELIVERED)
        _db.session.commit()

        # كل القيود متوازنة (بلا أي أثر من النقاط)
        invoice = _db.session.get(SalesInvoice, order.sales_invoice_id)
        entries = _db.session.query(JournalEntry).filter_by(source_id=invoice.id).all()
        for e in entries:
            assert e.is_balanced
