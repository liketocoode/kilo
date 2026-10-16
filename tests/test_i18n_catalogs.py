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

"""Tests for scripts/check_i18n.py: the real catalogs pass, and every kind of gap is caught."""

import importlib.util
import shutil
from pathlib import Path

import pytest
from babel.messages.pofile import read_po, write_po

ROOT = Path(__file__).resolve().parent.parent
LANGUAGES = ["nl", "fr", "es", "pt", "de", "it", "id", "pl", "ro", "hu", "da", "fi", "sv", "nb"]

spec = importlib.util.spec_from_file_location("check_i18n", ROOT / "scripts" / "check_i18n.py")
check_i18n = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_i18n)


@pytest.fixture
def translations(tmp_path):
    """A scratch copy of translations/ to break on purpose."""
    target = tmp_path / "translations"
    shutil.copytree(ROOT / "translations", target, ignore=shutil.ignore_patterns("*.mo"))
    return target


def edit_catalog(path, change, locale=None):
    """Read a .po/.pot file, let `change(catalog)` modify it, write it back."""
    with open(path, "rb") as f:
        catalog = read_po(f, locale=locale)
    change(catalog)
    with open(path, "wb") as f:
        write_po(f, catalog, no_location=True, width=79)


def po(translations, lang):
    return translations / lang / "LC_MESSAGES" / "messages.po"


def problems(translations):
    return check_i18n.find_problems(ROOT, translations, LANGUAGES)


def test_the_supported_languages_come_from_the_app():
    from app.i18n import SUPPORTED_LANGUAGES

    assert check_i18n.supported_languages() == [lang for lang in SUPPORTED_LANGUAGES if lang != "en"]
    assert sorted(check_i18n.supported_languages()) == sorted(LANGUAGES)


def test_the_real_catalogs_are_complete_and_up_to_date():
    assert check_i18n.find_problems() == []


def test_a_stale_template_is_reported(translations):
    def drop_an_entry(catalog):
        del catalog["Signed out."]

    edit_catalog(translations / "messages.pot", drop_an_entry)
    found = problems(translations)
    assert any("messages.pot is missing 'Signed out.'" in line for line in found)
    assert any("out of date" in line and "pybabel extract" in line for line in found)


def test_a_template_entry_that_left_the_source_is_reported(translations):
    def add_an_entry(catalog):
        catalog.add("A string nobody uses any more")

    edit_catalog(translations / "messages.pot", add_an_entry)
    assert any("no longer in the source" in line for line in problems(translations))


def test_untranslated_entries_are_reported(translations):
    def blank(catalog):
        catalog["Signed out."].string = ""

    edit_catalog(po(translations, "nl"), blank, locale="nl")
    assert any(line.startswith("nl: untranslated entry 'Signed out.'") for line in problems(translations))


def test_an_untranslated_plural_form_is_reported(translations):
    def blank_one_form(catalog):
        message = catalog[("Imported %(num)d entry.", "Imported %(num)d entries.")]
        message.string = (message.string[0], "")

    edit_catalog(po(translations, "de"), blank_one_form, locale="de")
    assert any(line.startswith("de: untranslated entry 'Imported") for line in problems(translations))


def test_fuzzy_entries_are_reported(translations):
    def make_fuzzy(catalog):
        catalog["Signed out."].flags.add("fuzzy")

    edit_catalog(po(translations, "fr"), make_fuzzy, locale="fr")
    assert any(line.startswith("fr: fuzzy entry 'Signed out.'") for line in problems(translations))


def test_a_missing_plural_form_is_reported(translations):
    def one_form_only(catalog):
        message = catalog[("Imported %(num)d entry.", "Imported %(num)d entries.")]
        message.string = (message.string[0],)

    edit_catalog(po(translations, "it"), one_form_only, locale="it")
    assert any(line.startswith("it: untranslated entry 'Imported") for line in problems(translations))


def test_a_missing_plural_forms_header_is_reported(translations):
    path = po(translations, "nl")
    text = path.read_text(encoding="utf-8")
    path.write_text(
        "\n".join(line for line in text.split("\n") if not line.startswith('"Plural-Forms:')),
        encoding="utf-8",
    )
    assert any(line.startswith("nl: the header has no Plural-Forms") for line in problems(translations))


def test_a_broken_placeholder_is_reported(translations):
    def misspell(catalog):
        catalog["We sent a new code to %(email)s."].string = "Abbiamo inviato un nuovo codice a %(emial)s."

    edit_catalog(po(translations, "it"), misspell, locale="it")
    assert any(line.startswith("it:") and "We sent a new code" in line for line in problems(translations))


def test_a_missing_entry_is_reported(translations):
    def remove(catalog):
        del catalog["Signed out."]

    edit_catalog(po(translations, "es"), remove, locale="es")
    assert any(line.startswith("es: missing entry 'Signed out.'") for line in problems(translations))


def test_a_stale_entry_is_reported(translations):
    def add(catalog):
        catalog.add("An old string", string="Una cadena antigua")

    edit_catalog(po(translations, "es"), add, locale="es")
    assert any(line.startswith("es: stale entry 'An old string'") for line in problems(translations))


def test_a_language_without_a_catalog_is_reported(translations):
    shutil.rmtree(translations / "pt")
    found = problems(translations)
    assert any(line.startswith("pt: no catalog") and "pybabel init" in line for line in found)


def test_machine_translated_entries_are_counted(translations):
    # On a scratch copy, so the test does not depend on how far the native reviews are (a
    # reviewed language has no markers left: Finnish since PR #16).
    def mark_all(catalog):
        for message in catalog:
            if message.id and "machine-translated" not in message.user_comments:
                message.user_comments.append("machine-translated")

    def unmark_all(catalog):
        for message in catalog:
            message.user_comments = [c for c in message.user_comments if c != "machine-translated"]

    edit_catalog(po(translations, "da"), mark_all, locale="da")
    edit_catalog(po(translations, "sv"), unmark_all, locale="sv")
    with open(po(translations, "da"), "rb") as f:
        entries = sum(1 for m in read_po(f, locale="da") if m.id)

    counts = check_i18n.count_machine_translated(translations, LANGUAGES)
    assert set(counts) == set(LANGUAGES)
    assert counts["da"] == entries > 0
    assert counts["sv"] == 0


def test_a_fully_reviewed_language_is_not_listed_as_needing_review(monkeypatch, capsys):
    monkeypatch.setattr(check_i18n, "find_problems", lambda: [])
    monkeypatch.setattr(check_i18n, "count_machine_translated", lambda _dir, _langs: {"nl": 3, "fi": 0})
    assert check_i18n.main() == 0
    out = capsys.readouterr().out
    assert "nl: 3 entries still marked machine-translated" in out
    assert "fi:" not in out


def test_the_command_line_fails_when_there_are_problems(monkeypatch, capsys):
    monkeypatch.setattr(check_i18n, "find_problems", lambda: ["nl: untranslated entry 'x'"])
    assert check_i18n.main() == 1
    assert "nl: untranslated entry 'x'" in capsys.readouterr().err


def test_the_command_line_succeeds_when_everything_is_in_order(monkeypatch, capsys):
    monkeypatch.setattr(check_i18n, "find_problems", list)
    assert check_i18n.main() == 0
    assert "Translations OK" in capsys.readouterr().out
