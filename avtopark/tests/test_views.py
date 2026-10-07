"""Integration tests through the web interface (Flask test client)."""
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from avtopark.models import FuelRecord, MaintenanceRecord, MaintenanceType, Trip, Vehicle
from avtopark.services.records import save_fuel_record
from avtopark.services.trips import start_trip

from .conftest import login

TODAY = date.today()


def vehicle_form(**overrides):
    data = {
        "reg_number": "cb 4821 km",
        "make": "Škoda",
        "model": "Octavia",
        "year": "2020",
        "vehicle_type": "car",
        "fuel_type": "diesel",
        "vin": "",
        "in_service_since": (TODAY - timedelta(days=30)).isoformat(),
        "initial_mileage": "96400",
        "responsible_driver_id": "0",
        "notes": "",
    }
    data.update(overrides)
    return data


def test_create_vehicle_normalises_plate(admin_client, db):
    response = admin_client.post("/vehicles/new", data=vehicle_form())
    assert response.status_code == 302
    vehicle = db.session.scalar(select(Vehicle))
    assert vehicle.reg_number == "СВ 4821 КМ"
    assert vehicle.current_mileage == 96_400
    assert len(vehicle.plans) >= 4  # default periodic activities


def test_create_vehicle_shows_errors(admin_client, db):
    admin_client.post("/vehicles/new", data=vehicle_form())
    response = admin_client.post("/vehicles/new", data=vehicle_form(reg_number="СВ 4821 КМ"))
    assert "Вече има МПС с регистрационен номер" in response.get_data(as_text=True)
    response = admin_client.post("/vehicles/new", data=vehicle_form(reg_number="123", year="1800"))
    text = response.get_data(as_text=True)
    assert "Невалиден регистрационен номер" in text or "Годината трябва" in text
    assert db.session.scalar(select(func.count(Vehicle.id))) == 1


def test_maintenance_for_missing_vehicle_is_not_saved(admin_client, db, make_vehicle):
    make_vehicle()
    mtype = db.session.scalar(select(MaintenanceType))
    response = admin_client.post("/maintenance/new", data={
        "vehicle_id": "9999", "maintenance_type_id": str(mtype.id), "performed_on": TODAY.isoformat(),
        "mileage": "10100", "cost": "120", "description": "Смяна на масло",
    })
    assert response.status_code == 200  # form shown again with an error
    assert db.session.scalar(select(func.count(MaintenanceRecord.id))) == 0


def test_maintenance_with_decreasing_mileage_shows_error(admin_client, db, make_vehicle):
    vehicle = make_vehicle(mileage=10_000)
    mtype = db.session.scalar(select(MaintenanceType))
    response = admin_client.post("/maintenance/new", data={
        "vehicle_id": str(vehicle.id), "maintenance_type_id": str(mtype.id), "performed_on": TODAY.isoformat(),
        "mileage": "9000", "cost": "120,50", "description": "Смяна на масло",
    })
    assert "по-малък" in response.get_data(as_text=True)
    assert db.session.scalar(select(func.count(MaintenanceRecord.id))) == 0


def test_completed_repair_returns_vehicle_to_service(admin_client, db, make_vehicle):
    vehicle = make_vehicle(mileage=10_000, status="in_repair")
    mtype = db.session.scalar(select(MaintenanceType).where(MaintenanceType.name == "Ремонт"))
    response = admin_client.post("/maintenance/new", data={
        "vehicle_id": str(vehicle.id), "maintenance_type_id": str(mtype.id), "performed_on": TODAY.isoformat(),
        "mileage": "10050", "cost": "1 250,40", "description": "Подмяна на съединител",
        "mark_vehicle_active": "y",
    })
    assert response.status_code == 302
    record = db.session.scalar(select(MaintenanceRecord))
    assert record.cost == Decimal("1250.40")  # Bulgarian decimal comma and spaces accepted
    assert db.session.get(Vehicle, vehicle.id).status == "active"


def test_fuel_total_is_calculated(admin_client, db, make_vehicle):
    vehicle = make_vehicle()
    admin_client.post("/fuel/new", data={
        "vehicle_id": str(vehicle.id), "driver_id": "0", "filled_on": TODAY.isoformat(), "mileage": "10300",
        "quantity": "41,37", "price_per_unit": "1,459", "full_tank": "y", "station": "Shell",
    })
    record = db.session.scalar(select(FuelRecord))
    assert record.total_cost == Decimal("60.36")


def test_delete_requires_confirmation_and_post(admin_client, db, make_vehicle):
    vehicle = make_vehicle()
    record = save_fuel_record(None, vehicle=vehicle, driver=None, filled_on=TODAY, mileage=10_100,
                              quantity=Decimal("20"), price_per_unit=Decimal("1.4"))
    db.session.commit()
    page = admin_client.get(f"/fuel/{record.id}/delete")
    assert "Да, изтрий" in page.get_data(as_text=True)
    assert db.session.scalar(select(func.count(FuelRecord.id))) == 1  # GET does not delete
    admin_client.post(f"/fuel/{record.id}/delete")
    assert db.session.scalar(select(func.count(FuelRecord.id))) == 0


def test_vehicle_with_history_is_protected_from_deletion(admin_client, db, make_vehicle):
    vehicle = make_vehicle()
    save_fuel_record(None, vehicle=vehicle, driver=None, filled_on=TODAY, mileage=10_100,
                     quantity=Decimal("20"), price_per_unit=Decimal("1.4"))
    db.session.commit()
    response = admin_client.post(f"/vehicles/{vehicle.id}/delete", follow_redirects=True)
    assert "не може да бъде изтрито" in response.get_data(as_text=True)
    assert db.session.get(Vehicle, vehicle.id) is not None


def test_return_from_trip_form(admin_client, db, make_vehicle, make_driver):
    vehicle = make_vehicle(mileage=50_000, since=TODAY - timedelta(days=30))
    trip = start_trip(vehicle=vehicle, driver=make_driver(), started_at=datetime.now() - timedelta(hours=5),
                      start_mileage=50_000, purpose="Доставка")
    db.session.commit()
    response = admin_client.post(f"/trips/{trip.id}/return", data={
        "ended_at": datetime.now().strftime("%Y-%m-%dT%H:%M"), "end_mileage": "50312",
        "fuel_quantity": "30", "fuel_price": "1,45", "fuel_full_tank": "y", "problem": "",
    })
    assert response.status_code == 302
    trip = db.session.get(Trip, trip.id)
    assert trip.end_mileage == 50_312 and not trip.is_open
    assert db.session.get(Vehicle, vehicle.id).current_mileage == 50_312
    assert db.session.scalar(select(FuelRecord)).total_cost == Decimal("43.50")


def test_return_form_requires_price_when_fuel_is_entered(admin_client, db, make_vehicle, make_driver):
    vehicle = make_vehicle(mileage=50_000, since=TODAY - timedelta(days=30))
    trip = start_trip(vehicle=vehicle, driver=make_driver(), started_at=datetime.now() - timedelta(hours=5),
                      start_mileage=50_000, purpose="Доставка")
    db.session.commit()
    response = admin_client.post(f"/trips/{trip.id}/return", data={
        "ended_at": datetime.now().strftime("%Y-%m-%dT%H:%M"), "end_mileage": "50312", "fuel_quantity": "30",
    })
    assert "Въведете цената" in response.get_data(as_text=True)
    assert db.session.get(Trip, trip.id).is_open


@pytest.mark.parametrize("url", [
    "/", "/check", "/check?all=1", "/vehicles/", "/maintenance/", "/maintenance/types", "/fuel/", "/drivers/",
    "/trips/", "/trips/open", "/insurance/", "/reports/", "/reports/upcoming", "/reports/costs",
    "/reports/inactive", "/reports/consumption", "/reports/usage", "/admin/users", "/admin/audit",
])
def test_pages_open_for_admin(admin_client, make_vehicle, url):
    make_vehicle()
    assert admin_client.get(url).status_code == 200


def test_reports_exports(admin_client, make_vehicle):
    make_vehicle()
    csv = admin_client.get("/reports/costs?export=csv")
    assert csv.mimetype == "text/csv"
    assert csv.get_data(as_text=True).startswith("﻿МПС;")
    ics = admin_client.get("/reports/upcoming?export=ics")
    body = ics.get_data()
    assert ics.mimetype == "text/calendar" and body.startswith(b"BEGIN:VCALENDAR")
    assert all(len(line) <= 75 for line in body.split(b"\r\n"))  # RFC 5545 line length


def test_maintenance_check_is_logged(admin_client):
    admin_client.get("/check")
    audit = admin_client.get("/admin/audit").get_data(as_text=True)
    assert "Проверка на обслужване" in audit


@pytest.mark.parametrize("username, url, status", [
    ("mechanic", "/fuel/new", 403),
    ("mechanic", "/maintenance/new", 200),
    ("mechanic", "/reports/costs", 403),
    ("mechanic", "/vehicles/new", 403),
    ("logistic", "/fuel/new", 200),
    ("logistic", "/trips/new", 200),
    ("logistic", "/maintenance/new", 403),
    ("logistic", "/insurance/new", 403),
    ("manager", "/reports/costs", 200),
    ("manager", "/vehicles/new", 200),
    ("manager", "/admin/users", 403),
    ("admin", "/admin/users", 200),
])
def test_role_permissions(client, username, url, status):
    login(client, username)
    assert client.get(url).status_code == status


def test_mechanic_cannot_delete_records(client, db, make_vehicle):
    vehicle = make_vehicle()
    login(client, "mechanic")
    assert client.post(f"/vehicles/{vehicle.id}/delete").status_code == 403
    assert db.session.get(Vehicle, vehicle.id) is not None
