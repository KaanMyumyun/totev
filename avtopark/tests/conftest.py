"""Shared fixtures: a fresh SQLite database file per test, users for every role and factories."""
import itertools
from datetime import date, timedelta

import pytest

from avtopark import create_app
from avtopark.config import TestConfig
from avtopark.extensions import db as _db
from avtopark.models import Driver, User
from avtopark.permissions import Role
from avtopark.seed import seed_reference_data
from avtopark.services.vehicles import create_vehicle

PASSWORD = "Test1234"
TODAY = date.today()


@pytest.fixture
def app(tmp_path):
    app = create_app(
        TestConfig,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'test.db'}",
        BACKUP_DIR=str(tmp_path / "backups"),
    )
    with app.app_context():
        _db.create_all()
        seed_reference_data()
        for username, role in [
            ("admin", Role.ADMIN),
            ("manager", Role.MANAGER),
            ("mechanic", Role.MECHANIC),
            ("logistic", Role.LOGISTICIAN),
        ]:
            user = User(username=username, full_name=username.title(), role=role)
            user.set_password(PASSWORD)
            _db.session.add(user)
        _db.session.commit()
        yield app
        _db.session.remove()
        _db.drop_all()
        _db.engine.dispose()


@pytest.fixture
def db(app):
    return _db


@pytest.fixture
def client(app):
    return app.test_client()


def login(client, username="admin", password=PASSWORD, **extra):
    return client.post("/login", data={"username": username, "password": password}, **extra)


@pytest.fixture
def admin_client(client):
    login(client, "admin")
    return client


@pytest.fixture
def make_vehicle(db):
    def factory(reg="СВ 1234 АВ", mileage=10_000, since=None, vehicle_type="car", fuel_type="diesel", status=None):
        vehicle = create_vehicle(
            reg_number=reg,
            make="Škoda",
            model="Octavia",
            year=2020,
            vehicle_type=vehicle_type,
            fuel_type=fuel_type,
            in_service_since=since or TODAY - timedelta(days=365),
            initial_mileage=mileage,
        )
        if status:
            vehicle.status = status
        db.session.commit()
        return vehicle

    return factory


@pytest.fixture
def make_driver(db):
    numbers = itertools.count(100_000_001)

    def factory(first="Иван", last="Петров", expiry=None, active=True):
        driver = Driver(
            first_name=first,
            last_name=last,
            license_number=str(next(numbers)),
            license_categories="B, C",
            license_expiry=expiry or TODAY + timedelta(days=365),
            active=active,
        )
        db.session.add(driver)
        db.session.commit()
        return driver

    return factory
