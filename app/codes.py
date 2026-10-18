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

"""The emailed one-time codes: issuing one, checking an entered one and clearing it.

There are two kinds, each with its own columns on `User` so one cannot clobber the other:
"signin" (auth.py) and "removal" (the "Remove my data" confirmation, account.py). The kinds
are the same names the rate limit uses (ratelimit.reserve_code). Only a Werkzeug scrypt hash
of a code is stored; a code expires after its lifetime and allows User.MAX_CODE_ATTEMPTS
wrong tries. The callers keep their own messages and security-log event names.
"""

import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import and_, or_
from werkzeug.security import check_password_hash, generate_password_hash

from app import db
from app.models import User
from app.utils import DELETE_CODE_TTL_MINUTES, SIGNIN_CODE_TTL_MINUTES, utcnow


@dataclass(frozen=True)
class _Slot:
    """Where a kind of code lives on `User`, and how long it is valid."""

    hash: str
    expires_at: str
    attempts: str
    ttl_minutes: int


SLOTS = {
    "signin": _Slot("code_hash", "code_expires_at", "code_attempts", SIGNIN_CODE_TTL_MINUTES),
    "removal": _Slot(
        "delete_code_hash", "delete_code_expires_at", "delete_code_attempts", DELETE_CODE_TTL_MINUTES
    ),
}

# The answers of check().
EXPIRED, LOCKED, WRONG, OK = "expired", "locked", "wrong", "ok"


def issue(user, kind, email, send, reservation):
    """Store a new 6-digit code of `kind` for `user` (its hash and expiry), then mail it.

    `send(email, code)` mails it. `reservation` is what the rate limit counted for this send; it
    is given back when the mail cannot be sent, so a failed send does not use up the allowance.
    A new code replaces an older one of the same kind and gets a fresh set of attempts.
    """
    slot = SLOTS[kind]
    code = f"{secrets.randbelow(1_000_000):06d}"
    setattr(user, slot.hash, generate_password_hash(code))
    setattr(user, slot.expires_at, utcnow() + timedelta(minutes=slot.ttl_minutes))
    setattr(user, slot.attempts, 0)
    db.session.commit()
    try:
        send(email, code)
    except Exception:
        reservation.release()
        raise


def is_pending(user, kind):
    """True while `user` has a code of `kind` that has not expired (it may be locked)."""
    slot = SLOTS[kind]
    expires_at = getattr(user, slot.expires_at)
    return bool(getattr(user, slot.hash) and expires_at and expires_at >= utcnow())


def check(user, kind, entered):
    """Check an entered code: EXPIRED, LOCKED (too many wrong tries), WRONG or OK.

    A wrong code counts as an attempt (committed here). OK leaves the code in place: the caller
    clears it (clear()) or removes the whole account.
    """
    slot = SLOTS[kind]
    if not is_pending(user, kind):
        return EXPIRED
    attempts = getattr(user, slot.attempts)
    if attempts >= User.MAX_CODE_ATTEMPTS:
        return LOCKED
    if not check_password_hash(getattr(user, slot.hash), entered):
        setattr(user, slot.attempts, attempts + 1)
        db.session.commit()
        return WRONG
    return OK


def clear(user, kind):
    """Forget the code of `kind` (after it was used); the caller commits."""
    slot = SLOTS[kind]
    setattr(user, slot.hash, None)
    setattr(user, slot.expires_at, None)
    setattr(user, slot.attempts, 0)


def hash_column(kind):
    """The `User` column holding the hash of a code of `kind`, for SQL (cleanup.py)."""
    return getattr(User, SLOTS[kind].hash)


def expired_hash(kind, now):
    """SQL condition: the row still holds a hash of `kind` that is useless at `now`."""
    expires_at = getattr(User, SLOTS[kind].expires_at)
    return and_(hash_column(kind).is_not(None), or_(expires_at.is_(None), expires_at < now))
