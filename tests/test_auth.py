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

"""Tests for email sign-in: codes, expiry, attempt limits and sign-out."""

import re

import pytest

from app import db, mail
from app.models import User
from app.utils import hash_email


def _get_code(outbox):
    body = outbox[-1].body
    match = re.search(r"\b(\d{6})\b", body)
    assert match, f"no code found in email body: {body!r}"
    return match.group(1)


def test_signup_sends_code_and_creates_unverified_user(client, app):
    with mail.record_messages() as outbox:
        resp = client.post("/signup", data={"email": "New@Example.com"}, follow_redirects=True)
    assert resp.status_code == 200
    assert len(outbox) == 1
    assert outbox[0].recipients == ["new@example.com"]

    with app.app_context():
        user = User.query.filter_by(email_hash=hash_email("new@example.com")).first()
        assert user is not None
        assert not user.is_verified


def test_verify_with_correct_code_signs_in(client, app):
    with mail.record_messages() as outbox:
        client.post("/signup", data={"email": "a@example.com"})
    code = _get_code(outbox)

    resp = client.post("/verify", data={"code": code}, follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        user = User.query.filter_by(email_hash=hash_email("a@example.com")).first()
        assert user.is_verified

    with client.session_transaction() as sess:
        assert sess.get("user_id") == user.id
        assert "pending_email" not in sess


def test_a_used_code_cannot_sign_in_again(client, app):
    with mail.record_messages() as outbox:
        client.post("/signup", data={"email": "used@example.com"})
    code = _get_code(outbox)
    client.post("/verify", data={"code": code})

    with app.app_context():
        user = User.query.filter_by(email_hash=hash_email("used@example.com")).first()
        assert user.code_hash is None and user.code_expires_at is None
    # Someone who saw the code (another browser waiting on the same address) is too late.
    other = app.test_client()
    with other.session_transaction() as sess:
        sess["pending_email"] = "used@example.com"
    resp = other.post("/verify", data={"code": code}, follow_redirects=True)
    assert b"That code has expired" in resp.data
    with other.session_transaction() as sess:
        assert "user_id" not in sess


def test_verify_with_wrong_code_fails(client, app):
    with mail.record_messages():
        client.post("/signup", data={"email": "b@example.com"})

    resp = client.post("/verify", data={"code": "000000"}, follow_redirects=True)
    assert b"Incorrect code" in resp.data

    with app.app_context():
        user = User.query.filter_by(email_hash=hash_email("b@example.com")).first()
        assert not user.is_verified


def test_resend_code_issues_new_code(client, app):
    with mail.record_messages() as outbox:
        client.post("/signup", data={"email": "c@example.com"})
    first_code = _get_code(outbox)

    with mail.record_messages() as outbox2:
        client.post("/verify/resend", follow_redirects=True)
    second_code = _get_code(outbox2)

    resp = client.post("/verify", data={"code": first_code}, follow_redirects=True)
    assert b"Incorrect code" in resp.data or b"expired" in resp.data

    resp = client.post("/verify", data={"code": second_code}, follow_redirects=True)
    assert b"verified" in resp.data.lower()


def test_logout_clears_session(client, app):
    with mail.record_messages() as outbox:
        client.post("/signup", data={"email": "d@example.com"})
    code = _get_code(outbox)
    client.post("/verify", data={"code": code})

    client.post("/logout", follow_redirects=True)
    with client.session_transaction() as sess:
        assert "user_id" not in sess


def test_signin_code_is_valid_for_30_minutes(client, app):
    from datetime import timedelta

    from app.utils import utcnow

    with mail.record_messages() as outbox:
        client.post("/signup", data={"email": "ttl@example.com"})
    assert "30 minutes" in outbox[0].body
    with app.app_context():
        user = User.query.filter_by(email_hash=hash_email("ttl@example.com")).first()
        remaining = user.code_expires_at - utcnow()
        assert timedelta(minutes=29) < remaining <= timedelta(minutes=30)


def test_send_code_button_sits_next_to_the_email_input(client):
    html = client.get("/").data.decode()
    row = html[html.index('class="input-with-button"'):]
    assert row.index('name="email"') < row.index("Send code") < row.index("</div>")


def _flashes(client):
    with client.session_transaction() as sess:
        return [message for _category, message in sess.get("_flashes", [])]


def test_verify_without_a_sign_in_in_progress_asks_for_the_email(client):
    resp = client.get("/verify")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/signup")
    assert _flashes(client) == ["Enter your email to get a verification code."]


def test_resend_without_a_sign_in_in_progress_goes_to_the_email_form(client):
    with mail.record_messages() as outbox:
        resp = client.post("/verify/resend")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/signup")
    assert outbox == [] and _flashes(client) == []


@pytest.mark.parametrize("request_page", [lambda c: c.get("/verify"), lambda c: c.post("/verify/resend")],
                         ids=["verify", "resend"])
def test_a_sign_in_whose_account_is_gone_is_forgotten(client, app, request_page):
    """The daily cleanup can remove the unverified account while its code page is open."""
    with mail.record_messages():
        client.post("/signup", data={"email": "gone@example.com"})
    with app.app_context():
        db.session.delete(User.query.filter_by(email_hash=hash_email("gone@example.com")).one())
        db.session.commit()
    client.get("/signup")  # take the "code sent" flash out of the session
    with mail.record_messages() as outbox:
        resp = request_page(client)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/signup")
    assert outbox == [] and _flashes(client) == []  # no account was made again, nothing was sent
    with client.session_transaction() as sess:
        assert "pending_email" not in sess
    with app.app_context():
        assert User.query.filter_by(email_hash=hash_email("gone@example.com")).first() is None


@pytest.mark.parametrize("cooldown", [False, True], ids=["sent", "cooldown"])
def test_signup_remembers_the_address_and_says_the_code_was_sent(client, app, cooldown):
    """Also when a code was sent less than a minute ago and no new one goes out."""
    if cooldown:
        app.config["RATELIMIT_MODE"] = "enforce"
        with mail.record_messages():
            client.post("/signup", data={"email": "twice@example.com"})
        client.get("/verify")  # take the first flash out of the session
    with mail.record_messages() as outbox:
        resp = client.post("/signup", data={"email": "twice@example.com"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/verify")
    assert len(outbox) == (0 if cooldown else 1)
    assert _flashes(client) == ["We sent a verification code to twice@example.com."]
    with client.session_transaction() as sess:
        assert sess["pending_email"] == "twice@example.com"
