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

"""The Content-Security-Policy and the other headers that limit what a page may do.

Every response says that its scripts, styles, images and fetch() calls come only from Kilo
itself, that forms only post to Kilo, and that no other site may show it in a frame. If HTML
ever gets into a page that should not be there (a bug in the escaping, say), the browser then
refuses to run an injected script or to send data elsewhere.

Kilo's own inline <script> blocks carry a nonce: a random value made for each response
(`csp_nonce()`, `nonce="{{ csp_nonce() }}"` in the templates) that the policy names. An injected
script cannot know it. `<script type="application/json">` data blocks need none: they never run.

The public chart (`main.public_chart`) is the one page other sites may frame, so it gets the
same policy without `frame-ancestors` and without X-Frame-Options.
"""

import secrets

from flask import request

# The endpoint whose page is made to be embedded in other sites (an iframe).
EMBEDDABLE_ENDPOINTS = frozenset({"main.public_chart"})


def csp_nonce():
    """The nonce of this response, made on first use (also in templates as `csp_nonce()`).

    Kept on the request, not on `g`: `g` belongs to the app context, which can outlive a
    request (the test fixtures keep one open), and a nonce must never be used twice.
    """
    return request.environ.setdefault("kilo.csp_nonce", secrets.token_urlsafe(18))


def policy(nonce, embeddable=False):
    """The Content-Security-Policy header value for one response."""
    rules = [
        "default-src 'self'",
        f"script-src 'self' 'nonce-{nonce}'",
        "style-src 'self'",
        "img-src 'self'",
        "connect-src 'self'",
        "form-action 'self'",
        "object-src 'none'",
        "base-uri 'none'",
    ]
    if not embeddable:
        rules.append("frame-ancestors 'none'")
    return "; ".join(rules)


def init_app(app):
    """Make `csp_nonce()` available to templates and send the headers with every response."""
    app.add_template_global(csp_nonce)

    @app.after_request
    def _security_headers(response):
        embeddable = request.endpoint in EMBEDDABLE_ENDPOINTS
        # setdefault: a response that sets its own policy keeps it.
        response.headers.setdefault("Content-Security-Policy", policy(csp_nonce(), embeddable))
        if not embeddable:
            # For browsers that predate frame-ancestors (clickjacking: a page loaded invisibly
            # over another site could trick a signed-in user into clicking "Remove my data").
            response.headers.setdefault("X-Frame-Options", "DENY")
        return response
