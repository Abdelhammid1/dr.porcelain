"""خدمة ورديات نقطة البيع.

فتح وردية:
    1) يُنشأ حساب فرعي للكاشير تحت 1030 (لو لم يكن موجودًا)
    2) قيد تحويل عهدة: مدين 1030-XXX / دائن 1010 بالمبلغ الافتتاحي (لو > 0)
    3) POSSession بحالة OPEN

قفل وردية:
    1) closing_cash_expected = رصيد 1030-XXX الحالي (بعد كل مبيعات الوردية)
       = opening_cash + Σ (cash sales) - Σ (refunds)
    2) closing_cash_actual = المُدخل من الكاشير
    3) difference = actual - expected
    4) قيد فروقات (لو فرق):
       - عجز (actual < expected): مدين 6500 / دائن 1030-XXX بالفرق
       - زيادة (actual > expected): مدين 1030-XXX / دائن 6500 بالفرق
       - مطابق: لا قيد
    5) قيد تحويل النقدية الفعلية للصندوق الرئيسي:
       مدين 1010 / دائن 1030-XXX بمبلغ actual (لصفير عهدة الكاشير)
    6) الوردية تصبح CLOSED
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.extensions import db
from app.models.account import Account, AccountType
from app.models.journal import JournalSourceType
from app.models.pos import POSSession, SessionStatus
from app.models.user import User
from app.services.ledger import LedgerLineDraft, post_journal_entry
from app.services.numbering import next_document_number


class POSError(ValueError):
    pass


ZERO = Decimal("0")


# ============ فتح وردية ============

def open_session(
    *,
    cashier_id: int,
    opening_cash: Decimal | float | str = 0,
    notes: str | None = None,
) -> POSSession:
    """يفتح وردية جديدة للكاشير مع تحويل النقدية الافتتاحية إلى عهدته."""
    cashier = db.session.get(User, cashier_id)
    if cashier is None or not cashier.is_active:
        raise POSError("الكاشير غير موجود أو موقوف.")

    # منع فتح وردية جديدة قبل قفل الحالية
    existing_open = (
        db.session.query(POSSession)
        .filter_by(cashier_id=cashier_id, status=SessionStatus.OPEN)
        .first()
    )
    if existing_open is not None:
        raise POSError(
            f"يوجد وردية مفتوحة بالفعل ({existing_open.doc_number}) — أقفلها أولاً."
        )

    opening = _as_dec(opening_cash)
    if opening < 0:
        raise POSError("الرصيد الافتتاحي لا يمكن أن يكون سالبًا.")

    # 1) نضمن وجود حساب فرعي للكاشير تحت 1030
    custody = _ensure_custody_account(cashier)

    # 2) قيد تحويل العهدة (لو المبلغ > 0)
    doc_number = next_document_number("pos_session")
    session = POSSession(
        doc_number=doc_number,
        cashier_id=cashier_id,
        custody_account_id=custody.id,
        status=SessionStatus.OPEN,
        opening_cash=_q(opening),
        opened_at=datetime.now(timezone.utc),
        notes_open=(notes or None),
    )
    db.session.add(session)
    db.session.flush()

    if opening > 0:
        cash = _get_system_account("1010")
        post_journal_entry(
            entry_date=date.today(),
            source_type=JournalSourceType.POS_SESSION,
            source_id=session.id,
            memo=f"فتح وردية {session.doc_number} — تحويل عهدة",
            lines=[
                LedgerLineDraft(custody.id, debit=opening,
                                memo=f"عهدة كاشير {cashier.username}"),
                LedgerLineDraft(cash.id, credit=opening,
                                memo=f"سحب من الصندوق الرئيسي لعهدة {cashier.username}"),
            ],
            user_id=cashier_id,
        )

    db.session.flush()
    return session


# ============ قفل وردية ============

def close_session(
    *,
    session_id: int,
    closing_cash_actual: Decimal | float | str,
    notes: str | None = None,
    user_id: int | None = None,
) -> POSSession:
    """يقفل الوردية ويُنشئ القيود اللازمة (فروقات + تحويل النقدية للصندوق)."""
    session = db.session.get(POSSession, session_id)
    if session is None:
        raise POSError("الوردية غير موجودة.")
    if session.status != SessionStatus.OPEN:
        raise POSError(f"الوردية {session.doc_number} مقفلة بالفعل.")

    actual = _as_dec(closing_cash_actual)
    if actual < 0:
        raise POSError("الرصيد النقدي الفعلي لا يمكن أن يكون سالبًا.")

    custody = session.custody_account
    # المُتوقَّع = الرصيد الحالي لحساب العهدة (يعكس الافتتاحي + المبيعات - المرتجعات)
    expected = custody.compute_balance()
    diff = _q(actual - expected)

    session.closing_cash_expected = _q(expected)
    session.closing_cash_actual = _q(actual)
    session.difference = diff
    session.notes_close = (notes or None)

    cash = _get_system_account("1010")
    discrepancy_acc = _get_system_account("6500")

    # 1) قيد الفروقات (لو وُجد)
    if diff < 0:
        # عجز: مدين 6500 / دائن 1030-XXX
        amount = -diff  # نجعله موجب للقيد
        post_journal_entry(
            entry_date=date.today(),
            source_type=JournalSourceType.POS_SESSION,
            source_id=session.id,
            memo=f"عجز نقدية وردية {session.doc_number}",
            lines=[
                LedgerLineDraft(discrepancy_acc.id, debit=amount,
                                memo=f"عجز صندوق وردية {session.doc_number}"),
                LedgerLineDraft(custody.id, credit=amount,
                                memo=f"عجز في عهدة {session.cashier.username}"),
            ],
            user_id=user_id,
        )
    elif diff > 0:
        # زيادة: مدين 1030-XXX / دائن 6500
        post_journal_entry(
            entry_date=date.today(),
            source_type=JournalSourceType.POS_SESSION,
            source_id=session.id,
            memo=f"زيادة نقدية وردية {session.doc_number}",
            lines=[
                LedgerLineDraft(custody.id, debit=diff,
                                memo=f"زيادة في عهدة {session.cashier.username}"),
                LedgerLineDraft(discrepancy_acc.id, credit=diff,
                                memo=f"زيادة نقدية وردية {session.doc_number}"),
            ],
            user_id=user_id,
        )
    # لو diff == 0 لا نُنشئ قيد

    # 2) قيد تحويل النقدية الفعلية للصندوق الرئيسي
    # بعد قيد الفروقات، رصيد العهدة = actual. نُحوِّله كله للصندوق.
    if actual > 0:
        post_journal_entry(
            entry_date=date.today(),
            source_type=JournalSourceType.POS_SESSION,
            source_id=session.id,
            memo=f"قفل وردية {session.doc_number} — تسليم النقدية",
            lines=[
                LedgerLineDraft(cash.id, debit=actual,
                                memo=f"تسليم عهدة كاشير {session.cashier.username}"),
                LedgerLineDraft(custody.id, credit=actual,
                                memo=f"تسليم عهدة كاشير {session.cashier.username}"),
            ],
            user_id=user_id,
        )

    session.status = SessionStatus.CLOSED
    session.closed_at = datetime.now(timezone.utc)
    db.session.flush()
    return session


# ============ استعلامات ============

def current_open_session_for(user_id: int) -> POSSession | None:
    return (
        db.session.query(POSSession)
        .filter_by(cashier_id=user_id, status=SessionStatus.OPEN)
        .first()
    )


def session_summary(session_id: int) -> dict:
    """يُرجِع ملخّصًا شاملاً للوردية — عدد الفواتير، المبيعات، الفروقات."""
    session = db.session.get(POSSession, session_id)
    if session is None:
        raise POSError("الوردية غير موجودة.")

    sales = session.sales
    total_sales = sum((Decimal(str(s.total)) for s in sales), ZERO)
    cash_sales = sum((Decimal(str(s.total)) for s in sales if s.payment_method.value == "cash"), ZERO)
    card_sales = sum((Decimal(str(s.total)) for s in sales if s.payment_method.value == "card"), ZERO)
    wallet_sales = sum((Decimal(str(s.total)) for s in sales if s.payment_method.value == "wallet"), ZERO)
    invoices_count = len(sales)

    current_custody_balance = session.custody_account.compute_balance()

    return {
        "session": session,
        "total_sales": total_sales,
        "cash_sales": cash_sales,
        "card_sales": card_sales,
        "wallet_sales": wallet_sales,
        "invoices_count": invoices_count,
        "current_custody_balance": current_custody_balance,
    }


# ============ Helpers ============

def _ensure_custody_account(cashier: User) -> Account:
    """يُنشِئ حساب عهدة فرعي للكاشير تحت 1030 لو لم يوجد."""
    parent = db.session.query(Account).filter_by(code="1030").one_or_none()
    if parent is None:
        raise POSError("حساب عهدة الكاشير 1030 غير موجود — شغّل init-coa.")

    existing = (
        db.session.query(Account)
        .filter_by(parent_id=parent.id)
        .filter(Account.code.like(f"1030-U-{cashier.id:06d}%"))
        .first()
    )
    if existing:
        return existing

    account = Account(
        code=f"1030-U-{cashier.id:06d}",
        name_ar=f"عهدة {cashier.full_name} ({cashier.username})",
        type=parent.type,  # ASSET
        parent_id=parent.id,
        is_postable=True,
        is_system=False,
        is_active=True,
    )
    db.session.add(account)
    db.session.flush()
    return account


def _get_system_account(code: str) -> Account:
    acc = db.session.query(Account).filter_by(code=code).one_or_none()
    if acc is None:
        raise POSError(f"الحساب النظامي {code} غير موجود.")
    if not acc.is_postable:
        raise POSError(f"الحساب {code} غير قابل للترحيل المباشر.")
    return acc


def _as_dec(x) -> Decimal:
    if x is None or x == "":
        return ZERO
    return x if isinstance(x, Decimal) else Decimal(str(x))


def _q(v: Decimal, places: int = 3) -> Decimal:
    return v.quantize(Decimal(10) ** -places)
