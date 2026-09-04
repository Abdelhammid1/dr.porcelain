from flask import Blueprint

sales_bp = Blueprint("sales", __name__, url_prefix="/sales")

from app.blueprints.sales import routes  # noqa: E402, F401
