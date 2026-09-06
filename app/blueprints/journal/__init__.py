from flask import Blueprint

journal_bp = Blueprint("journal", __name__, url_prefix="/journal")

from app.blueprints.journal import routes  # noqa: E402, F401
