from flask import Blueprint

pos_bp = Blueprint("pos", __name__, url_prefix="/pos")

from app.blueprints.pos import routes  # noqa: E402, F401
