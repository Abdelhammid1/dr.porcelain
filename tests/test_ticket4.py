"""اختبارات Ticket 4 — notifications, self-cancel, PDF, password reset,
   stock alerts, guest tracking."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.models.notification import Notification
from app.models.order import OrderStatus
from app.models.party import PartyType
from app.models.password_reset import PasswordResetToken
from app.models.role import Role
from app.models.stock_alert import StockAlert
from app.models.user import User
from app.services import notifications as notif_service
from app.services import password_reset as pwd_reset
from app.services import stock_alerts as stock_alert_service
from app.services.inventory import record_purchase
from app.services.orders import OrderLineDraft, create_order, transition_status
from app.services.parties import create_party
from app.services.products import create_category, create_product
from seeds.chart_of_accounts import seed_chart_of_accounts


@pytest.fixture()
def env(app):
    seed_chart_of_accounts(_db.session)
    _db.session.commit()

    tag = uuid.uuid4().hex[:8]
    customer = create_party(type=PartyType.CUSTOMER, name_ar=f"c{tag}", phone=f"010{tag}")
    customer.email = f"cust{tag}@example.com"
    customer.set_password("secret123")
    cat = create_category(name_ar=f"c{tag}")
    product = create_product(
        name_ar=f"p{tag}", category_id=cat.id, default_price=Decimal("100"),
        variants=[{"variant_name": "افتراضي"}],
    )
    variant = product.variants[0]
    record_purchase(variant_id=variant.id, qty=20, unit_cost=Decimal("50"),
                    move_date=date(2026, 1, 1))
    _db.session.commit()
    yield {"customer": customer, "variant": variant, "product": product, "tag": tag}


# ============ Epic 2 — Notifications ============

class TestNotifications:
    def test_create_and_read(self, env):
        n = notif_service.create(title="طلب جديد", notification_type="new_order")
        _db.session.commit()
        assert n.is_read is False

        # مستخدم غير موجود — لكن الإشعار عام (user_id=None) لذا يراه أي شخص
        items = notif_service.visible_for(user_id=99999)
        assert n in items

    def test_new_order_creates_notification(self, env):
        order = create_order(
            lines=[OrderLineDraft(variant_id=env["variant"].id, qty=1)],
            guest_name=env["customer"].name_ar,
            guest_phone=env["customer"].phone,
            shipping_address="عنوان", customer_id=env["customer"].id,
        )
        _db.session.commit()

        notif = (
            _db.session.query(Notification)
            .filter_by(notification_type="new_order")
            .filter(Notification.title.like(f"%{order.doc_number}%"))
            .first()
        )
        assert notif is not None

    def test_mark_all_read(self, env):
        notif_service.create(title="A", notification_type="test")
        notif_service.create(title="B", notification_type="test")
        _db.session.commit()
        count = notif_service.mark_all_read(user_id=1)
        _db.session.commit()
        assert count >= 2
        assert notif_service.count_unread_for(user_id=1) == 0


# ============ Epic 3 — Self-cancel ============

class TestSelfCancel:
    def test_cancel_restores_stock(self, env, client):
        variant = env["variant"]
        stock_before = Decimal(str(variant.stock_qty))

        order = create_order(
            lines=[OrderLineDraft(variant_id=variant.id, qty=3)],
            guest_name=env["customer"].name_ar,
            guest_phone=env["customer"].phone,
            shipping_address="عنوان", customer_id=env["customer"].id,
        )
        _db.session.commit()
        assert variant.stock_qty == stock_before - Decimal("3")

        transition_status(order_id=order.id, new_status=OrderStatus.CANCELLED)
        _db.session.commit()
        assert variant.stock_qty == stock_before


# ============ Epic 5 — Password reset ============

class TestPasswordReset:
    def test_admin_reset_flow(self, env):
        # عمل مستخدم أدمن مع دور
        role = _db.session.query(Role).filter_by(code="accountant").first()
        if role is None:
            role = Role(code=f"r{uuid.uuid4().hex[:6]}", name_ar="tester", is_system=False)
            _db.session.add(role)
            _db.session.flush()
        u = User(username=f"u{env['tag']}", full_name="X",
                 email=f"u{env['tag']}@example.com", role_id=role.id)
        u.set_password("oldpass")
        _db.session.add(u)
        _db.session.commit()

        tok = pwd_reset.request_admin_reset(u.email)
        assert tok is not None
        assert tok.user_id == u.id
        _db.session.commit()

        # التحقق يعمل
        assert pwd_reset.validate_token(tok.token) is not None

        # الاستخدام يغيّر كلمة المرور ويُعلَّم
        pwd_reset.consume_and_set_password(tok.token, "newpass1")
        _db.session.commit()
        _db.session.refresh(u)
        assert u.check_password("newpass1")
        assert not u.check_password("oldpass")

        # لا يُعاد استخدامه
        with pytest.raises(pwd_reset.TokenError, match="سبق"):
            pwd_reset.validate_token(tok.token)

    def test_unknown_email_returns_none_silently(self, env):
        tok = pwd_reset.request_admin_reset("nonexistent@example.com")
        assert tok is None

    def test_customer_reset_by_phone(self, env):
        tok = pwd_reset.request_customer_reset(env["customer"].phone)
        assert tok is not None
        assert tok.customer_id == env["customer"].id

    def test_expired_token_rejected(self, env):
        u = env["customer"]
        tok = pwd_reset._generate(customer_id=u.id)
        tok.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        _db.session.commit()
        with pytest.raises(pwd_reset.TokenError, match="انتهت"):
            pwd_reset.validate_token(tok.token)


# ============ Epic 6 — Stock alerts ============

class TestStockAlerts:
    def test_subscribe(self, env):
        alert = stock_alert_service.subscribe(
            variant_id=env["variant"].id, email="fan@example.com",
        )
        _db.session.commit()
        assert alert.id is not None
        assert alert.notified_at is None

    def test_duplicate_subscription_returns_existing(self, env):
        a1 = stock_alert_service.subscribe(
            variant_id=env["variant"].id, email="fan@example.com",
        )
        _db.session.commit()
        a2 = stock_alert_service.subscribe(
            variant_id=env["variant"].id, email="fan@example.com",
        )
        assert a1.id == a2.id

    def test_purchase_from_zero_triggers_notification(self, env):
        variant = env["variant"]
        # نستنفد المخزون
        variant.stock_qty = Decimal("0")
        _db.session.commit()
        # اشتراك عميلين
        stock_alert_service.subscribe(variant_id=variant.id, email="a@x.com")
        stock_alert_service.subscribe(variant_id=variant.id, email="b@x.com")
        _db.session.commit()

        # نشتري 5 قطع → hook يُشغّل التنبيه
        record_purchase(variant_id=variant.id, qty=5, unit_cost=Decimal("50"),
                        move_date=date(2026, 3, 1))
        _db.session.commit()

        # كل التنبيهات المعلَّمة notified_at
        pending = (
            _db.session.query(StockAlert)
            .filter_by(variant_id=variant.id, notified_at=None)
            .count()
        )
        assert pending == 0, "كل الاشتراكات لازم تُعلَّم كمُرسَلة بعد الشراء"

    def test_alert_not_triggered_when_still_zero(self, env):
        """لو الشراء لم يرفع من صفر، لا نطلق تنبيهات."""
        variant = env["variant"]
        # المخزون > 0 (لا يستحق تنبيهًا)
        stock_alert_service.subscribe(variant_id=variant.id, email="c@x.com")
        _db.session.commit()

        record_purchase(variant_id=variant.id, qty=5, unit_cost=Decimal("50"),
                        move_date=date(2026, 3, 1))
        _db.session.commit()

        pending = (
            _db.session.query(StockAlert)
            .filter_by(variant_id=variant.id, notified_at=None)
            .count()
        )
        assert pending == 1, "الاشتراك يظل معلقًا لأن الرصيد لم يعبر من صفر"


# ============ Epic 7 — Guest order tracking ============

class TestGuestTracking:
    def test_track_form_loads(self, env, client):
        r = client.get("/shop/track-order")
        assert r.status_code == 200
        assert "تتبع طلبك".encode() in r.data

    def test_track_with_matching_phone(self, env, client):
        order = create_order(
            lines=[OrderLineDraft(variant_id=env["variant"].id, qty=1)],
            guest_name=env["customer"].name_ar,
            guest_phone=env["customer"].phone,
            shipping_address="عنوان", customer_id=env["customer"].id,
        )
        _db.session.commit()

        r = client.post("/shop/track-order", data={
            "order_number": order.doc_number,
            "phone": env["customer"].phone,
        }, follow_redirects=True)
        assert r.status_code == 200
        # نراها في صفحة النتائج
        assert order.doc_number.encode() in r.data

    def test_track_wrong_phone_rejected(self, env, client):
        order = create_order(
            lines=[OrderLineDraft(variant_id=env["variant"].id, qty=1)],
            guest_name=env["customer"].name_ar,
            guest_phone=env["customer"].phone,
            shipping_address="عنوان", customer_id=env["customer"].id,
        )
        _db.session.commit()

        r = client.post("/shop/track-order", data={
            "order_number": order.doc_number,
            "phone": "9999999",  # غلط
        })
        assert r.status_code == 200
        assert "غير مطابقة".encode() in r.data


# ============ Epic 4 — PDF invoice (basic smoke) ============

class TestInvoicePdf:
    def test_pdf_generated_from_order(self, env):
        from app.services.invoice_pdf import order_invoice_pdf
        order = create_order(
            lines=[OrderLineDraft(variant_id=env["variant"].id, qty=1)],
            guest_name=env["customer"].name_ar,
            guest_phone=env["customer"].phone,
            shipping_address="عنوان", customer_id=env["customer"].id,
        )
        _db.session.commit()
        data = order_invoice_pdf(order)
        assert isinstance(data, bytes)
        assert data[:4] == b"%PDF", "الملف يبدأ بـ %PDF header صحيح"
