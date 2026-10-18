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

"""Tests for the client address, the security log and the lines written to it."""

import logging
import re

import pytest

from app import create_app, mail, security
from app.security import MAX_FIELD_LENGTH, client_ip, log_event
from tests.conftest import code_from


@pytest.fixture
def lines(caplog):
    """The security log lines written so far, as a list of strings."""
    caplog.set_level(logging.INFO, logger=security.LOGGER_NAME)
    return lambda: [r.getMessage() for r in caplog.records if r.name == security.LOGGER_NAME]


def _ip(app, remote, **headers):
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": remote}, headers=headers):
        return client_ip()


# --- the visitor's address ----------------------------------------------------------


def test_address_comes_from_x_real_ip_behind_the_proxy(app):
    assert _ip(app, "127.0.0.1", **{"X-Real-IP": "203.0.113.9"}) == "203.0.113.9"
    assert _ip(app, "::1", **{"X-Real-IP": "2001:db8::7"}) == "2001:db8::7"


def test_a_forged_header_is_ignored_on_a_direct_connection(app):
    # not from the proxy: whoever connects can write anything in the header
    assert _ip(app, "198.51.100.20", **{"X-Real-IP": "1.2.3.4"}) == "198.51.100.20"


def test_missing_or_unusable_header_gives_no_address_not_loopback(app):
    assert _ip(app, "127.0.0.1") is None  # no header: everyone would look like one visitor
    for bad in ("not-an-ip", "", "1.2.3.4, 5.6.7.8", "127.0.0.1", "::1", "0.0.0.0"):
        assert _ip(app, "127.0.0.1", **{"X-Real-IP": bad}) is None, bad


def test_addresses_are_normalised(app):
    assert _ip(app, "127.0.0.1", **{"X-Real-IP": "::ffff:203.0.113.9"}) == "203.0.113.9"
    assert _ip(app, "127.0.0.1", **{"X-Real-IP": "2001:0DB8:0000:0000:0000:0000:0000:0007"}) == "2001:db8::7"
    assert _ip(app, "::ffff:198.51.100.20") == "198.51.100.20"


# --- the log lines --------------------------------------------------------------------


def test_line_format_is_fixed(app, lines):
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}, headers={"X-Real-IP": "203.0.113.9"}):
        log_event("notfound", path="/.env")
        log_event("auth", event="code-wrong")
    assert lines() == [
        "kilo-notfound ip=203.0.113.9 path=/.env",
        "kilo-auth ip=203.0.113.9 event=code-wrong",
    ]


def test_unknown_address_is_logged_as_a_dash(app, lines):
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        log_event("notfound", path="/x")
    assert lines() == ["kilo-notfound ip=- path=/x"]


def test_a_visitor_cannot_forge_a_log_line(client, lines):
    # %0a is a newline once the server has decoded the path; %20 and %3D are a space and "="
    client.get(
        "/x%0Akilo-notfound%20ip%3D9.9.9.9%20path%3D/",
        headers={"X-Real-IP": "203.0.113.9"},
    )
    (line,) = lines()  # one request, one line
    assert "\n" not in line and "\r" not in line
    # a filter that reads "ip=<address> path=" finds only the real address
    assert re.findall(r"kilo-notfound ip=(\S+) path=", line) == ["203.0.113.9"]
    assert "ip=9.9.9.9" not in line


def test_long_values_are_cut(app, lines):
    with app.test_request_context("/"):
        log_event("notfound", path="/" + "a" * 5000)
    (line,) = lines()
    assert len(line) < MAX_FIELD_LENGTH + 60


# --- unknown pages ----------------------------------------------------------------------


def test_unknown_page_is_logged_with_the_real_address(client, lines):
    response = client.get("/.env", headers={"X-Real-IP": "203.0.113.9"})
    assert response.status_code == 404
    assert lines() == ["kilo-notfound ip=203.0.113.9 path=/.env"]


def test_unknown_page_is_logged_when_a_browser_is_redirected_too(client, lines):
    # a scanner that sends Accept: text/html gets the friendly redirect, not a 404
    response = client.get("/.git/config", headers={"X-Real-IP": "203.0.113.9", "Accept": "text/html"})
    assert response.status_code == 302
    assert lines() == ["kilo-notfound ip=203.0.113.9 path=/.git/config"]


def test_missing_static_file_is_logged(client, lines):
    client.get("/static/nothing-here.js", headers={"X-Real-IP": "203.0.113.9"})
    assert lines() == ["kilo-notfound ip=203.0.113.9 path=/static/nothing-here.js"]


@pytest.mark.parametrize("path", ["/favicon.ico", "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"])
def test_pages_browsers_ask_for_by_themselves_are_not_logged(client, lines, path):
    assert client.get(path, headers={"X-Real-IP": "203.0.113.9"}).status_code == 404
    assert lines() == []  # they would add up to a ban for every visitor


def test_a_page_that_only_accepts_posts_is_not_logged(client, lines):
    # typing /logout in the address bar is a slip, not a scan
    assert client.get("/logout", headers={"X-Real-IP": "203.0.113.9"}).status_code == 405
    assert lines() == []


def test_existing_pages_are_not_logged(client, lines):
    for path in ("/", "/about", "/robots.txt", "/static/style.css"):
        assert client.get(path, headers={"X-Real-IP": "203.0.113.9"}).status_code == 200
    assert lines() == []


# --- wrong sign-in codes ----------------------------------------------------------------


def _wrong_code(real_code):
    return "000000" if real_code != "000000" else "111111"


def test_wrong_sign_in_code_is_logged(client, lines):
    with mail.record_messages() as outbox:
        client.post("/signup", data={"email": "someone@example.com"})
    real = code_from(outbox)

    client.post("/verify", data={"code": _wrong_code(real)}, headers={"X-Real-IP": "203.0.113.9"})
    assert lines() == ["kilo-auth ip=203.0.113.9 event=code-wrong"]

    client.post("/verify", data={"code": real}, headers={"X-Real-IP": "203.0.113.9"})
    assert len(lines()) == 1  # the right code is not an event


def test_the_log_never_holds_an_address_or_a_code(client, lines):
    with mail.record_messages() as outbox:
        client.post("/signup", data={"email": "private.person@example.com"})
    real = code_from(outbox)
    wrong = _wrong_code(real)
    client.post("/verify", data={"code": wrong})
    text = "\n".join(lines())
    assert "private" not in text and "example.com" not in text and wrong not in text


def test_wrong_removal_code_is_logged(logged_in_client, lines):
    client, _ = logged_in_client
    client.get("/settings/export")
    with mail.record_messages() as outbox:
        client.post("/settings/remove/send-code")
    real = code_from(outbox)
    client.post(
        "/settings/remove/confirm",
        data={"code": _wrong_code(real)},
        headers={"X-Real-IP": "203.0.113.9"},
    )
    assert lines() == ["kilo-auth ip=203.0.113.9 event=removal-code-wrong"]


# --- the log file -------------------------------------------------------------------------


@pytest.fixture
def file_app(app, tmp_path):
    """An app that writes the security log to a file; the shared logger is put back afterwards."""
    path = tmp_path / "kilo-security.log"
    made = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "WTF_CSRF_ENABLED": False,
            "CHECK_EMAIL_MX": False,
            "SECURITY_LOG_FILE": str(path),
        }
    )
    yield made, path
    security.init_app(app)


def test_lines_go_to_the_file_in_a_format_fail2ban_can_read(file_app):
    made, path = file_app
    made.test_client().get("/.env", headers={"X-Real-IP": "203.0.113.9"})
    (line,) = path.read_text().splitlines()
    # a timestamp fail2ban recognises, then the fixed message
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d kilo-notfound ip=203\.0\.113\.9 path=/\.env", line)


def test_the_file_is_reopened_after_logrotate_moves_it(file_app):
    made, path = file_app
    client = made.test_client()
    client.get("/one", headers={"X-Real-IP": "203.0.113.9"})
    path.rename(path.with_name("kilo-security.log.1"))  # what logrotate does
    client.get("/two", headers={"X-Real-IP": "203.0.113.9"})
    assert "/one" in path.with_name("kilo-security.log.1").read_text()
    assert "/two" in path.read_text() and "/one" not in path.read_text()


def test_lines_are_not_written_twice(file_app):
    made, path = file_app
    made.test_client().get("/x", headers={"X-Real-IP": "203.0.113.9"})
    assert len(path.read_text().splitlines()) == 1
    # with a file the lines do not also travel up to the root logger (the application log)
    assert logging.getLogger(security.LOGGER_NAME).propagate is False


def test_creating_apps_again_does_not_stack_handlers(app, tmp_path):
    path = tmp_path / "log.txt"
    for _ in range(3):
        create_app(
            {
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "CHECK_EMAIL_MX": False,
                "SECURITY_LOG_FILE": str(path),
            }
        )
    logger = logging.getLogger(security.LOGGER_NAME)
    handlers = [h for h in logger.handlers if getattr(h, "_kilo_security_handler", False)]
    assert len(handlers) == 1
    security.init_app(app)  # back to "no file"
    assert not [h for h in logger.handlers if getattr(h, "_kilo_security_handler", False)]


def test_an_unwritable_log_path_does_not_stop_the_app(app, tmp_path, caplog):
    bad = tmp_path / "no-such-folder" / "security.log"
    caplog.set_level(logging.WARNING)
    made = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "CHECK_EMAIL_MX": False,
            "SECURITY_LOG_FILE": str(bad),
        }
    )
    try:
        assert made.test_client().get("/about").status_code == 200  # still serving
        assert any("Cannot write the security log" in r.getMessage() for r in caplog.records)
    finally:
        security.init_app(app)
