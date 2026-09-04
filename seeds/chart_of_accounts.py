"""دليل الحسابات الافتراضي — يشمل الأكواد المذكورة في الوثيقة + حسابات إضافية.

كل حساب في الشجرة أدناه:
    (code, name_ar, type, is_postable, parent_code)

- الحساب الأب (Control) عادةً is_postable=False — الترحيل يتم على الأبناء.
- كل هذه الحسابات is_system=True (لا تُحذف من الواجهة).

الحسابات الفرعية للعملاء (1200-xxx) والموردين (2100-xxx) تُنشَأ ديناميكيًا في Epic 1.2.
حسابات البنك المتعددة (1020-xxx) وعهدة الكاشير (1030-xxx) تُنشَأ من الإعدادات.
"""
from __future__ import annotations

from app.models.account import AccountType as T

# قائمة (code, name_ar, type, is_postable, parent_code)
CHART_OF_ACCOUNTS: list[tuple[str, str, T, bool, str | None]] = [
    # ================= الأصول =================
    ("1000", "الأصول", T.ASSET, False, None),
    ("1010", "النقدية بالصندوق", T.ASSET, True, "1000"),
    ("1020", "البنوك", T.ASSET, False, "1000"),          # أب — الأبناء 1020-001, 1020-002
    ("1030", "عهدة الكاشير", T.ASSET, False, "1000"),    # أب — أبناء لكل كاشير
    ("1100", "المخزون", T.ASSET, True, "1000"),
    ("1200", "ذمم عملاء", T.ASSET, False, "1000"),       # أب — الأبناء 1200-xxx لكل عميل
    ("1300", "ضريبة القيمة المضافة — مدخلات", T.ASSET, True, "1000"),

    # ================= الالتزامات =================
    ("2000", "الالتزامات", T.LIABILITY, False, None),
    ("2100", "ذمم موردين", T.LIABILITY, False, "2000"),  # أب — الأبناء 2100-xxx
    ("2200", "ضريبة القيمة المضافة — مخرجات", T.LIABILITY, True, "2000"),
    ("2300", "أقساط مستحقة على العملاء (تحكم)", T.LIABILITY, False, "2000"),  # يُستخدَم لاحقًا في Epic 3

    # ================= حقوق الملكية =================
    ("3000", "حقوق الملكية", T.EQUITY, False, None),
    ("3100", "رأس المال", T.EQUITY, True, "3000"),
    ("3200", "الأرباح المحتجزة", T.EQUITY, True, "3000"),
    ("3300", "مسحوبات صاحب المتجر", T.EQUITY, True, "3000"),

    # ================= الإيرادات =================
    ("4000", "الإيرادات", T.REVENUE, False, None),
    ("4100", "إيرادات المبيعات", T.REVENUE, True, "4000"),
    ("4110", "مرتجعات المبيعات", T.REVENUE, True, "4000"),   # Contra Revenue
    ("4120", "خصم مسموح به", T.REVENUE, True, "4000"),        # Contra Revenue
    ("4900", "إيرادات أخرى", T.REVENUE, True, "4000"),

    # ================= تكلفة البضاعة المباعة =================
    ("5000", "التكاليف", T.COST, False, None),
    ("5100", "تكلفة البضاعة المباعة", T.COST, True, "5000"),
    ("5110", "مرتجعات المشتريات", T.COST, True, "5000"),     # Contra COGS
    ("5120", "خصم مكتسب", T.COST, True, "5000"),              # Contra COGS

    # ================= المصروفات التشغيلية =================
    ("6000", "المصروفات التشغيلية", T.EXPENSE, False, None),
    ("6100", "إيجار", T.EXPENSE, True, "6000"),
    ("6200", "مرتبات وأجور", T.EXPENSE, True, "6000"),
    ("6300", "كهرباء ومياه وإنترنت", T.EXPENSE, True, "6000"),
    ("6400", "تسويق وإعلان", T.EXPENSE, True, "6000"),
    ("6410", "مصاريف شحن", T.EXPENSE, True, "6000"),
    ("6420", "مصاريف بنكية وعمولات", T.EXPENSE, True, "6000"),
    ("6500", "فروقات صندوق", T.EXPENSE, True, "6000"),
    ("6600", "إهلاك", T.EXPENSE, True, "6000"),
    ("6700", "صيانة ومستهلكات", T.EXPENSE, True, "6000"),
    ("6900", "مصروفات متنوعة", T.EXPENSE, True, "6000"),
]


def seed_chart_of_accounts(db_session) -> tuple[int, int]:
    """يُنشِئ الحسابات غير الموجودة. يُرجِع (created_count, skipped_count)."""
    from app.models.account import Account

    existing = {a.code: a for a in db_session.query(Account).all()}
    created = 0
    skipped = 0

    # نُنشِئ الحسابات على مستويات — الآباء أولًا
    for code, name_ar, atype, is_postable, parent_code in CHART_OF_ACCOUNTS:
        if code in existing:
            skipped += 1
            continue
        parent_id = None
        if parent_code:
            parent = existing.get(parent_code)
            if parent is None:
                # الأب لسه ما اتضافش في نفس اللفة — نلاقيه من قاعدة البيانات
                parent = db_session.query(Account).filter_by(code=parent_code).one_or_none()
            if parent is None:
                raise RuntimeError(
                    f"الحساب الأب {parent_code} غير موجود عند إضافة {code}."
                )
            parent_id = parent.id
        acc = Account(
            code=code,
            name_ar=name_ar,
            type=atype,
            parent_id=parent_id,
            is_postable=is_postable,
            is_system=True,
            is_active=True,
        )
        db_session.add(acc)
        db_session.flush()  # نحتاج الـ id للأبناء اللاحقين
        existing[code] = acc
        created += 1

    return created, skipped
