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

"""Baseline: the schema as it was when Alembic came in (2026-10-13).

Works on every database there is at that point:

* a new, empty one: the three tables are created;
* one from before Alembic: it was made by db.create_all() and then completed at each start by
  the old _add_missing_columns(), so it can lack the columns added since it was created, the
  unique index on user.public_id (SQLite cannot add a UNIQUE column, so it came separately) and
  the rate_event table. Exactly those are added, with the same defaults as before, and nothing
  else is touched.

From here on every schema change is a migration of its own (see the README).

Revision ID: 0001
Revises:
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

def later_user_columns():
    """Columns of the user table that a database from before Alembic may lack: the list of the old
    app._add_missing_columns(), with its SQL defaults so existing rows get the same values."""
    return [
        sa.Column("moving_avg_days", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("delete_code_hash", sa.String(length=255), nullable=True),
        sa.Column("delete_code_expires_at", sa.DateTime(), nullable=True),
        sa.Column("delete_code_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("public_enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("public_id", sa.String(length=64), nullable=True),
        sa.Column("public_show_stats", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("public_show_weight", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("session_version", sa.Integer(), nullable=False, server_default="0"),
    ]


def upgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "user" in tables:
        existing = {column["name"] for column in inspector.get_columns("user")}
        for column in later_user_columns():
            if column.name not in existing:
                op.add_column("user", column)
        if "ix_user_public_id" not in {index["name"] for index in inspector.get_indexes("user")}:
            op.create_index("ix_user_public_id", "user", ["public_id"], unique=True)
    else:
        op.create_table(
            "user",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("email_hash", sa.String(length=64), nullable=False),
            sa.Column("verified_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("code_hash", sa.String(length=255), nullable=True),
            sa.Column("code_expires_at", sa.DateTime(), nullable=True),
            sa.Column("code_attempts", sa.Integer(), nullable=False),
            sa.Column("delete_code_hash", sa.String(length=255), nullable=True),
            sa.Column("delete_code_expires_at", sa.DateTime(), nullable=True),
            sa.Column("delete_code_attempts", sa.Integer(), nullable=False),
            sa.Column("height_cm", sa.Float(), nullable=True),
            sa.Column("height_unit", sa.String(length=2), nullable=False),
            sa.Column("dark_mode", sa.Boolean(), nullable=False),
            sa.Column("chart_range", sa.String(length=4), nullable=False),
            sa.Column("moving_avg_days", sa.Integer(), nullable=False),
            sa.Column("session_version", sa.Integer(), nullable=False),
            sa.Column("public_enabled", sa.Boolean(), nullable=False),
            sa.Column("public_id", sa.String(length=64), nullable=True),
            sa.Column("public_show_stats", sa.Boolean(), nullable=False),
            sa.Column("public_show_weight", sa.Boolean(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_user_email_hash", "user", ["email_hash"], unique=True)
        op.create_index("ix_user_public_id", "user", ["public_id"], unique=True)

    if "weight_entry" not in tables:
        op.create_table(
            "weight_entry",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("entry_date", sa.Date(), nullable=False),
            sa.Column("weight", sa.Float(), nullable=False),
            sa.Column("note", sa.String(length=280), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "entry_date", name="uq_user_entry_date"),
        )
        op.create_index("ix_weight_entry_entry_date", "weight_entry", ["entry_date"], unique=False)
        op.create_index("ix_weight_entry_user_id", "weight_entry", ["user_id"], unique=False)

    if "rate_event" not in tables:
        op.create_table(
            "rate_event",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(length=16), nullable=False),
            sa.Column("subject", sa.String(length=64), nullable=False),
            sa.Column("at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_rate_event_lookup", "rate_event", ["kind", "subject", "at"], unique=False)


def downgrade():
    # Undoing the baseline would mean deleting every table and all data in them.
    raise NotImplementedError("The baseline cannot be undone; restore a backup instead.")
