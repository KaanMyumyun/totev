"""Dashboard, the "Провери обслужване" check and the health endpoint."""
from datetime import date

from flask import Blueprint, jsonify, render_template, request
from sqlalchemy import func, select, text

from ..extensions import db
from ..models import Trip, Vehicle, VehicleStatus
from ..services.audit import log_action
from ..services.costs import CATEGORY_LABELS, monthly_costs
from ..services.formatting import month_label
from ..services.reminders import DUE_SOON, OVERDUE, collect_reminders, count_by_level

bp = Blueprint("main", __name__)


def costs_chart_data(months):
    """Data for the stacked bar chart of monthly costs (read by static/js/charts.js)."""
    return {
        "labels": [month_label(m.month) for m in months],
        "datasets": [
            {"key": key, "label": label, "data": [float(m.amounts[key]) for m in months]}
            for key, label in CATEGORY_LABELS.items()
        ],
    }


@bp.route("/")
def dashboard():
    today = date.today()
    status_counts = dict(
        db.session.execute(select(Vehicle.status, func.count(Vehicle.id)).group_by(Vehicle.status)).all()
    )
    reminders = collect_reminders(today)
    level_counts = count_by_level(reminders)
    open_trips = db.session.scalars(
        select(Trip).where(Trip.ended_at.is_(None)).order_by(Trip.started_at)
    ).all()

    first_month = date(today.year - (1 if today.month <= 5 else 0), (today.month - 6) % 12 + 1, 1)
    months = monthly_costs(first_month, today)
    return render_template(
        "dashboard.html",
        status_counts=status_counts,
        total_vehicles=sum(status_counts.values()),
        reminders=reminders[:12],
        reminders_total=len(reminders),
        level_counts=level_counts,
        open_trips=open_trips,
        month_total=months[-1].total if months else 0,
        chart=costs_chart_data(months),
        VehicleStatus=VehicleStatus,
    )


@bp.route("/check")
def check():
    """'Провери обслужване': compare mileage and dates with the plan of every vehicle."""
    show_all = request.args.get("all") == "1"
    reminders = collect_reminders(include_ok=show_all)
    counts = count_by_level(reminders)
    log_action(
        "check", "reminders", None,
        f"Просрочени: {counts[OVERDUE]}, предстоящи: {counts[DUE_SOON]}",
    )
    db.session.commit()
    return render_template("check.html", reminders=reminders, counts=counts, show_all=show_all)


@bp.route("/health")
def health():
    """Used by Docker/monitoring to check that the app and the database respond."""
    try:
        db.session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - any database failure means "unhealthy"
        return jsonify(status="error", database="unavailable"), 503
    return jsonify(status="ok", database="ok")
