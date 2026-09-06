"""خدمة إرسال الإيميلات (Ticket 4 Epic 1).

**قاعدة أساسية:** فشل الإرسال (شبكة، مفتاح غلط، تعطيل الخدمة) لا يجب أن يُفشل
الطلب أو أي عملية أعمال. كل استدعاء يجب أن يُلَف try/except من جانب المستدعي.

في المرحلة الأولى نقدّم backend بسيط قابل للتبديل:
- لو email.enabled=False → لا نرسل شيئًا (نُسجّل log فقط).
- لو enabled بلا api_key → نُسجّل تحذيرًا ونتوقف بهدوء.
- لو enabled + api_key → نرسل عبر Resend REST API (نفس نمط Manasety).

الاستخدام:
    from app.services.email import send_email
    send_email(to='x@y.com', subject='...', html_body='...')  # نجاح صامت
"""
from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error

from app.models.setting import get_setting


logger = logging.getLogger(__name__)


class EmailDisabled(Exception):
    """يُرفع لو الخدمة معطّلة في الإعدادات (نُتجاهله عادةً)."""


def _api_key() -> str:
    return str(get_setting("email.api_key", "") or "").strip()


def _from_address() -> str:
    default = "no-reply@drporcelain.local"
    return str(get_setting("email.from_address", default) or default).strip() or default


def send_email(*, to: str, subject: str, html_body: str,
               reply_to: str | None = None) -> bool:
    """يرسل إيميلًا واحدًا. يعيد True لو نجح، False لو فشل/معطّل.

    لا يرمي استثناءات إلا لو `to` فارغ (خطأ مبرمج).
    """
    if not to or not to.strip():
        return False

    if not bool(get_setting("email.enabled", False)):
        logger.info("email disabled — skipping send to %s", to)
        return False

    api_key = _api_key()
    if not api_key:
        logger.warning("email.enabled=true but email.api_key missing — skipping.")
        return False

    payload = {
        "from": _from_address(),
        "to": [to.strip()],
        "subject": subject,
        "html": html_body,
    }
    if reply_to:
        payload["reply_to"] = reply_to

    try:
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            resp.read()
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        logger.warning("email send failed to %s: %s", to, e)
        return False
    except Exception as e:
        logger.exception("email send unexpected error: %s", e)
        return False


# ============ قوالب جاهزة ============

def send_order_confirmation(*, order) -> bool:
    """إيميل تأكيد للعميل (Ticket 4 Epic 1)."""
    to = getattr(order, "guest_email", None) or (
        order.customer.email if order.customer and order.customer.email else None
    )
    if not to:
        return False

    store_name = str(get_setting("store.name", "المتجر"))
    subject = f"تأكيد طلبك {order.doc_number} — {store_name}"
    lines_html = "".join(
        f"<tr><td>{l.product_name}</td><td>{l.qty}</td><td>{l.unit_price}</td>"
        f"<td>{l.line_total}</td></tr>"
        for l in order.lines
    )
    html = f"""
    <div dir="rtl" style="font-family:Arial,sans-serif;">
      <h2>شكرًا على طلبك يا {order.customer_display_name}!</h2>
      <p>تم استلام طلبك رقم <strong>{order.doc_number}</strong> بنجاح.</p>
      <table border="1" cellpadding="6" style="border-collapse:collapse;">
        <thead><tr><th>المنتج</th><th>الكمية</th><th>السعر</th><th>الإجمالي</th></tr></thead>
        <tbody>{lines_html}</tbody>
      </table>
      <p style="margin-top:12px;"><strong>الإجمالي: {order.total} ج.م</strong></p>
      <p>عنوان الشحن: {order.shipping_city or ''} — {order.shipping_address}</p>
      <hr>
      <small style="color:#888;">{store_name}</small>
    </div>
    """
    return send_email(to=to, subject=subject, html_body=html)


def send_admin_new_order_alert(*, order, order_admin_url: str = "") -> bool:
    """إيميل تنبيه للأدمن على طلب جديد."""
    admin_to = str(get_setting("email.admin_notification_address", "") or "").strip()
    if not admin_to:
        return False

    store_name = str(get_setting("store.name", "المتجر"))
    subject = f"طلب جديد {order.doc_number} — {store_name}"
    html = f"""
    <div dir="rtl" style="font-family:Arial,sans-serif;">
      <h3>طلب جديد وارد</h3>
      <p>رقم الطلب: <strong>{order.doc_number}</strong></p>
      <p>العميل: {order.customer_display_name} — {order.customer_phone}</p>
      <p>الإجمالي: {order.total} ج.م</p>
      {'<p><a href="' + order_admin_url + '">فتح الطلب</a></p>' if order_admin_url else ''}
    </div>
    """
    return send_email(to=admin_to, subject=subject, html_body=html)


def send_password_reset(*, to_email: str, reset_link: str) -> bool:
    """إيميل استرجاع كلمة المرور."""
    store_name = str(get_setting("store.name", "المتجر"))
    html = f"""
    <div dir="rtl" style="font-family:Arial,sans-serif;">
      <h3>استرجاع كلمة المرور</h3>
      <p>طُلب استرجاع كلمة المرور لحسابك في {store_name}.</p>
      <p><a href="{reset_link}" style="background:#0284c7;color:white;padding:10px 20px;
             text-decoration:none;border-radius:6px;">إعادة تعيين كلمة المرور</a></p>
      <p><small>هذا الرابط صالح لمدة 30 دقيقة فقط. لو مش أنت اللي طلبت، تجاهل الإيميل.</small></p>
    </div>
    """
    return send_email(to=to_email, subject=f"استرجاع كلمة المرور — {store_name}", html_body=html)


def send_back_in_stock(*, to_email: str, variant, product_url: str) -> bool:
    """إيميل إعلام بتوفر منتج كان نفذ."""
    store_name = str(get_setting("store.name", "المتجر"))
    html = f"""
    <div dir="rtl" style="font-family:Arial,sans-serif;">
      <h3>المنتج المطلوب متوفر الآن!</h3>
      <p><strong>{variant.display_name}</strong> عاد للمخزون.</p>
      <p><a href="{product_url}" style="background:#0284c7;color:white;padding:10px 20px;
             text-decoration:none;border-radius:6px;">اشتري الآن</a></p>
      <hr>
      <small style="color:#888;">{store_name}</small>
    </div>
    """
    return send_email(to=to_email, subject=f"توفر منتج: {variant.display_name} — {store_name}", html_body=html)
