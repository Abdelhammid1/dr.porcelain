"""كل الصلاحيات المتاحة في النظام (تُحقَن مرة واحدة عند التهيئة).

كل صلاحية:
    code:      المعرّف البرمجي (لا يتغير أبدًا بعد النشر)
    label_ar:  اسم للعرض
    group_ar:  تجميع في شاشة إدارة الأدوار
"""

PERMISSIONS: list[dict] = [
    # --- إدارة المستخدمين والأدوار ---
    {"code": "users.manage", "label_ar": "إدارة المستخدمين", "group_ar": "المستخدمون"},
    {"code": "roles.manage", "label_ar": "إدارة الأدوار والصلاحيات", "group_ar": "المستخدمون"},

    # --- الإعدادات ---
    {"code": "settings.manage", "label_ar": "إدارة إعدادات المتجر", "group_ar": "الإعدادات"},

    # --- دليل الحسابات والقيود ---
    {"code": "accounts.view", "label_ar": "عرض دليل الحسابات", "group_ar": "المحاسبة"},
    {"code": "accounts.manage", "label_ar": "إدارة دليل الحسابات", "group_ar": "المحاسبة"},
    {"code": "journal.view", "label_ar": "عرض القيود اليومية", "group_ar": "المحاسبة"},
    {"code": "journal.create", "label_ar": "إنشاء قيد يومي يدوي", "group_ar": "المحاسبة"},
    {"code": "journal.reverse", "label_ar": "عكس قيد", "group_ar": "المحاسبة"},

    # --- الأطراف ---
    {"code": "parties.view", "label_ar": "عرض العملاء والموردين", "group_ar": "العملاء والموردون"},
    {"code": "parties.manage", "label_ar": "إدارة العملاء والموردين", "group_ar": "العملاء والموردون"},

    # --- المنتجات والمخزون ---
    {"code": "products.view", "label_ar": "عرض المنتجات", "group_ar": "المنتجات والمخزون"},
    {"code": "products.manage", "label_ar": "إدارة المنتجات", "group_ar": "المنتجات والمخزون"},
    {"code": "inventory.view", "label_ar": "عرض حركة المخزون", "group_ar": "المنتجات والمخزون"},
    {"code": "inventory.adjust", "label_ar": "تسوية المخزون", "group_ar": "المنتجات والمخزون"},

    # --- المبيعات ---
    {"code": "sales.view", "label_ar": "عرض فواتير المبيعات", "group_ar": "المبيعات"},
    {"code": "sales.create", "label_ar": "إنشاء فاتورة بيع", "group_ar": "المبيعات"},
    {"code": "sales.return", "label_ar": "استرجاع بيع", "group_ar": "المبيعات"},
    {"code": "sales.discount", "label_ar": "إعطاء خصم على فاتورة البيع", "group_ar": "المبيعات"},

    # --- المشتريات ---
    {"code": "purchases.view", "label_ar": "عرض فواتير المشتريات", "group_ar": "المشتريات"},
    {"code": "purchases.create", "label_ar": "إنشاء فاتورة شراء", "group_ar": "المشتريات"},
    {"code": "purchases.return", "label_ar": "استرجاع شراء", "group_ar": "المشتريات"},

    # --- التحصيل والسداد ---
    {"code": "collect.customer", "label_ar": "تحصيل من عميل", "group_ar": "التحصيل والسداد"},
    {"code": "pay.vendor", "label_ar": "سداد لمورد", "group_ar": "التحصيل والسداد"},

    # --- سداد الموردين ---
    {"code": "vendor_payments.view", "label_ar": "عرض دفعات الموردين", "group_ar": "التحصيل والسداد"},
    {"code": "vendor_payments.manage", "label_ar": "إدارة جداول سداد الموردين", "group_ar": "التحصيل والسداد"},
    {"code": "vendor_payments.pay", "label_ar": "سداد دفعة لمورد", "group_ar": "التحصيل والسداد"},

    # --- تقارير الأعمار ---
    {"code": "reports.ap_aging", "label_ar": "تقرير أعمار ديون الموردين", "group_ar": "التقارير"},
    {"code": "reports.ar_aging", "label_ar": "تقرير أعمار ديون العملاء", "group_ar": "التقارير"},

    # --- القوائم المالية والتقارير المتقدمة ---
    {"code": "reports.income_statement", "label_ar": "قائمة الدخل", "group_ar": "التقارير"},
    {"code": "reports.balance_sheet",    "label_ar": "الميزانية العمومية", "group_ar": "التقارير"},
    {"code": "reports.cash_flow",        "label_ar": "التدفق النقدي", "group_ar": "التقارير"},
    {"code": "reports.vat",              "label_ar": "تقرير ضريبة القيمة المضافة", "group_ar": "التقارير"},
    {"code": "reports.profitability",    "label_ar": "تقرير ربحية المنتجات", "group_ar": "التقارير"},

    # --- التقسيط ---
    {"code": "installments.view", "label_ar": "عرض خطط التقسيط", "group_ar": "التقسيط"},
    {"code": "installments.create", "label_ar": "إنشاء بيع بالتقسيط", "group_ar": "التقسيط"},
    {"code": "installments.collect", "label_ar": "تحصيل قسط", "group_ar": "التقسيط"},

    # --- نقطة البيع ---
    {"code": "pos.use", "label_ar": "استخدام نقطة البيع", "group_ar": "نقطة البيع"},
    {"code": "pos.close_own", "label_ar": "قفل الوردية الخاصة", "group_ar": "نقطة البيع"},
    {"code": "pos.close_any", "label_ar": "قفل ورديات الآخرين", "group_ar": "نقطة البيع"},
    {"code": "pos.view_all", "label_ar": "عرض كل الورديات", "group_ar": "نقطة البيع"},

    # --- الطلبات الأونلاين ---
    {"code": "orders.view", "label_ar": "عرض الطلبات الأونلاين", "group_ar": "الطلبات"},
    {"code": "orders.manage", "label_ar": "إدارة حالات الطلبات", "group_ar": "الطلبات"},

    # --- مراجعات المنتجات (Epic 6 من تذكرة تحسينات المنتج) ---
    {"code": "reviews.view", "label_ar": "عرض مراجعات المنتجات", "group_ar": "المراجعات"},
    {"code": "reviews.moderate", "label_ar": "الموافقة على المراجعات أو رفضها", "group_ar": "المراجعات"},

    # --- أكواد الخصم (Ticket 2 Epic 2) ---
    {"code": "coupons.view", "label_ar": "عرض أكواد الخصم", "group_ar": "الخصومات"},
    {"code": "coupons.manage", "label_ar": "إنشاء وتعديل أكواد الخصم", "group_ar": "الخصومات"},

    # --- التقارير ---
    {"code": "reports.trial_balance", "label_ar": "ميزان مراجعة", "group_ar": "التقارير"},
    {"code": "reports.statements", "label_ar": "كشوف الحسابات", "group_ar": "التقارير"},
    {"code": "reports.financial", "label_ar": "القوائم المالية", "group_ar": "التقارير"},
]
