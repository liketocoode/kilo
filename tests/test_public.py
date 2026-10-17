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

"""The public chart: /public/<id>, turned on and named in the settings."""

import hashlib
import re
import sqlite3
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect

from app import create_app, db, routes
from app.forms import PublicChartForm
from app.models import User, WeightEntry
from app.utils import hash_email
from tests.conftest import input_attrs, script_value, signup_and_verify

EMAIL = "owner@example.com"
NOTE = "secret-note-text"


@pytest.fixture
def owner(client):
    """The signed-in owner with a week of entries, one of them with a note."""
    signup_and_verify(client, EMAIL)
    user = User.query.filter_by(email_hash=hash_email(EMAIL)).one()
    user.height_cm = 180
    today = date.today()
    for days in range(7):
        db.session.add(WeightEntry(user_id=user.id, entry_date=today - timedelta(days=days), weight=80 + days / 10))
    db.session.add(WeightEntry(user_id=user.id, entry_date=today - timedelta(days=8), weight=81, note=NOTE))
    db.session.commit()
    return user


def share(client, public_id="", enabled=True, show_stats=True, show_weight=True, dark=False, range_key=""):
    data = {"public_id": public_id, "public_range": range_key}
    if dark:
        data["public_dark_mode"] = "y"
    if enabled:
        data["public_enabled"] = "y"
    if show_stats:
        data["public_show_stats"] = "y"
    if show_weight:
        data["public_show_weight"] = "y"
    return client.post("/settings/public", data=data)


# --- turning it on and choosing the id -----------------------------------------------------


def test_sharing_is_off_until_turned_on(client, owner):
    assert owner.public_enabled is False and owner.public_id is None
    page = client.get("/settings").data.decode()
    proposed = re.search(r'name="public_id"[^>]*value="([0-9a-f]{64})"', page).group(1)
    assert client.get(f"/public/{proposed}").status_code == 404


def test_the_proposed_id_is_the_hash_of_the_address_and_the_time(client, owner, monkeypatch):
    monkeypatch.setattr(routes, "time", SimpleNamespace(time=lambda: 1_760_000_000.7))
    expected = hashlib.sha256(f"{EMAIL}1760000000".encode()).hexdigest()
    assert f'value="{expected}"' in client.get("/settings").data.decode()

    response = share(client)  # empty field: a new id is made up the same way
    assert response.status_code == 302 and response.location.endswith("/settings#public-chart")
    assert owner.public_enabled is True and owner.public_id == expected
    assert owner.public_id != owner.email_hash  # not the Gravatar id
    assert client.get(f"/public/{expected}").status_code == 200


def test_a_taken_proposal_is_never_offered(app, owner):
    first = hashlib.sha256(f"{EMAIL}100".encode()).hexdigest()
    db.session.add(User(email_hash=hash_email("other@example.com"), public_id=first))
    db.session.commit()
    proposal = routes.propose_public_id(EMAIL, now=100)
    assert proposal == hashlib.sha256(f"{EMAIL}101".encode()).hexdigest()
    # Without the address in the session the id is random.
    assert re.fullmatch(r"[0-9a-f]{64}", routes.propose_public_id(None))


def test_a_chosen_id_is_stored_in_lowercase_and_found_in_any_case(client, owner):
    share(client, "  Ramon_Weight-2026 ")
    assert owner.public_id == "ramon_weight-2026"
    assert client.get("/public/ramon_weight-2026").status_code == 200
    assert client.get("/public/RAMON_weight-2026").status_code == 200


@pytest.mark.parametrize("bad", ["ab", "a" * 65, "with space", "../etc", "café", "a/b", "a.b"])
def test_an_invalid_id_is_refused(client, owner, bad):
    response = share(client, bad)
    assert response.status_code == 200
    assert "letters (a-z), digits, hyphens or underscores" in response.data.decode()
    assert owner.public_id is None and owner.public_enabled is False


def test_an_id_another_user_has_is_refused_even_when_not_shared(client, owner, monkeypatch):
    db.session.add(User(email_hash=hash_email("other@example.com"), public_id="taken"))
    db.session.commit()
    commits = []
    monkeypatch.setattr(db.session, "commit", lambda: commits.append(1))
    response = share(client, "TAKEN")
    assert "That ID is already taken" in response.data.decode()
    assert commits == []  # checked against the existing ids before anything is saved
    monkeypatch.undo()
    db.session.rollback()
    assert owner.public_id is None


def test_saving_your_own_id_again_is_fine(client, owner):
    share(client, "mine")
    response = share(client, "mine", enabled=False)
    assert response.status_code == 302
    assert owner.public_id == "mine" and owner.public_enabled is False


def test_an_id_taken_between_the_check_and_the_save_is_refused_not_a_crash(client, owner, monkeypatch):
    db.session.add(User(email_hash=hash_email("other@example.com"), public_id="race"))
    db.session.commit()
    monkeypatch.setattr(PublicChartForm, "validate_public_id", lambda self, field: None)
    response = share(client, "race")
    assert response.status_code == 200
    assert "That ID is already taken" in response.data.decode()


def test_turning_it_off_keeps_the_id_and_closes_the_page(client, owner):
    share(client, "mine")
    share(client, "mine", enabled=False)
    assert client.get("/public/mine").status_code == 404
    share(client, "mine")
    assert client.get("/public/mine").status_code == 200


def test_the_settings_show_the_address_and_the_iframe_code_once_shared(client, owner):
    assert "&lt;iframe" not in client.get("/settings").data.decode()
    share(client, "mine")
    page = client.get("/settings").data.decode()
    assert 'href="/public/mine"' in page
    assert '&lt;iframe src="http://localhost/public/mine"' in page


# --- the public page ------------------------------------------------------------------------


def test_the_public_page_shows_the_chart_and_the_stat_boxes(client, owner):
    share(client, "mine")
    html = client.get("/public/mine").data.decode()
    assert 'id="weight-chart"' in html and 'id="range-buttons"' in html
    assert 'id="stat-current"' in html and 'id="stat-bmi"' in html and 'data-days="7"' in html
    assert "Height:" in html  # BMI is shown, as the owner chose
    assert len(script_value(html, "fullValues")) == 9
    assert script_value(html, "fullBmis") is not None


def test_the_stat_boxes_are_off_until_turned_on(client, owner):
    assert owner.public_show_stats is False
    assert "checked" not in input_attrs(client.get("/settings"), "public_show_stats")
    share(client, "mine", show_stats=False)
    html = client.get("/public/mine").data.decode()
    assert 'id="weight-chart"' in html and 'id="range-buttons"' in html
    assert 'class="stats' not in html and 'id="stat-current"' not in html and "Height:" not in html

    share(client, "mine")
    assert owner.public_show_stats is True
    assert "checked" in input_attrs(client.get("/settings"), "public_show_stats")
    assert 'id="stat-current"' in client.get("/public/mine").data.decode()


def test_the_dashboard_always_shows_the_stat_boxes(client, owner):
    share(client, "mine", show_stats=False)
    assert 'id="stat-current"' in client.get("/").data.decode()


def test_the_public_page_never_shows_notes_the_address_or_the_daily_log(client, owner):
    share(client, "mine")
    html = client.get("/public/mine").data.decode()
    assert NOTE not in html
    assert script_value(html, "fullNotes") == {}
    assert EMAIL not in html and owner.email_hash not in html
    assert 'id="day-rows"' not in html and "/entries/field" not in html
    assert "/avatar" not in html and "account-menu" not in html


def test_a_signed_in_visitor_sees_the_owners_page_not_their_own_account(app, owner):
    owner.public_id, owner.public_enabled = "mine", True
    db.session.commit()
    visitor = app.test_client()
    signup_and_verify(visitor, "visitor@example.com")
    html = visitor.get("/public/mine").data.decode()
    assert "visitor@example.com" not in html and "account-menu" not in html
    assert '<meta name="csrf-token"' not in html


def test_an_anonymous_visitor_gets_no_cookie(app, owner):
    owner.public_id, owner.public_enabled = "mine", True
    db.session.commit()
    response = app.test_client().get("/public/mine")
    assert response.status_code == 200
    assert "Set-Cookie" not in response.headers


def test_the_public_page_may_be_shown_in_an_iframe(client, owner):
    share(client, "mine")
    response = client.get("/public/mine")
    assert "X-Frame-Options" not in response.headers
    assert "frame-ancestors" not in response.headers.get("Content-Security-Policy", "")
    assert 'target="_blank"' in response.data.decode().split("</header>")[0]


@pytest.mark.parametrize(
    "path", ["/", "/about", "/settings", "/settings/export", "/robots.txt", "/avatar", "/static/style.css", "/nope"]
)
def test_every_other_page_refuses_to_be_framed(client, owner, path):
    response = client.get(path)
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"].split("; ")
    assert response.headers["X-Frame-Options"] == "DENY"


def test_a_public_address_without_a_chart_may_be_framed_too(client, owner):
    """An embed whose owner stopped sharing shows the short notice, not a browser error."""
    response = client.get("/public/nobody")
    assert response.status_code == 404
    assert "X-Frame-Options" not in response.headers
    assert "frame-ancestors" not in response.headers["Content-Security-Policy"]


def test_the_theme_is_the_public_pages_own_not_the_owners(client, owner):
    share(client, "mine")
    assert 'data-theme="light"' in client.get("/public/mine").data.decode()
    owner.dark_mode = True  # the owner's own theme does not reach the public page
    db.session.commit()
    assert 'data-theme="light"' in client.get("/public/mine").data.decode()
    share(client, "mine", dark=True)
    assert owner.public_dark_mode is True
    assert 'data-theme="dark"' in client.get("/public/mine").data.decode()
    owner.dark_mode = False
    db.session.commit()
    assert 'data-theme="dark"' in client.get("/public/mine").data.decode()


def test_the_address_can_still_choose_the_theme(client, owner):
    """Embed codes made before the theme was saved carry ?theme=; it still wins."""
    share(client, "mine", dark=True)
    assert 'data-theme="light"' in client.get("/public/mine?theme=light").data.decode()
    assert 'data-theme="dark"' in client.get("/public/mine?theme=evil").data.decode()
    share(client, "mine")
    assert 'data-theme="dark"' in client.get("/public/mine?theme=dark").data.decode()


def test_a_page_without_a_chart_is_a_404_page_not_a_redirect_and_is_logged(client, owner, monkeypatch):
    logged = []
    monkeypatch.setattr(routes, "log_event", lambda event, **fields: logged.append((event, fields)))
    response = client.get("/public/nobody", headers={"Accept": "text/html"})
    assert response.status_code == 404
    assert "There is no public chart at this address" in response.data.decode()
    assert logged == [("notfound", {"path": "/public/nobody"})]


def test_the_page_is_translated(client, owner):
    share(client, "mine")
    html = client.get("/public/mine", headers={"Accept-Language": "nl"}).data.decode()
    assert '<html lang="nl"' in html and "Gewichtsgrafiek" in html


def test_removing_the_account_removes_the_page(client, owner):
    share(client, "mine")
    db.session.delete(owner)
    db.session.commit()
    assert client.get("/public/mine").status_code == 404


# --- an existing database ------------------------------------------------------------------


def test_an_older_database_gets_the_columns_and_the_unique_index(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE user (id INTEGER PRIMARY KEY, email_hash VARCHAR(64) NOT NULL UNIQUE,"
            " verified_at DATETIME, created_at DATETIME NOT NULL, code_hash VARCHAR(255),"
            " code_expires_at DATETIME, code_attempts INTEGER NOT NULL DEFAULT 0,"
            " height_cm FLOAT, height_unit VARCHAR(2) NOT NULL DEFAULT 'cm',"
            " dark_mode BOOLEAN NOT NULL DEFAULT 0, chart_range VARCHAR(4) NOT NULL DEFAULT 'all')"
        )
        conn.execute("INSERT INTO user (email_hash, created_at) VALUES ('a', '2026-01-01')")
    made = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{path}"})
    with made.app_context():
        columns = {c["name"] for c in inspect(db.engine).get_columns("user")}
        assert {"public_enabled", "public_id", "public_show_stats", "public_show_weight", "session_version"} <= columns
        indexes = {i["name"]: i for i in inspect(db.engine).get_indexes("user")}
        assert indexes["ix_user_public_id"]["unique"]
        user = User.query.one()
        assert user.public_enabled is False and user.public_id is None
        assert user.public_show_stats is False
        assert user.public_show_weight is True
        db.session.remove()
        db.engine.dispose()


# --- "Show my weight" -----------------------------------------------------------------------------

def test_the_weight_is_shown_until_turned_off(client, owner):
    assert owner.public_show_weight is True
    assert "checked" in input_attrs(client.get("/settings"), "public_show_weight")
    share(client, "mine")
    html = client.get("/public/mine").data.decode()
    assert script_value(html, "fullValues")[-1] == 80.0
    assert script_value(html, "hideWeights") is False


def test_without_the_weight_no_kilogram_reaches_the_page(client, owner):
    share(client, "mine")
    shown = client.get("/public/mine").data.decode()
    real = script_value(shown, "fullValues") + script_value(shown, "fullMovingAvg")
    share(client, "mine", show_weight=False, show_stats=False)
    assert owner.public_show_weight is False
    html = client.get("/public/mine").data.decode()
    assert script_value(html, "hideWeights") is True
    assert script_value(html, "heightCm") is None
    assert script_value(html, "fullBmis") is None
    values = [v for v in script_value(html, "fullValues") if v is not None]
    moving = [v for v in script_value(html, "fullMovingAvg") if v is not None]
    assert min(values + moving) == 0 and max(values + moving) == 100
    # Exactly the real values rescaled to 0-100, and nothing else in the data.
    low, high = min(real), max(real)
    assert values + moving == [round((v - low) / (high - low) * 100, 2) for v in real]
    assert "kg" not in html.split('<section class="chart-wrap">')[1].split("</section>")[0]
    assert 'class="stats' not in html and "Height:" not in html and "BMI" not in html.split("<script")[0]


def test_the_shape_of_the_line_is_kept(client, owner, app):
    share(client, "mine")
    shown = script_value(client.get("/public/mine").data.decode(), "fullValues")
    share(client, "mine", show_weight=False, show_stats=False)
    hidden = script_value(client.get("/public/mine").data.decode(), "fullValues")
    assert len(hidden) == len(shown)
    # Same order everywhere: a higher weight is a higher point, and equal ones stay equal.
    for i in range(len(shown)):
        for j in range(len(shown)):
            assert (shown[i] < shown[j]) == (hidden[i] < hidden[j])
            assert (shown[i] == shown[j]) == (hidden[i] == hidden[j])


def test_a_flat_line_stays_in_the_middle(client, owner):
    for entry in WeightEntry.query.all():
        entry.weight = 80
    db.session.commit()
    share(client, "mine", show_weight=False, show_stats=False)
    values = script_value(client.get("/public/mine").data.decode(), "fullValues")
    assert set(values) == {50.0}


def test_without_the_weight_the_stat_boxes_are_off_whatever_was_sent(client, owner):
    share(client, "mine", show_weight=False, show_stats=True)  # e.g. without the page script
    assert owner.public_show_weight is False and owner.public_show_stats is False
    assert 'class="stats' not in client.get("/public/mine").data.decode()


def test_an_older_stats_setting_is_ignored_while_the_weight_is_hidden(client, owner):
    share(client, "mine")
    owner.public_show_stats, owner.public_show_weight = True, False  # as if set before this rule
    db.session.commit()
    assert 'class="stats' not in client.get("/public/mine").data.decode()


def test_the_settings_grey_the_stat_boxes_out_without_the_weight(client, owner):
    share(client, "mine", show_weight=False, show_stats=False)
    page = client.get("/settings").data.decode()
    assert "disabled" in input_attrs(page, "public_show_stats")
    assert 'class="toggle-row is-disabled" id="public-stats-row"' in page
    share(client, "mine")
    page = client.get("/settings").data.decode()
    assert "checked" in input_attrs(page, "public_show_stats")
    assert 'class="toggle-row" id="public-stats-row"' in page


def test_the_dashboard_always_shows_the_weights(client, owner):
    share(client, "mine", show_weight=False, show_stats=False)
    html = client.get("/").data.decode()
    assert script_value(html, "hideWeights") is False
    assert script_value(html, "fullValues")[-1] == 80.0


# --- a fixed range: ?range=3m, or the saved one -------------------------------------------------------


def test_a_range_in_the_address_fixes_the_chart(client, owner):
    share(client, "mine")
    html = client.get("/public/mine?range=1w").data.decode()
    assert script_value(html, "currentRange") == "1w"
    assert 'id="range-buttons"' not in html and "range-btn" not in html.split("<script")[0]
    # The stat boxes no longer switch the range either.
    assert "stat-card-link" not in html.split("<script")[0] and 'role="button"' not in html
    assert 'id="stat-current"' in html and 'data-days="7"' in html


@pytest.mark.parametrize("value, key", [("3M", "3m"), ("ALL", "all"), ("All", "all"), ("1W", "1w"), ("10Y", "10y")])
def test_the_range_is_not_case_sensitive(client, owner, value, key):
    share(client, "mine")
    html = client.get(f"/public/mine?range={value}").data.decode()
    assert script_value(html, "currentRange") == key and 'id="range-buttons"' not in html


def test_any_range_may_be_fixed_even_one_the_data_does_not_span(client, owner):
    share(client, "mine")
    assert script_value(client.get("/public/mine?range=20y").data.decode(), "currentRange") == "20y"


@pytest.mark.parametrize("query", ["", "?range=", "?range=3x", "?range=3 m", "?range=<b>"])
def test_without_a_known_range_the_buttons_stay(client, owner, query):
    share(client, "mine")
    html = client.get("/public/mine" + query).data.decode()
    assert 'id="range-buttons"' in html and "stat-card-link" in html
    assert script_value(html, "currentRange") == owner.chart_range


def test_the_saved_range_fixes_the_chart_when_the_address_has_none(client, owner):
    share(client, "mine", range_key="3m")
    assert owner.public_range == "3m"
    for query in ("", "?range=", "?range=3x", "?theme=dark"):
        html = client.get("/public/mine" + query).data.decode()
        assert script_value(html, "currentRange") == "3m" and 'id="range-buttons"' not in html


def test_a_range_in_the_address_wins_over_the_saved_one(client, owner):
    share(client, "mine", range_key="3m")
    html = client.get("/public/mine?range=1W").data.decode()
    assert script_value(html, "currentRange") == "1w" and 'id="range-buttons"' not in html


@pytest.mark.parametrize("sent, saved", [("1y", "1y"), ("", None), ("1Y", None), ("3x", None), ("<b>", None)])
def test_only_a_known_range_is_saved(client, owner, sent, saved):
    share(client, "mine", range_key="3m")
    share(client, "mine", range_key=sent)
    assert owner.public_range == saved


def test_the_dashboard_ignores_it(client, owner):
    html = client.get("/?range=1w").data.decode()
    assert 'id="range-buttons"' in html
    assert script_value(html, "currentRange") == owner.chart_range


def range_menu(page):
    return page.split('<select id="embed-range" name="public_range">')[1].split("</select>")[0]


def test_the_settings_offer_the_ranges_for_the_public_page(client, owner):
    share(client, "mine")
    menu = range_menu(client.get("/settings").data.decode())
    assert re.findall(r'<option value="([^"]*)"', menu) == ["", *[key for key, _label, _days in routes.CHART_RANGES]]
    assert "Visitor chooses (range buttons)" in menu
    assert "selected" not in menu  # nothing saved: the first choice


def test_the_settings_show_the_saved_range_also_while_not_shared(client, owner):
    share(client, "mine", range_key="6m")
    assert re.findall(r'<option value="([^"]*)" selected>', range_menu(client.get("/settings").data.decode())) == ["6m"]
    share(client, "mine", enabled=False, range_key="6m")
    assert re.findall(r'<option value="([^"]*)" selected>', range_menu(client.get("/settings").data.decode())) == ["6m"]
    assert owner.public_range == "6m"


def test_a_refused_save_keeps_the_picked_range(client, owner, app):
    with app.app_context():
        db.session.add(User(email_hash="other", public_id="taken"))
        db.session.commit()
    page = share(client, "taken", range_key="1m").data.decode()
    assert "That ID is already taken" in page
    assert re.findall(r'<option value="([^"]*)" selected>', range_menu(page)) == ["1m"]


def test_the_settings_show_the_saved_theme_of_the_public_page(client, owner):
    owner.dark_mode = True  # the owner's own theme is not the public page's
    db.session.commit()
    page = client.get("/settings").data.decode()
    assert "checked" not in input_attrs(page, "public_dark_mode")
    share(client, "mine", dark=True)
    page = client.get("/settings").data.decode()
    assert "checked" in input_attrs(page, "public_dark_mode")
    # Not only while shared: turning sharing off keeps the theme for the next time.
    share(client, "mine", enabled=False, dark=True)
    assert 'id="public_dark_mode"' in client.get("/settings").data.decode()
    assert owner.public_dark_mode is True


def test_the_link_and_embed_code_say_the_theme_and_the_range(client, owner):
    """The script puts ?theme= and &range= in both, from the switch and the menu."""
    share(client, "mine", dark=True)
    script = client.get("/settings").data.decode().split("</select>")[-1]
    assert "params.set('theme', document.getElementById('public_dark_mode').checked ? 'dark' : 'light')" in script
    assert "if (range) params.set('range', range)" in script


@pytest.mark.parametrize(
    "show_weight, show_stats, shown",
    [(True, True, True), (True, False, False), (False, True, False), (False, False, False)],
)
def test_the_stat_boxes_are_shown_only_with_the_weight(show_weight, show_stats, shown):
    user = User(public_show_weight=show_weight, public_show_stats=show_stats)
    assert user.public_stats_shown is shown
