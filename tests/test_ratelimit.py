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

"""Tests for the limits on emailed sign-in and removal codes."""

import logging
import re
from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import OperationalError

from app import create_app, db, mail, ratelimit, security
from app.models import RateEvent, User
from app.utils import hash_email
from tests.conftest import VISITOR_IP, code_from, sign_up

IP = VISITOR_IP


@pytest.fixture
def clock(monkeypatch):
    """A controllable clock for the limiter only (not Flask's own cookie timing)."""

    class Clock:
        now = datetime(2026, 10, 5, 12, 0, 0)

        def __call__(self):
            return self.now

        def advance(self, **kwargs):
            self.now += timedelta(**kwargs)

    fake = Clock()
    monkeypatch.setattr(ratelimit, "utcnow", fake)
    monkeypatch.setattr(ratelimit, "_warned_no_address", False)
    return fake


@pytest.fixture
def enforcing(app, clock):
    app.config["RATELIMIT_MODE"] = "enforce"
    return app


def counted(kind=None):
    query = RateEvent.query
    return (query.filter_by(kind=kind) if kind else query).count()


REFUSED = b"Too many requests. Try again in"


# --- one address: the wait between codes ---------------------------------------------------------


def test_a_second_request_within_the_wait_sends_nothing_but_looks_like_success(enforcing, clock, client):
    _, first = sign_up(client, "a@example.com")
    assert len(first) == 1
    clock.advance(seconds=10)
    response, second = sign_up(client, "a@example.com")
    assert second == []  # no second mail
    assert b"We sent a verification code to a@example.com" in response.data  # answered as for a fresh send
    assert REFUSED not in response.data
    clock.advance(seconds=55)  # 65 s after the first
    _, third = sign_up(client, "a@example.com")
    assert len(third) == 1


def test_a_new_code_may_be_sent_the_moment_the_wait_is_over(enforcing, clock, client):
    """The window is "the last 60 seconds": a send exactly 60 s ago is no longer in it."""
    assert len(sign_up(client, "edge@example.com")[1]) == 1
    clock.advance(seconds=59)
    assert sign_up(client, "edge@example.com")[1] == []
    clock.advance(seconds=1)
    assert len(sign_up(client, "edge@example.com")[1]) == 1


def test_the_owner_is_not_locked_out_by_someone_else_asking_for_codes(enforcing, clock, client, app):
    _, first = sign_up(client, "owner@example.com")
    code = code_from(first)
    clock.advance(seconds=5)
    sign_up(app.test_client(), "owner@example.com", ip="198.51.100.66")  # a prankster, a few seconds later
    # nothing replaced the code in the owner's mailbox: it still works
    client.post("/verify", data={"code": code})
    with client.session_transaction() as session:
        assert session.get("user_id") is not None


def test_a_new_code_replaces_the_old_one_and_the_newest_is_the_valid_one(enforcing, clock, client):
    _, first = sign_up(client, "owner@example.com")
    clock.advance(seconds=61)
    _, second = sign_up(client, "owner@example.com")
    old, new = code_from(first), code_from(second)
    client.post("/verify", data={"code": old if old != new else "000000"})
    with client.session_transaction() as session:
        assert "user_id" not in session  # the old code no longer works
    client.post("/verify", data={"code": new})
    with client.session_transaction() as session:
        assert session.get("user_id") is not None


# --- one address: the hourly limit ----------------------------------------------------------------


def _send_five(client, clock, email="busy@example.com"):
    for _ in range(5):
        _, outbox = sign_up(client, email)
        assert len(outbox) == 1
        clock.advance(seconds=61)


def test_the_sixth_code_in_an_hour_is_refused_and_the_wait_is_shown(enforcing, clock, client):
    _send_five(client, clock)  # sends at 0, 61, 122, 183, 244 s; now 305 s
    response, outbox = sign_up(client, "busy@example.com")
    assert outbox == []
    assert b"Too many requests. Try again in 55 minutes." in response.data  # the first one leaves at 3600 s


def test_the_limit_opens_up_again_when_the_oldest_send_leaves_the_hour(enforcing, clock, client):
    _send_five(client, clock)
    clock.advance(seconds=3599 - 305)  # t = 3599: one second early
    assert sign_up(client, "busy@example.com")[1] == []
    clock.advance(seconds=2)  # t = 3601: the first send is out of the window
    assert len(sign_up(client, "busy@example.com")[1]) == 1


def test_refused_requests_are_not_counted_and_do_not_extend_the_wait(enforcing, clock, client):
    _send_five(client, clock)
    before = counted()
    for _ in range(4):
        sign_up(client, "busy@example.com")
        clock.advance(seconds=30)
    assert counted() == before
    clock.advance(seconds=3601 - 305 - 120)
    assert len(sign_up(client, "busy@example.com")[1]) == 1


def test_addresses_are_independent_and_written_in_any_case(enforcing, clock, client):
    assert len(sign_up(client, "one@example.com")[1]) == 1
    assert len(sign_up(client, "two@example.com", ip="198.51.100.1")[1]) == 1
    # the same address in other letters and with spaces is the same address: inside its wait
    assert sign_up(client, "  ONE@Example.COM ")[1] == []


def test_the_refusal_is_the_same_for_an_address_with_and_without_an_account(enforcing, clock, client, app):
    app.config["RATELIMIT_ADDRESS_PER_HOUR"] = 1
    for email, ip in (("known@example.com", "198.51.100.1"), ("stranger@example.com", "198.51.100.2")):
        sign_up(client, email, ip=ip)
    with client.session_transaction() as session:
        session.clear()
    # one of them verified: an account in the strict sense
    user = User.query.filter_by(email_hash=hash_email("known@example.com")).one()
    user.verified_at = clock.now
    db.session.commit()
    clock.advance(seconds=61)
    texts = []
    for email, ip in (("known@example.com", "198.51.100.1"), ("stranger@example.com", "198.51.100.2")):
        response, outbox = sign_up(client, email, ip=ip)
        assert outbox == []
        texts.append(re.search(rb"Too many requests[^<]*", response.data).group(0))
    assert texts[0] == texts[1]


def test_resend_is_refused_with_a_message_while_the_wait_runs(enforcing, clock, client):
    sign_up(client, "a@example.com")
    with mail.record_messages() as outbox:
        response = client.post("/verify/resend", follow_redirects=True)
    assert outbox == []
    assert b"Too many requests. Try again in 1 minute." in response.data
    clock.advance(seconds=61)
    with mail.record_messages() as outbox:
        client.post("/verify/resend")
    assert len(outbox) == 1


# --- the visitor's address --------------------------------------------------------------------------


def test_one_visitor_address_cannot_start_codes_for_many_addresses(enforcing, clock, client, app):
    app.config["RATELIMIT_IP_PER_HOUR"] = 3
    for i in range(3):
        assert len(sign_up(client, f"p{i}@example.com")[1]) == 1
    response, outbox = sign_up(client, "p3@example.com")
    assert outbox == [] and REFUSED in response.data
    assert User.query.filter_by(email_hash=hash_email("p3@example.com")).first() is None  # no row left behind
    assert len(sign_up(client, "p3@example.com", ip="198.51.100.50")[1]) == 1  # another visitor is fine


def test_an_ipv6_household_shares_one_budget(enforcing, clock, client, app):
    app.config["RATELIMIT_IP_PER_HOUR"] = 2
    assert len(sign_up(client, "v1@example.com", ip="2001:db8:1:2::1")[1]) == 1
    assert len(sign_up(client, "v2@example.com", ip="2001:db8:1:2:ffff::7")[1]) == 1  # same /64
    assert sign_up(client, "v3@example.com", ip="2001:db8:1:2::99")[1] == []  # same /64: refused
    assert len(sign_up(client, "v3@example.com", ip="2001:db8:1:3::1")[1]) == 1  # another /64


def test_without_a_known_visitor_address_no_per_address_limit_is_applied(enforcing, clock, client, app, caplog):
    app.config["RATELIMIT_IP_PER_HOUR"] = 1
    caplog.set_level(logging.WARNING)
    for i in range(4):  # no X-Real-IP at all: everybody would look like 127.0.0.1
        assert len(sign_up(client, f"n{i}@example.com", ip=None)[1]) == 1
    warnings = [r for r in caplog.records if "address is unknown" in r.getMessage()]
    assert len(warnings) == 1  # said once, not for every request


def test_the_table_holds_no_plain_address(enforcing, clock, client):
    sign_up(client, "someone@example.com", ip=IP)
    events = RateEvent.query.all()
    assert {e.kind for e in events} == {"cooldown", "address-hour", "ip-hour", "global-hour"}
    for event in events:
        assert IP not in event.subject and "someone" not in event.subject
    ip_event = next(e for e in events if e.kind == "ip-hour")
    assert re.fullmatch(r"[0-9a-f]{32}", ip_event.subject)


# --- the whole site ---------------------------------------------------------------------------------


def test_the_global_ceiling_stops_everybody(enforcing, clock, client, app):
    app.config["RATELIMIT_GLOBAL_PER_HOUR"] = 3
    for i in range(3):
        assert len(sign_up(client, f"g{i}@example.com", ip=f"198.51.100.{i + 1}")[1]) == 1
    response, outbox = sign_up(client, "g9@example.com", ip="198.51.100.99")
    assert outbox == [] and REFUSED in response.data
    clock.advance(seconds=3601)
    assert len(sign_up(client, "g9@example.com", ip="198.51.100.99")[1]) == 1


def test_a_refused_attempt_does_not_use_up_the_other_limits(enforcing, clock, client, app):
    # refused by the visitor-address limit, so what it already counted for the address is given back
    app.config["RATELIMIT_IP_PER_HOUR"] = 1
    sign_up(client, "first@example.com")
    assert sign_up(client, "second@example.com")[1] == []  # same visitor: refused
    assert counted("cooldown") == 1 and counted("address-hour") == 1  # only the first send counts
    # the same address from another visitor right away: its own wait was never started
    assert len(sign_up(client, "second@example.com", ip="198.51.100.77")[1]) == 1


def test_counting_and_checking_cannot_be_separated(enforcing, clock, app):
    now = clock()
    ids = [ratelimit._insert_if_below("k", "s", 2, 60, now) for _ in range(3)]
    assert ids[0] and ids[1] and ids[2] is None  # the third is refused by the statement itself
    assert ratelimit._insert_if_below("k", "s", 2, 60, now + timedelta(seconds=61)) is not None  # window moved on
    assert ratelimit._insert_if_below("k", "other", 2, 60, now) is not None  # another subject


# --- removal codes ----------------------------------------------------------------------------------


def _remove_code(client):
    client.get("/settings/export")
    with mail.record_messages() as outbox:
        response = client.post("/settings/remove/send-code", follow_redirects=True)
    return response, outbox


def test_removal_codes_have_their_own_wait_and_hourly_limit(enforcing, clock, logged_in_client):
    client, _ = logged_in_client  # signed in a moment ago: that sign-in code does not get in the way
    response, outbox = _remove_code(client)
    assert len(outbox) == 1
    response, outbox = _remove_code(client)  # inside the wait
    assert outbox == [] and b"Too many requests. Try again in 1 minute." in response.data
    for _ in range(2):
        clock.advance(seconds=61)
        assert len(_remove_code(client)[1]) == 1
    clock.advance(seconds=61)
    response, outbox = _remove_code(client)  # a fourth within the hour
    assert outbox == [] and REFUSED in response.data
    assert counted("removal-hour") == 3 and counted("address-hour") == 1


# --- when the mail cannot be sent ------------------------------------------------------------------


def test_a_failed_send_does_not_use_up_the_allowance(enforcing, clock, client, monkeypatch):
    def broken(email, code):
        raise RuntimeError("the mail server is down")

    monkeypatch.setattr("app.auth.send_verification_email", broken)
    with pytest.raises(RuntimeError):
        client.post("/signup", data={"email": "a@example.com"}, headers={"X-Real-IP": IP})
    assert counted() == 0  # given back
    monkeypatch.undo()
    assert len(sign_up(client, "a@example.com")[1]) == 1  # no wait to sit out


def test_a_failed_removal_mail_gives_the_allowance_back_too(enforcing, clock, logged_in_client, monkeypatch):
    client, _ = logged_in_client
    client.get("/settings/export")

    def broken(email, code):
        raise RuntimeError("the mail server is down")

    monkeypatch.setattr("app.account.send_deletion_email", broken)
    with pytest.raises(RuntimeError):
        client.post("/settings/remove/send-code")
    assert counted("removal-hour") == 0 and counted("removal-wait") == 0


# --- the limiter must never take the site down -----------------------------------------------------


def test_if_the_limiter_fails_the_request_goes_through(enforcing, clock, client, monkeypatch, caplog):
    def broken(scopes, now):
        raise OperationalError("select", {}, Exception("database is locked"))

    monkeypatch.setattr(ratelimit, "_reserve", broken)
    caplog.set_level(logging.WARNING)
    assert len(sign_up(client, "a@example.com")[1]) == 1
    assert any("letting the request through" in r.getMessage() for r in caplog.records)


# --- the modes ---------------------------------------------------------------------------------------


def test_off_counts_nothing_and_limits_nothing(app, clock, client):
    assert app.config["RATELIMIT_MODE"] == "off"
    for _ in range(8):
        assert len(sign_up(client, "a@example.com")[1]) == 1
    assert counted() == 0


def test_log_mode_refuses_nothing_but_says_what_it_would_refuse(app, clock, client, caplog):
    app.config["RATELIMIT_MODE"] = "log"
    caplog.set_level(logging.INFO, logger=security.LOGGER_NAME)
    for _ in range(3):
        response, outbox = sign_up(client, "a@example.com")
        assert len(outbox) == 1  # every request is served
        assert REFUSED not in response.data
    lines = [r.getMessage() for r in caplog.records if r.name == security.LOGGER_NAME]
    assert lines == [f"kilo-auth ip={IP} event=code-would-refuse scope=cooldown"] * 2
    assert counted("cooldown") == 1  # what would have been refused is not counted, as when enforcing


def test_enforce_mode_logs_the_refusal_for_fail2ban(enforcing, clock, client, caplog):
    caplog.set_level(logging.INFO, logger=security.LOGGER_NAME)
    sign_up(client, "someone@example.com")
    clock.advance(seconds=61)
    for _ in range(4):
        sign_up(client, "someone@example.com")
        clock.advance(seconds=61)
    sign_up(client, "someone@example.com")  # the sixth in the hour
    lines = [r.getMessage() for r in caplog.records if r.name == security.LOGGER_NAME]
    assert lines == [f"kilo-auth ip={IP} event=code-refused scope=address"]
    assert "someone" not in lines[0] and hash_email("someone@example.com") not in lines[0]


def test_an_unknown_mode_becomes_log_and_says_so(caplog):
    caplog.set_level(logging.WARNING)
    made = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "CHECK_EMAIL_MX": False,
            "RATELIMIT_MODE": "strict",
        }
    )
    assert made.config["RATELIMIT_MODE"] == "log"
    assert any("RATELIMIT_MODE" in r.getMessage() for r in caplog.records)


def test_a_limit_of_zero_turns_that_limit_off(enforcing, clock, client, app):
    app.config["RATELIMIT_COOLDOWN_SECONDS"] = 0
    app.config["RATELIMIT_ADDRESS_PER_HOUR"] = 0
    for _ in range(8):
        assert len(sign_up(client, "a@example.com")[1]) == 1


def test_numbers_can_be_set_in_the_environment(monkeypatch):
    base = {"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "CHECK_EMAIL_MX": False}
    monkeypatch.setenv("RATELIMIT_IP_PER_HOUR", "7")
    monkeypatch.setenv("RATELIMIT_GLOBAL_PER_HOUR", "not a number")
    made = create_app(base)
    assert made.config["RATELIMIT_IP_PER_HOUR"] == 7
    assert made.config["RATELIMIT_GLOBAL_PER_HOUR"] == 60  # unusable: the default


def test_the_default_mode_is_log_so_deploying_changes_nothing(monkeypatch):
    monkeypatch.delenv("RATELIMIT_MODE", raising=False)
    made = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "CHECK_EMAIL_MX": False})
    assert made.config["RATELIMIT_MODE"] == "log"


# --- the refusal speaks the visitor's language, with the right plural form -----------------------------


@pytest.mark.parametrize(
    "lang, minutes, expected",
    [
        ("en", 1, "Try again in 1 minute."),
        ("en", 22, "Try again in 22 minutes."),
        ("nl", 1, "Probeer het over 1 minuut opnieuw."),
        ("nl", 22, "Probeer het over 22 minuten opnieuw."),
        ("fr", 1, "Réessayez dans 1 minute."),
        ("fr", 22, "Réessayez dans 22 minutes."),
        ("de", 22, "Versuche es in 22 Minuten erneut."),
        ("id", 22, "Coba lagi dalam 22 menit."),  # Indonesian has a single form
        ("pl", 1, "za 1 minutę."),  # Polish: 1, 2-4 and the rest
        ("pl", 22, "za 22 minuty."),
        ("pl", 5, "za 5 minut."),
        ("ro", 1, "peste 1 minut."),  # Romanian: 1, 2-19, and the rest ("de minute")
        ("ro", 5, "peste 5 minute."),
        ("ro", 22, "peste 22 de minute."),
    ],
)
def test_the_refusal_is_translated_with_the_plural_form_of_the_language(
    enforcing, clock, client, app, lang, minutes, expected
):
    app.config["RATELIMIT_ADDRESS_PER_HOUR"] = 1
    sign_up(client, "slow@example.com", lang=lang)
    clock.advance(seconds=3600 - minutes * 60)  # so that the wait is exactly `minutes` minutes
    response, outbox = sign_up(client, "slow@example.com", lang=lang)
    assert outbox == []
    text = response.data.decode()
    assert expected in text
    assert "%(num)d" not in text
