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

"""Limits on the emailed codes (sign-in and account removal).

Anyone can type any address into the sign-in form, which makes us send a mail to it. Without
limits that is a way to mail-bomb a third party, to ruin the reputation of the mail server
(it carries other mail too), and to try many codes. So before a code is emailed,
`reserve_code()` checks four limits and counts the send against all of them:

* per address: one code per RATELIMIT_COOLDOWN_SECONDS (a double click, a prankster), and at most
  RATELIMIT_ADDRESS_PER_HOUR sign-in codes an hour. Removal codes have their own wait and their
  own RATELIMIT_REMOVAL_PER_HOUR, so signing in and then removing an account do not get in each
  other's way;
* per visitor address: RATELIMIT_IP_PER_HOUR an hour, from the client address in
  `app.security.client_ip()` (a keyed hash of it, a /64 for IPv6). Skipped when the address is
  unknown, never applied to "127.0.0.1" for everybody;
* for the whole site: RATELIMIT_GLOBAL_PER_HOUR an hour, a circuit breaker for the mail reputation.

The limits are on SENDING. A code that was already mailed stays valid and can be entered, so
nobody is locked out of their own address by someone else asking for codes: every send is also
mailed to the owner and the newest code is the valid one. Requests that are refused are not
counted, so they do not extend the wait. A visitor sees the same answer whether or not an
address has an account.

RATELIMIT_MODE: `off` (no checks), `log` (count and log what WOULD be refused, refuse nothing: the
default, for choosing numbers from real traffic) or `enforce`. A limit of 0 turns that limit off.
If the limiter itself fails (a locked database) the request is let through and the failure logged.

The counts live in the `rate_event` table, so they are shared by all gunicorn workers and survive
restarts. Counting and checking is one INSERT ... SELECT per limit, so two workers cannot both slip
under a limit.
"""

import hashlib
import hmac
import ipaddress
import math
from dataclasses import dataclass, field
from datetime import timedelta

from flask import current_app
from flask_babel import ngettext
from sqlalchemy import DateTime, delete, func, insert, literal, select
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.models import RateEvent
from app.security import client_ip, log_event
from app.utils import utcnow

MODES = ("off", "log", "enforce")
HOUR = 3600

_warned_no_address = False


@dataclass
class Decision:
    """The answer of `reserve_code`: may the code be sent, and if not, why and for how long."""

    allowed: bool
    scope: str | None = None  # the limit that refused: cooldown, address, removal, ip or global
    retry_after: int = 0  # seconds until that limit lets a send through
    _event_ids: list = field(default_factory=list, repr=False)

    @property
    def retry_minutes(self):
        """`retry_after` as whole minutes, at least one (what a visitor is told)."""
        return max(1, math.ceil(self.retry_after / 60))

    def release(self):
        """Give the reservation back, because the mail could not be sent after all."""
        if not self._event_ids:
            return
        try:
            db.session.execute(delete(RateEvent).where(RateEvent.id.in_(self._event_ids)))
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Could not give back a rate limit reservation")
        self._event_ids = []


def mode():
    """The configured mode (`off`, `log` or `enforce`)."""
    return current_app.config["RATELIMIT_MODE"]


def refused_message(decision):
    """The translated text for a refused request. It does not say which limit refused."""
    minutes = decision.retry_minutes
    # NOTE: Shown when too many sign-in codes were requested. %(num)d is a number of minutes.
    return ngettext(
        "Too many requests. Try again in %(num)d minute.",
        "Too many requests. Try again in %(num)d minutes.",
        minutes,  # Flask-Babel fills in %(num)d from this itself
    )


def _network_subject(address):
    """A keyed hash of the visitor's network: the address, or the /64 for IPv6.

    Keyed with SECRET_KEY, so the table holds no address that could be looked up, and a whole
    IPv6 household shares one budget instead of rotating through its addresses.
    """
    parsed = ipaddress.ip_address(address)
    network = parsed if parsed.version == 4 else ipaddress.ip_network(f"{parsed}/64", strict=False)
    secret = str(current_app.config["SECRET_KEY"]).encode()
    return hmac.new(secret, str(network).encode(), hashlib.sha256).hexdigest()[:32]


def _scopes(kind, email_hash):
    """The limits one code send counts against: (scope, event kind, subject, limit, window seconds)."""
    config = current_app.config
    # Sign-in and removal codes have their own wait and hourly count: signing in and asking for the
    # removal code a few seconds later (they are different mails) must not trip a limit.
    if kind == "removal":
        scopes = [
            ("cooldown", "removal-wait", email_hash, 1, config["RATELIMIT_COOLDOWN_SECONDS"]),
            ("removal", "removal-hour", email_hash, config["RATELIMIT_REMOVAL_PER_HOUR"], HOUR),
        ]
    else:
        scopes = [
            ("cooldown", "cooldown", email_hash, 1, config["RATELIMIT_COOLDOWN_SECONDS"]),
            ("address", "address-hour", email_hash, config["RATELIMIT_ADDRESS_PER_HOUR"], HOUR),
        ]

    address = client_ip()
    if address:
        scopes.append(("ip", "ip-hour", _network_subject(address), config["RATELIMIT_IP_PER_HOUR"], HOUR))
    else:
        _warn_no_address()
    scopes.append(("global", "global-hour", "*", config["RATELIMIT_GLOBAL_PER_HOUR"], HOUR))
    # 0 means "this limit is off"
    return [s for s in scopes if s[3] > 0 and s[4] > 0]


def _warn_no_address():
    """Say once that the per-address limit is off because the visitor's address is unknown."""
    global _warned_no_address
    if not _warned_no_address:
        _warned_no_address = True
        current_app.logger.warning(
            "Rate limits: the visitor's address is unknown (no X-Real-IP header from the proxy?), "
            "so the per-address limit is not applied. See the README."
        )


def _in_window(event_kind, subject, window, now):
    """SQL conditions: the events of `event_kind` for `subject` in the last `window` seconds."""
    since = now - timedelta(seconds=window)
    return RateEvent.kind == event_kind, RateEvent.subject == subject, RateEvent.at > since


def _insert_if_below(event_kind, subject, limit, window, now):
    """Count one event, unless `limit` events already lie in the last `window` seconds.

    One statement, so the check and the insert cannot be separated by another worker. Returns
    the new row's id, or None when the limit is reached.
    """
    in_window = (
        select(func.count())
        .select_from(RateEvent)
        .where(*_in_window(event_kind, subject, window, now))
        .scalar_subquery()
    )
    statement = insert(RateEvent).from_select(
        ["kind", "subject", "at"],
        select(literal(event_kind), literal(subject), literal(now, DateTime)).where(in_window < limit),
    )
    result = db.session.execute(statement)
    return result.lastrowid if result.rowcount == 1 else None


def _retry_after(event_kind, subject, window, now):
    """Seconds until the oldest event in the window leaves it and a slot opens up."""
    oldest = db.session.scalar(select(func.min(RateEvent.at)).where(*_in_window(event_kind, subject, window, now)))
    if oldest is None:
        return window
    return max(1, math.ceil((oldest + timedelta(seconds=window) - now).total_seconds()))


def _reserve(scopes, now):
    """Count a send against every limit, or against none if any of them is reached."""
    event_ids = []
    for scope, event_kind, subject, limit, window in scopes:
        event_id = _insert_if_below(event_kind, subject, limit, window, now)
        if event_id is None:
            db.session.rollback()  # undo what this attempt counted so far
            return Decision(False, scope, _retry_after(event_kind, subject, window, now))
        event_ids.append(event_id)
    db.session.commit()
    return Decision(True, _event_ids=event_ids)


def reserve_code(kind, email_hash):
    """Check the limits for emailing a code and count the send; returns a `Decision`.

    `kind` is "signin" or "removal"; `email_hash` identifies the address. In `enforce` mode a
    refused decision must be honoured; in `log` mode the decision is always allowed and a refusal
    is only logged. If the mail then fails, call `decision.release()` so the failed send does not
    count.
    """
    if mode() == "off":
        return Decision(True)
    try:
        decision = _reserve(_scopes(kind, email_hash), utcnow())
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Rate limiting failed; letting the request through")
        return Decision(True)

    if decision.allowed:
        return decision
    enforcing = mode() == "enforce"
    log_event("auth", event="code-refused" if enforcing else "code-would-refuse", scope=decision.scope)
    return decision if enforcing else Decision(True)


def init_app(app):
    """Check the configuration; an unknown mode falls back to `log`, which refuses nothing."""
    if app.config.get("RATELIMIT_MODE") not in MODES:
        app.logger.warning(
            "RATELIMIT_MODE=%r is not one of %s; using 'log'.", app.config.get("RATELIMIT_MODE"), "/".join(MODES)
        )
        app.config["RATELIMIT_MODE"] = "log"
    if app.config["RATELIMIT_MODE"] == "off" and not app.testing:
        app.logger.warning("RATELIMIT_MODE=off: emailed codes are not rate limited.")
