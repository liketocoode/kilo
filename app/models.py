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

"""Database models: `WeightEntry`, `User` and `RateEvent`."""

from datetime import date

from app import db
from app.utils import utcnow


class WeightEntry(db.Model):
    """One weight (in kg) recorded by a user for a calendar day; unique per user and date."""
    __table_args__ = (db.UniqueConstraint("user_id", "entry_date", name="uq_user_entry_date"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    entry_date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    weight = db.Column(db.Float, nullable=False)
    note = db.Column(db.String(280), nullable=True)

    def __repr__(self):
        return f"<WeightEntry user={self.user_id} {self.entry_date} {self.weight}>"


class User(db.Model):
    """A registered user.

    Identified by a SHA-256 hash of their email (never the address itself). Holds
    the sign-in and account-removal codes plus display settings.
    """
    MAX_CODE_ATTEMPTS = 5
    HEIGHT_UNITS = ("cm", "m")

    id = db.Column(db.Integer, primary_key=True)
    # SHA-256 of the trimmed, lowercased email (the same hash Gravatar's REST
    # API uses, see https://docs.gravatar.com/rest/hash/). We never persist
    # the plaintext address so a database breach doesn't expose it.
    email_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    verified_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    code_hash = db.Column(db.String(255), nullable=True)
    code_expires_at = db.Column(db.DateTime, nullable=True)
    code_attempts = db.Column(db.Integer, nullable=False, default=0)

    # Separate from the sign-in code above: confirms the "remove my data"
    # request, so a sign-up attempt for this email can't clobber it.
    delete_code_hash = db.Column(db.String(255), nullable=True)
    delete_code_expires_at = db.Column(db.DateTime, nullable=True)
    delete_code_attempts = db.Column(db.Integer, nullable=False, default=0)

    height_cm = db.Column(db.Float, nullable=True)
    height_unit = db.Column(db.String(2), nullable=False, default="cm")
    dark_mode = db.Column(db.Boolean, nullable=False, default=False)
    chart_range = db.Column(db.String(4), nullable=False, default="all")
    moving_avg_days = db.Column(db.Integer, nullable=False, default=10)
    # Raised by "Sign out everywhere": a session cookie that carries an older number is no longer
    # accepted (see auth.keep_session_alive). Cookies from before this column count as 0.
    session_version = db.Column(db.Integer, nullable=False, default=0)

    # The public chart at /public/<public_id> (see routes.public_chart). The id is kept when
    # sharing is turned off, so turning it on again brings back the same address; it is
    # lowercase and unique among all users, shared or not.
    public_enabled = db.Column(db.Boolean, nullable=False, default=False)
    public_id = db.Column(db.String(64), nullable=True, unique=True, index=True)
    # Whether the public chart shows the stat boxes under it; off by default: only the chart.
    public_show_stats = db.Column(db.Boolean, nullable=False, default=False)
    # Whether the public chart shows the weights. Off: only the shape of the line (see
    # routes._without_weights), and never the stat boxes, whatever public_show_stats says.
    public_show_weight = db.Column(db.Boolean, nullable=False, default=True)

    entries = db.relationship(
        "WeightEntry", backref="user", cascade="all, delete-orphan", lazy="dynamic"
    )

    @property
    def is_verified(self):
        """True once the user has entered a correct emailed code."""
        return self.verified_at is not None

    def __repr__(self):
        return f"<User {self.email_hash[:8]}…>"


class RateEvent(db.Model):
    """One counted action (a code email that was sent), for the rate limits in app/ratelimit.py.

    Short-lived: nothing needs a row for longer than the longest window (an hour), and the daily
    cleanup removes the rest. `subject` is a hash (of an address, or a keyed hash of a network),
    never plaintext.
    """

    __table_args__ = (db.Index("ix_rate_event_lookup", "kind", "subject", "at"),)

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(16), nullable=False)  # which limit: "cooldown", "address-hour", ...
    subject = db.Column(db.String(64), nullable=False)  # who it counts against
    at = db.Column(db.DateTime, nullable=False)

    def __repr__(self):
        return f"<RateEvent {self.kind} {self.at}>"
