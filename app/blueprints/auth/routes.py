from datetime import datetime

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from urllib.parse import urlparse

from app.blueprints.auth import auth_bp
from app.blueprints.auth.forms import LoginForm
from app.extensions import db
from app.models.user import User
from app.services import password_reset as pwd_reset
from app.services.email import send_password_reset


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.query(User).filter_by(username=form.username.data.strip()).first()
        if user is None or not user.check_password(form.password.data):
            flash("بيانات الدخول غير صحيحة.", "danger")
            return render_template("auth/login.html", form=form), 401

        if not user.is_active:
            flash("هذا الحساب موقوف. تواصل مع صاحب المتجر.", "warning")
            return render_template("auth/login.html", form=form), 403

        login_user(user, remember=form.remember.data)
        user.last_login_at = datetime.utcnow()
        db.session.commit()

        # حماية من الـ open redirect
        next_page = request.args.get("next")
        if next_page and urlparse(next_page).netloc == "":
            return redirect(next_page)
        return redirect(url_for("main.dashboard"))

    return render_template("auth/login.html", form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("تم تسجيل الخروج.", "info")
    return redirect(url_for("auth.login"))


# ============ Ticket 4 Epic 5 — نسيان كلمة المرور ============

# رسالة موحّدة تُعرَض في الحالتين (منع user enumeration)
_UNIFIED_MESSAGE = (
    "لو الإيميل ده مسجّل عندنا، هيوصلك لينك استرجاع كلمة المرور خلال دقائق."
)


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip()
        try:
            token = pwd_reset.request_admin_reset(email)
            if token is not None:
                # ابعت الإيميل — فشله لا يوقف الرسالة الموحّدة
                reset_link = url_for("auth.reset_password", token=token.token,
                                     _external=True)
                try:
                    send_password_reset(to_email=email, reset_link=reset_link)
                except Exception:
                    pass
                db.session.commit()
        except Exception:
            db.session.rollback()
        flash(_UNIFIED_MESSAGE, "info")
        return redirect(url_for("auth.login"))
    return render_template("auth/forgot_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    try:
        tok = pwd_reset.validate_token(token)
        if tok.customer_id is not None:
            # هذا token خاص بعميل، ما ينفعش هنا
            raise pwd_reset.TokenError("رابط غير صالح.")
    except pwd_reset.TokenError as e:
        flash(str(e) + " اطلب لينك جديد.", "danger")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        new_password = request.form.get("new_password") or ""
        confirm = request.form.get("confirm") or ""
        if new_password != confirm:
            flash("كلمتا المرور لا تتطابقان.", "danger")
            return render_template("auth/reset_password.html", token=token)
        try:
            pwd_reset.consume_and_set_password(token, new_password)
            db.session.commit()
            flash("تم تحديث كلمة المرور. سجّل دخول الآن.", "success")
            return redirect(url_for("auth.login"))
        except pwd_reset.TokenError as e:
            db.session.rollback()
            flash(str(e), "danger")

    return render_template("auth/reset_password.html", token=token)
