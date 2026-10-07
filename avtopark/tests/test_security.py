"""Security: authentication, lockout, CSRF, XSS, SQL injection, headers, exports, backups."""
import re
import sqlite3
from datetime import date

import pytest
from sqlalchemy import select

from avtopark import create_app
from avtopark.config import TestConfig
from avtopark.extensions import db as _db
from avtopark.models import User, Vehicle
from avtopark.permissions import Role
from avtopark.seed import create_demo_data
from avtopark.services.backup import create_backup, restore_backup
from avtopark.services.errors import BusinessRuleError
from avtopark.services.exports import _csv_cell, _ics_fold
from avtopark.services.odometer import vehicle_readings
from avtopark.services.reminders import OVERDUE, collect_reminders

from .conftest import PASSWORD, login


def test_pages_require_login(client):
    response = client.get("/vehicles/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_health_is_public(client):
    assert client.get("/health").get_json() == {"status": "ok", "database": "ok"}


def test_wrong_password_and_unknown_user_get_the_same_message(client):
    first = login(client, "admin", "wrong-pass1").get_data(as_text=True)
    second = login(client, "nobody", "wrong-pass1").get_data(as_text=True)
    message = "Грешно потребителско име или парола."
    assert message in first and message in second


def test_account_is_locked_after_five_failed_attempts(client, db):
    for _ in range(5):
        login(client, "manager", "wrong-pass1")
    response = login(client, "manager", PASSWORD)
    assert "временно заключен" in response.get_data(as_text=True)
    assert db.session.scalar(select(User).where(User.username == "manager")).locked_until is not None


def test_inactive_user_cannot_log_in(client, db):
    user = db.session.scalar(select(User).where(User.username == "logistic"))
    user.active = False
    db.session.commit()
    response = login(client, "logistic")
    assert response.status_code == 200  # stays on the login page


def test_open_redirect_after_login_is_blocked(client):
    response = login(client, "admin", query_string={"next": "https://evil.example/phish"})
    assert response.headers["Location"] == "/"
    client.post("/logout")
    response = login(client, "admin", query_string={"next": "/fuel/"})
    assert response.headers["Location"] == "/fuel/"


def test_logout_requires_post(admin_client):
    assert admin_client.get("/logout").status_code == 405
    assert admin_client.post("/logout").status_code == 302
    assert admin_client.get("/").status_code == 302  # logged out


def test_passwords_are_hashed(db):
    user = db.session.scalar(select(User).where(User.username == "admin"))
    assert PASSWORD not in user.password_hash
    assert user.password_hash.startswith(("scrypt:", "pbkdf2:"))


def test_csrf_token_is_required(tmp_path):
    app = create_app(TestConfig, WTF_CSRF_ENABLED=True, SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'c.db'}")
    with app.app_context():
        _db.create_all()
        user = User(username="admin", full_name="Admin", role=Role.ADMIN)
        user.set_password(PASSWORD)
        _db.session.add(user)
        _db.session.commit()
        client = app.test_client()
        assert login(client).status_code == 400  # no token -> rejected
        page = client.get("/login").get_data(as_text=True)
        token = re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', page).group(1)
        response = client.post("/login", data={"username": "admin", "password": PASSWORD, "csrf_token": token})
        assert response.status_code == 302
        _db.session.remove()
        _db.drop_all()
        _db.engine.dispose()


def test_security_headers(client):
    headers = client.get("/login").headers
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["X-Content-Type-Options"] == "nosniff"


def test_user_input_is_escaped(admin_client, db, make_vehicle):
    vehicle = make_vehicle()
    vehicle.notes = '<script>alert("xss")</script>'
    db.session.commit()
    page = admin_client.get(f"/vehicles/{vehicle.id}").get_data(as_text=True)
    assert '<script>alert("xss")</script>' not in page
    assert "&lt;script&gt;" in page


def test_sql_injection_in_search_is_harmless(admin_client, make_vehicle):
    make_vehicle()
    response = admin_client.get("/vehicles/", query_string={"q": "' OR 1=1; DROP TABLE vehicles; --"})
    assert response.status_code == 200
    assert "Няма МПС, които отговарят" in response.get_data(as_text=True)
    assert admin_client.get("/vehicles/").status_code == 200  # table still exists


def test_csv_formula_injection_is_neutralised():
    assert _csv_cell("=HYPERLINK(\"http://x\")") == "'=HYPERLINK(\"http://x\")"
    assert _csv_cell("+359888") == "'+359888"
    assert _csv_cell(-5) == "-5"  # numbers are not changed


def test_ics_folding_keeps_utf8_characters_whole():
    line = "SUMMARY:" + "Годишен технически преглед – СА 1952 РВ" * 3
    folded = _ics_fold(line)
    parts = folded.split("\r\n")
    assert all(len(part.encode()) <= 75 for part in parts)
    assert "".join(part[1:] if i else part for i, part in enumerate(parts)) == line


def test_backup_and_rotation(app, make_vehicle):
    make_vehicle()
    paths = [create_backup(keep=2) for _ in range(3)]
    remaining = sorted(paths[0].parent.glob("avtopark-*.db"))
    assert len(remaining) == 2 and paths[-1] in remaining
    connection = sqlite3.connect(paths[-1])
    assert connection.execute("SELECT reg_number FROM vehicles").fetchone()[0] == "СВ 1234 АВ"
    connection.close()


def test_restore_rejects_files_that_are_not_backups(app, tmp_path):
    bogus = tmp_path / "bogus.db"
    bogus.write_text("this is not a database")
    with pytest.raises(BusinessRuleError):
        restore_backup(bogus)


def test_demo_data_is_consistent(app, db):
    create_demo_data(date.today())
    for vehicle in db.session.scalars(select(Vehicle)):
        readings = vehicle_readings(vehicle)
        for earlier, later in zip(readings, readings[1:]):
            assert not (later.on > earlier.on and later.mileage < earlier.mileage), vehicle.reg_number
    levels = [r.level for r in collect_reminders()]
    assert levels.count(OVERDUE) >= 3
