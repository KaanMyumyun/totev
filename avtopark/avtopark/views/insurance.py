"""Insurance policies (civil liability and casco)."""
from datetime import date

from flask import Blueprint, redirect, render_template, request, url_for
from sqlalchemy import select

from ..extensions import db
from ..forms import InsuranceForm
from ..models import InsurancePolicy, POLICY_TYPE_LABELS, Vehicle
from ..services.audit import log_action
from .common import confirm_delete, permission_required, save_changes, vehicle_choices

bp = Blueprint("insurance", __name__)


@bp.route("/")
def index():
    vehicle_id = request.args.get("vehicle_id", type=int)
    policy_type = request.args.get("type", "")
    show = request.args.get("show", "valid")
    today = date.today()
    stmt = select(InsurancePolicy)
    if vehicle_id:
        stmt = stmt.where(InsurancePolicy.vehicle_id == vehicle_id)
    if policy_type in POLICY_TYPE_LABELS:
        stmt = stmt.where(InsurancePolicy.policy_type == policy_type)
    if show == "valid":
        stmt = stmt.where(InsurancePolicy.end_date >= today)
    policies = db.session.scalars(stmt.order_by(InsurancePolicy.end_date)).all()
    return render_template(
        "insurance/index.html",
        policies=policies,
        vehicles=db.session.scalars(select(Vehicle).order_by(Vehicle.reg_number)).all(),
        filters={"vehicle_id": vehicle_id, "type": policy_type, "show": show},
        today=today,
    )


@bp.route("/new", methods=["GET", "POST"])
@bp.route("/<int:policy_id>/edit", methods=["GET", "POST"])
@permission_required("insurance.manage")
def edit(policy_id: int | None = None):
    policy = db.get_or_404(InsurancePolicy, policy_id) if policy_id else None
    form = InsuranceForm(obj=policy)
    form.vehicle_id.choices = vehicle_choices()
    if request.method == "GET" and policy is None:
        form.vehicle_id.data = request.args.get("vehicle_id", 0, type=int)
        if request.args.get("type") in POLICY_TYPE_LABELS:
            form.policy_type.data = request.args["type"]
    if form.validate_on_submit():
        insurer = form.insurer.data.strip()
        number = form.policy_number.data.strip()
        duplicate = db.session.scalar(
            select(InsurancePolicy).where(
                InsurancePolicy.insurer == insurer, InsurancePolicy.policy_number == number
            )
        )
        if duplicate is not None and duplicate is not policy:
            form.policy_number.errors.append("Вече има полица с този номер при този застраховател.")
        else:
            if policy is None:
                policy = InsurancePolicy()
                db.session.add(policy)
            policy.vehicle_id = form.vehicle_id.data
            policy.policy_type = form.policy_type.data
            policy.insurer = insurer
            policy.policy_number = number
            policy.start_date = form.start_date.data
            policy.end_date = form.end_date.data
            policy.premium = form.premium.data
            policy.notes = (form.notes.data or "").strip() or None
            db.session.flush()
            log_action("update" if policy_id else "create", "insurance", policy.id,
                       f"{policy.policy_type} {policy.policy_number}")
            if save_changes("Полицата е запазена."):
                return redirect(url_for("vehicles.detail", vehicle_id=policy.vehicle_id))
    return render_template(
        "form.html", form=form, title="Редакция на застраховка" if policy_id else "Нова застраховка",
        submit_label="Запази", cancel_url=url_for(".index"),
    )


@bp.route("/<int:policy_id>/delete", methods=["GET", "POST"])
@permission_required("records.delete")
def delete(policy_id: int):
    policy = db.get_or_404(InsurancePolicy, policy_id)
    if request.method == "POST":
        details = f"{policy.vehicle.reg_number}: {policy.type_label} № {policy.policy_number}"
        db.session.delete(policy)
        log_action("delete", "insurance", policy_id, details)
        save_changes("Полицата е изтрита.")
        return redirect(url_for(".index"))
    return confirm_delete(
        title="Изтриване на застраховка",
        message=f"Да се изтрие ли полица „{policy.type_label}“ № {policy.policy_number} "
                f"за {policy.vehicle.reg_number}?",
        details=["Премията ще изчезне и от отчетите за разходи."],
        cancel_url=url_for(".index"),
    )
