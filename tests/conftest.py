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

import os
import re
from pathlib import Path

import pytest
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po

from app import create_app, db, mail
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


def signup_and_verify(client, email):
    with mail.record_messages() as outbox:
        client.post("/signup", data={"email": email})
    match = re.search(r"\b(\d{6})\b", outbox[-1].body)
    client.post("/verify", data={"code": match.group(1)})


@pytest.fixture
def logged_in_client(client, app):
    signup_and_verify(client, "user@example.com")
    with app.app_context():
        user = User.query.first()
        user_id = user.id
    return client, user_id
