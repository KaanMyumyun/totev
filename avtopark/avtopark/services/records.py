"""Creating and changing records that carry an odometer reading.

Each function validates the business rules, applies the change and refreshes
the cached mileage of the affected vehicle(s). Committing is left to the caller.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select

from ..extensions import db
from ..models import (
    Driver,
    FuelRecord,
    MaintenancePlan,
    MaintenanceRecord,
    MaintenanceType,
    OdometerReading,
    User,
    Vehicle,
    VehicleStatus,
)
from .errors import BusinessRuleError
from .fuel import compute_total
from .odometer import refresh_vehicle_mileage, validate_reading


def _refresh(*vehicles: Vehicle | None) -> None:
    seen = set()
    for vehicle in vehicles:
        if vehicle is not None and vehicle.id not in seen:
            seen.add(vehicle.id)
            refresh_vehicle_mileage(vehicle)


def save_maintenance_record(
    record: MaintenanceRecord | None,
    *,
    vehicle: Vehicle,
    maintenance_type: MaintenanceType,
    performed_on: date,
    mileage: int,
    description: str,
    cost: Decimal,
    service_provider: str | None = None,
    user: User | None = None,
    mark_vehicle_active: bool = False,
) -> MaintenanceRecord:
    """Create (record=None) or update a maintenance/repair record."""
    if cost is None or cost < 0:
        raise BusinessRuleError("Цената не може да е отрицателна.", "cost")
    exclude = ("maintenance", record.id) if record is not None else None
    validate_reading(
        vehicle, performed_on, mileage, exclude=exclude,
        date_field="performed_on", mileage_field="mileage",
    )
    previous_vehicle = record.vehicle if record is not None else None
    if record is None:
        record = MaintenanceRecord(created_by=user)
        db.session.add(record)
    record.vehicle = vehicle
    record.maintenance_type = maintenance_type
    record.performed_on = performed_on
    record.mileage = mileage
    record.description = description.strip()
    record.cost = cost
    record.service_provider = (service_provider or "").strip() or None

    # Finishing a repair usually means the vehicle can be used again.
    if mark_vehicle_active and vehicle.status == VehicleStatus.IN_REPAIR:
        vehicle.status = VehicleStatus.ACTIVE
        vehicle.status_note = None
        vehicle.status_changed_at = datetime.now()
    _refresh(vehicle, previous_vehicle)
    return record


def save_fuel_record(
    record: FuelRecord | None,
    *,
    vehicle: Vehicle,
    driver: Driver | None,
    filled_on: date,
    mileage: int,
    quantity: Decimal,
    price_per_unit: Decimal,
    full_tank: bool = True,
    station: str | None = None,
    user: User | None = None,
) -> FuelRecord:
    if quantity is None or quantity <= 0:
        raise BusinessRuleError("Количеството трябва да е по-голямо от нула.", "quantity")
    if price_per_unit is None or price_per_unit <= 0:
        raise BusinessRuleError("Единичната цена трябва да е по-голяма от нула.", "price_per_unit")
    exclude = ("fuel", record.id) if record is not None else None
    validate_reading(
        vehicle, filled_on, mileage, exclude=exclude,
        date_field="filled_on", mileage_field="mileage",
    )
    previous_vehicle = record.vehicle if record is not None else None
    if record is None:
        record = FuelRecord(created_by_id=user.id if user else None)
        db.session.add(record)
    record.vehicle = vehicle
    record.driver = driver
    record.filled_on = filled_on
    record.mileage = mileage
    record.quantity = quantity
    record.price_per_unit = price_per_unit
    record.total_cost = compute_total(quantity, price_per_unit)
    record.full_tank = bool(full_tank)
    record.station = (station or "").strip() or None
    _refresh(vehicle, previous_vehicle)
    return record


def add_odometer_reading(
    vehicle: Vehicle, read_on: date, mileage: int, note: str | None = None, user: User | None = None
) -> OdometerReading:
    validate_reading(vehicle, read_on, mileage, date_field="read_on", mileage_field="mileage")
    reading = OdometerReading(
        vehicle=vehicle,
        read_on=read_on,
        mileage=mileage,
        note=(note or "").strip() or None,
        created_by_id=user.id if user else None,
    )
    db.session.add(reading)
    _refresh(vehicle)
    return reading


def delete_mileage_record(record) -> None:
    """Delete a maintenance/fuel/odometer record and recalculate the mileage."""
    vehicle = record.vehicle
    db.session.delete(record)
    _refresh(vehicle)


def save_plan(
    plan: MaintenancePlan | None,
    *,
    vehicle: Vehicle,
    maintenance_type: MaintenanceType,
    interval_km: int | None,
    interval_days: int | None,
    baseline_date: date,
    baseline_mileage: int,
    active: bool = True,
    notes: str | None = None,
) -> MaintenancePlan:
    if not interval_km and not interval_days:
        raise BusinessRuleError(
            "Въведете интервал в километри, в дни или и двете.", "interval_km"
        )
    if baseline_date > date.today():
        raise BusinessRuleError("Датата на последното извършване не може да е в бъдещето.", "baseline_date")
    if baseline_mileage > vehicle.current_mileage:
        raise BusinessRuleError(
            "Пробегът при последното извършване не може да е по-голям от текущия пробег на МПС.",
            "baseline_mileage",
        )
    duplicate = db.session.scalar(
        select(MaintenancePlan).where(
            MaintenancePlan.vehicle_id == vehicle.id,
            MaintenancePlan.maintenance_type_id == maintenance_type.id,
        )
    )
    if duplicate is not None and duplicate is not plan:
        raise BusinessRuleError(
            f"Дейността „{maintenance_type.name}“ вече е включена в плана на това МПС.",
            "maintenance_type_id",
        )
    if plan is None:
        plan = MaintenancePlan(vehicle=vehicle)
        db.session.add(plan)
    plan.maintenance_type = maintenance_type
    plan.interval_km = interval_km or None
    plan.interval_days = interval_days or None
    plan.baseline_date = baseline_date
    plan.baseline_mileage = baseline_mileage
    plan.active = active
    plan.notes = (notes or "").strip() or None
    return plan
