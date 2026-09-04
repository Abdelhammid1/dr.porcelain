from flask import redirect, render_template, url_for
from flask_login import current_user, login_required

from app.blueprints.main import main_bp
from app.services.dashboard import get_dashboard_data


@main_bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return redirect(url_for("auth.login"))


@main_bp.route("/dashboard")
@login_required
def dashboard():
    data = get_dashboard_data()
    return render_template("main/dashboard.html", data=data)
