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

"""The database migrations (Alembic, migrations/versions) that the app applies at every start.

The safety net is the first test: migrating an empty database must give exactly the schema of
the models, so a model change without a migration fails here (and in CI).
"""

import logging
import multiprocessing
import sqlite3

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

from app import MIGRATIONS_DIR, create_app, db, migrate
from app.models import User, WeightEntry


def make_app(path):
    return create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{path}"})


def head():
    with make_app(":memory:").app_context():
        return ScriptDirectory.from_config(migrate.get_config(MIGRATIONS_DIR)).get_current_head()


def schema_differences(app):
    """What separates the database from the models (empty when they match)."""
    with app.app_context(), db.engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        return compare_metadata(context, db.metadata)


def columns(engine, table):
    """The columns of a table as plain values (type objects do not compare equal)."""
    return [(c["name"], str(c["type"]), c["nullable"], c["default"]) for c in sa.inspect(engine).get_columns(table)]


def version_of(path):
    with sqlite3.connect(path) as conn:
        return [row[0] for row in conn.execute("SELECT version_num FROM alembic_version")]


def test_the_migrations_build_exactly_the_schema_of_the_models(tmp_path):
    app = make_app(tmp_path / "new.db")
    assert schema_differences(app) == []
    assert version_of(tmp_path / "new.db") == [head()]


def test_starting_again_changes_nothing(tmp_path):
    path = tmp_path / "kilo.db"
    with make_app(path).app_context():
        db.session.add(User(email_hash="a" * 64))
        db.session.commit()
    with make_app(path).app_context():
        assert User.query.count() == 1
    assert version_of(path) == [head()]


# The oldest shape a database can have: made by db.create_all() before any of the later columns,
# the unique public_id index and the rate_event table existed (the old _add_missing_columns()
# completed it at each start).
OLDEST_SCHEMA = """
CREATE TABLE user (
    id INTEGER NOT NULL PRIMARY KEY, email_hash VARCHAR(64) NOT NULL, verified_at DATETIME,
    created_at DATETIME NOT NULL, code_hash VARCHAR(255), code_expires_at DATETIME,
    code_attempts INTEGER NOT NULL, height_cm FLOAT, height_unit VARCHAR(2) NOT NULL,
    dark_mode BOOLEAN NOT NULL, chart_range VARCHAR(4) NOT NULL
);
CREATE UNIQUE INDEX ix_user_email_hash ON user (email_hash);
CREATE TABLE weight_entry (
    id INTEGER NOT NULL PRIMARY KEY, user_id INTEGER NOT NULL, entry_date DATE NOT NULL,
    weight FLOAT NOT NULL, note VARCHAR(280),
    CONSTRAINT uq_user_entry_date UNIQUE (user_id, entry_date), FOREIGN KEY(user_id) REFERENCES user (id)
);
CREATE INDEX ix_weight_entry_entry_date ON weight_entry (entry_date);
CREATE INDEX ix_weight_entry_user_id ON weight_entry (user_id);
INSERT INTO user (id, email_hash, created_at, code_attempts, height_unit, dark_mode, chart_range)
    VALUES (1, 'old', '2026-01-01 00:00:00', 0, 'cm', 1, '3m');
INSERT INTO weight_entry (user_id, entry_date, weight, note) VALUES (1, '2026-01-01', 80.5, 'first');
"""


def test_a_database_from_before_alembic_is_completed_and_keeps_its_data(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(OLDEST_SCHEMA)
    app = make_app(path)
    assert schema_differences(app) == []
    assert version_of(path) == [head()]
    with app.app_context():
        user = User.query.one()
        assert (user.email_hash, user.dark_mode, user.chart_range) == ("old", True, "3m")
        # The later columns got the defaults the old code gave them.
        assert (user.moving_avg_days, user.session_version, user.delete_code_attempts) == (10, 0, 0)
        assert (user.public_enabled, user.public_show_stats, user.public_show_weight) == (False, False, True)
        entry = WeightEntry.query.one()
        assert (entry.weight, entry.note) == (80.5, "first")
        indexes = {i["name"]: i for i in sa.inspect(db.engine).get_indexes("user")}
        assert indexes["ix_user_public_id"]["unique"]
        assert "rate_event" in sa.inspect(db.engine).get_table_names()


def test_a_database_from_the_last_version_before_alembic_only_gets_its_version(tmp_path):
    """Production's case: every column already there; the baseline must change nothing."""
    path = tmp_path / "recent.db"
    engine = sa.create_engine(f"sqlite:///{path}")
    db.metadata.create_all(engine)  # what create_all() made, completed up to the latest columns
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO user (email_hash, created_at, code_attempts, delete_code_attempts,"
                             " height_unit, dark_mode, chart_range, moving_avg_days, session_version,"
                             " public_enabled, public_show_stats, public_show_weight)"
                             " VALUES ('x', '2026-01-01', 0, 0, 'cm', 0, 'all', 7, 3, 1, 1, 0)"))
    before = columns(engine, "user")
    engine.dispose()
    app = make_app(path)
    assert version_of(path) == [head()]
    assert schema_differences(app) == []
    with app.app_context():
        assert columns(db.engine, "user") == before
        user = User.query.one()
        assert (user.moving_avg_days, user.session_version, user.public_show_weight) == (7, 3, False)


def test_the_baseline_cannot_be_undone(tmp_path):
    app = make_app(tmp_path / "kilo.db")
    with app.app_context(), pytest.raises(NotImplementedError):
        command.downgrade(migrate.get_config(MIGRATIONS_DIR), "base")


def test_migrating_keeps_the_apps_loggers_working(tmp_path):
    """Alembic's usual env.py calls logging.config.fileConfig(), which disables every existing
    logger, the security log among them; ours must not."""
    security = logging.getLogger("kilo.security")
    make_app(tmp_path / "kilo.db")
    assert not security.disabled
    assert not logging.getLogger("app").disabled


def _start(path, queue):
    try:
        make_app(path)
        queue.put("ok")
    except Exception as error:  # reported to the test
        queue.put(repr(error))


def test_processes_starting_at_once_migrate_a_new_database_only_once(tmp_path):
    """The lock: without it, two processes both see an empty database and both create tables."""
    path = tmp_path / "shared.db"
    spawn = multiprocessing.get_context("spawn")
    queue = spawn.Queue()
    processes = [spawn.Process(target=_start, args=(str(path), queue)) for _ in range(4)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(60)
    assert sorted(queue.get(timeout=5) for _ in processes) == ["ok"] * 4
    assert version_of(path) == [head()]
