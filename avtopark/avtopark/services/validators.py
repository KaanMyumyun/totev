"""Validation and normalisation of identifiers entered by users."""
import re

from .errors import BusinessRuleError

# Bulgarian plates use only the 12 Cyrillic letters that look like Latin ones.
# Users often type them with a Latin keyboard, so Latin look-alikes are converted.
PLATE_LETTERS = "АВЕКМНОРСТУХ"
_LATIN_TO_CYRILLIC = str.maketrans("ABEKMHOPCTYX", PLATE_LETTERS)
_PLATE_RE = re.compile(rf"^([{PLATE_LETTERS}]{{1,2}})(\d{{4}})([{PLATE_LETTERS}]{{2}})$")

# VIN: 17 characters, the letters I, O and Q are not allowed (ISO 3779).
_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


def normalize_plate(raw: str) -> str:
    """Return a registration number in the canonical form 'СВ 1234 АВ'."""
    compact = re.sub(r"[\s\-]", "", (raw or "").upper()).translate(_LATIN_TO_CYRILLIC)
    match = _PLATE_RE.match(compact)
    if not match:
        raise BusinessRuleError(
            "Невалиден регистрационен номер. Очакван формат: СВ 1234 АВ или В 1234 АВ.",
            field="reg_number",
        )
    return " ".join(match.groups())


def normalize_vin(raw: str | None) -> str | None:
    vin = re.sub(r"\s", "", raw or "").upper()
    if not vin:
        return None
    if not _VIN_RE.match(vin):
        raise BusinessRuleError(
            "VIN номерът трябва да съдържа точно 17 латински букви и цифри (без I, O и Q).",
            field="vin",
        )
    return vin


def check_password_strength(password: str, field: str = "password") -> None:
    if (
        len(password or "") < 8
        or not re.search(r"[^\W\d_]", password)
        or not re.search(r"\d", password)
    ):
        raise BusinessRuleError(
            "Паролата трябва да е поне 8 символа и да съдържа както букви, така и цифри.",
            field=field,
        )
