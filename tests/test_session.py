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

"""Tests for staying signed in: the persistent session cookie, its 90-day cap, the cookie flags and the key warning."""

import logging
import re
import time
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import pytest

from app import create_app, mail
from app.models import User
from tests.conftest import code_from

DAY = 24 * 3600


def sign_in(client, email="user@example.com"):
    """Sign up and enter the emailed code; returns the response to the code form."""
    with mail.record_messages() as outbox:
        client.post("/signup", data={"email": email})
    code = code_from(outbox)
    return client.post("/verify", data={"code": code})


def session_cookie(response):
    """The Set-Cookie header of the session cookie in a response, or None."""
    for header in response.headers.getlist("Set-Cookie"):
        if header.startswith("session="):
            return header
    return None


def expiry(cookie):
    value = re.search(r"Expires=([^;]+)", cookie).group(1)
    return parsedate_to_datetime(value)


def in_days(days):
    return datetime.now(UTC) + timedelta(days=days)


# ---- signing in --------------------------------------------------------------


def test_signing_in_gives_a_cookie_that_outlives_the_browser_window(client):
    cookie = session_cookie(sign_in(client))
    assert "Expires=" in cookie  # without it the browser drops the cookie when it closes
    assert abs((expiry(cookie) - in_days(30)).total_seconds()) < 120  # 30 days from now


def test_the_cookie_flags(client):
    cookie = session_cookie(sign_in(client))
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Path=/" in cookie
    assert "Secure" not in cookie  # off unless SESSION_COOKIE_SECURE=1 (plain-http development)


def test_the_cookie_is_only_https_when_configured(client, app):
    app.config["SESSION_COOKIE_SECURE"] = True
    assert "Secure" in session_cookie(sign_in(client))


def test_the_sign_in_time_is_recorded(client):
    sign_in(client)
    with client.session_transaction() as session:
        assert abs(session["login_at"] - time.time()) < 60
        assert session.permanent


def test_visitors_who_are_not_signed_in_keep_a_plain_session_cookie(client):
    cookie = session_cookie(client.get("/"))
    assert cookie is not None and "Expires=" not in cookie


# ---- staying signed in -------------------------------------------------------


def test_every_visit_pushes_the_expiry_forward(logged_in_client):
    client, _user_id = logged_in_client
    cookie = session_cookie(client.get("/settings"))
    assert cookie is not None, "the cookie is re-sent on every visit, which renews its expiry"
    assert abs((expiry(cookie) - in_days(30)).total_seconds()) < 120


def test_signed_in_state_survives_a_new_browser_window(logged_in_client, app):
    """A new window keeps only cookies that have an expiry date: copy exactly those to a new client."""
    client, _user_id = logged_in_client
    cookie = session_cookie(client.get("/"))
    assert "Expires=" in cookie
    value = re.match(r"session=([^;]*)", cookie).group(1)

    reopened = app.test_client()
    reopened.set_cookie("session", value)
    assert reopened.get("/settings").status_code == 200  # still signed in


def test_sessions_from_before_the_change_are_adopted(client):
    user = User(email_hash="a" * 64)
    from app import db

    db.session.add(user)
    db.session.commit()
    with client.session_transaction() as session:  # an old-style, browser-session-only login
        session["user_id"] = user.id
        session["email"] = "old@example.com"
    cookie = session_cookie(client.get("/settings"))
    assert "Expires=" in cookie
    with client.session_transaction() as session:
        assert session.permanent and abs(session["login_at"] - time.time()) < 60


# ---- the 90-day cap ----------------------------------------------------------


def test_after_90_days_the_user_must_sign_in_again(logged_in_client):
    client, _user_id = logged_in_client
    with client.session_transaction() as session:
        session["login_at"] = int(time.time()) - 91 * DAY
    response = client.get("/settings")
    assert response.status_code == 302  # sent back to ask for the address
    with client.session_transaction() as session:
        assert "user_id" not in session and "email" not in session and "login_at" not in session


def test_just_before_90_days_the_user_is_still_signed_in(logged_in_client):
    client, _user_id = logged_in_client
    with client.session_transaction() as session:
        session["login_at"] = int(time.time()) - 89 * DAY
    assert client.get("/settings").status_code == 200


def test_activity_does_not_extend_the_cap(logged_in_client):
    client, _user_id = logged_in_client
    with client.session_transaction() as session:
        session["login_at"] = int(time.time()) - 89 * DAY
    client.get("/")  # a visit renews the 30 days, not the 90
    with client.session_transaction() as session:
        assert time.time() - session["login_at"] > 88 * DAY


def test_signing_in_again_starts_a_new_90_days(client):
    sign_in(client)
    with client.session_transaction() as session:
        session["login_at"] = int(time.time()) - 91 * DAY
    client.get("/settings")  # ended
    sign_in(client)
    with client.session_transaction() as session:
        assert abs(session["login_at"] - time.time()) < 60


# ---- signing out -------------------------------------------------------------


def test_signing_out_forgets_everything(logged_in_client):
    client, _user_id = logged_in_client
    with client.session_transaction() as session:
        session["data_exported"] = True
    response = client.post("/logout")
    assert response.status_code == 302
    with client.session_transaction() as session:
        for key in ("user_id", "email", "login_at", "data_exported"):
            assert key not in session, key
        assert not session.permanent
    cookie = session_cookie(response)
    assert cookie is None or "Expires=" not in cookie  # no persistent cookie is left behind


def test_the_export_flag_does_not_carry_over_to_the_next_user(client):
    sign_in(client, "first@example.com")
    with client.session_transaction() as session:
        session["data_exported"] = True
    client.post("/logout")
    sign_in(client, "second@example.com")
    with client.session_transaction() as session:
        assert "data_exported" not in session


# ---- the signing key ---------------------------------------------------------


def make_app(**config):
    return create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", **config})


@pytest.mark.parametrize("key", ["dev", "short"])
def test_a_missing_or_short_secret_key_is_warned_about(caplog, key):
    with caplog.at_level(logging.WARNING):
        make_app(SECRET_KEY=key)
    assert "SECRET_KEY" in caplog.text


def test_a_proper_secret_key_is_not(caplog):
    with caplog.at_level(logging.WARNING):
        make_app(SECRET_KEY="0123456789abcdef0123456789abcdef")
    assert "SECRET_KEY" not in caplog.text


def test_the_test_configuration_is_not_nagged(caplog):
    with caplog.at_level(logging.WARNING):
        make_app(SECRET_KEY="dev", TESTING=True)
    assert "SECRET_KEY" not in caplog.text


def test_the_defaults():
    app = make_app(TESTING=True)
    assert app.config["PERMANENT_SESSION_LIFETIME"] == timedelta(days=30)
    assert app.config["SESSION_ABSOLUTE_LIFETIME"] == timedelta(days=90)
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True


# --- "Sign out everywhere" -----------------------------------------------------------------------


def signed_in(client):
    return 'id="account-menu"' in client.get("/").get_data(as_text=True)


@pytest.fixture
def two_devices(app):
    """The same user signed in on a laptop and a phone."""
    laptop, phone = app.test_client(), app.test_client()
    sign_in(laptop)
    sign_in(phone)
    assert signed_in(laptop) and signed_in(phone)
    return laptop, phone


def test_signing_out_everywhere_ends_the_other_sessions_but_not_this_one(two_devices):
    laptop, phone = two_devices
    response = laptop.post("/settings/sign-out-everywhere")
    assert response.status_code == 302 and response.location == "/settings"
    assert "You are now signed out on all other browsers and devices." in laptop.get("/settings").get_data(
        as_text=True
    )
    assert signed_in(laptop)
    assert not signed_in(phone)
    with phone.session_transaction() as sess:
        assert "user_id" not in sess and "email" not in sess  # the old cookie is emptied


def test_a_copied_cookie_stops_working_too(app, two_devices):
    laptop, _phone = two_devices
    with laptop.session_transaction() as sess:
        copied = dict(sess)
    thief = app.test_client()
    with thief.session_transaction() as sess:
        sess.update(copied)
    assert signed_in(thief)
    laptop.post("/settings/sign-out-everywhere")
    assert not signed_in(thief)


def test_a_cookie_from_before_the_feature_counts_as_number_0(app, two_devices):
    laptop, phone = two_devices
    with phone.session_transaction() as sess:
        del sess["session_version"]  # signed in before session_version existed
    assert signed_in(phone)  # still fine until "sign out everywhere" is used
    laptop.post("/settings/sign-out-everywhere")
    assert not signed_in(phone)  # not adopted as the new number


def test_signing_in_again_afterwards_works(two_devices):
    laptop, phone = two_devices
    laptop.post("/settings/sign-out-everywhere")
    sign_in(phone)
    assert signed_in(phone) and signed_in(laptop)


def test_it_can_be_used_more_than_once(two_devices):
    laptop, phone = two_devices
    laptop.post("/settings/sign-out-everywhere")
    sign_in(phone)
    phone.post("/settings/sign-out-everywhere")
    assert signed_in(phone) and not signed_in(laptop)
    assert User.query.one().session_version == 2


def test_other_users_stay_signed_in(app, two_devices):
    laptop, _phone = two_devices
    other = app.test_client()
    sign_in(other, "other@example.com")
    laptop.post("/settings/sign-out-everywhere")
    assert signed_in(other)


def test_it_needs_a_post_a_sign_in_and_a_csrf_token(app, client, two_devices):
    laptop, phone = two_devices
    assert laptop.get("/settings/sign-out-everywhere").status_code in (302, 405)
    assert client.post("/settings/sign-out-everywhere").status_code == 302  # signed out: to the start page
    app.config["WTF_CSRF_ENABLED"] = True
    laptop.post("/settings/sign-out-everywhere")  # no token: refused, nothing changes
    assert signed_in(phone)
    assert User.query.one().session_version == 0


def test_the_settings_page_offers_it(logged_in_client):
    client, _user_id = logged_in_client
    html = client.get("/settings").get_data(as_text=True)
    assert 'action="/settings/sign-out-everywhere"' in html
    assert "Sign out on other devices" in html
