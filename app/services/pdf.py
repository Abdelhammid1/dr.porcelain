"""توليد ملفات PDF عربية RTL باستخدام ReportLab.

نستخدم:
- reportlab: للأساسي
- arabic_reshaper + bidi.algorithm.get_display: لتشكيل النصوص العربية بشكل صحيح
- خط Cairo (يُحمَّل من static/fonts أو يُحوَّل لخط عربي مثبت)

ملاحظة على الخطوط: ReportLab يحتاج ملف TTF محلي. لتوصيل خط Cairo:
- في التطوير الأول نستخدم DejaVuSans (يدعم عربي أساسي)
- في الإنتاج، يُنسَخ ملف Cairo-Regular.ttf و Cairo-Bold.ttf إلى app/static/fonts/
"""
from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.fonts import addMapping
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

import arabic_reshaper
from bidi.algorithm import get_display


# --------- تسجيل الخط العربي ---------

FONT_DIR = Path(__file__).resolve().parent.parent / "static" / "fonts"
DEFAULT_ARABIC_FONT_NAME = "AppArabic"
DEFAULT_ARABIC_BOLD_NAME = "AppArabic-Bold"
_font_registered = False


def _register_fonts() -> tuple[str, str]:
    """يُسجِّل خطًا عربيًا في ReportLab. يُرجِع (regular_name, bold_name)."""
    global _font_registered
    if _font_registered:
        return DEFAULT_ARABIC_FONT_NAME, DEFAULT_ARABIC_BOLD_NAME

    # نبحث عن ملفات الخط في مجلد الخطوط
    regular_candidates = [
        FONT_DIR / "Cairo-Regular.ttf",
        FONT_DIR / "Tajawal-Regular.ttf",
        FONT_DIR / "Amiri-Regular.ttf",
    ]
    bold_candidates = [
        FONT_DIR / "Cairo-Bold.ttf",
        FONT_DIR / "Tajawal-Bold.ttf",
        FONT_DIR / "Amiri-Bold.ttf",
    ]

    regular_path = next((p for p in regular_candidates if p.exists()), None)
    bold_path = next((p for p in bold_candidates if p.exists()), None)

    if regular_path is None:
        # fallback: DejaVuSans المدمج مع ReportLab (يدعم عربي أساسي)
        import reportlab
        rl_fonts = Path(reportlab.__file__).parent / "fonts"
        dejavu = rl_fonts / "DejaVuSans.ttf"
        if dejavu.exists():
            pdfmetrics.registerFont(TTFont(DEFAULT_ARABIC_FONT_NAME, str(dejavu)))
            pdfmetrics.registerFont(TTFont(DEFAULT_ARABIC_BOLD_NAME, str(dejavu)))
        else:
            # آخر ملاذ — Helvetica (لا يدعم عربية جيدًا)
            _font_registered = True
            return "Helvetica", "Helvetica-Bold"
    else:
        pdfmetrics.registerFont(TTFont(DEFAULT_ARABIC_FONT_NAME, str(regular_path)))
        if bold_path:
            pdfmetrics.registerFont(TTFont(DEFAULT_ARABIC_BOLD_NAME, str(bold_path)))
        else:
            pdfmetrics.registerFont(TTFont(DEFAULT_ARABIC_BOLD_NAME, str(regular_path)))

    # نُسجّل عائلة الخط حتى يعمل ReportLab Paragraph مع ps2tt.
    # الخط الأساسي "AppArabic" — ps2tt يستطيع تقسيمه إلى (apparabic, bold=1) عند رؤية "-Bold".
    addMapping("AppArabic", 0, 0, DEFAULT_ARABIC_FONT_NAME)  # regular
    addMapping("AppArabic", 1, 0, DEFAULT_ARABIC_BOLD_NAME)  # bold
    addMapping("AppArabic", 0, 1, DEFAULT_ARABIC_FONT_NAME)  # italic → regular
    addMapping("AppArabic", 1, 1, DEFAULT_ARABIC_BOLD_NAME)  # bold-italic → bold

    _font_registered = True
    return DEFAULT_ARABIC_FONT_NAME, DEFAULT_ARABIC_BOLD_NAME


def ar(text: Any) -> str:
    """يُشكِّل نصًا عربيًا للعرض في ReportLab (Right-to-Left + shaping)."""
    if text is None:
        return ""
    reshaped = arabic_reshaper.reshape(str(text))
    return get_display(reshaped)


# ============ عناصر مشتركة ============

def _base_doc(title: str) -> tuple[SimpleDocTemplate, io.BytesIO, dict]:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=title,
    )
    regular, bold = _register_fonts()

    styles = {
        "title": ParagraphStyle(
            "title", fontName=bold, fontSize=18, alignment=1,  # center
            leading=24, spaceAfter=8, textColor=colors.HexColor("#0f172a"),
        ),
        "subtitle": ParagraphStyle(
            "subtitle", fontName=regular, fontSize=11, alignment=1,
            leading=16, spaceAfter=16, textColor=colors.HexColor("#64748b"),
        ),
        "h2": ParagraphStyle(
            "h2", fontName=bold, fontSize=13, alignment=2,  # right
            leading=18, spaceAfter=6, textColor=colors.HexColor("#0f172a"),
        ),
        "body": ParagraphStyle(
            "body", fontName=regular, fontSize=10, alignment=2, leading=14,
        ),
        "small": ParagraphStyle(
            "small", fontName=regular, fontSize=8, alignment=2, leading=11,
            textColor=colors.HexColor("#64748b"),
        ),
    }
    return doc, buf, styles


def _header_footer(store_name: str, tax_number: str | None):
    def _draw(canvas, doc):
        canvas.saveState()
        regular, bold = DEFAULT_ARABIC_FONT_NAME, DEFAULT_ARABIC_BOLD_NAME
        canvas.setFont(bold, 12)
        canvas.setFillColor(colors.HexColor("#0f172a"))
        canvas.drawRightString(doc.pagesize[0] - 15 * mm, doc.pagesize[1] - 10 * mm,
                                ar(store_name))
        if tax_number:
            canvas.setFont(regular, 8)
            canvas.setFillColor(colors.HexColor("#64748b"))
            canvas.drawRightString(doc.pagesize[0] - 15 * mm, doc.pagesize[1] - 15 * mm,
                                    ar(f"الرقم الضريبي: {tax_number}"))
        # رقم الصفحة
        canvas.setFont(regular, 8)
        canvas.drawString(15 * mm, 8 * mm, ar(f"صفحة {doc.page}"))
        canvas.restoreState()
    return _draw


# ============ ميزان المراجعة PDF ============

def trial_balance_pdf(
    tb_data: dict,
    *,
    store_name: str,
    tax_number: str | None = None,
) -> bytes:
    doc, buf, styles = _base_doc("ميزان المراجعة")
    story: list[Any] = []

    story.append(Paragraph(ar("ميزان المراجعة"), styles["title"]))

    period = ""
    if tb_data.get("date_from") or tb_data.get("date_to"):
        parts = []
        if tb_data.get("date_from"):
            parts.append(f"من {tb_data['date_from']}")
        if tb_data.get("date_to"):
            parts.append(f"إلى {tb_data['date_to']}")
        period = " ".join(parts)
    else:
        period = "منذ بداية التشغيل"
    story.append(Paragraph(ar(period), styles["subtitle"]))

    # الجدول
    headers = ["الرصيد الدائن", "الرصيد المدين", "حركة دائن", "حركة مدين", "اسم الحساب", "الكود"]
    data = [[ar(h) for h in headers]]
    for row in tb_data["rows"]:
        acc = row["account"]
        data.append([
            _fmt_money(row["closing_credit"]),
            _fmt_money(row["closing_debit"]),
            _fmt_money(row["credit_movements"]),
            _fmt_money(row["debit_movements"]),
            ar(acc.name_ar),
            acc.code,
        ])
    # صف الإجمالي
    data.append([
        _fmt_money(tb_data["totals"]["credit"]),
        _fmt_money(tb_data["totals"]["debit"]),
        "", "",
        ar("الإجمالي"),
        "",
    ])

    col_widths = [26 * mm, 26 * mm, 26 * mm, 26 * mm, 55 * mm, 20 * mm]
    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
        ("FONT", (0, 0), (-1, 0), DEFAULT_ARABIC_BOLD_NAME, 9),
        ("FONT", (0, 1), (-1, -2), DEFAULT_ARABIC_FONT_NAME, 8),
        ("FONT", (0, -1), (-1, -1), DEFAULT_ARABIC_BOLD_NAME, 9),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f0f9ff")),
        ("ALIGN", (0, 0), (3, -1), "CENTER"),   # المبالغ
        ("ALIGN", (4, 0), (4, -1), "RIGHT"),    # الاسم
        ("ALIGN", (5, 0), (5, -1), "CENTER"),   # الكود
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(tbl)

    story.append(Spacer(1, 8 * mm))
    balance_note = "متوازن ✓" if tb_data["is_balanced"] else "غير متوازن — راجع القيود"
    story.append(Paragraph(ar(balance_note), styles["h2"]))

    doc.build(story,
              onFirstPage=_header_footer(store_name, tax_number),
              onLaterPages=_header_footer(store_name, tax_number))
    return buf.getvalue()


# ============ كشف حساب طرف PDF ============

def party_statement_pdf(
    stmt: dict,
    *,
    store_name: str,
    tax_number: str | None = None,
) -> bytes:
    doc, buf, styles = _base_doc(f"كشف حساب {stmt['party'].name_ar}")
    story: list[Any] = []

    story.append(Paragraph(ar("كشف حساب"), styles["title"]))

    party = stmt["party"]
    subtitle = f"{party.name_ar} — {party.code}"
    story.append(Paragraph(ar(subtitle), styles["subtitle"]))

    if stmt.get("movements") and (stmt.get("opening_balance") is not None):
        info = f"حساب: {stmt['account'].code} - {stmt['account'].name_ar}"
        story.append(Paragraph(ar(info), styles["small"]))
        story.append(Spacer(1, 4 * mm))

    # الجدول
    headers = ["الرصيد", "دائن", "مدين", "البيان", "المستند", "التاريخ"]
    data = [[ar(h) for h in headers]]

    # الرصيد الافتتاحي
    data.append([
        _fmt_money(stmt["opening_balance"]),
        "",
        "",
        ar("الرصيد الافتتاحي"),
        "",
        "",
    ])

    for m in stmt["movements"]:
        data.append([
            _fmt_money(m["balance"]),
            _fmt_money(m["credit"]) if m["credit"] > 0 else "—",
            _fmt_money(m["debit"]) if m["debit"] > 0 else "—",
            ar(m["memo"] or "—"),
            f"{ar(m['doc_type'])} {m['doc_number']}",
            str(m["date"]),
        ])

    # الرصيد الختامي
    data.append([
        _fmt_money(stmt["closing_balance"]),
        "", "",
        ar("الرصيد الختامي"),
        "", "",
    ])

    col_widths = [28 * mm, 22 * mm, 22 * mm, 60 * mm, 30 * mm, 22 * mm]
    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
        ("FONT", (0, 0), (-1, 0), DEFAULT_ARABIC_BOLD_NAME, 9),
        ("FONT", (0, 1), (-1, -2), DEFAULT_ARABIC_FONT_NAME, 8),
        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#f8fafc")),
        ("FONT", (0, 1), (-1, 1), DEFAULT_ARABIC_BOLD_NAME, 8),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f0f9ff")),
        ("FONT", (0, -1), (-1, -1), DEFAULT_ARABIC_BOLD_NAME, 9),
        ("ALIGN", (0, 0), (2, -1), "CENTER"),
        ("ALIGN", (3, 0), (3, -1), "RIGHT"),
        ("ALIGN", (4, 0), (5, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(tbl)

    doc.build(story,
              onFirstPage=_header_footer(store_name, tax_number),
              onLaterPages=_header_footer(store_name, tax_number))
    return buf.getvalue()


def _fmt_money(v: Decimal | int | float | None) -> str:
    if v is None:
        return ""
    d = Decimal(str(v)).quantize(Decimal("0.001"))
    return f"{d:,.3f}"
