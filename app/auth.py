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

"""Passwordless email sign-in.

Users enter their email and get a 6-digit code (valid for
SIGNIN_CODE_TTL_MINUTES); entering it signs them in. Only a SHA-256 hash of the
email is stored - the plaintext address lives only in the session of the
signed-in user. What every request needs from the session (the current user,
`login_required`, keeping a session alive) is in app/sessions.py.
"""

import time

from flask import Blueprint, flash, redirect, render_template, session, url_for
from flask_babel import gettext as _

from app import codes, db, ratelimit
from app.email_utils import send_verification_email
from app.errors import back_url
from app.forms import SignupForm, VerifyCodeForm
from app.models import User
from app.security import log_event
from app.utils import SIGNIN_CODE_TTL_MINUTES, hash_email, normalize_email, utcnow
from app.version import app_version

bp = Blueprint("auth", __name__)


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    """Show the email form; on submit, email a code and go to the verify page."""
    form = SignupForm()
    if form.validate_on_submit():
        email = normalize_email(form.email.data)
        email_hash = hash_email(email)

        # Before anything is created or sent. A refused request leaves no account row behind.
        reservation = ratelimit.reserve_code("signin", email_hash)
        if not reservation.allowed:
            if reservation.scope != "cooldown":
                flash(ratelimit.refused_message(reservation), "error")
                return redirect(back_url())
            # A code was mailed less than a minute ago: a double click, or someone else asking for
            # this address. Do not send another one and do not replace the one in the mailbox
            # (that would invalidate it); answer exactly as for a fresh send.
            return _to_verify(email)

        user = User.query.filter_by(email_hash=email_hash).first()
        if user is None:
            user = User(email_hash=email_hash)
            db.session.add(user)
            db.session.commit()

        codes.issue(user, "signin", email, send_verification_email, reservation)
        return _to_verify(email)

    return render_template("signup.html", form=form)


def _to_verify(email):
    """After a code was sent (or one is on its way) to `email`: remember the address and go to
    the page that asks for the code."""
    session["pending_email"] = email
    flash(_("We sent a verification code to %(email)s.", email=email), "success")
    return redirect(url_for("auth.verify"))


def _pending_user():
    """(address, user) of the sign-in in progress: (None, None) without one. The address is
    forgotten when its account is gone (the cleanup removed it), with user None."""
    email = session.get("pending_email")
    if not email:
        return None, None
    user = User.query.filter_by(email_hash=hash_email(email)).first()
    if user is None:
        session.pop("pending_email", None)
    return email, user


@bp.route("/verify", methods=["GET", "POST"])
def verify():
    """Check the emailed code and sign the user in. Codes expire and allow a limited number of attempts."""
    email, user = _pending_user()
    if user is None:
        if not email:
            flash(_("Enter your email to get a verification code."), "error")
        return redirect(url_for("auth.signup"))

    form = VerifyCodeForm()
    if form.validate_on_submit():
        result = codes.check(user, "signin", form.code.data)
        if result == codes.EXPIRED:
            flash(_("That code has expired. Request a new one."), "error")
        elif result == codes.LOCKED:
            flash(_("Too many attempts. Request a new code."), "error")
        elif result == codes.WRONG:
            log_event("auth", event="code-wrong")
            flash(_("Incorrect code."), "error")
        else:
            user.verified_at = user.verified_at or utcnow()
            codes.clear(user, "signin")
            db.session.commit()
            session.pop("pending_email", None)
            session["user_id"] = user.id
            session["email"] = email
            session["login_at"] = int(time.time())
            session["session_version"] = user.session_version
            session.permanent = True  # the cookie outlives the browser window, see sessions.keep_session_alive
            flash(_("Email verified — you're signed in."), "success")
            return redirect(url_for("main.index"))

    return render_template(
        "verify.html", form=form, email=email, ttl_minutes=SIGNIN_CODE_TTL_MINUTES
    )


@bp.route("/verify/resend", methods=["POST"])
def resend_code():
    """Send a fresh sign-in code to the email address being verified."""
    email, user = _pending_user()
    if user is None:
        return redirect(url_for("auth.signup"))

    reservation = ratelimit.reserve_code("signin", user.email_hash)
    if not reservation.allowed:
        flash(ratelimit.refused_message(reservation), "error")
        return redirect(url_for("auth.verify"))

    codes.issue(user, "signin", email, send_verification_email, reservation)
    flash(_("We sent a new code to %(email)s.", email=email), "success")
    return redirect(url_for("auth.verify"))


@bp.route("/logout", methods=["POST"])
def logout():
    """Sign out: forget everything in the session (the user, their address, the export flag, ...)."""
    session.clear()
    flash(_("Signed out."), "success")
    return redirect(url_for("main.index"))


@bp.route("/about")
def about():
    """The About page: an introduction and how sign-in works."""
    return render_template(
        "about.html", ttl_minutes=SIGNIN_CODE_TTL_MINUTES, version=app_version()
    )
