"""الأدوار الافتراضية.

- owner: يحصل على كل الصلاحيات تلقائيًا (User.can() تُختصر لـ True له).
- accountant: كل شيء ما عدا إدارة المستخدمين/الأدوار.
- cashier: بيع فقط + عرض المنتجات/العملاء.
"""

ROLE_DEFAULTS: list[dict] = [
    {
        "code": "owner",
        "name_ar": "صاحب المتجر",
        "description": "صلاحيات كاملة على النظام.",
        "is_system": True,
        # المالك لا يحتاج قائمة صراحة — User.can() تُرجع True دومًا لكن نضيفها للاتساق
        "permissions": [],  # يعطى كل شيء ضمنيًا
    },
    {
        "code": "accountant",
        "name_ar": "محاسب",
        "description": "إدارة كل ما يخص المحاسبة والمبيعات والمشتريات والتقارير.",
        "is_system": True,
        "permissions": [
            "settings.manage",
            "accounts.view", "accounts.manage",
            "journal.view", "journal.create", "journal.reverse",
            "parties.view", "parties.manage",
            "products.view", "products.manage",
            "inventory.view", "inventory.adjust",
            "sales.view", "sales.create", "sales.return", "sales.discount",
            "purchases.view", "purchases.create", "purchases.return",
            "collect.customer", "pay.vendor",
            "vendor_payments.view", "vendor_payments.manage", "vendor_payments.pay",
            "reports.ap_aging", "reports.ar_aging",
            "reports.income_statement", "reports.balance_sheet", "reports.cash_flow",
            "reports.vat", "reports.profitability",
            "orders.view", "orders.manage",
            "installments.view", "installments.create", "installments.collect",
            "pos.use", "pos.close_own", "pos.close_any", "pos.view_all",
            "reports.trial_balance", "reports.statements", "reports.financial",
        ],
    },
    {
        "code": "cashier",
        "name_ar": "كاشير",
        "description": "بيع فقط + عرض العملاء والمنتجات + تحصيل + نقطة البيع.",
        "is_system": True,
        "permissions": [
            "parties.view",
            "products.view",
            "sales.view", "sales.create", "sales.discount",
            "collect.customer",
            "installments.view", "installments.create", "installments.collect",
            "pos.use", "pos.close_own",
        ],
    },
]
