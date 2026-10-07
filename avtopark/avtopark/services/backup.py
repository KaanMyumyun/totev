"""Database backups with the SQLite online backup API (safe while the app is running)."""
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import current_app

from ..extensions import db
from .errors import BusinessRuleError


def database_path() -> Path:
    url = db.engine.url
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise BusinessRuleError("Резервни копия се правят само за SQLite база във файл.")
    return Path(url.database)


def backup_dir() -> Path:
    configured = current_app.config.get("BACKUP_DIR")
    path = Path(configured) if configured else Path(current_app.instance_path) / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_backup(keep: int | None = None) -> Path:
    """Copy the database to backups/avtopark-YYYYmmdd-HHMMSS.db and keep the newest `keep` copies."""
    keep = current_app.config["BACKUP_KEEP"] if keep is None else keep
    target = backup_dir() / f"avtopark-{datetime.now():%Y%m%d-%H%M%S-%f}.db"
    source = sqlite3.connect(database_path())
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    backups = sorted(backup_dir().glob("avtopark-*.db"))
    for old in backups[: max(0, len(backups) - keep)]:
        old.unlink()
    return target


def restore_backup(backup_file: Path) -> Path:
    """Replace the database with a backup. The current database is saved first."""
    backup_file = Path(backup_file)
    check = sqlite3.connect(f"file:{backup_file}?mode=ro", uri=True)
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()[0]
        tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.DatabaseError as error:
        raise BusinessRuleError(f"Файлът не е валидна SQLite база: {error}") from error
    finally:
        check.close()
    if result != "ok" or "vehicles" not in tables:
        raise BusinessRuleError("Файлът не е резервно копие на системата или е повреден.")

    db.session.remove()
    db.engine.dispose()
    current = database_path()
    safety_copy = current.with_name(f"{current.stem}-before-restore-{datetime.now():%Y%m%d-%H%M%S}.db")
    if current.exists():
        shutil.copy2(current, safety_copy)
    source = sqlite3.connect(backup_file)
    destination = sqlite3.connect(current)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    return safety_copy
