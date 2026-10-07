"""Refuelling records."""
from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, select

from ..extensions import db
from ..forms import FuelForm
from ..models import Driver, FuelRecord, Vehicle
from ..services.audit import log_action
from ..services.errors import BusinessRuleError
from ..services.records import delete_mileage_record, save_fuel_record
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

bp = Blueprint("fuel", __name__)


@bp.route("/")
def index():
    vehicle_id = request.args.get("vehicle_id", type=int)
    driver_id = request.args.get("driver_id", type=int)
    date_from = date_arg("from", None)
    date_to = date_arg("to", None)
    stmt = select(FuelRecord)
    if vehicle_id:
        stmt = stmt.where(FuelRecord.vehicle_id == vehicle_id)
    if driver_id:
        stmt = stmt.where(FuelRecord.driver_id == driver_id)
    if date_from:
        stmt = stmt.where(FuelRecord.filled_on >= date_from)
    if date_to:
        stmt = stmt.where(FuelRecord.filled_on <= date_to)
    filtered = stmt.subquery()
    totals = db.session.execute(
        select(func.count(), func.sum(filtered.c.quantity), func.sum(filtered.c.total_cost))
    ).one()
    page = paginate(stmt.order_by(FuelRecord.filled_on.desc(), FuelRecord.mileage.desc()))
    return render_template(
        "fuel/index.html",
        page=page,
        totals=totals,
        vehicles=db.session.scalars(select(Vehicle).order_by(Vehicle.reg_number)).all(),
        drivers=db.session.scalars(select(Driver).order_by(Driver.last_name)).all(),
        filters={"vehicle_id": vehicle_id, "driver_id": driver_id, "from": date_from, "to": date_to},
    )


def _choices(form, record=None):
    form.vehicle_id.choices = vehicle_choices()
    form.driver_id.choices = driver_choices(
        placeholder="— не е посочен —", keep_id=record.driver_id if record else None
    )


def _save(form, record):
    return save_fuel_record(
        record,
        vehicle=db.session.get(Vehicle, form.vehicle_id.data),
        driver=db.session.get(Driver, form.driver_id.data) if form.driver_id.data else None,
        filled_on=form.filled_on.data,
        mileage=form.mileage.data,
        quantity=form.quantity.data,
        price_per_unit=form.price_per_unit.data,
        full_tank=form.full_tank.data,
        station=form.station.data,
        user=current_user,
    )


@bp.route("/new", methods=["GET", "POST"])
@permission_required("fuel.manage")
def create():
    form = FuelForm()
    _choices(form)
    if request.method == "GET":
        vehicle = db.session.get(Vehicle, request.args.get("vehicle_id", 0, type=int))
        if vehicle is not None:
            form.vehicle_id.data = vehicle.id
            form.mileage.data = vehicle.current_mileage
            form.driver_id.data = vehicle.responsible_driver_id or 0
    if form.validate_on_submit():
        try:
            record = _save(form, None)
        except BusinessRuleError as error:
            show_business_error(form, error)
        else:
            db.session.flush()
            log_action("create", "fuel", record.id,
                       f"{record.vehicle.reg_number}: {record.quantity} {record.vehicle.fuel_unit}, {record.total_cost} €")
            if save_changes(f"Зареждането е записано. Обща сума: {record.total_cost} €."):
                return redirect(url_for(".index"))
    return render_template(
        "form.html", form=form, title="Ново зареждане", submit_label="Запази",
        cancel_url=url_for(".index"),
        intro="Общата сума се изчислява автоматично от количеството и единичната цена.",
    )


@bp.route("/<int:record_id>/edit", methods=["GET", "POST"])
@permission_required("fuel.manage")
def edit(record_id: int):
    record = db.get_or_404(FuelRecord, record_id)
    form = FuelForm(obj=record)
    _choices(form, record)
    if request.method == "GET":
        form.driver_id.data = record.driver_id or 0
    if form.validate_on_submit():
        try:
            _save(form, record)
        except BusinessRuleError as error:
            show_business_error(form, error)
        else:
            log_action("update", "fuel", record.id, record.vehicle.reg_number)
            if save_changes("Зареждането е обновено."):
                return redirect(url_for(".index"))
    return render_template(
        "form.html", form=form, title="Редакция на зареждане", submit_label="Запази",
        cancel_url=url_for(".index"),
    )


@bp.route("/<int:record_id>/delete", methods=["GET", "POST"])
@permission_required("records.delete")
def delete(record_id: int):
    record = db.get_or_404(FuelRecord, record_id)
    if request.method == "POST":
        details = f"{record.vehicle.reg_number}: {record.filled_on:%d.%m.%Y}, {record.total_cost} €"
        delete_mileage_record(record)
        log_action("delete", "fuel", record_id, details)
        save_changes("Зареждането е изтрито.")
        return redirect(url_for(".index"))
    return confirm_delete(
        title="Изтриване на зареждане",
        message=f"Да се изтрие ли зареждането на {record.vehicle.reg_number} от {record.filled_on:%d.%m.%Y} "
                f"({record.quantity} {record.vehicle.fuel_unit}, {record.total_cost} €)?",
        cancel_url=url_for(".index"),
    )
