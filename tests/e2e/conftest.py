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

"""Fixtures for the browser tests: the app served on a free port, a signed-in browser.

The tests drive a real Chromium through Playwright (`page` comes from pytest-playwright). Each
test gets its own app on a temporary database file, never instance/weight.db. Signing in sets
the session cookie directly, so no mail or code is involved. Every test fails if the page logs
a JavaScript error or a Content-Security-Policy violation.

Without Playwright installed (the normal test job) this folder is not collected at all.
"""

import importlib.util
import threading
import time
from dataclasses import dataclass
from datetime import date, timedelta

import pytest
from werkzeug.serving import make_server

from app import create_app, db
from app.models import User, WeightEntry
from app.utils import hash_email, utcnow

if importlib.util.find_spec("playwright") is None:
    collect_ignore_glob = ["test_*.py"]

EMAIL = "browser@example.com"


@dataclass
class LiveServer:
    app: object
    url: str

    def add_user(self, email=EMAIL, weights=None, notes=None, **columns):
        """A verified user; `weights` and `notes` map days before today to a weight / note."""
        with self.app.app_context():
            user = User(email_hash=hash_email(email), verified_at=utcnow(), **columns)
            db.session.add(user)
            db.session.commit()
            for days_ago, weight in (weights or {}).items():
                note = (notes or {}).get(days_ago)
                db.session.add(WeightEntry(user_id=user.id, entry_date=date.today() - timedelta(days=days_ago),
                                           weight=weight, note=note))
            db.session.commit()
            return user.id

    def entry(self, user_id, days_ago=0):
        """(weight, note) of a day, or None when the day has no entry."""
        with self.app.app_context():
            day = date.today() - timedelta(days=days_ago)
            found = WeightEntry.query.filter_by(user_id=user_id, entry_date=day).first()
            return (found.weight, found.note) if found else None

    def user(self, user_id):
        with self.app.app_context():
            user = db.session.get(User, user_id)
            db.session.expunge(user)
            return user

    def sign_in(self, context, user_id, email=EMAIL):
        """Give the browser a signed session cookie, as after entering the emailed code."""
        with self.app.app_context():
            session_version = db.session.get(User, user_id).session_version
        serializer = self.app.session_interface.get_signing_serializer(self.app)
        cookie = serializer.dumps({
            "user_id": user_id, "email": email, "session_version": session_version,
            "login_at": int(time.time()), "_permanent": True,
        })
        context.add_cookies([{"name": self.app.config["SESSION_COOKIE_NAME"], "value": cookie, "url": self.url}])


@pytest.fixture
def live_server(tmp_path):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "browser-tests-only-" + "x" * 32,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'kilo.db'}",
        "AVATAR_CACHE_DIR": str(tmp_path / "avatars"),
        "GRAVATAR_ENABLED": False,
        "CHECK_EMAIL_MX": False,
        "RATELIMIT_MODE": "off",
    })
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield LiveServer(app, f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
        thread.join(5)
        with app.app_context():
            db.engine.dispose()


@pytest.fixture(autouse=True)
def no_page_errors(request, page):
    """Fail the test on any uncaught JavaScript error, CSP violation or failed request the page
    reports. A test marked `expected_http_errors` may see failed requests (Chromium logs each one,
    a 400 included); JavaScript errors and CSP violations still fail it."""
    allow_http_errors = request.node.get_closest_marker("expected_http_errors") is not None
    problems = []

    def on_console(message):
        if message.type != "error":
            return
        if allow_http_errors and message.text.startswith("Failed to load resource"):
            return
        problems.append(f"console: {message.text}")

    page.on("pageerror", lambda error: problems.append(f"page error: {error}"))
    page.on("console", on_console)
    yield
    assert problems == []
