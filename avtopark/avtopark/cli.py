"""Command line tools: flask --app avtopark <command>."""
import os
from pathlib import Path

import click
from sqlalchemy import func, select

from .extensions import db
from .models import User, Vehicle
from .permissions import ROLE_LABELS, Role
from .seed import DEMO_PASSWORD, create_demo_data, seed_reference_data
from .services.backup import create_backup, restore_backup
from .services.errors import BusinessRuleError
from .services.formatting import days_left_text, km_left_text
from .services.reminders import OVERDUE, collect_reminders
from .services.validators import check_password_strength


def register_cli(app):
    for command in (init_db, seed_demo, create_user, backup, restore, check_reminders):
        app.cli.add_command(command)


def _ask_password() -> str:
    while True:
        password = click.prompt("Парола", hide_input=True, confirmation_prompt="Повторете паролата")
        try:
            check_password_strength(password)
            return password
        except BusinessRuleError as error:
            click.echo(error.message, err=True)


@click.command("init-db")
@click.option("--drop", is_flag=True, help="Изтрива всички таблици и данни преди създаването.")
def init_db(drop):
    """Създава таблиците, видовете дейности и първия администратор."""
    if drop:
        click.confirm("Всички данни ще бъдат изтрити. Продължаване?", abort=True)
        db.drop_all()
    db.create_all()
    added = seed_reference_data()
    click.echo(f"Таблиците са създадени. Добавени видове дейности: {added}.")
    if not db.session.scalar(select(func.count(User.id)).where(User.role == Role.ADMIN)):
        # Non-interactive (Docker): the password comes from ADMIN_PASSWORD, the name defaults to "admin".
        password = os.environ.get("ADMIN_PASSWORD")
        username = os.environ.get("ADMIN_USERNAME") or (
            "admin" if password else click.prompt("Потребител за администратор", default="admin")
        )
        if password:
            try:
                check_password_strength(password)
            except BusinessRuleError as error:
                raise click.ClickException(f"ADMIN_PASSWORD: {error.message}") from error
        else:
            password = _ask_password()
        user = User(username=username, full_name="Администратор", role=Role.ADMIN)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Създаден е администратор „{username}“.")


@click.command("seed-demo")
@click.option("--yes", is_flag=True, help="Без потвърждение (данните се изтриват).")
def seed_demo(yes):
    """Изтрива базата и я зарежда с демонстрационен автопарк."""
    if not yes:
        click.confirm("Базата ще бъде изтрита и заредена с демо данни. Продължаване?", abort=True)
    db.drop_all()
    db.create_all()
    create_demo_data()
    vehicles = db.session.scalar(select(func.count(Vehicle.id)))
    click.echo(f"Демо данните са заредени: {vehicles} МПС.")
    click.echo(f"Потребители: admin, manager, mechanic, logistic – парола „{DEMO_PASSWORD}“.")


@click.command("create-user")
@click.argument("username")
@click.option("--role", type=click.Choice(list(ROLE_LABELS)), default=Role.MANAGER, show_default=True)
@click.option("--full-name", default=None, help="Име и фамилия.")
def create_user(username, role, full_name):
    """Създава потребител с дадена роля."""
    if db.session.scalar(select(User).where(User.username == username)):
        raise click.ClickException("Потребителското име е заето.")
    user = User(username=username, full_name=full_name or username, role=role)
    user.set_password(_ask_password())
    db.session.add(user)
    db.session.commit()
    click.echo(f"Създаден е потребител „{username}“ ({ROLE_LABELS[role]}).")


@click.command("backup")
@click.option("--keep", type=int, default=None, help="Колко последни копия да се пазят.")
def backup(keep):
    """Прави резервно копие на базата данни."""
    try:
        path = create_backup(keep)
    except BusinessRuleError as error:
        raise click.ClickException(error.message) from error
    click.echo(f"Резервно копие: {path}")


@click.command("restore")
@click.argument("backup_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def restore(backup_file):
    """Възстановява базата от резервно копие (спрете приложението преди това)."""
    click.confirm("Текущите данни ще бъдат заменени. Продължаване?", abort=True)
    try:
        safety_copy = restore_backup(backup_file)
    except BusinessRuleError as error:
        raise click.ClickException(error.message) from error
    click.echo(f"Базата е възстановена. Предишното състояние е запазено в {safety_copy}")


@click.command("check-reminders")
def check_reminders():
    """Отпечатва просрочените и предстоящите дейности (напр. за cron)."""
    reminders = collect_reminders()
    if not reminders:
        click.echo("Няма просрочени или предстоящи дейности.")
        return
    for reminder in reminders:
        subject = reminder.vehicle.reg_number if reminder.vehicle else reminder.driver.full_name
        remaining = days_left_text(reminder.status.days_left)
        if reminder.status.km_left is not None:
            remaining += f"; {km_left_text(reminder.status.km_left)}"
        click.echo(f"[{reminder.level_label:11}] {subject:11} {reminder.title} – {remaining}")
    if any(r.level == OVERDUE for r in reminders):
        raise SystemExit(1)
