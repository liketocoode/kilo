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

"""The numbers behind the chart and the stat boxes, and the daily log's pages.

Pure computation on a user's entries: the daily series (every calendar day, gaps
interpolated), its moving average, the stat boxes (all changes are changes of the
moving average), the BMI and which range buttons the data spans. `overview()` puts it
all in the one dict the dashboard, the public chart and every save's JSON answer use.
The only Flask in here is Babel, for the range labels and the total box's plural label.
"""

from collections import deque
from datetime import date, timedelta

from flask_babel import lazy_gettext as _l, ngettext

from app.models import WeightEntry
from app.utils import bmi

GRID_PAGE_SIZE = 30

# (key, label, days spanned — None means "all data"), in display order.
# 1w and all are always shown; the rest only appear once the user's data
# actually spans that long. The labels are lazy: they are translated when a
# page renders. Use the unit letters people use in each language (nl: 1W 1M 1J).
CHART_RANGES = [
    # NOTE: Chart range button: the whole recorded history.
    ("all", _l("All"), None),
    # NOTE: Chart range button: 20 years. The letter is the unit, Y for year.
    ("20y", _l("20Y"), 20 * 365),
    # NOTE: Chart range button: 15 years. The letter is the unit, Y for year.
    ("15y", _l("15Y"), 15 * 365),
    # NOTE: Chart range button: 10 years. The letter is the unit, Y for year.
    ("10y", _l("10Y"), 10 * 365),
    # NOTE: Chart range button: 5 years. The letter is the unit, Y for year.
    ("5y", _l("5Y"), 5 * 365),
    # NOTE: Chart range button: 1 year. The letter is the unit, Y for year.
    ("1y", _l("1Y"), 365),
    # NOTE: Chart range button: 6 months. The letter is the unit, M for month.
    ("6m", _l("6M"), 180),
    # NOTE: Chart range button: 3 months. The letter is the unit, M for month.
    ("3m", _l("3M"), 90),
    # NOTE: Chart range button: 1 month. The letter is the unit, M for month.
    ("1m", _l("1M"), 30),
    # NOTE: Chart range button: 2 weeks. The letter is the unit, W for week.
    ("2w", _l("2W"), 14),
    # NOTE: Chart range button: 1 week. The letter is the unit, W for week.
    ("1w", _l("1W"), 7),
]
CHART_RANGE_KEYS = {key for key, _label, _days in CHART_RANGES}
ALWAYS_SHOWN_RANGES = {"1w", "all"}


CHANGE_PERIODS = (7, 14, 30, 90, 180, 365)

# The chart range each x-day change box jumps to when clicked. The total
# change box (whole recorded span) jumps to "all".
CHANGE_PERIOD_RANGES = {7: "1w", 14: "2w", 30: "1m", 90: "3m", 180: "6m", 365: "1y"}


def _stats(entries, moving_average):
    """Headline numbers for the stat boxes, or None without entries.

    All changes are based on the moving average (see `_period_change`).
    """
    if not entries:
        return None

    latest = entries[-1]
    first = entries[0]
    # The series has one value per calendar day from the first entry to the
    # last, so its length - 1 is the number of days the data spans.
    total_days = len(moving_average) - 1
    return {
        "current": moving_average[-1],
        "start": first.weight,
        "total_change": latest.weight - first.weight,
        "changes": _moving_average_changes(moving_average),
        "total": _period_change(moving_average, total_days),
        "first_date": first.entry_date.isoformat(),
        "last_date": latest.entry_date.isoformat(),
        "entry_count": len(entries),
    }


def _period_change(moving_average, days):
    """Change in the moving average between the latest day and `days` days
    earlier. `moving_average` is the daily series (one value per calendar
    day, ending at the latest entry), so N days back is simply N indices
    back. None when there's less history than that (or days is 0).
    `change` is in kg; `g_per_day` is grams/day."""
    change = g_per_day = None
    if days > 0 and len(moving_average) > days:
        change = moving_average[-1] - moving_average[-1 - days]
        g_per_day = change * 1000 / days
    return {"days": days, "change": change, "g_per_day": g_per_day}


def _moving_average_changes(moving_average):
    """`_period_change` for each period in CHANGE_PERIODS, plus the chart
    range its box links to."""
    return [
        {**_period_change(moving_average, days), "range": CHANGE_PERIOD_RANGES[days]}
        for days in CHANGE_PERIODS
    ]


def _bmi(moving_average, height_cm):
    """BMI from the current (latest) moving-average weight; None without a
    height or any entries."""
    if not height_cm or not moving_average:
        return None
    return bmi(moving_average[-1], height_cm)


def entries_sorted(user):
    """The user's entries, oldest first."""
    return (
        WeightEntry.query.filter_by(user_id=user.id).order_by(WeightEntry.entry_date.asc()).all()
    )


def _available_range_keys(entries):
    """Chart range keys to offer: 1W and All always, the others once the data spans at least that long."""
    span_days = (date.today() - entries[0].entry_date).days if entries else 0
    return [
        key
        for key, _label, days in CHART_RANGES
        if key in ALWAYS_SHOWN_RANGES or (days is not None and span_days >= days)
    ]


def daily_series(entries, height_cm):
    """Every calendar day from the first entry to the last, inclusive.

    A category-axis chart spaces its labels evenly regardless of the actual
    date gap between them, so if the chart only plotted real entries, a
    stretch with no logged weigh-ins would look visually "compressed" next
    to daily-logged stretches — same on-screen width for a 1-day gap as for
    a 3-week one. Filling every day in between and linearly interpolating
    the weight for the ones with no real entry keeps the spacing uniform
    and the line continuous.

    Returns (labels, values, bmis, is_real) — bmis is None when height isn't
    set; is_real flags which days are actual measurements vs. interpolated
    fill, so the chart can hide dots/tooltip values for the latter.
    Stats/BMI cards use the real entries directly and are unaffected by
    this interpolation.
    """
    if not entries:
        return [], [], None, []

    start = entries[0].entry_date
    total_days = (entries[-1].entry_date - start).days

    values = []
    is_real = []
    prev_offset = 0
    prev_weight = entries[0].weight
    for entry in entries[1:]:
        offset = (entry.entry_date - start).days
        gap = offset - prev_offset
        for step in range(gap):
            values.append(prev_weight + (entry.weight - prev_weight) * (step / gap))
            is_real.append(step == 0)
        prev_offset = offset
        prev_weight = entry.weight
    values.append(prev_weight)
    is_real.append(True)

    labels = [(start + timedelta(days=i)).isoformat() for i in range(total_days + 1)]

    bmis = [bmi(v, height_cm) for v in values] if height_cm else None

    return labels, values, bmis, is_real


def _moving_average(values, window):
    """Trailing average at each index, over up to `window` preceding values
    (fewer at the start of the series, so it needs no burn-in period)."""
    if not values:
        return []
    result = []
    running_sum = 0.0
    buf = deque()
    for v in values:
        buf.append(v)
        running_sum += v
        if len(buf) > window:
            running_sum -= buf.popleft()
        result.append(running_sum / len(buf))
    return result


def overview(user, notes=True):
    """Everything the dashboard needs, as one dict.

    Stats, the chart series (labels, weights, BMIs, moving average), the BMI, the
    available ranges and the notes by date (for the chart tooltip; empty with
    `notes=False`, as on the public chart). It is also returned as JSON after each
    save so the page can refresh in place.
    """
    entries = entries_sorted(user)
    chart_labels, chart_values, chart_bmis, chart_real = daily_series(
        entries, user.height_cm
    )
    chart_moving_average = _moving_average(chart_values, user.moving_avg_days)
    stats = _stats(entries, chart_moving_average)
    if stats:
        # The page updates this label in place after a save; only the server
        # can pick the right plural form for the language.
        total_days = stats["total"]["days"]
        stats["total_label"] = ngettext("%(num)d-day change", "%(num)d-day change", total_days)
    return {
        "stats": stats,
        "chart_labels": chart_labels,
        "chart_values": chart_values,
        "chart_bmis": chart_bmis,
        "chart_real": chart_real,
        "chart_moving_average": chart_moving_average,
        # Only the days that have a note, {"2026-10-05": "text"}: most entries have none.
        "chart_notes": {e.entry_date.isoformat(): e.note for e in entries if e.note} if notes else {},
        "moving_avg_days": user.moving_avg_days,
        "bmi": _bmi(chart_moving_average, user.height_cm),
        "available_ranges": _available_range_keys(entries),
    }


def grid_page(user, end_date, size=GRID_PAGE_SIZE):
    """The `size` days ending on (and including) end_date, newest first."""
    days = [end_date - timedelta(days=i) for i in range(size)]
    entries = {
        e.entry_date: e
        for e in WeightEntry.query.filter(
            WeightEntry.user_id == user.id, WeightEntry.entry_date.in_(days)
        ).all()
    }
    return [
        {
            "date": d.isoformat(),
            "weight": entries[d].weight if d in entries else None,
            "note": (entries[d].note or "") if d in entries else "",
        }
        for d in days
    ]
