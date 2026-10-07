"""Database models of the fleet management system (SQLAlchemy ORM).

Integrity is enforced on two levels: the application validates input in forms
and services, and the database itself has foreign keys, UNIQUE and CHECK
constraints, so invalid data cannot be stored even by mistake.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from flask_login import UserMixin
from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db
from .permissions import ROLE_LABELS, role_can


class FixedDecimal(TypeDecorator):
    """Exact decimal number stored as an integer count of minor units.

    SQLite has no real DECIMAL type, so amounts are kept as integers
    (e.g. euro cents) to avoid floating point rounding errors.
    """

    impl = Integer
    cache_ok = True

    def __init__(self, scale: int = 2):
        super().__init__()
        self.scale = scale
        self._factor = Decimal(10) ** scale
        self._quantum = Decimal(1).scaleb(-scale)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        scaled = Decimal(str(value)) * self._factor
        return int(scaled.to_integral_value(rounding=ROUND_HALF_UP))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return (Decimal(value) / self._factor).quantize(self._quantum)


def _one_of(column: str, values) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


# --- Enumerations (stored as short codes, shown with Bulgarian labels) -------

class VehicleType:
    CAR = "car"
    VAN = "van"
    TRUCK = "truck"
    BUS = "bus"
    OTHER = "other"


VEHICLE_TYPE_LABELS = {
    VehicleType.CAR: "Лек автомобил",
    VehicleType.VAN: "Ван / товарен бус",
    VehicleType.TRUCK: "Камион",
    VehicleType.BUS: "Автобус",
    VehicleType.OTHER: "Друго",
}


class VehicleStatus:
    ACTIVE = "active"
    IN_REPAIR = "in_repair"
    OUT_OF_SERVICE = "out_of_service"


VEHICLE_STATUS_LABELS = {
    VehicleStatus.ACTIVE: "Активно",
    VehicleStatus.IN_REPAIR: "В ремонт",
    VehicleStatus.OUT_OF_SERVICE: "Спряно от движение",
}


class FuelType:
    DIESEL = "diesel"
    PETROL = "petrol"
    LPG = "lpg"
    HYBRID = "hybrid"
    ELECTRIC = "electric"


FUEL_TYPE_LABELS = {
    FuelType.DIESEL: "Дизел",
    FuelType.PETROL: "Бензин",
    FuelType.LPG: "Бензин / газ (LPG)",
    FuelType.HYBRID: "Хибрид (бензин)",
    FuelType.ELECTRIC: "Електричество",
}


class PolicyType:
    CIVIL_LIABILITY = "civil_liability"
    CASCO = "casco"


POLICY_TYPE_LABELS = {
    PolicyType.CIVIL_LIABILITY: "Гражданска отговорност",
    PolicyType.CASCO: "Каско",
}


# --- Users and audit ---------------------------------------------------------

class User(UserMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(_one_of("role", ROLE_LABELS), name="ck_users_role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    full_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(default=True)
    failed_logins: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None]
    last_login_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)

    @property
    def is_active(self) -> bool:  # used by Flask-Login: inactive users cannot log in
        return self.active

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def can(self, permission: str) -> bool:
        return role_can(self.role, permission)


class AuditLog(db.Model):
    """Who changed what and when. The username is copied so the log survives user deletion."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    username: Mapped[str | None] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(30))
    entity: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[int | None]
    details: Mapped[str] = mapped_column(String(500), default="")


# --- Drivers -------------------------------------------------------------------

class Driver(db.Model):
    __tablename__ = "drivers"

    id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str] = mapped_column(String(50))
    last_name: Mapped[str] = mapped_column(String(50))
    phone: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(120))
    license_number: Mapped[str] = mapped_column(String(20), unique=True)
    license_categories: Mapped[str] = mapped_column(String(40))
    license_expiry: Mapped[date]
    active: Mapped[bool] = mapped_column(default=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)

    trips: Mapped[list[Trip]] = relationship(
        back_populates="driver", order_by="desc(Trip.started_at)", passive_deletes="all"
    )

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    def has_history(self) -> bool:
        trips = db.session.scalar(select(func.count(Trip.id)).where(Trip.driver_id == self.id))
        fuel = db.session.scalar(
            select(func.count(FuelRecord.id)).where(FuelRecord.driver_id == self.id)
        )
        return bool(trips or fuel)

    def __str__(self) -> str:
        return self.full_name


# --- Vehicles ------------------------------------------------------------------

class Vehicle(db.Model):
    __tablename__ = "vehicles"
    __table_args__ = (
        CheckConstraint(_one_of("vehicle_type", VEHICLE_TYPE_LABELS), name="ck_vehicles_type"),
        CheckConstraint(_one_of("fuel_type", FUEL_TYPE_LABELS), name="ck_vehicles_fuel"),
        CheckConstraint(_one_of("status", VEHICLE_STATUS_LABELS), name="ck_vehicles_status"),
        CheckConstraint("year BETWEEN 1950 AND 2100", name="ck_vehicles_year"),
        CheckConstraint("initial_mileage >= 0", name="ck_vehicles_initial_mileage"),
        CheckConstraint("current_mileage >= initial_mileage", name="ck_vehicles_mileage"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    reg_number: Mapped[str] = mapped_column(String(16), unique=True)
    make: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(50))
    year: Mapped[int]
    vehicle_type: Mapped[str] = mapped_column(String(20))
    fuel_type: Mapped[str] = mapped_column(String(20))
    vin: Mapped[str | None] = mapped_column(String(17), unique=True)
    # Odometer value when the vehicle entered the fleet (the first known reading).
    in_service_since: Mapped[date] = mapped_column(default=date.today)
    initial_mileage: Mapped[int] = mapped_column(default=0)
    # Cached highest known odometer reading, kept up to date by services.odometer.
    current_mileage: Mapped[int] = mapped_column(default=0)
    mileage_updated_on: Mapped[date] = mapped_column(default=date.today)
    status: Mapped[str] = mapped_column(String(20), default=VehicleStatus.ACTIVE)
    status_changed_at: Mapped[datetime] = mapped_column(default=datetime.now)
    status_note: Mapped[str | None] = mapped_column(String(255))
    responsible_driver_id: Mapped[int | None] = mapped_column(
        ForeignKey("drivers.id", ondelete="SET NULL")
    )
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)

    responsible_driver: Mapped[Driver | None] = relationship()
    plans: Mapped[list[MaintenancePlan]] = relationship(
        back_populates="vehicle", cascade="all, delete-orphan", order_by="MaintenancePlan.id"
    )
    maintenance_records: Mapped[list[MaintenanceRecord]] = relationship(
        back_populates="vehicle",
        order_by="(desc(MaintenanceRecord.performed_on), desc(MaintenanceRecord.mileage))",
        passive_deletes="all",
    )
    fuel_records: Mapped[list[FuelRecord]] = relationship(
        back_populates="vehicle",
        order_by="(desc(FuelRecord.filled_on), desc(FuelRecord.mileage))",
        passive_deletes="all",
    )
    trips: Mapped[list[Trip]] = relationship(
        back_populates="vehicle", order_by="desc(Trip.started_at)", passive_deletes="all"
    )
    insurance_policies: Mapped[list[InsurancePolicy]] = relationship(
        back_populates="vehicle", order_by="desc(InsurancePolicy.end_date)", passive_deletes="all"
    )
    odometer_readings: Mapped[list[OdometerReading]] = relationship(
        back_populates="vehicle",
        order_by="desc(OdometerReading.read_on)",
        cascade="all, delete-orphan",
    )

    @property
    def display_name(self) -> str:
        return f"{self.reg_number} – {self.make} {self.model}"

    @property
    def type_label(self) -> str:
        return VEHICLE_TYPE_LABELS.get(self.vehicle_type, self.vehicle_type)

    @property
    def status_label(self) -> str:
        return VEHICLE_STATUS_LABELS.get(self.status, self.status)

    @property
    def fuel_label(self) -> str:
        return FUEL_TYPE_LABELS.get(self.fuel_type, self.fuel_type)

    @property
    def fuel_unit(self) -> str:
        return "kWh" if self.fuel_type == FuelType.ELECTRIC else "л"

    @property
    def open_trip(self) -> Trip | None:
        return db.session.scalar(
            select(Trip).where(Trip.vehicle_id == self.id, Trip.ended_at.is_(None))
        )

    def has_history(self) -> bool:
        """True when the vehicle has records that must be kept (repairs, fuel, trips, policies)."""
        for model in (MaintenanceRecord, FuelRecord, Trip, InsurancePolicy):
            count = db.session.scalar(
                select(func.count(model.id)).where(model.vehicle_id == self.id)
            )
            if count:
                return True
        return False

    def __str__(self) -> str:
        return self.display_name


class OdometerReading(db.Model):
    """A manual odometer reading, e.g. when the mileage is checked without a trip or refuel."""

    __tablename__ = "odometer_readings"
    __table_args__ = (CheckConstraint("mileage >= 0", name="ck_odometer_mileage"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(
        ForeignKey("vehicles.id", ondelete="CASCADE"), index=True
    )
    read_on: Mapped[date]
    mileage: Mapped[int]
    note: Mapped[str | None] = mapped_column(String(200))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)

    vehicle: Mapped[Vehicle] = relationship(back_populates="odometer_readings")


# --- Maintenance -------------------------------------------------------------

class MaintenanceType(db.Model):
    """Kind of activity (oil change, technical inspection, repair...) with default intervals."""

    __tablename__ = "maintenance_types"
    __table_args__ = (
        CheckConstraint(
            "default_interval_km IS NULL OR default_interval_km > 0", name="ck_type_interval_km"
        ),
        CheckConstraint(
            "default_interval_days IS NULL OR default_interval_days > 0",
            name="ck_type_interval_days",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str | None] = mapped_column(String(255))
    default_interval_km: Mapped[int | None]
    default_interval_days: Mapped[int | None]
    # Periodic types with auto_assign are added to the plan of every new vehicle.
    auto_assign: Mapped[bool] = mapped_column(default=False)
    active: Mapped[bool] = mapped_column(default=True)

    @property
    def is_periodic(self) -> bool:
        return bool(self.default_interval_km or self.default_interval_days)

    def is_used(self) -> bool:
        records = db.session.scalar(
            select(func.count(MaintenanceRecord.id)).where(
                MaintenanceRecord.maintenance_type_id == self.id
            )
        )
        plans = db.session.scalar(
            select(func.count(MaintenancePlan.id)).where(
                MaintenancePlan.maintenance_type_id == self.id
            )
        )
        return bool(records or plans)

    def __str__(self) -> str:
        return self.name


class MaintenancePlan(db.Model):
    """A periodic activity scheduled for one vehicle (every N km and/or every N days).

    The baseline is the last time the activity was done before it was tracked in
    the system; afterwards the latest maintenance record of the same type is used.
    """

    __tablename__ = "maintenance_plans"
    __table_args__ = (
        UniqueConstraint("vehicle_id", "maintenance_type_id", name="uq_plan_vehicle_type"),
        CheckConstraint(
            "interval_km IS NOT NULL OR interval_days IS NOT NULL", name="ck_plan_has_interval"
        ),
        CheckConstraint("interval_km IS NULL OR interval_km > 0", name="ck_plan_interval_km"),
        CheckConstraint(
            "interval_days IS NULL OR interval_days > 0", name="ck_plan_interval_days"
        ),
        CheckConstraint("baseline_mileage >= 0", name="ck_plan_baseline_mileage"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id", ondelete="CASCADE"))
    maintenance_type_id: Mapped[int] = mapped_column(
        ForeignKey("maintenance_types.id", ondelete="RESTRICT")
    )
    interval_km: Mapped[int | None]
    interval_days: Mapped[int | None]
    baseline_date: Mapped[date]
    baseline_mileage: Mapped[int]
    active: Mapped[bool] = mapped_column(default=True)
    notes: Mapped[str | None] = mapped_column(String(255))

    vehicle: Mapped[Vehicle] = relationship(back_populates="plans")
    maintenance_type: Mapped[MaintenanceType] = relationship()


class MaintenanceRecord(db.Model):
    """A maintenance or repair that was actually performed."""

    __tablename__ = "maintenance_records"
    __table_args__ = (
        CheckConstraint("mileage >= 0", name="ck_maint_mileage"),
        CheckConstraint("cost >= 0", name="ck_maint_cost"),
        Index("ix_maint_vehicle_date", "vehicle_id", "performed_on"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id", ondelete="RESTRICT"))
    maintenance_type_id: Mapped[int] = mapped_column(
        ForeignKey("maintenance_types.id", ondelete="RESTRICT")
    )
    performed_on: Mapped[date]
    mileage: Mapped[int]
    description: Mapped[str] = mapped_column(String(500))
    cost: Mapped[Decimal] = mapped_column(FixedDecimal(2))
    service_provider: Mapped[str | None] = mapped_column(String(120))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)

    vehicle: Mapped[Vehicle] = relationship(back_populates="maintenance_records")
    maintenance_type: Mapped[MaintenanceType] = relationship()
    created_by: Mapped[User | None] = relationship()


# --- Fuel ------------------------------------------------------------------------

class FuelRecord(db.Model):
    """A refuel (or a charge for electric vehicles: quantity in kWh)."""

    __tablename__ = "fuel_records"
    __table_args__ = (
        CheckConstraint("mileage >= 0", name="ck_fuel_mileage"),
        CheckConstraint("quantity > 0", name="ck_fuel_quantity"),
        CheckConstraint("price_per_unit > 0", name="ck_fuel_price"),
        CheckConstraint("total_cost >= 0", name="ck_fuel_total"),
        Index("ix_fuel_vehicle_date", "vehicle_id", "filled_on"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id", ondelete="RESTRICT"))
    driver_id: Mapped[int | None] = mapped_column(ForeignKey("drivers.id", ondelete="RESTRICT"))
    filled_on: Mapped[date]
    mileage: Mapped[int]
    quantity: Mapped[Decimal] = mapped_column(FixedDecimal(2))
    price_per_unit: Mapped[Decimal] = mapped_column(FixedDecimal(3))
    total_cost: Mapped[Decimal] = mapped_column(FixedDecimal(2))
    full_tank: Mapped[bool] = mapped_column(default=True)
    station: Mapped[str | None] = mapped_column(String(120))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)

    vehicle: Mapped[Vehicle] = relationship(back_populates="fuel_records")
    driver: Mapped[Driver | None] = relationship()


# --- Trips (who used which vehicle and when) ---------------------------------------

class Trip(db.Model):
    __tablename__ = "trips"
    __table_args__ = (
        CheckConstraint("start_mileage >= 0", name="ck_trip_start_mileage"),
        CheckConstraint(
            "end_mileage IS NULL OR end_mileage >= start_mileage", name="ck_trip_end_mileage"
        ),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ck_trip_dates"),
        CheckConstraint(
            "(ended_at IS NULL) = (end_mileage IS NULL)", name="ck_trip_closed_consistently"
        ),
        # A vehicle and a driver can each be on at most one open trip.
        Index(
            "uq_trip_open_vehicle", "vehicle_id", unique=True, sqlite_where=text("ended_at IS NULL")
        ),
        Index(
            "uq_trip_open_driver", "driver_id", unique=True, sqlite_where=text("ended_at IS NULL")
        ),
        Index("ix_trip_vehicle_start", "vehicle_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id", ondelete="RESTRICT"))
    driver_id: Mapped[int] = mapped_column(ForeignKey("drivers.id", ondelete="RESTRICT"))
    started_at: Mapped[datetime]
    ended_at: Mapped[datetime | None]
    start_mileage: Mapped[int]
    end_mileage: Mapped[int | None]
    purpose: Mapped[str] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)

    vehicle: Mapped[Vehicle] = relationship(back_populates="trips")
    driver: Mapped[Driver] = relationship(back_populates="trips")

    @property
    def is_open(self) -> bool:
        return self.ended_at is None

    @property
    def distance(self) -> int | None:
        if self.end_mileage is None:
            return None
        return self.end_mileage - self.start_mileage


# --- Insurance ---------------------------------------------------------------------

class InsurancePolicy(db.Model):
    __tablename__ = "insurance_policies"
    __table_args__ = (
        CheckConstraint(_one_of("policy_type", POLICY_TYPE_LABELS), name="ck_policy_type"),
        CheckConstraint("end_date > start_date", name="ck_policy_dates"),
        CheckConstraint("premium >= 0", name="ck_policy_premium"),
        UniqueConstraint("insurer", "policy_number", name="uq_policy_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id", ondelete="RESTRICT"))
    policy_type: Mapped[str] = mapped_column(String(20))
    insurer: Mapped[str] = mapped_column(String(120))
    policy_number: Mapped[str] = mapped_column(String(50))
    start_date: Mapped[date]
    end_date: Mapped[date]
    premium: Mapped[Decimal] = mapped_column(FixedDecimal(2))
    notes: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)

    vehicle: Mapped[Vehicle] = relationship(back_populates="insurance_policies")

    @property
    def type_label(self) -> str:
        return POLICY_TYPE_LABELS.get(self.policy_type, self.policy_type)

    def days_left(self, today: date | None = None) -> int:
        return (self.end_date - (today or date.today())).days
