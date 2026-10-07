"""Web forms (Flask-WTF). Every form has a CSRF token and server-side validation.

`layout` on a form lists its sections and fields for the generic form template:
[(section title or None, [(field name, Bootstrap column class), ...]), ...]
"""
from datetime import date, datetime
from decimal import Decimal

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DateField,
    DateTimeLocalField,
    DecimalField,
    IntegerField,
    PasswordField,
    SelectField,
    StringField,
    TextAreaField,
)
from wtforms.validators import (
    DataRequired,
    EqualTo,
    InputRequired,
    Length,
    NumberRange,
    Optional,
    Regexp,
    ValidationError,
)
from wtforms.widgets import TextInput

from .models import FUEL_TYPE_LABELS, POLICY_TYPE_LABELS, VEHICLE_STATUS_LABELS, VEHICLE_TYPE_LABELS
from .permissions import ROLE_LABELS

REQUIRED = "Полето е задължително."
MAX_MILEAGE = 5_000_000


class BaseForm(FlaskForm):
    class Meta:
        locales = ("bg",)  # built-in WTForms messages in Bulgarian

    layout: list = []
    live_total: tuple[str, str] | None = None  # (quantity field, price field) for a live total


class LocalDecimalField(DecimalField):
    """Decimal field that also accepts a comma as decimal separator ("1,45")."""

    widget = TextInput()

    def process_formdata(self, valuelist):
        if valuelist:
            valuelist = [
                v.replace(" ", "").replace(" ", "").replace(",", ".") for v in valuelist
            ]
        super().process_formdata(valuelist)


def not_in_future(form, field):
    if field.data is None:
        return
    value = field.data.date() if isinstance(field.data, datetime) else field.data
    if value > date.today():
        raise ValidationError("Датата не може да е в бъдещето.")


def valid_year(form, field):
    if field.data is not None and not 1950 <= field.data <= date.today().year + 1:
        raise ValidationError(f"Годината трябва да е между 1950 и {date.today().year + 1}.")


def _now() -> datetime:
    return datetime.now().replace(second=0, microsecond=0)


def mileage_field(label: str, required: bool = True) -> IntegerField:
    validators = [InputRequired(REQUIRED)] if required else [Optional()]
    validators.append(
        NumberRange(min=0, max=MAX_MILEAGE, message="Въведете пробег между 0 и 5 000 000 км.")
    )
    return IntegerField(label, validators=validators, render_kw={"min": 0, "step": 1})


# --- Authentication and users ----------------------------------------------------

class LoginForm(BaseForm):
    username = StringField(
        "Потребителско име",
        validators=[DataRequired(REQUIRED), Length(max=50)],
        render_kw={"autocomplete": "username", "autofocus": True},
    )
    password = PasswordField(
        "Парола",
        validators=[DataRequired(REQUIRED), Length(max=128)],
        render_kw={"autocomplete": "current-password"},
    )


class ChangePasswordForm(BaseForm):
    current_password = PasswordField("Текуща парола", validators=[DataRequired(REQUIRED)])
    new_password = PasswordField(
        "Нова парола",
        validators=[DataRequired(REQUIRED), Length(max=128)],
        description="Поне 8 символа, които включват букви и цифри.",
    )
    confirm = PasswordField(
        "Повторете новата парола",
        validators=[DataRequired(REQUIRED), EqualTo("new_password", message="Паролите не съвпадат.")],
    )
    layout = [(None, [("current_password", "col-md-6"), ("new_password", "col-md-6"), ("confirm", "col-md-6")])]


class UserForm(BaseForm):
    username = StringField(
        "Потребителско име",
        validators=[
            DataRequired(REQUIRED),
            Length(min=3, max=50),
            Regexp(r"^[A-Za-z0-9_.-]+$", message="Разрешени са латински букви, цифри и знаците . _ -"),
        ],
    )
    full_name = StringField("Име и фамилия", validators=[DataRequired(REQUIRED), Length(max=120)])
    role = SelectField("Роля", choices=list(ROLE_LABELS.items()))
    active = BooleanField("Активен профил", default=True)
    password = PasswordField(
        "Парола",
        validators=[Optional(), Length(max=128)],
        description="Поне 8 символа с букви и цифри. При редакция оставете празно, ако не я променяте.",
    )
    layout = [
        (None, [("username", "col-md-6"), ("full_name", "col-md-6"), ("role", "col-md-6"),
                ("password", "col-md-6"), ("active", "col-12")]),
    ]


# --- Vehicles ----------------------------------------------------------------------

class VehicleForm(BaseForm):
    reg_number = StringField(
        "Регистрационен номер",
        validators=[DataRequired(REQUIRED), Length(max=16)],
        render_kw={"placeholder": "СВ 1234 АВ", "autocapitalize": "characters"},
        description="Може да се въведе и с латински букви – ще бъде преобразуван.",
    )
    make = StringField("Марка", validators=[DataRequired(REQUIRED), Length(max=50)],
                       render_kw={"placeholder": "напр. Škoda"})
    model = StringField("Модел", validators=[DataRequired(REQUIRED), Length(max=50)],
                        render_kw={"placeholder": "напр. Octavia"})
    year = IntegerField("Година на производство", validators=[InputRequired(REQUIRED), valid_year])
    vehicle_type = SelectField("Тип МПС", choices=list(VEHICLE_TYPE_LABELS.items()))
    fuel_type = SelectField("Вид гориво", choices=list(FUEL_TYPE_LABELS.items()))
    vin = StringField("VIN (номер на рама)", validators=[Optional(), Length(max=20)],
                      description="17 символа, по желание.")
    in_service_since = DateField(
        "В автопарка от", validators=[DataRequired(REQUIRED), not_in_future], default=date.today
    )
    initial_mileage = mileage_field("Пробег при въвеждане в системата (км)")
    responsible_driver_id = SelectField("Отговорен шофьор (поверено на)", coerce=int, choices=[])
    notes = TextAreaField("Бележки", validators=[Optional(), Length(max=2000)], render_kw={"rows": 2})
    layout = [
        ("Основни данни", [("reg_number", "col-md-4"), ("make", "col-md-4"), ("model", "col-md-4"),
                           ("year", "col-md-4"), ("vehicle_type", "col-md-4"), ("fuel_type", "col-md-4"),
                           ("vin", "col-md-6"), ("responsible_driver_id", "col-md-6")]),
        ("Начален пробег", [("in_service_since", "col-md-4"), ("initial_mileage", "col-md-4")]),
        (None, [("notes", "col-12")]),
    ]


class StatusForm(BaseForm):
    status = SelectField("Нов статус", choices=list(VEHICLE_STATUS_LABELS.items()))
    note = StringField("Причина / бележка", validators=[Optional(), Length(max=255)],
                       render_kw={"placeholder": "напр. повреда в скоростната кутия"})


class OdometerForm(BaseForm):
    read_on = DateField("Дата", validators=[DataRequired(REQUIRED), not_in_future], default=date.today)
    mileage = mileage_field("Километраж (км)")
    note = StringField("Бележка", validators=[Optional(), Length(max=200)])


# --- Maintenance -----------------------------------------------------------------------

class MaintenanceTypeForm(BaseForm):
    name = StringField("Наименование", validators=[DataRequired(REQUIRED), Length(max=80)])
    description = StringField("Описание", validators=[Optional(), Length(max=255)])
    default_interval_km = IntegerField(
        "Интервал по пробег (км)",
        validators=[Optional(), NumberRange(min=1, max=1_000_000, message="Въведете положително число.")],
        description="Празно – дейността не се следи по пробег.",
    )
    default_interval_days = IntegerField(
        "Интервал по време (дни)",
        validators=[Optional(), NumberRange(min=1, max=3650, message="Въведете между 1 и 3650 дни.")],
        description="Празно – дейността не се следи по дата. Без двата интервала е непланирана (ремонт).",
    )
    auto_assign = BooleanField("Добавяй автоматично в плана на всяко ново МПС")
    active = BooleanField("Активен (показва се в падащите менюта)", default=True)
    layout = [
        (None, [("name", "col-md-6"), ("description", "col-md-6"),
                ("default_interval_km", "col-md-6"), ("default_interval_days", "col-md-6"),
                ("auto_assign", "col-12"), ("active", "col-12")]),
    ]


class PlanForm(BaseForm):
    maintenance_type_id = SelectField("Дейност", coerce=int, validators=[DataRequired("Изберете дейност.")])
    interval_km = IntegerField(
        "На всеки (км)", validators=[Optional(), NumberRange(min=1, max=1_000_000)]
    )
    interval_days = IntegerField(
        "На всеки (дни)", validators=[Optional(), NumberRange(min=1, max=3650)],
        description="Попълнете поне един от двата интервала.",
    )
    baseline_date = DateField(
        "Последно извършено на", validators=[DataRequired(REQUIRED), not_in_future], default=date.today
    )
    baseline_mileage = mileage_field("При пробег (км)")
    active = BooleanField("Активна (следи се за напомняния)", default=True)
    notes = StringField("Бележка", validators=[Optional(), Length(max=255)])
    layout = [
        ("Дейност и интервал", [("maintenance_type_id", "col-md-6"), ("interval_km", "col-md-3"),
                                ("interval_days", "col-md-3")]),
        ("Последно извършване (преди въвеждане на записи в системата)",
         [("baseline_date", "col-md-6"), ("baseline_mileage", "col-md-6")]),
        (None, [("notes", "col-12"), ("active", "col-12")]),
    ]


class MaintenanceRecordForm(BaseForm):
    vehicle_id = SelectField("МПС", coerce=int, validators=[DataRequired("Изберете МПС.")])
    maintenance_type_id = SelectField("Вид дейност", coerce=int,
                                      validators=[DataRequired("Изберете вид дейност.")])
    performed_on = DateField("Дата на извършване",
                             validators=[DataRequired(REQUIRED), not_in_future], default=date.today)
    mileage = mileage_field("Километраж при обслужването (км)")
    cost = LocalDecimalField(
        "Цена (€)", places=2,
        validators=[InputRequired(REQUIRED), NumberRange(min=0, max=1_000_000, message="Цената трябва да е между 0 и 1 000 000.")],
        render_kw={"inputmode": "decimal", "placeholder": "0,00"},
    )
    service_provider = StringField("Сервиз / изпълнител", validators=[Optional(), Length(max=120)])
    description = TextAreaField("Описание на извършеното",
                                validators=[DataRequired(REQUIRED), Length(max=500)], render_kw={"rows": 3})
    mark_vehicle_active = BooleanField("Ремонтът е завършен – върни МПС в статус „Активно“")
    layout = [
        (None, [("vehicle_id", "col-md-6"), ("maintenance_type_id", "col-md-6"),
                ("performed_on", "col-md-4"), ("mileage", "col-md-4"), ("cost", "col-md-4"),
                ("service_provider", "col-md-12"), ("description", "col-12"),
                ("mark_vehicle_active", "col-12")]),
    ]


# --- Fuel ------------------------------------------------------------------------------

class FuelForm(BaseForm):
    vehicle_id = SelectField("МПС", coerce=int, validators=[DataRequired("Изберете МПС.")])
    driver_id = SelectField("Шофьор", coerce=int, choices=[])
    filled_on = DateField("Дата", validators=[DataRequired(REQUIRED), not_in_future], default=date.today)
    mileage = mileage_field("Километраж (км)")
    quantity = LocalDecimalField(
        "Количество (л, за електромобили – kWh)", places=2,
        validators=[InputRequired(REQUIRED), NumberRange(min=Decimal("0.01"), max=2000, message="Количеството трябва да е между 0,01 и 2000.")],
        render_kw={"inputmode": "decimal"},
    )
    price_per_unit = LocalDecimalField(
        "Единична цена (€ за литър / kWh)", places=3,
        validators=[InputRequired(REQUIRED), NumberRange(min=Decimal("0.001"), max=100, message="Цената трябва да е между 0,001 и 100.")],
        render_kw={"inputmode": "decimal", "placeholder": "1,459"},
    )
    full_tank = BooleanField("Зареден до пълен резервоар (използва се за изчисляване на разхода)", default=True)
    station = StringField("Бензиностанция", validators=[Optional(), Length(max=120)])
    layout = [
        (None, [("vehicle_id", "col-md-6"), ("driver_id", "col-md-6"), ("filled_on", "col-md-4"),
                ("mileage", "col-md-4"), ("station", "col-md-4"), ("quantity", "col-md-4"),
                ("price_per_unit", "col-md-4"), ("full_tank", "col-12")]),
    ]
    live_total = ("quantity", "price_per_unit")


# --- Drivers ---------------------------------------------------------------------------

class DriverForm(BaseForm):
    first_name = StringField("Име", validators=[DataRequired(REQUIRED), Length(max=50)])
    last_name = StringField("Фамилия", validators=[DataRequired(REQUIRED), Length(max=50)])
    phone = StringField(
        "Телефон",
        validators=[Optional(), Length(max=30), Regexp(r"^[0-9+()\s-]{6,30}$", message="Невалиден телефонен номер.")],
        render_kw={"placeholder": "0888 123 456"},
    )
    email = StringField(
        "Имейл",
        validators=[Optional(), Length(max=120), Regexp(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", message="Невалиден имейл адрес.")],
    )
    license_number = StringField(
        "№ на шофьорска книжка",
        validators=[DataRequired(REQUIRED), Regexp(r"^[A-Za-z0-9]{5,20}$", message="Номерът съдържа от 5 до 20 латински букви и цифри.")],
    )
    license_categories = StringField("Категории", validators=[DataRequired(REQUIRED), Length(max=40)],
                                     render_kw={"placeholder": "B, C, CE"})
    license_expiry = DateField("Книжката е валидна до", validators=[DataRequired(REQUIRED)])
    active = BooleanField("Активен (може да получава МПС)", default=True)
    notes = TextAreaField("Бележки", validators=[Optional(), Length(max=2000)], render_kw={"rows": 2})
    layout = [
        ("Лични данни", [("first_name", "col-md-6"), ("last_name", "col-md-6"),
                         ("phone", "col-md-6"), ("email", "col-md-6")]),
        ("Шофьорска книжка", [("license_number", "col-md-4"), ("license_categories", "col-md-4"),
                              ("license_expiry", "col-md-4")]),
        (None, [("notes", "col-12"), ("active", "col-12")]),
    ]


# --- Trips -------------------------------------------------------------------------------

class TripStartForm(BaseForm):
    vehicle_id = SelectField("МПС", coerce=int, validators=[DataRequired("Изберете МПС.")])
    driver_id = SelectField("Шофьор", coerce=int, validators=[DataRequired("Изберете шофьор.")])
    started_at = DateTimeLocalField("Начало на курса", validators=[DataRequired(REQUIRED), not_in_future],
                                    default=_now)
    start_mileage = mileage_field("Начален километраж (км)")
    purpose = StringField("Цел / маршрут", validators=[DataRequired(REQUIRED), Length(max=200)],
                          render_kw={"placeholder": "напр. доставка София – Пловдив"})
    notes = TextAreaField("Бележки", validators=[Optional(), Length(max=2000)], render_kw={"rows": 2})
    layout = [
        (None, [("vehicle_id", "col-md-6"), ("driver_id", "col-md-6"), ("started_at", "col-md-6"),
                ("start_mileage", "col-md-6"), ("purpose", "col-12"), ("notes", "col-12")]),
    ]


class TripReturnForm(BaseForm):
    ended_at = DateTimeLocalField("Време на връщане", validators=[DataRequired(REQUIRED), not_in_future],
                                  default=_now)
    end_mileage = mileage_field("Краен километраж (км)")
    notes = TextAreaField("Бележки за курса", validators=[Optional(), Length(max=2000)], render_kw={"rows": 2})
    fuel_quantity = LocalDecimalField(
        "Заредено количество (л / kWh)", places=2,
        validators=[Optional(), NumberRange(min=Decimal("0.01"), max=2000)],
        render_kw={"inputmode": "decimal"},
    )
    fuel_price = LocalDecimalField(
        "Единична цена (€)", places=3,
        validators=[Optional(), NumberRange(min=Decimal("0.001"), max=100)],
        render_kw={"inputmode": "decimal", "placeholder": "1,459"},
    )
    fuel_full_tank = BooleanField("Пълен резервоар", default=True)
    fuel_station = StringField("Бензиностанция", validators=[Optional(), Length(max=120)])
    problem = TextAreaField(
        "Забелязан проблем по МПС",
        validators=[Optional(), Length(max=200)],
        render_kw={"rows": 2, "placeholder": "Оставете празно, ако няма проблем"},
        description="Ако опишете проблем, МПС автоматично получава статус „В ремонт“.",
    )
    layout = [
        ("Връщане", [("ended_at", "col-md-6"), ("end_mileage", "col-md-6"), ("notes", "col-12")]),
        ("Зареждане по време на курса (по желание)",
         [("fuel_quantity", "col-md-4"), ("fuel_price", "col-md-4"), ("fuel_station", "col-md-4"),
          ("fuel_full_tank", "col-12")]),
        ("Състояние на МПС", [("problem", "col-12")]),
    ]
    live_total = ("fuel_quantity", "fuel_price")

    def validate(self, extra_validators=None):
        # Checked on form level: Optional() stops the field validators when the price is empty.
        valid = super().validate(extra_validators)
        if self.fuel_quantity.data and not self.fuel_price.data:
            self.fuel_price.errors.append("Въведете цената на зареденото гориво.")
            return False
        return valid


class TripEditForm(BaseForm):
    started_at = DateTimeLocalField("Начало на курса", validators=[DataRequired(REQUIRED), not_in_future])
    start_mileage = mileage_field("Начален километраж (км)")
    ended_at = DateTimeLocalField("Време на връщане", validators=[Optional(), not_in_future])
    end_mileage = mileage_field("Краен километраж (км)", required=False)
    purpose = StringField("Цел / маршрут", validators=[DataRequired(REQUIRED), Length(max=200)])
    notes = TextAreaField("Бележки", validators=[Optional(), Length(max=2000)], render_kw={"rows": 2})
    layout = [
        (None, [("started_at", "col-md-6"), ("start_mileage", "col-md-6"), ("ended_at", "col-md-6"),
                ("end_mileage", "col-md-6"), ("purpose", "col-12"), ("notes", "col-12")]),
    ]


# --- Insurance ---------------------------------------------------------------------------

class InsuranceForm(BaseForm):
    vehicle_id = SelectField("МПС", coerce=int, validators=[DataRequired("Изберете МПС.")])
    policy_type = SelectField("Вид застраховка", choices=list(POLICY_TYPE_LABELS.items()))
    insurer = StringField("Застраховател", validators=[DataRequired(REQUIRED), Length(max=120)])
    policy_number = StringField("№ на полица", validators=[DataRequired(REQUIRED), Length(max=50)])
    start_date = DateField("Валидна от", validators=[DataRequired(REQUIRED)], default=date.today)
    end_date = DateField("Валидна до", validators=[DataRequired(REQUIRED)],
                         description="Попълва се автоматично за 1 година – може да се промени.")
    premium = LocalDecimalField(
        "Премия (€)", places=2,
        validators=[InputRequired(REQUIRED), NumberRange(min=0, max=1_000_000)],
        render_kw={"inputmode": "decimal"},
    )
    notes = StringField("Бележка", validators=[Optional(), Length(max=255)])
    layout = [
        (None, [("vehicle_id", "col-md-6"), ("policy_type", "col-md-6"), ("insurer", "col-md-6"),
                ("policy_number", "col-md-6"), ("start_date", "col-md-4"), ("end_date", "col-md-4"),
                ("premium", "col-md-4"), ("notes", "col-12")]),
    ]

    def validate_end_date(self, field):
        if self.start_date.data and field.data and field.data <= self.start_date.data:
            raise ValidationError("Крайната дата трябва да е след началната.")
