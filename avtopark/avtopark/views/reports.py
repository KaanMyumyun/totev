"""Reports: upcoming maintenance, costs, inactive vehicles, consumption, usage."""
from datetime import date, datetime, time, timedelta

from flask import Blueprint, Response, render_template, request
from sqlalchemy import select

from ..extensions import db
from ..models import Driver, MaintenanceType, Trip, Vehicle, VehicleStatus
from ..services.costs import (
    CATEGORY_LABELS,
    costs_by_vehicle,
    maintenance_costs_by_type,
    monthly_costs,
    totals,
)
from ..services.exports import build_ics, csv_response
from ..services.fuel import consumption_by_vehicle
from ..services.reminders import (
    DUE_SOON,
    INSURANCE,
    LICENSE,
    MAINTENANCE,
    OVERDUE,
    collect_reminders,
    count_by_level,
)
from .common import int_arg, period_args, permission_required
from .main import costs_chart_data

bp = Blueprint("reports", __name__)


@bp.route("/")
def index():
    return render_template("reports/index.html")


@bp.route("/upcoming")
def upcoming():
    """Schedule of upcoming and overdue activities (maintenance, insurance, licences)."""
    days = int_arg("days", 30, 0, 730)
    km = int_arg("km", 1000, 0, 100_000)
    kind = request.args.get("kind", "")
    type_id = request.args.get("type_id", type=int)
    level = request.args.get("level", "")
    kinds = (kind,) if kind in (MAINTENANCE, INSURANCE, LICENSE) else (MAINTENANCE, INSURANCE, LICENSE)

    reminders = collect_reminders(days_ahead=days, km_ahead=km, kinds=kinds)
    if type_id:
        reminders = [r for r in reminders if r.plan is not None and r.plan.maintenance_type_id == type_id]
    if level in (OVERDUE, DUE_SOON):
        reminders = [r for r in reminders if r.level == level]

    export = request.args.get("export")
    if export == "csv":
        return csv_response(
            f"predstoyashti-{date.today():%Y%m%d}.csv",
            ["Състояние", "Вид", "МПС / шофьор", "Дейност", "Последно", "Срок (дата)", "Остават дни",
             "Срок (км)", "Остават км", "Прогнозна дата"],
            (
                [r.level_label, r.kind_label,
                 r.vehicle.display_name if r.vehicle else (r.driver.full_name if r.driver else ""),
                 r.title, r.last_done, r.status.due_date, r.status.days_left, r.status.due_km,
                 r.status.km_left, r.estimated_date]
                for r in reminders
            ),
        )
    if export == "ics":
        return Response(
            build_ics(reminders, date.today()),
            mimetype="text/calendar",
            headers={"Content-Disposition": 'attachment; filename="avtopark-obsluzhvane.ics"'},
        )
    return render_template(
        "reports/upcoming.html",
        reminders=reminders,
        counts=count_by_level(reminders),
        types=db.session.scalars(select(MaintenanceType).order_by(MaintenanceType.name)).all(),
        filters={"days": days, "km": km, "kind": kind, "type_id": type_id, "level": level},
    )


@bp.route("/costs")
@permission_required("reports.costs")
def costs():
    today = date.today()
    date_from, date_to = period_args(date(today.year, 1, 1), today)
    vehicle_id = request.args.get("vehicle_id", type=int) or None
    rows = costs_by_vehicle(date_from, date_to, vehicle_id)
    summary = totals(rows)

    if request.args.get("export") == "csv":
        return csv_response(
            f"razhodi-{date_from:%Y%m%d}-{date_to:%Y%m%d}.csv",
            ["МПС", "Марка и модел", "Гориво (€)", "Обслужване и ремонти (€)", "Застраховки (€)",
             "Общо (€)", "Изминати км", "€ на км"],
            (
                [r.vehicle.reg_number, f"{r.vehicle.make} {r.vehicle.model}", r.fuel, r.maintenance,
                 r.insurance, r.total, r.distance, r.cost_per_km]
                for r in rows
            ),
        )
    months = monthly_costs(date_from, date_to, vehicle_id)
    return render_template(
        "reports/costs.html",
        rows=rows,
        summary=summary,
        chart=costs_chart_data(months),
        by_type=maintenance_costs_by_type(date_from, date_to, vehicle_id),
        category_labels=CATEGORY_LABELS,
        vehicles=db.session.scalars(select(Vehicle).order_by(Vehicle.reg_number)).all(),
        filters={"from": date_from, "to": date_to, "vehicle_id": vehicle_id},
    )


@bp.route("/inactive")
def inactive():
    """Vehicles in repair or out of service, and active vehicles that have not moved recently."""
    today = date.today()
    idle_days = int_arg("idle_days", 30, 1, 365)
    inactive_vehicles = db.session.scalars(
        select(Vehicle).where(Vehicle.status != VehicleStatus.ACTIVE).order_by(Vehicle.status_changed_at)
    ).all()
    idle_vehicles = db.session.scalars(
        select(Vehicle)
        .where(
            Vehicle.status == VehicleStatus.ACTIVE,
            Vehicle.mileage_updated_on < today - timedelta(days=idle_days),
            ~Vehicle.trips.any(Trip.ended_at.is_(None)),
        )
        .order_by(Vehicle.mileage_updated_on)
    ).all()
    return render_template(
        "reports/inactive.html",
        inactive_vehicles=inactive_vehicles,
        idle_vehicles=idle_vehicles,
        idle_days=idle_days,
    )


@bp.route("/consumption")
def consumption():
    today = date.today()
    date_from, date_to = period_args(today - timedelta(days=365), today)
    rows = consumption_by_vehicle(date_from, date_to)
    return render_template("reports/consumption.html", rows=rows, filters={"from": date_from, "to": date_to})


@bp.route("/usage")
def usage():
    """Who used which vehicle and when."""
    today = date.today()
    date_from, date_to = period_args(today - timedelta(days=30), today)
    vehicle_id = request.args.get("vehicle_id", type=int)
    driver_id = request.args.get("driver_id", type=int)
    start = datetime.combine(date_from, time.min)
    end = datetime.combine(date_to + timedelta(days=1), time.min)
    stmt = select(Trip).where(Trip.started_at >= start, Trip.started_at < end)
    if vehicle_id:
        stmt = stmt.where(Trip.vehicle_id == vehicle_id)
    if driver_id:
        stmt = stmt.where(Trip.driver_id == driver_id)
    trips = db.session.scalars(stmt.order_by(Trip.started_at)).all()

    per_driver: dict[int, dict] = {}
    for trip in trips:
        entry = per_driver.setdefault(
            trip.driver_id, {"driver": trip.driver, "trips": 0, "km": 0, "vehicles": set()}
        )
        entry["trips"] += 1
        entry["km"] += trip.distance or 0
        entry["vehicles"].add(trip.vehicle.reg_number)
    summary = sorted(per_driver.values(), key=lambda e: -e["km"])

    if request.args.get("export") == "csv":
        return csv_response(
            f"kursove-{date_from:%Y%m%d}-{date_to:%Y%m%d}.csv",
            ["МПС", "Шофьор", "Начало", "Край", "Начален км", "Краен км", "Изминати км", "Цел"],
            (
                [t.vehicle.reg_number, t.driver.full_name, t.started_at, t.ended_at, t.start_mileage,
                 t.end_mileage, t.distance, t.purpose]
                for t in trips
            ),
        )
    return render_template(
        "reports/usage.html",
        trips=trips,
        summary=summary,
        vehicles=db.session.scalars(select(Vehicle).order_by(Vehicle.reg_number)).all(),
        drivers=db.session.scalars(select(Driver).order_by(Driver.last_name)).all(),
        filters={"from": date_from, "to": date_to, "vehicle_id": vehicle_id, "driver_id": driver_id},
    )
