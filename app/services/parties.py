"""خدمة إدارة العملاء والموردين.

القواعد:
- عند إنشاء عميل جديد، نُنشِئ حساب فرعي تلقائيًا تحت 1200 (ذمم عملاء) بكود تسلسلي.
- عند إنشاء مورد جديد، نُنشِئ حساب فرعي تلقائيًا تحت 2100 (ذمم موردين).
- الحساب الفرعي مرتبط بالطرف عبر `Account.party_id` و `Party.account`.
- لا يمكن حذف الطرف طالما له حركة (رصيد != 0 أو قيود مرتبطة).
- كود الطرف تسلسلي مستمر (C-000001, V-000001).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Iterable

from flask import current_app

from app.extensions import db
from app.models.account import Account, AccountType
from app.models.journal import JournalEntry, JournalEntryStatus, JournalLine
from app.models.party import Party, PartyType
from app.models.sequence import NumberSequence


class PartyError(ValueError):
    """أخطاء منطقية على مستوى إدارة الأطراف."""


# -------- ثوابت الربط ----------
# كل نوع طرف يُربط بحساب أب معيّن.
PARTY_CONTROL_ACCOUNT: dict[PartyType, str] = {
    PartyType.CUSTOMER: "1200",  # ذمم عملاء
    PartyType.VENDOR: "2100",    # ذمم موردين
}


def create_party(
    *,
    type: PartyType,
    name_ar: str,
    phone: str | None = None,
    email: str | None = None,
    address: str | None = None,
    tax_number: str | None = None,
    notes: str | None = None,
) -> Party:
    """يُنشِئ طرفًا جديدًا مع فتح حساب فرعي تلقائي تحت الحساب الأب المناسب.

    ملاحظة: لا يعمل commit — الاستدعاء يُحكِم Transaction الخارجية.
    """
    name_ar = (name_ar or "").strip()
    if not name_ar:
        raise PartyError("اسم الطرف مطلوب.")
    if phone:
        phone = phone.strip()

    # منع تكرار العميل/المورد بنفس الرقم — تجربة UX مطلوبة عند تسجيل الفواتير
    if phone:
        existing = (
            db.session.query(Party)
            .filter_by(type=type, phone=phone, is_active=True)
            .first()
        )
        if existing is not None:
            raise PartyError(
                f"يوجد بالفعل {_type_label_ar(type)} برقم {phone}: {existing.name_ar} ({existing.code})."
            )

    # 1) توليد كود الطرف
    code = _next_party_code(type)

    # 2) إنشاء الطرف
    party = Party(
        type=type,
        code=code,
        name_ar=name_ar,
        phone=phone or None,
        email=(email or None),
        address=(address or None),
        tax_number=(tax_number or None),
        notes=(notes or None),
        is_active=True,
    )
    db.session.add(party)
    db.session.flush()  # نحتاج party.id

    # 3) إنشاء الحساب الفرعي تحت الحساب الأب المناسب
    account = _create_sub_account_for_party(party)
    db.session.flush()

    # 4) نتأكد إن الربط ثنائي الاتجاه
    assert account.party_id == party.id
    return party


def update_party(
    party_id: int,
    *,
    name_ar: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    address: str | None = None,
    tax_number: str | None = None,
    notes: str | None = None,
    is_active: bool | None = None,
) -> Party:
    party = db.session.get(Party, party_id)
    if party is None:
        raise PartyError("الطرف غير موجود.")

    if name_ar is not None:
        name_ar = name_ar.strip()
        if not name_ar:
            raise PartyError("الاسم لا يمكن أن يكون فارغًا.")
        party.name_ar = name_ar
        # نُحدِّث اسم الحساب المرتبط ليطابق (مع الاحتفاظ بالكود)
        if party.account is not None:
            party.account.name_ar = f"{name_ar} ({party.code})"

    if phone is not None:
        phone = phone.strip() or None
        if phone:
            dup = (
                db.session.query(Party)
                .filter(Party.type == party.type, Party.phone == phone, Party.id != party.id)
                .first()
            )
            if dup is not None:
                raise PartyError(f"رقم {phone} مستخدم لطرف آخر ({dup.code}).")
        party.phone = phone

    if email is not None:
        party.email = email.strip() or None
    if address is not None:
        party.address = address.strip() or None
    if tax_number is not None:
        party.tax_number = tax_number.strip() or None
    if notes is not None:
        party.notes = notes.strip() or None

    if is_active is not None:
        party.is_active = bool(is_active)
        # عند إلغاء التنشيط نمنع الحساب من الاستخدام في قيود جديدة
        if party.account is not None:
            party.account.is_active = bool(is_active)

    db.session.flush()
    return party


def deactivate_party(party_id: int) -> Party:
    """تعطيل الطرف (نستخدمها بدل الحذف). لا نحذف طرف له حركة أبدًا."""
    return update_party(party_id, is_active=False)


def can_delete_party(party_id: int) -> tuple[bool, str | None]:
    """يفحص إن كان يمكن حذف الطرف نهائيًا.

    شرط الحذف: لا حركة على حسابه (لا سطور قيود مرتبطة بحسابه على الإطلاق).
    """
    party = db.session.get(Party, party_id)
    if party is None:
        return False, "الطرف غير موجود."
    if party.account is None:
        return True, None
    lines_count = (
        db.session.query(JournalLine).filter_by(account_id=party.account.id).count()
    )
    if lines_count > 0:
        return False, "لا يمكن حذف طرف له حركة محاسبية — عطّله بدلاً من ذلك."
    return True, None


def delete_party(party_id: int) -> None:
    ok, reason = can_delete_party(party_id)
    if not ok:
        raise PartyError(reason or "لا يمكن الحذف.")
    party = db.session.get(Party, party_id)
    if party is None:
        return
    if party.account is not None:
        db.session.delete(party.account)
    db.session.delete(party)
    db.session.flush()


# ============ كشف الحساب ============

def party_statement(
    party_id: int,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """يُرجِع كشف الحساب مع الرصيد الجاري لكل حركة.

    البنية:
        {
          "party": Party,
          "account": Account,
          "opening_balance": Decimal,   # الرصيد قبل date_from
          "movements": [
              {"date": date, "doc_type": str, "doc_number": str, "memo": str,
               "debit": Decimal, "credit": Decimal, "balance": Decimal,
               "entry_id": int, "source_type": str, "source_id": int|None}
          ],
          "closing_balance": Decimal,
        }

    الرصيد الجاري بالجانب الطبيعي للحساب:
    - عميل (أصل): مدين - دائن
    - مورد (التزام): دائن - مدين
    """
    party = db.session.get(Party, party_id)
    if party is None:
        raise PartyError("الطرف غير موجود.")
    if party.account is None:
        raise PartyError(f"الطرف {party.code} ليس له حساب مرتبط.")

    account = party.account
    normal_debit = account.type.normal_side == "debit"

    def sign(d: Decimal, c: Decimal) -> Decimal:
        return (d - c) if normal_debit else (c - d)

    # الرصيد الافتتاحي: كل الحركات المرحّلة قبل date_from
    opening_balance = Decimal("0")
    if date_from is not None:
        rows = (
            db.session.query(JournalLine, JournalEntry)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .filter(JournalLine.account_id == account.id)
            .filter(JournalEntry.status == JournalEntryStatus.POSTED)
            .filter(JournalEntry.entry_date < date_from)
            .all()
        )
        for line, _ in rows:
            opening_balance += sign(Decimal(str(line.debit)), Decimal(str(line.credit)))

    # الحركات ضمن النطاق
    q = (
        db.session.query(JournalLine, JournalEntry)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .filter(JournalLine.account_id == account.id)
        .filter(JournalEntry.status == JournalEntryStatus.POSTED)
        .order_by(JournalEntry.entry_date, JournalEntry.id, JournalLine.id)
    )
    if date_from is not None:
        q = q.filter(JournalEntry.entry_date >= date_from)
    if date_to is not None:
        q = q.filter(JournalEntry.entry_date <= date_to)

    running = opening_balance
    movements = []
    for line, entry in q.all():
        d = Decimal(str(line.debit))
        c = Decimal(str(line.credit))
        running += sign(d, c)
        movements.append({
            "date": entry.entry_date,
            "doc_number": entry.doc_number,
            "doc_type": _doc_type_label_ar(entry.source_type.value),
            "source_type": entry.source_type.value,
            "source_id": entry.source_id,
            "entry_id": entry.id,
            "memo": line.memo or entry.memo or "",
            "debit": d,
            "credit": c,
            "balance": running,
        })

    return {
        "party": party,
        "account": account,
        "opening_balance": opening_balance,
        "movements": movements,
        "closing_balance": running,
        "current_balance": account.compute_balance() if (date_from or date_to) else running,
    }


# ============ Helpers داخلية ============

def _create_sub_account_for_party(party: Party) -> Account:
    """يُنشِئ الحساب الفرعي تحت الحساب الأب المناسب ويربطه بالطرف."""
    parent_code = PARTY_CONTROL_ACCOUNT[party.type]
    parent = db.session.query(Account).filter_by(code=parent_code).one_or_none()
    if parent is None:
        raise PartyError(
            f"الحساب الأب {parent_code} غير موجود — شغّل init-coa الأول."
        )

    # كود الحساب الفرعي: PARENT-code (مثال: 1200-C-000001)
    # نستخدم كود الطرف كامل في الحساب الفرعي لسهولة التتبع البصري.
    account = Account(
        code=f"{parent_code}-{party.code}",
        name_ar=f"{party.name_ar} ({party.code})",
        type=parent.type,  # نفس نوع الأب (أصل للعملاء، التزام للموردين)
        parent_id=parent.id,
        party_id=party.id,
        is_postable=True,
        is_system=False,
        is_active=True,
    )
    db.session.add(account)
    return account


def _next_party_code(party_type: PartyType) -> str:
    """يُرجِع كود تسلسلي فريد للعميل/المورد. مستقل عن ترقيم المستندات."""
    prefix = current_app.config["DOC_PREFIXES"][party_type.value]  # C أو V
    # نستخدم NumberSequence بـ year=0 (لا تصفير سنوي للأطراف)
    row = (
        db.session.query(NumberSequence)
        .filter_by(doc_type=f"party_{party_type.value}", year=0)
        .with_for_update()
        .one_or_none()
    )
    if row is None:
        row = NumberSequence(doc_type=f"party_{party_type.value}", year=0, last_number=0)
        db.session.add(row)
        db.session.flush()
    row.last_number += 1
    return f"{prefix}-{row.last_number:06d}"


def _type_label_ar(t: PartyType) -> str:
    return "عميل" if t == PartyType.CUSTOMER else "مورد"


DOC_TYPE_LABELS_AR = {
    "manual": "قيد يدوي",
    "sales_invoice": "فاتورة بيع",
    "sales_return": "مرتجع بيع",
    "purchase_invoice": "فاتورة شراء",
    "purchase_return": "مرتجع شراء",
    "customer_receipt": "تحصيل من عميل",
    "vendor_payment": "سداد لمورد",
    "installment": "سداد قسط",
    "pos_session": "قفل وردية POS",
    "inventory_adjustment": "تسوية مخزون",
    "reversal": "عكس قيد",
    "opening": "رصيد افتتاحي",
}


def _doc_type_label_ar(source_type: str) -> str:
    return DOC_TYPE_LABELS_AR.get(source_type, source_type)
