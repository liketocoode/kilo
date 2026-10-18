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

"""Tests that the fail2ban filters in deploy/fail2ban/ fit the lines the app really writes.

The app logs lines such as `kilo-notfound ip=... path=...` and the filters pick them apart; if
either side changes without the other, bans silently stop working (or hit the wrong address).
So the lines here are written by the app itself, then run through the filters' own patterns.
When the real `fail2ban-regex` is installed it is run as well.
"""

import configparser
import re
import shutil
import subprocess

import pytest

from app import create_app, mail, security
from tests.conftest import ROOT, VISITOR_IP, code_from, sign_up

DEPLOY = ROOT / "deploy" / "fail2ban"
LOGPATH = "/var/log/gunicorn/kilo-security.log"
REAL = VISITOR_IP

# fail2ban's <ADDR>: an IPv4 or IPv6 address (and nothing else, so "-" never matches)
ADDR = r"(?P<ip>(?:\d{1,3}\.){3}\d{1,3}|[0-9a-fA-F:]*:[0-9a-fA-F:]+)"
TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d")


def _read(path):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")
    return parser


def _failregexes(name):
    text = _read(DEPLOY / "filter.d" / f"{name}.conf")["Definition"]["failregex"]
    return [line.strip().replace("<ADDR>", ADDR) for line in text.splitlines() if line.strip()]


def banned(name, line):
    """The address filter `name` would ban for this log line, or None. Like fail2ban it first cuts
    the timestamp off, and what is left starts with a space."""
    rest = TIMESTAMP.sub("", line, count=1)
    for pattern in _failregexes(name):
        match = re.search(pattern, rest)
        if match:
            return match.group("ip")
    return None


@pytest.fixture
def security_app(app, tmp_path):
    """Build apps that write the security log to a file; the shared logger is restored afterwards."""
    log = tmp_path / "kilo-security.log"

    def build(**config):
        return create_app(
            {
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "WTF_CSRF_ENABLED": False,
                "CHECK_EMAIL_MX": False,
                "SECURITY_LOG_FILE": str(log),
                **config,
            }
        )

    yield build, log
    security.init_app(app)


def lines_of(log):
    return log.read_text().splitlines() if log.exists() else []


# --- unknown pages ------------------------------------------------------------------------------


def test_an_unknown_page_is_banned_by_the_real_address(security_app):
    build, log = security_app
    build().test_client().get("/.env", headers={"X-Real-IP": REAL})
    build().test_client().get("/.git/config", headers={"X-Real-IP": "2001:db8::7"})
    assert [banned("kilo-notfound", line) for line in lines_of(log)] == [REAL, "2001:db8::7"]


def test_a_forged_path_cannot_get_another_address_banned(security_app):
    build, log = security_app
    build().test_client().get(
        "/x%0Akilo-notfound%20ip%3D9.9.9.9%20path%3D/", headers={"X-Real-IP": "198.51.100.4"}
    )
    (line,) = lines_of(log)
    assert banned("kilo-notfound", line) == "198.51.100.4"  # never 9.9.9.9


def test_routine_requests_and_unknown_visitors_are_never_banned(security_app):
    build, log = security_app
    client = build().test_client()
    client.get("/favicon.ico", headers={"X-Real-IP": REAL})  # not even logged
    client.get("/nothing-here")  # no X-Real-IP: logged as ip=-
    (line,) = lines_of(log)
    assert "ip=-" in line and banned("kilo-notfound", line) is None


# --- sign-in abuse ------------------------------------------------------------------------------


def test_wrong_codes_are_banned_by_the_real_address(security_app):
    build, log = security_app
    client = build().test_client()
    sign_up(client, "someone@example.com")
    client.post("/verify", data={"code": "000000"}, headers={"X-Real-IP": REAL})
    assert [banned("kilo-auth", line) for line in lines_of(log)] == [REAL]


def _refusal_line(security_app, scope):
    """The kilo-auth line the app writes when the limit named `scope` refuses a request."""
    build, log = security_app
    quiet = {"RATELIMIT_COOLDOWN_SECONDS": 0, "RATELIMIT_ADDRESS_PER_HOUR": 0, "RATELIMIT_IP_PER_HOUR": 0,
             "RATELIMIT_GLOBAL_PER_HOUR": 0, "RATELIMIT_REMOVAL_PER_HOUR": 0}
    only = {
        "cooldown": {"RATELIMIT_COOLDOWN_SECONDS": 60},
        "address": {"RATELIMIT_ADDRESS_PER_HOUR": 1},
        "ip": {"RATELIMIT_IP_PER_HOUR": 1},
        "global": {"RATELIMIT_GLOBAL_PER_HOUR": 1},
    }[scope]
    client = build(RATELIMIT_MODE="enforce", **{**quiet, **only}).test_client()
    if scope == "ip":
        sign_up(client, "one@example.com")
        sign_up(client, "two@example.com")  # the same visitor, another address
    elif scope == "global":
        sign_up(client, "one@example.com", ip="198.51.100.1")
        sign_up(client, "two@example.com", ip="198.51.100.2")
    else:
        sign_up(client, "one@example.com")
        sign_up(client, "one@example.com")
    (line,) = lines_of(log)
    assert f"event=code-refused scope={scope}" in line
    return line


@pytest.mark.parametrize("scope", ["address", "ip"])
def test_refusals_by_the_per_address_and_per_visitor_limits_are_banned(security_app, scope):
    assert banned("kilo-auth", _refusal_line(security_app, scope)) is not None


@pytest.mark.parametrize("scope", ["cooldown", "global"])
def test_a_double_click_and_the_site_wide_cap_are_never_banned(security_app, scope):
    # a double click is not abuse, and the site-wide cap trips for everybody: banning the visitors
    # who run into it would punish the innocent
    assert banned("kilo-auth", _refusal_line(security_app, scope)) is None


def _sign_in(client, email="me@example.com"):
    with mail.record_messages() as outbox:
        client.post("/signup", data={"email": email})
        code = code_from(outbox)
        client.post("/verify", data={"code": code})


def test_a_wrong_removal_code_is_banned(security_app):
    build, log = security_app
    client = build(RATELIMIT_MODE="off").test_client()
    _sign_in(client)
    client.get("/settings/export")
    with mail.record_messages() as outbox:
        client.post("/settings/remove/send-code")
        real = code_from(outbox)
    wrong = "000000" if real != "000000" else "111111"
    client.post("/settings/remove/confirm", data={"code": wrong}, headers={"X-Real-IP": REAL})
    (line,) = lines_of(log)
    assert "event=removal-code-wrong" in line
    assert banned("kilo-auth", line) == REAL


def test_a_refused_removal_code_is_banned(security_app):
    build, log = security_app
    client = build(
        RATELIMIT_MODE="enforce", RATELIMIT_COOLDOWN_SECONDS=0, RATELIMIT_REMOVAL_PER_HOUR=1,
        RATELIMIT_ADDRESS_PER_HOUR=0, RATELIMIT_IP_PER_HOUR=0, RATELIMIT_GLOBAL_PER_HOUR=0,
    ).test_client()
    _sign_in(client)
    client.get("/settings/export")
    with mail.record_messages():
        client.post("/settings/remove/send-code", headers={"X-Real-IP": REAL})
        client.post("/settings/remove/send-code", headers={"X-Real-IP": REAL})
    refusals = [line for line in lines_of(log) if "scope=removal" in line]
    assert [banned("kilo-auth", line) for line in refusals] == [REAL]


def test_what_the_limits_would_do_in_log_mode_is_never_banned(security_app):
    build, log = security_app
    # the hourly limit for one address would refuse the second request; the scope is one that the
    # ban filter does take, so only the "would" keeps this line from being banned
    client = build(
        RATELIMIT_MODE="log", RATELIMIT_COOLDOWN_SECONDS=0, RATELIMIT_ADDRESS_PER_HOUR=1,
        RATELIMIT_IP_PER_HOUR=0, RATELIMIT_GLOBAL_PER_HOUR=0,
    ).test_client()
    sign_up(client, "one@example.com")
    sign_up(client, "one@example.com")
    (line,) = lines_of(log)
    assert "event=code-would-refuse scope=address" in line
    assert banned("kilo-auth", line) is None


def test_lines_that_are_not_exactly_in_the_logged_shape_are_not_matched():
    stamp = "2026-10-05 15:30:00 "
    for name, line in [
        ("kilo-notfound", "kilo-notfound ip=203.0.113.9 path=/with space"),  # the app encodes spaces
        ("kilo-notfound", "kilo-notfound ip=203.0.113.9 path=/x trailing=1"),
        ("kilo-notfound", "kilo-notfound ip=not-an-ip path=/x"),
        ("kilo-notfound", "kilo-notfound ip=- path=/x"),
        ("kilo-notfound", "kilo-notfound ip=999 path=/x"),
        ("kilo-notfound", "something kilo-notfound ip=203.0.113.9 path=/x"),  # not at the start of the message
        ("kilo-auth", "kilo-auth ip=203.0.113.9 event=code-wrong and more"),
        ("kilo-auth", "kilo-auth ip=203.0.113.9 event=code-wrong-ish"),
        ("kilo-auth", "kilo-auth ip=203.0.113.9 event=code-refused scope=address-ish"),
        ("kilo-auth", "kilo-auth ip=203.0.113.9 event=code-refused"),
        ("kilo-auth", "kilo-auth ip=- event=code-wrong"),
    ]:
        assert banned(name, stamp + line) is None, line
    # and the plain good ones do match, so the list above is not passing for a silly reason
    assert banned("kilo-notfound", stamp + "kilo-notfound ip=203.0.113.9 path=/x") == "203.0.113.9"
    assert banned("kilo-auth", stamp + "kilo-auth ip=203.0.113.9 event=code-wrong") == "203.0.113.9"


# --- the jail file ---------------------------------------------------------------------------------


def test_the_jails_name_existing_filters_and_use_the_agreed_numbers():
    jails = _read(DEPLOY / "jail.d" / "kilo.local")
    assert set(jails.sections()) == {"kilo-notfound", "kilo-auth"}
    for name in jails.sections():
        section = jails[name]
        assert (DEPLOY / "filter.d" / f"{section['filter']}.conf").exists()
        assert section["enabled"] == "true"
        assert section["logpath"] == LOGPATH
        assert section["backend"] == "auto"  # read the file, whatever [DEFAULT] uses
        # the ban action, ignoreip and so on come from the server's own [DEFAULT]
        assert "banaction" not in section and "ignoreip" not in section and "action" not in section
    assert (jails["kilo-notfound"]["maxretry"], jails["kilo-notfound"]["findtime"]) == ("10", "60")
    assert (jails["kilo-auth"]["maxretry"], jails["kilo-auth"]["findtime"]) == ("10", "10m")


def test_the_documented_log_path_is_the_one_in_the_jails():
    readme = (ROOT / "README.md").read_text()
    assert LOGPATH in readme


# --- the real tool, when it is installed -----------------------------------------------------------


@pytest.mark.skipif(shutil.which("fail2ban-regex") is None, reason="fail2ban-regex is not installed")
def test_the_real_fail2ban_regex_agrees(tmp_path):
    sample = tmp_path / "sample.log"
    sample.write_text(
        "2026-10-05 15:30:00 kilo-notfound ip=203.0.113.9 path=/.env\n"
        "2026-10-05 15:30:01 kilo-notfound ip=- path=/x\n"
        "2026-10-05 15:31:12 kilo-auth ip=203.0.113.9 event=code-wrong\n"
        "2026-10-05 15:31:13 kilo-auth ip=203.0.113.9 event=code-refused scope=global\n"
        "2026-10-05 15:31:14 kilo-auth ip=203.0.113.9 event=code-would-refuse scope=address\n"
    )
    for name, expected in (("kilo-notfound", 1), ("kilo-auth", 1)):
        result = subprocess.run(
            ["fail2ban-regex", "-c", str(DEPLOY), str(sample), name],
            capture_output=True, text=True, check=True,
        )
        assert f"Failregex: {expected} total" in result.stdout, result.stdout
