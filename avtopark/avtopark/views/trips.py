"""Trips: handing out a vehicle to a driver and returning it."""
from datetime import datetime, time, timedelta

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from ..extensions import db
from ..forms import TripEditForm, TripReturnForm, TripStartForm
from ..models import Driver, Trip, Vehicle, VehicleStatus
from ..services.audit import log_action
from ..services.errors import BusinessRuleError
from ..services.formatting import format_km
from ..services.trips import delete_trip, finish_trip, start_trip, update_trip
from .common import (
    confirm_delete,
    date_arg,
    driver_choices,
    paginate,
    permission_required,
    save_changes,
    show_business_error,
    vehicle_choices,
)

bp = Blueprint("trips", __name__)


@bp.route("/")
def index():
    state = request.args.get("state", "")
    vehicle_id = request.args.get("vehicle_id", type=int)
    driver_id = request.args.get("driver_id", type=int)
    date_from = date_arg("from", None)
    date_to = date_arg("to", None)
    stmt = select(Trip)
    if state == "open":
        stmt = stmt.where(Trip.ended_at.is_(None))
    elif state == "closed":
        stmt = stmt.where(Trip.ended_at.is_not(None))
    if vehicle_id:
        stmt = stmt.where(Trip.vehicle_id == vehicle_id)
    if driver_id:
        stmt = stmt.where(Trip.driver_id == driver_id)
    if date_from:
        stmt = stmt.where(Trip.started_at >= date_from)
    if date_to:
        stmt = stmt.where(Trip.started_at < datetime.combine(date_to + timedelta(days=1), time.min))
    page = paginate(stmt.order_by(Trip.started_at.desc()))
    return render_template(
        "trips/index.html",
        page=page,
        vehicles=db.session.scalars(select(Vehicle).order_by(Vehicle.reg_number)).all(),
        drivers=db.session.scalars(select(Driver).order_by(Driver.last_name)).all(),
        filters={"state": state, "vehicle_id": vehicle_id, "driver_id": driver_id, "from": date_from, "to": date_to},
    )


@bp.route("/open")
def open_trips():
    """Quick list for "Връщане от курс"."""
    trips = db.session.scalars(select(Trip).where(Trip.ended_at.is_(None)).order_by(Trip.started_at)).all()
    return render_template("trips/open.html", trips=trips)


@bp.route("/new", methods=["GET", "POST"])
@permission_required("trips.manage")
def start():
    form = TripStartForm()
    available = db.session.scalars(
        select(Vehicle)
        .where(Vehicle.status == VehicleStatus.ACTIVE, ~Vehicle.trips.any(Trip.ended_at.is_(None)))
        .order_by(Vehicle.reg_number)
    ).all()
    form.vehicle_id.choices = vehicle_choices(available)
    form.driver_id.choices = driver_choices()
    if request.method == "GET":
        vehicle = db.session.get(Vehicle, request.args.get("vehicle_id", 0, type=int))
        if vehicle is not None and vehicle in available:
            form.vehicle_id.data = vehicle.id
            form.start_mileage.data = vehicle.current_mileage
            form.driver_id.data = vehicle.responsible_driver_id or 0
    if form.validate_on_submit():
        try:
            trip = start_trip(
                vehicle=db.session.get(Vehicle, form.vehicle_id.data),
                driver=db.session.get(Driver, form.driver_id.data),
                started_at=form.started_at.data,
                start_mileage=form.start_mileage.data,
                purpose=form.purpose.data,
                notes=form.notes.data,
                user=current_user,
            )
        except BusinessRuleError as error:
            show_business_error(form, error)
        else:
            db.session.flush()
            log_action("create", "trip", trip.id, f"{trip.vehicle.reg_number} → {trip.driver.full_name}")
            if save_changes(f"МПС {trip.vehicle.reg_number} е отпуснато на {trip.driver.full_name}."):
                return redirect(url_for(".open_trips"))
    return render_template(
        "form.html", form=form, title="Отпускане на МПС (начало на курс)", submit_label="Отпусни МПС",
        cancel_url=url_for(".open_trips"),
        intro="В списъка са само активните МПС, които в момента не са в курс.",
    )


@bp.route("/<int:trip_id>/return", methods=["GET", "POST"])
@permission_required("trips.manage")
def return_vehicle(trip_id: int):
    trip = db.get_or_404(Trip, trip_id)
    if not trip.is_open:
        return redirect(url_for(".index"))
    form = TripReturnForm()
    if request.method == "GET":
        form.end_mileage.data = trip.vehicle.current_mileage
    if form.validate_on_submit():
        try:
            finish_trip(
                trip,
                ended_at=form.ended_at.data,
                end_mileage=form.end_mileage.data,
                notes=form.notes.data,
                fuel_quantity=form.fuel_quantity.data,
                fuel_price=form.fuel_price.data,
                fuel_full_tank=form.fuel_full_tank.data,
                fuel_station=form.fuel_station.data,
                problem=form.problem.data,
                user=current_user,
            )
        except BusinessRuleError as error:
            show_business_error(form, error)
        else:
            log_action("update", "trip", trip.id, f"Връщане на {trip.vehicle.reg_number}, {trip.distance} км")
            message = f"{trip.vehicle.reg_number} е върнато. Изминати: {trip.distance} км."
            if trip.vehicle.status == VehicleStatus.IN_REPAIR:
                message += " МПС е отбелязано „В ремонт“."
            if save_changes(message):
                return redirect(url_for(".open_trips"))
    return render_template(
        "form.html", form=form,
        title=f"Връщане от курс – {trip.vehicle.reg_number}",
        intro=f"Шофьор: {trip.driver.full_name}. Начало: {trip.started_at:%d.%m.%Y %H:%M} при "
              f"{format_km(trip.start_mileage)}. Цел: {trip.purpose}.",
        submit_label="Отчети връщането", cancel_url=url_for(".open_trips"),
    )


@bp.route("/<int:trip_id>/edit", methods=["GET", "POST"])
@permission_required("trips.manage")
def edit(trip_id: int):
    trip = db.get_or_404(Trip, trip_id)
    form = TripEditForm(obj=trip)
    if form.validate_on_submit():
        try:
            update_trip(
                trip,
                started_at=form.started_at.data,
                start_mileage=form.start_mileage.data,
                ended_at=form.ended_at.data,
                end_mileage=form.end_mileage.data,
                purpose=form.purpose.data,
                notes=form.notes.data,
            )
        except BusinessRuleError as error:
            show_business_error(form, error)
        else:
            log_action("update", "trip", trip.id, trip.vehicle.reg_number)
            if save_changes("Курсът е обновен."):
                return redirect(url_for(".index"))
    return render_template(
        "form.html", form=form, title=f"Редакция на курс – {trip.vehicle.reg_number}, {trip.driver.full_name}",
        submit_label="Запази", cancel_url=url_for(".index"),
    )


@bp.route("/<int:trip_id>/delete", methods=["GET", "POST"])
@permission_required("records.delete")
def delete(trip_id: int):
    trip = db.get_or_404(Trip, trip_id)
    if request.method == "POST":
        details = f"{trip.vehicle.reg_number}, {trip.driver.full_name}, {trip.started_at:%d.%m.%Y}"
        delete_trip(trip)
        log_action("delete", "trip", trip_id, details)
        save_changes("Курсът е изтрит.")
        return redirect(url_for(".index"))
    return confirm_delete(
        title="Изтриване на курс",
        message=f"Да се изтрие ли курсът на {trip.vehicle.reg_number} с {trip.driver.full_name} "
                f"от {trip.started_at:%d.%m.%Y %H:%M}?",
        details=["Историята на ползване на МПС ще бъде променена.", "Действието е необратимо."],
        cancel_url=url_for(".index"),
    )
