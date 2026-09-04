from flask import Blueprint

purchases_bp = Blueprint("purchases", __name__, url_prefix="/purchases")

from app.blueprints.purchases import routes  # noqa: E402, F401
