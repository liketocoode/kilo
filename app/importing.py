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

"""Pick the right importer for an uploaded file.

The format is recognised from the content, never from the file name, so a
Hacker's Diet export renamed to .txt still works: XML starts with "<", a
Hacker's Diet CSV with its `Epoch` line or its column header, and anything else
is the plain `date,weight[,note]` CSV.
"""

from app.csv_io import LEADING_JUNK, decode_text, parse_generic_csv
from app.hackdiet_io import looks_like_hackdiet_csv, parse_hackdiet_csv, parse_hackdiet_xml


def parse_import(file_bytes):
    """Parse an uploaded file into an `ImportResult`; may raise `csv_io.ImportFileError`."""
    if file_bytes.lstrip(LEADING_JUNK)[:1] == b"<":
        return parse_hackdiet_xml(file_bytes)
    text = decode_text(file_bytes)
    if looks_like_hackdiet_csv(text):
        return parse_hackdiet_csv(text)
    return parse_generic_csv(text)
