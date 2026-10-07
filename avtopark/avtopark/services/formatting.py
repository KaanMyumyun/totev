"""Bulgarian formatting of numbers, money and dates (1 234,56 €; 07.10.2026)."""
from datetime import date, datetime
from decimal import Decimal

NBSP = " "


def format_number(value, decimals: int = 0) -> str:
    if value is None:
        return "—"
    text = f"{value:,.{decimals}f}"
    return text.replace(",", NBSP).replace(".", ",")


def format_money(value, currency: str = "€") -> str:
    if value is None:
        return "—"
    return f"{format_number(Decimal(value), 2)}{NBSP}{currency}"


def format_km(value) -> str:
    if value is None:
        return "—"
    return f"{format_number(value)}{NBSP}км"


def format_date(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        value = value.date()
    return value.strftime("%d.%m.%Y")


def format_datetime(value) -> str:
    if value is None:
        return "—"
    return value.strftime("%d.%m.%Y %H:%M")


def days_word(n: int) -> str:
    return "ден" if abs(n) == 1 else "дни"


def days_left_text(days: int | None) -> str:
    """Human readable distance to a deadline: 'след 5 дни', 'днес', 'просрочено с 3 дни'."""
    if days is None:
        return "—"
    if days == 0:
        return "днес"
    if days > 0:
        return f"след {days} {days_word(days)}"
    return f"просрочено с {-days} {days_word(days)}"


def km_left_text(km: int | None) -> str:
    if km is None:
        return "—"
    if km >= 0:
        return f"остават {format_km(km)}"
    return f"просрочено с {format_km(-km)}"


def month_label(month: date) -> str:
    names = ["яну", "фев", "мар", "апр", "май", "юни", "юли", "авг", "сеп", "окт", "ное", "дек"]
    return f"{names[month.month - 1]} {month.year}"
