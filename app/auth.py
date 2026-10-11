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
signed-in user. Also holds the `login_required` decorator and the template
context shared by all pages.
"""

import secrets
import time
from datetime import timedelta
from functools import wraps

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_babel import get_locale, gettext as _
from werkzeug.security import check_password_hash, generate_password_hash

from app import db, ratelimit
from app.email_utils import send_verification_email
from app.errors import back_url
from app.forms import SignupForm, VerifyCodeForm
from app.models import User
from app.security import log_event
from app.utils import SIGNIN_CODE_TTL_MINUTES, hash_email, normalize_email, utcnow
from app.version import app_version

bp = Blueprint("auth", __name__)


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


@bp.before_app_request
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


@bp.app_context_processor
def inject_current_user():
    """Template context for every page: the user, their email, the theme and the language."""
    user = get_current_user()
    return {
        "current_locale": str(get_locale()),
        "current_user": user,
        "current_email": session.get("email"),
        "theme": "dark" if user and user.dark_mode else "light",
    }


def _issue_code(user, email, reservation):
    """Create a new sign-in code for `user`: store its hash and expiry, then email it.

    `reservation` is what the rate limit counted for this send; it is given back when the mail
    cannot be sent, so a failed send does not use up the visitor's allowance.
    """
    code = f"{secrets.randbelow(1_000_000):06d}"
    user.code_hash = generate_password_hash(code)
    user.code_expires_at = utcnow() + timedelta(minutes=SIGNIN_CODE_TTL_MINUTES)
    user.code_attempts = 0
    db.session.commit()
    try:
        send_verification_email(email, code)
    except Exception:
        reservation.release()
        raise


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
            session["pending_email"] = email
            flash(_("We sent a verification code to %(email)s.", email=email), "success")
            return redirect(url_for("auth.verify"))

        user = User.query.filter_by(email_hash=email_hash).first()
        if user is None:
            user = User(email_hash=email_hash)
            db.session.add(user)
            db.session.commit()

        _issue_code(user, email, reservation)
        session["pending_email"] = email
        flash(_("We sent a verification code to %(email)s.", email=email), "success")
        return redirect(url_for("auth.verify"))

    return render_template("signup.html", form=form)


@bp.route("/verify", methods=["GET", "POST"])
def verify():
    """Check the emailed code and sign the user in. Codes expire and allow a limited number of attempts."""
    email = session.get("pending_email")
    if not email:
        flash(_("Enter your email to get a verification code."), "error")
        return redirect(url_for("auth.signup"))

    user = User.query.filter_by(email_hash=hash_email(email)).first()
    if user is None:
        session.pop("pending_email", None)
        return redirect(url_for("auth.signup"))

    form = VerifyCodeForm()
    if form.validate_on_submit():
        if not user.code_hash or not user.code_expires_at or user.code_expires_at < utcnow():
            flash(_("That code has expired. Request a new one."), "error")
        elif user.code_attempts >= User.MAX_CODE_ATTEMPTS:
            flash(_("Too many attempts. Request a new code."), "error")
        elif not check_password_hash(user.code_hash, form.code.data):
            user.code_attempts += 1
            db.session.commit()
            log_event("auth", event="code-wrong")
            flash(_("Incorrect code."), "error")
        else:
            user.verified_at = user.verified_at or utcnow()
            user.code_hash = None
            user.code_expires_at = None
            user.code_attempts = 0
            db.session.commit()
            session.pop("pending_email", None)
            session["user_id"] = user.id
            session["email"] = email
            session["login_at"] = int(time.time())
            session["session_version"] = user.session_version
            session.permanent = True  # the cookie outlives the browser window, see keep_session_alive
            flash(_("Email verified — you're signed in."), "success")
            return redirect(url_for("main.index"))

    return render_template(
        "verify.html", form=form, email=email, ttl_minutes=SIGNIN_CODE_TTL_MINUTES
    )


@bp.route("/verify/resend", methods=["POST"])
def resend_code():
    """Send a fresh sign-in code to the email address being verified."""
    email = session.get("pending_email")
    if not email:
        return redirect(url_for("auth.signup"))

    user = User.query.filter_by(email_hash=hash_email(email)).first()
    if user is None:
        session.pop("pending_email", None)
        return redirect(url_for("auth.signup"))

    reservation = ratelimit.reserve_code("signin", user.email_hash)
    if not reservation.allowed:
        flash(ratelimit.refused_message(reservation), "error")
        return redirect(url_for("auth.verify"))

    _issue_code(user, email, reservation)
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
