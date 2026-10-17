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

"""Shared pytest fixtures: an app on an in-memory database, a client and a signed-in client."""

import functools
import html
import json
import os
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po
from werkzeug.security import generate_password_hash

from app import codes, create_app, db, mail
from app.models import User

# The project's top folder, for tests that read its files.
ROOT = Path(__file__).resolve().parent.parent
TRANSLATIONS_DIR = str(ROOT / "translations")


@pytest.fixture(scope="session", autouse=True)
def compiled_catalogs():
    """Compile translations/*/LC_MESSAGES/messages.po to .mo (gitignored, built at deploy time)
    so the language tests also work in a fresh checkout."""
    for lang in sorted(os.listdir(TRANSLATIONS_DIR)):
        base = os.path.join(TRANSLATIONS_DIR, lang, "LC_MESSAGES", "messages")
        if not os.path.exists(base + ".po"):
            continue
        if os.path.exists(base + ".mo") and os.path.getmtime(base + ".mo") >= os.path.getmtime(base + ".po"):
            continue
        with open(base + ".po", "rb") as po_file:
            catalog = read_po(po_file, locale=lang)
        with open(base + ".mo", "wb") as mo_file:
            write_mo(mo_file, catalog)


@pytest.fixture(autouse=True)
def fast_code_hashes(request, monkeypatch):
    """Hash emailed codes with a cheap method: production's scrypt costs about 0.1 s per hash and
    per check, which was two thirds of the suite's time. Checking a code still works, because a
    hash names its own method. A test marked `real_hash` keeps scrypt."""
    if request.node.get_closest_marker("real_hash"):
        return
    monkeypatch.setattr(
        codes, "generate_password_hash", functools.partial(generate_password_hash, method="pbkdf2:sha256:1")
    )


@pytest.fixture
def app(tmp_path):
    app = create_app(
        {
            # Never contact Gravatar or write to the real instance folder; tests/test_avatar.py
            # turns fetching on with a stubbed download.
            "GRAVATAR_ENABLED": False,
            "AVATAR_CACHE_DIR": str(tmp_path / "avatars"),
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "WTF_CSRF_ENABLED": False,
            # No real DNS in tests; test_mx.py turns this on with a stubbed resolver.
            "CHECK_EMAIL_MX": False,
            # Off in the shared fixtures, like CSRF: most tests sign the same address up again and
            # again. tests/test_ratelimit.py turns it on.
            "RATELIMIT_MODE": "off",
        }
    )

    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


# A visitor's address as the proxy reports it (X-Real-IP), from the documentation range.
VISITOR_IP = "203.0.113.9"


def code_from(outbox):
    """The 6-digit code in the newest mail."""
    body = outbox[-1].body
    match = re.search(r"\b(\d{6})\b", body)
    assert match, f"no code found in email body: {body!r}"
    return match.group(1)


def sign_up(client, email, ip=VISITOR_IP, lang=None):
    """POST /signup as a visitor behind the proxy (no address: none known); returns
    (response after the redirect, mails sent)."""
    headers = {"X-Real-IP": ip} if ip else {}
    if lang:
        headers["Accept-Language"] = lang
    with mail.record_messages() as outbox:
        response = client.post("/signup", data={"email": email}, headers=headers, follow_redirects=True)
    return response, outbox


def flashes(page, kind="success|error"):
    """The flash messages of `kind` on a page (a response or its HTML), as the visitor reads them."""
    if not isinstance(page, str):
        page = page.get_data(as_text=True)
    return [html.unescape(text) for text in re.findall(rf'class="flash flash-(?:{kind})">([^<]+)<', page)]


def script_value(html, name):
    """The JSON value a `let name = ...;` or `const name = ...;` line of the page script holds.

    Reads the value, not how the script is written: a renamed helper or a reformatted line does
    not break the tests that use it.
    """
    return json.loads(re.search(rf"(?:let|const) {name} = (.*);\n", html).group(1))


def input_attrs(page, name):
    """The attributes of the <input name="..."> on a page, as a dict; a valueless attribute such
    as `checked` maps to None. Does not depend on the order WTForms writes them in."""

    class Finder(HTMLParser):
        found = None

        def handle_starttag(self, tag, attrs):
            if tag == "input" and dict(attrs).get("name") == name and self.found is None:
                self.found = dict(attrs)

    finder = Finder()
    finder.feed(page if isinstance(page, str) else page.get_data(as_text=True))
    assert finder.found is not None, f'no <input name="{name}"> on the page'
    return finder.found


def signup_and_verify(client, email):
    with mail.record_messages() as outbox:
        client.post("/signup", data={"email": email})
    client.post("/verify", data={"code": code_from(outbox)})


@pytest.fixture
def logged_in_client(client, app):
    signup_and_verify(client, "user@example.com")
    with app.app_context():
        user = User.query.first()
        user_id = user.id
    return client, user_id
