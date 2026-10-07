"""Application configuration. Values can be overridden with environment variables."""
import os
from datetime import timedelta


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default


class Config:
    # Security. SECRET_KEY must be set in production; in development a random
    # key is generated and stored in the instance folder.
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE")
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    LOGIN_MAX_ATTEMPTS = 5
    LOGIN_LOCK_MINUTES = 15

    # Database. Defaults to instance/avtopark.db (see create_app).
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    BACKUP_DIR = os.environ.get("BACKUP_DIR")
    BACKUP_KEEP = _env_int("BACKUP_KEEP", 14)

    # Forms: use the Bulgarian messages that ship with WTForms.
    WTF_I18N_ENABLED = False

    # Business rules
    REMINDER_DAYS_AHEAD = _env_int("REMINDER_DAYS_AHEAD", 30)
    REMINDER_KM_AHEAD = _env_int("REMINDER_KM_AHEAD", 1000)
    MAX_KM_PER_DAY = _env_int("MAX_KM_PER_DAY", 2000)
    ITEMS_PER_PAGE = 25
    CURRENCY = "€"


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret-key"
    WTF_CSRF_ENABLED = False
    SESSION_COOKIE_SECURE = False
