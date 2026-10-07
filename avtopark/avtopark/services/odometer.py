"""Odometer readings: consistency checks and the vehicle's current mileage.

Every record with a mileage (maintenance, refuel, trip start/end, manual reading
and the initial mileage of the vehicle) is an odometer reading. Together they
must form a non-decreasing sequence in time, which protects the history from
typing mistakes such as an extra zero.
"""
from dataclasses import dataclass
from datetime import date, timedelta

from flask import current_app
from sqlalchemy import select

from ..extensions import db
from ..models import FuelRecord, MaintenanceRecord, OdometerReading, Trip, Vehicle
from .errors import BusinessRuleError
from .formatting import days_word, format_date, format_km


@dataclass(frozen=True)
class Reading:
    on: date
    mileage: int
    source: str  # where the reading comes from, used in error messages
    key: tuple[str, int]  # identifies the record so it can be excluded while editing it


def vehicle_readings(vehicle: Vehicle, exclude: tuple[str, int] | None = None) -> list[Reading]:
    """All known odometer readings of a vehicle, sorted by date and mileage."""
    readings = [
        Reading(
            vehicle.in_service_since,
            vehicle.initial_mileage,
            "началния пробег на МПС",
            ("initial", vehicle.id),
        )
    ]
    for rec in db.session.scalars(
        select(MaintenanceRecord).where(MaintenanceRecord.vehicle_id == vehicle.id)
    ):
        readings.append(
            Reading(
                rec.performed_on,
                rec.mileage,
                f"обслужване от {format_date(rec.performed_on)}",
                ("maintenance", rec.id),
            )
        )
    for rec in db.session.scalars(select(FuelRecord).where(FuelRecord.vehicle_id == vehicle.id)):
        readings.append(
            Reading(
                rec.filled_on,
                rec.mileage,
                f"зареждане от {format_date(rec.filled_on)}",
                ("fuel", rec.id),
            )
        )
    for trip in db.session.scalars(select(Trip).where(Trip.vehicle_id == vehicle.id)):
        started = trip.started_at.date()
        readings.append(
            Reading(started, trip.start_mileage, f"начало на курс от {format_date(started)}", ("trip", trip.id))
        )
        if trip.ended_at is not None and trip.end_mileage is not None:
            ended = trip.ended_at.date()
            readings.append(
                Reading(ended, trip.end_mileage, f"връщане от курс на {format_date(ended)}", ("trip", trip.id))
            )
    for rec in db.session.scalars(
        select(OdometerReading).where(OdometerReading.vehicle_id == vehicle.id)
    ):
        readings.append(
            Reading(
                rec.read_on,
                rec.mileage,
                f"отчет на километража от {format_date(rec.read_on)}",
                ("reading", rec.id),
            )
        )
    if exclude is not None:
        readings = [r for r in readings if r.key != exclude]
    readings.sort(key=lambda r: (r.on, r.mileage))
    return readings


def validate_reading(
    vehicle: Vehicle,
    on: date,
    mileage: int,
    *,
    exclude: tuple[str, int] | None = None,
    date_field: str = "date",
    mileage_field: str = "mileage",
    today: date | None = None,
) -> None:
    """Raise BusinessRuleError if a new reading does not fit the vehicle's history.

    Rules: the date is not in the future; the mileage is not lower than a reading
    from an earlier date and not higher than a reading from a later date; the
    mileage does not grow faster than MAX_KM_PER_DAY.
    """
    today = today or date.today()
    if mileage is None or mileage < 0:
        raise BusinessRuleError("Километражът трябва да е цяло положително число.", mileage_field)
    if on > today:
        raise BusinessRuleError("Датата не може да е в бъдещето.", date_field)

    if (
        exclude != ("initial", vehicle.id)
        and on >= vehicle.in_service_since
        and mileage < vehicle.initial_mileage
    ):
        raise BusinessRuleError(
            f"Километражът ({format_km(mileage)}) е по-малък от началния пробег на МПС "
            f"({format_km(vehicle.initial_mileage)}).",
            mileage_field,
        )

    readings = vehicle_readings(vehicle, exclude)
    before = [r for r in readings if r.on < on]
    after = [r for r in readings if r.on > on]

    if before:
        highest = max(before, key=lambda r: r.mileage)
        if mileage < highest.mileage:
            raise BusinessRuleError(
                f"Километражът ({format_km(mileage)}) е по-малък от отчетения по-рано "
                f"{format_km(highest.mileage)} при {highest.source}.",
                mileage_field,
            )
        latest = before[-1]
        days = (on - latest.on).days
        growth = mileage - latest.mileage
        if growth > current_app.config["MAX_KM_PER_DAY"] * days:
            raise BusinessRuleError(
                f"Пробегът нараства с {format_km(growth)} за {days} {days_word(days)} след "
                f"{latest.source} – вероятно има грешка при въвеждането.",
                mileage_field,
            )
    if after:
        lowest = min(after, key=lambda r: r.mileage)
        if mileage > lowest.mileage:
            raise BusinessRuleError(
                f"Километражът ({format_km(mileage)}) е по-голям от отчетения по-късно "
                f"{format_km(lowest.mileage)} при {lowest.source}.",
                mileage_field,
            )


def refresh_vehicle_mileage(vehicle: Vehicle) -> None:
    """Recalculate the cached current mileage from all readings (after any change)."""
    db.session.flush()
    readings = vehicle_readings(vehicle)
    vehicle.current_mileage = max(r.mileage for r in readings)
    vehicle.mileage_updated_on = max(r.on for r in readings)


def average_daily_km(
    vehicle: Vehicle, today: date | None = None, window_days: int = 90
) -> float | None:
    """Average distance per day over the last `window_days`, used to project due dates."""
    today = today or date.today()
    readings = vehicle_readings(vehicle)
    window_start = today - timedelta(days=window_days)
    recent = [r for r in readings if window_start < r.on <= today]
    if not recent:
        return None
    older = [r for r in readings if r.on <= window_start]
    first = older[-1] if older else readings[0]
    last = recent[-1]
    days = (last.on - first.on).days
    if days <= 0:
        return None
    return (last.mileage - first.mileage) / days
