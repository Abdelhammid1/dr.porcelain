"""فاتورة PDF للطلب (Ticket 4 Epic 4).

يستخدم البنية الجاهزة في `services.pdf` (arabic reshaping + خطوط) لتوليد
فاتورة تُحمَّل للعميل. البيانات الديناميكية للشركة تُقرأ من الإعدادات.
"""
from __future__ import annotations

import io
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from app.models.setting import get_setting
from app.services.pdf import _base_doc, _register_fonts, ar


def _sty():
    from reportlab.lib.styles import ParagraphStyle
    from app.services.pdf import _register_fonts
    font_reg, font_bold = _register_fonts()
    return {
        "h1": ParagraphStyle("h1", fontName=font_bold, fontSize=18,
                             alignment=1, spaceAfter=6),
        "h2": ParagraphStyle("h2", fontName=font_bold, fontSize=12,
                             alignment=2, spaceAfter=4),
        "body": ParagraphStyle("body", fontName=font_reg, fontSize=10,
                                alignment=2, spaceAfter=2),
        "small": ParagraphStyle("small", fontName=font_reg, fontSize=8,
                                 alignment=1, textColor=colors.grey),
    }


def order_invoice_pdf(order) -> bytes:
    """يُنتِج bytes لملف PDF بفاتورة الطلب."""
    doc, buf, meta = _base_doc(ar(f"فاتورة {order.doc_number}"))
    sty = _sty()
    _font_reg, font_bold = _register_fonts()

    store_name = str(get_setting("store.name", "المتجر"))
    store_addr = str(get_setting("store.address", ""))
    store_phone = str(get_setting("store.phone", ""))
    tax_num = str(get_setting("store.tax_number", ""))

    story = []
    story.append(Paragraph(ar(store_name), sty["h1"]))
    if store_addr:
        story.append(Paragraph(ar(store_addr), sty["small"]))
    if store_phone:
        story.append(Paragraph(ar(f"هاتف: {store_phone}"), sty["small"]))
    if tax_num:
        story.append(Paragraph(ar(f"رقم ضريبي: {tax_num}"), sty["small"]))
    story.append(Spacer(1, 6 * mm))

    story.append(Paragraph(ar(f"فاتورة رقم: {order.doc_number}"), sty["h2"]))
    story.append(Paragraph(ar(f"التاريخ: {order.order_date}"), sty["body"]))
    story.append(Paragraph(ar(f"العميل: {order.customer_display_name}"), sty["body"]))
    story.append(Paragraph(ar(f"الهاتف: {order.customer_phone}"), sty["body"]))
    if order.shipping_address:
        story.append(Paragraph(ar(f"الشحن: {order.shipping_city or ''} — {order.shipping_address}"),
                                sty["body"]))
    if order.status.value == "cancelled":
        story.append(Paragraph(ar("*** طلب ملغى ***"), sty["h2"]))
    story.append(Spacer(1, 6 * mm))

    # جدول البنود
    table_data = [[ar("م"), ar("المنتج"), ar("الكمية"), ar("السعر"), ar("الإجمالي")]]
    for i, line in enumerate(order.lines, start=1):
        table_data.append([
            ar(str(i)),
            ar(line.product_name),
            ar(str(line.qty)),
            ar(f"{line.unit_price} ج.م"),
            ar(f"{line.line_total} ج.م"),
        ])
    tbl = Table(table_data, colWidths=[15*mm, 70*mm, 20*mm, 30*mm, 30*mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0284c7")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 6 * mm))

    # الملخص
    summary = [
        [ar("الإجمالي الفرعي"), ar(f"{order.subtotal} ج.م")],
    ]
    if Decimal(str(order.discount_amount or 0)) > 0:
        summary.append([ar("الخصم"), ar(f"-{order.discount_amount} ج.م")])
    if Decimal(str(order.tax_amount or 0)) > 0:
        summary.append([ar(f"ضريبة {order.tax_rate}%"), ar(f"{order.tax_amount} ج.م")])
    if Decimal(str(order.shipping_fee or 0)) > 0:
        summary.append([ar("الشحن"), ar(f"{order.shipping_fee} ج.م")])
    summary.append([ar("الإجمالي النهائي"), ar(f"{order.total} ج.م")])

    stbl = Table(summary, colWidths=[80*mm, 40*mm])
    stbl.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LINEABOVE", (0, -1), (-1, -1), 1, colors.black),
        ("FONTNAME", (0, -1), (-1, -1), font_bold),
    ]))
    story.append(stbl)
    story.append(Spacer(1, 10 * mm))

    # بيانات الدفع (لو موجودة)
    bank = str(get_setting("payment.bank_name", "") or "")
    bank_acc = str(get_setting("payment.bank_account_number", "") or "")
    if bank and bank_acc:
        story.append(Paragraph(ar(f"للتحويل: {bank} — {bank_acc}"), sty["small"]))
    instapay = str(get_setting("payment.instapay_handle", "") or "")
    if instapay:
        story.append(Paragraph(ar(f"InstaPay: {instapay}"), sty["small"]))

    doc.build(story)
    return buf.getvalue()
