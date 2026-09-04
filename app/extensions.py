"""إمتدادات Flask مشتركة — تُهيَّأ داخل create_app()."""
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()

login_manager.login_view = "auth.login"
login_manager.login_message = "من فضلك سجّل الدخول للوصول لهذه الصفحة."
login_manager.login_message_category = "warning"
login_manager.session_protection = "strong"
