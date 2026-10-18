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

"""The plain CSV import and the CSV export on the settings page.

The Hacker's Diet files are in test_import_hackdiet.py.
"""

import io
from datetime import date

from sqlalchemy import event

from app import db
from app.models import WeightEntry


def _upload(client, text, filename="data.csv"):
    return client.post(
        "/settings/import",
        data={"csv_file": (io.BytesIO(text.encode()), filename)},
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def test_import_creates_entries(logged_in_client, app):
    client, user_id = logged_in_client
    text = "23-9-26,79.7,optional notes\n25-9-26,80.5,\n"
    resp = _upload(client, text)
    assert resp.status_code == 200
    assert b"Imported 2" in resp.data

    with app.app_context():
        entries = WeightEntry.query.filter_by(user_id=user_id).order_by(WeightEntry.entry_date).all()
        assert len(entries) == 2
        assert entries[0].entry_date == date(2026, 9, 23)
        assert entries[0].weight == 79.7
        assert entries[0].note == "optional notes"
        assert entries[1].note is None


def test_import_overwrites_existing_entry_for_same_date(logged_in_client, app):
    client, user_id = logged_in_client
    client.post("/entries/field", json={"date": "2026-09-23", "weight": 70.0, "note": "old"})
    _upload(client, "23-9-26,79.7,new note\n")
    with app.app_context():
        entries = WeightEntry.query.filter_by(user_id=user_id).all()
        assert len(entries) == 1
        assert entries[0].weight == 79.7
        assert entries[0].note == "new note"


def test_import_of_a_date_that_comes_twice_keeps_the_last_line(logged_in_client, app):
    client, user_id = logged_in_client
    resp = _upload(client, "23-9-26,79.7,first\n23-9-26,80.1,second\n")
    assert resp.status_code == 200
    with app.app_context():
        entry = WeightEntry.query.filter_by(user_id=user_id).one()
        assert (entry.weight, entry.note) == (80.1, "second")


def test_import_does_not_query_the_entries_once_per_line(logged_in_client, app):
    client, _ = logged_in_client
    lines = "".join(f"{day}-9-26,80,\n" for day in range(1, 31))
    statements = []

    def record(_conn, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith("SELECT") and "weight_entry" in statement:
            statements.append(statement)

    with app.app_context():
        engine = db.engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        resp = _upload(client, lines)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert b"Imported 30" in resp.data
    # The import itself reads the entries once; the settings page shown afterwards reads none.
    assert len(statements) < 5


def test_import_reports_skipped_invalid_lines(logged_in_client, app):
    client, user_id = logged_in_client
    text = "23-9-26,79.7,\nnot-a-date,80,\n24-9-26,not-a-number,\n"
    resp = _upload(client, text)
    assert b"Imported 1" in resp.data
    assert b"Skipped 2" in resp.data
    with app.app_context():
        assert WeightEntry.query.filter_by(user_id=user_id).count() == 1


def test_import_four_digit_year(logged_in_client, app):
    client, user_id = logged_in_client
    _upload(client, "23-9-2026,79.7,\n")
    with app.app_context():
        entry = WeightEntry.query.filter_by(user_id=user_id).one()
        assert entry.entry_date == date(2026, 9, 23)


def test_import_requires_login(client):
    resp = _upload(client, "23-9-26,79.7,\n")
    assert resp.status_code == 200
    assert b"Sign in" in resp.data


def test_import_rejects_wrong_file_type(logged_in_client, app):
    client, user_id = logged_in_client
    resp = client.post(
        "/settings/import",
        data={"csv_file": (io.BytesIO(b"not a csv"), "data.png")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"CSV, XML or text files only" in resp.data
    with app.app_context():
        assert WeightEntry.query.filter_by(user_id=user_id).count() == 0


def test_export_returns_csv_attachment(logged_in_client, app):
    client, _ = logged_in_client
    client.post("/entries/field", json={"date": "2026-09-23", "weight": 79.7, "note": "hi"})

    resp = client.get("/settings/export")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    assert "attachment" in resp.headers["Content-Disposition"]
    assert resp.data.decode() == "23-9-26,79.7,hi\r\n"


def test_export_requires_login(client):
    resp = client.get("/settings/export")
    assert resp.status_code == 302


def test_export_then_import_round_trips(logged_in_client, app):
    client, user_id = logged_in_client
    client.post("/entries/field", json={"date": "2026-09-23", "weight": 79.7, "note": "hi"})
    client.post("/entries/field", json={"date": "2026-09-24", "weight": 80.1})

    exported = client.get("/settings/export").data

    with app.app_context():
        WeightEntry.query.filter_by(user_id=user_id).delete()
        db.session.commit()

    _upload(client, exported.decode())
    with app.app_context():
        entries = WeightEntry.query.filter_by(user_id=user_id).order_by(WeightEntry.entry_date).all()
        assert [e.weight for e in entries] == [79.7, 80.1]


def test_export_filename_contains_todays_date(logged_in_client):
    client, _ = logged_in_client
    resp = client.get("/settings/export")
    assert (
        f"filename=kilo-tracker-export-{date.today().isoformat()}.csv"
        in resp.headers["Content-Disposition"]
    )


def test_csv_import_uses_the_same_weight_limit(logged_in_client):
    client, _ = logged_in_client
    data = {"csv_file": (io.BytesIO(b"1-1-26,500\n2-1-26,500.1\n3-1-26,750\n"), "w.csv")}
    client.post("/settings/import", data=data, content_type="multipart/form-data")
    assert [e.weight for e in WeightEntry.query.all()] == [500.0]
