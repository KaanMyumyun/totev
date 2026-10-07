"""Administration: user accounts and the audit log."""
from datetime import datetime

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from ..extensions import db
from ..forms import UserForm
from ..models import AuditLog, User
from ..permissions import Role
from ..services.audit import ACTION_LABELS, log_action
from ..services.errors import BusinessRuleError
from ..services.validators import check_password_strength
from .common import paginate, permission_required, save_changes, show_business_error

bp = Blueprint("admin", __name__)


@bp.route("/users")
@permission_required("users.manage")
def users():
    all_users = db.session.scalars(select(User).order_by(User.username)).all()
    return render_template("admin/users.html", users=all_users, now=datetime.now())


@bp.route("/users/new", methods=["GET", "POST"])
@bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@permission_required("users.manage")
def edit_user(user_id: int | None = None):
    user = db.get_or_404(User, user_id) if user_id else None
    form = UserForm(obj=user)
    if request.method == "GET" and user is not None:
        form.password.data = ""
    if form.validate_on_submit():
        username = form.username.data.strip()
        duplicate = db.session.scalar(select(User).where(User.username == username))
        if duplicate is not None and duplicate is not user:
            form.username.errors.append("Потребителското име е заето.")
        elif user is None and not form.password.data:
            form.password.errors.append("Задайте парола на новия потребител.")
        elif user is not None and user.id == current_user.id and (
            not form.active.data or form.role.data != Role.ADMIN
        ):
            form.role.errors.append("Не можете да деактивирате собствения си профил или да отнемете своята администраторска роля.")
        else:
            try:
                if form.password.data:
                    check_password_strength(form.password.data)
            except BusinessRuleError as error:
                show_business_error(form, error)
            else:
                if user is None:
                    user = User()
                    db.session.add(user)
                user.username = username
                user.full_name = form.full_name.data.strip()
                user.role = form.role.data
                user.active = form.active.data
                if form.password.data:
                    user.set_password(form.password.data)
                    user.failed_logins = 0
                    user.locked_until = None
                db.session.flush()
                log_action("update" if user_id else "create", "user", user.id, f"{user.username} ({user.role_label})")
                if save_changes("Потребителят е запазен."):
                    return redirect(url_for(".users"))
    return render_template(
        "form.html", form=form, title="Редакция на потребител" if user_id else "Нов потребител",
        submit_label="Запази", cancel_url=url_for(".users"),
    )


@bp.route("/audit")
@permission_required("audit.view")
def audit():
    action = request.args.get("action", "")
    stmt = select(AuditLog)
    if action in ACTION_LABELS:
        stmt = stmt.where(AuditLog.action == action)
    page = paginate(stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()), per_page=50)
    return render_template("admin/audit.html", page=page, action=action, action_labels=ACTION_LABELS)
