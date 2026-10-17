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

"""Tests for importing The Hacker's Diet CSV/XML exports and for the header rule of the plain CSV import.

The exports below are made up, but follow the real ones: a per-month header and
StartTrend line in the CSV, Windows-1252 text, days without a weight, a note
without a weight, and an XML with a DOCTYPE that points at a DTD.
"""

import io
import socket
from datetime import date

import pytest

from app import db
from app.csv_io import ImportFileError, parse_csv
from app.importing import parse_import
from app.models import User, WeightEntry
from tests.conftest import flashes

# (date, weight text, comment) per day; months are separated like the real export does it
DAYS = {
    (2007, 8): [
        (1, "94", ""), (2, "", ""), (3, "93.5", "after the party, finally"), (4, "", "a note without a weight"),
        (5, "93.2", "señor"),  # "ñ" is one byte in Windows-1252
    ],
    (2007, 9): [(1, "92.8", ""), (2, "", ""), (3, "92.4", "")],
}
EXPECTED_ROWS = [
    (date(2007, 8, 1), 94.0, None),
    (date(2007, 8, 3), 93.5, "after the party, finally"),
    (date(2007, 8, 5), 93.2, "señor"),
    (date(2007, 9, 1), 92.8, None),
    (date(2007, 9, 3), 92.4, None),
]


def hd_csv(days=DAYS, unit="kilogram", encoding="cp1252", with_epoch=True):
    lines = []
    if with_epoch:
        lines += [
            "Epoch,2025-11-20T14:07:50Z",
            "User,1.0,somebody,,,,somebody@example.com,2007-11-20T21:25:38Z",
            f"Preferences,1.0,{unit},{unit},calorie,0,.",
            "Diet-Plan,1.0,-75,98,85,2025-02-28T00:00:00Z,1",
        ]
    for (year, month), month_days in days.items():
        lines += ["Date,Weight,Rung,Flag,Comment", "StartTrend,0,0,1195940413,1195940413,1.0"]
        for day, weight, comment in month_days:
            quoted = f'"{comment}"' if "," in comment else comment
            lines.append(f"{year}-{month:02d}-{day:02d},{weight},,0,{quoted}")
    return ("\r\n".join(lines) + "\r\n").encode(encoding)


def hd_xml(days=DAYS, unit="kilogram", height="187", display_unit=None, doctype=True, extra=""):
    parts = ['<?xml version="1.0" encoding="UTF-8"?>']
    if doctype:
        parts += [
            '<?xml-stylesheet type="text/css" href="http://www.fourmilab.ch/hackdiet/online/hackdiet_db.css"?>',
            '<!DOCTYPE hackersdiet SYSTEM\n          "http://www.fourmilab.ch/hackdiet/online/hackersdiet.dtd">',
        ]
    parts += [
        '<hackersdiet version="1.0">',
        "<epoch>2025-11-20T14:07:40Z</epoch>",
        '<account version="1.0"><user version="1.0"><login-name>somebody</login-name>',
        f"<e-mail>somebody@example.com</e-mail><height>{height}</height></user>",
        (
            f"<preferences><log-unit>{unit}</log-unit>"
            f"<display-unit>{display_unit or unit}</display-unit></preferences></account>"
        ),
        '<monthlogs version="1.0">',
    ]
    for (year, month), month_days in days.items():
        parts.append(f"<monthlog><properties><year>{year}</year><month>{month}</month>"
                     f"<weight-unit>{unit}</weight-unit></properties><days>")
        for day, weight, comment in month_days:
            safe = comment.replace("ñ", "&#241;")
            parts.append(f"<day><date>{day}</date><weight>{weight}</weight><rung></rung><flag></flag><comment>{safe}</comment></day>")
        parts.append("</days></monthlog>")
    parts += [extra, "</monthlogs></hackersdiet>"]
    return "\n".join(parts).encode("utf-8")


# ---- the CSV export ---------------------------------------------------------


def test_the_csv_export_gives_weights_and_notes():
    result = parse_import(hd_csv())
    assert result.rows == EXPECTED_ROWS
    assert result.errors == []
    assert result.height_cm is None  # the CSV has no height


def test_the_per_month_header_and_trend_lines_are_not_errors():
    # two months means two "Date,Weight,..." and two "StartTrend" lines, plus the metadata at the top
    assert parse_import(hd_csv()).errors == []


def test_days_without_a_weight_are_ignored_and_a_note_without_one_is_counted():
    result = parse_import(hd_csv())
    assert len(result.rows) == 5  # of 8 days
    assert result.notes_skipped == 1


def test_windows_1252_text_is_decoded_not_mangled():
    result = parse_import(hd_csv(encoding="cp1252"))
    assert "señor" in [note for _d, _w, note in result.rows]
    assert "�" not in "".join(note or "" for _d, _w, note in result.rows)


def test_utf_8_text_works_too():
    assert parse_import(hd_csv(encoding="utf-8")).rows == EXPECTED_ROWS


def test_a_comma_in_a_note_is_kept():
    assert (date(2007, 8, 3), 93.5, "after the party, finally") in parse_import(hd_csv()).rows


def test_a_csv_without_the_metadata_lines_is_still_recognised():
    result = parse_import(hd_csv(with_epoch=False))
    assert result.rows == EXPECTED_ROWS and result.errors == []


def test_the_login_and_email_in_the_file_are_never_returned():
    result = parse_import(hd_csv())
    assert "somebody" not in repr(result)


# ---- the XML export ---------------------------------------------------------


def test_the_xml_export_gives_the_same_entries_as_the_csv_export():
    xml, csv_result = parse_import(hd_xml()), parse_import(hd_csv())
    assert xml.rows == csv_result.rows == EXPECTED_ROWS
    assert xml.notes_skipped == csv_result.notes_skipped == 1
    assert xml.errors == []


def test_the_xml_export_gives_the_height():
    assert parse_import(hd_xml(height="187")).height_cm == 187.0


@pytest.mark.parametrize(
    "height, expected", [("", None), ("abc", None), ("20", None), ("400", None), ("50", 50.0), ("186.5", 186.5)]
)
def test_only_a_sensible_height_is_used(height, expected):
    assert parse_import(hd_xml(height=height)).height_cm == expected


def test_the_height_of_an_account_in_pounds_is_not_guessed():
    # in the imperial unit system the number may be inches, so it is ignored
    assert parse_import(hd_xml(unit="pound", height="73")).height_cm is None


def test_the_dtd_is_never_fetched(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("the importer tried to reach the network")

    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    assert parse_import(hd_xml(doctype=True)).rows == EXPECTED_ROWS


def test_an_xml_without_a_doctype_works_too():
    assert parse_import(hd_xml(doctype=False)).rows == EXPECTED_ROWS


def test_a_file_that_declares_its_own_entities_is_refused():
    bomb = (b'<?xml version="1.0"?><!DOCTYPE hackersdiet [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
            b"<hackersdiet><epoch>&b;</epoch></hackersdiet>")
    with pytest.raises(ImportFileError, match="valid Hacker's Diet XML"):
        parse_import(bomb)


@pytest.mark.parametrize(
    "data",
    [b"<hackersdiet><monthlogs>", b"<other><monthlogs/></other>", b"<?xml version='1.0'?>", b"<<<"],
)
def test_a_broken_or_foreign_xml_is_refused(data):
    with pytest.raises(ImportFileError, match="valid Hacker's Diet XML"):
        parse_import(data)


# ---- units, ranges and bad values -------------------------------------------


POUNDS = {(2020, 1): [(1, "180", ""), (2, "200.5", "heavier")]}


@pytest.mark.parametrize("make", [hd_csv, hd_xml])
def test_pounds_are_converted_to_kilograms(make):
    rows = parse_import(make(POUNDS, unit="pound")).rows
    assert rows[0] == (date(2020, 1, 1), pytest.approx(81.647, abs=0.001), None)
    assert rows[1] == (date(2020, 1, 2), pytest.approx(90.945, abs=0.001), "heavier")
    assert [row[1] for row in rows] == [81.647, 90.945]  # rounded to grams, no long tail


@pytest.mark.parametrize("make", [hd_csv, hd_xml])
def test_a_unit_that_cannot_be_converted_refuses_the_file(make):
    with pytest.raises(ImportFileError, match="stone"):
        parse_import(make(POUNDS, unit="stone"))


@pytest.mark.parametrize("make", [hd_csv, hd_xml])
def test_a_weight_out_of_range_is_reported_and_skipped(make):
    result = parse_import(make({(2020, 1): [(1, "600", ""), (2, "80", "")]}))
    assert result.rows == [(date(2020, 1, 2), 80.0, None)]
    assert len(result.errors) == 1 and "out of range" in result.errors[0]


@pytest.mark.parametrize("make", [hd_csv, hd_xml])
def test_an_invalid_weight_is_reported_with_where_it_is(make):
    result = parse_import(make({(2020, 1): [(1, "eighty", ""), (2, "80", "")]}))
    assert len(result.rows) == 1
    assert len(result.errors) == 1 and "eighty" in result.errors[0]
    assert ("2020-01-01" in result.errors[0]) or ("line" in result.errors[0])


def test_an_impossible_date_is_reported():
    xml = parse_import(hd_xml({(2021, 6): [(31, "80", "")]}))  # June has 30 days
    assert xml.rows == [] and "2021-06-31" in xml.errors[0]
    csv_result = parse_import(hd_csv({(2021, 6): [(31, "80", "")]}))
    assert csv_result.rows == [] and "invalid date" in csv_result.errors[0]


def test_a_decimal_comma_is_accepted_in_a_quoted_weight():
    data = b'Date,Weight,Rung,Flag,Comment\r\n2020-01-01,"80,5",,0,\r\n'
    assert parse_import(data).rows == [(date(2020, 1, 1), 80.5, None)]


# ---- recognising the format --------------------------------------------------


@pytest.mark.parametrize("prefix", [b"", b"\xef\xbb\xbf", b"\n\n  "])
def test_xml_is_recognised_by_its_content(prefix):
    assert parse_import(prefix + hd_xml()).rows == EXPECTED_ROWS


def test_a_csv_that_starts_at_the_column_header_is_recognised():
    data = hd_csv(with_epoch=False)
    assert data.startswith(b"Date,Weight,Rung")
    assert parse_import(data).rows == EXPECTED_ROWS


def test_a_plain_csv_is_still_the_plain_csv():
    result = parse_import(b"23-9-26,79.7,some text\n24-9-2026,80.5\n")
    assert result.rows == [(date(2026, 9, 23), 79.7, "some text"), (date(2026, 9, 24), 80.5, None)]


# ---- the plain CSV: the header rule and the new conveniences ----------------


@pytest.mark.parametrize(
    "header",
    ["date,weight,note", "Date,Weight,Note", "datum,gewicht,notitie", "Date;Weight;Note", "Weights", "date,weight"],
)
def test_a_header_as_the_first_line_is_skipped_without_a_warning(header):
    rows, errors = parse_csv(f"{header}\n23-9-26,79.7,x\n24-9-26,80.5,\n")
    assert errors == []
    assert [row[1] for row in rows] == [79.7, 80.5]


def test_blank_lines_before_the_header_do_not_matter():
    rows, errors = parse_csv("\n\n  \ndate,weight,note\n23-9-26,79.7,\n")
    assert errors == [] and len(rows) == 1


def test_a_file_with_only_a_header_has_no_rows_and_no_errors():
    assert parse_csv("date,weight,note\n") == ([], [])


def test_only_the_first_line_may_be_a_header():
    rows, errors = parse_csv("date,weight,note\n23-9-26,79.7,\ndate,weight,note\n24-9-26,80,\n")
    assert len(rows) == 2
    assert len(errors) == 1 and "line 3" in errors[0] and "invalid date" in errors[0]


@pytest.mark.parametrize("first", ["foo,80", "23-9-26,abc", "99-99-99,80"])
def test_a_first_line_that_is_half_data_is_still_reported(first):
    rows, errors = parse_csv(f"{first}\n24-9-26,80,\n")
    assert len(rows) == 1 and len(errors) == 1 and "line 1" in errors[0]


def test_a_first_data_line_is_never_mistaken_for_a_header():
    rows, errors = parse_csv("23-9-26,79.7,\n")
    assert rows == [(date(2026, 9, 23), 79.7, None)] and errors == []


def test_iso_dates_are_accepted():
    rows, errors = parse_csv("2026-09-23,79.7,iso\n2026-9-24,80\n")
    assert errors == []
    assert rows == [(date(2026, 9, 23), 79.7, "iso"), (date(2026, 9, 24), 80.0, None)]


def test_a_windows_1252_csv_keeps_its_accents():
    rows, _errors = parse_csv("23-9-26,79.7,caf\xe9 se\xf1or\n".encode("cp1252"))
    assert rows[0][2] == "café señor"


def test_a_utf_8_csv_with_a_bom_works():
    rows, errors = parse_csv("﻿date,weight,note\n23-9-26,79.7,x\n".encode())
    assert errors == [] and len(rows) == 1


# ---- through the web page ----------------------------------------------------


def upload(client, data, name="export.csv", **kwargs):
    client.get("/settings")  # show (and so clear) any message left over from signing in
    return client.post(
        "/settings/import",
        data={"csv_file": (io.BytesIO(data), name)},
        content_type="multipart/form-data",
        follow_redirects=True,
        **kwargs,
    )


@pytest.mark.parametrize(
    "make, name", [(hd_csv, "hackdiet_db.csv"), (hd_xml, "hackdiet_db.xml"), (hd_csv, "renamed.txt")]
)
def test_uploading_a_hacker_s_diet_export_saves_the_entries(logged_in_client, make, name):
    client, user_id = logged_in_client
    response = upload(client, make(), name)
    entries = WeightEntry.query.filter_by(user_id=user_id).order_by(WeightEntry.entry_date).all()
    assert [(e.entry_date, e.weight, e.note) for e in entries] == EXPECTED_ROWS
    messages = flashes(response)
    assert "Imported 5 entries." in messages
    assert "1 note without a weight was skipped." in messages


def test_the_xml_sets_the_height_when_there_is_none(logged_in_client):
    client, user_id = logged_in_client
    response = upload(client, hd_xml(), "hackdiet_db.xml")
    assert db.session.get(User, user_id).height_cm == 187.0
    assert "Your height was set to 1.87 m." in flashes(response)


def test_an_existing_height_is_not_overwritten(logged_in_client):
    client, user_id = logged_in_client
    client.post("/settings", data={"height_value": "186.4", "height_unit": "cm"})
    response = upload(client, hd_xml(), "hackdiet_db.xml")
    assert db.session.get(User, user_id).height_cm == pytest.approx(186.4)
    assert not any("height was set" in m for m in flashes(response))


def test_the_csv_leaves_the_height_alone(logged_in_client):
    client, user_id = logged_in_client
    upload(client, hd_csv(), "hackdiet_db.csv")
    assert db.session.get(User, user_id).height_cm is None


def test_importing_overwrites_an_entry_on_the_same_date_like_before(logged_in_client):
    client, user_id = logged_in_client
    upload(client, b"23-9-26,70,mine\n")
    upload(client, hd_csv({(2026, 9): [(23, "81.2", "")]}))
    entry = WeightEntry.query.filter_by(user_id=user_id).one()
    assert (entry.weight, entry.note) == (81.2, None)


def test_a_header_only_file_says_no_rows_and_no_warning(logged_in_client):
    client, _user_id = logged_in_client
    messages = flashes(upload(client, b"date,weight,note\n"))
    assert messages == ["No rows found in the file."]


def test_a_refused_file_says_why_and_imports_nothing(logged_in_client):
    client, user_id = logged_in_client
    messages = flashes(upload(client, hd_xml(POUNDS, unit="stone"), "x.xml"))
    assert any("stone" in m and "can't be imported" in m for m in messages)
    assert WeightEntry.query.filter_by(user_id=user_id).count() == 0
    messages = flashes(upload(client, b"<hackersdiet><monthlogs>", "broken.xml"))
    assert "That file isn't a valid Hacker's Diet XML export." in messages


def test_xml_files_are_allowed_and_other_types_are_not(logged_in_client):
    client, user_id = logged_in_client
    assert "CSV, XML or text files only." in flashes(upload(client, hd_xml(), "export.pdf"))
    assert WeightEntry.query.filter_by(user_id=user_id).count() == 0


def test_messages_are_translated(logged_in_client):
    client, _user_id = logged_in_client
    client.environ_base["HTTP_ACCEPT_LANGUAGE"] = "nl"
    messages = flashes(upload(client, hd_xml(), "x.xml"))
    joined = " ".join(messages).replace("Geïmporteerd: 5 metingen.", "5 metingen geïmporteerd.")
    assert "5 metingen geïmporteerd." in joined
    assert "Je lengte is ingesteld op 1,87 m." in messages
    assert "1 notitie zonder gewicht is overgeslagen." in messages


def test_the_import_page_mentions_the_hacker_s_diet(logged_in_client):
    client, _user_id = logged_in_client
    html = client.get("/settings").get_data(as_text=True)
    assert "export of The Hacker's Diet Online" in html


# ---- size --------------------------------------------------------------------


def test_an_import_may_be_bigger_than_the_general_upload_limit(logged_in_client):
    client, user_id = logged_in_client
    # a long log lists every day, so its XML is well over 1 MB, the limit everywhere else
    empty_days = {(y, m): [(d, "", "") for d in range(1, 29)] for y in range(2000, 2040) for m in range(1, 13)}
    big = hd_xml({**empty_days, (2041, 1): [(1, "80", "")]})
    assert len(big) > 1024 * 1024
    response = upload(client, big, "big.xml")
    assert response.status_code == 200
    assert WeightEntry.query.filter_by(user_id=user_id).count() == 1


def test_everything_else_keeps_the_one_megabyte_limit(client):
    # A form over the limit goes back with a message (app/errors.py) instead of a bare 413.
    response = client.post("/signup", data={"email": "a" * 2_000_000})
    assert response.status_code == 302
    assert "the limit is 1 MB" in client.get("/").get_data(as_text=True)


def test_an_import_above_the_import_limit_is_refused(logged_in_client, app):
    client, _user_id = logged_in_client
    app.config["IMPORT_MAX_CONTENT_LENGTH"] = 1024  # a tiny limit, to test the refusal cheaply
    response = client.post(
        "/settings/import",
        data={"csv_file": (io.BytesIO(b"23-9-26,70,\n" * 500), "big.csv")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 302 and response.location == "/"
    assert "The file is too large" in client.get("/").get_data(as_text=True)
    assert WeightEntry.query.count() == 0


def test_a_single_cell_line_that_is_a_date_is_still_reported_on_the_first_line():
    # only a line with neither a date nor a weight counts as a header
    rows, errors = parse_csv("23-9-26\n24-9-26,80\n")
    assert len(rows) == 1 and len(errors) == 1 and "line 1" in errors[0]


# ---- Yet Another Diet Tool ----------------------------------------------------
#
# Its export is `Date,Weight,Notes` (the header line is optional) with d-M-yy dates
# without leading zeros, two-decimal weights and every note in quotes. That is the
# plain CSV format, so it needs no importer of its own - these tests pin that.

YADT_ROWS = [
    ("1-8-07", "94.00", ""),
    ("12-8-07", "93.00", ""),
    ("31-8-07", "94.00", ""),
    ("1-9-07", "93.50", 'felt "light", for once'),  # a comma and quotes inside a quoted note
    ("14-10-26", "92.04", ""),
    ("3-10-26", "93.80", "señor"),
]
YADT_EXPECTED = [
    (date(2007, 8, 1), 94.0, None),
    (date(2007, 8, 12), 93.0, None),
    (date(2007, 8, 31), 94.0, None),
    (date(2007, 9, 1), 93.5, 'felt "light", for once'),
    (date(2026, 10, 14), 92.04, None),
    (date(2026, 10, 3), 93.8, "señor"),
]


def yadt_csv(rows=YADT_ROWS, header=True, newline="\n", encoding="utf-8"):
    """A file like the tool writes: only the notes are quoted (doubling any quote inside)."""
    lines = ["Date,Weight,Notes" + newline] if header else []
    for day, weight, note in rows:
        quoted = '"' + note.replace('"', '""') + '"'
        lines.append(f"{day},{weight},{quoted}{newline}")
    return "".join(lines).encode(encoding)


@pytest.mark.parametrize("header", [True, False])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_a_yadt_export_imports_with_or_without_its_header_line(header, newline):
    result = parse_import(yadt_csv(header=header, newline=newline))
    assert result.errors == []
    assert result.rows == YADT_EXPECTED
    assert result.height_cm is None and result.notes_skipped == 0


def test_the_two_yadt_variants_give_the_same_entries():
    assert parse_import(yadt_csv(header=True)).rows == parse_import(yadt_csv(header=False)).rows


def test_a_yadt_export_saved_as_windows_1252_keeps_its_accents():
    rows = parse_import(yadt_csv(encoding="cp1252")).rows
    assert rows[-1][2] == "señor"


def test_a_yadt_export_of_other_dates_with_a_header_has_no_warning_even_for_a_lone_header():
    assert parse_csv("Date,Weight,Notes\n") == ([], [])


def test_uploading_a_yadt_export_through_the_page_gives_only_the_success_message(logged_in_client):
    client, user_id = logged_in_client
    for header in (True, False):
        WeightEntry.query.filter_by(user_id=user_id).delete()
        db.session.commit()
        messages = flashes(upload(client, yadt_csv(header=header), "my-diet-data.csv"))
        assert messages == ["Imported 6 entries."], (header, messages)
        saved = {e.entry_date: (e.weight, e.note) for e in WeightEntry.query.filter_by(user_id=user_id)}
        assert len(saved) == 6
        assert saved[date(2007, 8, 1)] == (94.0, None)
        assert saved[date(2026, 10, 3)] == (93.8, "señor")


def test_importing_a_yadt_export_after_a_hacker_s_diet_one_overwrites_the_same_days(logged_in_client):
    client, user_id = logged_in_client
    upload(client, hd_csv({(2007, 8): [(1, "94", "from hd"), (2, "93", "")]}), "hackdiet_db.csv")
    upload(client, yadt_csv([("1-8-07", "94.00", "from yadt"), ("3-8-07", "92.00", "")]), "my-diet-data.csv")
    entries = {e.entry_date: (e.weight, e.note) for e in WeightEntry.query.filter_by(user_id=user_id)}
    assert entries == {
        date(2007, 8, 1): (94.0, "from yadt"),  # same day: the later import wins
        date(2007, 8, 2): (93.0, None),  # only in the first file: kept
        date(2007, 8, 3): (92.0, None),
    }


def test_the_import_page_names_both_tools(logged_in_client):
    client, _user_id = logged_in_client
    html = client.get("/settings").get_data(as_text=True)
    assert "The Hacker's Diet Online" in html and "Yet Another Diet Tool" in html
