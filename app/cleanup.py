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

"""Housekeeping for the user table, run daily by scripts/cleanup_signups.py.

Anyone can create an account row by typing an address into the sign-in form, and
nothing else ever removes it. This removes

* **stale sign-ups**: accounts that were never verified and have been idle for
  STALE_SIGNUP_DAYS. Idle is measured from the LATER of the creation time and the
  moment the latest sign-in code was issued, so someone who signed up weeks ago and
  asked for a new code today keeps the row while typing it. A verified account is
  never touched, however long ago its owner signed in, and neither is one that has
  weight entries;
* **expired code hashes**: a sign-in or account-removal code that is past its expiry
  is useless but its hash would otherwise stay in the row until the next code;
* **old rate-limit events**: the counts behind the limits on emailed codes (app/ratelimit.py)
  are only needed for an hour; anything older than a day is deleted;
* **cached avatars** (app/avatar.py) of accounts that no longer exist, and any that has not
  been refreshed for a month.

Each part is a single SQL statement with all its conditions in it, so there is no moment
between "looks stale" and "deleted" in which a code could be verified. Running it twice,
or at any time of day, is safe.
"""

import argparse
import sys
from datetime import UTC, timedelta

from sqlalchemy import and_, delete, exists, func, or_, select, update

from app import avatar, codes, db
from app.models import RateEvent, User, WeightEntry
from app.utils import SIGNIN_CODE_TTL_MINUTES, STALE_SIGNUP_DAYS, utcnow

# Rate-limit counts older than this are of no use any more (the longest window is an hour).
RATE_EVENT_KEEP = timedelta(hours=24)


def _stale_signup(now, stale_days):
    """The condition for "an account nobody has verified or used for `stale_days` days"."""
    cutoff = now - timedelta(days=stale_days)
    # The latest code was issued at code_expires_at minus its lifetime, so "issued before the
    # cutoff" is "expires before the cutoff plus the lifetime".
    latest_code_is_old = or_(
        User.code_expires_at.is_(None),
        User.code_expires_at < cutoff + timedelta(minutes=SIGNIN_CODE_TTL_MINUTES),
    )
    return and_(
        User.verified_at.is_(None),
        User.created_at < cutoff,
        latest_code_is_old,
        # An unverified account cannot have entries (they need a signed-in session); the
        # check is a second line of defence against deleting someone's data.
        ~exists().where(WeightEntry.user_id == User.id),
    )


def _count(condition):
    return db.session.scalar(select(func.count()).select_from(User).where(condition))


def cleanup(now=None, dry_run=False, stale_days=STALE_SIGNUP_DAYS):
    """Remove stale sign-ups and clear expired code hashes; returns how many of each.

    With `dry_run` nothing is changed and the numbers say what would be. `now` is for tests.
    Needs an application context.
    """
    now = now or utcnow()
    stale = _stale_signup(now, stale_days)
    signin = codes.expired_hash("signin", now)
    removal = codes.expired_hash("removal", now)

    old_events = RateEvent.at < now - RATE_EVENT_KEEP

    if dry_run:
        # Count the hashes as they would be AFTER the stale rows are gone, like the real run.
        return {
            "stale_signups": _count(stale),
            "signin_hashes": _count(and_(signin, ~stale)),
            "removal_hashes": _count(and_(removal, ~stale)),
            "rate_events": db.session.scalar(select(func.count()).select_from(RateEvent).where(old_events)),
            "avatars": avatar.prune(_known_hashes(exclude=stale), now=_timestamp(now), dry_run=True),
        }

    removed = db.session.execute(delete(User).where(stale)).rowcount
    signin_cleared = db.session.execute(
        update(User).where(signin).values({codes.hash_column("signin"): None})
    ).rowcount
    removal_cleared = db.session.execute(
        update(User).where(removal).values({codes.hash_column("removal"): None})
    ).rowcount
    events_removed = db.session.execute(delete(RateEvent).where(old_events)).rowcount
    db.session.commit()
    return {
        "stale_signups": removed,
        "signin_hashes": signin_cleared,
        "removal_hashes": removal_cleared,
        "rate_events": events_removed,
        "avatars": avatar.prune(_known_hashes(), now=_timestamp(now)),
    }


def _timestamp(now):
    """A naive UTC datetime as a Unix time, for comparing with file modification times."""
    return now.replace(tzinfo=UTC).timestamp()


def _known_hashes(exclude=None):
    """The email hashes of all accounts (without those matching `exclude`, for a dry run)."""
    query = select(User.email_hash)
    if exclude is not None:
        # Not `~exclude`: a condition that is NULL for a row (no code issued) would drop that row too.
        query = query.where(User.id.not_in(select(User.id).where(exclude)))
    return set(db.session.scalars(query))


def main(argv=None, app=None):
    """Command line entry point; prints what was done and returns the exit status."""
    parser = argparse.ArgumentParser(
        description="Remove sign-ups that were never verified and clear expired code hashes."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="only print what would be removed or cleared"
    )
    parser.add_argument(
        "--stale-days",
        type=int,
        default=STALE_SIGNUP_DAYS,
        help=f"days an unverified account may sit idle (default {STALE_SIGNUP_DAYS})",
    )
    args = parser.parse_args(argv)
    if args.stale_days < 1:
        parser.error("--stale-days must be at least 1")

    if app is None:
        from app import create_app

        app = create_app()
    with app.app_context():
        result = cleanup(dry_run=args.dry_run, stale_days=args.stale_days)

    verb = "Would remove" if args.dry_run else "Removed"
    clear = "Would clear" if args.dry_run else "Cleared"
    print(f"{verb} {result['stale_signups']} stale sign-up(s) (unverified, idle for {args.stale_days}+ days).")
    print(f"{clear} {result['signin_hashes']} expired sign-in code hash(es).")
    print(f"{clear} {result['removal_hashes']} expired account-removal code hash(es).")
    print(f"{verb} {result['rate_events']} old rate-limit count(s).")
    print(f"{verb} {result['avatars']} cached avatar file(s) of removed or long-inactive accounts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
