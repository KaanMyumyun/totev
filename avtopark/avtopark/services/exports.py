"""File exports: CSV for spreadsheets and iCalendar (.ics) for calendar applications."""
import csv
import io
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from flask import Response


def _csv_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f").replace(".", ",")
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    text = str(value)
    # A cell starting with = + - @ would be run as a formula by Excel (CSV injection).
    if isinstance(value, str) and text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def csv_response(filename: str, header: list[str], rows) -> Response:
    """CSV with ';' separators and a UTF-8 BOM, so Excel opens Cyrillic text correctly."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(header)
    for row in rows:
        writer.writerow([_csv_cell(value) for value in row])
    return Response(
        "\ufeff" + buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _ics_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    )


def _ics_fold(line: str, limit: int = 75) -> str:
    """Fold a line to at most 75 octets (RFC 5545) without splitting UTF-8 characters."""
    if len(line.encode()) <= limit:
        return line
    parts, current, size, max_size = [], "", 0, limit
    for char in line:
        char_size = len(char.encode())
        if size + char_size > max_size:
            parts.append(current)
            current, size, max_size = "", 0, limit - 1  # continuation lines start with a space
        current += char
        size += char_size
    parts.append(current)
    return "\r\n ".join(parts)


def build_ics(reminders, today: date) -> str:
    """Calendar with an all-day event per reminder (overdue ones are placed today)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Avtopark//Fleet maintenance//BG",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Автопарк – обслужване",
    ]
    for reminder in reminders:
        day = reminder.deadline
        if day is None:
            continue
        day = max(day, today)
        if reminder.vehicle is not None:
            subject = reminder.vehicle.reg_number
        else:
            subject = reminder.driver.full_name if reminder.driver is not None else ""
        summary = f"{reminder.title} – {subject}" if subject else reminder.title
        description = f"{reminder.level_label}. Последно: {reminder.last_done}"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{reminder.uid}-{day:%Y%m%d}@avtopark",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
            f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{_ics_escape(summary)}",
            f"DESCRIPTION:{_ics_escape(description)}",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            "TRIGGER:-P1D",
            f"DESCRIPTION:{_ics_escape(summary)}",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_ics_fold(line) for line in lines) + "\r\n"
