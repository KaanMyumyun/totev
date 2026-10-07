"""Cost reports: fuel, maintenance and insurance per vehicle and per month."""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from ..extensions import db
from ..models import FuelRecord, InsurancePolicy, MaintenanceRecord, MaintenanceType, Vehicle
from .odometer import vehicle_readings

ZERO = Decimal("0.00")

# (model, date column, amount column) for each cost category.
CATEGORIES = {
    "fuel": (FuelRecord, FuelRecord.filled_on, FuelRecord.total_cost),
    "maintenance": (MaintenanceRecord, MaintenanceRecord.performed_on, MaintenanceRecord.cost),
    # A policy premium is counted on the day the policy starts.
    "insurance": (InsurancePolicy, InsurancePolicy.start_date, InsurancePolicy.premium),
}
CATEGORY_LABELS = {"fuel": "Гориво", "maintenance": "Обслужване и ремонти", "insurance": "Застраховки"}


@dataclass
class VehicleCosts:
    vehicle: Vehicle
    fuel: Decimal = ZERO
    maintenance: Decimal = ZERO
    insurance: Decimal = ZERO
    distance: int | None = None

    @property
    def total(self) -> Decimal:
        return self.fuel + self.maintenance + self.insurance

    @property
    def cost_per_km(self) -> Decimal | None:
        if not self.distance:
            return None
        return (self.total / self.distance).quantize(Decimal("0.001"))


@dataclass
class MonthCosts:
    month: date
    amounts: dict[str, Decimal] = field(
        default_factory=lambda: {key: ZERO for key in CATEGORIES}
    )

    @property
    def total(self) -> Decimal:
        return sum(self.amounts.values(), ZERO)


def month_range(date_from: date, date_to: date) -> list[date]:
    """First day of every month between the two dates (inclusive)."""
    months = []
    current = date(date_from.year, date_from.month, 1)
    while current <= date_to:
        months.append(current)
        current = date(current.year + current.month // 12, current.month % 12 + 1, 1)
    return months


def distance_in_period(vehicle: Vehicle, date_from: date, date_to: date) -> int | None:
    """Kilometres driven in the period, from the odometer readings."""
    readings = vehicle_readings(vehicle)
    values = [r.mileage for r in readings if date_from <= r.on <= date_to]
    earlier = [r for r in readings if r.on < date_from]
    if earlier:
        values.append(earlier[-1].mileage)
    if len(values) < 2:
        return None
    return max(values) - min(values)


def costs_by_vehicle(
    date_from: date, date_to: date, vehicle_id: int | None = None
) -> list[VehicleCosts]:
    stmt = select(Vehicle).order_by(Vehicle.reg_number)
    if vehicle_id is not None:
        stmt = stmt.where(Vehicle.id == vehicle_id)
    rows = {v.id: VehicleCosts(vehicle=v) for v in db.session.scalars(stmt)}

    for key, (model, date_col, amount_col) in CATEGORIES.items():
        sums = select(model.vehicle_id, func.sum(amount_col)).where(
            date_col.between(date_from, date_to)
        )
        if vehicle_id is not None:
            sums = sums.where(model.vehicle_id == vehicle_id)
        for vid, amount in db.session.execute(sums.group_by(model.vehicle_id)):
            if vid in rows:
                setattr(rows[vid], key, amount or ZERO)

    for row in rows.values():
        row.distance = distance_in_period(row.vehicle, date_from, date_to)
    return sorted(rows.values(), key=lambda r: (-r.total, r.vehicle.reg_number))


def monthly_costs(date_from: date, date_to: date, vehicle_id: int | None = None) -> list[MonthCosts]:
    months = {m: MonthCosts(month=m) for m in month_range(date_from, date_to)}
    for key, (model, date_col, amount_col) in CATEGORIES.items():
        stmt = select(date_col, amount_col).where(date_col.between(date_from, date_to))
        if vehicle_id is not None:
            stmt = stmt.where(model.vehicle_id == vehicle_id)
        for day, amount in db.session.execute(stmt):
            months[date(day.year, day.month, 1)].amounts[key] += amount
    return list(months.values())


def maintenance_costs_by_type(date_from: date, date_to: date, vehicle_id: int | None = None):
    """(type name, number of records, total cost) ordered by cost."""
    total = func.sum(MaintenanceRecord.cost)
    stmt = (
        select(MaintenanceType.name, func.count(MaintenanceRecord.id), total)
        .join(MaintenanceRecord.maintenance_type)
        .where(MaintenanceRecord.performed_on.between(date_from, date_to))
        .group_by(MaintenanceType.name)
        .order_by(total.desc())
    )
    if vehicle_id is not None:
        stmt = stmt.where(MaintenanceRecord.vehicle_id == vehicle_id)
    return [(name, count, amount or ZERO) for name, count, amount in db.session.execute(stmt)]


def totals(rows: list[VehicleCosts]) -> dict[str, Decimal]:
    result = {key: ZERO for key in CATEGORIES}
    for row in rows:
        for key in CATEGORIES:
            result[key] += getattr(row, key)
    result["total"] = sum((result[key] for key in CATEGORIES), ZERO)
    return result
