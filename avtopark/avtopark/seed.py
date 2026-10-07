"""Reference data and a realistic demo fleet with about 16 months of history.

The demo is generated relative to today's date, so the reminders always look
current: a few activities are overdue, several are due soon, the rest are OK.
"""
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import select

from .extensions import db
from .models import (
    AuditLog,
    Driver,
    FuelRecord,
    FuelType,
    InsurancePolicy,
    MaintenancePlan,
    MaintenanceRecord,
    MaintenanceType,
    OdometerReading,
    PolicyType,
    Trip,
    User,
    Vehicle,
    VehicleStatus,
    VehicleType,
)
from .permissions import Role
from .services.fuel import compute_total
from .services.odometer import refresh_vehicle_mileage

OIL = "Смяна на масло и филтри"
INSPECTION = "Годишен технически преглед"
TYRES = "Сезонна смяна на гуми"
BRAKES = "Обслужване на спирачна система"
TIMING = "Смяна на ангренажен ремък / верига"
AIRCON = "Обслужване на климатична система"
TACHOGRAPH = "Проверка на тахограф"
REPAIR = "Ремонт"
DIAGNOSTICS = "Диагностика"

# name, description, interval km, interval days, added to new vehicles automatically
DEFAULT_TYPES = [
    (OIL, "Двигателно масло, маслен, въздушен и горивен филтър", 10_000, 365, True),
    (INSPECTION, "Задължителен периодичен преглед за техническа изправност", None, 365, True),
    (TYRES, "Летни / зимни гуми, монтаж и баланс", None, 180, True),
    (BRAKES, "Накладки, дискове и спирачна течност", 40_000, 730, True),
    (TIMING, "Ангренажен комплект и водна помпа", 120_000, 1825, False),
    (AIRCON, "Дезинфекция и зареждане с хладилен агент", None, 730, False),
    (TACHOGRAPH, "Периодична проверка на тахографа (камиони и автобуси)", None, 730, False),
    (REPAIR, "Непланиран ремонт след повреда", None, None, False),
    (DIAGNOSTICS, "Компютърна диагностика и оглед", None, None, False),
]


def seed_reference_data() -> int:
    """Insert the default maintenance types that are missing. Returns how many were added."""
    existing = set(db.session.scalars(select(MaintenanceType.name)))
    added = 0
    for name, description, km, days, auto in DEFAULT_TYPES:
        if name not in existing:
            db.session.add(
                MaintenanceType(
                    name=name,
                    description=description,
                    default_interval_km=km,
                    default_interval_days=days,
                    auto_assign=auto,
                )
            )
            added += 1
    db.session.commit()
    return added


# --- Demo fleet -------------------------------------------------------------------

DEMO_PASSWORD = "Demo1234"

USERS = [
    ("admin", "Системен администратор", Role.ADMIN),
    ("manager", "Даниела Петкова", Role.MANAGER),
    ("mechanic", "Красимир Ангелов", Role.MECHANIC),
    ("logistic", "Светослав Илиев", Role.LOGISTICIAN),
]

# key, first name, last name, phone, categories, licence expiry (days from today), active
DRIVERS = [
    ("georgi", "Георги", "Петров", "0888 412 365", "B", 1460, True),
    ("maria", "Мария", "Иванова", "0887 254 118", "B", 2210, True),
    ("nikolay", "Николай", "Стоянов", "0899 631 904", "B", 980, True),
    ("elena", "Елена", "Тодорова", "0878 115 742", "B", 2900, True),
    ("anna", "Анна", "Христова", "0886 903 221", "B", 1720, True),
    ("ivan", "Иван", "Димитров", "0888 770 431", "B, C, CE", 1100, True),
    ("petar", "Петър", "Георгиев", "0897 318 556", "B, C, CE", 640, True),
    ("stefan", "Стефан", "Атанасов", "0889 542 870", "B, C, CE", 1500, True),
    ("dimitar", "Димитър", "Колев", "0876 224 913", "B, C", 830, True),
    ("hristo", "Христо", "Николов", "0898 467 125", "B, C1", 18, True),
    ("kalin", "Калин", "Маринов", "0885 390 674", "B, C", -9, True),
    ("vasil", "Васил", "Христов", "0882 157 349", "B", -200, False),
]
LATIN_NAMES = {
    "georgi": "g.petrov", "maria": "m.ivanova", "nikolay": "n.stoyanov", "elena": "e.todorova",
    "anna": "a.hristova", "ivan": "i.dimitrov", "petar": "p.georgiev", "stefan": "s.atanasov",
    "dimitar": "d.kolev", "hristo": "h.nikolov", "kalin": "k.marinov", "vasil": "v.hristov",
}
VAN_DRIVERS = ["dimitar", "hristo", "kalin", "ivan", "petar"]
TRUCK_DRIVERS = ["stefan", "ivan", "petar"]


@dataclass
class DemoVehicle:
    key: str
    reg: str
    make: str
    model: str
    year: int
    vtype: str
    fuel: str
    wmi: str  # first characters of the VIN (manufacturer code)
    start_km: int
    daily_km: int
    consumption: float  # l/100 km (kWh/100 km for electric)
    tank: int
    driver: str | None = None  # responsible driver (service cars)
    pool: bool = False  # vans and trucks handed out per trip
    casco_days: int | None = None  # casco end, days from today
    civil_days: int = 200  # civil liability end, days from today
    stop_days: int | None = None  # stopped this many days ago
    stop_status: str | None = None
    stop_note: str | None = None
    entries: list = field(default_factory=list)


VEHICLES = [
    DemoVehicle("octavia", "СВ 4821 КМ", "Škoda", "Octavia Combi 2.0 TDI", 2020, VehicleType.CAR,
                FuelType.DIESEL, "TMBJR7NX", 96_400, 72, 5.4, 50, driver="georgi", casco_days=40,
                civil_days=163),
    DemoVehicle("corolla", "СВ 7310 НТ", "Toyota", "Corolla 1.8 Hybrid", 2022, VehicleType.CAR,
                FuelType.HYBRID, "NMTBZ3BE", 31_800, 64, 4.7, 43, driver="maria", casco_days=150,
                civil_days=12),
    DemoVehicle("passat", "СА 1952 РВ", "Volkswagen", "Passat Variant 2.0 TDI", 2018, VehicleType.CAR,
                FuelType.DIESEL, "WVWZZZ3C", 168_900, 88, 5.9, 66, driver="nikolay", civil_days=96),
    DemoVehicle("duster", "СН 6604 ВХ", "Dacia", "Duster 1.3 TCe 4x4", 2021, VehicleType.CAR,
                FuelType.PETROL, "UU1HSDAC", 61_200, 58, 7.6, 50, driver="anna", civil_days=-3,
                stop_days=5, stop_status=VehicleStatus.IN_REPAIR,
                stop_note="Подмяна на съединител – в „Авто Сервиз Младост“"),
    DemoVehicle("transit", "В 3378 ТК", "Ford", "Transit Custom 2.0 EcoBlue", 2019, VehicleType.VAN,
                FuelType.DIESEL, "WF0YXXTT", 118_300, 145, 8.6, 70, pool=True, civil_days=25),
    DemoVehicle("sprinter", "В 9045 АН", "Mercedes-Benz", "Sprinter 316 CDI", 2017, VehicleType.VAN,
                FuelType.DIESEL, "WDB9066331", 201_500, 150, 10.4, 75, pool=True, civil_days=214),
    DemoVehicle("master", "РВ 2290 СМ", "Renault", "Master 2.3 dCi", 2016, VehicleType.VAN,
                FuelType.DIESEL, "VF1MA000", 262_100, 125, 10.9, 80, pool=True, civil_days=180,
                stop_days=52, stop_status=VehicleStatus.OUT_OF_SERVICE,
                stop_note="Повреда в скоростната кутия – очаква се оферта за ремонт"),
    DemoVehicle("daily", "ВТ 1066 ОР", "Iveco", "Daily 35S16", 2021, VehicleType.VAN,
                FuelType.DIESEL, "ZCFC135B", 54_600, 135, 11.2, 90, pool=True, civil_days=287),
    DemoVehicle("actros", "СТ 5512 КА", "Mercedes-Benz", "Actros 1845 LS", 2018, VehicleType.TRUCK,
                FuelType.DIESEL, "WDB9634031", 498_000, 510, 29.5, 400, pool=True, civil_days=131),
    DemoVehicle("volvo", "РА 8143 ВВ", "Volvo", "FH 500 4x2", 2020, VehicleType.TRUCK,
                FuelType.DIESEL, "YV2RT40A", 342_500, 545, 28.8, 450, pool=True, casco_days=95,
                civil_days=248),
    DemoVehicle("man", "Е 4410 ХР", "MAN", "TGL 12.250", 2015, VehicleType.TRUCK,
                FuelType.DIESEL, "WMAN13ZZ", 371_200, 290, 22.0, 200, pool=True, civil_days=-40,
                stop_days=160, stop_status=VehicleStatus.OUT_OF_SERVICE,
                stop_note="Неизправна горивна система – предстои решение за бракуване"),
    DemoVehicle("kona", "А 7789 МС", "Hyundai", "Kona Electric 64 kWh", 2023, VehicleType.CAR,
                FuelType.ELECTRIC, "KMHK381G", 12_300, 52, 15.5, 64, driver="elena", casco_days=200,
                civil_days=310),
]

# Fixed points that make the reminders interesting on the demo day:
# (vehicle, activity) -> ("km", km before the current mileage) or ("days", days ago)
FORCED = {
    ("sprinter", OIL): ("km", 10_600),  # overdue by 600 km
    ("passat", INSPECTION): ("days", 369),  # overdue by 4 days
    ("octavia", OIL): ("km", 9_450),  # due in 550 km
    ("kona", TYRES): ("days", 172),  # due in 8 days
    ("transit", BRAKES): ("km", 39_300),  # due in 700 km
}
# Plans whose last execution is before the demo history (baseline only).
BASELINE_DAYS_AGO = {("actros", TACHOGRAPH): 712, ("volvo", TACHOGRAPH): 455, ("man", TACHOGRAPH): 610}

INSURERS = ["ДЗИ", "Булстрад", "Армеец", "Лев Инс", "Алианц България", "Дженерали", "Евроинс"]
STATIONS = ["Shell", "OMV", "Lukoil", "EKO", "Petrol", "Rompetrol"]
CITIES = ["София", "Пловдив", "Варна", "Бургас", "Стара Загора", "Русе", "Велико Търново", "Плевен"]
CAR_SERVICES = ["Авто Сервиз Младост", "Bosch Car Service Люлин", "Мобил Сервиз Варна"]
TRUCK_SERVICES = ["ТИР Сервиз Казичене", "Truck Center Пловдив"]
INSPECTION_POINTS = ["ПГТП „Автотест“ София", "ПГТП „Контрол Авто“ Варна"]
TYRE_SERVICES = ["Гуми Експрес", "Euromaster София"]
REPAIRS = [
    ("Смяна на акумулатор", 140, 220),
    ("Подмяна на предни амортисьори", 320, 520),
    ("Ремонт на алтернатор", 180, 320),
    ("Смяна на крушки и чистачки", 25, 60),
    ("Подмяна на тампони на двигателя", 160, 280),
    ("Ремонт на климатик – смяна на компресор", 650, 950),
    ("Подмяна на датчик NOx / DPF", 210, 380),
    ("Смяна на водна помпа", 260, 420),
    ("Ремонт на ауспухова система", 150, 300),
    ("Смяна на челно стъкло", 380, 650),
]
COST_FACTOR = {VehicleType.CAR: 1.0, VehicleType.VAN: 1.4, VehicleType.TRUCK: 2.6}


@dataclass
class DayEntry:
    day: date
    start_km: int
    end_km: int
    driver: str | None


def _money(value: float) -> Decimal:
    return Decimal(str(round(value, 2)))


def _one_year_earlier(day: date) -> date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # 29 February
        return day.replace(year=day.year - 1, day=28)


def _fuel_price(fuel: str, day: date, rng: random.Random) -> Decimal:
    base = {FuelType.DIESEL: 1.43, FuelType.PETROL: 1.39, FuelType.HYBRID: 1.39,
            FuelType.LPG: 0.62, FuelType.ELECTRIC: 0.29}[fuel]
    t = (day - date(2025, 1, 1)).days
    swing = 0.0 if fuel == FuelType.ELECTRIC else 0.055 * math.sin(t / 58)
    return Decimal(str(round(base + swing + rng.uniform(-0.015, 0.015), 3)))


class DemoBuilder:
    def __init__(self, today: date, seed: int = 19):
        self.today = today
        self.rng = random.Random(seed)
        self.start = today - timedelta(days=480)
        self.trips_from = today - timedelta(days=180)
        self.types: dict[str, MaintenanceType] = {}
        self.drivers: dict[str, Driver] = {}
        self.vehicles: dict[str, Vehicle] = {}
        self.trips: list[dict] = []
        self.driver_busy: dict[str, set[date]] = {}
        # Vehicles that are on the road right now: (vehicle, driver, days since start, purpose)
        self.open_trips = [
            ("transit", "dimitar", 0, "Доставка до клиенти – София"),
            ("daily", "hristo", 0, "Обслужване на обекти – Перник"),
            ("volvo", "stefan", 2, "Транспорт София – Солун – София"),
        ]

    # -- people -------------------------------------------------------------------
    def create_users(self):
        existing = set(db.session.scalars(select(User.username)))
        for username, full_name, role in USERS:
            if username in existing:
                continue
            user = User(username=username, full_name=full_name, role=role)
            user.set_password(DEMO_PASSWORD)
            db.session.add(user)

    def create_drivers(self):
        numbers = self.rng.sample(range(280_000_000, 399_999_999), len(DRIVERS))
        for (key, first, last, phone, categories, expiry_days, active), number in zip(DRIVERS, numbers):
            driver = Driver(
                first_name=first,
                last_name=last,
                phone=phone,
                email=f"{LATIN_NAMES[key]}@avtopark.example",
                license_number=str(number),
                license_categories=categories,
                license_expiry=self.today + timedelta(days=expiry_days),
                active=active,
                notes="Напуснал фирмата" if not active else None,
            )
            db.session.add(driver)
            self.drivers[key] = driver

    # -- vehicles and odometer history -----------------------------------------------
    def create_vehicles(self):
        allowed = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
        for spec in VEHICLES:
            vin = spec.wmi + "".join(self.rng.choice(allowed) for _ in range(17 - len(spec.wmi)))
            vehicle = Vehicle(
                reg_number=spec.reg,
                make=spec.make,
                model=spec.model,
                year=spec.year,
                vehicle_type=spec.vtype,
                fuel_type=spec.fuel,
                vin=vin,
                in_service_since=self.start,
                initial_mileage=spec.start_km,
                current_mileage=spec.start_km,
                mileage_updated_on=self.start,
                responsible_driver=self.drivers[spec.driver] if spec.driver else None,
                status=VehicleStatus.ACTIVE,
                status_changed_at=datetime.combine(self.start, time(9, 0)),
            )
            db.session.add(vehicle)
            self.vehicles[spec.key] = vehicle

    def _driving_days(self, spec: DemoVehicle):
        """Decide for every day how far the vehicle drove (and with whom)."""
        km = spec.start_km
        end_day = self.today
        if spec.stop_days:
            end_day = self.today - timedelta(days=spec.stop_days)
        for key, _driver, days_ago, _purpose in self.open_trips:
            if key == spec.key:
                end_day = min(end_day, self.today - timedelta(days=days_ago))
        day = self.start
        trip: dict | None = None
        while day < end_day:
            weekday = day.weekday()
            driven = 0
            driver = spec.driver
            if spec.pool and day >= self.trips_from:
                if trip is None and weekday < 5 and self.rng.random() < 0.85:
                    span = 1 if spec.vtype != VehicleType.TRUCK else self.rng.randint(2, 5)
                    last_day = min(day + timedelta(days=span - 1), end_day - timedelta(days=1))
                    days = {day + timedelta(days=i) for i in range((last_day - day).days + 1)}
                    trip_driver = self._free_driver(spec, days)
                    if trip_driver:
                        self.driver_busy.setdefault(trip_driver, set()).update(days)
                        trip = {"spec": spec, "driver": trip_driver, "start": day, "end": last_day, "start_km": km}
                        self.trips.append(trip)
                if trip is not None:
                    low, high = (0.7, 1.3) if spec.vtype == VehicleType.TRUCK else (0.5, 1.5)
                    driven = int(spec.daily_km * self.rng.uniform(low, high))
                    driver = trip["driver"]
            else:
                chance = 0.9 if weekday < 5 else (0.25 if weekday == 5 else 0.05)
                if self.rng.random() < chance:
                    driven = int(spec.daily_km * self.rng.uniform(0.55, 1.45))
            spec.entries.append(DayEntry(day, km, km + driven, driver))
            km += driven
            if trip is not None and day >= trip["end"]:
                trip["end_km"] = km
                trip = None
            day += timedelta(days=1)

    def _free_driver(self, spec: DemoVehicle, days: set[date]) -> str | None:
        pool = TRUCK_DRIVERS if spec.vtype == VehicleType.TRUCK else VAN_DRIVERS
        candidates = [
            key for key in pool
            if not (self.driver_busy.get(key, set()) & days)
            and self.drivers[key].license_expiry >= max(days)
            and self.drivers[key].active
        ]
        return self.rng.choice(candidates) if candidates else None

    # -- maintenance -----------------------------------------------------------------
    def create_plans(self, spec: DemoVehicle, vehicle: Vehicle):
        keys = [OIL, INSPECTION, TYRES, BRAKES]
        if spec.fuel == FuelType.ELECTRIC:
            keys.remove(OIL)  # electric vehicles have no engine oil
        if spec.vtype == VehicleType.TRUCK:
            keys.append(TACHOGRAPH)
        if spec.vtype != VehicleType.TRUCK and spec.year <= 2018:
            keys.append(TIMING)
        plans = {}
        for key in keys:
            mtype = self.types[key]
            interval_km = mtype.default_interval_km
            interval_days = mtype.default_interval_days
            if spec.vtype == VehicleType.TRUCK and key == OIL:
                interval_km = 60_000  # trucks use long-life oil
            if (spec.key, key) in BASELINE_DAYS_AGO:
                offset = BASELINE_DAYS_AGO[(spec.key, key)] - (self.today - self.start).days
            else:
                offset = self.rng.randint(15, min(interval_days or 300, 300) - 15)
            baseline_km = spec.start_km - int(offset * spec.daily_km * 0.6)
            if interval_km:
                baseline_km = max(baseline_km, spec.start_km - int(interval_km * 0.85))
            plan = MaintenancePlan(
                vehicle=vehicle,
                maintenance_type=mtype,
                interval_km=interval_km,
                interval_days=interval_days,
                baseline_date=self.start - timedelta(days=offset),
                baseline_mileage=max(0, baseline_km),
            )
            db.session.add(plan)
            plans[key] = plan
        return plans

    def _service_details(self, key: str, spec: DemoVehicle, day: date):
        truck = spec.vtype == VehicleType.TRUCK
        factor = COST_FACTOR[spec.vtype]
        rng = self.rng
        if key == OIL:
            text = ("Смяна на масло 10W-40 (38 л), маслен и горивни филтри" if truck
                    else "Смяна на двигателно масло 5W-30, маслен, въздушен и горивен филтър")
            cost = rng.uniform(470, 640) if truck else rng.uniform(135, 190) * (1.3 if spec.vtype == VehicleType.VAN else 1)
            provider = rng.choice(TRUCK_SERVICES if truck else CAR_SERVICES)
        elif key == INSPECTION:
            text, cost = "Годишен технически преглед – без забележки", {1.0: 38, 1.4: 45, 2.6: 85}[factor]
            provider = rng.choice(INSPECTION_POINTS)
        elif key == TYRES:
            text = "Монтаж на зимни гуми, баланс" if day.month in (9, 10, 11, 12, 1, 2) else "Монтаж на летни гуми, баланс"
            cost = rng.uniform(48, 70) * factor
            provider = rng.choice(TYRE_SERVICES)
        elif key == BRAKES:
            text = "Смяна на предни накладки и дискове, спирачна течност"
            cost = rng.uniform(230, 380) * factor
            provider = rng.choice(TRUCK_SERVICES if truck else CAR_SERVICES)
        elif key == TACHOGRAPH:
            text, cost = "Периодична проверка и калибриране на тахографа", rng.uniform(110, 150)
            provider = rng.choice(TRUCK_SERVICES)
        else:  # timing belt
            text, cost = "Смяна на ангренажен комплект и водна помпа", rng.uniform(420, 620) * factor
            provider = rng.choice(CAR_SERVICES)
        return text, _money(cost), provider

    def _forced_window(self, spec: DemoVehicle, plan: MaintenancePlan, key: str, final_km: int):
        """(forced day, first day of the window in which normal scheduling is paused)."""
        kind, value = FORCED[(spec.key, key)]
        if kind == "days":
            forced_day = self.today - timedelta(days=value)
            return forced_day, forced_day - timedelta(days=(plan.interval_days or 365) // 2)
        target = final_km - value
        forced_day = next((e.day for e in spec.entries if e.end_km >= target), None)
        pause_km = target - (plan.interval_km or 0) // 2
        pause_day = next((e.day for e in spec.entries if e.end_km >= pause_km), forced_day)
        return forced_day, pause_day

    def create_history(self, spec: DemoVehicle, vehicle: Vehicle, plans: dict):
        rng = self.rng
        final_km = spec.entries[-1].end_km if spec.entries else spec.start_km
        last = {key: (plan.baseline_date, plan.baseline_mileage) for key, plan in plans.items()}
        forced = {
            key: self._forced_window(spec, plan, key, final_km)
            for key, plan in plans.items() if (spec.key, key) in FORCED
        }
        fuel_used = 0.0
        refill_at = spec.tank * rng.uniform(0.55, 0.8)
        reading_every = 45 if not spec.pool else None
        km_by_day = {}

        for entry in spec.entries:
            day, km = entry.day, entry.end_km
            km_by_day[day] = km
            driven = entry.end_km - entry.start_km
            fuel_used += driven * spec.consumption / 100 * rng.uniform(0.93, 1.07)

            for key, plan in plans.items():
                if key in forced:
                    forced_day, pause_day = forced[key]
                    if day == forced_day:
                        self._add_service(spec, vehicle, key, day, km)
                    if pause_day is not None and day >= pause_day:
                        continue
                last_day, last_km = last[key]
                due_km = plan.interval_km and km - last_km >= plan.interval_km * 0.96
                due_days = plan.interval_days and (day - last_day).days >= plan.interval_days - 4
                if (due_km or due_days) and day.weekday() < 6:
                    self._add_service(spec, vehicle, key, day, km)
                    last[key] = (day, km)

            if driven and rng.random() < 0.0045:
                text, low, high = rng.choice(REPAIRS)
                db.session.add(MaintenanceRecord(
                    vehicle=vehicle, maintenance_type=self.types[REPAIR], performed_on=day, mileage=km,
                    description=text, cost=_money(rng.uniform(low, high) * COST_FACTOR[spec.vtype]),
                    service_provider=rng.choice(TRUCK_SERVICES if spec.vtype == VehicleType.TRUCK else CAR_SERVICES),
                ))

            if fuel_used >= refill_at:
                full = rng.random() < 0.9
                quantity = fuel_used if full else fuel_used * rng.uniform(0.4, 0.7)
                price = _fuel_price(spec.fuel, day, rng)
                quantity_dec = Decimal(str(round(quantity, 2)))
                if spec.fuel == FuelType.ELECTRIC:
                    station = rng.choice(["Eldrive", "Fines Charge", "Зарядна станция – офис"])
                else:
                    station = f"{rng.choice(STATIONS)} {rng.choice(CITIES)}"
                driver = self.drivers[entry.driver] if entry.driver else None
                db.session.add(FuelRecord(
                    vehicle=vehicle, driver=driver, filled_on=day, mileage=km, quantity=quantity_dec,
                    price_per_unit=price, total_cost=compute_total(quantity_dec, price), full_tank=full,
                    station=station,
                ))
                fuel_used = 0.0 if full else fuel_used - quantity
                refill_at = spec.tank * rng.uniform(0.55, 0.8)

            if reading_every and (day - self.start).days % reading_every == 44:
                db.session.add(OdometerReading(vehicle=vehicle, read_on=day, mileage=km, note="Месечен отчет"))

        if spec.stop_days:
            self._stop_vehicle(spec, vehicle, final_km)

    def _add_service(self, spec, vehicle, key, day, km):
        text, cost, provider = self._service_details(key, spec, day)
        db.session.add(MaintenanceRecord(
            vehicle=vehicle, maintenance_type=self.types[key], performed_on=day, mileage=km,
            description=text, cost=cost, service_provider=provider,
        ))

    def _stop_vehicle(self, spec: DemoVehicle, vehicle: Vehicle, km: int):
        day = self.today - timedelta(days=spec.stop_days)
        vehicle.status = spec.stop_status
        vehicle.status_note = spec.stop_note
        vehicle.status_changed_at = datetime.combine(day, time(9, 30))
        if spec.stop_status == VehicleStatus.OUT_OF_SERVICE:
            texts = {
                "master": ("Установена повреда в скоростната кутия (лагери на 5-та предавка)", 60),
                "man": ("Диагностика на горивна система – необходима подмяна на ГНП и дюзи, оценка 6 800 €", 85),
            }
            text, cost = texts[spec.key]
            db.session.add(MaintenanceRecord(
                vehicle=vehicle, maintenance_type=self.types[DIAGNOSTICS], performed_on=day, mileage=km,
                description=text, cost=Decimal(cost), service_provider=self.rng.choice(TRUCK_SERVICES),
            ))

    # -- trips -------------------------------------------------------------------------
    def create_trips(self):
        purposes_van = ["Доставка до клиенти – София", "Доставка София – Пловдив", "Обслужване на обекти – Перник",
                        "Зареждане на склад Казичене", "Доставка до клиенти – Варна", "Монтаж на оборудване – Бургас"]
        purposes_truck = ["Транспорт София – Солун – София", "Транспорт Пловдив – Букурещ", "Курс София – Варна – София",
                          "Международен курс София – Будапеща", "Доставка на материали – Русе"]
        for trip in self.trips:
            spec = trip["spec"]
            truck = spec.vtype == VehicleType.TRUCK
            started = datetime.combine(trip["start"], time(self.rng.randint(6, 8), self.rng.choice([0, 15, 30, 45])))
            ended = datetime.combine(trip["end"], time(self.rng.randint(16, 19), self.rng.choice([0, 10, 25, 40, 55])))
            db.session.add(Trip(
                vehicle=self.vehicles[spec.key], driver=self.drivers[trip["driver"]], started_at=started,
                ended_at=ended, start_mileage=trip["start_km"], end_mileage=trip["end_km"],
                purpose=self.rng.choice(purposes_truck if truck else purposes_van),
            ))
        # Vehicles that are on the road right now (open trips).
        for index, (key, driver_key, days_ago, purpose) in enumerate(self.open_trips):
            started = datetime.combine(self.today - timedelta(days=days_ago), time(6 + index, 10 + index * 15))
            spec = next(v for v in VEHICLES if v.key == key)
            km = spec.entries[-1].end_km if spec.entries else spec.start_km
            db.session.add(Trip(
                vehicle=self.vehicles[key], driver=self.drivers[driver_key], started_at=started,
                start_mileage=km, purpose=purpose,
            ))

    # -- insurance -------------------------------------------------------------------
    def create_policies(self, spec: DemoVehicle, vehicle: Vehicle):
        factor = COST_FACTOR[spec.vtype]
        insurer = self.rng.choice(INSURERS)
        for policy_type, end_days, premium in (
            (PolicyType.CIVIL_LIABILITY, spec.civil_days, 255 * factor ** 2.4),
            (PolicyType.CASCO, spec.casco_days, 690 * factor ** 1.9),
        ):
            if end_days is None:
                continue
            end = self.today + timedelta(days=end_days)
            while end >= self.start:
                start = _one_year_earlier(end) + timedelta(days=1)
                number = f"{self.rng.randint(10, 99)}{self.rng.randint(10_000_000, 99_999_999)}"
                db.session.add(InsurancePolicy(
                    vehicle=vehicle, policy_type=policy_type, insurer=insurer,
                    policy_number=f"BG/{start:%y}/{number}", start_date=start, end_date=end,
                    premium=_money(premium * self.rng.uniform(0.93, 1.08)),
                ))
                end = start - timedelta(days=1)

    def build(self):
        self.types = {t.name: t for t in db.session.scalars(select(MaintenanceType))}
        self.create_users()
        self.create_drivers()
        self.create_vehicles()
        db.session.flush()
        for _key, driver_key, days_ago, _purpose in self.open_trips:
            busy = {self.today - timedelta(days=i) for i in range(days_ago + 1)}
            self.driver_busy.setdefault(driver_key, set()).update(busy)
        for spec in sorted(VEHICLES, key=lambda s: (s.vtype != VehicleType.TRUCK, s.key)):
            self._driving_days(spec)
        for spec in VEHICLES:
            vehicle = self.vehicles[spec.key]
            plans = self.create_plans(spec, vehicle)
            self.create_history(spec, vehicle, plans)
            self.create_policies(spec, vehicle)
        self.create_trips()
        db.session.flush()
        for vehicle in self.vehicles.values():
            refresh_vehicle_mileage(vehicle)
        db.session.add(AuditLog(action="create", entity="demo", details="Заредени демонстрационни данни"))
        db.session.commit()


def create_demo_data(today: date | None = None) -> None:
    """Fill an empty database with the demo fleet (reference data included)."""
    seed_reference_data()
    DemoBuilder(today or date.today()).build()
