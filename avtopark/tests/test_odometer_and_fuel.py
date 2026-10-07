"""Mileage consistency rules and fuel calculations."""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import pytest

from avtopark.services.errors import BusinessRuleError
from avtopark.services.fuel import average_consumption, compute_total
from avtopark.services.odometer import average_daily_km, validate_reading
from avtopark.services.records import add_odometer_reading, delete_mileage_record, save_fuel_record

TODAY = date.today()


@pytest.fixture
def vehicle_with_reading(db, make_vehicle):
    """Vehicle: 10 000 km a year ago, 20 000 km 100 days ago."""
    vehicle = make_vehicle(mileage=10_000, since=TODAY - timedelta(days=365))
    add_odometer_reading(vehicle, TODAY - timedelta(days=100), 20_000)
    db.session.commit()
    return vehicle


def test_current_mileage_follows_readings(vehicle_with_reading):
    assert vehicle_with_reading.current_mileage == 20_000
    assert vehicle_with_reading.mileage_updated_on == TODAY - timedelta(days=100)


def test_reading_lower_than_earlier_one_is_rejected(vehicle_with_reading):
    with pytest.raises(BusinessRuleError) as error:
        validate_reading(vehicle_with_reading, TODAY - timedelta(days=50), 15_000)
    assert "по-малък" in error.value.message
    assert error.value.field == "mileage"


def test_reading_higher_than_later_one_is_rejected(vehicle_with_reading):
    with pytest.raises(BusinessRuleError) as error:
        validate_reading(vehicle_with_reading, TODAY - timedelta(days=150), 25_000)
    assert "по-голям" in error.value.message


def test_reading_in_the_future_is_rejected(vehicle_with_reading):
    with pytest.raises(BusinessRuleError) as error:
        validate_reading(vehicle_with_reading, TODAY + timedelta(days=1), 21_000, date_field="filled_on")
    assert error.value.field == "filled_on"


def test_unrealistic_growth_is_rejected(vehicle_with_reading):
    # 2000 km per day is the limit: an extra zero is caught.
    validate_reading(vehicle_with_reading, TODAY - timedelta(days=99), 22_000)
    with pytest.raises(BusinessRuleError) as error:
        validate_reading(vehicle_with_reading, TODAY - timedelta(days=99), 22_001)
    assert "грешка" in error.value.message


def test_valid_reading_between_existing_ones(vehicle_with_reading):
    validate_reading(vehicle_with_reading, TODAY - timedelta(days=200), 15_000)
    validate_reading(vehicle_with_reading, TODAY, 20_000)  # equal to the previous reading is fine


def test_reading_below_initial_mileage_is_rejected(vehicle_with_reading):
    with pytest.raises(BusinessRuleError):
        validate_reading(vehicle_with_reading, TODAY - timedelta(days=300), 9_000)


def test_deleting_a_record_recalculates_mileage(db, vehicle_with_reading):
    record = save_fuel_record(
        None, vehicle=vehicle_with_reading, driver=None, filled_on=TODAY, mileage=21_500,
        quantity=Decimal("40"), price_per_unit=Decimal("1.459"),
    )
    db.session.commit()
    assert vehicle_with_reading.current_mileage == 21_500
    delete_mileage_record(record)
    db.session.commit()
    assert vehicle_with_reading.current_mileage == 20_000


def test_average_daily_km(db, make_vehicle):
    vehicle = make_vehicle(mileage=10_000, since=TODAY - timedelta(days=200))
    add_odometer_reading(vehicle, TODAY - timedelta(days=100), 15_000)
    add_odometer_reading(vehicle, TODAY - timedelta(days=10), 19_500)
    db.session.commit()
    assert average_daily_km(vehicle, TODAY) == pytest.approx(50.0)


# --- Fuel ------------------------------------------------------------------------

def test_total_is_rounded_to_cents():
    assert compute_total(Decimal("41.37"), Decimal("1.459")) == Decimal("60.36")  # 60.358...
    assert compute_total(Decimal("0.01"), Decimal("0.001")) == Decimal("0.00")


@dataclass
class Fill:
    mileage: int
    quantity: Decimal
    full_tank: bool = True
    filled_on: date = TODAY


def test_consumption_full_tank_method():
    fills = [Fill(1000, Decimal("50")), Fill(1500, Decimal("30")), Fill(2000, Decimal("29"))]
    # 59 litres for 1000 km -> 5.9 l/100 km (the first fill is not counted)
    assert average_consumption(fills) == Decimal("5.90")


def test_consumption_counts_partial_fills_in_between():
    fills = [Fill(1000, Decimal("50")), Fill(1400, Decimal("10"), full_tank=False), Fill(2000, Decimal("50"))]
    assert average_consumption(fills) == Decimal("6.00")


def test_consumption_needs_two_full_fills():
    assert average_consumption([]) is None
    assert average_consumption([Fill(1000, Decimal("50"))]) is None
    assert average_consumption([Fill(1000, Decimal("50")), Fill(1500, Decimal("20"), full_tank=False)]) is None


def test_fuel_record_rejects_zero_quantity(db, make_vehicle):
    vehicle = make_vehicle()
    with pytest.raises(BusinessRuleError):
        save_fuel_record(None, vehicle=vehicle, driver=None, filled_on=TODAY, mileage=10_100,
                         quantity=Decimal("0"), price_per_unit=Decimal("1.5"))
