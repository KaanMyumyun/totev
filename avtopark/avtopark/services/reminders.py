"""Reminders: periodic maintenance, insurance policies and driving licences.

This is the logic behind the "Провери обслужване" button and the dashboard.
"""
import math
from dataclasses import dataclass
from datetime import date, timedelta

from flask import current_app
from sqlalchemy import func, select

from ..extensions import db
from ..models import (
    Driver,
    InsurancePolicy,
    MaintenancePlan,
    MaintenanceRecord,
    PolicyType,
    POLICY_TYPE_LABELS,
    Vehicle,
    VehicleStatus,
)
from .formatting import format_date, format_km
from .odometer import average_daily_km

OVERDUE = "overdue"
DUE_SOON = "due_soon"
OK = "ok"
LEVEL_LABELS = {OVERDUE: "Просрочено", DUE_SOON: "Предстои", OK: "Наред"}
LEVEL_ORDER = {OVERDUE: 0, DUE_SOON: 1, OK: 2}

MAINTENANCE = "maintenance"
INSURANCE = "insurance"
LICENSE = "license"
KIND_LABELS = {MAINTENANCE: "Поддръжка", INSURANCE: "Застраховка", LICENSE: "Шофьорска книжка"}

# An expired casco is reported for this many days; it is optional, unlike civil liability.
CASCO_GRACE_DAYS = 30


@dataclass(frozen=True)
class DueStatus:
    level: str
    due_date: date | None = None
    due_km: int | None = None
    days_left: int | None = None
    km_left: int | None = None


def _passed(left: int | None) -> bool:
    return left is not None and left < 0


def _within(left: int | None, window: int) -> bool:
    return left is not None and left <= window


def evaluate_due(
    *,
    last_date: date,
    last_km: int,
    interval_days: int | None,
    interval_km: int | None,
    today: date,
    current_km: int,
    days_ahead: int,
    km_ahead: int,
) -> DueStatus:
    """Compare today's date and the current mileage with a periodic interval.

    The limit that comes first decides: the activity is overdue as soon as the
    date OR the mileage limit is passed, and due soon when either of them is
    within the warning window (days_ahead / km_ahead). Reaching the limit
    exactly (0 days or 0 km left) counts as due soon, not overdue.
    """
    due_date = last_date + timedelta(days=interval_days) if interval_days else None
    due_km = last_km + interval_km if interval_km else None
    days_left = (due_date - today).days if due_date else None
    km_left = due_km - current_km if due_km is not None else None
    if _passed(days_left) or _passed(km_left):
        level = OVERDUE
    elif _within(days_left, days_ahead) or _within(km_left, km_ahead):
        level = DUE_SOON
    else:
        level = OK
    return DueStatus(level, due_date, due_km, days_left, km_left)


def evaluate_expiry(end_date: date, today: date, days_ahead: int) -> DueStatus:
    """Status of something valid until `end_date` inclusive (a policy or a licence)."""
    days_left = (end_date - today).days
    if days_left < 0:
        level = OVERDUE
    elif days_left <= days_ahead:
        level = DUE_SOON
    else:
        level = OK
    return DueStatus(level, due_date=end_date, days_left=days_left)


@dataclass
class Reminder:
    kind: str
    title: str
    status: DueStatus
    vehicle: Vehicle | None = None
    driver: Driver | None = None
    plan: MaintenancePlan | None = None
    policy: InsurancePolicy | None = None
    last_done: str = ""
    estimated_date: date | None = None  # projected date when a mileage limit will be reached

    @property
    def level(self) -> str:
        return self.status.level

    @property
    def level_label(self) -> str:
        return LEVEL_LABELS[self.level]

    @property
    def kind_label(self) -> str:
        return KIND_LABELS[self.kind]

    @property
    def deadline(self) -> date | None:
        """The earliest known date: the date limit or the projected mileage date."""
        dates = [d for d in (self.status.due_date, self.estimated_date) if d is not None]
        return min(dates) if dates else None

    @property
    def uid(self) -> str:
        if self.plan is not None:
            return f"plan-{self.plan.id}"
        if self.policy is not None:
            return f"policy-{self.policy.id}"
        if self.driver is not None:
            return f"license-{self.driver.id}"
        return f"{self.kind}-{self.vehicle.id if self.vehicle else 0}"

    @property
    def sort_key(self):
        deadline = self.deadline or date.max
        km_left = self.status.km_left if self.status.km_left is not None else math.inf
        return (LEVEL_ORDER[self.level], deadline, km_left)


def _last_done_map(vehicle_id: int | None = None) -> dict[tuple[int, int], tuple[date, int]]:
    """Date and mileage of the latest record per (vehicle, maintenance type)."""
    stmt = select(
        MaintenanceRecord.vehicle_id,
        MaintenanceRecord.maintenance_type_id,
        func.max(MaintenanceRecord.performed_on),
        func.max(MaintenanceRecord.mileage),
    ).group_by(MaintenanceRecord.vehicle_id, MaintenanceRecord.maintenance_type_id)
    if vehicle_id is not None:
        stmt = stmt.where(MaintenanceRecord.vehicle_id == vehicle_id)
    return {(row[0], row[1]): (row[2], row[3]) for row in db.session.execute(stmt)}


def last_done_for_plan(plan: MaintenancePlan, last_done: tuple[date, int] | None):
    """The baseline of a plan is replaced by the latest record made after it."""
    if last_done is not None and last_done[0] >= plan.baseline_date:
        return last_done
    return plan.baseline_date, plan.baseline_mileage


def _maintenance_reminders(today, days_ahead, km_ahead, vehicle, include_inactive):
    stmt = (
        select(MaintenancePlan)
        .join(MaintenancePlan.vehicle)
        .where(MaintenancePlan.active.is_(True))
        .order_by(Vehicle.reg_number, MaintenancePlan.id)
    )
    if vehicle is not None:
        stmt = stmt.where(MaintenancePlan.vehicle_id == vehicle.id)
    if not include_inactive:
        stmt = stmt.where(Vehicle.status != VehicleStatus.OUT_OF_SERVICE)

    last_done = _last_done_map(vehicle.id if vehicle is not None else None)
    daily_km: dict[int, float | None] = {}
    reminders = []
    for plan in db.session.scalars(stmt):
        current = plan.vehicle
        last_date, last_km = last_done_for_plan(
            plan, last_done.get((plan.vehicle_id, plan.maintenance_type_id))
        )
        status = evaluate_due(
            last_date=last_date,
            last_km=last_km,
            interval_days=plan.interval_days,
            interval_km=plan.interval_km,
            today=today,
            current_km=current.current_mileage,
            days_ahead=days_ahead,
            km_ahead=km_ahead,
        )
        estimated = None
        if status.km_left is not None and status.km_left <= 0:
            estimated = today  # the mileage limit is already reached
        elif status.km_left is not None:
            if current.id not in daily_km:
                daily_km[current.id] = average_daily_km(current, today)
            per_day = daily_km[current.id]
            if per_day and per_day > 0:
                estimated = today + timedelta(days=math.ceil(status.km_left / per_day))
        reminders.append(
            Reminder(
                kind=MAINTENANCE,
                title=plan.maintenance_type.name,
                status=status,
                vehicle=current,
                plan=plan,
                last_done=f"{format_date(last_date)} при {format_km(last_km)}",
                estimated_date=estimated,
            )
        )
    return reminders


def _policy_reminder(policy: InsurancePolicy, today: date, days_ahead: int) -> Reminder:
    return Reminder(
        kind=INSURANCE,
        title=f"Застраховка „{policy.type_label}“",
        status=evaluate_expiry(policy.end_date, today, days_ahead),
        vehicle=policy.vehicle,
        policy=policy,
        last_done=f"{policy.insurer}, полица № {policy.policy_number}",
    )


def _insurance_reminders(today, days_ahead, vehicle, include_inactive):
    stmt = select(Vehicle).order_by(Vehicle.reg_number)
    if vehicle is not None:
        stmt = stmt.where(Vehicle.id == vehicle.id)
    if not include_inactive:
        stmt = stmt.where(Vehicle.status != VehicleStatus.OUT_OF_SERVICE)
    vehicles = list(db.session.scalars(stmt))

    latest: dict[tuple[int, str], InsurancePolicy] = {}
    for policy in db.session.scalars(select(InsurancePolicy).order_by(InsurancePolicy.end_date)):
        latest[(policy.vehicle_id, policy.policy_type)] = policy

    reminders = []
    for current in vehicles:
        # Civil liability insurance is mandatory for every vehicle in use.
        civil = latest.get((current.id, PolicyType.CIVIL_LIABILITY))
        if civil is None:
            reminders.append(
                Reminder(
                    kind=INSURANCE,
                    title=f"Застраховка „{POLICY_TYPE_LABELS[PolicyType.CIVIL_LIABILITY]}“",
                    status=DueStatus(OVERDUE),
                    vehicle=current,
                    last_done="Няма въведена полица",
                )
            )
        else:
            reminders.append(_policy_reminder(civil, today, days_ahead))
        casco = latest.get((current.id, PolicyType.CASCO))
        if casco is not None and casco.end_date >= today - timedelta(days=CASCO_GRACE_DAYS):
            reminders.append(_policy_reminder(casco, today, days_ahead))
    return reminders


def _license_reminders(today, days_ahead):
    reminders = []
    stmt = select(Driver).where(Driver.active.is_(True)).order_by(Driver.last_name)
    for driver in db.session.scalars(stmt):
        reminders.append(
            Reminder(
                kind=LICENSE,
                title="Шофьорска книжка",
                status=evaluate_expiry(driver.license_expiry, today, days_ahead),
                driver=driver,
                last_done=f"№ {driver.license_number}, кат. {driver.license_categories}",
            )
        )
    return reminders


def collect_reminders(
    today: date | None = None,
    *,
    days_ahead: int | None = None,
    km_ahead: int | None = None,
    vehicle: Vehicle | None = None,
    kinds=(MAINTENANCE, INSURANCE, LICENSE),
    include_ok: bool = False,
    include_inactive: bool = False,
) -> list[Reminder]:
    """Check every plan, policy and licence and return what needs attention.

    Vehicles that are out of service are skipped unless include_inactive is set.
    The result is sorted: overdue first, then by the nearest deadline.
    """
    config = current_app.config
    today = today or date.today()
    days_ahead = config["REMINDER_DAYS_AHEAD"] if days_ahead is None else days_ahead
    km_ahead = config["REMINDER_KM_AHEAD"] if km_ahead is None else km_ahead

    reminders: list[Reminder] = []
    if MAINTENANCE in kinds:
        reminders += _maintenance_reminders(today, days_ahead, km_ahead, vehicle, include_inactive)
    if INSURANCE in kinds:
        reminders += _insurance_reminders(today, days_ahead, vehicle, include_inactive)
    if LICENSE in kinds and vehicle is None:
        reminders += _license_reminders(today, days_ahead)
    if not include_ok:
        reminders = [r for r in reminders if r.level != OK]
    reminders.sort(key=lambda r: r.sort_key)
    return reminders


def count_by_level(reminders) -> dict[str, int]:
    counts = {OVERDUE: 0, DUE_SOON: 0, OK: 0}
    for reminder in reminders:
        counts[reminder.level] += 1
    return counts


def worst_level_by_vehicle(reminders) -> dict[int, str]:
    worst: dict[int, str] = {}
    for reminder in reminders:
        if reminder.vehicle is None:
            continue
        current = worst.get(reminder.vehicle.id)
        if current is None or LEVEL_ORDER[reminder.level] < LEVEL_ORDER[current]:
            worst[reminder.vehicle.id] = reminder.level
    return worst
