from flask import Blueprint

storefront_bp = Blueprint("storefront", __name__, url_prefix="/shop")

from app.blueprints.storefront import routes  # noqa: E402, F401
