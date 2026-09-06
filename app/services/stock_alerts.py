"""خدمة تنبيهات "أعلمني لما يتوفر" (Ticket 4 Epic 6)."""
from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db
from app.models.product import ProductVariant
from app.models.stock_alert import StockAlert


class AlertError(ValueError):
    pass


def subscribe(*, variant_id: int, email: str,
              customer_id: int | None = None) -> StockAlert:
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise AlertError("أدخل بريدًا إلكترونيًا صحيحًا.")
    v = db.session.get(ProductVariant, variant_id)
    if v is None:
        raise AlertError("المنتج غير موجود.")

    # منع التكرار: لو نفس البريد له تنبيه غير مُرسل لنفس المتغير، نُرجعه
    existing = (
        db.session.query(StockAlert)
        .filter_by(variant_id=variant_id, email=email, notified_at=None)
        .first()
    )
    if existing is not None:
        return existing

    alert = StockAlert(
        variant_id=variant_id,
        email=email,
        customer_id=customer_id,
    )
    db.session.add(alert)
    db.session.flush()
    return alert


def notify_available(variant_id: int) -> int:
    """يُرسل إيميلات لكل الاشتراكات غير المُرسَلة لهذا المتغير.

    يُستدعى تلقائيًا من hook في inventory (بعد شراء يرفع الرصيد من 0 لأكتر).
    يعيد عدد الإيميلات المُرسَلة.
    """
    from flask import url_for
    from app.services.email import send_back_in_stock

    pending = (
        db.session.query(StockAlert)
        .filter_by(variant_id=variant_id, notified_at=None)
        .all()
    )
    if not pending:
        return 0

    v = db.session.get(ProductVariant, variant_id)
    if v is None or v.stock_qty <= 0:
        return 0

    try:
        product_url = url_for("storefront.product", product_id=v.product_id,
                              _external=True)
    except Exception:
        product_url = f"/shop/product/{v.product_id}"

    sent = 0
    now = datetime.now(timezone.utc)
    for alert in pending:
        try:
            send_back_in_stock(to_email=alert.email, variant=v,
                                product_url=product_url)
        except Exception:
            pass
        alert.notified_at = now
        sent += 1
    db.session.flush()
    return sent
