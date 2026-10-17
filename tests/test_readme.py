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

"""Tests that the README documents every environment variable the app reads."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")


def variables_the_app_reads():
    """Every name read with os.environ.get("NAME", ...) , _env_int("NAME", ...) or _env_flag("NAME", ...) in app/."""
    names = set()
    for source in (ROOT / "app").glob("*.py"):
        text = source.read_text(encoding="utf-8")
        names |= set(re.findall(r'(?:os\.environ\.get|_env_int|_env_flag)\("([A-Z][A-Z0-9_]+)"', text))
    return names


def configuration_section():
    """The README text from the kilo.env example to the end of the variable table."""
    start = README.index("### 2. Configure with `kilo.env`")
    table = README.index("| Variable | Default | Purpose |", start)
    end = README.index("\n\n", table)
    return README[start:end], README[start:table], README[table:end]


def test_the_app_reads_the_variables_this_test_expects():
    # a guard for the test itself: if the pattern stopped finding variables, nothing below would
    names = variables_the_app_reads()
    assert {"SECRET_KEY", "MAIL_SERVER", "RATELIMIT_MODE", "SECURITY_LOG_FILE"} <= names
    assert len(names) >= 15


def test_every_variable_is_in_the_kilo_env_example():
    _, example, _ = configuration_section()
    # in the example, active or as a commented-out default
    missing = [n for n in sorted(variables_the_app_reads()) if not re.search(rf"^#? ?{n}=", example, re.MULTILINE)]
    assert missing == []


def test_every_variable_is_in_the_table_of_settings():
    _, _, table = configuration_section()
    assert [name for name in sorted(variables_the_app_reads()) if f"`{name}`" not in table] == []
