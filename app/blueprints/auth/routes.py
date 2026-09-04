from datetime import datetime

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from urllib.parse import urlparse

from app.blueprints.auth import auth_bp
from app.blueprints.auth.forms import LoginForm
from app.extensions import db
from app.models.user import User


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
