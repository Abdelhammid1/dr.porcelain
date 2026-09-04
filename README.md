# دكتور بورسلين — النظام المحاسبي والمتجر الإلكتروني

نظام محاسبي متكامل ومتجر إلكتروني لأدوات ومستلزمات المنزل. عربي RTL بالكامل،
بمحرك دفتر أستاذ مزدوج القيد، نسخة مستقلة لكل عميل.

## التقنيات
- **Backend:** Flask 3 + SQLAlchemy 2 + Alembic (Flask-Migrate)
- **DB:** PostgreSQL 14+
- **Frontend:** Bootstrap 5 RTL + خط Cairo + Bootstrap Icons
- **PDF:** ReportLab + arabic-reshaper + python-bidi
- **الاختبارات:** pytest + pytest-flask (85 اختبار)

---

## المرحلة 1 — الأساس المحاسبي (مكتملة ✅)

| # | Epic | الحالة |
|---|------|-------|
| Scaffolding | Flask + PostgreSQL + Bootstrap RTL + Auth + Roles + Settings | ✅ |
| 1.1 | محرك دفتر الأستاذ (Balanced entries, Sequences, Reversals) | ✅ |
| 1.2 | العملاء والموردون (Auto sub-accounts، Statement view) | ✅ |
| 1.3 | المنتجات والمخزون (Variants، EAN-13، Weighted Avg Cost) | ✅ |
| 1.4 | فاتورة بيع كاش (3 قيود ذرية + مرتجعات) | ✅ |
| 1.5 | فاتورة شراء (نقدي/بنك/آجل + شحن + خصم) | ✅ |
| 1.6 | ميزان مراجعة + كشف PDF عربي | ✅ |

**اختبارات:** 85/85 ✓

---

## التثبيت والتشغيل (Windows)

### 1) تثبيت الاعتماديات
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2) تجهيز قاعدة البيانات
```powershell
& "C:\Program Files\PostgreSQL\18\bin\createdb.exe" -U postgres mtgar_dev
& "C:\Program Files\PostgreSQL\18\bin\createdb.exe" -U postgres mtgar_test
```

### 3) نسخ الإعدادات وتحديث كلمة مرور Postgres
```powershell
copy .env.example .env
# عدّل DATABASE_URL في .env
```

### 4) تشغيل Migrations وتهيئة البيانات
```powershell
flask db init
flask db migrate -m "initial"
flask db upgrade
flask init-all
```

### 5) تشغيل الخادم
```powershell
flask run
```
افتح: `http://127.0.0.1:5000` وسجّل دخول بـ `admin`.

## الاختبارات
```powershell
$env:TEST_DATABASE_URL="sqlite:///:memory:"; $env:FLASK_ENV="testing"; pytest
```

---

## الشاشات المتاحة

- **لوحة المعلومات** — KPIs مبدئية
- **العملاء / الموردون** — قائمة + بحث + إنشاء + عرض + تعديل + كشف حساب
- **المنتجات** — قائمة + فلترة + إنشاء بمتغيرات ديناميكية + عرض + تعديل
- **التصنيفات** — شجرة 3 مستويات + إدارة كاملة
- **حركة المخزون** — سجل كامل لكل متغير
- **فواتير المبيعات** — قائمة + إنشاء (بحث حي عن العملاء والمنتجات) + عرض + طباعة + مرتجع كامل/جزئي
- **فواتير المشتريات** — نفس الشيء + 3 طرق دفع
- **ميزان المراجعة** — ويب + PDF عربي RTL
- **كشف حساب أي طرف** — ويب + PDF

---

## القرارات المعمارية الثابتة

### النشر
- **نسخة مستقلة لكل عميل** (single-tenant per deployment)
- إعدادات المتجر تُعدَّل من جدول `settings` — لا hardcode

### القيود المالية
- **3 خانات عشرية** لكل المبالغ (`Numeric(18, 3)`)
- **الضريبة تُحسب مرة واحدة** على إجمالي الفاتورة
- **الترقيم يتصفّر مع كل سنة جديدة** (INV-2026-000001)
- **كل عملية داخل Transaction ذرية** — أي فشل جزئي = rollback كامل
- **القيود لا تُحذف** — تُعكس بقيد جديد مع سبب موثّق

### دليل الحسابات (33 حساب)
- الأصول 1000: النقدية، البنوك (متعددة)، عهدة الكاشير، المخزون، ذمم عملاء، ضريبة مدخلات
- الالتزامات 2000: ذمم موردين، ضريبة مخرجات، أقساط مستحقة
- حقوق الملكية 3000: رأس المال، أرباح محتجزة، مسحوبات
- الإيرادات 4000: مبيعات، مرتجعات، خصم مسموح به، إيرادات أخرى
- التكلفة 5000: COGS، مرتجعات مشتريات، خصم مكتسب
- المصروفات 6000: إيجار، مرتبات، كهرباء، تسويق، شحن، عمولات بنكية، فروقات صندوق، إهلاك، صيانة، متنوعة

### قيود فاتورة البيع الكاش (3 قيود ذرية)
```
1) البيع:      مدين 1200-CUST      / دائن 4100 + 2200 [+ خصم 4120]
2) التحصيل:    مدين 1010 (كاش)     / دائن 1200-CUST
3) COGS:      مدين 5100           / دائن 1100 (بمتوسط التكلفة)
```

### قيود فاتورة الشراء (حسب طريقة الدفع)
```
CASH   : مدين 1100 + 1300 / دائن 1010
BANK   : مدين 1100 + 1300 / دائن 1020-xxx (البنك المُختار)
CREDIT : مدين 1100 + 1300 / دائن 2100-VVV (حساب المورد)
```

### المتغيرات (Variants)
- نفس المنتج بألوان مختلفة = عدة `ProductVariant` تحت `Product` واحد
- كل متغير له SKU + باركود + سعر + رصيد مستقل
- الباركود EAN-13 يُولَّد تلقائيًا (بادئة 200 للاستخدام الداخلي)

---

## بنية المشروع
```
app/
├── __init__.py               # App factory
├── config.py                 # الإعدادات
├── extensions.py             # db, login, csrf
├── cli.py                    # init-all, init-coa, create-owner
├── models/                   # 12 model (Account, Journal, Party, Product, Sales, Purchases…)
├── services/                 # منطق الأعمال (ledger, sales, purchases, inventory, reports, pdf…)
├── blueprints/
│   ├── auth/                 # تسجيل الدخول
│   ├── main/                 # Dashboard
│   ├── parties/              # عملاء وموردون
│   ├── products/             # منتجات + تصنيفات
│   ├── sales/                # فواتير بيع
│   ├── purchases/            # فواتير شراء
│   └── reports/              # ميزان المراجعة + كشوف PDF
├── templates/                # قوالب Jinja2 RTL
└── static/
    ├── css/app.css           # نظام التصميم
    └── fonts/Cairo-*.ttf     # خط عربي للـ PDF
seeds/                        # صلاحيات + أدوار + دليل حسابات
tests/                        # 85 اختبار pytest
```

## الترخيص
ملكية خاصة — شلبي إنتليجنس للبرمجيات وحلول الذكاء الاصطناعي.
