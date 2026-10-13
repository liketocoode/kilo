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

"""Alembic's environment: runs the migrations in migrations/versions against the app's database.

Run by app._migrate_database() at every start, and by `flask db ...` when writing a migration.
Unlike the Flask-Migrate template, this file does not call logging.config.fileConfig(): that
would switch off every logger that already exists (the security log among them) each time the
app starts.
"""

import logging

from alembic import context
from flask import current_app

logger = logging.getLogger("alembic.env")
config = context.config
target_db = current_app.extensions["migrate"].db


def run_migrations_offline():
    """Write the SQL instead of running it (`flask db upgrade --sql`)."""
    context.configure(
        url=str(target_db.engine.url),
        target_metadata=target_db.metadata,
        literal_binds=True,
        **current_app.extensions["migrate"].configure_args,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run the migrations on the app's own engine."""

    def process_revision_directives(context, revision, directives):
        # `flask db migrate` without a model change writes no empty migration.
        if getattr(config.cmd_opts, "autogenerate", False) and directives[0].upgrade_ops.is_empty():
            directives[:] = []
            logger.info("No changes in schema detected.")

    conf_args = dict(current_app.extensions["migrate"].configure_args)
    conf_args.setdefault("process_revision_directives", process_revision_directives)

    with target_db.engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_db.metadata, **conf_args)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
