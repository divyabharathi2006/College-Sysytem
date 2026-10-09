from flask_login import LoginManager
from flask_limiter import Limiter
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

from security import rate_limit_ip_key

# Keep Flask extensions independent of the app module so `python app.py` and
# `from app import create_app` always use the same SQLAlchemy instance.
db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()
limiter = Limiter(key_func=rate_limit_ip_key, default_limits=[], headers_enabled=True)
