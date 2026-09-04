"""تهيئة pytest — يُنشئ تطبيقًا للاختبار وقاعدة بيانات نظيفة لكل جلسة."""
from __future__ import annotations

import os

import pytest

# نُحدّث بيئة الاختبار قبل استيراد التطبيق حتى تُقرأ الإعدادات الصحيحة
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app  # noqa: E402
from app.config import TestingConfig  # noqa: E402
from app.extensions import db as _db  # noqa: E402


@pytest.fixture(scope="session")
def app():
    app = create_app(TestingConfig)
    with app.app_context():
        _db.create_all()
        yield app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def db(app):
    """يعيد db بعد rollback بعد كل اختبار للحفاظ على العزل."""
    yield _db
    _db.session.rollback()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def runner(app):
    return app.test_cli_runner()
