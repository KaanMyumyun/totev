"""Vehicle registry: list, details, create, edit, status, odometer readings, delete."""
from datetime import date, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, or_, select

from ..extensions import db
from ..forms import OdometerForm, StatusForm, VehicleForm
from ..models import (
    OdometerReading,
    Vehicle,
    VEHICLE_STATUS_LABELS,
    VEHICLE_TYPE_LABELS,
)
from ..services.audit import log_action
from ..services.costs import costs_by_vehicle
from ..services.errors import BusinessRuleError
from ..services.fuel import average_consumption
from ..services.odometer import average_daily_km
from ..services.records import add_odometer_reading, delete_mileage_record
from ..services.reminders import INSURANCE, MAINTENANCE, collect_reminders, worst_level_by_vehicle
from ..services.validators import PLATE_LETTERS
from ..services.vehicles import change_status, create_vehicle, delete_vehicle, update_vehicle
from .common import confirm_delete, driver_choices, permission_required, save_changes, show_business_error

bp = Blueprint("vehicles", __name__)

_LATIN_TO_CYRILLIC = str.maketrans("ABEKMHOPCTYX", PLATE_LETTERS)


def _form_data(form: VehicleForm) -> dict:
    return {
        "reg_number": form.reg_number.data,
        "make": form.make.data,
        "model": form.model.data,
        "year": form.year.data,
        "vehicle_type": form.vehicle_type.data,
        "fuel_type": form.fuel_type.data,
        "vin": form.vin.data,
        "in_service_since": form.in_service_since.data,
        "initial_mileage": form.initial_mileage.data,
        "responsible_driver_id": form.responsible_driver_id.data or None,
        "notes": form.notes.data,
    }


@bp.route("/")
def index():
    query = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    vehicle_type = request.args.get("type", "")
    stmt = select(Vehicle)
    if query:
        like = f"%{query}%"
        # Plates are stored with Cyrillic letters and spaces; match "CB4821" as well.
        plate = "".join(query.upper().split()).translate(_LATIN_TO_CYRILLIC)
        stmt = stmt.where(
            or_(
                func.replace(Vehicle.reg_number, " ", "").like(f"%{plate}%"),
                Vehicle.make.ilike(like),
                Vehicle.model.ilike(like),
                Vehicle.vin.ilike(like),
            )
        )
    if status in VEHICLE_STATUS_LABELS:
        stmt = stmt.where(Vehicle.status == status)
    if vehicle_type in VEHICLE_TYPE_LABELS:
        stmt = stmt.where(Vehicle.vehicle_type == vehicle_type)
    vehicles = db.session.scalars(stmt.order_by(Vehicle.reg_number)).all()
    worst = worst_level_by_vehicle(collect_reminders(include_inactive=True))
    return render_template(
        "vehicles/index.html", vehicles=vehicles, worst=worst, q=query, status=status, vehicle_type=vehicle_type
    )


@bp.route("/new", methods=["GET", "POST"])
@permission_required("vehicles.manage")
def create():
    form = VehicleForm()
    form.responsible_driver_id.choices = driver_choices(placeholder="— няма —")
    if form.validate_on_submit():
        try:
            vehicle = create_vehicle(**_form_data(form))
        except BusinessRuleError as error:
            show_business_error(form, error)
        else:
            log_action("create", "vehicle", vehicle.id, vehicle.display_name)
            if save_changes(
                f"МПС {vehicle.reg_number} е добавено. Проверете плана за поддръжка и въведете "
                "кога последно е извършена всяка дейност."
            ):
                return redirect(url_for(".detail", vehicle_id=vehicle.id))
    return render_template(
        "form.html", form=form, title="Ново МПС", submit_label="Запази",
        cancel_url=url_for(".index"),
        intro="Периодичните дейности по подразбиране (масло, технически преглед, гуми...) ще бъдат добавени автоматично в плана.",
    )


@bp.route("/<int:vehicle_id>")
def detail(vehicle_id: int):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    today = date.today()
    plan_rows = collect_reminders(
        today, vehicle=vehicle, kinds=(MAINTENANCE,), include_ok=True, include_inactive=True
    )
    insurance_rows = collect_reminders(
        today, vehicle=vehicle, kinds=(INSURANCE,), include_ok=True, include_inactive=True
    )
    year_costs = costs_by_vehicle(today - timedelta(days=365), today, vehicle.id)[0]
    # Prefixes keep the field ids of the two small forms on the page unique.
    status_form = StatusForm(prefix="st", status=vehicle.status, note=vehicle.status_note)
    reading_form = OdometerForm(prefix="odo", mileage=vehicle.current_mileage)
    return render_template(
        "vehicles/detail.html",
        vehicle=vehicle,
        plan_rows=plan_rows,
        insurance_rows=insurance_rows,
        year_costs=year_costs,
        consumption=average_consumption(vehicle.fuel_records),
        daily_km=average_daily_km(vehicle, today),
        status_form=status_form,
        reading_form=reading_form,
        open_trip=vehicle.open_trip,
    )


@bp.route("/<int:vehicle_id>/edit", methods=["GET", "POST"])
@permission_required("vehicles.manage")
def edit(vehicle_id: int):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    form = VehicleForm(obj=vehicle)
    form.responsible_driver_id.choices = driver_choices(
        placeholder="— няма —", keep_id=vehicle.responsible_driver_id
    )
    if request.method == "GET":
        form.responsible_driver_id.data = vehicle.responsible_driver_id or 0
    if form.validate_on_submit():
        try:
            update_vehicle(vehicle, **_form_data(form))
        except BusinessRuleError as error:
            show_business_error(form, error)
        else:
            log_action("update", "vehicle", vehicle.id, vehicle.display_name)
            if save_changes("Данните за МПС са обновени."):
                return redirect(url_for(".detail", vehicle_id=vehicle.id))
    return render_template(
        "form.html", form=form, title=f"Редакция на {vehicle.reg_number}", submit_label="Запази",
        cancel_url=url_for(".detail", vehicle_id=vehicle.id),
    )


@bp.route("/<int:vehicle_id>/status", methods=["POST"])
@permission_required("vehicles.status")
def status(vehicle_id: int):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    form = StatusForm(prefix="st")
    if form.validate_on_submit():
        try:
            change_status(vehicle, form.status.data, form.note.data)
        except BusinessRuleError as error:
            show_business_error(None, error)
        else:
            log_action("status", "vehicle", vehicle.id, f"{vehicle.reg_number}: {vehicle.status_label}")
            save_changes(f"Статусът на {vehicle.reg_number} е „{vehicle.status_label}“.")
    else:
        flash("Невалиден статус.", "danger")
    return redirect(url_for(".detail", vehicle_id=vehicle.id))


@bp.route("/<int:vehicle_id>/readings", methods=["POST"])
@permission_required("odometer.add")
def add_reading(vehicle_id: int):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    form = OdometerForm(prefix="odo")
    if form.validate_on_submit():
        try:
            reading = add_odometer_reading(
                vehicle, form.read_on.data, form.mileage.data, form.note.data, current_user
            )
        except BusinessRuleError as error:
            show_business_error(None, error)
        else:
            log_action("create", "odometer", vehicle.id, f"{vehicle.reg_number}: {reading.mileage} км")
            save_changes("Километражът е отчетен.")
    else:
        for errors in form.errors.values():
            for message in errors:
                flash(message, "danger")
    return redirect(url_for(".detail", vehicle_id=vehicle.id))


@bp.route("/readings/<int:reading_id>/delete", methods=["GET", "POST"])
@permission_required("records.delete")
def delete_reading(reading_id: int):
    reading = db.get_or_404(OdometerReading, reading_id)
    vehicle = reading.vehicle
    if request.method == "POST":
        delete_mileage_record(reading)
        log_action("delete", "odometer", vehicle.id, f"{vehicle.reg_number}: {reading.mileage} км")
        save_changes("Отчетът на километража е изтрит.")
        return redirect(url_for(".detail", vehicle_id=vehicle.id))
    return confirm_delete(
        title="Изтриване на отчет на километраж",
        message=f"Да се изтрие ли отчетът {reading.mileage} км от {reading.read_on:%d.%m.%Y} за {vehicle.reg_number}?",
        cancel_url=url_for(".detail", vehicle_id=vehicle.id),
    )


@bp.route("/<int:vehicle_id>/delete", methods=["GET", "POST"])
@permission_required("records.delete")
def delete(vehicle_id: int):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    if vehicle.has_history():
        flash(
            "МПС има история (ремонти, зареждания, курсове или застраховки) и не може да бъде изтрито. "
            "За да го извадите от употреба, сменете статуса му на „Спряно от движение“.",
            "warning",
        )
        return redirect(url_for(".detail", vehicle_id=vehicle.id))
    if request.method == "POST":
        try:
            name = vehicle.display_name
            delete_vehicle(vehicle)
        except BusinessRuleError as error:
            show_business_error(None, error)
            return redirect(url_for(".detail", vehicle_id=vehicle_id))
        log_action("delete", "vehicle", vehicle_id, name)
        if save_changes(f"МПС {name} е изтрито."):
            return redirect(url_for(".index"))
        return redirect(url_for(".detail", vehicle_id=vehicle_id))
    return confirm_delete(
        title="Изтриване на МПС",
        message=f"Сигурни ли сте, че искате да изтриете {vehicle.display_name}?",
        details=["Ще бъде изтрит и планът за поддръжка на МПС.", "Действието е необратимо."],
        cancel_url=url_for(".detail", vehicle_id=vehicle.id),
    )
