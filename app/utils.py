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

"""Small shared helpers: email hashing, decimal parsing, code lifetimes and UTC time."""

import hashlib
import re
from datetime import UTC, datetime

# A plain decimal number: digits with at most one decimal separator, a point or
# a comma (most of our languages write 72,5). Stricter than float(), which also
# accepts "1e2", "1_0", "nan" and "inf". No thousands separators.
DECIMAL_RE = re.compile(r"^(\d+([.,]\d*)?|[.,]\d+)$")


def parse_decimal(raw):
    """Parse a user-typed number such as "72.5" or "72,5" into a float.

    Surrounding whitespace is ignored. Returns None for anything that is not a
    plain decimal with at most one separator, so "1,234.5", "1e2" and "nan" are
    all rejected. A lone "1,234" is read as 1.234, never as a thousand.
    """
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not DECIMAL_RE.match(raw):
        return None
    return float(raw.replace(",", "."))

# How long an emailed code stays valid. Deleting an account is destructive,
# so its confirmation code gets a shorter window than the sign-in code.
SIGNIN_CODE_TTL_MINUTES = 30
DELETE_CODE_TTL_MINUTES = 10

# An account that was never verified and has been idle this long is removed by
# the daily cleanup (see app/cleanup.py).
STALE_SIGNUP_DAYS = 7

# The longest note a day can have, in characters. The note editor, the JSON API and every
# importer cut a note to this length; templates see it as NOTE_MAX_LENGTH.
NOTE_MAX_LENGTH = 280


def bmi(weight_kg, height_cm):
    """Body-mass index of a weight (kg) at a height (cm)."""
    return weight_kg / (height_cm / 100) ** 2


def normalize_email(email):
    """Trim and lowercase an email so equivalent addresses hash the same."""
    return email.strip().lower()


def hash_email(email):
    """SHA-256 of the normalized email — matches Gravatar's hash spec
    (https://docs.gravatar.com/rest/hash/) so it doubles as the avatar id."""
    return hashlib.sha256(normalize_email(email).encode("utf-8")).hexdigest()


def utcnow():
    """Current UTC time as a naive datetime (matches the naive DateTime columns in SQLite)."""
    # Naive UTC, matching the naive DateTime columns stored in SQLite.
    return datetime.now(UTC).replace(tzinfo=None)
