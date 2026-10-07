"""Drivers and their usage history."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import or_, select

from ..extensions import db
from ..forms import DriverForm
from ..models import Driver, FuelRecord, Vehicle
from ..services.audit import log_action
from .common import confirm_delete, permission_required, save_changes

bp = Blueprint("drivers", __name__)


@bp.route("/")
def index():
    query = request.args.get("q", "").strip()
    show = request.args.get("show", "active")
    stmt = select(Driver)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            or_(Driver.first_name.like(like), Driver.last_name.like(like), Driver.license_number.like(like))
        )
    if show == "active":
        stmt = stmt.where(Driver.active.is_(True))
    elif show == "inactive":
        stmt = stmt.where(Driver.active.is_(False))
    drivers = db.session.scalars(stmt.order_by(Driver.last_name, Driver.first_name)).all()
    return render_template("drivers/index.html", drivers=drivers, q=query, show=show)


@bp.route("/<int:driver_id>")
def detail(driver_id: int):
    driver = db.get_or_404(Driver, driver_id)
    vehicles = db.session.scalars(select(Vehicle).where(Vehicle.responsible_driver_id == driver.id)).all()
    fuel_records = db.session.scalars(
        select(FuelRecord).where(FuelRecord.driver_id == driver.id).order_by(FuelRecord.filled_on.desc()).limit(20)
    ).all()
    total_km = sum(t.distance or 0 for t in driver.trips)
    return render_template(
        "drivers/detail.html", driver=driver, vehicles=vehicles, fuel_records=fuel_records, total_km=total_km
    )


def _apply(form: DriverForm, driver: Driver) -> None:
    driver.first_name = form.first_name.data.strip()
    driver.last_name = form.last_name.data.strip()
    driver.phone = (form.phone.data or "").strip() or None
    driver.email = (form.email.data or "").strip() or None
    driver.license_number = form.license_number.data.strip().upper()
    driver.license_categories = form.license_categories.data.strip().upper()
    driver.license_expiry = form.license_expiry.data
    driver.active = form.active.data
    driver.notes = (form.notes.data or "").strip() or None


@bp.route("/new", methods=["GET", "POST"])
@bp.route("/<int:driver_id>/edit", methods=["GET", "POST"])
@permission_required("drivers.manage")
def edit(driver_id: int | None = None):
    driver = db.get_or_404(Driver, driver_id) if driver_id else None
    form = DriverForm(obj=driver)
    if form.validate_on_submit():
        number = form.license_number.data.strip().upper()
        duplicate = db.session.scalar(select(Driver).where(Driver.license_number == number))
        if duplicate is not None and duplicate is not driver:
            form.license_number.errors.append("Вече има шофьор с този номер на книжка.")
        elif driver is not None and not form.active.data and any(t.is_open for t in driver.trips):
            form.active.errors.append("Шофьорът е в курс – първо приключете курса.")
        else:
            if driver is None:
                driver = Driver()
                db.session.add(driver)
            _apply(form, driver)
            db.session.flush()
            log_action("update" if driver_id else "create", "driver", driver.id, driver.full_name)
            if save_changes("Данните за шофьора са запазени."):
                return redirect(url_for(".detail", driver_id=driver.id))
    return render_template(
        "form.html", form=form, title="Редакция на шофьор" if driver_id else "Нов шофьор",
        submit_label="Запази",
        cancel_url=url_for(".detail", driver_id=driver_id) if driver_id else url_for(".index"),
        intro="Съхраняват се само данните, необходими за управлението на автопарка (без ЕГН).",
    )


@bp.route("/<int:driver_id>/delete", methods=["GET", "POST"])
@permission_required("records.delete")
def delete(driver_id: int):
    driver = db.get_or_404(Driver, driver_id)
    if driver.has_history():
        flash("Шофьорът има курсове или зареждания в историята и не може да бъде изтрит. "
              "Маркирайте го като неактивен.", "warning")
        return redirect(url_for(".detail", driver_id=driver.id))
    if request.method == "POST":
        name = driver.full_name
        db.session.delete(driver)
        log_action("delete", "driver", driver_id, name)
        if save_changes(f"Шофьорът {name} е изтрит."):
            return redirect(url_for(".index"))
        return redirect(url_for(".detail", driver_id=driver_id))
    return confirm_delete(
        title="Изтриване на шофьор",
        message=f"Да се изтрие ли шофьорът {driver.full_name}?",
        details=["Личните му данни ще бъдат премахнати от системата.", "Действието е необратимо."],
        cancel_url=url_for(".detail", driver_id=driver.id),
    )
