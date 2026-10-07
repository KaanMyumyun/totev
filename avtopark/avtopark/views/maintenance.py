"""Maintenance and repair records, activity types and per-vehicle maintenance plans."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, select

from ..extensions import db
from ..forms import MaintenanceRecordForm, MaintenanceTypeForm, PlanForm
from ..models import MaintenancePlan, MaintenanceRecord, MaintenanceType, Vehicle
from ..services.audit import log_action
from ..services.errors import BusinessRuleError
from ..services.records import delete_mileage_record, save_maintenance_record, save_plan
from .common import (
    confirm_delete,
    date_arg,
    paginate,
    permission_required,
    save_changes,
    show_business_error,
    type_choices,
    vehicle_choices,
)

bp = Blueprint("maintenance", __name__)


# --- Records ----------------------------------------------------------------------

@bp.route("/")
def records():
    vehicle_id = request.args.get("vehicle_id", type=int)
    type_id = request.args.get("type_id", type=int)
    date_from = date_arg("from", None)
    date_to = date_arg("to", None)
    stmt = select(MaintenanceRecord)
    if vehicle_id:
        stmt = stmt.where(MaintenanceRecord.vehicle_id == vehicle_id)
    if type_id:
        stmt = stmt.where(MaintenanceRecord.maintenance_type_id == type_id)
    if date_from:
        stmt = stmt.where(MaintenanceRecord.performed_on >= date_from)
    if date_to:
        stmt = stmt.where(MaintenanceRecord.performed_on <= date_to)
    filtered = stmt.subquery()
    total = db.session.scalar(select(func.sum(filtered.c.cost)))
    page = paginate(stmt.order_by(MaintenanceRecord.performed_on.desc(), MaintenanceRecord.id.desc()))
    return render_template(
        "maintenance/records.html",
        page=page,
        total=total,
        vehicles=db.session.scalars(select(Vehicle).order_by(Vehicle.reg_number)).all(),
        types=db.session.scalars(select(MaintenanceType).order_by(MaintenanceType.name)).all(),
        filters={"vehicle_id": vehicle_id, "type_id": type_id, "from": date_from, "to": date_to},
    )


def _record_form_choices(form, record=None):
    form.vehicle_id.choices = vehicle_choices()
    form.maintenance_type_id.choices = type_choices(keep_id=record.maintenance_type_id if record else None)


def _save_record(form, record):
    vehicle = db.session.get(Vehicle, form.vehicle_id.data)
    mtype = db.session.get(MaintenanceType, form.maintenance_type_id.data)
    return save_maintenance_record(
        record,
        vehicle=vehicle,
        maintenance_type=mtype,
        performed_on=form.performed_on.data,
        mileage=form.mileage.data,
        description=form.description.data,
        cost=form.cost.data,
        service_provider=form.service_provider.data,
        user=current_user,
        mark_vehicle_active=form.mark_vehicle_active.data,
    )


@bp.route("/new", methods=["GET", "POST"])
@permission_required("maintenance.manage")
def create_record():
    form = MaintenanceRecordForm()
    _record_form_choices(form)
    if request.method == "GET":
        vehicle = db.session.get(Vehicle, request.args.get("vehicle_id", 0, type=int))
        if vehicle is not None:
            form.vehicle_id.data = vehicle.id
            form.mileage.data = vehicle.current_mileage
            form.mark_vehicle_active.data = vehicle.status == "in_repair"
        form.maintenance_type_id.data = request.args.get("type_id", 0, type=int)
    if form.validate_on_submit():
        try:
            record = _save_record(form, None)
        except BusinessRuleError as error:
            show_business_error(form, error)
        else:
            db.session.flush()
            log_action(
                "create", "maintenance", record.id,
                f"{record.vehicle.reg_number}: {record.maintenance_type.name}, {record.cost} €",
            )
            if save_changes("Записът за обслужване е добавен."):
                return redirect(url_for("vehicles.detail", vehicle_id=record.vehicle_id))
    return render_template(
        "form.html", form=form, title="Нов запис за обслужване / ремонт", submit_label="Запази",
        cancel_url=url_for("vehicles.detail", vehicle_id=form.vehicle_id.data)
        if form.vehicle_id.data else url_for(".records"),
        intro="Изберете МПС – текущият му километраж се попълва автоматично.",
    )


@bp.route("/<int:record_id>/edit", methods=["GET", "POST"])
@permission_required("maintenance.manage")
def edit_record(record_id: int):
    record = db.get_or_404(MaintenanceRecord, record_id)
    form = MaintenanceRecordForm(obj=record)
    _record_form_choices(form, record)
    if form.validate_on_submit():
        try:
            _save_record(form, record)
        except BusinessRuleError as error:
            show_business_error(form, error)
        else:
            log_action("update", "maintenance", record.id, record.vehicle.reg_number)
            if save_changes("Записът е обновен."):
                return redirect(url_for("vehicles.detail", vehicle_id=record.vehicle_id))
    return render_template(
        "form.html", form=form, title="Редакция на запис за обслужване", submit_label="Запази",
        cancel_url=url_for("vehicles.detail", vehicle_id=record.vehicle_id),
    )


@bp.route("/<int:record_id>/delete", methods=["GET", "POST"])
@permission_required("records.delete")
def delete_record(record_id: int):
    record = db.get_or_404(MaintenanceRecord, record_id)
    vehicle_id = record.vehicle_id
    if request.method == "POST":
        details = f"{record.vehicle.reg_number}: {record.maintenance_type.name} от {record.performed_on:%d.%m.%Y}"
        delete_mileage_record(record)
        log_action("delete", "maintenance", record_id, details)
        save_changes("Записът е изтрит.")
        return redirect(url_for("vehicles.detail", vehicle_id=vehicle_id))
    return confirm_delete(
        title="Изтриване на запис за обслужване",
        message=f"Да се изтрие ли „{record.maintenance_type.name}“ от {record.performed_on:%d.%m.%Y} "
                f"за {record.vehicle.reg_number}?",
        details=["Записът участва в историята и в отчетите за разходи.", "Действието е необратимо."],
        cancel_url=url_for("vehicles.detail", vehicle_id=vehicle_id),
    )


# --- Maintenance types --------------------------------------------------------------

@bp.route("/types")
def types():
    all_types = db.session.scalars(select(MaintenanceType).order_by(MaintenanceType.name)).all()
    return render_template("maintenance/types.html", types=all_types)


@bp.route("/types/new", methods=["GET", "POST"])
@bp.route("/types/<int:type_id>/edit", methods=["GET", "POST"])
@permission_required("types.manage")
def edit_type(type_id: int | None = None):
    mtype = db.get_or_404(MaintenanceType, type_id) if type_id else None
    form = MaintenanceTypeForm(obj=mtype)
    if form.validate_on_submit():
        duplicate = db.session.scalar(select(MaintenanceType).where(MaintenanceType.name == form.name.data.strip()))
        if duplicate is not None and duplicate is not mtype:
            form.name.errors.append("Вече има дейност с това име.")
        else:
            if mtype is None:
                mtype = MaintenanceType()
                db.session.add(mtype)
            mtype.name = form.name.data.strip()
            mtype.description = (form.description.data or "").strip() or None
            mtype.default_interval_km = form.default_interval_km.data or None
            mtype.default_interval_days = form.default_interval_days.data or None
            mtype.auto_assign = form.auto_assign.data and mtype.is_periodic
            mtype.active = form.active.data
            db.session.flush()
            log_action("update" if type_id else "create", "maintenance_type", mtype.id, mtype.name)
            if save_changes("Видът дейност е запазен."):
                return redirect(url_for(".types"))
    return render_template(
        "form.html", form=form, title="Редакция на вид дейност" if type_id else "Нов вид дейност",
        submit_label="Запази", cancel_url=url_for(".types"),
    )


@bp.route("/types/<int:type_id>/delete", methods=["GET", "POST"])
@permission_required("types.manage")
def delete_type(type_id: int):
    mtype = db.get_or_404(MaintenanceType, type_id)
    if mtype.is_used():
        flash("Дейността се използва в записи или планове. Вместо да я изтривате, я маркирайте като неактивна.", "warning")
        return redirect(url_for(".types"))
    if request.method == "POST":
        name = mtype.name
        db.session.delete(mtype)
        log_action("delete", "maintenance_type", type_id, name)
        save_changes(f"Дейността „{name}“ е изтрита.")
        return redirect(url_for(".types"))
    return confirm_delete(
        title="Изтриване на вид дейност",
        message=f"Да се изтрие ли дейността „{mtype.name}“?",
        cancel_url=url_for(".types"),
    )


# --- Maintenance plans --------------------------------------------------------------

def _save_plan_from_form(form, plan, vehicle):
    return save_plan(
        plan,
        vehicle=vehicle,
        maintenance_type=db.session.get(MaintenanceType, form.maintenance_type_id.data),
        interval_km=form.interval_km.data,
        interval_days=form.interval_days.data,
        baseline_date=form.baseline_date.data,
        baseline_mileage=form.baseline_mileage.data,
        active=form.active.data,
        notes=form.notes.data,
    )


@bp.route("/plans/new/<int:vehicle_id>", methods=["GET", "POST"])
@bp.route("/plans/<int:plan_id>/edit", methods=["GET", "POST"])
@permission_required("plans.manage")
def edit_plan(vehicle_id: int | None = None, plan_id: int | None = None):
    plan = db.get_or_404(MaintenancePlan, plan_id) if plan_id else None
    vehicle = plan.vehicle if plan else db.get_or_404(Vehicle, vehicle_id)
    form = PlanForm(obj=plan)
    form.maintenance_type_id.choices = type_choices(
        keep_id=plan.maintenance_type_id if plan else None, periodic_only=True
    )
    if request.method == "GET" and plan is None:
        form.baseline_mileage.data = vehicle.current_mileage
        selected = db.session.get(MaintenanceType, request.args.get("type_id", 0, type=int))
        if selected is not None:
            form.maintenance_type_id.data = selected.id
            form.interval_km.data = selected.default_interval_km
            form.interval_days.data = selected.default_interval_days
    if form.validate_on_submit():
        try:
            plan = _save_plan_from_form(form, plan, vehicle)
        except BusinessRuleError as error:
            show_business_error(form, error)
        else:
            db.session.flush()
            log_action("update" if plan_id else "create", "plan", plan.id,
                       f"{vehicle.reg_number}: {plan.maintenance_type.name}")
            if save_changes("Планът за поддръжка е запазен."):
                return redirect(url_for("vehicles.detail", vehicle_id=vehicle.id))
    return render_template(
        "form.html", form=form,
        title=f"{'Редакция на дейност от плана' if plan_id else 'Нова дейност в плана'} – {vehicle.reg_number}",
        submit_label="Запази", cancel_url=url_for("vehicles.detail", vehicle_id=vehicle.id),
        intro="Ако дейността няма записи в системата, напомнянето се изчислява от „последно извършено“.",
    )


@bp.route("/plans/<int:plan_id>/delete", methods=["GET", "POST"])
@permission_required("plans.manage")
def delete_plan(plan_id: int):
    plan = db.get_or_404(MaintenancePlan, plan_id)
    vehicle = plan.vehicle
    if request.method == "POST":
        details = f"{vehicle.reg_number}: {plan.maintenance_type.name}"
        db.session.delete(plan)
        log_action("delete", "plan", plan_id, details)
        save_changes("Дейността е премахната от плана.")
        return redirect(url_for("vehicles.detail", vehicle_id=vehicle.id))
    return confirm_delete(
        title="Премахване от плана",
        message=f"Да се премахне ли „{plan.maintenance_type.name}“ от плана на {vehicle.reg_number}?",
        details=["Записите за извършени дейности остават в историята."],
        cancel_url=url_for("vehicles.detail", vehicle_id=vehicle.id),
    )
