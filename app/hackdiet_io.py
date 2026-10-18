# Kilo Tracker - a small weight-tracking web app.
# Copyright (C) 2026 Ramón de Vries <ramon@11tools.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version. See the LICENSE file for the full text.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Import of the CSV and XML exports of The Hacker's Diet Online.

Both exports hold every day of the log (most of them empty). Of everything in
them only the date, the weight, the note ("comment") and, in the XML, the height
are used; the account, plan, trend, rung and flag data are ignored, and the
login name and email address in them are never even read.

CSV: a few metadata lines (`Epoch`, `User`, `Preferences`, `Diet-Plan`), then
for every month a `Date,Weight,Rung,Flag,Comment` header, a `StartTrend` line
and one `YYYY-MM-DD,weight,rung,flag,comment` line per day. It has no height.
Windows-1252 text is common.

XML: `<hackersdiet>` with `<account>` (the height is in `<user><height>`) and a
`<monthlog>` per month with `<properties>` (year, month, weight-unit) and one
`<day>` (date = day of the month, weight, comment) per day. The file points at
a DTD with a DOCTYPE; the DTD is never fetched, and a document that declares
entities of its own is refused (that is how XML expansion attacks work).

Weights are converted to kilograms from the unit the file declares; a unit that
cannot be converted reliably (stone) refuses the file rather than import wrong
numbers. The height is only used if the account is metric, where it is in cm.
"""

import re
import xml.etree.ElementTree as ET
from functools import partial

from flask_babel import gettext as _

from app.csv_io import (
    LEADING_JUNK,
    CsvRowError,
    ImportFileError,
    ImportResult,
    data_rows,
    line_weight_error,
    parse_ddmyy,
    weight_kg,
)
from app.utils import MAX_HEIGHT_CM, MIN_HEIGHT_CM, NOTE_MAX_LENGTH, parse_decimal

POUND_KG = 0.45359237
# Weights are rounded to grams: a pound is 0.45359237 kg, so 180.4 lb has a long tail.
WEIGHT_DIGITS = 3
UNIT_FACTORS = {
    "kilogram": 1.0, "kilograms": 1.0, "kg": 1.0,
    "pound": POUND_KG, "pounds": POUND_KG, "lb": POUND_KG, "lbs": POUND_KG,
}
METRIC_UNITS = ("kilogram", "kilograms", "kg")

# CSV lines that are not days: the metadata at the top and the per-month header/trend lines.
CSV_SKIPPED_LINES = {"epoch", "user", "preferences", "diet-plan", "date", "starttrend"}
ISO_DATE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$")


def looks_like_hackdiet_csv(text):
    """True if the first line is the `Epoch,...` line or the column header of a Hacker's Diet CSV."""
    for line in text.splitlines():
        if line.strip():
            first = line.split(",")[0].strip().lower()
            return first == "epoch" or line.strip().lower().replace(" ", "").startswith("date,weight,rung")
    return False


def _unit_factor(unit):
    """kg per unit for a unit name from the file, or an ImportFileError for one we cannot convert."""
    factor = UNIT_FACTORS.get((unit or "kilogram").strip().lower())
    if factor is None:
        raise ImportFileError(_("That file stores weights in %(unit)s, which can't be imported.", unit=unit))
    return factor


def _iso_date(text):
    """The date of a `2026-09-23` cell, or None (the CSV writes ISO dates only)."""
    if not ISO_DATE.match(text):
        return None
    try:
        return parse_ddmyy(text)
    except CsvRowError:
        return None


def _add_weighing(result, entry_date, weight_text, note, factor, weight_error):
    """One day into `result`: a row, an error (`weight_error(text, problem)` words it), or a
    skipped note (a note needs a weight to hang on)."""
    if not weight_text:
        if note:
            result.notes_skipped += 1
        return
    weight, problem = weight_kg(weight_text, factor, digits=WEIGHT_DIGITS)
    if problem:
        result.errors.append(weight_error(weight_text, problem))
    else:
        result.rows.append((entry_date, weight, note or None))


def parse_hackdiet_csv(text):
    """Parse the text of a Hacker's Diet CSV export into an `ImportResult`."""
    result = ImportResult()
    factor = 1.0
    for lineno, row in data_rows(text):
        key = row[0].strip()
        if key.lower() == "preferences":
            factor = _unit_factor(row[2] if len(row) > 2 else "")  # the unit weights are logged in
            continue
        if key.lower() in CSV_SKIPPED_LINES:
            continue

        weight_text = row[1].strip() if len(row) > 1 else ""
        note = ",".join(row[4:]).strip()[:NOTE_MAX_LENGTH] if len(row) > 4 else ""
        entry_date = _iso_date(key)
        if entry_date is None:
            result.errors.append(_("line %(line)d: invalid date %(value)s", line=lineno, value=repr(key)))
            continue
        _add_weighing(result, entry_date, weight_text, note, factor, partial(line_weight_error, lineno))
    return result


def _height_cm(root, metric):
    """The account's height in cm, if there is a sensible one (metric accounts only)."""
    if not metric:
        return None  # in another unit system we cannot tell what the number means
    value = parse_decimal((root.findtext("account/user/height") or "").strip())
    if value is None:
        return None
    if MIN_HEIGHT_CM <= value <= MAX_HEIGHT_CM:
        return value
    return None


def _month(monthlog, account_unit):
    """(year, month, kg factor) of a `<monthlog>`, or None when its year or month is missing."""
    properties = monthlog.find("properties")
    if properties is None:
        return None
    year = parse_decimal(properties.findtext("year") or "")
    month = parse_decimal(properties.findtext("month") or "")
    if year is None or month is None:
        return None
    return int(year), int(month), _unit_factor(properties.findtext("weight-unit") or account_unit)


def _day_date(day, year, month):
    """(date or None, label) of a `<day>`; the label names the day in error messages."""
    value = parse_decimal((day.findtext("date") or "").strip())
    number = int(value) if value is not None else None
    label = f"{year:04d}-{month:02d}" + (f"-{number:02d}" if number is not None else "")
    if number is None:
        return None, label
    try:
        return parse_ddmyy(f"{year:04d}-{month}-{number}"), label
    except CsvRowError:
        return None, label


def _day_weight_error(label, text, problem):
    """The import's message for an XML day whose weight `weight_kg` refused."""
    if problem == "invalid":
        return _("%(date)s: invalid weight %(value)s", date=label, value=repr(text))
    return _("%(date)s: weight out of range", date=label)


def parse_hackdiet_xml(data):
    """Parse the bytes of a Hacker's Diet XML export into an `ImportResult`."""
    invalid = ImportFileError(_("That file isn't a valid Hacker's Diet XML export."))
    data = data.lstrip(LEADING_JUNK)  # the XML declaration must be the very first thing
    if b"<!ENTITY" in data:
        raise invalid
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise invalid from exc
    if root.tag != "hackersdiet":
        raise invalid

    account_unit = (root.findtext("account/preferences/log-unit") or "kilogram").strip().lower()
    display_unit = (root.findtext("account/preferences/display-unit") or account_unit).strip().lower()
    result = ImportResult(height_cm=_height_cm(root, display_unit in METRIC_UNITS))

    for monthlog in root.iterfind("monthlogs/monthlog"):
        month = _month(monthlog, account_unit)
        if month is None:
            continue
        year, month_number, factor = month
        for day in monthlog.iterfind("days/day"):
            weight_text = (day.findtext("weight") or "").strip()
            note = (day.findtext("comment") or "").strip()[:NOTE_MAX_LENGTH]
            if not weight_text and not note:
                continue
            entry_date, label = _day_date(day, year, month_number)
            if entry_date is None:
                result.errors.append(_("%(date)s: invalid date", date=label))
                continue
            _add_weighing(result, entry_date, weight_text, note, factor, partial(_day_weight_error, label))
    return result
