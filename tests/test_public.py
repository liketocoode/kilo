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
import json
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
from tests.conftest import signup_and_verify

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


def share(client, public_id="", enabled=True, show_stats=True):
    data = {"public_id": public_id}
    if enabled:
        data["public_enabled"] = "y"
    if show_stats:
        data["public_show_stats"] = "y"
    return client.post("/settings/public", data=data)


def script_value(html, name):
    """The JSON a `let name = ...;` line of the page script holds."""
    return json.loads(re.search(rf"let {name} = (.*);\n", html).group(1))


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
    assert '<input id="public_show_stats"' in client.get("/settings").data.decode()
    share(client, "mine", show_stats=False)
    html = client.get("/public/mine").data.decode()
    assert 'id="weight-chart"' in html and 'id="range-buttons"' in html
    assert 'class="stats' not in html and 'id="stat-current"' not in html and "Height:" not in html

    share(client, "mine")
    assert owner.public_show_stats is True
    assert '<input checked id="public_show_stats"' in client.get("/settings").data.decode()
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
    assert response.headers["Content-Security-Policy"] == "frame-ancestors 'none'"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_a_public_address_without_a_chart_may_be_framed_too(client, owner):
    """An embed whose owner stopped sharing shows the short notice, not a browser error."""
    response = client.get("/public/nobody")
    assert response.status_code == 404
    assert "X-Frame-Options" not in response.headers and "Content-Security-Policy" not in response.headers


def test_the_theme_follows_the_owner_or_the_address(client, owner):
    share(client, "mine")
    assert 'data-theme="light"' in client.get("/public/mine").data.decode()
    owner.dark_mode = True
    db.session.commit()
    assert 'data-theme="dark"' in client.get("/public/mine").data.decode()
    assert 'data-theme="light"' in client.get("/public/mine?theme=light").data.decode()
    assert 'data-theme="dark"' in client.get("/public/mine?theme=evil").data.decode()


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
        assert {"public_enabled", "public_id", "public_show_stats"} <= columns
        indexes = {i["name"]: i for i in inspect(db.engine).get_indexes("user")}
        assert indexes["ix_user_public_id"]["unique"]
        user = User.query.one()
        assert user.public_enabled is False and user.public_id is None
        assert user.public_show_stats is False
        db.session.remove()
        db.engine.dispose()
