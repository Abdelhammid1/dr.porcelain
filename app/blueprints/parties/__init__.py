from flask import Blueprint

parties_bp = Blueprint("parties", __name__, url_prefix="/parties")

from app.blueprints.parties import routes  # noqa: E402, F401
