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

"""Dashboard and JSON endpoints.

The dashboard (`/`) shows the weight chart, the stat boxes and an
infinite-scrolling daily log. The other routes save single entries, page
through older days, store view preferences, and handle settings and CSV
import/export. `/public/<id>` shows a user's chart and stat boxes to anyone,
when the user has turned that on.
"""

import hashlib
import secrets
import time
from collections import deque
from datetime import date, timedelta

from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_babel import format_date, format_decimal, gettext as _, lazy_gettext as _l, ngettext
from sqlalchemy.exc import IntegrityError

from app import avatar, db
from app.auth import get_current_user, login_required
from app.csv_io import MAX_WEIGHT_KG, MIN_WEIGHT_KG, ImportFileError, export_csv
from app.errors import back_url
from app.forms import ImportForm, PublicChartForm, SettingsForm, SignupForm
from app.i18n import LANGUAGE_COOKIE, LANGUAGE_COOKIE_DAYS, meters
from app.importing import parse_import
from app.models import User, WeightEntry
from app.security import log_event
from app.utils import NOTE_MAX_LENGTH, bmi, height_from_cm, height_to_cm, parse_decimal

bp = Blueprint("main", __name__)

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


def _entries_sorted(user):
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


def _daily_series(entries, height_cm):
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


def _overview(user, notes=True):
    """Everything the dashboard needs, as one dict.

    Stats, the chart series (labels, weights, BMIs, moving average), the BMI, the
    available ranges and the notes by date (for the chart tooltip; empty with
    `notes=False`, as on the public chart). It is also returned as JSON after each
    save so the page can refresh in place.
    """
    entries = _entries_sorted(user)
    chart_labels, chart_values, chart_bmis, chart_real = _daily_series(
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


def _grid_page(user, end_date, size=GRID_PAGE_SIZE):
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


ROBOTS_TXT = """\
# Everything is behind a login except the front page, the About page and the
# charts users share at /public/..., which are meant for whoever gets the link,
# not for search engines; so crawlers are kept to the first two. (robots.txt is
# a request, not access control - the data itself is protected by sign-in.)
User-agent: *
Allow: /$
Allow: /about$
Disallow: /
"""


@bp.route("/robots.txt", methods=["GET"])
def robots_txt():
    """Ask search-engine crawlers to stay out of everything but the public pages."""
    return Response(ROBOTS_TXT, mimetype="text/plain")


@bp.route("/language", methods=["POST"])
def set_language():
    """Remember the language picked in the language menu, or forget it ("browser language").

    Kept in a cookie, so it works signed out too and needs nothing stored on the server; it is
    per browser. Goes back to the page the menu was on.
    """
    language = request.form.get("language", "")
    response = redirect(back_url())
    if language in current_app.config["SUPPORTED_LANGUAGES"]:
        response.set_cookie(
            LANGUAGE_COOKIE,
            language,
            max_age=int(timedelta(days=LANGUAGE_COOKIE_DAYS).total_seconds()),
            secure=current_app.config["SESSION_COOKIE_SECURE"],
            httponly=True,
            samesite="Lax",
        )
    else:
        response.delete_cookie(LANGUAGE_COOKIE)
    return response


@bp.route("/avatar", methods=["GET"])
def user_avatar():
    """The signed-in user's Gravatar, fetched and cached by the server (see app/avatar.py),
    or a local placeholder. Signed out, the placeholder too: no 404 for an image."""
    user = get_current_user()
    found = avatar.get(user.email_hash) if user else None
    if found is None:
        response = current_app.send_static_file("avatar-placeholder.svg")
        max_age = int(timedelta(hours=1).total_seconds())
    else:
        content_type, data = found
        response = Response(data, mimetype=content_type)
        max_age = avatar.CACHE_SECONDS
    # The URL carries a short piece of the hash (see base.html), so another account signing in
    # on the same browser does not get this one from the browser's cache.
    response.headers["Cache-Control"] = f"private, max-age={max_age}"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.route("/", methods=["GET"])
def index():
    """The dashboard for signed-in users, the sign-in page otherwise."""
    user = get_current_user()
    if user is None:
        return render_template("login.html", form=SignupForm())

    today = date.today()
    grid_days = _grid_page(user, today)
    return render_template(
        "index.html",
        public=False,
        show_stats=True,
        grid_days=grid_days,
        grid_page_size=GRID_PAGE_SIZE,
        today_date=today.isoformat(),
        today_entry_missing=bool(grid_days) and grid_days[0]["weight"] is None,
        **_chart_context(user, _overview(user)),
    )


def _chart_context(user, overview):
    """What index.html needs for the chart and the stat boxes, on the dashboard and the public page."""
    chart_range = user.chart_range if user.chart_range in overview["available_ranges"] else "all"
    return {
        "chart_ranges": CHART_RANGES,
        "chart_range_days": {key: days for key, _label, days in CHART_RANGES},
        "chart_range_labels": {key: str(label) for key, label, _days in CHART_RANGES},
        "chart_range": chart_range,
        "height_cm": user.height_cm,
        **overview,
    }


def _without_weights(overview):
    """The chart data of `overview` with nothing that gives the weight away, for a public chart
    whose owner turned "Show my weight" off.

    The weights and the moving average are rescaled together to 0-100 (the lowest value 0, the
    highest 100), so the line keeps its shape; the BMIs, the height and the stats are left out,
    since a BMI with a height gives the weight back. The page itself gets nothing else: this
    is not hidden by the browser, it is never sent.
    """
    values = overview["chart_values"]
    moving_average = overview["chart_moving_average"]
    everything = [v for v in values + moving_average if v is not None]
    low, high = (min(everything), max(everything)) if everything else (0, 0)
    span = high - low

    def rescale(value):
        return 50.0 if span == 0 else round((value - low) / span * 100, 2)

    return {
        **overview,
        "chart_values": [rescale(v) for v in values],
        "chart_moving_average": [rescale(v) for v in moving_average],
        "chart_bmis": None,
        "stats": None,
        "bmi": None,
    }


@bp.route("/public/<public_id>", methods=["GET"])
def public_chart(public_id):
    """A user's chart (and the stat boxes, unless turned off) for anyone, also inside another
    site's iframe.

    Only when the user turned it on in the settings. Never the notes, the address or the
    daily log. Its theme and range are the ones saved for it (public_dark_mode, not the owner's
    own theme, and public_range); `?theme=dark|light` and `?range=<key>` in the address override
    them (the settings put both in the link and the embed code).
    """
    user = User.query.filter_by(public_id=public_id.lower(), public_enabled=True).first()
    if user is None:
        # Logged like any unknown page, so guessing ids adds up to a ban (see app/errors.py),
        # but answered here: a browser is not sent to the sign-in page inside someone's iframe.
        log_event("notfound", path=request.path)
        return render_template("public_missing.html"), 404
    theme = request.args.get("theme")
    if theme not in ("light", "dark"):
        theme = "dark" if user.public_dark_mode else "light"
    overview = _overview(user, notes=False)
    context = _chart_context(user, overview)
    # A fixed range (?range=3m, else the saved one) shows the chart in that range without the
    # range buttons; any range key is allowed, also one the data does not span yet, in any case
    # (3M, All). Anything else in the address counts as not given.
    range_key = request.args.get("range", "").lower()
    if range_key not in CHART_RANGE_KEYS:
        range_key = user.public_range
    fixed_range = range_key in CHART_RANGE_KEYS
    if fixed_range:
        context["chart_range"] = range_key
    show_weight = user.public_show_weight
    if not show_weight:
        context.update(_without_weights(overview), height_cm=None)
    return render_template(
        "index.html",
        public=True,
        show_stats=user.public_stats_shown,
        hide_weights=not show_weight,
        fixed_range=fixed_range,
        theme=theme,
        **context,
    )


@bp.route("/entries/window", methods=["GET"])
@login_required
def entries_window():
    """Older days for the daily log's infinite scroll: the page of days before `before` (an ISO date)."""
    before_str = request.args.get("before")
    try:
        end_date = date.fromisoformat(before_str) - timedelta(days=1)
    except (TypeError, ValueError):
        return jsonify(error=_("Invalid date.")), 400

    user = get_current_user()
    return jsonify(days=_grid_page(user, end_date))


OUTLIER_THRESHOLD = 0.10


def _put_entry(user, existing, entry_date, weight, note):
    """Create the entry for `entry_date` or overwrite its weight and note; the caller commits.

    `existing` maps dates to the user's entries and gets the new one too, so a date that comes
    twice (an import file can have that) updates the first instead of adding a second row.
    """
    entry = existing.get(entry_date)
    if entry is None:
        entry = existing[entry_date] = WeightEntry(user_id=user.id, entry_date=entry_date)
        db.session.add(entry)
    entry.weight = weight
    entry.note = note


def _outlier_warning(user, entry_date, weight):
    """A human-readable warning if `weight` differs from the nearest real
    entry before or after entry_date by more than OUTLIER_THRESHOLD, else
    None. Compares against actual logged entries (not interpolated days),
    checking whichever date is being edited itself excluded."""
    before = (
        WeightEntry.query.filter(
            WeightEntry.user_id == user.id, WeightEntry.entry_date < entry_date
        )
        .order_by(WeightEntry.entry_date.desc())
        .first()
    )
    after = (
        WeightEntry.query.filter(
            WeightEntry.user_id == user.id, WeightEntry.entry_date > entry_date
        )
        .order_by(WeightEntry.entry_date.asc())
        .first()
    )

    for neighbor in (before, after):
        if neighbor is None or not neighbor.weight:
            continue
        diff_ratio = abs(weight - neighbor.weight) / neighbor.weight
        if diff_ratio > OUTLIER_THRESHOLD:
            return _(
                "%(weight)s is %(percent)s%% different from your entry of %(other)s on %(date)s.",
                weight=format_decimal(weight),
                percent=format_decimal(round(diff_ratio * 100)),
                other=format_decimal(neighbor.weight),
                date=format_date(neighbor.entry_date, format="medium"),
            )
    return None


def _json_object():
    """The request's JSON body if it is an object, else None: a list, a string or a number in its
    place is not a request any endpoint here understands (and has no .get)."""
    data = request.get_json(silent=True)
    if data is None:
        return {}  # no body, or not JSON: each endpoint reports the field it misses
    return data if isinstance(data, dict) else None


def _invalid_request():
    return jsonify(error=_("Invalid request.")), 400


@bp.route("/entries/field", methods=["POST"])
@login_required
def save_field():
    """Create, update or clear one day's weight and note (JSON).

    Validates the weight. It may answer with status "warning" for a large jump from
    neighbouring entries; the client overrides that by sending `confirm`. Returns
    the refreshed overview.
    """
    data = _json_object()
    if data is None:
        return _invalid_request()
    try:
        entry_date = date.fromisoformat(data.get("date", ""))
    except (TypeError, ValueError):  # TypeError: a date that is not text (5, null, a list)
        return jsonify(error=_("Invalid date.")), 400

    user = get_current_user()
    entry = WeightEntry.query.filter_by(user_id=user.id, entry_date=entry_date).first()

    weight_raw = data.get("weight")
    if weight_raw in (None, ""):
        removed = None
        if entry is not None:
            # Sent back so the page can offer "Undo": saving these again restores the entry.
            removed = {"weight": entry.weight, "note": entry.note or ""}
            db.session.delete(entry)
            db.session.commit()
        return jsonify(status="cleared", removed=removed, **_overview(user))

    # Plain decimals only, "80.5" or "80,5": float() would also take "1e2", "1_0", "nan"... A JSON
    # number goes through the same check as text (80.5 -> "80.5"), so true, NaN and the like do not.
    weight = parse_decimal(str(weight_raw))
    if weight is None:
        return jsonify(error=_("Enter a valid weight, like 72.5.")), 400
    if not (MIN_WEIGHT_KG <= weight <= MAX_WEIGHT_KG):
        message = _(
            "Weight must be between %(min)s and %(max)s kg.",
            min=format_decimal(MIN_WEIGHT_KG),
            max=format_decimal(MAX_WEIGHT_KG),
        )
        return jsonify(error=message), 400

    if not data.get("confirm"):
        warning = _outlier_warning(user, entry_date, weight)
        if warning:
            return jsonify(status="warning", message=warning)

    note = (data.get("note") or "").strip()[:NOTE_MAX_LENGTH] or None

    _put_entry(user, {entry_date: entry} if entry else {}, entry_date, weight, note)
    db.session.commit()
    return jsonify(status="saved", **_overview(user))


@bp.route("/settings/dark-mode", methods=["POST"])
@login_required
def set_dark_mode():
    """Store the user's dark-mode preference."""
    data = _json_object()
    if data is None:
        return _invalid_request()  # not silently "off": the page only ever sends an object
    user = get_current_user()
    user.dark_mode = bool(data.get("dark_mode"))
    db.session.commit()
    return jsonify(status="saved", dark_mode=user.dark_mode)


@bp.route("/chart-range", methods=["POST"])
@login_required
def set_chart_range():
    """Remember the user's selected chart range."""
    data = _json_object()
    if data is None:
        return _invalid_request()
    range_key = data.get("range")
    if not isinstance(range_key, str) or range_key not in CHART_RANGE_KEYS:  # a list is unhashable
        return jsonify(error=_("Invalid range.")), 400

    user = get_current_user()
    user.chart_range = range_key
    db.session.commit()
    return jsonify(status="saved")


def _fill_settings_form(form, user):
    form.dark_mode.data = user.dark_mode
    form.moving_avg_days.data = user.moving_avg_days
    if user.height_cm is not None:
        form.height_unit.data = user.height_unit
        form.height_value.data = height_from_cm(user.height_cm, user.height_unit)


def propose_public_id(email, now=None):
    """A new id for the public chart: SHA-256 of the address and the time in seconds.

    Not the email hash, which is also the Gravatar id: anyone who knows the address can work
    that one out, while this one also depends on when it was made. Random when the address is
    not in the session. Never one that another user already has.
    """
    while True:
        if email:
            seconds = int(time.time() if now is None else now)
            proposal = hashlib.sha256(f"{email.strip().lower()}{seconds}".encode()).hexdigest()
            now = seconds + 1  # the next try, if this one is taken
        else:
            proposal = secrets.token_hex(32)
        if User.query.filter_by(public_id=proposal).first() is None:
            return proposal


def _render_settings(user, form=None, public_form=None):
    """The settings page; a form that was just posted keeps what the user typed."""
    if form is None:
        form = SettingsForm(formdata=None)
        _fill_settings_form(form, user)
    if public_form is None:
        public_form = PublicChartForm(formdata=None)
        public_form.public_enabled.data = user.public_enabled
        public_form.public_show_weight.data = user.public_show_weight
        public_form.public_show_stats.data = user.public_stats_shown
        public_form.public_dark_mode.data = user.public_dark_mode
        public_form.public_id.data = user.public_id or propose_public_id(session.get("email"))
    if not public_form.public_show_weight.data:
        public_form.public_show_stats.data = False  # shown switched off and greyed out
    return render_template(
        "settings.html",
        form=form,
        public_form=public_form,
        import_form=ImportForm(),
        user=user,
        chart_ranges=CHART_RANGES,
    )


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    """View and update the user's settings."""
    user = get_current_user()
    form = SettingsForm()

    if request.method == "GET":
        _fill_settings_form(form, user)

    if form.validate_on_submit():
        user.height_cm = height_to_cm(form.height_value.data, form.height_unit.data)
        user.height_unit = form.height_unit.data
        user.dark_mode = form.dark_mode.data
        user.moving_avg_days = form.moving_avg_days.data
        db.session.commit()
        flash(_("Settings updated."), "success")
        return redirect(url_for("main.settings"))

    return _render_settings(user, form=form)


@bp.route("/settings/public", methods=["POST"])
@login_required
def set_public_chart():
    """Turn the public chart on or off and set its id (empty: a newly proposed one)."""
    user = get_current_user()
    form = PublicChartForm()
    form.user_id = user.id
    if not form.validate_on_submit():
        return _render_settings(user, public_form=form)

    user.public_enabled = form.public_enabled.data
    user.public_show_weight = form.public_show_weight.data
    # The stat boxes show kilograms, so they go when the weight does.
    user.public_show_stats = form.public_show_weight.data and form.public_show_stats.data
    user.public_dark_mode = form.public_dark_mode.data
    # The range menu is not a form field (its choices are CHART_RANGES, defined here); "" or
    # anything unknown: the visitor chooses.
    range_key = request.form.get("public_range", "")
    user.public_range = range_key if range_key in CHART_RANGE_KEYS else None
    user.public_id = form.public_id.data or propose_public_id(session.get("email"))
    try:
        db.session.commit()
    except IntegrityError:
        # Someone else took the same id between the check and the save.
        db.session.rollback()
        form.public_id.errors = [_("That ID is already taken. Choose another one.")]
        return _render_settings(user, public_form=form)
    if user.public_enabled:
        flash(_("Your chart is now public."), "success")
    else:
        flash(_("Your chart is no longer public."), "success")
    return redirect(url_for("main.settings", _anchor="public-chart"))


@bp.route("/settings/export", methods=["GET"])
@login_required
def export_entries():
    """Download all entries as CSV, and mark the data as downloaded for the removal flow."""
    user = get_current_user()
    csv_text = export_csv(_entries_sorted(user))
    # Unlocks the "remove my data" flow (see app/account.py), which insists
    # on a download first.
    session["data_exported"] = True
    return Response(
        csv_text,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=kilo-tracker-export-{date.today().isoformat()}.csv"
        },
    )


def _flash_import_result(result, height_set):
    """What the import did, as flash messages: entries, a height, skipped notes and lines."""
    imported, errors = len(result.rows), result.errors
    if imported:
        flash(
            ngettext("Imported %(num)d entry.", "Imported %(num)d entries.", imported),
            "success",
        )
    if height_set:
        flash(_("Your height was set to %(height)s.", height=meters(result.height_cm / 100)), "success")
    if result.notes_skipped:
        flash(
            ngettext(
                "%(num)d note without a weight was skipped.",
                "%(num)d notes without a weight were skipped.",
                result.notes_skipped,
            ),
            "error",
        )
    if errors:
        details = "; ".join(errors[:5])
        if len(errors) > 5:
            details = _("%(details)s (+%(count)d more)", details=details, count=len(errors) - 5)
        flash(
            ngettext(
                "Skipped %(num)d invalid line: %(details)s",
                "Skipped %(num)d invalid lines: %(details)s",
                len(errors),
                details=details,
            ),
            "error",
        )
    if not imported and not errors and not result.notes_skipped:
        flash(_("No rows found in the file."), "error")


@bp.route("/settings/import", methods=["POST"])
@login_required
def import_entries():
    """Import entries from an uploaded file (CSV, or a Hacker's Diet CSV/XML export), overwriting
    entries on the same dates.

    A height in the file is used only if the user has not set one yet.
    """
    user = get_current_user()
    form = ImportForm()

    if not form.validate_on_submit():
        for field_errors in form.errors.values():
            for error in field_errors:
                flash(str(error), "error")
        return redirect(url_for("main.settings"))

    try:
        result = parse_import(form.csv_file.data.read())
    except ImportFileError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.settings"))

    # All the user's entries in one query, not one per imported line.
    existing = {entry.entry_date: entry for entry in user.entries}
    for entry_date, weight, note in result.rows:
        _put_entry(user, existing, entry_date, weight, note)
    height_set = result.height_cm is not None and user.height_cm is None
    if height_set:
        user.height_cm = result.height_cm
    db.session.commit()

    _flash_import_result(result, height_set)
    return redirect(url_for("main.settings"))
