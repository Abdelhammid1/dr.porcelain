"""توليد أرقام باركود EAN-13 داخلية.

- نستخدم بادئة 200-299 (المخصصة للاستخدام الداخلي في المتاجر — In-Store).
- الرقم الحادي عشر هو رقم تسلسلي، الآخر رقم تحقق (checksum).
"""
from __future__ import annotations

from app.extensions import db
from app.models.product import ProductVariant
from app.models.sequence import NumberSequence


INTERNAL_PREFIX = "200"  # In-Store — لا يتعارض مع أكواد المنتجات الأصلية


def compute_ean13_checksum(digits12: str) -> int:
    """يحسب رقم التحقق لـ EAN-13 (Modulo 10)."""
    if len(digits12) != 12 or not digits12.isdigit():
        raise ValueError("EAN-13 يحتاج 12 رقمًا لحساب الـ checksum.")
    total = 0
    for i, ch in enumerate(digits12):
        n = int(ch)
        total += n if i % 2 == 0 else n * 3
    return (10 - (total % 10)) % 10


def next_internal_ean13() -> str:
    """يُرجِع باركود EAN-13 جديد غير مستخدَم.

    البنية: 200 + 9 أرقام تسلسلية + 1 checksum = 13 رقمًا.
    """
    row = (
        db.session.query(NumberSequence)
        .filter_by(doc_type="barcode_ean13", year=0)
        .with_for_update()
        .one_or_none()
    )
    if row is None:
        row = NumberSequence(doc_type="barcode_ean13", year=0, last_number=0)
        db.session.add(row)
        db.session.flush()

    # نحاول حتى نصل لباركود غير موجود (نادرًا يحدث تعارض إذا استُخدم يدويًا).
    for _ in range(50):
        row.last_number += 1
        body = f"{INTERNAL_PREFIX}{row.last_number:09d}"
        check = compute_ean13_checksum(body)
        code = f"{body}{check}"
        exists = db.session.query(ProductVariant.id).filter_by(barcode=code).first()
        if exists is None:
            return code
    raise RuntimeError("تعذّر توليد باركود فريد بعد محاولات متعددة.")
