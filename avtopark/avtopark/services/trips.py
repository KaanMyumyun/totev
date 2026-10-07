"""Handing out vehicles to drivers and taking them back ("курсове")."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from ..extensions import db
from ..models import Driver, Trip, User, Vehicle, VehicleStatus
from .errors import BusinessRuleError
from .formatting import format_date, format_datetime, format_km
from .odometer import refresh_vehicle_mileage, validate_reading
from .records import save_fuel_record


def open_trip_for_vehicle(vehicle: Vehicle, exclude_id: int | None = None) -> Trip | None:
    stmt = select(Trip).where(Trip.vehicle_id == vehicle.id, Trip.ended_at.is_(None))
    if exclude_id is not None:
        stmt = stmt.where(Trip.id != exclude_id)
    return db.session.scalar(stmt)


def open_trip_for_driver(driver: Driver, exclude_id: int | None = None) -> Trip | None:
    stmt = select(Trip).where(Trip.driver_id == driver.id, Trip.ended_at.is_(None))
    if exclude_id is not None:
        stmt = stmt.where(Trip.id != exclude_id)
    return db.session.scalar(stmt)


def ensure_can_start(vehicle: Vehicle, driver: Driver, started_at: datetime, exclude_id=None):
    if vehicle.status != VehicleStatus.ACTIVE:
        raise BusinessRuleError(
            f"МПС {vehicle.reg_number} е със статус „{vehicle.status_label}“ и не може да бъде отпуснато.",
            "vehicle_id",
        )
    busy = open_trip_for_vehicle(vehicle, exclude_id)
    if busy is not None:
        raise BusinessRuleError(
            f"МПС {vehicle.reg_number} вече е в курс от {format_datetime(busy.started_at)} "
            f"с шофьор {busy.driver.full_name}.",
            "vehicle_id",
        )
    if not driver.active:
        raise BusinessRuleError(f"Шофьорът {driver.full_name} е неактивен.", "driver_id")
    if driver.license_expiry < started_at.date():
        raise BusinessRuleError(
            f"Шофьорската книжка на {driver.full_name} е изтекла на {format_date(driver.license_expiry)}.",
            "driver_id",
        )
    busy = open_trip_for_driver(driver, exclude_id)
    if busy is not None:
        raise BusinessRuleError(
            f"{driver.full_name} вече е в курс с МПС {busy.vehicle.reg_number}.", "driver_id"
        )


def start_trip(
    *,
    vehicle: Vehicle,
    driver: Driver,
    started_at: datetime,
    start_mileage: int,
    purpose: str,
    notes: str | None = None,
    user: User | None = None,
) -> Trip:
    ensure_can_start(vehicle, driver, started_at)
    validate_reading(
        vehicle, started_at.date(), start_mileage,
        date_field="started_at", mileage_field="start_mileage",
    )
    trip = Trip(
        vehicle=vehicle,
        driver=driver,
        started_at=started_at,
        start_mileage=start_mileage,
        purpose=purpose.strip(),
        notes=(notes or "").strip() or None,
        created_by_id=user.id if user else None,
    )
    db.session.add(trip)
    refresh_vehicle_mileage(vehicle)
    return trip


def _check_end(
    vehicle: Vehicle,
    trip_id: int,
    started_at: datetime,
    start_mileage: int,
    ended_at: datetime,
    end_mileage: int,
) -> None:
    if ended_at < started_at:
        raise BusinessRuleError(
            f"Времето на връщане е преди началото на курса ({format_datetime(started_at)}).",
            "ended_at",
        )
    if end_mileage < start_mileage:
        raise BusinessRuleError(
            f"Крайният километраж не може да е по-малък от началния ({format_km(start_mileage)}).",
            "end_mileage",
        )
    validate_reading(
        vehicle, ended_at.date(), end_mileage, exclude=("trip", trip_id),
        date_field="ended_at", mileage_field="end_mileage",
    )


def finish_trip(
    trip: Trip,
    *,
    ended_at: datetime,
    end_mileage: int,
    notes: str | None = None,
    fuel_quantity: Decimal | None = None,
    fuel_price: Decimal | None = None,
    fuel_full_tank: bool = True,
    fuel_station: str | None = None,
    problem: str | None = None,
    user: User | None = None,
) -> Trip:
    """Return a vehicle: new mileage, optional refuel and optional problem report."""
    if not trip.is_open:
        raise BusinessRuleError("Този курс вече е приключен.")
    _check_end(trip.vehicle, trip.id, trip.started_at, trip.start_mileage, ended_at, end_mileage)
    trip.ended_at = ended_at
    trip.end_mileage = end_mileage
    if notes and notes.strip():
        trip.notes = "\n".join(filter(None, [trip.notes, notes.strip()]))

    if fuel_quantity:
        save_fuel_record(
            None,
            vehicle=trip.vehicle,
            driver=trip.driver,
            filled_on=ended_at.date(),
            mileage=end_mileage,
            quantity=fuel_quantity,
            price_per_unit=fuel_price,
            full_tank=fuel_full_tank,
            station=fuel_station,
            user=user,
        )
    if problem and problem.strip():
        vehicle = trip.vehicle
        vehicle.status = VehicleStatus.IN_REPAIR
        vehicle.status_note = f"Сигнал от {trip.driver.full_name}: {problem.strip()}"[:255]
        vehicle.status_changed_at = datetime.now()
    refresh_vehicle_mileage(trip.vehicle)
    return trip


def update_trip(
    trip: Trip,
    *,
    started_at: datetime,
    start_mileage: int,
    ended_at: datetime | None,
    end_mileage: int | None,
    purpose: str,
    notes: str | None,
) -> Trip:
    """Correct an existing trip (vehicle and driver stay the same)."""
    if (ended_at is None) != (end_mileage is None):
        raise BusinessRuleError(
            "За приключен курс попълнете и времето на връщане, и крайния километраж.", "ended_at"
        )
    validate_reading(
        trip.vehicle, started_at.date(), start_mileage, exclude=("trip", trip.id),
        date_field="started_at", mileage_field="start_mileage",
    )
    if ended_at is not None:
        _check_end(trip.vehicle, trip.id, started_at, start_mileage, ended_at, end_mileage)
    trip.started_at = started_at
    trip.start_mileage = start_mileage
    trip.ended_at = ended_at
    trip.end_mileage = end_mileage
    trip.purpose = purpose.strip()
    trip.notes = (notes or "").strip() or None
    refresh_vehicle_mileage(trip.vehicle)
    return trip


def delete_trip(trip: Trip) -> None:
    vehicle = trip.vehicle
    db.session.delete(trip)
    refresh_vehicle_mileage(vehicle)
