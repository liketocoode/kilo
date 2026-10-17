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

"""Tests for the "Remove my data" flow."""


from datetime import timedelta

from app import codes, db, mail
from app.models import User, WeightEntry
from app.utils import DELETE_CODE_TTL_MINUTES, utcnow
from tests.conftest import code_from, flashes


def _request_code(client):
    with mail.record_messages() as outbox:
        client.post("/settings/remove/send-code")
    return outbox


def _add_entry(client):
    client.post("/entries/field", json={"date": "2026-01-01", "weight": 80.0})


def test_code_is_refused_until_data_was_downloaded(logged_in_client):
    client, _ = logged_in_client
    outbox = _request_code(client)
    assert outbox == []


def test_removal_flow_deletes_account_and_entries(logged_in_client, app):
    client, user_id = logged_in_client
    _add_entry(client)
    assert client.get("/settings/export").status_code == 200

    code = code_from(_request_code(client))
    resp = client.post("/settings/remove/confirm", data={"code": code})

    assert resp.status_code == 302
    assert db.session.get(User, user_id) is None
    assert WeightEntry.query.filter_by(user_id=user_id).count() == 0
    # signed out afterwards
    assert b"Sign in" in client.get("/").data or b"email" in client.get("/").data.lower()


def test_wrong_code_does_not_delete_and_attempts_are_limited(logged_in_client):
    client, user_id = logged_in_client
    client.get("/settings/export")
    code = code_from(_request_code(client))
    wrong = "000000" if code != "000000" else "111111"

    for _ in range(User.MAX_CODE_ATTEMPTS):
        client.post("/settings/remove/confirm", data={"code": wrong})
    # even the right code is now refused
    client.post("/settings/remove/confirm", data={"code": code})
    assert db.session.get(User, user_id) is not None


def test_removal_needs_export_in_this_session(logged_in_client, app):
    client, user_id = logged_in_client
    client.get("/settings/export")
    code = code_from(_request_code(client))
    with client.session_transaction() as s:
        s.pop("data_exported")
    client.post("/settings/remove/confirm", data={"code": code})
    assert db.session.get(User, user_id) is not None


def test_remove_pages_require_login(client):
    assert client.get("/settings/remove").status_code == 302
    assert client.post("/settings/remove/send-code").status_code == 302
    assert client.post("/settings/remove/confirm", data={"code": "123456"}).status_code == 302


def test_settings_page_links_to_remove_data(logged_in_client):
    client, _ = logged_in_client
    html = client.get("/settings").data.decode()
    assert "<title>Settings — Kilo Tracker</title>" in html
    assert 'href="/settings/remove"' in html


def test_without_the_address_in_the_session_no_code_is_sent(logged_in_client):
    """The address lives only in the session: without it there is nowhere to send the code."""
    client, _ = logged_in_client
    client.get("/settings/export")
    with client.session_transaction() as sess:
        del sess["email"]
    outbox = _request_code(client)
    assert outbox == []
    assert "Sign in again to confirm this request." in flashes(client.get("/settings/remove"), "error")


def test_a_malformed_code_is_refused_and_deletes_nothing(logged_in_client, app):
    client, user_id = logged_in_client
    client.get("/settings/export")
    _request_code(client)
    resp = client.post("/settings/remove/confirm", data={"code": "12"}, follow_redirects=True)
    assert "Enter the 6-digit code." in flashes(resp, "error")
    assert db.session.get(User, user_id) is not None
    assert db.session.get(User, user_id).delete_code_attempts == 0  # not even counted as a try


def test_an_expired_code_deletes_nothing(logged_in_client, app, monkeypatch):
    client, user_id = logged_in_client
    client.get("/settings/export")
    code = code_from(_request_code(client))
    later = utcnow() + timedelta(minutes=DELETE_CODE_TTL_MINUTES, seconds=1)
    monkeypatch.setattr(codes, "utcnow", lambda: later)
    resp = client.post("/settings/remove/confirm", data={"code": code}, follow_redirects=True)
    assert "That code has expired. Request a new one." in flashes(resp, "error")
    assert db.session.get(User, user_id) is not None
