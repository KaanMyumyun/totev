"""Unit tests: registration numbers, VIN, passwords and Bulgarian formatting."""
from decimal import Decimal

import pytest

from avtopark.services.errors import BusinessRuleError
from avtopark.services.formatting import days_left_text, format_money, format_number, km_left_text
from avtopark.services.validators import check_password_strength, normalize_plate, normalize_vin


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("СВ 1234 АВ", "СВ 1234 АВ"),  # canonical Cyrillic
        ("СВ1234АВ", "СВ 1234 АВ"),  # without spaces
        ("cb1234ab", "СВ 1234 АВ"),  # Latin look-alikes, lower case
        ("CB-1234-AB", "СВ 1234 АВ"),  # dashes
        ("В 3378 ТК", "В 3378 ТК"),  # one-letter region (Варна)
        ("  а 7789 мс ", "А 7789 МС"),  # Cyrillic lower case and spaces around
    ],
)
def test_normalize_plate_accepts_valid_formats(raw, expected):
    assert normalize_plate(raw) == expected


@pytest.mark.parametrize("raw", ["", "123456", "СВ 123 АВ", "СВ 12345 АВ", "ABC 1234 AB", "СВ 1234 ЖЗ", "СВ 1234 А"])
def test_normalize_plate_rejects_invalid(raw):
    with pytest.raises(BusinessRuleError) as error:
        normalize_plate(raw)
    assert error.value.field == "reg_number"


def test_vin_is_optional_and_normalised():
    assert normalize_vin("") is None
    assert normalize_vin("   ") is None
    assert normalize_vin("wvwzzz3czwe123456") == "WVWZZZ3CZWE123456"


@pytest.mark.parametrize("raw", ["WVWZZZ3CZWE12345", "WVWZZZ3CZWE1234567", "WVWZZZ3CZWI123456", "WVWZZZ3CZWO12345Q"])
def test_vin_rejects_wrong_length_and_letters_i_o_q(raw):
    with pytest.raises(BusinessRuleError):
        normalize_vin(raw)


@pytest.mark.parametrize("password", ["short1", "onlyletters", "12345678", ""])
def test_weak_passwords_are_rejected(password):
    with pytest.raises(BusinessRuleError):
        check_password_strength(password)


def test_strong_password_is_accepted():
    check_password_strength("Avtopark2026")
    check_password_strength("парола123")  # Cyrillic letters count as letters


def test_bulgarian_number_formatting():
    nbsp = " "
    assert format_number(1234567) == f"1{nbsp}234{nbsp}567"
    assert format_number(Decimal("1234.5"), 2) == f"1{nbsp}234,50"
    assert format_money(Decimal("1829.36")) == f"1{nbsp}829,36{nbsp}€"
    assert format_number(None) == "—"


@pytest.mark.parametrize(
    "days, text",
    [(0, "днес"), (1, "след 1 ден"), (12, "след 12 дни"), (-1, "просрочено с 1 ден"), (-4, "просрочено с 4 дни")],
)
def test_days_left_text(days, text):
    assert days_left_text(days) == text


def test_km_left_text():
    assert km_left_text(550).startswith("остават 550")
    assert km_left_text(-469).startswith("просрочено с 469")
