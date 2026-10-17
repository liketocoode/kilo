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

"""Tests for app/codes.py, the emailed one-time codes, for both kinds (sign-in and removal).

The flows in auth.py and account.py are tested through their pages in test_auth.py and
test_account.py; these pin the shared rules themselves (lifetime, expiry at the boundary,
attempts, the kinds staying apart), each for both kinds.
"""

from datetime import timedelta

import pytest

from app import codes, db
from app.models import User
from app.utils import DELETE_CODE_TTL_MINUTES, SIGNIN_CODE_TTL_MINUTES

NOW = codes.utcnow()
KINDS = ["signin", "removal"]


class FakeReservation:
    released = False

    def release(self):
        self.released = True


@pytest.fixture
def clock(monkeypatch):
    """A fixed `utcnow()` for app/codes.py; set `clock.now` to move it."""

    class Clock:
        now = NOW

    monkeypatch.setattr(codes, "utcnow", lambda: Clock.now)
    return Clock


@pytest.fixture
def user(app):
    with app.app_context():
        user = User(email_hash="a" * 64)
        db.session.add(user)
        db.session.commit()
        yield user


def issue(user, kind):
    """Issue a code and return what was mailed."""
    sent = []
    codes.issue(user, kind, "someone@example.com", lambda email, code: sent.append(code), FakeReservation())
    return sent[0]


def wrong(code):
    return f"{(int(code) + 1) % 1_000_000:06d}"


@pytest.mark.parametrize(
    ("kind", "minutes"), [("signin", SIGNIN_CODE_TTL_MINUTES), ("removal", DELETE_CODE_TTL_MINUTES)]
)
def test_a_code_is_six_digits_stored_only_as_a_hash_for_its_lifetime(user, clock, kind, minutes):
    code = issue(user, kind)
    slot = codes.SLOTS[kind]
    assert len(code) == 6 and code.isdigit()
    assert code not in getattr(user, slot.hash)
    assert getattr(user, slot.expires_at) == NOW + timedelta(minutes=minutes)
    assert codes.is_pending(user, kind)


@pytest.mark.real_hash
@pytest.mark.parametrize("kind", KINDS)
def test_a_code_is_hashed_with_scrypt(user, clock, kind):
    """What production stores (the tests use a cheap hash, see conftest.fast_code_hashes)."""
    code = issue(user, kind)
    stored = getattr(user, codes.SLOTS[kind].hash)
    assert stored.startswith("scrypt:")
    assert codes.check(user, kind, code) == codes.OK


@pytest.mark.parametrize("kind", KINDS)
def test_the_tests_use_a_cheap_hash(user, clock, kind):
    issue(user, kind)
    assert getattr(user, codes.SLOTS[kind].hash).startswith("pbkdf2:sha256:1$")


@pytest.mark.parametrize("kind", KINDS)
def test_the_code_is_valid_up_to_and_including_its_expiry(user, clock, kind):
    code = issue(user, kind)
    expires_at = getattr(user, codes.SLOTS[kind].expires_at)
    clock.now = expires_at
    assert codes.check(user, kind, code) == codes.OK
    clock.now = expires_at + timedelta(microseconds=1)
    assert not codes.is_pending(user, kind)
    assert codes.check(user, kind, code) == codes.EXPIRED


@pytest.mark.parametrize("kind", KINDS)
def test_no_code_is_expired(user, clock, kind):
    assert not codes.is_pending(user, kind)
    assert codes.check(user, kind, "123456") == codes.EXPIRED


@pytest.mark.parametrize("kind", KINDS)
def test_wrong_codes_count_until_the_code_is_locked(user, clock, kind):
    code = issue(user, kind)
    for attempt in range(1, User.MAX_CODE_ATTEMPTS + 1):
        assert codes.check(user, kind, wrong(code)) == codes.WRONG
        assert getattr(user, codes.SLOTS[kind].attempts) == attempt
    # Locked: even the right code is refused now, and nothing more is counted.
    assert codes.check(user, kind, code) == codes.LOCKED
    assert getattr(user, codes.SLOTS[kind].attempts) == User.MAX_CODE_ATTEMPTS
    # A new code gets a fresh set of attempts.
    code = issue(user, kind)
    assert codes.check(user, kind, code) == codes.OK


@pytest.mark.parametrize("kind", KINDS)
def test_a_wrong_attempt_is_saved(user, clock, kind):
    code = issue(user, kind)
    codes.check(user, kind, wrong(code))
    db.session.expire_all()
    assert getattr(db.session.get(User, user.id), codes.SLOTS[kind].attempts) == 1


@pytest.mark.parametrize("kind", KINDS)
def test_a_new_code_replaces_the_old_one(user, clock, kind):
    old = issue(user, kind)
    new = issue(user, kind)
    if old != new:  # one in a million they are the same
        assert codes.check(user, kind, old) == codes.WRONG
    assert codes.check(user, kind, new) == codes.OK


@pytest.mark.parametrize("kind", KINDS)
def test_clear_forgets_the_code(user, clock, kind):
    code = issue(user, kind)
    codes.check(user, kind, wrong(code))
    codes.clear(user, kind)
    slot = codes.SLOTS[kind]
    assert getattr(user, slot.hash) is None
    assert getattr(user, slot.expires_at) is None
    assert getattr(user, slot.attempts) == 0
    assert codes.check(user, kind, code) == codes.EXPIRED


@pytest.mark.parametrize(("kind", "other"), [("signin", "removal"), ("removal", "signin")])
def test_the_kinds_are_kept_apart(user, clock, kind, other):
    code = issue(user, other)
    issue(user, kind)
    codes.clear(user, kind)
    assert codes.check(user, other, code) == codes.OK


@pytest.mark.parametrize("kind", KINDS)
def test_a_failed_mail_gives_the_reservation_back(user, clock, kind):
    def broken(email, code):
        raise OSError("no mail server")

    reservation = FakeReservation()
    with pytest.raises(OSError):
        codes.issue(user, kind, "someone@example.com", broken, reservation)
    assert reservation.released


@pytest.mark.parametrize("kind", KINDS)
def test_a_sent_mail_keeps_the_reservation(user, clock, kind):
    reservation = FakeReservation()
    codes.issue(user, kind, "someone@example.com", lambda email, code: None, reservation)
    assert not reservation.released


@pytest.mark.parametrize("kind", KINDS)
def test_expired_hash_finds_only_useless_hashes(user, clock, kind):
    issue(user, kind)
    expires_at = getattr(user, codes.SLOTS[kind].expires_at)

    def found(now):
        return db.session.scalars(db.select(User.id).where(codes.expired_hash(kind, now))).all()

    assert found(expires_at) == []
    assert found(expires_at + timedelta(seconds=1)) == [user.id]
    assert codes.hash_column(kind).key == codes.SLOTS[kind].hash
