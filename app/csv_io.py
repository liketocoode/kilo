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

"""CSV import and export of weight entries.

The format is one `date,weight[,note]` line per entry, with dates as d-M-yy or
d-M-yyyy (or ISO, 2026-09-23) and weights in kilograms. A first line that looks
like a header (neither a date nor a weight) is skipped without a warning.
This module also holds what every importer shares: the result type, the
"this file cannot be imported at all" error and the text decoding.

A note that a spreadsheet would read as a formula (it starts with =, +, -, @, a tab
or a carriage return) is exported with an apostrophe in front, and the apostrophe is
taken off again on import (see `protect_cell`).
"""

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date

from flask_babel import gettext as _

from app.utils import NOTE_MAX_LENGTH, parse_decimal

MIN_WEIGHT_KG = 1
MAX_WEIGHT_KG = 500

# What may come before the first real byte of an uploaded file: a UTF-8 byte order mark and
# whitespace. An XML file is recognised (and parsed) after stripping it.
LEADING_JUNK = b"\xef\xbb\xbf \t\r\n"

# Excel, LibreOffice and Google Sheets treat a cell that starts with one of these as a formula
# ("CSV injection"): a note such as =HYPERLINK(...) would run when the export is opened. An
# apostrophe in front makes the spreadsheet show the text as it is. A note that already starts
# with apostrophes before such a character gets one more, so the import can always take exactly
# one off and every note survives an export and import unchanged.
_FORMULA_START = re.compile(r"'*[=+\-@\t\r]")
_PROTECTED = re.compile(r"'+[=+\-@\t\r]")


def protect_cell(text):
    """`text` made safe to open in a spreadsheet: an apostrophe in front of a would-be formula."""
    return "'" + text if _FORMULA_START.match(text) else text


def unprotect_cell(text):
    """The reverse of `protect_cell`, for a note read from a CSV file."""
    return text[1:] if _PROTECTED.match(text) else text


class CsvRowError(Exception):
    """A CSV row could not be parsed (the caller words the message, with the line number)."""


class ImportFileError(Exception):
    """The whole file cannot be imported; the (translated) message says why."""


@dataclass
class ImportResult:
    """What an importer found: entries to save, per-row problems, and optional extras."""

    rows: list = field(default_factory=list)  # (entry_date, weight_kg, note)
    errors: list = field(default_factory=list)  # translated one-line descriptions of skipped rows
    height_cm: float | None = None  # a height found in the file, if it has one
    notes_skipped: int = 0  # days that have a note but no weight (an entry needs a weight)


def decode_text(data):
    """Text from file bytes: UTF-8 (with or without BOM), else Windows-1252.

    Spreadsheets and older programs write Windows-1252 ("ñ" as one byte), which
    is not valid UTF-8; Latin-1 is the last resort because it accepts any byte.
    """
    if isinstance(data, str):
        return data
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1")


def parse_ddmyy(text):
    """Parse a d-M-yy or d-M-yyyy date ('23-9-26', '23-9-2026') or an ISO date ('2026-09-23')."""
    parts = text.strip().split("-")
    if len(parts) != 3:
        raise CsvRowError()
    try:
        numbers = [int(p) for p in parts]
    except ValueError as exc:
        raise CsvRowError() from exc
    if len(parts[0]) == 4:
        year, month, day = numbers  # ISO, year first
    else:
        day, month, year = numbers
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise CsvRowError() from exc


def format_ddmyy(d):
    """Format a date as d-M-yy, e.g. 23-9-26 (the reverse of `parse_ddmyy`)."""
    return f"{d.day}-{d.month}-{d.strftime('%y')}"


def data_rows(text):
    """(line number, cells) for every line of a CSV text that is not blank."""
    for lineno, row in enumerate(csv.reader(io.StringIO(text)), start=1):
        if row and any(cell.strip() for cell in row):
            yield lineno, row


def weight_kg(text, factor=1.0, digits=None):
    """(weight in kg or None, problem) for a weight cell: problem is None, "invalid" or "range".

    `factor` converts the file's unit to kg (pounds: 0.45359237); `digits` rounds the result
    (the Hacker's Diet importers use 3, so pounds do not turn into long fractions of a kilogram).
    """
    value = parse_decimal(text)
    if value is None:
        return None, "invalid"
    kg = value * factor
    if digits is not None:
        kg = round(kg, digits)
    if not (MIN_WEIGHT_KG <= kg <= MAX_WEIGHT_KG):
        return None, "range"
    return kg, None


def line_weight_error(lineno, text, problem):
    """The import's message for a CSV line whose weight `weight_kg` refused."""
    if problem == "invalid":
        return _("line %(line)d: invalid weight %(value)s", line=lineno, value=repr(text))
    return _("line %(line)d: weight out of range", line=lineno)


def _looks_like_header(row):
    """True if a row is not data at all: its first cell is no date and its second no weight."""
    try:
        parse_ddmyy(row[0])
        return False
    except CsvRowError:
        pass
    return parse_decimal(row[1].strip() if len(row) > 1 else "") is None


def parse_generic_csv(text):
    """Parse the text of a `date,weight[,note]` CSV into an `ImportResult` (weight always in kg).

    If the first line is not data but looks like a header ("date,weight,note"), it
    is skipped silently. Any later line that is not data is reported.
    """
    result = ImportResult()
    first_line = True
    for lineno, row in data_rows(text):
        was_first, first_line = first_line, False
        if was_first and _looks_like_header(row):
            continue
        if len(row) < 2:
            # NOTE: date,weight[,note] names the columns of a CSV line; translate the names (note is optional).
            result.errors.append(_("line %(line)d: expected date,weight[,note]", line=lineno))
            continue

        date_str, weight_str = row[0].strip(), row[1].strip()
        note = unprotect_cell(",".join(row[2:]).strip())[:NOTE_MAX_LENGTH] or None

        try:
            entry_date = parse_ddmyy(date_str)
        except CsvRowError:
            result.errors.append(_("line %(line)d: invalid date %(value)s", line=lineno, value=repr(date_str)))
            continue

        weight, problem = weight_kg(weight_str)  # kg as typed, not rounded
        if problem:
            result.errors.append(line_weight_error(lineno, weight_str, problem))
            continue

        result.rows.append((entry_date, weight, note))

    return result


def parse_csv(file_bytes):
    """Parse CSV bytes of `date,weight,note` lines (weight always in kg).

    Returns (rows, errors) where rows is a list of
    (entry_date, weight_kg, note) tuples and errors is a list of
    one-line descriptions of skipped rows.
    """
    result = parse_generic_csv(decode_text(file_bytes))
    return result.rows, result.errors


def export_csv(entries):
    """Render entries as CSV text: `date,weight,note`, with the weight in kg to one decimal.

    Only the note is free text; it goes through `protect_cell` (the date and weight are made here
    and can never start like a formula).
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    for entry in entries:
        writer.writerow([format_ddmyy(entry.entry_date), f"{entry.weight:.1f}", protect_cell(entry.note or "")])
    return buf.getvalue()
