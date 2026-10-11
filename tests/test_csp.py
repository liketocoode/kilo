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

"""The Content-Security-Policy (app/csp.py): the header on every response, and a nonce on every
inline script of every page, so the policy blocks nothing of Kilo's own.

Whether a browser really blocks nothing was checked in Chromium; these tests keep the pages and
the header in step.
"""

import re

import pytest

from app import db, errors
from app.models import User, WeightEntry
from tests.conftest import signup_and_verify

BROWSER = {"Accept": "text/html"}
SCRIPT_TAG = re.compile(r"<script\b([^>]*)>", re.IGNORECASE)


def rules(response):
    """The policy as {directive: value}."""
    out = {}
    for part in response.headers["Content-Security-Policy"].split(";"):
        name, _sep, value = part.strip().partition(" ")
        out[name] = value
    return out


def nonce_of(response):
    return re.search(r"'nonce-([^']+)'", response.headers["Content-Security-Policy"]).group(1)


def assert_every_inline_script_has_the_nonce(response):
    """Each <script> either loads a file from Kilo (src="/static/...") or carries this response's
    nonce, or is a JSON data block (which never runs)."""
    html = response.get_data(as_text=True)
    nonce = nonce_of(response)
    tags = SCRIPT_TAG.findall(html)
    assert tags, "the page has no scripts at all"
    for attrs in tags:
        if 'type="application/json"' in attrs:
            continue
        src = re.search(r'src="([^"]+)"', attrs)
        if src:
            assert src.group(1).startswith("/static/"), attrs
        else:
            assert f'nonce="{nonce}"' in attrs, attrs


@pytest.fixture
def signed_in(client):
    signup_and_verify(client, "someone@example.com")
    user = User.query.one()
    db.session.add(WeightEntry(user_id=user.id, entry_date=__import__("datetime").date(2026, 10, 1), weight=80))
    user.public_id, user.public_enabled = "mine", True
    db.session.commit()
    return client


def test_the_policy(client):
    policy = rules(client.get("/"))
    assert policy["default-src"] == "'self'"
    assert re.fullmatch(r"'self' 'nonce-[A-Za-z0-9_-]{24}'", policy["script-src"])
    assert policy["style-src"] == "'self'"
    assert policy["img-src"] == "'self'"
    assert policy["connect-src"] == "'self'"
    assert policy["form-action"] == "'self'"
    assert policy["object-src"] == "'none'"
    assert policy["base-uri"] == "'none'"
    assert policy["frame-ancestors"] == "'none'"
    assert "'unsafe-inline'" not in str(policy) and "'unsafe-eval'" not in str(policy)


def test_the_nonce_is_new_for_every_response(client):
    nonces = {nonce_of(client.get("/")) for _ in range(5)}
    assert len(nonces) == 5


@pytest.mark.parametrize(
    "path", ["/", "/settings", "/settings/remove", "/about", "/public/mine"]
)
def test_every_inline_script_of_a_signed_in_page_carries_the_nonce(signed_in, path):
    response = signed_in.get(path)
    assert response.status_code == 200
    assert_every_inline_script_has_the_nonce(response)


def test_the_pages_of_a_signed_out_visitor_carry_it_too(client):
    for path in ("/", "/about", "/public/nobody"):
        assert_every_inline_script_has_the_nonce(client.get(path))
    client.post("/signup", data={"email": "new@example.com"})
    assert_every_inline_script_has_the_nonce(client.get("/verify"))


def test_the_error_page_carries_it(app):
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.route("/boom")
    def boom():
        raise RuntimeError("test")

    response = app.test_client().get("/boom", headers=BROWSER)
    assert response.status_code == 500
    assert_every_inline_script_has_the_nonce(response)


def test_the_public_chart_keeps_the_policy_but_may_be_framed(signed_in):
    policy = rules(signed_in.get("/public/mine"))
    assert "frame-ancestors" not in policy
    assert policy["script-src"].startswith("'self' 'nonce-")


def test_no_template_has_an_inline_style_or_event_handler(app):
    """style-src 'self' would block them; the policy deliberately has no 'unsafe-inline'."""
    import pathlib

    for path in pathlib.Path(app.root_path, "templates").glob("*.html"):
        html = path.read_text()
        # The iframe code in the settings is shown as text (&lt;iframe ... style=...&gt;), not used.
        html = re.sub(r"&lt;iframe.*?&gt;", "", html)
        assert not re.search(r"<[a-z][^>]*\sstyle=", html, re.IGNORECASE), path.name
        assert not re.search(r"<[a-z][^>]*\son[a-z]+=", html, re.IGNORECASE), path.name
        assert "<style" not in html, path.name


def test_the_fallback_error_page_has_no_inline_style():
    assert "style=" not in errors.FALLBACK_500 and "<script" not in errors.FALLBACK_500
