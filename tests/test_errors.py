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

"""Tests for the friendly error handling in app/errors.py: CSRF failures, missing pages, uploads
that are too large and unexpected errors."""

import html as html_lib
import io
import re

import pytest

from app import db, errors
from app.models import User, WeightEntry
from tests.conftest import flashes, signup_and_verify

BROWSER = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
EXPIRED = "Your session has expired and nothing was changed. Please try again."
EXPIRED_JSON = "Your session has expired. Reload the page and try again."
NOT_AVAILABLE = "That page isn't available, so you were taken to the start page."


@pytest.fixture
def csrf_on(app):
    """Turn CSRF protection on (the shared fixtures run with it off)."""
    app.config["WTF_CSRF_ENABLED"] = True
    return app


def csrf_token(client):
    html = client.get("/").get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


# ---- CSRF: forms ------------------------------------------------------------


def test_a_form_without_a_token_goes_back_with_a_message(client, csrf_on):
    response = client.post(
        "/signup", data={"email": "a@example.com"}, headers={"Referer": "http://localhost/signup?lang=nl"}
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/signup?lang=nl"
    assert EXPIRED in flashes(client.get("/signup").get_data(as_text=True), "error")


def test_nothing_is_changed_when_the_token_is_refused(client, csrf_on):
    from app.models import User

    client.post("/signup", data={"email": "a@example.com"})
    assert User.query.count() == 0


def test_an_expired_token_gets_the_same_friendly_answer(client, csrf_on):
    token = csrf_token(client)
    csrf_on.config["WTF_CSRF_TIME_LIMIT"] = -1  # every token is now too old
    response = client.post("/signup", data={"email": "a@example.com", "csrf_token": token})
    assert response.status_code == 302
    assert EXPIRED in flashes(client.get("/").get_data(as_text=True), "error")


def test_a_valid_token_still_works(client, csrf_on):
    token = csrf_token(client)
    response = client.post("/signup", data={"email": "a@example.com", "csrf_token": token})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/verify")


@pytest.mark.parametrize(
    "referrer",
    [
        None,
        "https://evil.example/phish",
        "http://localhost.evil.example/x",  # host that merely starts like ours
        "http://localhost//evil.example",  # path that looks protocol-relative
        "javascript:alert(1)",
        "not a url",
    ],
)
def test_the_redirect_never_leaves_the_site(client, csrf_on, referrer):
    headers = {"Referer": referrer} if referrer else {}
    response = client.post("/signup", data={"email": "a@example.com"}, headers=headers)
    assert response.status_code == 302
    assert response.headers["Location"] == "/"


@pytest.mark.parametrize(
    "lang, text",
    [
        ("nl", "Je sessie is verlopen en er is niets gewijzigd. Probeer het opnieuw."),
        ("de", "Deine Sitzung ist abgelaufen und es wurde nichts geändert. Bitte versuche es erneut."),
        ("pl", "Twoja sesja wygasła i nic nie zostało zmienione. Spróbuj ponownie."),
    ],
)
def test_the_form_message_is_in_the_visitors_language(client, csrf_on, lang, text):
    client.environ_base["HTTP_ACCEPT_LANGUAGE"] = lang
    client.post("/signup", data={"email": "a@example.com"})
    assert text in flashes(client.get("/").get_data(as_text=True), "error")


# ---- CSRF: the dashboard's background saves ---------------------------------


def test_a_refused_background_save_answers_with_json_and_a_code(logged_in_client, csrf_on):
    client, _user_id = logged_in_client
    response = client.post("/entries/field", json={"date": "2026-01-01", "weight": "80"})
    assert response.status_code == 400
    assert response.get_json() == {"error": EXPIRED_JSON, "code": "csrf_expired"}
    assert WeightEntry.query.count() == 0


def test_a_background_save_with_a_valid_token_works(logged_in_client, csrf_on):
    client, _user_id = logged_in_client
    token = csrf_token(client)
    response = client.post(
        "/entries/field",
        json={"date": "2026-01-01", "weight": "80", "confirm": True},
        headers={"X-CSRFToken": token},
    )
    assert response.status_code == 200
    assert WeightEntry.query.count() == 1


def test_an_expired_token_on_a_background_save_is_reported_the_same_way(logged_in_client, csrf_on):
    client, _user_id = logged_in_client
    token = csrf_token(client)
    csrf_on.config["WTF_CSRF_TIME_LIMIT"] = -1
    response = client.post(
        "/entries/field", json={"date": "2026-01-01", "weight": "80"}, headers={"X-CSRFToken": token}
    )
    assert response.status_code == 400
    assert response.get_json()["code"] == "csrf_expired"


def test_the_json_message_is_translated(logged_in_client, csrf_on):
    client, _user_id = logged_in_client
    response = client.post(
        "/entries/field",
        json={"date": "2026-01-01", "weight": "80"},
        headers={"Accept-Language": "nl"},
    )
    assert response.get_json()["error"] == "Je sessie is verlopen. Laad de pagina opnieuw en probeer het nog eens."


def test_the_dashboard_has_a_session_expired_dialog_and_uses_it(logged_in_client):
    client, _user_id = logged_in_client
    html = client.get("/", headers={"Accept-Language": "de"}).get_data(as_text=True)
    assert 'id="session-expired-prompt"' in html
    assert "Sitzung abgelaufen" in html and "Seite neu laden" in html
    # title, text and button are all rendered by the page, so they always match its language
    assert "Deine Sitzung ist abgelaufen. Lade die Seite neu und versuche es erneut." in html
    # Checked once, in saveDay, which both the row save and the "today" prompt go through.
    assert html.count("data.code === 'csrf_expired'") == 1
    assert html.count("await saveDay(") == 2
    # Every JSON POST of the page (saves, range, dark mode) sends the token through postJson.
    assert html.count("X-CSRFToken") == 1
    assert "location.reload()" in html


# ---- missing pages ----------------------------------------------------------


@pytest.mark.parametrize("path", ["/nope", "/nope/deeper", "/settings/nope", "/about/x", "/entries"])
def test_a_missing_page_sends_a_browser_to_the_start_page(client, path):
    response = client.get(path, headers=BROWSER)
    assert response.status_code == 302
    assert response.headers["Location"] == "/"
    assert NOT_AVAILABLE in flashes(client.get("/").get_data(as_text=True), "error")


def test_typing_a_post_only_address_gets_the_same_treatment(client):
    for path in ("/logout", "/verify/resend", "/chart-range"):
        response = client.get(path, headers=BROWSER)
        assert response.status_code == 302, path
        assert response.headers["Location"] == "/"


def test_signed_out_visitors_land_on_the_sign_in_form(client):
    html = client.get("/nope", headers=BROWSER, follow_redirects=True).get_data(as_text=True)
    assert NOT_AVAILABLE in flashes(html, "error")
    assert 'action="/signup"' in html


def test_signed_in_visitors_land_on_their_dashboard(logged_in_client):
    client, _user_id = logged_in_client
    html = client.get("/nope", headers=BROWSER, follow_redirects=True).get_data(as_text=True)
    assert NOT_AVAILABLE in flashes(html, "error")
    assert 'id="weight-chart"' in html


def test_the_missing_page_message_is_translated(client):
    client.environ_base["HTTP_ACCEPT_LANGUAGE"] = "nl"
    html = client.get("/nope", headers=BROWSER, follow_redirects=True).get_data(as_text=True)
    assert "Die pagina is niet beschikbaar, daarom ben je naar de startpagina gebracht." in flashes(html, "error")


@pytest.mark.parametrize(
    "method, path, headers",
    [
        ("get", "/nope", {}),  # curl, scripts and most bots do not ask for HTML explicitly
        ("get", "/nope", {"Accept": "application/json"}),
        ("get", "/static/missing.css", BROWSER),
        ("get", "/favicon.ico", {"Accept": "image/avif,image/webp,*/*"}),
        ("post", "/nope", BROWSER),
        ("head", "/nope", BROWSER),
    ],
)
def test_everything_else_keeps_a_plain_404(client, method, path, headers):
    assert getattr(client, method)(path, headers=headers).status_code == 404


def test_existing_pages_are_not_redirected(client):
    assert client.get("/about", headers=BROWSER).status_code == 200
    assert client.get("/robots.txt", headers=BROWSER).status_code == 200


# ---- how long a page's CSRF token stays valid ----------------------------------


def aged_token(client, app, days):
    """A valid token for the client's session, signed as if its page had been rendered `days` ago.

    Only this one signature is back-dated; patching the clock while a request runs would also
    back-date Flask's own session cookie, which is signed by the same library.
    """
    import time

    from itsdangerous import TimestampSigner, URLSafeTimedSerializer

    client.get("/")  # makes sure the session has its raw CSRF token
    with client.session_transaction() as session:
        raw = session["csrf_token"]
    serializer = URLSafeTimedSerializer(app.secret_key, salt="wtf-csrf-token")
    original = TimestampSigner.get_timestamp
    TimestampSigner.get_timestamp = lambda self: int(time.time()) - int(days * 24 * 3600)
    try:
        return serializer.dumps(raw)
    finally:
        TimestampSigner.get_timestamp = original


def test_the_csrf_time_limit_is_30_days(app):
    assert app.config["WTF_CSRF_TIME_LIMIT"] == 30 * 24 * 3600


@pytest.mark.parametrize("days, accepted", [(0, True), (1, True), (29, True), (31, False), (60, False)])
def test_a_page_stays_usable_for_30_days_and_then_asks_to_reload(logged_in_client, app, csrf_on, days, accepted):
    client, _user_id = logged_in_client
    token = aged_token(client, app, days)
    response = client.post(
        "/entries/field", json={"date": "2026-01-01", "weight": "80", "confirm": True}, headers={"X-CSRFToken": token}
    )
    if accepted:
        assert response.status_code == 200
    else:
        assert response.status_code == 400 and response.get_json()["code"] == "csrf_expired"
    # only the page's token aged; the user is still signed in (reloading the page fixes it)
    assert client.get("/settings").status_code == 200


# --- 413: an upload that is too large ---------------------------------------------------------

MB = 1024 * 1024


def test_a_too_large_import_goes_back_to_the_settings_with_the_limit(logged_in_client):
    client, _user_id = logged_in_client
    big = io.BytesIO(b"1-1-26,80.0,x\n" * (9 * MB // 14))
    response = client.post(
        "/settings/import",
        data={"csv_file": (big, "big.csv")},
        headers={"Referer": "http://localhost/settings"},
    )
    assert response.status_code == 302 and response.location == "/settings"
    page = html_lib.unescape(client.get("/settings").get_data(as_text=True))
    assert "The file is too large: the limit is 8 MB. Nothing was imported or changed." in page
    assert WeightEntry.query.count() == 0


def test_an_import_just_under_the_limit_still_works(logged_in_client):
    client, _user_id = logged_in_client
    line = b"1-1-26,80.0," + b"x" * 200 + b"\n"
    data = line * (7 * MB // len(line))  # 7 MB: over the 1 MB of other pages
    response = client.post("/settings/import", data={"csv_file": (io.BytesIO(data), "a.csv")})
    assert response.status_code == 302
    assert WeightEntry.query.count() == 1


def test_other_forms_name_their_own_limit_in_the_visitors_language(logged_in_client):
    client, _user_id = logged_in_client
    response = client.post(
        "/settings",
        data={"height_value": "x" * (2 * MB)},
        headers={"Referer": "http://localhost/settings", "Accept-Language": "nl"},
    )
    assert response.status_code == 302
    page = html_lib.unescape(client.get("/settings", headers={"Accept-Language": "nl"}).get_data(as_text=True))
    assert "de limiet is 1 MB" in page


def test_a_too_large_background_save_gets_json(logged_in_client):
    client, _user_id = logged_in_client
    response = client.post("/entries/field", json={"date": "2026-10-01", "weight": "80", "note": "x" * (2 * MB)})
    assert response.status_code == 413
    assert response.get_json()["error"].startswith("The file is too large: the limit is 1 MB.")


# --- 500: an unexpected error -------------------------------------------------------------------

BOOM = "Something went wrong on our side. Please try again in a moment."


@pytest.fixture
def boom_app(app):
    """An app with a page that fails, answering like production (TESTING re-raises errors)."""
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.route("/boom", methods=["GET", "POST"])
    def boom():
        raise RuntimeError("an unexpected bug")

    @app.route("/boom-db")
    def boom_db():
        # A failed flush leaves the session unusable until it is rolled back.
        db.session.add(User(email_hash=User.query.first().email_hash))
        db.session.flush()

    return app


def test_an_unexpected_error_shows_a_friendly_page(boom_app):
    response = boom_app.test_client().get("/boom", headers=BROWSER)
    assert response.status_code == 500
    page = html_lib.unescape(response.get_data(as_text=True))
    assert "Something went wrong" in page and BOOM in page
    assert 'href="/"' in page and 'class="site-header"' in page
    assert "RuntimeError" not in page and "an unexpected bug" not in page  # no internals


def test_the_error_is_still_logged(boom_app, caplog):
    boom_app.test_client().get("/boom")
    assert "an unexpected bug" in caplog.text


def test_the_error_page_is_translated(boom_app):
    response = boom_app.test_client().get("/boom", headers={**BROWSER, "Accept-Language": "nl"})
    page = html_lib.unescape(response.get_data(as_text=True))
    assert '<html lang="nl"' in page and "Er ging iets mis" in page


def test_a_background_request_gets_json(boom_app):
    response = boom_app.test_client().post("/boom", json={})
    assert response.status_code == 500
    assert response.get_json() == {"error": BOOM}


def test_a_failed_database_transaction_does_not_break_the_error_page(boom_app):
    client = boom_app.test_client()
    signup_and_verify(client, "someone@example.com")
    response = client.get("/boom-db", headers=BROWSER)
    assert response.status_code == 500
    page = response.get_data(as_text=True)
    assert BOOM in page and "someone@example.com" in page  # the header still shows the account


def test_when_the_error_page_itself_fails_a_plain_page_is_sent(boom_app, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("the template is broken too")

    monkeypatch.setattr(errors, "render_template", broken)
    response = boom_app.test_client().get("/boom", headers=BROWSER)
    assert response.status_code == 500 and response.mimetype == "text/html"
    assert "Something went wrong" in response.get_data(as_text=True)


def test_the_rest_of_a_refused_upload_is_read_so_the_browser_gets_the_answer(app):
    """gunicorn closes the connection after answering; a browser still sending the file would
    show "connection reset" instead of the message (seen with Chromium), so the body is read."""
    body = io.BytesIO(b"x" * (3 * MB))
    with app.test_request_context("/settings/import", method="POST", input_stream=body,
                                  headers={"Content-Length": str(3 * MB)}):
        errors._drain_upload()
    assert body.read() == b""


# The test request helper sets Content-Length from the stream it is given; environ_overrides keeps
# the length a real client claimed, which is what these cases are about.
def _claimed(length):
    return {"CONTENT_LENGTH": str(length)}


def test_an_upload_shorter_than_it_claims_does_not_hang(app):
    body = io.BytesIO(b"x" * MB)  # says 3 MB, sends 1 MB
    with app.test_request_context("/settings/import", method="POST", input_stream=body,
                                  environ_overrides=_claimed(3 * MB)) as ctx:
        assert ctx.request.content_length == 3 * MB
        errors._drain_upload()
    assert body.read() == b""


def test_a_connection_that_breaks_while_draining_is_ignored(app):
    reads = []

    class Broken(io.BytesIO):
        def read(self, *args):
            reads.append(args)
            raise OSError("connection reset by peer")

    with app.test_request_context("/settings/import", method="POST", input_stream=Broken(b"x" * MB),
                                  environ_overrides=_claimed(MB)):
        errors._drain_upload()  # no exception: the answer still goes out
    assert len(reads) == 1


def test_a_request_without_a_body_stream_is_left_alone(app):
    with app.test_request_context("/settings/import", method="POST", environ_overrides=_claimed(MB)) as ctx:
        ctx.request.environ.pop("wsgi.input")
        assert ctx.request.content_length == MB
        errors._drain_upload()


def test_a_huge_upload_is_not_read(app, monkeypatch):
    monkeypatch.setattr(errors, "DRAIN_MAX_BYTES", 1 * MB)
    body = io.BytesIO(b"x" * (3 * MB))
    with app.test_request_context("/settings/import", method="POST", input_stream=body,
                                  headers={"Content-Length": str(3 * MB)}):
        errors._drain_upload()
    assert len(body.read()) == 3 * MB
