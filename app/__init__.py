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
Flask-Mail, configures SQLite for use by several processes (WAL mode), and brings
the database to the newest migration (Alembic, migrations/versions) at every start.
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import timedelta

try:
    import fcntl
except ImportError:  # Windows: no lock (production runs on Linux)
    fcntl = None

from flask import Flask, current_app, request, url_for
from flask_mail import Mail
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from sqlalchemy import event
from sqlalchemy.engine import Engine

from . import csp, i18n, security
from .utils import NOTE_MAX_LENGTH, PROJECT_ROOT

db = SQLAlchemy()
# Alembic through Flask-Migrate; the migrations are in migrations/versions (see _migrate_database).
# Batch mode: SQLite can only add columns in place, so other changes copy the table.
migrate = Migrate(render_as_batch=True)
csrf = CSRFProtect()
mail = Mail()


SQLITE_BUSY_TIMEOUT_MS = 30_000


def static_url(filename):
    """The address of a file in static/ with its modification time as `?v=`: Kilo sends static
    files without a cache lifetime, so a browser may reuse its copy for a while. A deploy that
    changes the dashboard scripts changes the address too, so the new page never runs with an
    old script."""
    mtime = int(os.path.getmtime(os.path.join(current_app.static_folder, filename)))
    return url_for("static", filename=filename, v=mtime)


def _env_int(name, default):
    """An integer setting from the environment; a missing or invalid value gives the default."""
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_flag(name, default):
    """An on/off setting from the environment: on only when it is "1"; unset gives the default."""
    return os.environ.get(name, "1" if default else "0") == "1"


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
        MAIL_USE_TLS=_env_flag("MAIL_USE_TLS", False),
        MAIL_USERNAME=os.environ.get("MAIL_USERNAME"),
        MAIL_PASSWORD=os.environ.get("MAIL_PASSWORD"),
        MAIL_DEFAULT_SENDER=os.environ.get("MAIL_DEFAULT_SENDER", "no-reply@weight-tracker.local"),
        MAIL_SUPPRESS_SEND=_env_flag("MAIL_SUPPRESS_SEND", True),
        CHECK_EMAIL_MX=_env_flag("CHECK_EMAIL_MX", True),
        # A log of suspicious requests (unknown pages, wrong sign-in codes) in a fixed format
        # for fail2ban, see app/security.py. Unset: nothing is written.
        SECURITY_LOG_FILE=os.environ.get("SECURITY_LOG_FILE"),
        # The header's avatar is fetched from Gravatar by the server and cached on disk, see
        # app/avatar.py. 0 shows a local placeholder for everyone and never contacts Gravatar.
        GRAVATAR_ENABLED=_env_flag("GRAVATAR_ENABLED", True),
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
        SESSION_COOKIE_SECURE=_env_flag("SESSION_COOKIE_SECURE", False),
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

    # The note length limit, for the note editor's maxlength (see app/utils.py).
    app.jinja_env.globals["NOTE_MAX_LENGTH"] = NOTE_MAX_LENGTH
    app.add_template_global(static_url)

    # Content-Security-Policy and X-Frame-Options on every response (see app/csp.py).
    csp.init_app(app)

    # Before CSRF protection: its before_request check can fail the request, and
    # the error answer must already be in the visitor's language.
    i18n.init_app(app)
    csrf.init_app(app)
    mail.init_app(app)

    from . import account, auth, errors, ratelimit, routes, sessions
    errors.init_app(app)
    ratelimit.init_app(app)
    app.register_blueprint(routes.bp)
    app.register_blueprint(account.bp)
    app.register_blueprint(auth.bp)
    sessions.init_app(app)

    migrate.init_app(app, db, directory=MIGRATIONS_DIR)
    with app.app_context():
        _migrate_database(app)

    return app


MIGRATIONS_DIR = os.path.join(PROJECT_ROOT, "migrations")


@contextmanager
def _migration_lock():
    """Only one process migrates a database file at a time.

    gunicorn's preload_app runs create_app() once, in the master, but the daily cleanup script
    starts its own app and could meet a restart. The lock is a file next to the database, so it
    works for any number of processes on this machine. An in-memory database (the tests) needs none.
    """
    path = db.engine.url.database
    if not path or path == ":memory:" or fcntl is None:
        yield
        return
    with open(path + ".migrate-lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _migrate_database(app):
    """Bring the database to the newest migration (migrations/versions), at every start.

    A new database is created by the migrations themselves; an existing one only gets the ones it
    has not had yet (Alembic remembers them in the alembic_version table). Databases from before
    Alembic are handled by the first migration, which completes whatever they lack.
    """
    from alembic import command

    with _migration_lock():
        command.upgrade(migrate.get_config(MIGRATIONS_DIR), "head")

