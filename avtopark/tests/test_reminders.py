"""Unit and integration tests of the reminder logic ("Провери обслужване")."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from avtopark.models import InsurancePolicy, MaintenancePlan, MaintenanceType, VehicleStatus
from avtopark.seed import INSPECTION, OIL
from avtopark.services.records import save_maintenance_record
from avtopark.services.reminders import (
    DUE_SOON,
    INSURANCE,
    LICENSE,
    MAINTENANCE,
    OK,
    OVERDUE,
    collect_reminders,
    count_by_level,
    evaluate_due,
    evaluate_expiry,
    worst_level_by_vehicle,
)
from sqlalchemy import select

TODAY = date(2026, 10, 7)


def due(**overrides):
    params = dict(
        last_date=TODAY - timedelta(days=100),
        last_km=50_000,
        interval_days=365,
        interval_km=10_000,
        today=TODAY,
        current_km=55_000,
        days_ahead=30,
        km_ahead=1000,
    )
    params.update(overrides)
    return evaluate_due(**params)


def test_far_from_both_limits_is_ok():
    status = due()
    assert status.level == OK
    assert status.km_left == 5000
    assert status.days_left == 265
    assert status.due_km == 60_000
    assert status.due_date == TODAY + timedelta(days=265)


def test_mileage_limit_passed_is_overdue_even_if_date_is_far():
    status = due(current_km=60_469)
    assert status.level == OVERDUE
    assert status.km_left == -469


def test_date_limit_passed_is_overdue_even_if_mileage_is_low():
    status = due(last_date=TODAY - timedelta(days=369), current_km=51_000)
    assert status.level == OVERDUE
    assert status.days_left == -4


@pytest.mark.parametrize(
    "current_km, level",
    [(58_999, OK), (59_000, DUE_SOON), (59_999, DUE_SOON), (60_000, DUE_SOON), (60_001, OVERDUE)],
)
def test_mileage_boundaries(current_km, level):
    # warning window 1000 km: 59 000 is the first "due soon"; reaching 60 000 exactly is not yet overdue
    assert due(current_km=current_km).level == level


@pytest.mark.parametrize("days_ago, level", [(334, OK), (335, DUE_SOON), (365, DUE_SOON), (366, OVERDUE)])
def test_date_boundaries(days_ago, level):
    assert due(last_date=TODAY - timedelta(days=days_ago)).level == level


def test_date_only_interval_ignores_mileage():
    status = due(interval_km=None, current_km=999_999)
    assert status.km_left is None
    assert status.level == OK


def test_expiry():
    assert evaluate_expiry(TODAY + timedelta(days=31), TODAY, 30).level == OK
    assert evaluate_expiry(TODAY + timedelta(days=30), TODAY, 30).level == DUE_SOON
    assert evaluate_expiry(TODAY, TODAY, 30).level == DUE_SOON  # valid until the end of the day
    assert evaluate_expiry(TODAY - timedelta(days=1), TODAY, 30).level == OVERDUE


# --- With the database ------------------------------------------------------------

def _oil_plan(db, vehicle):
    oil = db.session.scalar(select(MaintenanceType).where(MaintenanceType.name == OIL))
    return db.session.scalar(
        select(MaintenancePlan).where(
            MaintenancePlan.vehicle_id == vehicle.id, MaintenancePlan.maintenance_type_id == oil.id
        )
    ), oil


def test_new_vehicle_gets_default_plan(make_vehicle):
    vehicle = make_vehicle()
    names = {plan.maintenance_type.name for plan in vehicle.plans}
    assert {OIL, INSPECTION} <= names
    assert all(plan.baseline_mileage == vehicle.initial_mileage for plan in vehicle.plans)


def test_plan_baseline_is_used_until_a_record_exists(db, make_vehicle):
    vehicle = make_vehicle(mileage=10_000, since=date.today() - timedelta(days=30))
    plan, oil = _oil_plan(db, vehicle)
    vehicle.current_mileage = 19_500  # 500 km before the 10 000 km interval
    db.session.commit()
    reminders = collect_reminders(vehicle=vehicle, kinds=(MAINTENANCE,), include_ok=True)
    oil_reminder = next(r for r in reminders if r.plan.id == plan.id)
    assert oil_reminder.level == DUE_SOON
    assert oil_reminder.status.km_left == 500

    # After the oil change is recorded, the next one is due 10 000 km later.
    save_maintenance_record(
        None, vehicle=vehicle, maintenance_type=oil, performed_on=date.today(), mileage=19_500,
        description="Смяна на масло", cost=Decimal("150"),
    )
    db.session.commit()
    reminders = collect_reminders(vehicle=vehicle, kinds=(MAINTENANCE,), include_ok=True)
    oil_reminder = next(r for r in reminders if r.plan.id == plan.id)
    assert oil_reminder.level == OK
    assert oil_reminder.status.km_left == 10_000


def test_out_of_service_vehicles_are_not_checked(db, make_vehicle):
    vehicle = make_vehicle(status=VehicleStatus.OUT_OF_SERVICE)
    assert collect_reminders(kinds=(MAINTENANCE, INSURANCE)) == []
    assert collect_reminders(kinds=(MAINTENANCE, INSURANCE), include_inactive=True, include_ok=True)


def test_missing_civil_liability_is_overdue(make_vehicle):
    vehicle = make_vehicle()
    reminders = collect_reminders(kinds=(INSURANCE,))
    assert len(reminders) == 1
    assert reminders[0].level == OVERDUE
    assert reminders[0].vehicle.id == vehicle.id


def test_insurance_expiring_soon(db, make_vehicle):
    vehicle = make_vehicle()
    db.session.add(InsurancePolicy(
        vehicle=vehicle, policy_type="civil_liability", insurer="ДЗИ", policy_number="1",
        start_date=date.today() - timedelta(days=350), end_date=date.today() + timedelta(days=14),
        premium=Decimal("250"),
    ))
    db.session.commit()
    reminders = collect_reminders(kinds=(INSURANCE,))
    assert [r.level for r in reminders] == [DUE_SOON]
    assert reminders[0].status.days_left == 14


def test_expired_driving_licence(make_driver):
    make_driver(expiry=date.today() - timedelta(days=9))
    make_driver(first="Мария", expiry=date.today() + timedelta(days=500))
    make_driver(first="Стар", expiry=date.today() - timedelta(days=100), active=False)  # inactive: ignored
    reminders = collect_reminders(kinds=(LICENSE,))
    assert len(reminders) == 1
    assert reminders[0].level == OVERDUE
    assert reminders[0].driver.first_name == "Иван"


def test_sorting_counting_and_worst_level(db, make_vehicle, make_driver):
    vehicle = make_vehicle(since=date.today() - timedelta(days=10))  # plan is fresh, only the policy is missing
    make_driver(expiry=date.today() + timedelta(days=10))
    reminders = collect_reminders()
    assert reminders[0].level == OVERDUE  # overdue first
    counts = count_by_level(reminders)
    assert counts[OVERDUE] == 1 and counts[DUE_SOON] == 1
    assert worst_level_by_vehicle(reminders) == {vehicle.id: OVERDUE}
