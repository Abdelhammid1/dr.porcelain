from flask import Blueprint

installments_bp = Blueprint("installments", __name__, url_prefix="/installments")

from app.blueprints.installments import routes  # noqa: E402, F401
