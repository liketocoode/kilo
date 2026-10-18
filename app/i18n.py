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

"""Language selection for Kilo Tracker.

The UI language is, in this order: a hidden `?lang=xx` query parameter (for testing; never
stored), the language picked in the language menu (the LANGUAGE_COOKIE, see routes.set_language),
the browser's Accept-Language header matched on the primary subtag (`nl-BE` -> `nl`, `pt-PT` ->
`pt`), and English. The supported languages are listed once, in `SUPPORTED_LANGUAGES` in the app
config.

Also holds the Jinja filters that show numbers, weights and dates the way the
active language writes them (`72,5 kg` in Dutch, `72.5 kg` in English).
"""

import re
from datetime import date

from babel.units import format_unit
from flask import current_app, has_request_context, request
from flask_babel import Babel, format_date, format_decimal, get_locale, gettext as _, refresh

# The languages the app is translated into: the only place they are listed.
# To add one, list it here and run `pybabel init` (see TRANSLATING.md). "en" is
# the source language and the fallback.
SUPPORTED_LANGUAGES = (
    "en", "nl", "fr", "es", "pt", "de", "it", "id", "pl", "ro", "hu", "da", "fi", "sv", "nb",
)
DEFAULT_LANGUAGE = "en"

# Browser language codes that map to a language above. Norwegian is written in
# Bokmål ("nb") by most people, but browsers also send "no" and "nn" (Nynorsk).
LANGUAGE_ALIASES = {"no": "nb", "nn": "nb"}

# Each language in the language menu, named in itself (a German reader looks for "Deutsch").
LANGUAGE_NAMES = {
    "en": "English",
    "nl": "Nederlands",
    "fr": "Français",
    "es": "Español",
    "pt": "Português (Brasil)",
    "de": "Deutsch",
    "it": "Italiano",
    "id": "Bahasa Indonesia",
    "pl": "Polski",
    "ro": "Română",
    "hu": "Magyar",
    "da": "Dansk",
    "fi": "Suomi",
    "sv": "Svenska",
    "nb": "Norsk bokmål",
}

# The cookie that holds the language picked in the menu; without it the browser decides.
LANGUAGE_COOKIE = "kilo_lang"
LANGUAGE_COOKIE_DAYS = 365

babel = Babel()


def _supported_language(tag, supported):
    """The supported language a tag like "nl-BE" or "no" stands for, or None."""
    primary = tag.strip().replace("_", "-").split("-")[0].lower()
    primary = LANGUAGE_ALIASES.get(primary, primary)
    return primary if primary in supported else None


def select_locale():
    """Pick the locale for the current request (see the module docstring)."""
    supported = current_app.config["SUPPORTED_LANGUAGES"]
    if not has_request_context():
        return current_app.config["BABEL_DEFAULT_LOCALE"]

    override = _supported_language(request.args.get("lang", ""), supported)
    if override:
        return override

    chosen = chosen_language()
    if chosen:
        return chosen

    for tag, _quality in request.accept_languages:
        language = _supported_language(tag, supported)
        if language:
            return language

    return current_app.config["BABEL_DEFAULT_LOCALE"]


def menu_languages():
    """The languages in the order the language menu lists them.

    First the supported languages the browser asks for, in its order of preference; then
    English, if it was not among them; then all the others alphabetically by their own name
    (`LANGUAGE_NAMES`), so a visitor finds theirs at the top or by its first letter.
    """
    supported = current_app.config["SUPPORTED_LANGUAGES"]
    ordered = []
    for tag, _quality in request.accept_languages:
        language = _supported_language(tag, supported)
        if language and language not in ordered:
            ordered.append(language)
    if DEFAULT_LANGUAGE not in ordered:
        ordered.append(DEFAULT_LANGUAGE)
    rest = [code for code in supported if code not in ordered]
    return ordered + sorted(rest, key=lambda code: LANGUAGE_NAMES[code].casefold())


def chosen_language():
    """The language picked in the language menu (the cookie), or None to follow the browser."""
    value = request.cookies.get(LANGUAGE_COOKIE, "")
    return value if value in current_app.config["SUPPORTED_LANGUAGES"] else None


def kg(value, signed=False):
    """A weight in kilograms with one decimal and the unit: `72,5 kg`. `signed` adds a + or -."""
    return format_unit(
        value, "kilogram", length="short", format="+0.0;-0.0" if signed else "0.0", locale=get_locale()
    )


def megabytes(n_bytes):
    """A size in bytes as whole megabytes with the unit: `8 MB` (`8 Mo` in French)."""
    return format_unit(
        n_bytes / (1024 * 1024), "digital-megabyte", length="short", format="0.#", locale=get_locale()
    )


def grams_per_day(value):
    """A signed rate in grams per day, e.g. `-35 g/day`."""
    # NOTE: Rate of weight change. %(grams)s is a signed whole number; g is gram.
    return _("%(grams)s g/day", grams=format_decimal(value, format="+0;-0"))


def meters(value):
    """A length in metres with two decimals and the unit: `1,86 m`."""
    text = format_unit(value, "meter", length="short", format="0.00", locale=get_locale())
    if not re.search(r"[\s\u202f]m$", text):
        # CLDR spells the unit out in a few languages (Danish "meter", Romanian
        # "metri"); "m" is the SI symbol everywhere, so keep it consistent.
        text = format_decimal(value, format="0.00") + "\u00a0m"
    return text


def decimal1(value):
    """A number with exactly one decimal, e.g. a BMI of `22,5`."""
    return format_decimal(value, format="0.0")


def decimal_input(value):
    """A weight for a text input: the locale's decimal separator, no grouping, 1-2 decimals."""
    return format_decimal(value, format="0.0#")


def localdate(value):
    """A date (or ISO date string) in the locale's medium format, e.g. `1 sep 2026`."""
    if isinstance(value, str):
        value = date.fromisoformat(value)
    return format_date(value, format="medium")


def init_app(app):
    """Register Flask-Babel with `select_locale` as the locale selector, and the template filters."""
    babel.init_app(app, locale_selector=select_locale)
    app.add_template_global(chosen_language)
    app.add_template_global(menu_languages)
    app.jinja_env.globals["LANGUAGE_NAMES"] = LANGUAGE_NAMES
    for func in (kg, meters, megabytes, grams_per_day, decimal1, decimal_input, localdate):
        app.add_template_filter(func)

    @app.before_request
    def _reset_locale():
        # Flask-Babel caches the locale on the app context. That is one context
        # per request in production, but a long-lived outer one (the test
        # fixtures) would carry one request's language over to the next.
        refresh()

    @app.after_request
    def _vary_on_language(response):
        # Pages, flashes and JSON errors all depend on Accept-Language. Without
        # this, a cache in front of the app (Apache mod_cache, a CDN, the
        # browser) serves the first visitor's language to everyone. They depend on
        # the language cookie too; "Vary: Cookie" is already added by Flask, because
        # every request reads the session (sessions.keep_session_alive).
        if request.endpoint != "static":
            response.vary.add("Accept-Language")
        return response
