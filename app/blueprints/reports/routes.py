"""Routes للتقارير الأساسية + تصدير PDF."""
from __future__ import annotations

from datetime import date, datetime

from flask import Response, abort, render_template, request
from flask_login import login_required

from app.blueprints.reports import reports_bp
from app.extensions import db
from app.models.party import Party, PartyType
from app.models.setting import get_setting
from app.services.aging import ap_aging, ar_aging
from app.services.excel import csv_response
from app.services.financial_statements import balance_sheet, cash_flow, income_statement
from app.services.pdf import party_statement_pdf, trial_balance_pdf
from app.services.profitability import product_profitability
from app.services.reports import party_statement_data, trial_balance
from app.services.security import require_permission
from app.services.vat_report import vat_report


# ============ Trial Balance ============

@reports_bp.route("/trial-balance", methods=["GET"])
@login_required
@require_permission("reports.trial_balance")
def trial_balance_view():
    date_from = _parse_date(request.args.get("from"))
    date_to = _parse_date(request.args.get("to"))
    include_zero = request.args.get("include_zero") == "1"

    data = trial_balance(date_from=date_from, date_to=date_to, include_zero_balance=include_zero)
    return render_template(
        "reports/trial_balance.html",
        data=data,
        date_from=date_from,
        date_to=date_to,
        include_zero=include_zero,
    )


@reports_bp.route("/trial-balance.pdf", methods=["GET"])
@login_required
@require_permission("reports.trial_balance")
def trial_balance_pdf_view():
    date_from = _parse_date(request.args.get("from"))
    date_to = _parse_date(request.args.get("to"))
    include_zero = request.args.get("include_zero") == "1"

    data = trial_balance(date_from=date_from, date_to=date_to, include_zero_balance=include_zero)
    pdf_bytes = trial_balance_pdf(
        data,
        store_name=str(get_setting("store.name", "المتجر")),
        tax_number=str(get_setting("store.tax_number", "") or "") or None,
    )
    filename = f"trial-balance-{date.today().isoformat()}.pdf"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"inline; filename={filename}"},
    )


# ============ كشف حساب طرف ============

@reports_bp.route("/party-statement/<type_str>/<int:party_id>", methods=["GET"])
@login_required
@require_permission("reports.statements")
def party_statement_view(type_str, party_id):
    try:
        party_type = PartyType(type_str)
    except ValueError:
        abort(404)
    party = db.session.get(Party, party_id)
    if party is None or party.type != party_type:
        abort(404)

    date_from = _parse_date(request.args.get("from"))
    date_to = _parse_date(request.args.get("to"))
    stmt = party_statement_data(party_id, date_from=date_from, date_to=date_to)
    return render_template(
        "reports/party_statement.html",
        stmt=stmt,
        party=party,
        party_type=party_type,
        date_from=date_from,
        date_to=date_to,
    )


@reports_bp.route("/party-statement/<type_str>/<int:party_id>.pdf", methods=["GET"])
@login_required
@require_permission("reports.statements")
def party_statement_pdf_view(type_str, party_id):
    try:
        party_type = PartyType(type_str)
    except ValueError:
        abort(404)
    party = db.session.get(Party, party_id)
    if party is None or party.type != party_type:
        abort(404)

    date_from = _parse_date(request.args.get("from"))
    date_to = _parse_date(request.args.get("to"))
    stmt = party_statement_data(party_id, date_from=date_from, date_to=date_to)
    pdf_bytes = party_statement_pdf(
        stmt,
        store_name=str(get_setting("store.name", "المتجر")),
        tax_number=str(get_setting("store.tax_number", "") or "") or None,
    )
    filename = f"statement-{party.code}-{date.today().isoformat()}.pdf"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"inline; filename={filename}"},
    )


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


# ============ AP / AR Aging ============

@reports_bp.route("/ap-aging", methods=["GET"])
@login_required
@require_permission("reports.ap_aging")
def ap_aging_view():
    as_of = _parse_date(request.args.get("as_of")) or date.today()
    data = ap_aging(as_of=as_of)
    return render_template("reports/ap_aging.html", data=data, as_of=as_of)


@reports_bp.route("/ar-aging", methods=["GET"])
@login_required
@require_permission("reports.ar_aging")
def ar_aging_view():
    as_of = _parse_date(request.args.get("as_of")) or date.today()
    data = ar_aging(as_of=as_of)
    return render_template("reports/ar_aging.html", data=data, as_of=as_of)


# ============ القوائم المالية ============

def _default_period():
    """أول الشهر الحالي إلى اليوم."""
    today = date.today()
    return today.replace(day=1), today


@reports_bp.route("/income-statement", methods=["GET"])
@login_required
@require_permission("reports.income_statement")
def income_statement_view():
    default_from, default_to = _default_period()
    date_from = _parse_date(request.args.get("from")) or default_from
    date_to = _parse_date(request.args.get("to")) or default_to
    stmt = income_statement(date_from=date_from, date_to=date_to)
    return render_template("reports/income_statement.html",
                           stmt=stmt, date_from=date_from, date_to=date_to)


@reports_bp.route("/income-statement.csv", methods=["GET"])
@login_required
@require_permission("reports.income_statement")
def income_statement_csv():
    default_from, default_to = _default_period()
    date_from = _parse_date(request.args.get("from")) or default_from
    date_to = _parse_date(request.args.get("to")) or default_to
    stmt = income_statement(date_from=date_from, date_to=date_to)

    rows = []
    rows.append(["الإيرادات", "", ""])
    for l in stmt.revenues:
        rows.append([l.account.code, l.account.name_ar, l.amount])
    rows.append(["إجمالي الإيرادات", "", stmt.total_revenue])
    rows.append(["تكلفة البضاعة المباعة", "", ""])
    for l in stmt.costs:
        rows.append([l.account.code, l.account.name_ar, l.amount])
    rows.append(["إجمالي التكلفة", "", stmt.total_cost])
    rows.append(["الربح الإجمالي", "", stmt.gross_profit])
    rows.append(["المصروفات", "", ""])
    for l in stmt.expenses:
        rows.append([l.account.code, l.account.name_ar, l.amount])
    rows.append(["إجمالي المصروفات", "", stmt.total_expense])
    rows.append(["صافي الربح", "", stmt.net_profit])

    data, mime, fn = csv_response(
        ["الكود", "البيان", "المبلغ"],
        rows,
        filename=f"income-statement-{date_from}-to-{date_to}.csv",
    )
    return Response(data, mimetype=mime, headers={"Content-Disposition": f"attachment; filename={fn}"})


@reports_bp.route("/balance-sheet", methods=["GET"])
@login_required
@require_permission("reports.balance_sheet")
def balance_sheet_view():
    as_of = _parse_date(request.args.get("as_of")) or date.today()
    bs = balance_sheet(as_of=as_of)
    return render_template("reports/balance_sheet.html", bs=bs, as_of=as_of)


@reports_bp.route("/cash-flow", methods=["GET"])
@login_required
@require_permission("reports.cash_flow")
def cash_flow_view():
    default_from, default_to = _default_period()
    date_from = _parse_date(request.args.get("from")) or default_from
    date_to = _parse_date(request.args.get("to")) or default_to
    cf = cash_flow(date_from=date_from, date_to=date_to)
    return render_template("reports/cash_flow.html",
                           cf=cf, date_from=date_from, date_to=date_to)


# ============ VAT ============

@reports_bp.route("/vat", methods=["GET"])
@login_required
@require_permission("reports.vat")
def vat_view():
    default_from, default_to = _default_period()
    date_from = _parse_date(request.args.get("from")) or default_from
    date_to = _parse_date(request.args.get("to")) or default_to
    report = vat_report(date_from=date_from, date_to=date_to)
    return render_template("reports/vat.html",
                           report=report, date_from=date_from, date_to=date_to)


# ============ ربحية المنتجات ============

@reports_bp.route("/profitability", methods=["GET"])
@login_required
@require_permission("reports.profitability")
def profitability_view():
    default_from, default_to = _default_period()
    date_from = _parse_date(request.args.get("from")) or default_from
    date_to = _parse_date(request.args.get("to")) or default_to
    report = product_profitability(date_from=date_from, date_to=date_to)
    return render_template("reports/profitability.html",
                           report=report, date_from=date_from, date_to=date_to)


@reports_bp.route("/profitability.csv", methods=["GET"])
@login_required
@require_permission("reports.profitability")
def profitability_csv():
    default_from, default_to = _default_period()
    date_from = _parse_date(request.args.get("from")) or default_from
    date_to = _parse_date(request.args.get("to")) or default_to
    report = product_profitability(date_from=date_from, date_to=date_to)

    rows = []
    for r in report.rows:
        rows.append([
            r.product.name_ar, r.qty_sold, r.revenue, r.cost, r.gross_profit, r.margin_percent,
        ])
    rows.append(["الإجمالي", "", report.total_revenue, report.total_cost,
                 report.total_gross_profit, report.total_margin_percent])

    data, mime, fn = csv_response(
        ["المنتج", "الكمية", "الإيراد", "التكلفة", "الربح الإجمالي", "الهامش %"],
        rows,
        filename=f"profitability-{date_from}-to-{date_to}.csv",
    )
    return Response(data, mimetype=mime, headers={"Content-Disposition": f"attachment; filename={fn}"})
