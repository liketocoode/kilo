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

"""Remove stale sign-ups and clear expired code hashes (see app/cleanup.py).

    python scripts/cleanup_signups.py --dry-run   # only print what would happen
    python scripts/cleanup_signups.py             # do it

Run it from the project folder with the same environment as the app (it reads the
same database). Safe to run at any time, and twice in a row. A daily systemd timer
for it is in deploy/systemd/, see the README.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.cleanup import main

if __name__ == "__main__":
    sys.exit(main())
