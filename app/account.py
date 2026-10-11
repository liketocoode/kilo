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

"""Account actions: "Sign out everywhere" and data removal ("Remove my data").

Removal is a three-step flow behind /settings/remove: the user first downloads their data
(the CSV export sets a flag in the session), then requests a confirmation code
by email, and finally enters that code to permanently delete their account and
all their weight entries.
"""

import secrets
from datetime import timedelta

from flask import Blueprint, flash, redirect, render_template, session, url_for
from flask_babel import gettext as _
from werkzeug.security import check_password_hash, generate_password_hash

from app import avatar, db, ratelimit
from app.auth import get_current_user, login_required
from app.email_utils import send_deletion_email
from app.forms import VerifyCodeForm
from app.models import User
from app.security import log_event
from app.utils import DELETE_CODE_TTL_MINUTES, utcnow

bp = Blueprint("account", __name__)


@bp.route("/settings/sign-out-everywhere", methods=["POST"])
@login_required
def sign_out_everywhere():
    """Sign out every other browser and device; this one stays signed in.

    Sessions are signed cookies with no list of them on the server, so each one carries the
    user's `session_version` from its sign-in; raising the number makes every older cookie
    invalid at its next request (see auth.keep_session_alive). This session takes the new number.
    """
    user = get_current_user()
    user.session_version += 1
    db.session.commit()
    session["session_version"] = user.session_version
    flash(_("You are now signed out on all other browsers and devices."), "success")
    return redirect(url_for("main.settings"))


def _code_pending(user):
    """True if the user has an unexpired removal code waiting to be entered."""
    return bool(
        user.delete_code_hash
        and user.delete_code_expires_at
        and user.delete_code_expires_at >= utcnow()
    )


@bp.route("/settings/remove", methods=["GET"])
@login_required
def remove_data():
    """Show the removal page: the download step, then the confirmation step."""
    user = get_current_user()
    return render_template(
        "remove_data.html",
        form=VerifyCodeForm(),
        exported=bool(session.get("data_exported")),
        code_pending=_code_pending(user),
        email=session.get("email"),
        ttl_minutes=DELETE_CODE_TTL_MINUTES,
    )


@bp.route("/settings/remove/send-code", methods=["POST"])
@login_required
def send_removal_code():
    """Email a new confirmation code. Needs a data download earlier in this session."""
    user = get_current_user()
    email = session.get("email")
    if not session.get("data_exported"):
        flash(_("Download your data first."), "error")
        return redirect(url_for("account.remove_data"))
    if not email:
        flash(_("Sign in again to confirm this request."), "error")
        return redirect(url_for("account.remove_data"))

    reservation = ratelimit.reserve_code("removal", user.email_hash)
    if not reservation.allowed:
        flash(ratelimit.refused_message(reservation), "error")
        return redirect(url_for("account.remove_data"))

    code = f"{secrets.randbelow(1_000_000):06d}"
    user.delete_code_hash = generate_password_hash(code)
    user.delete_code_expires_at = utcnow() + timedelta(minutes=DELETE_CODE_TTL_MINUTES)
    user.delete_code_attempts = 0
    db.session.commit()
    try:
        send_deletion_email(email, code)
    except Exception:
        reservation.release()  # a failed send does not use up the allowance
        raise
    flash(_("We sent a confirmation code to %(email)s.", email=email), "success")
    return redirect(url_for("account.remove_data"))


@bp.route("/settings/remove/confirm", methods=["POST"])
@login_required
def confirm_removal():
    """Delete the account and all its data if the emailed code is correct.

    Refuses if the data wasn't downloaded in this session, the code has expired,
    or there were too many wrong attempts.
    """
    user = get_current_user()
    form = VerifyCodeForm()
    if not session.get("data_exported"):
        flash(_("Download your data first."), "error")
    elif not form.validate_on_submit():
        flash(_("Enter the 6-digit code."), "error")
    elif not _code_pending(user):
        flash(_("That code has expired. Request a new one."), "error")
    elif user.delete_code_attempts >= User.MAX_CODE_ATTEMPTS:
        flash(_("Too many attempts. Request a new code."), "error")
    elif not check_password_hash(user.delete_code_hash, form.code.data):
        user.delete_code_attempts += 1
        db.session.commit()
        log_event("auth", event="removal-code-wrong")
        flash(_("Incorrect code."), "error")
    else:
        # WeightEntry rows go with the user (cascade="all, delete-orphan").
        email_hash = user.email_hash
        db.session.delete(user)
        db.session.commit()
        avatar.forget(email_hash)
        session.clear()
        flash(_("Your account and all your data have been removed."), "success")
        return redirect(url_for("main.index"))
    return redirect(url_for("account.remove_data"))
