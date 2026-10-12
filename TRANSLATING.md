# Translating Kilo Tracker

Kilo is translated into Dutch (`nl`), French (`fr`), Spanish (`es`), Brazilian
Portuguese (`pt`), German (`de`), Italian (`it`), Indonesian (`id`), Polish
(`pl`), Romanian (`ro`), Hungarian (`hu`), Danish (`da`), Finnish (`fi`), Swedish
(`sv`) and Norwegian Bokmål (`nb`). English (`en`) is the source language and the
fallback. All fourteen translations were **machine-generated** and need review
by people who speak the language; that is the most useful contribution you can
make.

Visitors can pick a language in the language menu (in the header, or in the account
menu when signed in; the names come from `LANGUAGE_NAMES` in `app/i18n.py`, so a new
language needs its own name there). The choice is kept in a `kilo_lang` cookie. Without
one, the page language follows the browser's
`Accept-Language` header, matched on the first part of the tag (`nl-BE` and
`nl-NL` both give Dutch, `pt-PT` gives Brazilian Portuguese). Browsers send
`no` or `nn` for Norwegian; both give Bokmål (`LANGUAGE_ALIASES` in
`app/i18n.py`). For testing, `?lang=de` on any URL overrides it for that request
(nothing is stored).

## Where things are

| What | Where |
| --- | --- |
| Supported languages | `SUPPORTED_LANGUAGES` in `app/i18n.py` (the only list) |
| Source strings (template) | `translations/messages.pot` |
| One catalog per language | `translations/<lang>/LC_MESSAGES/messages.po` |
| Compiled catalogs | `messages.mo` next to each `.po`: **not in git**, built at deploy time |
| Extraction settings | `babel.cfg` |
| Completeness check | `scripts/check_i18n.py` |

You need the development setup from the README (`pip install -r
requirements-dev.txt`), which installs Flask-Babel and its `pybabel` tool. The
commands below assume the virtual environment is active (otherwise use
`.venv/bin/pybabel` and `.venv/bin/python`).

## Try a language

```bash
pybabel compile -d translations
python run.py
```

Open <http://127.0.0.1:5000/?lang=nl> (or any supported language). Recompile
after every change to a `.po` file.

## Improve an existing translation

1. Edit `translations/<lang>/LC_MESSAGES/messages.po`. Each entry has the
   English text (`msgid`), your translation (`msgstr`) and sometimes a note for
   translators (a line starting with `#.`). Plural entries have `msgstr[0]`
   (singular) and `msgstr[1]` (plural).
2. Once a person has reviewed an entry, delete its `# machine-translated` line.
   Entries that still have it are the ones that need a look; the check script
   (below) counts them per language.
3. Run `pybabel compile -d translations`, look at the result in the browser, and
   run `python scripts/check_i18n.py`.

Keep these things exactly as in the English text:

* placeholders such as `%(email)s`, `%(num)d` and `%(grams)s` (you may move them
  to where the sentence needs them), and `%%`, which is a literal percent sign;
* the HTML tags `<strong>` and `<code>`, and `&middot;`;
* the name **Kilo** and **Kilo Tracker**.

Translate the text inside `<code>` on the import page: `date,weight,note` names
the three columns (`datum,gewicht,notitie`) and `d-M-yy` / `d-M-yyyy` describe
the date pattern with the first letters of day, month and year in your language
(`j-M-aa` in French, `T-M-JJ` in German). Keep a capital `M` for the month so it
cannot be mistaken for minutes. These are only descriptions: the file itself has
no header and is the same in every language, such as `23-9-26,79.7,some text`
(day-month-year, a point as the decimal separator).

### Style

* Short, friendly and neutral. Avoid judgmental wording about gaining or losing
  weight.
* Address the user informally where that is normal for apps: *je* (nl), *du*
  (de, da, sv, nb), *tú* (es), *você* (pt, as is usual in Brazilian apps), *tu*
  (it, ro), *kamu* (id), *te* (hu), *sinä* (fi) and the informal imperative in
  Polish. French uses *vous*, which is still the norm in French apps. Where the
  source speaks as the author ("I tried ..."), avoid wording that reveals
  gender (Polish, for example, uses an impersonal form).
* Use the unit letters people use in your language for the chart ranges (`1J`
  for one year in Dutch and German, `1A` in French, Spanish, Portuguese and
  Italian, `1v` in Finnish; `1S` for one week in the Romance languages, `1T` in
  Polish, `1vk` in Finnish).
* Be consistent: the same word for "entry", "weigh-in", "moving average", "BMI"
  everywhere.

## Update the catalogs after the code changed

Whenever a string is added, removed or reworded in the code or the templates:

**1. Extract** the strings into the template:

```bash
pybabel extract -F babel.cfg -k _l -c NOTE: --no-location \
  --project="Kilo Tracker" --copyright-holder="Ramón de Vries" \
  --msgid-bugs-address=info@11tools.com -o translations/messages.pot .
```

**2. Merge** the template into every catalog with this script, run from the
project folder:

```bash
python - <<'EOF'
from pathlib import Path

from babel.messages.pofile import read_po, write_po

with open("translations/messages.pot", "rb") as f:
    template = read_po(f)

for path in sorted(Path("translations").glob("*/LC_MESSAGES/messages.po")):
    with open(path, "rb") as f:
        catalog = read_po(f, locale=path.parent.parent.name)
    catalog.update(template, no_fuzzy_matching=True, update_header_comment=False)
    with open(path, "wb") as f:
        write_po(f, catalog, width=79, no_location=True, ignore_obsolete=True)
    empty = sum(1 for m in catalog if m.id and not m.string)
    print(f"{path.parent.parent.name}: {empty} to translate")
EOF
```

It adds the new entries (with an empty translation), empties the entries whose
English text changed, removes entries that are no longer used, and updates
`POT-Creation-Date`. Everything else in the files stays as it was.

Do not use `pybabel update` for this step. It re-wraps every long line in every
catalog at 76 columns, while these files are wrapped at 79, so one new string
turns into a diff of well over a hundred lines per language and hides what
really changed.

**3. Translate** the new, empty entries in every `.po` file (a plural entry
needs both `msgstr[0]` and `msgstr[1]`; mark machine translations with a
`# machine-translated` line above the entry), run
`pybabel compile -d translations` and `python scripts/check_i18n.py`.

`no_fuzzy_matching=True` keeps Babel from guessing: a changed English string
becomes an empty entry that has to be translated again, instead of a "fuzzy"
entry. Fuzzy entries are skipped by `pybabel compile`, so they would show up in
English; the check script fails on them.

## Add a language

1. Add its code to `SUPPORTED_LANGUAGES` in `app/i18n.py`, and its name, written in the
   language itself, to `LANGUAGE_NAMES` there (for the language menu).
2. Create the catalog (replace `xx`):

   ```bash
   pybabel init -i translations/messages.pot -d translations -l xx
   ```

3. Look at the `Plural-Forms` line in the new file's header. It must be right for
   the language (languages differ in how many plural forms they have, and
   whether 0 is singular, as in French and Brazilian Portuguese). `pybabel init`
   fills in the usual rule; fix it if it is wrong for your variant.
4. Translate every entry as described above and mark each one with `# machine-translated`
   unless a person wrote it.
5. Run `pybabel compile -d translations`, then check the layout in the browser
   at desktop width and on a phone (390 px wide): long words and long dates must
   not overflow. The date column of the daily log has its own width variable
   (`--day-date-col` in `static/style.css`) for languages whose dates are long
   (Brazilian Portuguese and Hungarian have one; a language that wraps its
   dates in the log needs one too).
6. If the language's browser code differs from the one you used (like `no` for
   `nb`), add it to `LANGUAGE_ALIASES` in `app/i18n.py`.
7. Run `python scripts/check_i18n.py` and `pytest`.

Only left-to-right languages are supported for now.

## Write translatable strings in the code

* Python: `gettext` (`_()`) at request time, `lazy_gettext` (`_l()`) for strings
  defined at import time (form labels, module constants), `ngettext` for
  anything with a count. Templates use `{{ _("...") }}` and `{% trans %}`.
* Write whole sentences with placeholders: `_("You lost %(amount)s", amount=x)`.
  Never glue translated fragments together, and never use `n == 1 ? ... : ...`.
* Short or ambiguous strings ("Save", "Goal") get a note for translators: a
  `# NOTE: ...` comment on the line above in Python, `{# NOTE: ... #}` in a
  template.
* JavaScript has no gettext: the server renders the strings it needs as JSON in
  `<script type="application/json" id="i18n">` (see `index.html`) and the page
  script reads them with `t("key")`.
* Never build numbers or dates by hand. Use `Intl.NumberFormat` and
  `Intl.DateTimeFormat` in JavaScript, the `kg`, `decimal1`, `localdate` template
  filters and Flask-Babel's `format_decimal`/`format_date` in Python. Weights are
  always kilograms; what is stored is locale-neutral (floats and ISO dates).
* Weights and heights are typed with either `72,5` or `72.5` in every language;
  `parse_decimal` in `app/utils.py` is the single parser.

## The check

```bash
python scripts/check_i18n.py
```

It fails (exit status 1) when `translations/messages.pot` is out of date with the
source, or when a supported language has a missing or stale entry, an
untranslated or fuzzy entry, a broken placeholder or no catalog at all. It also
prints how many entries per language are still marked `# machine-translated`.
`pytest` runs the same check (`tests/test_i18n_catalogs.py`), so a pull request
that adds a string without translating it fails the tests.
