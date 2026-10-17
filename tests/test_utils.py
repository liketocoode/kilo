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

"""Tests for the decimal parser in app/utils.py and its use in the CSV import."""

import pytest

from app.csv_io import parse_csv
from app.utils import bmi, parse_decimal


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("72", 72.0),
        ("72.5", 72.5),
        ("72,5", 72.5),
        ("  72,5  ", 72.5),
        ("72.", 72.0),
        ("72,", 72.0),
        (".5", 0.5),
        (",5", 0.5),
        ("1,234", 1.234),  # a decimal comma, never a thousands separator
    ],
)
def test_parse_decimal_accepts_one_separator(raw, expected):
    assert parse_decimal(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", " ", ".", ",", "1,234.5", "1.234,5", "72,5,1", "72.5.1", "1e2", "1_0", "nan", "inf",
     "-72", "+72", "72 kg", "7 2", "abc", None, 72.5],
)
def test_parse_decimal_rejects_everything_else(raw):
    assert parse_decimal(raw) is None


def test_csv_import_accepts_a_quoted_decimal_comma():
    rows, errors = parse_csv('1-2-26,"72,5"\n2-2-26,72.5\n')
    assert errors == []
    assert [weight for _date, weight, _note in rows] == [72.5, 72.5]


def test_csv_import_rejects_exponent_and_nan_weights():
    rows, errors = parse_csv("1-2-26,1e2\n2-2-26,nan\n")
    assert rows == []
    assert len(errors) == 2


def test_bmi_is_the_weight_over_the_height_in_metres_squared():
    assert bmi(81, 180) == pytest.approx(25.0)
    assert bmi(72.5, 172.5) == pytest.approx(72.5 / 1.725**2)
