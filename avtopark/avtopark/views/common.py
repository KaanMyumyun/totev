"""Helpers shared by the views."""
from datetime import date
from functools import wraps
from urllib.parse import urlsplit

from flask import abort, current_app, flash, render_template, request
from flask_login import current_user
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Driver, MaintenanceType, Vehicle
from ..services.errors import BusinessRuleError


def permission_required(permission: str):
    """Allow the view only to roles that have `permission` (see permissions.py)."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return current_app.login_manager.unauthorized()
            if not current_user.can(permission):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def vehicle_choices(vehicles=None, placeholder: str = "— изберете МПС —"):
    """Select options with the current mileage, used by JavaScript to prefill forms."""
    if vehicles is None:
        vehicles = db.session.scalars(select(Vehicle).order_by(Vehicle.reg_number))
    choices = [(0, placeholder, {})] if placeholder else []
    for vehicle in vehicles:
        choices.append(
            (
                vehicle.id,
                f"{vehicle.display_name} ({vehicle.status_label.lower()})"
                if vehicle.status != "active"
                else vehicle.display_name,
                {"data-mileage": vehicle.current_mileage, "data-unit": vehicle.fuel_unit},
            )
        )
    return choices


def driver_choices(*, only_active: bool = True, placeholder: str = "— изберете шофьор —", keep_id=None):
    stmt = select(Driver).order_by(Driver.last_name, Driver.first_name)
    if only_active:
        stmt = stmt.where((Driver.active.is_(True)) | (Driver.id == (keep_id or 0)))
    return [(0, placeholder)] + [(d.id, f"{d.full_name} ({d.license_categories})") for d in db.session.scalars(stmt)]


def type_choices(*, keep_id=None, periodic_only: bool = False):
    stmt = select(MaintenanceType).where(
        (MaintenanceType.active.is_(True)) | (MaintenanceType.id == (keep_id or 0))
    ).order_by(MaintenanceType.name)
    types = [t for t in db.session.scalars(stmt) if t.is_periodic or not periodic_only]
    return [(0, "— изберете дейност —")] + [(t.id, t.name) for t in types]


def show_business_error(form, error: BusinessRuleError) -> None:
    """Undo partial changes and show the error next to its field (or as a message)."""
    db.session.rollback()
    field = getattr(form, error.field, None) if (form is not None and error.field) else None
    if field is not None:
        field.errors = [*field.errors, error.message]
    else:
        flash(error.message, "danger")


def save_changes(success_message: str | None = None) -> bool:
    """Commit; integrity violations from the database become a friendly message."""
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        current_app.logger.warning("Integrity error on %s", request.path)
        flash(
            "Записът не може да бъде съхранен, защото нарушава целостта на данните "
            "(дублиран номер или връзка към несъществуващ запис).",
            "danger",
        )
        return False
    if success_message:
        flash(success_message, "success")
    return True


def confirm_delete(*, title: str, message: str, cancel_url: str, details=None):
    """Confirmation page shown before any delete (the delete itself is a POST with CSRF token)."""
    return render_template(
        "confirm_delete.html", title=title, message=message, cancel_url=cancel_url, details=details or []
    )


def date_arg(name: str, default: date) -> date:
    value = request.args.get(name, "").strip()
    if not value:
        return default
    try:
        return date.fromisoformat(value)
    except ValueError:
        flash("Невалидна дата във филтъра – използвана е стойността по подразбиране.", "warning")
        return default


def int_arg(name: str, default=None, minimum: int | None = None, maximum: int | None = None):
    value = request.args.get(name, type=int)
    if value is None:
        return default
    if minimum is not None and value < minimum:
        return minimum
    if maximum is not None and value > maximum:
        return maximum
    return value


def period_args(default_from: date, default_to: date):
    date_from = date_arg("from", default_from)
    date_to = date_arg("to", default_to)
    if date_from > date_to:
        flash("Началната дата е след крайната – датите са разменени.", "warning")
        date_from, date_to = date_to, date_from
    return date_from, date_to


def is_safe_redirect(target: str | None) -> bool:
    """Only relative URLs on this site are allowed after login (no open redirect)."""
    if not target:
        return False
    parts = urlsplit(target)
    return not parts.scheme and not parts.netloc and target.startswith("/") and not target.startswith("//")


def paginate(stmt, per_page: int | None = None):
    return db.paginate(
        stmt,
        page=request.args.get("page", 1, type=int),
        per_page=per_page or current_app.config["ITEMS_PER_PAGE"],
        error_out=False,
    )
