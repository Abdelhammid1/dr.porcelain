from flask import Blueprint

vendor_payments_bp = Blueprint("vendor_payments", __name__, url_prefix="/vendor-payments")

from app.blueprints.vendor_payments import routes  # noqa: E402, F401
