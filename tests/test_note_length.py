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

"""Tests that every way a note comes in is cut to the one limit, `utils.NOTE_MAX_LENGTH`.

The editor, the JSON API, the plain CSV import and both Hacker's Diet imports must agree: if one
of them kept a different number, notes would be cut short (or kept longer) without any error.
"""

import pytest

from app.importing import parse_import
from app.models import WeightEntry
from app.utils import NOTE_MAX_LENGTH
from tests.test_import_hackdiet import hd_csv, hd_xml

TOO_LONG = "x" * (NOTE_MAX_LENGTH + 20)


def test_the_limit_is_280_characters():
    # A change of the limit is deliberate: it is shown to users in the editor.
    assert NOTE_MAX_LENGTH == 280


def test_the_database_column_has_the_limit():
    assert WeightEntry.__table__.c.note.type.length == NOTE_MAX_LENGTH


def test_the_note_editor_has_the_limit(logged_in_client):
    client, _ = logged_in_client
    html = client.get("/").get_data(as_text=True)
    assert f'maxlength="{NOTE_MAX_LENGTH}"' in html
    assert f"textarea.value.trim().slice(0, {NOTE_MAX_LENGTH})" in html


def test_a_saved_note_is_cut_to_the_limit(logged_in_client, app):
    client, user_id = logged_in_client
    response = client.post("/entries/field", json={"date": "2026-10-01", "weight": "80", "note": TOO_LONG})
    assert response.status_code == 200
    with app.app_context():
        entry = WeightEntry.query.filter_by(user_id=user_id).one()
        assert entry.note == "x" * NOTE_MAX_LENGTH


@pytest.mark.parametrize(
    "data",
    [
        f"1-10-26,80,{TOO_LONG}\n".encode(),
        hd_csv(days={(2026, 10): [(1, "80", TOO_LONG)]}),
        hd_xml(days={(2026, 10): [(1, "80", TOO_LONG)]}),
    ],
    ids=["plain-csv", "hackdiet-csv", "hackdiet-xml"],
)
def test_an_imported_note_is_cut_to_the_limit(app, data):
    with app.test_request_context():
        result = parse_import(data)
    assert [note for _date, _weight, note in result.rows] == ["x" * NOTE_MAX_LENGTH]
