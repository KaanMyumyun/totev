"""Trips, cost reports and data integrity at database level."""
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from avtopark.models import (
    FuelRecord,
    InsurancePolicy,
    MaintenanceRecord,
    MaintenanceType,
    Trip,
    Vehicle,
    VehicleStatus,
)
from avtopark.services.costs import costs_by_vehicle, month_range, monthly_costs, totals
from avtopark.services.errors import BusinessRuleError
from avtopark.services.records import save_fuel_record, save_maintenance_record
from avtopark.services.trips import finish_trip, start_trip
from avtopark.services.vehicles import change_status, delete_vehicle

TODAY = date.today()
NOW = datetime.now().replace(second=0, microsecond=0)


@pytest.fixture
def trip_setup(db, make_vehicle, make_driver):
    vehicle = make_vehicle(mileage=50_000, since=TODAY - timedelta(days=60))
    driver = make_driver()
    return vehicle, driver


def _start(vehicle, driver, started_at=None):
    return start_trip(vehicle=vehicle, driver=driver, started_at=started_at or NOW - timedelta(hours=8),
                      start_mileage=vehicle.current_mileage, purpose="Доставка")


# --- Trips -----------------------------------------------------------------------

def test_vehicle_in_repair_cannot_be_handed_out(db, trip_setup):
    vehicle, driver = trip_setup
    vehicle.status = VehicleStatus.IN_REPAIR
    with pytest.raises(BusinessRuleError) as error:
        _start(vehicle, driver)
    assert error.value.field == "vehicle_id"


def test_vehicle_cannot_be_on_two_trips(db, trip_setup, make_driver):
    vehicle, driver = trip_setup
    _start(vehicle, driver)
    db.session.commit()
    with pytest.raises(BusinessRuleError) as error:
        _start(vehicle, make_driver(first="Петър"))
    assert "вече е в курс" in error.value.message


def test_driver_cannot_drive_two_vehicles(db, trip_setup, make_vehicle):
    vehicle, driver = trip_setup
    _start(vehicle, driver)
    db.session.commit()
    other = make_vehicle(reg="В 1111 АА", since=TODAY - timedelta(days=60))
    with pytest.raises(BusinessRuleError) as error:
        _start(other, driver)
    assert error.value.field == "driver_id"


def test_expired_licence_blocks_the_trip(db, trip_setup, make_driver):
    vehicle, _ = trip_setup
    driver = make_driver(expiry=TODAY - timedelta(days=1))
    with pytest.raises(BusinessRuleError) as error:
        _start(vehicle, driver)
    assert "изтекла" in error.value.message


def test_return_updates_mileage_adds_fuel_and_reports_problem(db, trip_setup):
    vehicle, driver = trip_setup
    trip = _start(vehicle, driver)
    db.session.commit()
    finish_trip(trip, ended_at=NOW, end_mileage=50_420, fuel_quantity=Decimal("35.5"),
                fuel_price=Decimal("1.459"), problem="Свети лампа за двигателя")
    db.session.commit()
    assert trip.distance == 420
    assert vehicle.current_mileage == 50_420
    fuel = db.session.scalar(select(FuelRecord).where(FuelRecord.vehicle_id == vehicle.id))
    assert fuel.total_cost == Decimal("51.79") and fuel.driver_id == driver.id
    assert vehicle.status == VehicleStatus.IN_REPAIR
    assert "Свети лампа" in vehicle.status_note


def test_return_with_lower_mileage_is_rejected(db, trip_setup):
    vehicle, driver = trip_setup
    trip = _start(vehicle, driver)
    with pytest.raises(BusinessRuleError) as error:
        finish_trip(trip, ended_at=NOW, end_mileage=49_999)
    assert error.value.field == "end_mileage"


def test_return_before_start_is_rejected(db, trip_setup):
    vehicle, driver = trip_setup
    trip = _start(vehicle, driver)
    with pytest.raises(BusinessRuleError) as error:
        finish_trip(trip, ended_at=trip.started_at - timedelta(minutes=1), end_mileage=50_100)
    assert error.value.field == "ended_at"


def test_status_cannot_change_while_on_trip(db, trip_setup):
    vehicle, driver = trip_setup
    _start(vehicle, driver)
    db.session.commit()
    with pytest.raises(BusinessRuleError):
        change_status(vehicle, VehicleStatus.OUT_OF_SERVICE, "Повреда")


def test_database_allows_only_one_open_trip_per_vehicle(db, trip_setup, make_driver):
    vehicle, driver = trip_setup
    other = make_driver(first="Петър")
    db.session.add_all([
        Trip(vehicle=vehicle, driver=driver, started_at=NOW, start_mileage=50_000, purpose="a"),
        Trip(vehicle=vehicle, driver=other, started_at=NOW, start_mileage=50_000, purpose="b"),
    ])
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


# --- Costs -----------------------------------------------------------------------

def test_costs_per_vehicle_and_period(db, make_vehicle):
    vehicle = make_vehicle(mileage=10_000, since=TODAY - timedelta(days=200))
    repair = db.session.scalar(select(MaintenanceType).where(MaintenanceType.name == "Ремонт"))
    save_maintenance_record(None, vehicle=vehicle, maintenance_type=repair, performed_on=TODAY - timedelta(days=20),
                            mileage=12_000, description="Акумулатор", cost=Decimal("180.50"))
    save_maintenance_record(None, vehicle=vehicle, maintenance_type=repair, performed_on=TODAY - timedelta(days=150),
                            mileage=11_000, description="Извън периода", cost=Decimal("999"))
    save_fuel_record(None, vehicle=vehicle, driver=None, filled_on=TODAY - timedelta(days=10), mileage=12_500,
                     quantity=Decimal("40"), price_per_unit=Decimal("1.5"))
    db.session.add(InsurancePolicy(vehicle=vehicle, policy_type="civil_liability", insurer="ДЗИ", policy_number="7",
                                   start_date=TODAY - timedelta(days=5), end_date=TODAY + timedelta(days=360),
                                   premium=Decimal("260")))
    db.session.commit()

    rows = costs_by_vehicle(TODAY - timedelta(days=30), TODAY)
    assert len(rows) == 1
    row = rows[0]
    assert (row.fuel, row.maintenance, row.insurance) == (Decimal("60.00"), Decimal("180.50"), Decimal("260.00"))
    assert row.total == Decimal("500.50")
    assert row.distance == 1_500  # 11 000 before the period -> 12 500
    assert totals(rows)["total"] == Decimal("500.50")
    assert sum(m.total for m in monthly_costs(TODAY - timedelta(days=30), TODAY)) == Decimal("500.50")


def test_month_range_crosses_the_year():
    assert month_range(date(2025, 11, 15), date(2026, 2, 3)) == [
        date(2025, 11, 1), date(2025, 12, 1), date(2026, 1, 1), date(2026, 2, 1)
    ]


# --- Integrity -----------------------------------------------------------------------

def test_record_for_missing_vehicle_is_rejected_by_the_database(db):
    repair = db.session.scalar(select(MaintenanceType))
    db.session.add(MaintenanceRecord(vehicle_id=9999, maintenance_type_id=repair.id, performed_on=TODAY,
                                     mileage=1, description="x", cost=Decimal("1")))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_negative_cost_is_rejected_by_the_database(db, make_vehicle):
    vehicle = make_vehicle()
    repair = db.session.scalar(select(MaintenanceType))
    db.session.add(MaintenanceRecord(vehicle=vehicle, maintenance_type=repair, performed_on=TODAY,
                                     mileage=10_000, description="x", cost=Decimal("-5")))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_duplicate_registration_number(db, make_vehicle):
    make_vehicle(reg="СВ 1234 АВ")
    with pytest.raises(BusinessRuleError) as error:
        make_vehicle(reg="cb1234ab")  # the same plate typed with Latin letters
    assert error.value.field == "reg_number"


def test_vehicle_with_history_cannot_be_deleted(db, make_vehicle):
    vehicle = make_vehicle()
    save_fuel_record(None, vehicle=vehicle, driver=None, filled_on=TODAY, mileage=10_200,
                     quantity=Decimal("30"), price_per_unit=Decimal("1.4"))
    db.session.commit()
    with pytest.raises(BusinessRuleError):
        delete_vehicle(vehicle)
    # Even bypassing the check, the database refuses (ON DELETE RESTRICT).
    db.session.delete(vehicle)
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_vehicle_without_history_is_deleted_with_its_plan(db, make_vehicle):
    vehicle = make_vehicle()
    delete_vehicle(vehicle)
    db.session.commit()
    assert db.session.scalar(select(Vehicle)) is None


def test_money_is_stored_exactly(db, make_vehicle):
    vehicle = make_vehicle()
    repair = db.session.scalar(select(MaintenanceType))
    for _ in range(3):
        save_maintenance_record(None, vehicle=vehicle, maintenance_type=repair, performed_on=TODAY,
                                mileage=10_000, description="x", cost=Decimal("0.10"))
    db.session.commit()
    assert costs_by_vehicle(TODAY, TODAY)[0].maintenance == Decimal("0.30")  # not 0.30000000000000004
