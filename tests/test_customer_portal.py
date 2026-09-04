"""اختبارات بوابة العميل (Customer Portal)."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.models.party import Party, PartyType
from app.services.customer_auth import (
    CustomerAuthError,
    login_customer,
    register_customer,
)
from app.services.parties import create_party
from seeds.chart_of_accounts import seed_chart_of_accounts


@pytest.fixture()
def env(app):
    seed_chart_of_accounts(_db.session)
    _db.session.commit()
    yield


# ---------- Registration ----------

class TestRegisterCustomer:
    def test_new_customer_creates_party_and_account(self, env):
        tag = uuid.uuid4().hex[:8]
        p = register_customer(
            name_ar=f"عميل {tag}",
            phone=f"010{tag[:8]}",
            password="Secret123",
        )
        _db.session.commit()

        assert p.type == PartyType.CUSTOMER
        assert p.code.startswith("C-")
        assert p.has_portal_account
        assert p.check_password("Secret123")
        # حساب فرعي تحت 1200
        assert p.account is not None
        assert p.account.parent.code == "1200"

    def test_short_password_rejected(self, env):
        with pytest.raises(CustomerAuthError, match="6 أحرف"):
            register_customer(name_ar="ع", phone="01111111111", password="abc")

    def test_existing_party_gets_password_activated(self, env):
        """إذا كان Party موجود من قبل (طلب ضيف سابق)، نُفعّل حسابه لا نُنشئ جديد."""
        tag = uuid.uuid4().hex[:8]
        phone = f"012{tag[:8]}"
        existing = create_party(type=PartyType.CUSTOMER, name_ar="ضيف قديم", phone=phone)
        _db.session.commit()
        assert not existing.has_portal_account

        p = register_customer(name_ar="ضيف قديم", phone=phone, password="NewPassword")
        _db.session.commit()

        assert p.id == existing.id  # نفس السجل
        assert p.has_portal_account
        assert p.check_password("NewPassword")

    def test_cannot_reregister_active_account(self, env):
        tag = uuid.uuid4().hex[:8]
        phone = f"015{tag[:8]}"
        register_customer(name_ar="ك", phone=phone, password="pass123")
        _db.session.commit()
        with pytest.raises(CustomerAuthError, match="سجّل الدخول"):
            register_customer(name_ar="ك", phone=phone, password="another")


# ---------- Login ----------

class TestLoginCustomer:
    def test_correct_credentials_succeed(self, env, app):
        with app.test_request_context():
            tag = uuid.uuid4().hex[:8]
            register_customer(name_ar="ك", phone=f"019{tag[:8]}", password="Secret1234")
            _db.session.commit()

            p = login_customer(phone=f"019{tag[:8]}", password="Secret1234")
            _db.session.commit()
            assert p.type == PartyType.CUSTOMER

    def test_wrong_password_rejected(self, env, app):
        with app.test_request_context():
            tag = uuid.uuid4().hex[:8]
            register_customer(name_ar="ك", phone=f"019{tag[:8]}", password="Right123")
            _db.session.commit()

            with pytest.raises(CustomerAuthError, match="غير صحيحة"):
                login_customer(phone=f"019{tag[:8]}", password="wrong")

    def test_unknown_phone_rejected(self, env, app):
        with app.test_request_context():
            with pytest.raises(CustomerAuthError, match="غير صحيحة"):
                login_customer(phone="0100000000", password="whatever")


# ---------- End-to-end via test client ----------

class TestPortalRoutes:
    def _register_and_login(self, client, phone, password="Password123", name="عميل"):
        client.post("/shop/account/register", data={
            "name": name, "phone": phone, "password": password,
        })

    def test_register_then_access_dashboard(self, env, app, client):
        _db.session.commit()
        tag = uuid.uuid4().hex[:8]
        self._register_and_login(client, f"020{tag[:8]}")
        # بعد التسجيل هيكون مسجّل دخول تلقائيًا
        r = client.get("/shop/account/")
        assert r.status_code == 200

    def test_unauthenticated_blocked(self, env, client):
        r = client.get("/shop/account/", follow_redirects=False)
        assert r.status_code == 302
        assert "/shop/account/login" in r.headers.get("Location", "")

    def test_orders_shown_only_for_own_customer(self, env, app, client):
        """العميل يشوف طلباته فقط."""
        from app.services.orders import create_order, OrderLineDraft
        from app.services.products import create_category, create_product
        from app.services.inventory import record_purchase

        tag = uuid.uuid4().hex[:8]
        cat = create_category(name_ar=f"c{tag}")
        p = create_product(name_ar=f"p{tag}", category_id=cat.id,
                           default_price=Decimal("50"),
                           variants=[{"variant_name": "افتراضي"}])
        record_purchase(variant_id=p.variants[0].id, qty=10, unit_cost=20,
                        move_date=date(2026, 1, 1))
        _db.session.commit()

        # عميلان
        c1 = register_customer(name_ar="A", phone=f"099{tag[:8]}", password="pass123")
        c2 = register_customer(name_ar="B", phone=f"088{tag[:8]}", password="pass123")
        _db.session.commit()

        # طلب لكل واحد
        create_order(lines=[OrderLineDraft(p.variants[0].id, qty=1)],
                     guest_name="A", guest_phone=c1.phone,
                     shipping_address="عنوان",
                     customer_id=c1.id)
        o2 = create_order(lines=[OrderLineDraft(p.variants[0].id, qty=1)],
                          guest_name="B", guest_phone=c2.phone,
                          shipping_address="عنوان",
                          customer_id=c2.id)
        _db.session.commit()

        # سجّل دخول c1 وحاول رؤية طلب c2 → 403
        client.post("/shop/account/login", data={
            "phone": c1.phone, "password": "pass123",
        })
        r = client.get(f"/shop/account/order/{o2.doc_number}")
        assert r.status_code == 403
