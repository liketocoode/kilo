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

"""Friendly answers for the errors a visitor can run into.

* An expired or missing CSRF token (a form or tab left open too long, or the
  session cookie is gone): a form is sent back to the page it came from with a
  message saying nothing was changed and to try again; the dashboard's
  background saves (JSON) get a JSON error the page turns into a "Session
  expired" dialog with a Reload button.
* A page that does not exist, or one that can only be posted to (typing /logout
  in the address bar): a browser is sent to the start page - the dashboard, or
  the sign-in form when signed out - with a message. Anything that is not a
  browser opening a page (an image, an API call, a POST) keeps a plain error.
* An upload that is too large (413): a form is sent back to the page it came
  from with a message that names the limit; JSON gets a JSON error.
* An unexpected error (500): a page in the site's style that says so, with a way
  back; JSON gets a JSON error. The traceback is still logged by Flask first.
"""

from urllib.parse import urlsplit

from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_wtf.csrf import CSRFError

from app import db
from app.i18n import megabytes
from app.security import log_event

# Pages that browsers ask for by themselves and that this app does not have: they are
# not an attack, so they are not logged (and cannot add up to a ban).
ROUTINE_BROWSER_PATHS = frozenset(
    {"/favicon.ico", "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"}
)

# Sent as "code" in the JSON error so the page can tell an expired session from
# any other failed save.
CSRF_ERROR_CODE = "csrf_expired"


def _wants_json():
    """True for the page's own fetch() calls (JSON body or an explicit JSON Accept)."""
    return request.is_json or request.accept_mimetypes.best == "application/json"


def back_url():
    """The page the visitor came from if it is on this site, else the start page.

    Only the path and query are used, so a foreign Referer can never send
    anyone to another site.
    """
    start = url_for("main.index")
    referrer = request.referrer
    if not referrer:
        return start
    parts = urlsplit(referrer)
    if parts.netloc != request.host or not parts.path.startswith("/") or parts.path.startswith("//"):
        return start
    return parts.path + (f"?{parts.query}" if parts.query else "")


def _is_browser_page_request():
    """A browser asking for a web page: GET, explicitly accepting HTML, not a static file."""
    return (
        request.method == "GET"
        and "text/html" in request.headers.get("Accept", "")
        and not request.path.startswith("/static/")
    )


def csrf_failed(error):
    """The CSRF token is missing or too old: say so and what to do, change nothing."""
    if _wants_json():
        message = _("Your session has expired. Reload the page and try again.")
        return jsonify(error=message, code=CSRF_ERROR_CODE), 400
    flash(_("Your session has expired and nothing was changed. Please try again."), "error")
    return redirect(back_url())


def page_not_available(error):
    """404 or 405: send a browser to the start page, leave every other client alone.

    Every request for an unknown page (404) is also written to the security log, whatever
    the answer, because fail2ban bans addresses that keep asking for pages such as /.env.
    """
    if error.code == 404 and request.path not in ROUTINE_BROWSER_PATHS:
        log_event("notfound", path=request.path)
    if _is_browser_page_request():
        flash(_("That page isn't available, so you were taken to the start page."), "error")
        return redirect(url_for("main.index"))
    return error


# How much of a refused upload is read (and thrown away) so the browser gets the answer; see
# `_drain_upload`. A larger one is cut off: the visitor then sees a connection error instead.
DRAIN_MAX_BYTES = 64 * 1024 * 1024


def _drain_upload():
    """Read the rest of a refused request body before answering.

    gunicorn closes the connection after the answer; a browser that is still sending the file
    then shows "connection reset" instead of the page we send back (checked with Chromium). Up
    to DRAIN_MAX_BYTES are read in small chunks and thrown away, never kept in memory.
    """
    length = request.content_length
    if not length or length > DRAIN_MAX_BYTES:
        return
    stream = request.environ.get("wsgi.input")
    if stream is None:
        return
    remaining = length
    try:
        while remaining > 0:
            chunk = stream.read(min(64 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
    except OSError:
        pass


def upload_too_large(error):
    """413: the upload is over the limit (8 MB for an import, 1 MB elsewhere). Nothing was saved."""
    _drain_upload()
    limit = request.max_content_length or current_app.config["MAX_CONTENT_LENGTH"]
    message = _(
        "The file is too large: the limit is %(size)s. Nothing was imported or changed.",
        size=megabytes(limit),
    )
    if _wants_json():
        return jsonify(error=message), 413
    if request.method == "POST":
        flash(message, "error")
        return redirect(back_url())
    return error


# When even the friendly page cannot be made (the database is gone, a template is broken),
# this is sent instead: no template, no database, no translation.
FALLBACK_500 = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kilo Tracker</title></head>
<body>
<h1>Something went wrong</h1>
<p>Kilo Tracker could not handle this request. Please try again in a moment.</p>
<p><a href="/">Back to the start page</a></p>
</body></html>
"""


def server_error(error):
    """500: an unexpected error. Flask has already logged the traceback (kilo-error.log).

    The database session is rolled back first: a failed transaction would make the page's own
    queries (the signed-in user in the header) fail as well.
    """
    try:
        db.session.rollback()
    except Exception:  # the page must still be answered
        current_app.logger.exception("Could not roll back the database session")
    message = _("Something went wrong on our side. Please try again in a moment.")
    if _wants_json():
        return jsonify(error=message), 500
    try:
        return render_template("error.html", message=message), 500
    except Exception:  # the error page itself failed; answer without it
        current_app.logger.exception("The error page could not be shown")
        return FALLBACK_500, 500, {"Content-Type": "text/html; charset=utf-8"}


def init_app(app):
    """Register the error handlers."""
    app.register_error_handler(CSRFError, csrf_failed)
    app.register_error_handler(404, page_not_available)
    app.register_error_handler(405, page_not_available)
    app.register_error_handler(413, upload_too_large)
    app.register_error_handler(500, server_error)
