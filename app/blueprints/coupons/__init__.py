from flask import Blueprint

coupons_bp = Blueprint("coupons", __name__, url_prefix="/coupons")

from app.blueprints.coupons import routes  # noqa: E402, F401
