"""Login, logout and password change."""
import math
from datetime import datetime, timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from ..forms import ChangePasswordForm, LoginForm
from ..models import User
from ..services.audit import log_action
from ..services.errors import BusinessRuleError
from ..services.validators import check_password_strength
from .common import is_safe_redirect, show_business_error

bp = Blueprint("auth", __name__)

_dummy_hash: str | None = None


def _spend_hash_time(password: str) -> None:
    """Check a dummy hash for unknown users so response times do not reveal valid usernames."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = generate_password_hash("not-a-real-password")
    check_password_hash(_dummy_hash, password)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data.strip()
        user = db.session.scalar(select(User).where(User.username == username))
        now = datetime.now()

        if user is not None and user.locked_until and user.locked_until > now:
            minutes = math.ceil((user.locked_until - now).total_seconds() / 60)
            flash(
                f"Профилът е временно заключен след няколко неуспешни опита за вход. "
                f"Опитайте отново след {minutes} мин.",
                "danger",
            )
            return render_template("auth/login.html", form=form)

        if user is None:
            _spend_hash_time(form.password.data)
        if user is None or not user.check_password(form.password.data) or not user.active:
            if user is not None:
                user.failed_logins += 1
                if user.failed_logins >= current_app.config["LOGIN_MAX_ATTEMPTS"]:
                    user.locked_until = now + timedelta(minutes=current_app.config["LOGIN_LOCK_MINUTES"])
                    user.failed_logins = 0
            log_action("login_failed", "user", user.id if user else None, f"Потребител: {username[:50]}")
            db.session.commit()
            # The same message for a wrong name and a wrong password.
            flash("Грешно потребителско име или парола.", "danger")
            return render_template("auth/login.html", form=form)

        user.failed_logins = 0
        user.locked_until = None
        user.last_login_at = now
        session.clear()
        login_user(user)
        session.permanent = True
        log_action("login", "user", user.id)
        db.session.commit()
        next_url = request.args.get("next")
        return redirect(next_url if is_safe_redirect(next_url) else url_for("main.dashboard"))
    return render_template("auth/login.html", form=form)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    log_action("logout", "user", current_user.id)
    db.session.commit()
    logout_user()
    session.clear()
    flash("Излязохте от системата.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/password", methods=["GET", "POST"])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            form.current_password.errors.append("Грешна текуща парола.")
        else:
            try:
                check_password_strength(form.new_password.data, field="new_password")
            except BusinessRuleError as error:
                show_business_error(form, error)
            else:
                current_user.set_password(form.new_password.data)
                log_action("update", "user", current_user.id, "Смяна на парола")
                db.session.commit()
                flash("Паролата е сменена.", "success")
                return redirect(url_for("main.dashboard"))
    return render_template(
        "form.html", form=form, title="Смяна на парола", submit_label="Смени паролата",
        cancel_url=url_for("main.dashboard"),
    )
