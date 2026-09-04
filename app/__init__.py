"""App factory — يُنشئ تطبيق Flask وينفّذ كل التهيئة."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from flask import Flask, render_template

from app.config import get_config
from app.extensions import csrf, db, login_manager, migrate


def create_app(config_class=None) -> Flask:
    app = Flask(
        __name__,
        static_folder="static",
        template_folder="templates",
    )
    app.config.from_object(config_class or get_config())

    # التأكد من وجود مجلدات الرفع
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    _register_extensions(app)
    _register_blueprints(app)
    _register_shell_context(app)
    _register_template_filters(app)
    _register_error_handlers(app)
    _register_cli(app)

    return app


def _register_extensions(app: Flask) -> None:
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    # user_loader
    from app.models.user import User

    @login_manager.user_loader
    def load_user(user_id: str):
        return db.session.get(User, int(user_id))


def _register_blueprints(app: Flask) -> None:
    from app.blueprints.auth import auth_bp
    from app.blueprints.main import main_bp
    from app.blueprints.parties import parties_bp
    from app.blueprints.products import products_bp
    from app.blueprints.installments import installments_bp
    from app.blueprints.orders import orders_bp
    from app.blueprints.pos import pos_bp
    from app.blueprints.purchases import purchases_bp
    from app.blueprints.reports import reports_bp
    from app.blueprints.sales import sales_bp
    from app.blueprints.storefront import storefront_bp
    from app.blueprints.vendor_payments import vendor_payments_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(parties_bp)
    app.register_blueprint(products_bp)
    app.register_blueprint(sales_bp)
    app.register_blueprint(purchases_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(pos_bp)
    app.register_blueprint(installments_bp)
    app.register_blueprint(vendor_payments_bp)
    app.register_blueprint(orders_bp)
    app.register_blueprint(storefront_bp)


def _register_shell_context(app: Flask) -> None:
    """`flask shell` يعطينا db وكل النماذج مباشرة."""

    @app.shell_context_processor
    def make_shell_context():
        from app import models  # noqa: F401

        return {
            "db": db,
            "models": models,
        }


def _register_template_filters(app: Flask) -> None:
    """فلاتر Jinja خاصة بالعربية والأرقام."""

    decimals = app.config.get("MONEY_DECIMAL_PLACES", 3)
    symbol = app.config.get("DEFAULT_CURRENCY_SYMBOL", "ج.م")

    @app.template_filter("money")
    def money_filter(value) -> str:
        if value is None:
            return f"0.{'0' * decimals} {symbol}"
        try:
            d = Decimal(str(value))
        except Exception:
            return f"— {symbol}"
        q = Decimal(10) ** -decimals
        d = d.quantize(q)
        # فاصلة آلاف عادية بالإنجليزي (الأرقام في النظام هندية = ٠١٢٣… عبر CSS لاحقًا)
        formatted = f"{d:,.{decimals}f}"
        return f"{formatted} {symbol}"

    @app.template_filter("qty")
    def qty_filter(value) -> str:
        if value is None:
            return "0"
        try:
            d = Decimal(str(value))
        except Exception:
            return "—"
        # إظهار بدون كسور غير ضرورية
        if d == d.to_integral_value():
            return f"{int(d):,}"
        return f"{d.normalize():,f}"

    @app.template_filter("hdate")
    def hijri_or_greg_date(value):
        """للآن نعرض التاريخ الميلادي؛ سنضيف الهجري لاحقًا لو طُلب."""
        if value is None:
            return "—"
        return value.strftime("%Y/%m/%d")


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(404)
    def not_found(_):
        return render_template("errors/404.html"), 404

    @app.errorhandler(403)
    def forbidden(_):
        return render_template("errors/403.html"), 403

    @app.errorhandler(500)
    def server_error(_):
        db.session.rollback()
        return render_template("errors/500.html"), 500


def _register_cli(app: Flask) -> None:
    """أوامر CLI للتهيئة الأولية."""
    from app.cli import register_cli_commands

    register_cli_commands(app)
