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

"""Direct tests of the numbers behind the chart and the stat boxes (app/overview.py).

The pages are tested through HTTP elsewhere; here the calculations get their edge cases one by
one (a single entry, gaps, a window longer than the data, the boundaries of a period), where a
wrong number would otherwise only show deep inside a page.
"""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app import db, overview
from app.models import User, WeightEntry
from app.utils import bmi

DAY = timedelta(days=1)


def entries(*pairs, start=date(2026, 1, 1)):
    """Entries as the views get them, oldest first: (days after `start`, weight) pairs."""
    return [SimpleNamespace(entry_date=start + offset * DAY, weight=weight) for offset, weight in pairs]


@pytest.fixture
def today(monkeypatch):
    """A fixed `date.today()` for app/overview.py, so a run across midnight cannot shift a span."""

    class FrozenDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 7, 1)

    monkeypatch.setattr(overview, "date", FrozenDate)
    return date(2026, 7, 1)


# --- the moving average ---------------------------------------------------------------------


def test_the_moving_average_uses_fewer_values_at_the_start():
    assert overview._moving_average([1.0, 2.0, 3.0], 2) == [1.0, 1.5, 2.5]


def test_a_window_longer_than_the_data_averages_everything_so_far():
    assert overview._moving_average([1.0, 2.0, 3.0], 10) == [1.0, 1.5, 2.0]


def test_a_window_of_one_is_the_values_themselves():
    assert overview._moving_average([80.0, 81.5, 79.0], 1) == [80.0, 81.5, 79.0]


def test_no_values_have_no_moving_average():
    assert overview._moving_average([], 10) == []


def test_the_running_sum_drops_the_oldest_value_exactly():
    # Many steps: a running sum that drifted would show here.
    values = [80.0 + (i % 7) / 10 for i in range(1000)]
    result = overview._moving_average(values, 10)
    assert result[-1] == pytest.approx(sum(values[-10:]) / 10)


# --- changes over a period --------------------------------------------------------------------


def test_a_period_change_is_the_moving_average_now_minus_then():
    assert overview._period_change([81.0] + [80.0] * 7, 7) == {"days": 7, "change": -1.0, "g_per_day": -1000 / 7}


def test_a_period_needs_one_more_day_than_its_length():
    # 7 days back from the last of 8 values is the first one; with 7 values there is none.
    assert overview._period_change([80.0] * 8, 7)["change"] == 0.0
    assert overview._period_change([80.0] * 7, 7) == {"days": 7, "change": None, "g_per_day": None}


def test_a_period_of_zero_days_has_no_change():
    assert overview._period_change([80.0] * 3, 0) == {"days": 0, "change": None, "g_per_day": None}


def test_every_change_box_links_to_its_chart_range():
    changes = overview._moving_average_changes([80.0] * 400)
    assert [(c["days"], c["range"]) for c in changes] == [
        (7, "1w"), (14, "2w"), (30, "1m"), (90, "3m"), (180, "6m"), (365, "1y"),
    ]
    assert all(c["change"] == 0.0 for c in changes)


# --- the daily series ------------------------------------------------------------------------


def test_the_days_between_two_entries_are_filled_in_a_straight_line():
    labels, values, bmis, is_real = overview.daily_series(entries((0, 80.0), (3, 83.0)), None)
    assert labels == ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]
    assert values == [80.0, 81.0, 82.0, 83.0]
    assert is_real == [True, False, False, True]
    assert bmis is None


def test_entries_on_consecutive_days_are_all_real():
    _labels, values, _bmis, is_real = overview.daily_series(entries((0, 80.0), (1, 79.0), (2, 81.0)), None)
    assert values == [80.0, 79.0, 81.0] and is_real == [True, True, True]


def test_a_single_entry_is_a_single_day():
    assert overview.daily_series(entries((0, 80.0)), None) == (["2026-01-01"], [80.0], None, [True])


def test_no_entries_give_an_empty_series():
    assert overview.daily_series([], 180) == ([], [], None, [])


def test_with_a_height_every_day_has_a_bmi():
    _labels, values, bmis, _is_real = overview.daily_series(entries((0, 81.0), (2, 83.0)), 180)
    assert bmis == [bmi(v, 180) for v in values]
    assert bmis[0] == pytest.approx(25.0)


# --- the stat boxes --------------------------------------------------------------------------


def test_the_stats_are_based_on_the_moving_average():
    rows = entries((0, 82.0), (10, 80.0))
    _labels, values, _bmis, _real = overview.daily_series(rows, None)
    moving = overview._moving_average(values, 3)
    stats = overview._stats(rows, moving)
    assert stats["current"] == moving[-1] != rows[-1].weight
    assert stats["total"] == overview._period_change(moving, 10)
    assert (stats["start"], stats["total_change"]) == (82.0, -2.0)
    assert (stats["first_date"], stats["last_date"], stats["entry_count"]) == ("2026-01-01", "2026-01-11", 2)


def test_one_entry_has_stats_but_no_changes_yet():
    stats = overview._stats(entries((0, 80.0)), [80.0])
    assert stats["current"] == 80.0 and stats["entry_count"] == 1
    assert stats["total"]["change"] is None
    assert all(c["change"] is None for c in stats["changes"])


def test_no_entries_have_no_stats():
    assert overview._stats([], []) is None


@pytest.mark.parametrize(
    "height, moving, expected", [(180, [81.0], pytest.approx(25.0)), (None, [81.0], None), (180, [], None)]
)
def test_the_bmi_uses_the_latest_moving_average(height, moving, expected):
    assert overview._bmi(moving, height) == expected


# --- which chart ranges are offered ----------------------------------------------------------


@pytest.mark.parametrize(
    "span_days, offered",
    [
        (0, ["all", "1w"]),
        (13, ["all", "1w"]),
        (14, ["all", "2w", "1w"]),  # a range appears on the day the data spans it
        (90, ["all", "3m", "1m", "2w", "1w"]),
        (365, ["all", "1y", "6m", "3m", "1m", "2w", "1w"]),
        (20 * 365, ["all", "20y", "15y", "10y", "5y", "1y", "6m", "3m", "1m", "2w", "1w"]),
    ],
)
def test_a_range_is_offered_once_the_data_spans_it(today, span_days, offered):
    first = SimpleNamespace(entry_date=today - span_days * DAY, weight=80.0)
    assert overview._available_range_keys([first]) == offered


def test_without_entries_only_the_two_fixed_ranges_are_offered(today):
    assert overview._available_range_keys([]) == ["all", "1w"]


# --- a page of the daily log ------------------------------------------------------------------


def test_a_log_page_is_newest_first_with_the_empty_days_too(app):
    user = User(email_hash="a" * 64)
    db.session.add(user)
    db.session.commit()
    db.session.add_all([
        WeightEntry(user_id=user.id, entry_date=date(2026, 3, 10), weight=80.0, note="hi"),
        WeightEntry(user_id=user.id, entry_date=date(2026, 3, 8), weight=81.0),
        WeightEntry(user_id=user.id, entry_date=date(2026, 3, 1), weight=90.0),  # outside the page
    ])
    db.session.commit()
    page = overview.grid_page(user, date(2026, 3, 10), size=3)
    assert page == [
        {"date": "2026-03-10", "weight": 80.0, "note": "hi"},
        {"date": "2026-03-09", "weight": None, "note": ""},
        {"date": "2026-03-08", "weight": 81.0, "note": ""},
    ]
