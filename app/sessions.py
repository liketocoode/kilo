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

"""The session on every request: who is signed in, and for how long.

`get_current_user` and `login_required` are what the views use; `keep_session_alive` runs
before every request (it slides the 30-day cookie, enforces the 90-day cap and "Sign out
everywhere"), and `inject_current_user` gives every template the user, the address, the
theme and the language. Signing in itself is app/auth.py.
"""

import time
from functools import wraps

from flask import current_app, flash, redirect, request, session, url_for
from flask_babel import get_locale, gettext as _

from app import db
from app.models import User


def init_app(app):
    """Register the per-request hook and the template context. Last of the request hooks, as
    when they belonged to the auth blueprint: CSRF protection and the language come first."""
    app.before_request(keep_session_alive)
    app.context_processor(inject_current_user)


def get_current_user():
    """The signed-in `User` for this request, or None."""
    user_id = session.get("user_id")
    if user_id is None:
        return None
    return db.session.get(User, user_id)


def login_required(view):
    """Redirect to the front page unless a user is signed in."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if get_current_user() is None:
            flash(_("Enter your email to sign in."), "error")
            return redirect(url_for("main.index"))
        return view(*args, **kwargs)

    return wrapped


def keep_session_alive():
    """Keep signed-in users signed in across browser restarts, but not forever.

    A signed-in session is marked permanent, so its cookie gets an expiry date
    (PERMANENT_SESSION_LIFETIME, 30 days) that is pushed forward on every visit, and it
    survives closing the browser. `login_at` caps that: 90 days after the sign-in
    (SESSION_ABSOLUTE_LIFETIME) the user has to sign in again, however active they are, so a
    copied cookie cannot be kept alive for ever. Sessions from before this existed (no
    `login_at`) are adopted: their 90 days start at the first visit.

    A session whose `session_version` is older than the user's has been signed out by "Sign out
    everywhere" (account.sign_out_everywhere). A session without one counts as 0, never as the
    current number: adopting it would let a copied cookie from before that survive.
    """
    if "user_id" not in session or request.endpoint == "static":
        return
    user = db.session.get(User, session["user_id"])
    if user is not None and session.get("session_version", 0) != user.session_version:
        session.clear()
        return
    now = int(time.time())
    login_at = session.get("login_at")
    if login_at is None:
        session["login_at"] = now
    elif now - login_at > current_app.config["SESSION_ABSOLUTE_LIFETIME"].total_seconds():
        session.clear()  # the page then asks for the email address again, as for any visitor
        return
    session.permanent = True


def inject_current_user():
    """Template context for every page: the user, their email, the theme and the language."""
    user = get_current_user()
    return {
        "current_locale": str(get_locale()),
        "current_user": user,
        "current_email": session.get("email"),
        "theme": "dark" if user and user.dark_mode else "light",
    }
