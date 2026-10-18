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

"""Tests for the daily cleanup of stale sign-ups and expired code hashes."""

import itertools
from datetime import date, datetime, timedelta

import pytest

from app import db, mail
from app.cleanup import cleanup, main
from app.models import RateEvent, User, WeightEntry
from app.utils import SIGNIN_CODE_TTL_MINUTES, hash_email
from tests.conftest import code_from

NOW = datetime(2026, 10, 5, 12, 0, 0)
DAY = timedelta(days=1)
_ids = itertools.count(1)


def make_user(name, created_ago, verified=False, code_issued_ago=None, code_hash="hash", **extra):
    """An account created `created_ago` before NOW; its latest code was issued `code_issued_ago` before NOW."""
    user = User(
        email_hash=hash_email(f"{name}{next(_ids)}@example.com"),
        created_at=NOW - created_ago,
        verified_at=(NOW - created_ago) if verified else None,
        **extra,
    )
    if code_issued_ago is not None:
        user.code_hash = code_hash
        user.code_expires_at = NOW - code_issued_ago + timedelta(minutes=SIGNIN_CODE_TTL_MINUTES)
    db.session.add(user)
    db.session.commit()
    return user.id


def exists(user_id):
    return db.session.get(User, user_id) is not None


# --- stale sign-ups ----------------------------------------------------------------------


def test_a_verified_user_is_never_removed_however_old(app):
    old = make_user("old", 400 * DAY, verified=True)
    cleanup(now=NOW)
    assert exists(old)


def test_an_unverified_account_idle_for_a_week_is_removed(app):
    never_asked = make_user("a", 8 * DAY)  # no code at all
    expired_code = make_user("b", 8 * DAY, code_issued_ago=8 * DAY)
    cleanup(now=NOW)
    assert not exists(never_asked) and not exists(expired_code)


def test_a_recent_account_is_kept(app):
    assert exists(make_user("recent", 6 * DAY))
    cleanup(now=NOW)
    assert exists(db.session.query(User.id).scalar())


def test_a_pending_code_protects_an_old_row(app):
    # created long ago, but asked for a new code a minute ago and is typing it right now
    typing = make_user("typing", 30 * DAY, code_issued_ago=timedelta(minutes=1))
    cleanup(now=NOW)
    assert exists(typing)


def test_idle_time_counts_from_the_latest_code_not_from_creation(app):
    asked_six_days_ago = make_user("c", 30 * DAY, code_issued_ago=6 * DAY)
    asked_eight_days_ago = make_user("d", 30 * DAY, code_issued_ago=8 * DAY)
    cleanup(now=NOW)
    assert exists(asked_six_days_ago)
    assert not exists(asked_eight_days_ago)


def test_the_retention_boundary(app):
    exactly = make_user("e", 7 * DAY)
    just_over = make_user("f", 7 * DAY + timedelta(seconds=1))
    cleanup(now=NOW)
    assert exists(exactly)  # not yet older than 7 days
    assert not exists(just_over)


def test_an_account_with_entries_is_kept_as_a_second_line_of_defence(app):
    user_id = make_user("g", 30 * DAY)
    db.session.add(WeightEntry(user_id=user_id, entry_date=date(2026, 1, 1), weight=80.0))
    db.session.commit()
    cleanup(now=NOW)
    assert exists(user_id)
    assert WeightEntry.query.count() == 1


def test_a_removed_address_can_sign_up_again(client, app):
    with mail.record_messages():
        client.post("/signup", data={"email": "again@example.com"})
    assert User.query.count() == 1
    cleanup(now=datetime.now() + 10 * DAY)  # long after the sign-up and its code
    assert User.query.count() == 0
    with mail.record_messages() as outbox:
        assert client.post("/signup", data={"email": "again@example.com"}).status_code == 302
    assert len(outbox) == 1 and User.query.count() == 1


def test_a_visitor_whose_row_was_removed_is_sent_back_to_sign_up(client, app):
    client.post("/signup", data={"email": "slow@example.com"})  # now on the verify page
    cleanup(now=datetime.now() + 10 * DAY)
    response = client.get("/verify")
    assert response.status_code == 302 and response.headers["Location"].endswith("/signup")


# --- expired code hashes --------------------------------------------------------------------


def test_expired_hashes_are_cleared_and_valid_ones_kept(app):
    expired = make_user("h", 1 * DAY, verified=True, code_issued_ago=1 * DAY)
    valid = make_user("i", 1 * DAY, verified=True, code_issued_ago=timedelta(minutes=5))
    no_expiry = make_user("j", 1 * DAY, verified=True, code_issued_ago=1 * DAY)
    db.session.get(User, no_expiry).code_expires_at = None
    db.session.commit()
    cleanup(now=NOW)
    assert db.session.get(User, expired).code_hash is None
    assert db.session.get(User, valid).code_hash == "hash"
    assert db.session.get(User, no_expiry).code_hash is None
    assert exists(expired) and exists(valid) and exists(no_expiry)  # clearing never deletes


def test_expired_removal_code_hashes_are_cleared_too(app):
    old = make_user("k", 5 * DAY, verified=True, delete_code_hash="x", delete_code_expires_at=NOW - timedelta(hours=1))
    live = make_user(
        "l", 5 * DAY, verified=True, delete_code_hash="y", delete_code_expires_at=NOW + timedelta(minutes=5)
    )
    cleanup(now=NOW)
    assert db.session.get(User, old).delete_code_hash is None
    assert db.session.get(User, live).delete_code_hash == "y"


def test_a_cleared_code_is_reported_as_expired_when_entered(client, app):
    with mail.record_messages() as outbox:
        client.post("/signup", data={"email": "late@example.com"})
    code = code_from(outbox)
    cleanup(now=datetime.now() + 2 * timedelta(minutes=SIGNIN_CODE_TTL_MINUTES))  # the code has expired
    assert User.query.one().code_hash is None
    response = client.post("/verify", data={"code": code})
    assert b"That code has expired" in response.data


# --- the whole run ----------------------------------------------------------------------------


def test_a_second_run_changes_nothing(app):
    make_user("m", 20 * DAY)
    make_user("n", 3 * DAY, verified=True, code_issued_ago=2 * DAY)
    first = cleanup(now=NOW)
    assert first == {"stale_signups": 1, "signin_hashes": 1, "removal_hashes": 0, "rate_events": 0, "avatars": 0}
    assert cleanup(now=NOW) == dict.fromkeys(first, 0)


def test_a_dry_run_reports_what_the_real_run_does_and_changes_nothing(app):
    make_user("o", 20 * DAY, code_issued_ago=20 * DAY)  # stale: its hash goes with the row
    make_user("p", 3 * DAY, verified=True, code_issued_ago=2 * DAY)  # an expired hash to clear
    make_user("q", 3 * DAY, verified=True, delete_code_hash="z", delete_code_expires_at=NOW - DAY)

    def snapshot():
        return sorted((u.id, u.code_hash, u.delete_code_hash) for u in User.query)

    before = snapshot()
    planned = cleanup(now=NOW, dry_run=True)
    assert snapshot() == before
    assert planned == {
        "stale_signups": 1, "signin_hashes": 1, "removal_hashes": 1, "rate_events": 0, "avatars": 0
    }
    assert cleanup(now=NOW) == planned


def test_stale_days_can_be_changed(app):
    two_days = make_user("r", 2 * DAY)
    cleanup(now=NOW, stale_days=3)
    assert exists(two_days)
    cleanup(now=NOW, stale_days=1)
    assert not exists(two_days)


# --- the counts behind the rate limits ------------------------------------------------------------


def test_rate_limit_counts_older_than_a_day_are_removed(app):
    for age in (timedelta(hours=25), timedelta(hours=23), timedelta(minutes=5)):
        db.session.add(RateEvent(kind="cooldown", subject="x", at=NOW - age))
    db.session.commit()
    planned = cleanup(now=NOW, dry_run=True)
    assert planned["rate_events"] == 1 and RateEvent.query.count() == 3  # a dry run changes nothing
    assert cleanup(now=NOW)["rate_events"] == 1
    assert sorted(e.at for e in RateEvent.query) == [NOW - timedelta(hours=23), NOW - timedelta(minutes=5)]
    assert cleanup(now=NOW)["rate_events"] == 0


# --- the command ------------------------------------------------------------------------------


def test_the_command_prints_a_summary(app, capsys):
    make_user("s", 30 * DAY)
    assert main(["--dry-run"], app=app) == 0
    out = capsys.readouterr().out
    assert "Would remove 1 stale sign-up(s)" in out and "Would clear 0 expired sign-in" in out
    assert User.query.count() == 1  # a dry run

    assert main([], app=app) == 0
    assert "Removed 1 stale sign-up(s)" in capsys.readouterr().out
    assert User.query.count() == 0


def test_the_command_rejects_a_silly_retention(app, capsys):
    with pytest.raises(SystemExit) as stop:
        main(["--stale-days", "0"], app=app)
    assert stop.value.code == 2
    assert "at least 1" in capsys.readouterr().err
