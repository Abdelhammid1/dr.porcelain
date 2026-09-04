"""خدمة توليد أرقام المستندات المتسلسلة.

- ترقيم مستقل لكل نوع مستند + كل سنة (لو reset_annually مفعّل).
- البادئة (INV, PB, JE...) تُقرأ من `Config.DOC_PREFIXES`.
- آمن ضد التزامن عبر SELECT ... FOR UPDATE.

الاستخدام:
    from app.services.numbering import next_document_number
    number = next_document_number("sales_invoice")  # → "INV-2026-000001"
"""
from __future__ import annotations

from datetime import date

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.setting import get_setting
from app.models.sequence import NumberSequence


class NumberingError(RuntimeError):
    pass


def _current_year(today: date | None = None) -> int:
    """السنة المعتمدة للترقيم — إن كان تصفير مع بداية السنة المالية مختلف عن يناير،
    نأخذ الشهر في الاعتبار.
    """
    today = today or date.today()
    fiscal_start_month = int(get_setting("fiscal.year_start_month", 1) or 1)
    if fiscal_start_month == 1 or today.month >= fiscal_start_month:
        return today.year
    return today.year - 1


def next_document_number(doc_type: str, today: date | None = None) -> str:
    """يُرجِع رقم مستند جديد فريد.

    doc_type يجب أن يكون واحدًا من مفاتيح `Config.DOC_PREFIXES`.
    """
    prefixes: dict[str, str] = current_app.config["DOC_PREFIXES"]
    prefix = prefixes.get(doc_type)
    if not prefix:
        raise NumberingError(f"doc_type '{doc_type}' غير معرّف في DOC_PREFIXES")

    reset_annually = bool(get_setting("numbering.reset_annually", True))
    year = _current_year(today) if reset_annually else 0

    # SELECT ... FOR UPDATE لتفادي السباق. إذا لم يوجد الصف نُنشِئه.
    row = (
        db.session.query(NumberSequence)
        .filter_by(doc_type=doc_type, year=year)
        .with_for_update()
        .one_or_none()
    )
    if row is None:
        row = NumberSequence(doc_type=doc_type, year=year, last_number=0)
        db.session.add(row)
        try:
            db.session.flush()
        except IntegrityError:
            # سباق: صف آخر أُنشِئ في نفس اللحظة — نعيد المحاولة داخل الجلسة.
            db.session.rollback()
            row = (
                db.session.query(NumberSequence)
                .filter_by(doc_type=doc_type, year=year)
                .with_for_update()
                .one()
            )

    row.last_number += 1

    if reset_annually:
        return f"{prefix}-{year}-{row.last_number:06d}"
    return f"{prefix}-{row.last_number:06d}"
