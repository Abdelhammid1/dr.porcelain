from flask import Blueprint

accounts_bp = Blueprint("accounts", __name__, url_prefix="/accounts")

from app.blueprints.accounts import routes  # noqa: E402, F401
