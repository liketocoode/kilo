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

"""Made-up files in the shape of the real exports, for the import tests.

The Hacker's Diet exports follow the real ones: a per-month header and StartTrend line in the
CSV, Windows-1252 text, days without a weight, a note without a weight, and an XML with a
DOCTYPE that points at a DTD. Never copy a real export in here (they are personal data).
"""

from datetime import date

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
