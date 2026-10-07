"""Fuel cost and consumption calculations."""
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from ..extensions import db
from ..models import FuelRecord, Vehicle

CENT = Decimal("0.01")


def compute_total(quantity: Decimal, price_per_unit: Decimal) -> Decimal:
    """Total cost of a refuel rounded to cents."""
    return (Decimal(quantity) * Decimal(price_per_unit)).quantize(CENT, rounding=ROUND_HALF_UP)


def average_consumption(records) -> Decimal | None:
    """Average consumption per 100 km using the full-tank method.

    Between two refuels to a full tank, the fuel put in after the first one is
    exactly what was used for the distance between them. Partial refuels in
    between are added to the amount. Returns None without two full refuels.
    """
    fills = sorted(records, key=lambda r: (r.mileage, r.filled_on))
    full = [i for i, rec in enumerate(fills) if rec.full_tank]
    if len(full) < 2:
        return None
    first, last = full[0], full[-1]
    distance = fills[last].mileage - fills[first].mileage
    if distance <= 0:
        return None
    used = sum((Decimal(rec.quantity) for rec in fills[first + 1 : last + 1]), Decimal(0))
    return (used * 100 / distance).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class ConsumptionRow:
    vehicle: Vehicle
    fills: int
    quantity: Decimal
    cost: Decimal
    consumption: Decimal | None

    @property
    def average_price(self) -> Decimal | None:
        if not self.quantity:
            return None
        return (self.cost / self.quantity).quantize(Decimal("0.001"))


def consumption_by_vehicle(date_from: date, date_to: date) -> list[ConsumptionRow]:
    records = db.session.scalars(
        select(FuelRecord)
        .where(FuelRecord.filled_on.between(date_from, date_to))
        .order_by(FuelRecord.vehicle_id, FuelRecord.mileage)
    )
    grouped: dict[int, list[FuelRecord]] = {}
    for rec in records:
        grouped.setdefault(rec.vehicle_id, []).append(rec)

    rows = []
    for recs in grouped.values():
        rows.append(
            ConsumptionRow(
                vehicle=recs[0].vehicle,
                fills=len(recs),
                quantity=sum((r.quantity for r in recs), Decimal(0)),
                cost=sum((r.total_cost for r in recs), Decimal(0)),
                consumption=average_consumption(recs),
            )
        )
    rows.sort(key=lambda row: row.vehicle.reg_number)
    return rows
