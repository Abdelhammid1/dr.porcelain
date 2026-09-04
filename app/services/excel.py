"""تصدير التقارير إلى Excel — يستخدم `openpyxl` (مُثبَّت بالفعل عبر reportlab إن لم يكن مستقلًا).

نظام بسيط بلا اعتماديات جديدة: نُنتج CSV UTF-8 مع BOM لضمان قراءة العربي في Excel.
Excel يقرأ CSV UTF-8-with-BOM ك RTL تلقائيًا عند تحديد اللغة العربية في النظام.
"""
from __future__ import annotations

import csv
import io
from decimal import Decimal
from typing import Iterable


def csv_response(headers: list[str], rows: Iterable[list], *, filename: str) -> tuple[bytes, str, str]:
    """يُرجِع (bytes, mimetype, filename) لاستخدامه في Flask Response.

    البيانات مُنسَّقة كـ CSV UTF-8-with-BOM. تُفتح في Excel وتُظهر العربية بشكل صحيح.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_fmt(v) for v in row])
    data = ("﻿" + buf.getvalue()).encode("utf-8")
    return data, "text/csv; charset=utf-8", filename


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, Decimal):
        return f"{v:.3f}"
    return v
