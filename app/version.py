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

"""The running version of Kilo Tracker, read from the git checkout.

The version is the tag of the checked-out commit if it has one, otherwise the
7-character commit id, plus the commit date. It's read once (when first
needed) and cached, so a deployed checkout needs a restart to show a new
version. Without git or outside a repository there is no version.
"""

import os
import subprocess
from functools import lru_cache

from app.utils import PROJECT_ROOT

SHORT_ID_LENGTH = 7


def _git(repo_dir, *args):
    """Output of `git <args>` run in `repo_dir`, or None if it fails."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def read_version(repo_dir):
    """Version info for the checkout in `repo_dir`, or None if unavailable.

    A dict with `commit` (7-character id), `tag` (the newest tag pointing at
    HEAD, or None), `date` (commit date, YYYY-MM-DD) and `label` (the tag if
    there is one, otherwise the commit id).
    """
    commit = _git(repo_dir, "rev-parse", f"--short={SHORT_ID_LENGTH}", "HEAD")
    if commit is None:
        return None
    tags = _git(repo_dir, "tag", "--points-at", "HEAD", "--sort=-v:refname")
    tag = tags.splitlines()[0] if tags else None
    return {
        "commit": commit,
        "tag": tag,
        "date": _git(repo_dir, "log", "-1", "--format=%cs", "HEAD"),
        "label": tag or commit,
    }


@lru_cache(maxsize=1)
def app_version():
    """Version info of this app's own checkout (see `read_version`)."""
    return read_version(PROJECT_ROOT)
