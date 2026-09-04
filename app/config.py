"""إعدادات التطبيق — تُقرأ من متغيرات البيئة أو تأخذ قيمًا افتراضية.

المبادئ:
- SECRET_KEY وDATABASE_URL يجب أن تُضبطا في Production.
- إعدادات المتجر (اسم، عنوان، ضريبة، إلخ) لا تُخزَّن هنا — بل في جدول `settings`
  حتى تختلف من نسخة نشر لأخرى دون تعديل الكود.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Config:
    # --- Flask ---
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    # --- قاعدة البيانات ---
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/mtgar_dev",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

    # --- الجلسات ---
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 12  # 12 ساعة

    # --- WTForms / CSRF ---
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600

    # --- اللغة والمنطقة ---
    LANGUAGE = "ar"
    TIMEZONE = os.environ.get("TIMEZONE", "Africa/Cairo")
    LOCALE = os.environ.get("LOCALE", "ar_EG")

    # --- ثوابت مالية على مستوى النظام (ليست إعدادات متجر) ---
    # عدد الخانات العشرية للمبالغ في قاعدة البيانات وفي العرض.
    MONEY_DECIMAL_PLACES = 3
    # كود العملة الافتراضي (يُعدَّل من settings عند بدء أي نشر جديد).
    DEFAULT_CURRENCY_CODE = "EGP"
    DEFAULT_CURRENCY_SYMBOL = "ج.م"

    # --- ثوابت الأدوار الافتراضية ---
    DEFAULT_ROLES = ("owner", "accountant", "cashier")

    # --- بادئات المستندات ---
    DOC_PREFIXES = {
        "sales_invoice": "INV",
        "sales_return": "SR",
        "purchase_invoice": "PB",
        "purchase_return": "PR",
        "journal_entry": "JE",
        "customer_receipt": "RCV",
        "vendor_payment": "PAY",
        # ورديات POS
        "pos_session": "POS",
        # التقسيط
        "installment_plan": "INST",
        # الطلبات الأونلاين
        "order": "ORD",
        # أكواد الأطراف
        "customer": "C",
        "vendor": "V",
    }

    # ترقيم الأطراف مستمر دائمًا (لا يتصفر سنويًا) — كود العميل هوية أبدية
    PARTY_CODE_ANNUAL_RESET = False

    # --- مسارات الملفات ---
    UPLOAD_FOLDER = BASE_DIR / "app" / "static" / "uploads"
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024  # 8MB لرفع اللوجو والصور


class DevelopmentConfig(Config):
    DEBUG = True
    TEMPLATES_AUTO_RELOAD = True


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/mtgar_test",
    )
    WTF_CSRF_ENABLED = False


class ProductionConfig(Config):
    DEBUG = False


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config() -> type[Config]:
    env = os.environ.get("FLASK_ENV", "development").lower()
    return CONFIG_MAP.get(env, DevelopmentConfig)
