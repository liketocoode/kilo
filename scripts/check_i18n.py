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

"""Check that the translation catalogs are complete and up to date.

Fails (exit status 1) when

* translations/messages.pot is out of date with the strings in the source, or
* a supported language has no catalog, no Plural-Forms header, a missing or stale
  entry, an untranslated entry (an empty msgstr, or an empty plural form), a fuzzy
  entry (`pybabel compile` skips those) or a broken placeholder.

Run it from anywhere: `python scripts/check_i18n.py`. See TRANSLATING.md.
"""

import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from babel.messages.pofile import read_po

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The `pybabel extract` options that produce translations/messages.pot (the same
# ones TRANSLATING.md tells contributors to use).
EXTRACT_ARGS = [
    "-F", "babel.cfg",
    "-k", "_l",
    "-c", "NOTE:",
    "--no-location",
    "--project=Kilo Tracker",
    "--copyright-holder=Ramón de Vries",
    "--msgid-bugs-address=info@11tools.com",
]
EXTRACT_COMMAND = shlex.join(["pybabel", "extract", *EXTRACT_ARGS, "-o", "translations/messages.pot", "."])


def supported_languages():
    """The languages listed in app/i18n.py, without "en" (the source language)."""
    sys.path.insert(0, str(PROJECT_ROOT))
    from app.i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES

    return [lang for lang in SUPPORTED_LANGUAGES if lang != DEFAULT_LANGUAGE]


def extract_template(project_root, out_path):
    """Run `pybabel extract` over the source in `project_root`, writing the template to `out_path`."""
    result = subprocess.run(
        [sys.executable, "-m", "babel.messages.frontend", "extract", *EXTRACT_ARGS, "-o", str(out_path), "."],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("pybabel extract failed:\n" + result.stderr)


def _entries(catalog):
    """{msgid: translator notes} for the real entries of a catalog (not the header)."""
    return {message.id: tuple(message.auto_comments) for message in catalog if message.id}


def _show(msgid):
    text = msgid[0] if isinstance(msgid, tuple) else msgid
    return repr(text if len(text) <= 60 else text[:57] + "...")


def _template_problems(source_pot, committed_pot):
    """Differences between the template extracted from the source and the committed one."""
    with open(source_pot, "rb") as f:
        fresh = _entries(read_po(f))
    with open(committed_pot, "rb") as f:
        committed = _entries(read_po(f))

    problems = []
    for msgid in fresh.keys() - committed.keys():
        problems.append(f"messages.pot is missing {_show(msgid)}")
    for msgid in committed.keys() - fresh.keys():
        problems.append(f"messages.pot has {_show(msgid)}, which is no longer in the source")
    for msgid in fresh.keys() & committed.keys():
        if fresh[msgid] != committed[msgid]:
            problems.append(f"messages.pot has outdated translator notes for {_show(msgid)}")
    if problems:
        problems.append(f"messages.pot is out of date; regenerate it with: {EXTRACT_COMMAND}")
    return problems


def _entry_problems(lang, entries, template):
    """Fuzzy and untranslated entries among those the template still has."""
    problems = []
    for msgid, message in entries.items():
        if msgid not in template:
            continue
        if message.fuzzy:
            problems.append(f"{lang}: fuzzy entry {_show(msgid)} (review it, then remove the fuzzy flag)")
        # Babel pads a plural entry with fewer forms than the language needs with
        # empty strings, so a missing form shows up here as untranslated too.
        forms = message.string if isinstance(message.string, (tuple, list)) else (message.string,)
        if not all(form and form.strip() for form in forms):
            problems.append(f"{lang}: untranslated entry {_show(msgid)}")
    return problems


def _catalog_problems(lang, po_path, template):
    """Problems in one language's catalog, compared with the template's entries."""
    if not po_path.exists():
        return [
            (
                f"{lang}: no catalog at {po_path.relative_to(po_path.parents[3])}; "
                f"create it with: pybabel init -i translations/messages.pot -d translations -l {lang}"
            )
        ]

    with open(po_path, "rb") as f:
        catalog = read_po(f, locale=lang)
    entries = {message.id: message for message in catalog if message.id}

    problems = []
    # Babel fills in a default rule when the header is absent, so look at the file itself.
    if '"Plural-Forms:' not in po_path.read_text(encoding="utf-8"):
        problems.append(f"{lang}: the header has no Plural-Forms line (plurals would use a default rule)")
    for msgid in template.keys() - entries.keys():
        problems.append(f"{lang}: missing entry {_show(msgid)} (merge the template: see TRANSLATING.md)")
    for msgid in entries.keys() - template.keys():
        problems.append(f"{lang}: stale entry {_show(msgid)} (merge the template: see TRANSLATING.md)")

    problems += _entry_problems(lang, entries, template)

    for message, errors in catalog.check():
        for error in errors:
            problems.append(f"{lang}: {_show(message.id)}: {error}")
    return problems


def find_problems(project_root=PROJECT_ROOT, translations_dir=None, languages=None):
    """All problems found, as a list of readable lines (empty when everything is in order).

    `translations_dir` defaults to <project_root>/translations; `languages` to the
    supported languages from app/i18n.py. Both can be overridden for tests.
    """
    project_root = Path(project_root)
    translations_dir = Path(translations_dir) if translations_dir else project_root / "translations"
    languages = supported_languages() if languages is None else languages

    committed_pot = translations_dir / "messages.pot"
    if not committed_pot.exists():
        return [f"{committed_pot} does not exist; create it with: {EXTRACT_COMMAND}"]

    with tempfile.TemporaryDirectory() as tmp:
        fresh_pot = Path(tmp) / "messages.pot"
        extract_template(project_root, fresh_pot)
        problems = _template_problems(fresh_pot, committed_pot)

    with open(committed_pot, "rb") as f:
        template = _entries(read_po(f))
    for lang in languages:
        problems += _catalog_problems(lang, translations_dir / lang / "LC_MESSAGES" / "messages.po", template)
    return problems


def count_machine_translated(translations_dir, languages):
    """{language: number of entries still marked "# machine-translated"} (needs a human review)."""
    counts = {}
    for lang in languages:
        po_path = Path(translations_dir) / lang / "LC_MESSAGES" / "messages.po"
        if po_path.exists():
            with open(po_path, "rb") as f:
                catalog = read_po(f, locale=lang)
                counts[lang] = sum(1 for m in catalog if m.id and "machine-translated" in m.user_comments)
    return counts


def main():
    problems = find_problems()
    if problems:
        print("Translation check failed:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    languages = supported_languages()
    print(f"Translations OK: messages.pot is current and {', '.join(languages)} are complete.")
    for lang, count in count_machine_translated(PROJECT_ROOT / "translations", languages).items():
        if count:
            print(f"  {lang}: {count} entries still marked machine-translated (need a review)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
