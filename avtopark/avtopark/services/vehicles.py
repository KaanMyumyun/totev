"""Vehicle registry operations."""
from datetime import date, datetime

from sqlalchemy import select

from ..extensions import db
from ..models import MaintenancePlan, MaintenanceType, Vehicle, VehicleStatus, VEHICLE_STATUS_LABELS
from .errors import BusinessRuleError
from .odometer import refresh_vehicle_mileage, validate_reading
from .validators import normalize_plate, normalize_vin


def _ensure_unique(reg_number: str, vin: str | None, vehicle: Vehicle | None) -> None:
    other = db.session.scalar(select(Vehicle).where(Vehicle.reg_number == reg_number))
    if other is not None and other is not vehicle:
        raise BusinessRuleError(f"Вече има МПС с регистрационен номер {reg_number}.", "reg_number")
    if vin:
        other = db.session.scalar(select(Vehicle).where(Vehicle.vin == vin))
        if other is not None and other is not vehicle:
            raise BusinessRuleError(f"Вече има МПС с VIN {vin} ({other.reg_number}).", "vin")


def create_vehicle(
    *,
    reg_number: str,
    make: str,
    model: str,
    year: int,
    vehicle_type: str,
    fuel_type: str,
    vin: str | None = None,
    in_service_since: date | None = None,
    initial_mileage: int = 0,
    responsible_driver_id: int | None = None,
    notes: str | None = None,
) -> Vehicle:
    """Register a vehicle and add the default periodic activities to its plan."""
    reg_number = normalize_plate(reg_number)
    vin = normalize_vin(vin)
    _ensure_unique(reg_number, vin, None)
    in_service_since = in_service_since or date.today()
    if in_service_since > date.today():
        raise BusinessRuleError("Датата не може да е в бъдещето.", "in_service_since")
    if initial_mileage is None or initial_mileage < 0:
        raise BusinessRuleError("Пробегът не може да е отрицателен.", "initial_mileage")

    vehicle = Vehicle(
        reg_number=reg_number,
        make=make.strip(),
        model=model.strip(),
        year=year,
        vehicle_type=vehicle_type,
        fuel_type=fuel_type,
        vin=vin,
        in_service_since=in_service_since,
        initial_mileage=initial_mileage,
        current_mileage=initial_mileage,
        mileage_updated_on=in_service_since,
        responsible_driver_id=responsible_driver_id or None,
        notes=(notes or "").strip() or None,
    )
    db.session.add(vehicle)
    default_types = db.session.scalars(
        select(MaintenanceType).where(
            MaintenanceType.active.is_(True), MaintenanceType.auto_assign.is_(True)
        )
    )
    for mtype in default_types:
        if mtype.is_periodic:
            vehicle.plans.append(
                MaintenancePlan(
                    maintenance_type=mtype,
                    interval_km=mtype.default_interval_km,
                    interval_days=mtype.default_interval_days,
                    baseline_date=in_service_since,
                    baseline_mileage=initial_mileage,
                )
            )
    db.session.flush()
    return vehicle


def update_vehicle(
    vehicle: Vehicle,
    *,
    reg_number: str,
    make: str,
    model: str,
    year: int,
    vehicle_type: str,
    fuel_type: str,
    vin: str | None,
    in_service_since: date,
    initial_mileage: int,
    responsible_driver_id: int | None,
    notes: str | None,
) -> Vehicle:
    reg_number = normalize_plate(reg_number)
    vin = normalize_vin(vin)
    _ensure_unique(reg_number, vin, vehicle)
    if (in_service_since, initial_mileage) != (vehicle.in_service_since, vehicle.initial_mileage):
        # The initial mileage is the first reading: it must fit the rest of the history.
        validate_reading(
            vehicle, in_service_since, initial_mileage, exclude=("initial", vehicle.id),
            date_field="in_service_since", mileage_field="initial_mileage",
        )
    vehicle.reg_number = reg_number
    vehicle.make = make.strip()
    vehicle.model = model.strip()
    vehicle.year = year
    vehicle.vehicle_type = vehicle_type
    vehicle.fuel_type = fuel_type
    vehicle.vin = vin
    vehicle.in_service_since = in_service_since
    vehicle.initial_mileage = initial_mileage
    vehicle.responsible_driver_id = responsible_driver_id or None
    vehicle.notes = (notes or "").strip() or None
    refresh_vehicle_mileage(vehicle)
    return vehicle


def change_status(vehicle: Vehicle, status: str, note: str | None = None) -> None:
    if status not in VEHICLE_STATUS_LABELS:
        raise BusinessRuleError("Невалиден статус.", "status")
    if status != VehicleStatus.ACTIVE and vehicle.open_trip is not None:
        raise BusinessRuleError(
            "МПС е в курс. Първо въведете връщането му, след това сменете статуса.", "status"
        )
    if status != vehicle.status:
        vehicle.status_changed_at = datetime.now()
    vehicle.status = status
    vehicle.status_note = (note or "").strip() or None


def delete_vehicle(vehicle: Vehicle) -> None:
    """Only vehicles without history can be deleted; the history must stay traceable."""
    if vehicle.has_history():
        raise BusinessRuleError(
            "МПС има записи (ремонти, зареждания, курсове или застраховки) и не може да бъде "
            "изтрито. Сменете статуса му на „Спряно от движение“."
        )
    db.session.delete(vehicle)
