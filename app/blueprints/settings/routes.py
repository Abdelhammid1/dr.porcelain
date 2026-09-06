"""Admin routes لإدارة إعدادات النظام (Ticket 3 Epic 2)."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import login_required
from werkzeug.utils import secure_filename

from app.blueprints.settings import settings_bp
from app.extensions import db
from app.models.setting import SETTING_DEFAULTS, Setting, get_setting, set_setting
from app.services.security import require_permission


ALLOWED_LOGO_EXT = {"png", "jpg", "jpeg", "webp", "gif", "svg"}


@settings_bp.route("/", methods=["GET"])
@login_required
@require_permission("settings.manage")
def index():
    # اجمع الإعدادات كلها من DB + إن غاب مفتاح، خذ افتراضيه من SETTING_DEFAULTS
    stored = {s.key: s for s in db.session.query(Setting).all()}
    grouped: dict[str, list[dict]] = {}
    for key, spec in SETTING_DEFAULTS.items():
        row = stored.get(key)
        current_value = row.value if row else spec.get("value", "")
        grouped.setdefault(spec.get("group_ar", "عام"), []).append({
            "key": key,
            "value": current_value,
            "dtype": spec.get("dtype", "str"),
            "label_ar": spec.get("label_ar", key),
        })
    return render_template("settings/index.html", grouped=grouped,
                           current_logo=str(get_setting("store.logo_path", "")))


@settings_bp.route("/save", methods=["POST"])
@login_required
@require_permission("settings.manage")
def save():
    # كل الإعدادات المُقدَّمة تُحفَظ
    for key, spec in SETTING_DEFAULTS.items():
        dtype = spec.get("dtype", "str")
        if dtype == "bool":
            # checkbox — لو مش موجود ف value = False
            value = "true" if key in request.form else "false"
        else:
            value = request.form.get(key)
        if value is not None:
            set_setting(key, value)
    db.session.commit()
    flash("تم حفظ الإعدادات.", "success")
    return redirect(url_for("settings.index"))


@settings_bp.route("/upload-logo", methods=["POST"])
@login_required
@require_permission("settings.manage")
def upload_logo():
    f = request.files.get("logo")
    if not f or not f.filename:
        flash("اختر ملف لوجو.", "warning")
        return redirect(url_for("settings.index"))
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_LOGO_EXT:
        flash(f"صيغة غير مدعومة ({ext}).", "danger")
        return redirect(url_for("settings.index"))

    upload_folder = Path(current_app.config["UPLOAD_FOLDER"]) / "settings"
    upload_folder.mkdir(parents=True, exist_ok=True)
    safe_ext = secure_filename(f"x.{ext}").rsplit(".", 1)[-1] or ext
    name = f"logo-{uuid.uuid4().hex}.{safe_ext}"
    dest = upload_folder / name
    f.save(str(dest))

    # نُخزن مسارًا نسبيًا داخل static/ حتى url_for('static', filename=...) يعمل
    static_root = Path(current_app.static_folder)
    rel = str(dest.relative_to(static_root)).replace(os.sep, "/")
    set_setting("store.logo_path", rel)
    db.session.commit()
    flash("تم رفع اللوجو.", "success")
    return redirect(url_for("settings.index"))
