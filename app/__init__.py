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

"""Application factory and shared extensions for Kilo Tracker.

Creates the Flask app (`create_app`), wires up SQLAlchemy, CSRF protection and
Flask-Mail, configures SQLite for use by several processes (WAL mode), and adds
columns that newer versions of the app expect to databases created by older
ones.
"""

import os
import sqlite3
from datetime import timedelta

from flask import Flask, request
from flask_mail import Mail
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from sqlalchemy import event
from sqlalchemy.engine import Engine

from . import i18n, security

db = SQLAlchemy()
csrf = CSRFProtect()
mail = Mail()


SQLITE_BUSY_TIMEOUT_MS = 30_000


def _env_int(name, default):
    """An integer setting from the environment; a missing or invalid value gives the default."""
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


@event.listens_for(Engine, "connect")
def _configure_sqlite(dbapi_connection, connection_record):
    """Make SQLite behave with several app processes (e.g. gunicorn workers)
    sharing one database file.

    WAL lets readers and the single writer work at the same time instead of
    blocking each other; a long busy timeout makes a writer wait for the lock
    rather than fail with "database is locked" after the 5s default."""
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")  # safe with WAL, fewer fsyncs
    cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    cursor.close()


def create_app(test_config=None):
    """Build and configure the Flask application.

    `test_config`, if given, overrides the default configuration (the tests use it
    to point at an in-memory database). Settings such as SECRET_KEY, the mail
    server and CHECK_EMAIL_MX come from environment variables.
    """
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev"),
        SQLALCHEMY_DATABASE_URI="sqlite:///" + os.path.join(app.instance_path, "weight.db"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MAIL_SERVER=os.environ.get("MAIL_SERVER", "localhost"),
        MAIL_PORT=int(os.environ.get("MAIL_PORT", "25")),
        MAIL_USE_TLS=os.environ.get("MAIL_USE_TLS", "0") == "1",
        MAIL_USERNAME=os.environ.get("MAIL_USERNAME"),
        MAIL_PASSWORD=os.environ.get("MAIL_PASSWORD"),
        MAIL_DEFAULT_SENDER=os.environ.get("MAIL_DEFAULT_SENDER", "no-reply@weight-tracker.local"),
        MAIL_SUPPRESS_SEND=os.environ.get("MAIL_SUPPRESS_SEND", "1") == "1",
        CHECK_EMAIL_MX=os.environ.get("CHECK_EMAIL_MX", "1") == "1",
        # A log of suspicious requests (unknown pages, wrong sign-in codes) in a fixed format
        # for fail2ban, see app/security.py. Unset: nothing is written.
        SECURITY_LOG_FILE=os.environ.get("SECURITY_LOG_FILE"),
        # The header's avatar is fetched from Gravatar by the server and cached on disk, see
        # app/avatar.py. 0 shows a local placeholder for everyone and never contacts Gravatar.
        GRAVATAR_ENABLED=os.environ.get("GRAVATAR_ENABLED", "1") == "1",
        AVATAR_CACHE_DIR=os.path.join(app.instance_path, "avatars"),
        # Limits on emailed codes, see app/ratelimit.py. The mode is "log" until the numbers have
        # been checked against real traffic; 0 turns a single limit off.
        RATELIMIT_MODE=os.environ.get("RATELIMIT_MODE", "log"),
        RATELIMIT_COOLDOWN_SECONDS=_env_int("RATELIMIT_COOLDOWN_SECONDS", 60),
        RATELIMIT_ADDRESS_PER_HOUR=_env_int("RATELIMIT_ADDRESS_PER_HOUR", 5),
        RATELIMIT_REMOVAL_PER_HOUR=_env_int("RATELIMIT_REMOVAL_PER_HOUR", 3),
        RATELIMIT_IP_PER_HOUR=_env_int("RATELIMIT_IP_PER_HOUR", 20),
        RATELIMIT_GLOBAL_PER_HOUR=_env_int("RATELIMIT_GLOBAL_PER_HOUR", 60),
        MAX_CONTENT_LENGTH=1 * 1024 * 1024,
        # An import may be bigger: a Hacker's Diet XML export lists every day (about 75 KB a year).
        IMPORT_MAX_CONTENT_LENGTH=8 * 1024 * 1024,
        SUPPORTED_LANGUAGES=i18n.SUPPORTED_LANGUAGES,
        BABEL_DEFAULT_LOCALE=i18n.DEFAULT_LANGUAGE,
        BABEL_TRANSLATION_DIRECTORIES=os.path.join(app.root_path, "..", "translations"),
        WTF_I18N_ENABLED=True,
        # Signed-in users stay signed in when the browser is closed: the session cookie is
        # renewed on every visit and expires 30 days after the last one, but never more than
        # 90 days after the sign-in itself (enforced in auth.py).
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        SESSION_ABSOLUTE_LIFETIME=timedelta(days=90),
        # How old the CSRF token in a page may be, counted from when the page was rendered (in
        # seconds, as Flask-WTF wants). The token is also tied to the session, so this matches the
        # 30 days a session lasts: a tab left open or restored later keeps working.
        WTF_CSRF_TIME_LIMIT=int(timedelta(days=30).total_seconds()),
        SESSION_COOKIE_SAMESITE="Lax",
        # In production set SESSION_COOKIE_SECURE=1, so the cookie is only ever sent over HTTPS.
        # Off by default: it would stop the plain-http development server from signing in.
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",
    )

    if test_config:
        app.config.update(test_config)

    secret_key = str(app.config["SECRET_KEY"])
    if not app.testing and (secret_key == "dev" or len(secret_key) < 16):
        # The signed session cookie is the only thing that proves who is signed in.
        app.logger.warning(
            "SECRET_KEY is the insecure default or very short: anyone who knows it can forge a "
            "signed-in session. Set SECRET_KEY in the environment (see the README)."
        )

    os.makedirs(app.instance_path, exist_ok=True)

    security.init_app(app)
    db.init_app(app)

    @app.before_request
    def _allow_large_imports():
        # Registered before CSRF protection, whose check reads the form and so
        # already enforces the limit.
        if request.endpoint == "main.import_entries":
            request.max_content_length = app.config["IMPORT_MAX_CONTENT_LENGTH"]

    # Before CSRF protection: its before_request check can fail the request, and
    # the error answer must already be in the visitor's language.
    i18n.init_app(app)
    csrf.init_app(app)
    mail.init_app(app)

    from . import account, auth, errors, ratelimit, routes
    errors.init_app(app)
    ratelimit.init_app(app)
    app.register_blueprint(routes.bp)
    app.register_blueprint(account.bp)
    app.register_blueprint(auth.bp)

    with app.app_context():
        db.create_all()
        _add_missing_columns()

    return app


def _add_missing_columns():
    """db.create_all() only creates tables that don't exist yet — it never
    alters an existing one. There's no migration framework here, so new
    nullable-with-default columns are added by hand with a plain ALTER TABLE
    when a pre-existing database is missing them."""
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    if "user" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("user")}
    new_columns = {
        "moving_avg_days": "INTEGER NOT NULL DEFAULT 10",
        "delete_code_hash": "VARCHAR(255)",
        "delete_code_expires_at": "DATETIME",
        "delete_code_attempts": "INTEGER NOT NULL DEFAULT 0",
        "public_enabled": "BOOLEAN NOT NULL DEFAULT 0",
        "public_id": "VARCHAR(64)",
        "public_show_stats": "BOOLEAN NOT NULL DEFAULT 1",
    }
    for name, ddl in new_columns.items():
        if name not in existing:
            db.session.execute(text(f"ALTER TABLE user ADD COLUMN {name} {ddl}"))
    # SQLite cannot add a UNIQUE column, so the index create_all() makes on a new database is
    # added separately (NULLs do not clash in a unique index).
    db.session.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_user_public_id ON user (public_id)"))
    db.session.commit()
