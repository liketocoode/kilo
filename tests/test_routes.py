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

"""The dashboard, the chart and its ranges, the stat boxes and the other pages.

Saving entries is in test_entries.py, the settings in test_settings.py, import and export in
test_import_export.py. This file also guards every route: see PUBLIC_ENDPOINTS.
"""

import re
from datetime import date, timedelta

import pytest
from sqlalchemy import event

from app import db
from app.models import User, WeightEntry
from tests.conftest import script_value


def test_index_shows_login_prompt_when_logged_out(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Sign in" in resp.data


def test_index_shows_grid_when_logged_in(logged_in_client):
    client, _ = logged_in_client
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"day-row" in resp.data
    assert b"No entries yet" in resp.data


# Endpoints anyone may reach. Every other endpoint must send a signed-out visitor away; adding one
# here is a decision about what strangers may do, not a way to make a failing test pass.
PUBLIC_ENDPOINTS = {
    "static", "main.index", "main.robots_txt", "main.set_language", "main.user_avatar",
    "main.public_chart", "auth.about", "auth.signup", "auth.verify", "auth.resend_code", "auth.logout",
}


def test_every_other_endpoint_needs_a_sign_in(app, client):
    """So a new route that forgets @login_required fails here, not in production."""
    guarded = sorted(
        (rule.endpoint, "POST" if "POST" in rule.methods else "GET", rule.rule)
        for rule in app.url_map.iter_rules()
        if rule.endpoint not in PUBLIC_ENDPOINTS
    )
    assert len(guarded) == 12  # a new route shows up here: decide whether it is public
    for endpoint, method, path in guarded:
        resp = client.open(path, method=method, json={} if method == "POST" else None)
        assert resp.status_code == 302, endpoint
        assert resp.headers["Location"].endswith("/"), endpoint
    with client.session_transaction() as sess:
        assert "user_id" not in sess


def test_bmi_shown_once_height_and_weight_are_set(logged_in_client, app):
    client, _ = logged_in_client
    client.post("/settings", data={"height_value": "180", "height_unit": "cm"})
    client.post("/entries/field", json={"date": date.today().isoformat(), "weight": 81.0})

    resp = client.get("/")
    assert resp.status_code == 200
    # 81 / 1.8^2 = 25.0
    assert b"25.0" in resp.data


def test_the_avatar_comes_from_kilo_not_gravatar(logged_in_client, app):
    client, user_id = logged_in_client
    with app.app_context():
        email_hash = db.session.get(User, user_id).email_hash
    html = client.get("/").data.decode()
    assert "gravatar.com/avatar" not in html
    assert f'src="/avatar?v={email_hash[:8]}"' in html


def test_about_page_renders(client):
    resp = client.get("/about")
    assert resp.status_code == 200
    assert b"SHA-256" in resp.data
    assert b"Hacker's Diet Online" in resp.data
    assert b"mailto:info@11tools.com" in resp.data
    assert b'href="https://github.com/ramondevries/kilo"' in resp.data


def _queries(app, request):
    """The SQL statements one request runs."""
    statements = []

    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    engine = db.engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        response = request()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert response.status_code == 200
    return statements


def test_the_number_of_queries_does_not_grow_with_the_data(logged_in_client, app):
    """No query per entry or per day: 10 entries and 2,000 (over five years) take the same
    number of queries for the dashboard, a page of the daily log and a save. Counted rather
    than timed, so a slow machine cannot make it fail."""
    client, user_id = logged_in_client
    today = date.today()
    counts = {}
    added = 0
    for size, weight in ((10, "80.5"), (2000, "81.5")):
        db.session.add_all(
            WeightEntry(user_id=user_id, entry_date=today - timedelta(days=d), weight=80 + (d % 7) / 10,
                        note="a note" if d % 3 == 0 else None)  # notes go into the chart tooltip
            for d in range(added, size)  # today included, with a note: each save below updates it
        )
        db.session.commit()
        added = size
        # Each round's save changes today's weight (an UPDATE both times) and sends its note back.
        save = {"date": str(today), "weight": weight, "note": "a note", "confirm": True}
        counts[size] = {
            "dashboard": len(_queries(app, lambda: client.get("/"))),
            "log page": len(_queries(app, lambda: client.get(f"/entries/window?before={today}"))),
            "save": len(_queries(app, lambda save=save: client.post("/entries/field", json=save))),
        }
    assert counts[10] == counts[2000]
    assert counts[2000]["dashboard"] <= 4 and counts[2000]["log page"] <= 2 and counts[2000]["save"] <= 5


def _add_entry(app, user_id, days_ago, weight=70.0):
    with app.app_context():
        db.session.add(
            WeightEntry(
                user_id=user_id, entry_date=date.today() - timedelta(days=days_ago), weight=weight
            )
        )
        db.session.commit()


def _range_buttons(html):
    """Map range key -> {'hidden': bool, 'active': bool} parsed from the rendered buttons."""
    result = {}
    for tag in re.findall(r"<button\b[^>]*data-range=[^>]*>", html):
        key = re.search(r'data-range="(\w+)"', tag).group(1)
        result[key] = {"hidden": " hidden" in tag, "active": "active" in tag}
    return result


def test_chart_range_default_is_all_with_only_always_shown_buttons(logged_in_client):
    client, _ = logged_in_client
    buttons = _range_buttons(client.get("/").data.decode())

    assert buttons["all"]["active"] is True
    assert buttons["1w"]["hidden"] is False
    assert buttons["all"]["hidden"] is False
    for key in ("1m", "2w", "3m", "6m", "1y", "5y", "10y", "15y", "20y"):
        assert buttons[key]["hidden"] is True, f"{key} should be hidden with no data"


def test_chart_range_buttons_unlock_as_data_spans_grow(logged_in_client, app):
    client, user_id = logged_in_client
    _add_entry(app, user_id, days_ago=0)
    _add_entry(app, user_id, days_ago=400)

    buttons = _range_buttons(client.get("/").data.decode())
    for key in ("1w", "2w", "1m", "3m", "6m", "1y", "all"):
        assert buttons[key]["hidden"] is False, f"{key} should be visible (400-day span)"
    for key in ("5y", "10y", "15y", "20y"):
        assert buttons[key]["hidden"] is True, f"{key} should still be hidden"


def test_set_chart_range_persists_and_reflects_on_reload(logged_in_client, app):
    client, user_id = logged_in_client
    _add_entry(app, user_id, days_ago=0)
    _add_entry(app, user_id, days_ago=40)

    resp = client.post("/chart-range", json={"range": "1m"})
    assert resp.status_code == 200
    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.chart_range == "1m"

    buttons = _range_buttons(client.get("/").data.decode())
    assert buttons["1m"]["active"] is True
    assert buttons["all"]["active"] is False


def test_set_chart_range_rejects_invalid_value(logged_in_client, app):
    client, user_id = logged_in_client
    resp = client.post("/chart-range", json={"range": "bogus"})
    assert resp.status_code == 400
    with app.app_context():
        assert db.session.get(User, user_id).chart_range == "all"


@pytest.mark.parametrize("value", [["1w"], {"key": "1w"}, 7, None])
def test_set_chart_range_refuses_a_range_that_is_not_text(logged_in_client, app, value):
    client, user_id = logged_in_client
    resp = client.post("/chart-range", json={"range": value})
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Invalid range."}
    with app.app_context():
        assert db.session.get(User, user_id).chart_range == "all"


def test_set_chart_range_requires_login(client):
    resp = client.post("/chart-range", json={"range": "1m"})
    assert resp.status_code == 302


def test_chart_range_falls_back_to_all_when_stored_range_unavailable(logged_in_client, app):
    client, user_id = logged_in_client
    _add_entry(app, user_id, days_ago=0)
    with app.app_context():
        user = db.session.get(User, user_id)
        user.chart_range = "5y"  # stale preference; data doesn't span 5 years
        db.session.commit()

    buttons = _range_buttons(client.get("/").data.decode())
    assert buttons["5y"]["hidden"] is True
    assert buttons["all"]["active"] is True
    assert buttons["5y"]["active"] is False


def test_chart_bmis_null_without_height(logged_in_client):
    client, _ = logged_in_client
    resp = client.post("/entries/field", json={"date": "2026-09-23", "weight": 80.0})
    assert resp.get_json()["chart_bmis"] is None


def test_chart_bmis_computed_with_height(logged_in_client):
    client, _ = logged_in_client
    client.post("/settings", data={"height_value": "180", "height_unit": "cm"})

    client.post("/entries/field", json={"date": "2026-09-23", "weight": 81.0})
    resp = client.post(
        "/entries/field", json={"date": "2026-09-24", "weight": 90.0, "confirm": True}
    )
    data = resp.get_json()

    assert data["chart_labels"] == ["2026-09-23", "2026-09-24"]
    assert data["chart_bmis"] == pytest.approx([25.0, 27.777777777777775])


def test_chart_bmis_cleared_alongside_entry(logged_in_client):
    client, _ = logged_in_client
    client.post("/settings", data={"height_value": "180", "height_unit": "cm"})
    client.post("/entries/field", json={"date": "2026-09-23", "weight": 81.0})

    resp = client.post("/entries/field", json={"date": "2026-09-23", "weight": ""})
    data = resp.get_json()
    assert data["chart_bmis"] is None
    assert data["chart_labels"] == []


def test_chart_fills_gaps_with_linear_interpolation(logged_in_client):
    client, _ = logged_in_client
    client.post("/entries/field", json={"date": "2026-09-01", "weight": 70.0})
    resp = client.post(
        "/entries/field", json={"date": "2026-09-05", "weight": 90.0, "confirm": True}
    )
    data = resp.get_json()

    assert data["chart_labels"] == [
        "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05",
    ]
    assert data["chart_values"] == pytest.approx([70.0, 75.0, 80.0, 85.0, 90.0])
    assert data["chart_real"] == [True, False, False, False, True]


def test_chart_real_flags_every_point_true_when_no_gaps(logged_in_client):
    client, _ = logged_in_client
    client.post("/entries/field", json={"date": "2026-09-01", "weight": 70.0})
    resp = client.post("/entries/field", json={"date": "2026-09-02", "weight": 71.0})
    assert resp.get_json()["chart_real"] == [True, True]


def test_chart_series_single_entry_has_no_gaps_to_fill(logged_in_client):
    client, _ = logged_in_client
    resp = client.post("/entries/field", json={"date": "2026-09-23", "weight": 79.0})
    data = resp.get_json()
    assert data["chart_labels"] == ["2026-09-23"]
    assert data["chart_values"] == [79.0]
    assert data["chart_real"] == [True]


def test_chart_bmis_use_interpolated_weight(logged_in_client):
    client, _ = logged_in_client
    client.post("/settings", data={"height_value": "180", "height_unit": "cm"})
    client.post("/entries/field", json={"date": "2026-09-01", "weight": 70.0})
    resp = client.post(
        "/entries/field", json={"date": "2026-09-05", "weight": 90.0, "confirm": True}
    )
    data = resp.get_json()

    # 80.0 kg / 1.8^2 = 24.691... at the midpoint (2026-09-03)
    assert data["chart_bmis"][2] == pytest.approx(80.0 / 1.8 ** 2)


def test_stats_unaffected_by_interpolation(logged_in_client):
    client, _ = logged_in_client
    client.post("/entries/field", json={"date": "2026-09-01", "weight": 70.0})
    resp = client.post(
        "/entries/field", json={"date": "2026-09-05", "weight": 90.0, "confirm": True}
    )
    stats = resp.get_json()["stats"]
    # start/total change come from real entries only, not the interpolated points
    assert stats["start"] == 70.0
    assert stats["total_change"] == 20.0


def test_today_prompt_shows_when_todays_entry_missing(logged_in_client):
    client, _ = logged_in_client
    html = client.get("/").data.decode()
    assert script_value(html, "todayEntryMissing") is True
    assert script_value(html, "todayDate") == date.today().isoformat()
    assert 'id="today-prompt"' in html


def test_today_prompt_hidden_flag_when_todays_entry_exists(logged_in_client):
    client, _ = logged_in_client
    client.post("/entries/field", json={"date": date.today().isoformat(), "weight": 80.0})
    html = client.get("/").data.decode()
    assert script_value(html, "todayEntryMissing") is False


def test_today_prompt_missing_when_only_older_entries_exist(logged_in_client):
    client, _ = logged_in_client
    client.post(
        "/entries/field", json={"date": (date.today() - timedelta(days=1)).isoformat(), "weight": 80.0}
    )
    html = client.get("/").data.decode()
    assert script_value(html, "todayEntryMissing") is True


def test_chart_moving_average_default_window(logged_in_client):
    client, _ = logged_in_client
    # Weights stay within the 10% outlier threshold of each other so every
    # post actually saves.
    client.post("/entries/field", json={"date": "2026-09-01", "weight": 70.0})
    client.post("/entries/field", json={"date": "2026-09-02", "weight": 75.0})
    resp = client.post("/entries/field", json={"date": "2026-09-03", "weight": 80.0})
    data = resp.get_json()
    # window=30 covers the whole (short) series, so this is a running mean.
    assert data["chart_moving_average"] == pytest.approx([70.0, 72.5, 75.0])


def test_chart_moving_average_uses_custom_window(logged_in_client):
    client, _ = logged_in_client
    client.post(
        "/settings",
        data={"height_value": "180", "height_unit": "cm", "moving_avg_days": "2"},
    )
    client.post("/entries/field", json={"date": "2026-09-01", "weight": 70.0})
    client.post("/entries/field", json={"date": "2026-09-02", "weight": 75.0})
    resp = client.post("/entries/field", json={"date": "2026-09-03", "weight": 80.0})
    data = resp.get_json()
    # window=2: [70, (70+75)/2, (75+80)/2]
    assert data["chart_moving_average"] == pytest.approx([70.0, 72.5, 77.5])


def test_chart_moving_average_length_matches_interpolated_series(logged_in_client):
    client, _ = logged_in_client
    client.post("/entries/field", json={"date": "2026-09-01", "weight": 70.0})
    resp = client.post("/entries/field", json={"date": "2026-09-05", "weight": 74.0})
    data = resp.get_json()
    assert len(data["chart_moving_average"]) == len(data["chart_values"])


def _post_weights(client, weights, start=date(2026, 1, 1)):
    for i, w in enumerate(weights):
        d = (start + timedelta(days=i)).isoformat()
        resp = client.post("/entries/field", json={"date": d, "weight": w, "confirm": True})
    return resp.get_json()


def test_changes_use_moving_average_and_need_enough_history(logged_in_client):
    client, _ = logged_in_client
    client.post("/settings", data={"height_value": "180", "height_unit": "cm", "moving_avg_days": "1"})
    # 15 days of history: 0..14, weight rising 0.1 kg/day (window 1 = raw values)
    data = _post_weights(client, [80.0 + 0.1 * i for i in range(15)])
    by_days = {c["days"]: c for c in data["stats"]["changes"]}
    assert by_days[7]["change"] == pytest.approx(0.7)
    assert by_days[7]["g_per_day"] == pytest.approx(100.0)
    assert by_days[14]["change"] == pytest.approx(1.4)
    assert by_days[30]["change"] is None
    assert by_days[365]["g_per_day"] is None


def test_today_prompt_skip_key_is_per_user(logged_in_client):
    client, user_id = logged_in_client
    html = client.get("/").data.decode()
    assert f"'weightTracker.todayPromptSkippedDate.' + {user_id}" in html


def test_current_weight_and_bmi_use_the_moving_average(logged_in_client):
    client, _ = logged_in_client
    client.post("/settings", data={"height_value": "200", "height_unit": "cm", "moving_avg_days": "2"})
    data = _post_weights(client, [80.0, 90.0])
    # 2-day window: (80 + 90) / 2, not the latest 90
    assert data["stats"]["current"] == pytest.approx(85.0)
    assert data["bmi"] == pytest.approx(85.0 / 2.0 ** 2)


def test_total_change_and_entry_summary(logged_in_client):
    client, _ = logged_in_client
    client.post("/settings", data={"height_value": "180", "height_unit": "cm", "moving_avg_days": "1"})
    data = _post_weights(client, [80.0, 79.0, 78.0, 77.0], start=date(2026, 1, 1))
    stats = data["stats"]
    assert stats["total"]["days"] == 3
    assert stats["total"]["change"] == pytest.approx(-3.0)
    assert stats["total"]["g_per_day"] == pytest.approx(-1000.0)
    assert stats["first_date"] == "2026-01-01"
    assert stats["last_date"] == "2026-01-04"
    assert stats["entry_count"] == 4


def test_total_change_is_empty_with_a_single_entry(logged_in_client):
    client, _ = logged_in_client
    data = _post_weights(client, [80.0])
    total = data["stats"]["total"]
    assert total["days"] == 0 and total["change"] is None and total["g_per_day"] is None
    assert data["stats"]["entry_count"] == 1


def test_print_stylesheet_is_linked_for_print_only(logged_in_client):
    client, _ = logged_in_client
    html = client.get("/").data.decode()
    assert re.search(r'<link[^>]+print\.css[^>]+media="print"', html)
    assert client.get("/static/print.css").status_code == 200


def test_every_about_section_has_a_back_link_to_the_dashboard(client):
    html = client.get("/about").data.decode()
    assert len(re.findall(r'<a class="back-link" href="/"', html)) == html.count("<section") == 2


def test_daily_log_has_scroll_to_top_button(logged_in_client):
    client, _ = logged_in_client
    html = client.get("/").data.decode()
    assert 'id="scroll-top-btn"' in html
    assert html.index("Daily log") < html.index('id="scroll-top-btn"')


def test_change_colours_flip_when_bmi_is_below_22(logged_in_client):
    client, _ = logged_in_client
    client.post("/settings", data={"height_value": "180", "height_unit": "cm", "moving_avg_days": "1"})
    # 60 kg at 1.80 m -> BMI 18.5
    _post_weights(client, [61.0, 60.0])
    assert 'class="stats bmi-low"' in client.get("/").data.decode()

    # 81 kg at 1.80 m -> BMI 25.0
    _post_weights(client, [81.0, 81.0])
    assert "bmi-low" not in client.get("/").data.decode().split('<section class="stats')[1].split(">")[0]


def test_change_colours_are_not_flipped_without_a_height(logged_in_client):
    client, _ = logged_in_client
    _post_weights(client, [61.0, 60.0])
    assert 'class="stats"' in client.get("/").data.decode()


def test_mobile_layout_rules_and_viewport_are_present(logged_in_client):
    client, _ = logged_in_client
    html = client.get("/").data.decode()
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in html
    css = client.get("/static/style.css").data.decode()
    assert "@media (max-width: 720px)" in css
    # short year labels on the chart axis for narrow screens
    assert "matchMedia('(max-width: 720px)')" in html


def test_account_dropdown_links_to_about_before_log_out(logged_in_client):
    client, _ = logged_in_client
    html = client.get("/").data.decode()
    dropdown = html[html.index('id="account-dropdown"'):]
    assert dropdown.index('href="/about"') < dropdown.index("Log out")


def test_two_week_range_sits_between_one_month_and_one_week(logged_in_client):
    from app.overview import CHART_RANGES

    keys = [key for key, _label, _days in CHART_RANGES]
    assert keys.index("1m") + 1 == keys.index("2w") == keys.index("1w") - 1
    assert {k: d for k, _l, d in CHART_RANGES}["2w"] == 14

    client, _ = logged_in_client
    resp = client.post("/chart-range", json={"range": "2w"})
    assert resp.status_code == 200


def test_two_week_button_appears_once_data_spans_14_days(logged_in_client, app):
    client, user_id = logged_in_client
    _add_entry(app, user_id, days_ago=0)
    _add_entry(app, user_id, days_ago=10)
    assert _range_buttons(client.get("/").data.decode())["2w"]["hidden"] is True
    _add_entry(app, user_id, days_ago=14)
    assert _range_buttons(client.get("/").data.decode())["2w"]["hidden"] is False


def test_change_boxes_link_to_their_chart_ranges(logged_in_client):
    client, _ = logged_in_client
    _post_weights(client, [80.0, 79.0])
    html = client.get("/").data.decode()
    for days, key in ((7, "1w"), (14, "2w"), (30, "1m"), (90, "3m"), (180, "6m"), (365, "1y")):
        assert f'data-days="{days}" data-range="{key}"' in html
    assert 'id="stat-total-card" data-range="all"' in html


def test_range_cutoff_uses_local_dates_not_utc(logged_in_client):
    client, _ = logged_in_client
    html = client.get("/").data.decode()
    # toISOString() is UTC: east of Greenwich it moved the cutoff a day too
    # early, so 1W/2W/1M each showed one day too many.
    assert "toISOString().slice(0, 10)" not in html
    assert "date.getFullYear()" in html
    assert "localIso(cutoff)" in html


def test_chart_pads_the_right_edge_up_to_today(logged_in_client):
    client, _ = logged_in_client
    html = client.get("/").data.decode()
    # The page script adds empty days up to today (local date) after the last entry.
    assert "localIso(today)" in html
    assert "values.push(null)" in html


def test_three_and_six_month_ranges_are_multiples_of_30_days(logged_in_client, app):
    from app.overview import CHART_RANGES

    days = {k: d for k, _l, d in CHART_RANGES}
    assert days["3m"] == 90
    assert days["6m"] == 180

    client, user_id = logged_in_client
    _add_entry(app, user_id, days_ago=0)
    _add_entry(app, user_id, days_ago=179)
    assert _range_buttons(client.get("/").data.decode())["6m"]["hidden"] is True
    _add_entry(app, user_id, days_ago=180)
    assert _range_buttons(client.get("/").data.decode())["6m"]["hidden"] is False


def test_three_month_button_appears_at_90_days(logged_in_client, app):
    client, user_id = logged_in_client
    _add_entry(app, user_id, days_ago=0)
    _add_entry(app, user_id, days_ago=89)
    assert _range_buttons(client.get("/").data.decode())["3m"]["hidden"] is True
    _add_entry(app, user_id, days_ago=90)
    assert _range_buttons(client.get("/").data.decode())["3m"]["hidden"] is False


def test_landscape_phone_layout_rules_are_present(client):
    css = client.get("/static/style.css").data.decode()
    # short landscape viewports get the same flowing layout as portrait phones
    assert "(orientation: landscape) and (max-height: 500px)" in css


def test_chart_tooltip_shows_bmi_from_the_moving_average(logged_in_client):
    client, _ = logged_in_client
    html = client.get("/").data.decode()
    assert "afterBody" in html
    assert "movingAvgBmiOn(items[0].label)" in html
    # the label comes from the page's translated strings, with the user's window (10 days by default)
    assert "t('bmiTooltip'" in html
    assert "BMI (10-day avg): %(bmi)s" in html


def test_responsive_breakpoints_and_axis_thinning_are_present(logged_in_client):
    client, _ = logged_in_client
    css = client.get("/static/style.css").data.decode()
    assert "@media (max-width: 1000px), (max-height: 600px)" in css  # flow layout
    assert "@media (max-width: 720px)" in css  # small text
    html = client.get("/").data.decode()
    assert "function thinTicks" in html and "onResize" in html


def test_chart_has_a_right_hand_bmi_axis_tied_to_the_weight_ticks(logged_in_client):
    client, _ = logged_in_client
    html = client.get("/").data.decode()
    assert "yBmi:" in html
    assert "position: 'right'" in html
    assert "oneDecimalFormat.format(bmiForWeight(weight))" in html
    assert "display: !!heightCm" in html  # only shown once a height is set


def test_robots_txt_blocks_crawlers_except_the_public_pages(client):
    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    assert resp.mimetype == "text/plain"
    body = resp.data.decode()
    assert "User-agent: *" in body
    assert "Disallow: /\n" in body
    assert "Allow: /$" in body
    assert "Allow: /about$" in body


def test_robots_txt_needs_no_login(logged_in_client):
    client, _ = logged_in_client
    assert client.get("/robots.txt").status_code == 200


def test_the_account_button_has_a_menu_icon_after_the_email_address(logged_in_client):
    client, _ = logged_in_client
    html = client.get("/").get_data(as_text=True)
    button = html[html.index('id="account-trigger"'):html.index('id="account-dropdown"')]
    assert "user@example.com" in button and 'class="menu-icon"' in button
    assert button.index("user@example.com") < button.index('class="menu-icon"')  # after the address
    assert 'aria-hidden="true"' in button[button.index('class="menu-icon"'):]  # decorative


def test_signed_out_visitors_have_no_account_button(client):
    html = client.get("/").get_data(as_text=True)
    assert 'id="account-trigger"' not in html and 'class="menu-icon"' not in html


def test_the_bmi_axis_copies_the_weight_range_again_after_its_ticks_are_built(logged_in_client):
    """Chart.js snaps an axis to its own ticks after afterDataLimits (bounds: 'ticks'), so on a narrow
    range (1W) the BMI axis drifted to another range than the weight axis. The copy must happen in
    afterBuildTicks too; the exact pixel alignment is checked in a real browser (see CLAUDE.md)."""
    client, _ = logged_in_client
    html = client.get("/").data.decode()
    block = html[html.index("afterBuildTicks(scale)"):]
    block = block[:block.index("ticks: {")]
    assert "scale.min = weightScale.min" in block and "scale.max = weightScale.max" in block
    data_limits = html[html.index("afterDataLimits(scale)"):html.index("afterBuildTicks(scale)")]
    assert "scale.min = weightScale.min" in data_limits and "scale.max = weightScale.max" in data_limits


def test_chart_tooltip_title_has_the_weekday_in_the_languages_own_order(logged_in_client):
    client, _ = logged_in_client
    html = client.get("/").data.decode()
    # Intl spells out weekday, day, abbreviated month and year, and orders them per language
    # ("Maandag 5 okt 2026", "Montag, 5. Okt. 2026", "2026. okt. 5., hétfő")
    assert "weekday: 'long', day: 'numeric', month: 'short', year: 'numeric'" in html
    # the tooltip title uses it (the daily log and the stat boxes keep the plain medium date)
    assert "title: (items) => (items.length ? fmtWeekdayDate(items[0].label) : '')" in html
    assert "dateFormat.format(parseIsoDate(iso))" in html


def test_overview_has_the_notes_by_date_for_the_chart_tooltip(logged_in_client):
    client, _ = logged_in_client
    _post_weights(client, [80.0, 79.0, 78.0], start=date(2026, 1, 1))

    def overview_notes():
        resp = client.post("/entries/field", json={"date": "2026-01-02", "weight": 79.0, "note": client_note[0]})
        return resp.get_json()["chart_notes"]

    client_note = ["Felt great after the long walk"]
    assert overview_notes() == {"2026-01-02": "Felt great after the long walk"}  # only days that have a note

    client_note[0] = "Changed my mind"
    assert overview_notes() == {"2026-01-02": "Changed my mind"}

    client_note[0] = ""  # clearing the note removes it from the tooltip data
    assert overview_notes() == {}


def test_page_embeds_the_notes_without_breaking_out_of_the_script(logged_in_client):
    client, _ = logged_in_client
    client.post("/entries/field", json={"date": "2026-01-02", "weight": 80.0, "note": "</script><b>hi</b> & more"})
    html = client.get("/").data.decode()
    assert script_value(html, "fullNotes") == {"2026-01-02": "</script><b>hi</b> & more"}  # intact
    assert "</script><b>hi" not in html  # tojson escapes < > &, so a note cannot close the script
    assert "\\u003c/script\\u003e" in html


def test_chart_tooltip_shows_the_note_under_the_bmi(logged_in_client):
    client, _ = logged_in_client
    html = client.get("/").data.decode()
    assert "footer: (items) => (items.length ? wrapNote(fullNotes[items[0].label]) : [])" in html
    assert "function wrapNote(" in html
    assert "fullNotes = data.chart_notes;" in html  # refreshed after every save


def test_note_editor_has_an_emoji_button_and_one_shared_panel(logged_in_client):
    client, _ = logged_in_client
    html = client.get("/").data.decode()
    # one panel for the whole log, not one per day (a copy per row would add thousands of buttons)
    assert html.count('id="emoji-panel"') == 1
    assert 'id="emoji-panel" role="group"' in html and "hidden>" in html.split('id="emoji-panel"')[1][:200]
    # every note editor (the day-row template and the server-rendered rows) has the toggle button
    assert html.count('class="note-emoji-btn"') == html.count('class="note-textarea"')
    assert 'aria-expanded="false" aria-label="Insert an emoji"' in html


def test_emoji_picker_lists_the_most_common_emoji_first(logged_in_client):
    client, _ = logged_in_client
    html = client.get("/").data.decode()
    # general favourites first, then the ones for movement, food and mood
    assert "const EMOJI = [\n    '😊', '😂', '👍', '❤️', '😢', '🎉', '🔥', '🙏'" in html
    assert html.index("'🏃'") > html.index("'🙏'")  # movement after the general favourites
    # recently used ones (kept per device) are shown above the list
    assert "const EMOJI_STORAGE_KEY = 'kilo.recentEmoji';" in html
    assert 'id="emoji-recent"' in html and "Recently used" in html


def test_emoji_picker_respects_the_note_length_limit(logged_in_client):
    client, _ = logged_in_client
    html = client.get("/").data.decode()
    # setRangeText ignores maxlength, so inserting checks it itself
    assert "textarea.value.length - selected + emoji.length > textarea.maxLength" in html
    assert 'maxlength="280"' in html


def test_emoji_picker_labels_are_translated(logged_in_client):
    client, _ = logged_in_client
    html = client.get("/?lang=nl").data.decode()
    assert 'aria-label="Emoji invoegen"' in html
    assert "Onlangs gebruikt" in html


def test_emoji_picker_styles_exist(client):
    css = client.get("/static/style.css").data.decode()
    assert ".emoji-panel" in css and ".emoji-grid" in css and ".emoji-btn" in css
    assert "@media (pointer: coarse)" in css  # bigger targets for fingertips


def test_note_editor_buttons_stay_on_one_line_and_the_box_is_wide(client):
    css = client.get("/static/style.css").data.decode()
    editor = css[css.index(".note-popover {"):css.index(".note-popover[hidden]")]
    assert "width: 260px;" in editor  # wide enough for the emoji button, Cancel and Save in any language
    assert "max-width: calc(100vw - 1.5rem);" in editor  # but never wider than a tiny screen
    actions = css[css.index(".note-popover-actions {"):]
    actions = actions[:actions.index("}")]
    assert "flex-wrap: nowrap;" in actions
    assert "flex-wrap: wrap" not in actions  # a wrapped row put Save under Cancel in 12 of 15 languages


def test_note_in_the_tooltip_is_not_italic(logged_in_client):
    client, _ = logged_in_client
    html = client.get("/").data.decode()
    # the canvas would slant the emoji in a note as well
    assert "footerFont: { weight: 'normal', style: 'normal' }" in html
    assert "italic" not in html.split("footerFont")[1][:120]


def test_note_editor_is_kept_inside_the_screen(logged_in_client):
    client, _ = logged_in_client
    html = client.get("/").data.decode()
    assert "function placePopover(popover)" in html
    # called when the editor opens, before the cursor goes into the text box
    opening = html.split("if (opening) {")[1][:120]
    assert "placePopover(popover);" in opening


def test_chart_js_is_served_by_the_app_not_a_cdn(logged_in_client):
    client, _ = logged_in_client
    html = client.get("/").data.decode()
    # No visitor data goes to a third party for the chart (see app/static/vendor/README.md).
    assert re.findall(r'<script src="([^"]+)"', html) == ["/static/vendor/chartjs-4.4.4/chart.umd.js"]
    script = client.get("/static/vendor/chartjs-4.4.4/chart.umd.js")
    assert script.status_code == 200
    assert b"Chart.js v4.4.4" in script.data
    assert b"sourceMappingURL" not in script.data
