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

"""The settings page and the preferences: height, dark mode, the moving-average window.

Also the theme every page gets and the settings page's back arrows. The public chart's settings
are in test_public.py, import and export in test_import_export.py.
"""

import re

import pytest

from app import db
from app.models import User


def test_settings_requires_login(client):
    resp = client.get("/settings")
    assert resp.status_code == 302


def test_settings_updates_height_in_cm(logged_in_client, app):
    client, user_id = logged_in_client
    resp = client.post(
        "/settings", data={"height_value": "180", "height_unit": "cm"}, follow_redirects=True
    )
    assert resp.status_code == 200
    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.height_cm == 180
        assert user.height_unit == "cm"


def test_settings_updates_height_in_meters(logged_in_client, app):
    client, user_id = logged_in_client
    client.post("/settings", data={"height_value": "1.8", "height_unit": "m"})
    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.height_cm == 180
        assert user.height_unit == "m"


@pytest.mark.parametrize("value, unit, shown", [("1.87", "m", "1.87"), ("187.5", "cm", "187.5")])
def test_settings_show_the_height_in_the_unit_it_was_saved_in(logged_in_client, value, unit, shown):
    client, _user_id = logged_in_client
    client.post("/settings", data={"height_value": value, "height_unit": unit})
    page = client.get("/settings", headers={"Accept-Language": "en"}).get_data(as_text=True)
    assert re.search(r'<input[^>]*name="height_value"[^>]*>', page).group(0).count(f'value="{shown}"') == 1


def test_set_dark_mode_requires_login(client):
    resp = client.post("/settings/dark-mode", json={"dark_mode": True})
    assert resp.status_code == 302


def test_set_dark_mode_saves_immediately(logged_in_client, app):
    client, user_id = logged_in_client
    resp = client.post("/settings/dark-mode", json={"dark_mode": True})
    assert resp.get_json() == {"status": "saved", "dark_mode": True}
    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.dark_mode is True

    resp2 = client.post("/settings/dark-mode", json={"dark_mode": False})
    assert resp2.get_json() == {"status": "saved", "dark_mode": False}
    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.dark_mode is False


def test_dark_mode_toggle_in_account_dropdown(logged_in_client):
    client, _ = logged_in_client
    html = client.get("/").data.decode()
    assert 'class="dark-mode-toggle"' in html


def test_dark_mode_field_is_first_in_settings_form(logged_in_client):
    client, _ = logged_in_client
    html = client.get("/settings").data.decode()
    assert html.index("dark-mode-toggle") < html.index("height_value")


def test_default_theme_is_light(logged_in_client, app):
    client, user_id = logged_in_client
    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.dark_mode is False

    resp = client.get("/")
    assert b'data-theme="light"' in resp.data


def test_logged_out_theme_is_light(client):
    resp = client.get("/")
    assert b'data-theme="light"' in resp.data


def test_settings_toggle_dark_mode(logged_in_client, app):
    client, user_id = logged_in_client
    resp = client.post(
        "/settings",
        data={"height_value": "180", "height_unit": "cm", "dark_mode": "y"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.dark_mode is True

    resp2 = client.get("/")
    assert b'data-theme="dark"' in resp2.data


def test_settings_untoggle_dark_mode(logged_in_client, app):
    client, user_id = logged_in_client
    client.post(
        "/settings", data={"height_value": "180", "height_unit": "cm", "dark_mode": "y"}
    )
    client.post("/settings", data={"height_value": "180", "height_unit": "cm"})
    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.dark_mode is False


def test_moving_avg_days_defaults_to_10(logged_in_client, app):
    _, user_id = logged_in_client
    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.moving_avg_days == 10


def test_settings_updates_moving_avg_days(logged_in_client, app):
    client, user_id = logged_in_client
    client.post(
        "/settings",
        data={"height_value": "180", "height_unit": "cm", "moving_avg_days": "14"},
    )
    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.moving_avg_days == 14


@pytest.mark.parametrize(
    "value,unit,ok",
    [
        ("50", "cm", True),
        ("275", "cm", True),
        ("49.9", "cm", False),
        ("275.1", "cm", False),
        ("0.5", "m", True),
        ("2.75", "m", True),
        ("0.49", "m", False),
        ("2.76", "m", False),
        ("180", "m", False),
        ("1.8", "cm", False),
    ],
)
def test_height_must_be_between_50_and_275_cm(logged_in_client, app, value, unit, ok):
    client, user_id = logged_in_client
    resp = client.post("/settings", data={"height_value": value, "height_unit": unit})
    user = db.session.get(User, user_id)
    if ok:
        assert resp.status_code == 302
        assert user.height_cm == pytest.approx(float(value) * (100 if unit == "m" else 1))
    else:
        assert resp.status_code == 200
        assert b"between 50 and 275 cm" in resp.data
        assert user.height_cm is None


def test_non_numeric_height_is_rejected_without_crashing(logged_in_client):
    client, _ = logged_in_client
    resp = client.post("/settings", data={"height_value": "tall", "height_unit": "cm"})
    assert resp.status_code == 200


@pytest.mark.parametrize("height, unit, cm", [("1,8", "m", 180), ("1.8", "m", 180), ("180,5", "cm", 180.5)])
def test_height_accepts_comma_and_point(logged_in_client, height, unit, cm):
    client, user_id = logged_in_client
    resp = client.post("/settings", data={"height_value": height, "height_unit": unit})
    assert resp.status_code == 302
    assert db.session.get(User, user_id).height_cm == pytest.approx(cm)


@pytest.mark.parametrize("height", ["1e2", "1_8", "nan", "-180", "+180", "1,234.5"])
def test_height_must_be_a_plain_decimal(logged_in_client, height):
    client, user_id = logged_in_client
    resp = client.post("/settings", data={"height_value": height, "height_unit": "cm"})
    assert resp.status_code == 200
    assert db.session.get(User, user_id).height_cm is None


def test_number_fields_are_marked_decimal_only(logged_in_client):
    client, _ = logged_in_client
    assert b"data-decimal-only" in client.get("/settings").data
    index = client.get("/").data
    assert index.count(b"data-decimal-only") >= 2


def test_every_settings_section_has_a_back_link_to_the_dashboard(logged_in_client):
    client, _ = logged_in_client
    html = client.get("/settings").data.decode()
    assert len(re.findall(r'<a class="back-link" href="/"', html)) == html.count("<section") == 6


def test_remove_data_page_has_back_link_to_settings(logged_in_client):
    client, _ = logged_in_client
    html = client.get("/settings/remove").data.decode()
    assert re.search(r'<a class="back-link" href="/settings"', html)
