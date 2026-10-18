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

"""Saving, clearing and paging the days of the daily log (the JSON entries API).

One day's weight and note, Undo after clearing, the question about an unusual weight, the
rules for a weight, the checks on a JSON request, and the older days for the infinite scroll.
"""

from datetime import date, timedelta

import pytest

from app import db
from app.models import User, WeightEntry
from tests.conftest import signup_and_verify


def test_save_field_creates_entry(logged_in_client, app):
    client, user_id = logged_in_client
    resp = client.post(
        "/entries/field",
        json={"date": "2026-01-01", "weight": 80.5, "note": "morning"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "saved"
    with app.app_context():
        entry = WeightEntry.query.filter_by(user_id=user_id).one()
        assert entry.weight == 80.5
        assert entry.note == "morning"


def test_save_field_upserts_same_date(logged_in_client, app):
    client, user_id = logged_in_client
    client.post("/entries/field", json={"date": "2026-01-01", "weight": 80.5})
    client.post("/entries/field", json={"date": "2026-01-01", "weight": 81.0})
    with app.app_context():
        entries = WeightEntry.query.filter_by(user_id=user_id).all()
        assert len(entries) == 1
        assert entries[0].weight == 81.0


def test_save_field_empty_weight_deletes_entry(logged_in_client, app):
    client, user_id = logged_in_client
    client.post("/entries/field", json={"date": "2026-01-01", "weight": 80.5})
    resp = client.post("/entries/field", json={"date": "2026-01-01", "weight": ""})
    assert resp.get_json()["status"] == "cleared"
    with app.app_context():
        assert WeightEntry.query.filter_by(user_id=user_id).count() == 0


def test_clearing_a_weight_says_what_was_removed_so_it_can_be_undone(logged_in_client, app):
    client, user_id = logged_in_client
    client.post("/entries/field", json={"date": "2026-01-01", "weight": "80,5", "note": "after the party"})
    removed = client.post("/entries/field", json={"date": "2026-01-01", "weight": ""}).get_json()["removed"]
    assert removed == {"weight": 80.5, "note": "after the party"}

    # "Undo" saves the same values again (confirmed, so no outlier question).
    resp = client.post(
        "/entries/field", json={"date": "2026-01-01", "weight": str(removed["weight"]), "note": removed["note"],
                                "confirm": True},
    )
    assert resp.get_json()["status"] == "saved"
    with app.app_context():
        (entry,) = WeightEntry.query.filter_by(user_id=user_id).all()
        assert (entry.weight, entry.note) == (80.5, "after the party")


def test_clearing_a_day_without_a_weight_removes_nothing(logged_in_client):
    client, _ = logged_in_client
    data = client.post("/entries/field", json={"date": "2026-01-01", "weight": ""}).get_json()
    assert data["status"] == "cleared" and data["removed"] is None


def test_a_removed_weight_without_a_note_gives_an_empty_note(logged_in_client):
    client, _ = logged_in_client
    client.post("/entries/field", json={"date": "2026-01-01", "weight": 80.5})
    data = client.post("/entries/field", json={"date": "2026-01-01", "weight": ""}).get_json()
    assert data["removed"] == {"weight": 80.5, "note": ""}


def test_the_dashboard_has_the_undo_notice(logged_in_client):
    client, _ = logged_in_client
    html = client.get("/").get_data(as_text=True)
    assert 'id="undo-toast"' in html and 'role="status"' in html and ">Undo</button>" in html
    assert '"weightRemoved": "Weight of %(date)s removed."' in html


def test_save_field_rejects_invalid_weight(logged_in_client, app):
    client, user_id = logged_in_client
    resp = client.post("/entries/field", json={"date": "2026-01-01", "weight": -5})
    assert resp.status_code == 400
    with app.app_context():
        assert WeightEntry.query.filter_by(user_id=user_id).count() == 0


@pytest.mark.parametrize("weight, saved", [(80.5, 80.5), (70, 70.0), ("80,5", 80.5), ("80.5", 80.5)])
def test_save_field_takes_a_json_number_or_text(logged_in_client, app, weight, saved):
    client, user_id = logged_in_client
    resp = client.post("/entries/field", json={"date": "2026-01-01", "weight": weight, "confirm": True})
    assert resp.status_code == 200
    with app.app_context():
        assert WeightEntry.query.filter_by(user_id=user_id).one().weight == saved


@pytest.mark.parametrize(
    "weight", [True, float("nan"), float("inf"), -5, [80], {"kg": 80}, "1e2", "nan"],
    ids=["true", "nan", "inf", "negative", "list", "object", "exponent", "nan-text"],
)
def test_save_field_takes_only_a_plain_number_as_weight(logged_in_client, app, weight):
    """A JSON number goes through the same check as typed text: true is not 1 kg, NaN not a weight."""
    client, user_id = logged_in_client
    resp = client.post("/entries/field", json={"date": "2026-01-01", "weight": weight, "confirm": True})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "Enter a valid weight, like 72.5."
    with app.app_context():
        assert WeightEntry.query.filter_by(user_id=user_id).count() == 0


@pytest.mark.parametrize("url", ["/entries/field", "/settings/dark-mode", "/chart-range"])
@pytest.mark.parametrize("body", [["x"], "text", 5, True], ids=["list", "string", "number", "true"])
def test_a_json_body_that_is_not_an_object_is_refused(logged_in_client, app, url, body):
    """A list or a number in place of an object was a 500 (it has no .get); nothing changes."""
    client, user_id = logged_in_client
    client.post("/settings/dark-mode", json={"dark_mode": True})
    resp = client.post(url, json=body)
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Invalid request."}
    with app.app_context():
        user = db.session.get(User, user_id)
        assert (user.dark_mode, user.chart_range, user.entries.count()) == (True, "all", 0)


@pytest.mark.parametrize("value", ["nope", "", 5, None, ["2026-01-01"], {"y": 2026}])
def test_save_field_refuses_a_date_that_is_not_an_iso_date(logged_in_client, value):
    client, _user_id = logged_in_client
    resp = client.post("/entries/field", json={"date": value, "weight": "80"})
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Invalid date."}


@pytest.mark.parametrize("before", ["garbage", "", "2026-13-01"])
def test_entries_window_refuses_a_date_that_is_not_one(logged_in_client, before):
    client, _user_id = logged_in_client
    resp = client.get(f"/entries/window?before={before}")
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Invalid date."}


def test_a_request_without_a_json_body_reports_the_missing_field(logged_in_client):
    """No body (or not JSON) is not "Invalid request.": each endpoint names what it misses."""
    client, _user_id = logged_in_client
    assert client.post("/entries/field").get_json() == {"error": "Invalid date."}
    assert client.post("/chart-range", data="range=1w").get_json() == {"error": "Invalid range."}
    assert client.post("/settings/dark-mode", json=None).status_code == 200  # as before: off


def test_save_field_requires_login(client):
    resp = client.post("/entries/field", json={"date": "2026-01-01", "weight": 80.5})
    assert resp.status_code == 302


def test_entries_window_returns_days_before_given_date(logged_in_client, app):
    client, user_id = logged_in_client
    with app.app_context():
        db.session.add(WeightEntry(user_id=user_id, entry_date=date(2026, 1, 1), weight=80.0))
        db.session.commit()

    resp = client.get("/entries/window?before=2026-01-02")
    assert resp.status_code == 200
    days = resp.get_json()["days"]
    assert len(days) == 30
    assert days[0]["date"] == "2026-01-01"
    assert days[0]["weight"] == 80.0
    assert days[-1]["date"] == (date(2026, 1, 1) - timedelta(days=29)).isoformat()


def test_data_is_isolated_between_users(app):
    client_a = app.test_client()
    client_b = app.test_client()

    signup_and_verify(client_a, "a@example.com")
    signup_and_verify(client_b, "b@example.com")

    client_a.post("/entries/field", json={"date": "2026-01-01", "weight": 70.0})
    client_b.post("/entries/field", json={"date": "2026-01-01", "weight": 90.0})

    resp_a = client_a.get("/")
    resp_b = client_b.get("/")
    assert b"70.0" in resp_a.data
    assert b"90.0" not in resp_a.data
    assert b"90.0" in resp_b.data
    assert b"70.0" not in resp_b.data

    with app.app_context():
        assert WeightEntry.query.count() == 2


def test_save_field_warns_on_outlier_before_neighbor(logged_in_client, app):
    client, user_id = logged_in_client
    client.post("/entries/field", json={"date": "2026-09-01", "weight": 80.0})

    resp = client.post("/entries/field", json={"date": "2026-09-05", "weight": 95.0})
    data = resp.get_json()
    assert data["status"] == "warning"
    assert "message" in data
    with app.app_context():
        assert WeightEntry.query.filter_by(user_id=user_id, entry_date=date(2026, 9, 5)).count() == 0


def test_save_field_warning_message_mentions_neighbor(logged_in_client):
    client, _ = logged_in_client
    client.post("/entries/field", json={"date": "2026-09-01", "weight": 80.0})

    resp = client.post("/entries/field", json={"date": "2026-09-05", "weight": 95.0})
    message = resp.get_json()["message"]
    assert "80" in message
    assert "Sep 1, 2026" in message  # the neighbour's date, formatted for the locale


def test_save_field_confirm_overrides_outlier_warning(logged_in_client, app):
    client, user_id = logged_in_client
    client.post("/entries/field", json={"date": "2026-09-01", "weight": 80.0})

    resp = client.post(
        "/entries/field", json={"date": "2026-09-05", "weight": 95.0, "confirm": True}
    )
    data = resp.get_json()
    assert data["status"] == "saved"
    with app.app_context():
        entry = WeightEntry.query.filter_by(user_id=user_id, entry_date=date(2026, 9, 5)).one()
        assert entry.weight == 95.0


def test_save_field_no_warning_within_threshold(logged_in_client):
    client, _ = logged_in_client
    client.post("/entries/field", json={"date": "2026-09-01", "weight": 80.0})

    # 85 is a 6.25% change from 80 — within the 10% threshold
    resp = client.post("/entries/field", json={"date": "2026-09-05", "weight": 85.0})
    assert resp.get_json()["status"] == "saved"


def test_save_field_checks_after_neighbor_when_no_before_neighbor(logged_in_client):
    client, _ = logged_in_client
    client.post("/entries/field", json={"date": "2026-09-10", "weight": 80.0})

    # Inserting an earlier date with no entry before it, but one after it
    # that differs by more than 10%.
    resp = client.post("/entries/field", json={"date": "2026-09-01", "weight": 95.0})
    assert resp.get_json()["status"] == "warning"


def test_save_field_no_neighbors_no_warning(logged_in_client):
    client, _ = logged_in_client
    # The very first entry ever — nothing to compare against.
    resp = client.post("/entries/field", json={"date": "2026-09-01", "weight": 300.0})
    assert resp.get_json()["status"] == "saved"


def test_save_field_editing_existing_entry_does_not_compare_to_itself(logged_in_client):
    client, _ = logged_in_client
    client.post("/entries/field", json={"date": "2026-09-01", "weight": 80.0})
    # Re-editing that same day to a very different value: only neighbor is
    # itself, which the query excludes, so there's nothing to warn against.
    resp = client.post("/entries/field", json={"date": "2026-09-01", "weight": 200.0})
    assert resp.get_json()["status"] == "saved"


@pytest.mark.parametrize(
    "weight", ["1e2", "1_0", "nan", "inf", "-80", "+80", "abc", "8,0,1", "1,234.5", "80,5.1", "80.5.1", ",", "."]
)
def test_weight_must_be_a_plain_decimal(logged_in_client, weight):
    client, _ = logged_in_client
    resp = client.post("/entries/field", json={"date": "2026-01-01", "weight": weight})
    assert resp.status_code == 400
    assert WeightEntry.query.count() == 0


@pytest.mark.parametrize("weight", ["80", "80.5", "80.", "80.25", "80,5", " 80,5 ", "80,", "80,25"])
def test_plain_decimal_weights_are_accepted(logged_in_client, weight):
    client, _ = logged_in_client
    resp = client.post("/entries/field", json={"date": "2026-01-01", "weight": weight, "confirm": True})
    assert resp.status_code == 200


@pytest.mark.parametrize("weight", ["72,5", "72.5"])
def test_comma_and_point_save_the_same_weight(logged_in_client, weight):
    client, _ = logged_in_client
    resp = client.post("/entries/field", json={"date": "2026-01-01", "weight": weight, "confirm": True})
    assert resp.status_code == 200
    assert WeightEntry.query.one().weight == 72.5


@pytest.mark.parametrize(
    "weight, ok",
    [("1", True), ("0.9", False), ("500", True), ("500,0", True), ("500.1", False), ("501", False), ("1000", False)],
)
def test_weight_must_be_between_1_and_500_kg(logged_in_client, weight, ok):
    client, _ = logged_in_client
    resp = client.post("/entries/field", json={"date": "2026-01-01", "weight": weight, "confirm": True})
    assert resp.status_code == (200 if ok else 400)
    assert WeightEntry.query.count() == (1 if ok else 0)
    if not ok:
        assert resp.get_json()["error"] == "Weight must be between 1 and 500 kg."


def test_the_weight_limit_message_is_translated_with_the_limit(logged_in_client):
    client, _ = logged_in_client
    resp = client.post(
        "/entries/field", json={"date": "2026-01-01", "weight": "501"}, headers={"Accept-Language": "nl"}
    )
    assert resp.get_json()["error"] == "Het gewicht moet tussen 1 en 500 kg liggen."
