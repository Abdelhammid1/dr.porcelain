"""إعدادات المتجر — Key/Value مع نوع مُعرَّف لكل مفتاح.

هذا هو الجدول الذي يُستخدَم للتخصيص بين نسخة نشر وأخرى (اسم المتجر، الرقم الضريبي،
اللوجو، بداية السنة المالية، نسبة الضريبة، سياسة المرتجعات، إلخ).

كل مفتاح له default_value في `SETTING_DEFAULTS` أدناه. القراءة والكتابة تتم عبر
`app.services.settings.get_setting()` و `set_setting()`.
"""
from __future__ import annotations

from decimal import Decimal
from sqlalchemy import Column, Integer, String, Text

from app.extensions import db
from app.models.base import TimestampMixin


class Setting(db.Model, TimestampMixin):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True)
    key = Column(String(80), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=True)
    # dtype يخبرنا كيف نُحوّل value من نص إلى قيمة فعلية (str/int/decimal/bool/date/json).
    dtype = Column(String(20), nullable=False, default="str")
    label_ar = Column(String(160), nullable=True)
    group_ar = Column(String(80), nullable=True)

    def __repr__(self) -> str:
        return f"<Setting {self.key}={self.value!r}>"


# قيم افتراضية تُحقَن في الجدول عند أول تشغيل عبر `flask init-settings`.
# label_ar وgroup_ar تُستخدَم في شاشة الإعدادات.
SETTING_DEFAULTS: dict[str, dict] = {
    # --- هوية المتجر ---
    "store.name": {
        "value": "دكتور بورسلين",
        "dtype": "str",
        "label_ar": "اسم المتجر",
        "group_ar": "هوية المتجر",
    },
    "store.legal_name": {
        "value": "",
        "dtype": "str",
        "label_ar": "الاسم القانوني (على الفاتورة)",
        "group_ar": "هوية المتجر",
    },
    "store.address": {
        "value": "",
        "dtype": "str",
        "label_ar": "العنوان",
        "group_ar": "هوية المتجر",
    },
    "store.phone": {
        "value": "",
        "dtype": "str",
        "label_ar": "الهاتف",
        "group_ar": "هوية المتجر",
    },
    "store.tax_number": {
        "value": "",
        "dtype": "str",
        "label_ar": "الرقم الضريبي",
        "group_ar": "هوية المتجر",
    },
    "store.commercial_reg": {
        "value": "",
        "dtype": "str",
        "label_ar": "رقم السجل التجاري",
        "group_ar": "هوية المتجر",
    },
    "store.logo_path": {
        "value": "",
        "dtype": "str",
        "label_ar": "مسار اللوجو",
        "group_ar": "هوية المتجر",
    },
    # --- الضريبة ---
    "tax.enabled": {
        "value": "false",
        "dtype": "bool",
        "label_ar": "تفعيل ضريبة القيمة المضافة",
        "group_ar": "الضريبة",
    },
    "tax.default_rate": {
        "value": "14.000",
        "dtype": "decimal",
        "label_ar": "نسبة ضريبة القيمة المضافة الافتراضية (%)",
        "group_ar": "الضريبة",
    },
    "tax.calculation_mode": {
        "value": "per_invoice",  # per_invoice | per_line
        "dtype": "str",
        "label_ar": "طريقة حساب الضريبة",
        "group_ar": "الضريبة",
    },
    "tax.prices_include_vat": {
        "value": "false",
        "dtype": "bool",
        "label_ar": "الأسعار المعروضة شاملة الضريبة",
        "group_ar": "الضريبة",
    },
    # --- السنة المالية والترقيم ---
    "fiscal.year_start_month": {
        "value": "1",
        "dtype": "int",
        "label_ar": "شهر بداية السنة المالية (1-12)",
        "group_ar": "السنة المالية",
    },
    "numbering.reset_annually": {
        "value": "true",
        "dtype": "bool",
        "label_ar": "تصفير ترقيم المستندات مع كل سنة جديدة",
        "group_ar": "السنة المالية",
    },
    # --- المرتجعات ---
    "returns.window_days": {
        "value": "14",
        "dtype": "int",
        "label_ar": "المدة المسموح فيها بالمرتجع (بالأيام)",
        "group_ar": "سياسة المرتجعات",
    },
    "returns.restocking_fee_percent": {
        "value": "0.000",
        "dtype": "decimal",
        "label_ar": "نسبة رسوم إعادة التخزين (%)",
        "group_ar": "سياسة المرتجعات",
    },
    "returns.allow_partial": {
        "value": "true",
        "dtype": "bool",
        "label_ar": "السماح بالاسترجاع الجزئي (سطر/كمية)",
        "group_ar": "سياسة المرتجعات",
    },
    # --- المنتجات ---
    "products.autogenerate_barcode": {
        "value": "true",
        "dtype": "bool",
        "label_ar": "توليد الباركود تلقائيًا (EAN-13)",
        "group_ar": "المنتجات",
    },
    "products.low_stock_default": {
        "value": "5",
        "dtype": "int",
        "label_ar": "الحد الأدنى الافتراضي للمخزون قبل التنبيه",
        "group_ar": "المنتجات",
    },
    # --- المتجر الإلكتروني ---
    "storefront.enabled": {
        "value": "true",
        "dtype": "bool",
        "label_ar": "تفعيل المتجر الإلكتروني",
        "group_ar": "المتجر الإلكتروني",
    },
    "storefront.announcement": {
        "value": "شحن مجاني للطلبات فوق 500 جنيه · جودة عالية بأسعار الجملة",
        "dtype": "str",
        "label_ar": "شريط العروض العلوي",
        "group_ar": "المتجر الإلكتروني",
    },
    "storefront.hero_title": {
        "value": "أحدث مجموعة سيراميك وأدوات منزلية",
        "dtype": "str",
        "label_ar": "عنوان الـ Hero الرئيسي",
        "group_ar": "المتجر الإلكتروني",
    },
    "storefront.hero_subtitle": {
        "value": "اكتشف تشكيلة واسعة بأسعار حصرية — جودة تدوم وأسعار تناسب",
        "dtype": "str",
        "label_ar": "العنوان الفرعي",
        "group_ar": "المتجر الإلكتروني",
    },
    "storefront.hero_cta": {
        "value": "تسوق الآن",
        "dtype": "str",
        "label_ar": "نص زر التسوق الرئيسي",
        "group_ar": "المتجر الإلكتروني",
    },
    "storefront.shipping_fee": {
        "value": "40.000",
        "dtype": "decimal",
        "label_ar": "قيمة الشحن الافتراضية (بالجنيه)",
        "group_ar": "المتجر الإلكتروني",
    },
    "storefront.free_shipping_threshold": {
        "value": "500.000",
        "dtype": "decimal",
        "label_ar": "الحد الأدنى للشحن المجاني",
        "group_ar": "المتجر الإلكتروني",
    },
    # --- Ticket 3 Epic 8 — بيانات التحويل (كلها اختيارية، تظهر في الفاتورة الآجلة) ---
    "payment.bank_name": {
        "value": "", "dtype": "str",
        "label_ar": "اسم البنك", "group_ar": "بيانات الدفع",
    },
    "payment.bank_account_number": {
        "value": "", "dtype": "str",
        "label_ar": "رقم الحساب البنكي", "group_ar": "بيانات الدفع",
    },
    "payment.bank_iban": {
        "value": "", "dtype": "str",
        "label_ar": "IBAN", "group_ar": "بيانات الدفع",
    },
    "payment.instapay_handle": {
        "value": "", "dtype": "str",
        "label_ar": "هاندل InstaPay", "group_ar": "بيانات الدفع",
    },
    "payment.ewallet_number": {
        "value": "", "dtype": "str",
        "label_ar": "رقم المحفظة الإلكترونية", "group_ar": "بيانات الدفع",
    },
    "payment.ewallet_provider": {
        "value": "", "dtype": "str",
        "label_ar": "مزود المحفظة (فودافون كاش، إتصالات، إلخ)",
        "group_ar": "بيانات الدفع",
    },
    # --- Ticket 3 Epic 9 — تذكيرات الأقساط ---
    "installments.reminders_enabled": {
        "value": "false", "dtype": "bool",
        "label_ar": "تفعيل تذكيرات الأقساط اليومية",
        "group_ar": "الأقساط",
    },
    "installments.reminder_days_before": {
        "value": "3", "dtype": "int",
        "label_ar": "التذكير قبل استحقاق القسط بـ (أيام)",
        "group_ar": "الأقساط",
    },
    # --- Ticket 3 Epic 10 — نقاط الولاء ---
    "loyalty.enabled": {
        "value": "false", "dtype": "bool",
        "label_ar": "تفعيل نظام نقاط الولاء",
        "group_ar": "نقاط الولاء",
    },
    "loyalty.points_per_currency_unit": {
        "value": "1", "dtype": "decimal",
        "label_ar": "عدد النقاط لكل جنيه (يُكتسب عند البيع)",
        "group_ar": "نقاط الولاء",
    },
    "loyalty.redemption_value": {
        "value": "0.010", "dtype": "decimal",
        "label_ar": "قيمة النقطة الواحدة عند الاستبدال (بالجنيه)",
        "group_ar": "نقاط الولاء",
    },
    # --- Ticket 3 Epic 11 — طريقة تقييم المخزون ---
    "inventory.costing_method": {
        "value": "weighted_average", "dtype": "str",
        "label_ar": "طريقة تقييم المخزون (weighted_average / fifo)",
        "group_ar": "المخزون",
    },
    # --- Ticket 4 Epic 1 — الإيميلات ---
    "email.enabled": {
        "value": "false", "dtype": "bool",
        "label_ar": "تفعيل إرسال الإيميلات",
        "group_ar": "الإشعارات (إيميل)",
    },
    "email.admin_notification_address": {
        "value": "", "dtype": "str",
        "label_ar": "إيميل الأدمن (يوصله تنبيه الطلبات الجديدة)",
        "group_ar": "الإشعارات (إيميل)",
    },
    "email.from_address": {
        "value": "", "dtype": "str",
        "label_ar": "الإيميل المُرسل منه (from)",
        "group_ar": "الإشعارات (إيميل)",
    },
    "email.api_key": {
        "value": "", "dtype": "str",
        "label_ar": "مفتاح API لخدمة الإرسال (Resend، إلخ)",
        "group_ar": "الإشعارات (إيميل)",
    },
}


def _cast(value: str | None, dtype: str):
    if value is None or value == "":
        if dtype == "bool":
            return False
        if dtype == "int":
            return 0
        if dtype == "decimal":
            return Decimal("0")
        return ""
    if dtype == "bool":
        return str(value).strip().lower() in ("true", "1", "yes", "on")
    if dtype == "int":
        return int(value)
    if dtype == "decimal":
        return Decimal(str(value))
    return value


def get_setting(key: str, default=None):
    """قراءة إعداد وتحويله إلى نوعه الصحيح."""
    row: Setting | None = db.session.query(Setting).filter_by(key=key).one_or_none()
    if row is None:
        if key in SETTING_DEFAULTS:
            spec = SETTING_DEFAULTS[key]
            return _cast(spec["value"], spec["dtype"])
        return default
    return _cast(row.value, row.dtype)


def set_setting(key: str, value) -> None:
    """كتابة إعداد. لو الإعداد جديد نأخذ dtype وlabel من DEFAULTS إن وُجد."""
    row: Setting | None = db.session.query(Setting).filter_by(key=key).one_or_none()
    if row is None:
        spec = SETTING_DEFAULTS.get(key, {"dtype": "str", "label_ar": key, "group_ar": "عام"})
        row = Setting(
            key=key,
            dtype=spec.get("dtype", "str"),
            label_ar=spec.get("label_ar"),
            group_ar=spec.get("group_ar"),
        )
        db.session.add(row)
    row.value = "" if value is None else str(value)
