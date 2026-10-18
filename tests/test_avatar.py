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

"""The avatar proxy: /avatar fetches the Gravatar on the server and keeps it in a disk cache.

Gravatar is never contacted: `urllib`'s opener in app/avatar.py is replaced by a stub that
records the URLs asked for and answers like Gravatar would.
"""

import io
import os
import time
import urllib.error
import urllib.request
from email.message import Message

import pytest

from app import avatar, db, mail
from app.cleanup import cleanup
from app.models import User
from app.utils import hash_email, utcnow
from tests.conftest import code_from, signup_and_verify

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 50
EMAIL = "someone@example.com"
HASH = hash_email(EMAIL)


class FakeResponse(io.BytesIO):
    def __init__(self, data, content_type):
        super().__init__(data)
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeGravatar:
    """Stands in for `avatar._opener`: answers with `answer` (bytes + type, 404, or an error)."""

    def __init__(self):
        self.urls = []
        self.answer = (PNG, "image/png")

    def open(self, request, timeout):
        self.urls.append(request.full_url)
        assert timeout == avatar.TIMEOUT_SECONDS
        if self.answer == 404:
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", Message(), None)
        if isinstance(self.answer, Exception):
            raise self.answer
        data, content_type = self.answer
        return FakeResponse(data, content_type)


@pytest.fixture
def gravatar(app, monkeypatch):
    app.config["GRAVATAR_ENABLED"] = True
    fake = FakeGravatar()
    monkeypatch.setattr(avatar, "_opener", fake)
    return fake


@pytest.fixture
def signed_in(client):
    signup_and_verify(client, EMAIL)
    return client


def cache_files(app):
    folder = app.config["AVATAR_CACHE_DIR"]
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


def age_files(app, seconds):
    """Pretend every cache file was written `seconds` ago."""
    folder = app.config["AVATAR_CACHE_DIR"]
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        then = os.path.getmtime(path) - seconds
        os.utime(path, (then, then))


# --- the route ------------------------------------------------------------------------------


def test_the_avatar_is_fetched_once_and_then_served_from_the_cache(app, gravatar, signed_in):
    first = signed_in.get("/avatar")
    assert first.status_code == 200
    assert first.mimetype == "image/png" and first.data == PNG
    assert gravatar.urls == [f"https://www.gravatar.com/avatar/{HASH}?s=40&d=404"]
    assert cache_files(app) == [f"{HASH}.png"]

    again = signed_in.get("/avatar")
    assert again.data == PNG
    assert len(gravatar.urls) == 1  # from the disk cache


def test_the_answer_may_be_cached_by_the_browser_only(gravatar, signed_in):
    response = signed_in.get("/avatar")
    assert response.headers["Cache-Control"] == f"private, max-age={avatar.CACHE_SECONDS}"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_a_stale_avatar_is_fetched_again(app, gravatar, signed_in):
    signed_in.get("/avatar")
    age_files(app, avatar.CACHE_SECONDS + 1)
    gravatar.answer = (JPEG, "image/jpeg")
    response = signed_in.get("/avatar")
    assert response.mimetype == "image/jpeg" and response.data == JPEG
    assert len(gravatar.urls) == 2
    assert cache_files(app) == [f"{HASH}.jpg"]  # the old .png is gone


def test_no_gravatar_gives_the_placeholder_and_is_remembered(app, gravatar, signed_in):
    gravatar.answer = 404
    response = signed_in.get("/avatar")
    assert response.mimetype == "image/svg+xml"
    assert b"<svg" in response.data
    assert response.headers["Cache-Control"] == "private, max-age=3600"
    assert cache_files(app) == [f"{HASH}.none"]
    signed_in.get("/avatar")
    assert len(gravatar.urls) == 1


@pytest.mark.parametrize(
    "answer",
    [
        urllib.error.URLError("timed out"),
        TimeoutError("timed out"),
        urllib.error.HTTPError("x", 500, "Server Error", Message(), None),
        urllib.error.HTTPError("x", 302, "Found", Message(), None),  # a redirect is not followed
        (b"<svg onload=alert(1)></svg>", "image/svg+xml"),
        (b"<html></html>", "text/html"),
        (b"not really a png", "image/png"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * avatar.MAX_BYTES, "image/png"),
    ],
)
def test_an_unusable_answer_gives_the_placeholder_and_a_retry_later(app, gravatar, signed_in, answer):
    gravatar.answer = answer
    response = signed_in.get("/avatar")
    assert response.mimetype == "image/svg+xml"
    signed_in.get("/avatar")
    assert len(gravatar.urls) == 1  # not again on every page load
    age_files(app, avatar.RETRY_SECONDS + 1)
    gravatar.answer = (PNG, "image/png")
    assert signed_in.get("/avatar").data == PNG


def test_an_old_avatar_is_kept_while_gravatar_cannot_be_reached(app, gravatar, signed_in):
    signed_in.get("/avatar")
    age_files(app, avatar.CACHE_SECONDS + 1)
    gravatar.answer = urllib.error.URLError("down")
    assert signed_in.get("/avatar").data == PNG
    signed_in.get("/avatar")
    assert len(gravatar.urls) == 2  # and it waits RETRY_SECONDS before asking again


def test_signed_out_visitors_get_the_placeholder_and_nothing_is_fetched(client, gravatar):
    response = client.get("/avatar")
    assert response.status_code == 200 and response.mimetype == "image/svg+xml"
    assert gravatar.urls == []


def test_with_gravatar_turned_off_nothing_is_fetched(app, gravatar, signed_in):
    app.config["GRAVATAR_ENABLED"] = False
    assert signed_in.get("/avatar").mimetype == "image/svg+xml"
    assert gravatar.urls == [] and cache_files(app) == []


def test_the_url_differs_per_account_so_a_browser_cache_is_not_shared(app, signed_in):
    html = signed_in.get("/").data.decode()
    assert f'src="/avatar?v={HASH[:8]}"' in html
    assert "gravatar.com" not in html.split("<main")[0]  # the header links no third party


def test_an_unwritable_cache_still_serves_the_avatar(app, gravatar, signed_in, tmp_path):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    app.config["AVATAR_CACHE_DIR"] = str(blocker / "avatars")
    assert signed_in.get("/avatar").data == PNG


# --- housekeeping ---------------------------------------------------------------------------


def test_removing_the_account_deletes_its_cached_avatar(app, gravatar, signed_in):
    signed_in.get("/avatar")
    assert cache_files(app) == [f"{HASH}.png"]
    assert signed_in.get("/settings/export").status_code == 200  # "Remove my data" needs a download
    with mail.record_messages() as outbox:
        signed_in.post("/settings/remove/send-code")
    code = code_from(outbox)
    signed_in.post("/settings/remove/confirm", data={"code": code})
    assert User.query.count() == 0
    assert cache_files(app) == []


def test_the_cleanup_removes_avatars_of_removed_and_long_inactive_accounts(app):
    folder = app.config["AVATAR_CACHE_DIR"]
    os.makedirs(folder)
    kept = User(email_hash=hash_email("a@example.com"))
    inactive = User(email_hash=hash_email("b@example.com"))
    db.session.add_all([kept, inactive])
    db.session.commit()
    gone = hash_email("gone@example.com")
    for name in (f"{kept.email_hash}.png", f"{inactive.email_hash}.none", f"{gone}.jpg", "README", ".tmp-x"):
        with open(os.path.join(folder, name), "wb") as f:
            f.write(b"x")
    month_ago = time.time() - avatar.KEEP_SECONDS - 60
    os.utime(os.path.join(folder, f"{inactive.email_hash}.none"), (month_ago, month_ago))

    planned = cleanup(now=utcnow(), dry_run=True)
    assert planned["avatars"] == 2 and len(cache_files(app)) == 5  # a dry run changes nothing
    assert cleanup(now=utcnow())["avatars"] == 2
    assert cache_files(app) == sorted([".tmp-x", "README", f"{kept.email_hash}.png"])


def test_the_cleanup_removes_a_temporary_file_left_for_an_hour(app):
    """A write that was interrupted leaves a .tmp- file; one still being written must stay."""
    folder = app.config["AVATAR_CACHE_DIR"]
    os.makedirs(folder)
    now = time.time()
    for name, age in ((".tmp-recent", avatar.TMP_KEEP_SECONDS - 60), (".tmp-left", avatar.TMP_KEEP_SECONDS + 60)):
        path = os.path.join(folder, name)
        with open(path, "wb") as f:
            f.write(b"x")
        os.utime(path, (now - age, now - age))
    assert avatar.TMP_KEEP_SECONDS == 3600
    assert avatar.prune(set(), now=now) == 1
    assert cache_files(app) == [".tmp-recent"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root may delete in a read-only folder")
def test_the_cleanup_counts_only_the_files_it_could_delete(app):
    folder = app.config["AVATAR_CACHE_DIR"]
    os.makedirs(folder)
    with open(os.path.join(folder, f"{hash_email('gone@example.com')}.png"), "wb") as f:
        f.write(b"x")
    os.chmod(folder, 0o500)  # no deleting in it
    try:
        assert avatar.prune(set(), dry_run=True) == 1
        assert avatar.prune(set()) == 0
    finally:
        os.chmod(folder, 0o700)
    assert len(cache_files(app)) == 1


def test_the_real_opener_does_not_follow_a_redirect():
    """A redirect could point anywhere; the opener must stop at it (the stub above skips this)."""
    import http.server
    import threading

    visited = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            visited.append(self.path)
            if self.path == "/avatar":
                self.send_response(302)
                self.send_header("Location", "/elsewhere")
            else:
                self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/avatar"
        with pytest.raises(urllib.error.HTTPError) as error:
            avatar._opener.open(urllib.request.Request(url), timeout=5)
        assert error.value.code == 302
        assert visited == ["/avatar"]
    finally:
        server.shutdown()
        server.server_close()
