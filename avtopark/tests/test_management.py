"""CRUD through the interface for drivers, insurance, activity types, plans, users; and the CLI."""
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from avtopark.models import (
    Driver,
    InsurancePolicy,
    MaintenancePlan,
    MaintenanceType,
    OdometerReading,
    Trip,
    User,
    Vehicle,
)
from avtopark.seed import TACHOGRAPH

from .conftest import PASSWORD, login

TODAY = date.today()


def driver_form(**overrides):
    data = {
        "first_name": "Георги", "last_name": "Петров", "phone": "0888 412 365", "email": "g.petrov@example.com",
        "license_number": "284716359", "license_categories": "b, c", "license_expiry": "2030-05-01",
        "active": "y", "notes": "",
    }
    data.update(overrides)
    return data


def test_driver_create_edit_and_duplicate_licence(admin_client, db):
    assert admin_client.post("/drivers/new", data=driver_form()).status_code == 302
    driver = db.session.scalar(select(Driver))
    assert driver.license_categories == "B, C"
    response = admin_client.post("/drivers/new", data=driver_form(first_name="Друг"))
    assert "Вече има шофьор с този номер" in response.get_data(as_text=True)
    response = admin_client.post("/drivers/new", data=driver_form(license_number="1", email="no-at-sign"))
    text = response.get_data(as_text=True)
    assert "Невалиден имейл адрес" in text and "от 5 до 20" in text
    admin_client.post(f"/drivers/{driver.id}/edit", data=driver_form(phone="0899 000 000"))
    assert db.session.get(Driver, driver.id).phone == "0899 000 000"
    assert admin_client.get(f"/drivers/{driver.id}").status_code == 200


def test_driver_without_history_can_be_deleted(admin_client, db, make_driver):
    driver = make_driver()
    admin_client.post(f"/drivers/{driver.id}/delete")
    assert db.session.scalar(select(func.count(Driver.id))) == 0


def test_insurance_policy_create_and_duplicate(admin_client, db, make_vehicle):
    vehicle = make_vehicle()
    data = {
        "vehicle_id": str(vehicle.id), "policy_type": "civil_liability", "insurer": "ДЗИ",
        "policy_number": "BG/26/123", "start_date": TODAY.isoformat(),
        "end_date": (TODAY + timedelta(days=364)).isoformat(), "premium": "265,40", "notes": "",
    }
    assert admin_client.post("/insurance/new", data=data).status_code == 302
    assert db.session.scalar(select(InsurancePolicy)).premium == Decimal("265.40")
    response = admin_client.post("/insurance/new", data=data)
    assert "Вече има полица с този номер" in response.get_data(as_text=True)
    response = admin_client.post("/insurance/new", data=dict(data, policy_number="X", end_date=TODAY.isoformat()))
    assert "Крайната дата трябва да е след началната" in response.get_data(as_text=True)


def test_maintenance_type_crud(admin_client, db):
    response = admin_client.post("/maintenance/types/new", data={
        "name": "Смяна на антифриз", "description": "", "default_interval_km": "",
        "default_interval_days": "730", "active": "y",
    })
    assert response.status_code == 302
    created = db.session.scalar(select(MaintenanceType).where(MaintenanceType.name == "Смяна на антифриз"))
    assert created.default_interval_days == 730 and created.is_periodic
    response = admin_client.post("/maintenance/types/new", data={"name": "Смяна на антифриз", "active": "y"})
    assert "Вече има дейност с това име" in response.get_data(as_text=True)
    admin_client.post(f"/maintenance/types/{created.id}/delete")
    assert db.session.get(MaintenanceType, created.id) is None


def test_type_in_use_cannot_be_deleted(admin_client, db, make_vehicle):
    vehicle = make_vehicle()
    used = vehicle.plans[0].maintenance_type
    admin_client.post(f"/maintenance/types/{used.id}/delete")
    assert db.session.get(MaintenanceType, used.id) is not None


def test_plan_add_duplicate_and_remove(admin_client, db, make_vehicle):
    vehicle = make_vehicle()
    tacho = db.session.scalar(select(MaintenanceType).where(MaintenanceType.name == TACHOGRAPH))
    form = {"maintenance_type_id": str(tacho.id), "interval_km": "", "interval_days": "730",
            "baseline_date": (TODAY - timedelta(days=100)).isoformat(), "baseline_mileage": "9000", "active": "y"}
    assert admin_client.post(f"/maintenance/plans/new/{vehicle.id}", data=form).status_code == 302
    response = admin_client.post(f"/maintenance/plans/new/{vehicle.id}", data=form)
    assert "вече е включена в плана" in response.get_data(as_text=True)
    response = admin_client.post(f"/maintenance/plans/new/{vehicle.id}", data=dict(form, interval_days=""))
    assert "Въведете интервал" in response.get_data(as_text=True)
    plan = db.session.scalar(select(MaintenancePlan).where(MaintenancePlan.maintenance_type_id == tacho.id))
    admin_client.post(f"/maintenance/plans/{plan.id}/delete")
    assert db.session.get(MaintenancePlan, plan.id) is None


def test_vehicle_edit_status_and_reading(admin_client, db, make_vehicle):
    vehicle = make_vehicle(mileage=10_000)
    response = admin_client.post(f"/vehicles/{vehicle.id}/edit", data={
        "reg_number": "СВ 1234 АВ", "make": "Škoda", "model": "Superb", "year": "2021", "vehicle_type": "car",
        "fuel_type": "diesel", "vin": "", "in_service_since": vehicle.in_service_since.isoformat(),
        "initial_mileage": "10000", "responsible_driver_id": "0", "notes": "Служебен",
    })
    assert response.status_code == 302 and db.session.get(Vehicle, vehicle.id).model == "Superb"
    admin_client.post(f"/vehicles/{vehicle.id}/status", data={"st-status": "in_repair", "st-note": "Спукана гума"})
    assert db.session.get(Vehicle, vehicle.id).status == "in_repair"
    admin_client.post(f"/vehicles/{vehicle.id}/readings", data={"odo-read_on": TODAY.isoformat(), "odo-mileage": "10450"})
    assert db.session.get(Vehicle, vehicle.id).current_mileage == 10_450
    reading = db.session.scalar(select(OdometerReading))
    admin_client.post(f"/vehicles/readings/{reading.id}/delete")
    assert db.session.get(Vehicle, vehicle.id).current_mileage == 10_000


def test_start_trip_form_only_offers_available_vehicles(admin_client, db, make_vehicle, make_driver):
    active = make_vehicle(mileage=20_000)
    broken = make_vehicle(reg="В 2222 ВВ", status="in_repair")
    driver = make_driver()
    page = admin_client.get("/trips/new").get_data(as_text=True)
    assert active.reg_number in page and broken.reg_number not in page
    response = admin_client.post("/trips/new", data={
        "vehicle_id": str(active.id), "driver_id": str(driver.id),
        "started_at": (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
        "start_mileage": "20000", "purpose": "Доставка София – Пловдив", "notes": "",
    })
    assert response.status_code == 302
    assert db.session.scalar(select(Trip)).is_open


def test_admin_manages_users(admin_client, db):
    response = admin_client.post("/admin/users/new", data={
        "username": "ivan.m", "full_name": "Иван Механиков", "role": "mechanic", "active": "y", "password": "weak",
    })
    assert "поне 8 символа" in response.get_data(as_text=True)
    admin_client.post("/admin/users/new", data={
        "username": "ivan.m", "full_name": "Иван Механиков", "role": "mechanic", "active": "y",
        "password": "Mehanik2026",
    })
    user = db.session.scalar(select(User).where(User.username == "ivan.m"))
    assert user.role == "mechanic" and user.check_password("Mehanik2026")
    admin = db.session.scalar(select(User).where(User.username == "admin"))
    response = admin_client.post(f"/admin/users/{admin.id}/edit", data={
        "username": "admin", "full_name": "Admin", "role": "manager", "active": "y", "password": "",
    })
    assert "Не можете да деактивирате собствения си профил" in response.get_data(as_text=True)


def test_change_own_password(client, db):
    login(client, "manager")
    response = client.post("/password", data={"current_password": "wrong", "new_password": "Novaparola1",
                                             "confirm": "Novaparola1"})
    assert "Грешна текуща парола" in response.get_data(as_text=True)
    client.post("/password", data={"current_password": PASSWORD, "new_password": "Novaparola1",
                                   "confirm": "Novaparola1"})
    assert db.session.scalar(select(User).where(User.username == "manager")).check_password("Novaparola1")


def test_cli_commands(app, make_vehicle, monkeypatch):
    runner = app.test_cli_runner()
    make_vehicle()
    result = runner.invoke(args=["check-reminders"])
    assert "Гражданска отговорност" in result.output and result.exit_code == 1  # overdue -> exit code 1
    result = runner.invoke(args=["backup", "--keep", "3"])
    assert "Резервно копие" in result.output
    result = runner.invoke(args=["create-user", "nova", "--role", "logistician"], input="Logistika1\nLogistika1\n")
    assert result.exit_code == 0, result.output
    monkeypatch.setenv("ADMIN_PASSWORD", "Admin12345")
    assert runner.invoke(args=["init-db"]).exit_code == 0
