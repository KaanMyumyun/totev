"""Fleet management system ("Система за управление на автопарк")."""
import logging
import os
import secrets
from datetime import date
from logging.handlers import RotatingFileHandler

from flask import Flask, render_template, request
from flask_login import current_user
from flask_wtf.csrf import CSRFError

from .config import Config
from .extensions import csrf, db, login_manager
from .services import formatting

__version__ = "1.0.0"

# Pages that can be opened without logging in.
PUBLIC_ENDPOINTS = {"auth.login", "static", "main.health"}


def create_app(config_object=Config, **overrides) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object)
    app.config.update(overrides)
    os.makedirs(app.instance_path, exist_ok=True)

    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        db_path = os.path.join(app.instance_path, "avtopark.db")
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = _load_or_create_secret_key(app.instance_path)

    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Моля, влезте в системата."
    login_manager.login_message_category = "warning"

    _register_blueprints(app)
    _register_request_hooks(app)
    _register_error_handlers(app)
    _register_template_helpers(app)
    _configure_logging(app)

    from .cli import register_cli

    register_cli(app)
    return app


def _load_or_create_secret_key(instance_path: str) -> str:
    """Development fallback: a random key kept in the instance folder (never in the code)."""
    path = os.path.join(instance_path, "secret_key")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    key = secrets.token_hex(32)
    with open(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600), "w", encoding="utf-8") as handle:
        handle.write(key)
    return key


def _register_blueprints(app: Flask) -> None:
    from .views import admin, auth, drivers, fuel, insurance, main, maintenance, reports, trips, vehicles

    app.register_blueprint(auth.bp)
    app.register_blueprint(main.bp)
    app.register_blueprint(vehicles.bp, url_prefix="/vehicles")
    app.register_blueprint(maintenance.bp, url_prefix="/maintenance")
    app.register_blueprint(fuel.bp, url_prefix="/fuel")
    app.register_blueprint(drivers.bp, url_prefix="/drivers")
    app.register_blueprint(trips.bp, url_prefix="/trips")
    app.register_blueprint(insurance.bp, url_prefix="/insurance")
    app.register_blueprint(reports.bp, url_prefix="/reports")
    app.register_blueprint(admin.bp, url_prefix="/admin")


def _register_request_hooks(app: Flask) -> None:
    @login_manager.user_loader
    def load_user(user_id: str):
        from .models import User

        return db.session.get(User, int(user_id))

    @app.before_request
    def require_login():
        # Every page except the public ones needs an authenticated user.
        if request.endpoint is None or request.endpoint in PUBLIC_ENDPOINTS:
            return None
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        return None

    @app.after_request
    def security_headers(response):
        headers = response.headers
        headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
            "frame-ancestors 'none'",
        )
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "same-origin")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.endpoint != "static":
            headers.setdefault("Cache-Control", "no-store")
        return response


def _register_error_handlers(app: Flask) -> None:
    messages = {
        400: ("Невалидна заявка", "Заявката не може да бъде обработена."),
        403: ("Нямате достъп", "Вашата роля не позволява това действие."),
        404: ("Не е намерено", "Търсената страница или запис не съществува."),
        405: ("Непозволен метод", "Това действие не може да се изпълни по този начин."),
        500: ("Вътрешна грешка", "Възникна неочаквана грешка. Опитайте отново или се свържете с администратора."),
    }

    def render_error(code: int):
        title, text = messages[code]
        return render_template("errors/error.html", code=code, title=title, text=text), code

    for code in (400, 403, 404, 405):
        app.register_error_handler(code, lambda error, code=code: render_error(code))

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        return (
            render_template(
                "errors/error.html",
                code=400,
                title="Изтекла сесия",
                text="Формулярът е изтекъл или е изпратен от друг сайт. Презаредете страницата и опитайте отново.",
            ),
            400,
        )

    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        return render_error(500)


def _register_template_helpers(app: Flask) -> None:
    from . import models
    from .permissions import ROLE_LABELS
    from .services import reminders

    app.jinja_env.filters.update(
        number=formatting.format_number,
        money=lambda value: formatting.format_money(value, app.config["CURRENCY"]),
        km=formatting.format_km,
        date_bg=formatting.format_date,
        datetime_bg=formatting.format_datetime,
        days_left=formatting.days_left_text,
        km_left=formatting.km_left_text,
    )

    @app.context_processor
    def inject_globals():
        def can(permission: str) -> bool:
            return current_user.is_authenticated and current_user.can(permission)

        return {
            "can": can,
            "today": date.today(),
            "currency": app.config["CURRENCY"],
            "app_version": __version__,
            "VEHICLE_STATUS_LABELS": models.VEHICLE_STATUS_LABELS,
            "VEHICLE_TYPE_LABELS": models.VEHICLE_TYPE_LABELS,
            "FUEL_TYPE_LABELS": models.FUEL_TYPE_LABELS,
            "POLICY_TYPE_LABELS": models.POLICY_TYPE_LABELS,
            "ROLE_LABELS": ROLE_LABELS,
            "LEVEL_LABELS": reminders.LEVEL_LABELS,
        }


def _configure_logging(app: Flask) -> None:
    if app.debug or app.testing:
        return
    log_dir = os.path.join(app.instance_path, "logs")
    os.makedirs(log_dir, exist_ok=True)
    handler = RotatingFileHandler(
        os.path.join(log_dir, "avtopark.log"), maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.setLevel(logging.INFO)
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
