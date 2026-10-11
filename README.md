# Kilo Tracker

A small Flask app for logging your weight over time: add daily entries, see
a trend chart, and track your progress with the 7-, 14-, 30-, 90-, 180- and
365-day change and the total change, all based on a moving average.

## Try it now

Want to see where your weight is really heading? **Kilo Tracker is free to use
at [kilo.11tools.com](https://kilo.11tools.com)** - no install, no password and
nothing to pay. Sign in with just your email address, log your weight in
seconds, and watch the trend line cut through the daily ups and downs. It works
great on your phone, shows your BMI at a glance, and you can import or export
all your data as CSV whenever you like (the CSV and XML exports of
[The Hacker's Diet Online](https://www.fourmilab.ch/hackdiet/online/hdo.html) and
the CSV export of [Yet Another Diet Tool](https://yadt.iotide.com/) can be
imported too). You can even share your chart on a public page, or embed it in
your own website.
Give it a spin, and if you'd rather host it yourself, everything you need is
below.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pybabel compile -d translations
```

## Run

```bash
python run.py
```

Then open http://127.0.0.1:5000. Data is stored in `instance/weight.db`
(SQLite, created automatically).

Weights are in kilograms and heights in centimetres or metres.

## Languages

Kilo speaks English, Dutch, French, Spanish, Brazilian Portuguese, German,
Italian, Indonesian, Polish, Romanian, Hungarian, Danish, Finnish, Swedish and
Norwegian (Bokmål). The language follows the browser's `Accept-Language` setting (there is
no switcher); add `?lang=de` to a URL to try another one. The translations are
machine-generated and welcome improvements: see [TRANSLATING.md](TRANSLATING.md).
The compiled catalogs (`*.mo`) are not in git: run `pybabel compile -d
translations` after cloning and after every update, as below.

## Public chart

Under **Settings → Public chart** a user can share their chart (with the BMI
axis) on a public page at `/public/<id>`, which anyone with the link can open
and which can be embedded in another website; the settings show the address and
an `<iframe>` code to copy. By default the page shows only the chart and its
range buttons; the stat boxes (including the BMI and height) can be switched on
there. The day notes, the email address and the daily log are never shown. The proposed ID is the SHA-256
of the user's email address and the time in seconds (not the email hash, which is
also the Gravatar id); the user can choose their own instead: 3 to 64 letters
(a-z), digits, hyphens or underscores, unique among all users and not
case-sensitive. Turning sharing off keeps the ID, so turning it on again brings
back the same address. Add `?theme=dark` or `?theme=light` to the address to
override the owner's theme. The page sets no framing restriction, so any site
can show it; `robots.txt` asks search engines to leave it alone. An address
without a shared chart answers 404 and is written to the security log like any
unknown page.

## Email sign-up

Visit `/signup` to sign up with just an email address: you'll receive a
6-digit verification code by email (valid for 30 minutes) and entering it
signs you in. By default outgoing mail is suppressed and the code is only
logged to the console, so sign-up works out of the box with no mail server.

To send real emails in production, configure an SMTP server via env vars and
turn off suppression:

```bash
export MAIL_SERVER=smtp.example.com
export MAIL_PORT=587
export MAIL_USE_TLS=1
export MAIL_USERNAME=...
export MAIL_PASSWORD=...
export MAIL_DEFAULT_SENDER=no-reply@example.com
export MAIL_SUPPRESS_SEND=0
```

## Production

`python run.py` starts the Flask development server with the debugger on. Don't
use it in production; run the app under [gunicorn](https://gunicorn.org/)
instead, behind a reverse proxy that terminates HTTPS.

### 1. Install

```bash
git clone https://github.com/ramondevries/kilo.git /srv/kilo
cd /srv/kilo
python -m venv .venv
.venv/bin/pip install -r requirements.txt gunicorn
.venv/bin/pybabel compile -d translations
```

Create a system user to run it (for example `kilo`) that owns `/srv/kilo`. The
`instance/` folder inside it holds the SQLite database (`weight.db`) and must
be writable by that user.

### 2. Configure with `kilo.env`

All settings are environment variables. Put them in an environment file, for
example `/etc/kilo.env`, readable only by the service user
(`chmod 600 /etc/kilo.env`), and keep it out of git:

```bash
# Signs the session cookies. Required in production: generate one with
#   python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=replace-with-64-random-hex-characters

# Production runs behind HTTPS: only ever send the sign-in cookie over it.
SESSION_COOKIE_SECURE=1

# Outgoing mail (see "Email sign-up" above). MAIL_SUPPRESS_SEND must be 0,
# otherwise no email is sent and the codes are only written to the log.
MAIL_SERVER=smtp.example.com
MAIL_PORT=587
MAIL_USE_TLS=1
MAIL_USERNAME=your-smtp-user
MAIL_PASSWORD=your-smtp-password
MAIL_DEFAULT_SENDER=Kilo Tracker <no-reply@example.com>
MAIL_SUPPRESS_SEND=0

# Optional: set to 0 to skip the MX-record check of the email domain at
# sign-up (it needs outbound DNS from the server).
# CHECK_EMAIL_MX=1

# Optional: set to 0 to never contact Gravatar; everyone then gets the local
# placeholder avatar. With 1 the server fetches avatars over HTTPS (see "Avatars").
# GRAVATAR_ENABLED=1

# Log of suspicious requests (unknown pages, wrong sign-in codes) for fail2ban;
# the user that runs gunicorn must be able to write there. See "Security log".
SECURITY_LOG_FILE=/var/log/gunicorn/kilo-security.log

# Limits on emailed codes (see "Limits on emailed codes"). "log" refuses nothing
# and only logs what it WOULD refuse: leave it so for a week or two, look at the
# security log, and then change it to "enforce". "off" switches the limits off.
RATELIMIT_MODE=log
# The numbers, shown with their defaults (uncomment to change one; 0 turns that
# limit off). Codes per address wait this many seconds and are limited per hour;
# a "visitor" is one client address (a /64 for IPv6).
# RATELIMIT_COOLDOWN_SECONDS=60
# RATELIMIT_ADDRESS_PER_HOUR=5
# RATELIMIT_REMOVAL_PER_HOUR=3
# RATELIMIT_IP_PER_HOUR=20
# RATELIMIT_GLOBAL_PER_HOUR=60
```

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | `dev` | Session signing key. Always set your own (the app warns in its log if it is `dev` or short). |
| `SESSION_COOKIE_SECURE` | `0` | `1` in production: the sign-in cookie is then only sent over HTTPS |
| `MAIL_SERVER` | `localhost` | SMTP host |
| `MAIL_PORT` | `25` | SMTP port, usually `587` |
| `MAIL_USE_TLS` | `0` | `1` for STARTTLS (port 587). Implicit SSL on port 465 is not supported. |
| `MAIL_USERNAME`, `MAIL_PASSWORD` | unset | SMTP login |
| `MAIL_DEFAULT_SENDER` | `no-reply@weight-tracker.local` | The From address |
| `MAIL_SUPPRESS_SEND` | `1` | `0` to really send mail |
| `CHECK_EMAIL_MX` | `1` | `0` to disable the MX-record check at sign-up |
| `GRAVATAR_ENABLED` | `1` | `0` to never contact Gravatar and show a placeholder avatar for everyone; see "Avatars" below |
| `RATELIMIT_MODE` | `log` | Limits on emailed codes: `off`, `log` (count and log what would be refused, refuse nothing) or `enforce`; see "Limits on emailed codes" below |
| `RATELIMIT_COOLDOWN_SECONDS` | `60` | Wait between two codes for one address |
| `RATELIMIT_ADDRESS_PER_HOUR` | `5` | Sign-in codes an hour for one address |
| `RATELIMIT_REMOVAL_PER_HOUR` | `3` | Account-removal codes an hour for one address |
| `RATELIMIT_IP_PER_HOUR` | `20` | Codes an hour started from one visitor address (a /64 for IPv6) |
| `RATELIMIT_GLOBAL_PER_HOUR` | `60` | Codes an hour for the whole site |
| `SECURITY_LOG_FILE` | unset | File for the security log (unknown pages, wrong sign-in codes); see "Security log" below. Nothing is logged when unset. |

Signed-in users stay signed in when they close the browser: the `session` cookie lasts
30 days after the last visit (renewed on every visit) and never more than 90 days after the
sign-in, after which the email code is asked for again. Logging out removes it. The cookie is
signed with `SECRET_KEY` (anyone who knows the key can forge a sign-in, so keep it secret and
constant; changing it signs everyone out) and holds the signed-in address, so with
`SESSION_COOKIE_SECURE=1` it is only sent over HTTPS.

Values with spaces or `<>` (such as `MAIL_DEFAULT_SENDER`) are fine in a
systemd `EnvironmentFile`; if you source the file from a shell instead, quote
them.

### 3. Run gunicorn with systemd

`run.py` exposes the app as `run:app`. Create `/etc/systemd/system/kilo.service`:

```ini
[Unit]
Description=Kilo Tracker
After=network.target

[Service]
User=kilo
WorkingDirectory=/srv/kilo
EnvironmentFile=/etc/kilo.env
Environment="PATH=/srv/kilo/.venv/bin:/usr/bin"
ExecStart=/srv/kilo/.venv/bin/gunicorn --workers 3 --bind 127.0.0.1:8000 run:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now kilo
sudo systemctl status kilo
journalctl -u kilo -f        # logs (verification codes too, if mail is suppressed)
```

`--workers 3` is a sensible start; several workers can share the SQLite
database safely (it runs in WAL mode with a 30 second busy timeout). After
changing `kilo.env`, restart with `sudo systemctl restart kilo`.

### 4. Reverse proxy (nginx) with HTTPS

Get a certificate (for example with `certbot --nginx`) and proxy to gunicorn:

```nginx
server {
    server_name kilo.example.com;
    client_max_body_size 8m;   # the app limits uploads itself: 8 MB for imports, 1 MB elsewhere

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

With Apache (`mod_proxy`, `mod_proxy_http` and `mod_headers` enabled):

```apache
<VirtualHost *:443>
    ServerName kilo.example.com
    ProxyPreserveHost On
    RequestHeader set X-Forwarded-Proto "https"
    RequestHeader set X-Real-IP "expr=%{REMOTE_ADDR}"
    ProxyPass / http://127.0.0.1:8000/
    ProxyPassReverse / http://127.0.0.1:8000/
</VirtualHost>
```

The page language comes from the browser's `Accept-Language` header, which
both proxies pass on, and every page answers with `Vary: Accept-Language`. If
you cache responses in front of the app (Apache `mod_cache`, a CDN), let the
cache honour `Vary`: a cache that ignores it, or a rule that strips it (`Header
unset Vary`), serves the first visitor's language to everyone.

#### Logging the visitor's IP address

Behind a proxy, gunicorn's access log shows `127.0.0.1` for everyone, because it
logs the address of whoever connected to it. The proxy examples above therefore
set `X-Real-IP`; tell gunicorn to log that header instead, in a
`gunicorn.conf.py` (start gunicorn with `--config gunicorn.conf.py`):

```python
accesslog = "-"   # or a file; "-" goes to the journal
access_log_format = '%({x-real-ip}i)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'
```

(Passing it as `--access-logformat` on the `ExecStart` line of a systemd unit
needs every `%` doubled to `%%`, because systemd reads `%` as a specifier; the
config file avoids that.)

`set` replaces whatever the client sent, so the header cannot be forged, as long
as gunicorn only listens on `127.0.0.1` (the examples bind it there). Restart
gunicorn afterwards. The app reads the same header for the security log below,
and believes it only when the connection comes from the loopback interface (a
direct connection could send a forged one); without the header it logs `-`
instead of treating everyone as `127.0.0.1`. If another proxy or a CDN sits in
front of this one, the address you see is that proxy's; with Apache add
`mod_remoteip` (`RemoteIPHeader X-Forwarded-For`).

#### Security log

Set `SECURITY_LOG_FILE` (for example `/var/log/gunicorn/kilo-security.log`; the
user that runs gunicorn must be able to write there) and the app adds one line
for every suspicious request, in a fixed format that fail2ban can read:

```
2026-10-05 15:30:00 kilo-notfound ip=203.0.113.9 path=/.env
2026-10-05 15:31:12 kilo-auth ip=203.0.113.9 event=code-wrong
```

* `kilo-notfound`: a request for a page that does not exist, also when a browser
  was redirected to the start page. The `/favicon.ico` and `apple-touch-icon`
  requests browsers make by themselves are left out.
* `kilo-auth`: a wrong sign-in code (`code-wrong`) or a wrong code for removing
  an account (`removal-code-wrong`).

Only the address and the event are logged, never an email address or a code, and
a path is percent-encoded and cut to 200 characters so a request cannot forge a
line. If the file cannot be written the app still starts and says so once in its
log. `deploy/logrotate/kilo-gunicorn` is an example logrotate rule for this file
and the gunicorn logs (the app reopens its file by itself after rotation).

#### Avatars

The avatar in the header is the user's [Gravatar](https://gravatar.com), but the
browser never contacts gravatar.com: it asks Kilo for `/avatar`, and the server
fetches the image (over HTTPS from `www.gravatar.com`, so the server needs
outbound HTTPS) and keeps it for a day in `instance/avatars/`. Gravatar sees only
the server, about once a day per active user, never the visitor's address or
browser. Without a Gravatar, or when it cannot be reached, a local placeholder is
shown. Only PNG, JPEG, GIF and WebP images up to 100 KB are accepted. The cached
file is deleted when its account is removed, and the daily cleanup (see "Cleaning
up stale sign-ups") deletes the files of removed accounts and of accounts that
have not visited for a month. `GRAVATAR_ENABLED=0` turns the fetching off.

### Backups and updates

Back up the database with SQLite's own tool, not a plain file copy (recent
writes can still be in the `-wal` file):

```bash
sqlite3 /srv/kilo/instance/weight.db ".backup /backups/weight.db"
```

To update: `git pull`, `.venv/bin/pip install -r requirements.txt`,
`.venv/bin/pybabel compile -d translations` (the translations are compiled at
deploy time, not stored in git), then `sudo systemctl restart kilo`. New
database columns are added automatically at startup.

The About page shows the running version, read from the git checkout: the tag
of the checked-out commit if it has one (for example `git tag v1.0.0`),
otherwise the 7-character commit id, plus the commit date. It is read once, so
restart the service after updating. If no version is shown, the app couldn't
run `git` on its folder - for example when the checkout belongs to another
user (`git config --global --add safe.directory /srv/kilo` fixes that).

#### Banning with fail2ban

`deploy/fail2ban/` has two filters and a jail file that read the security log, for a server that
runs [fail2ban](https://www.fail2ban.org/) (written for 1.0.x):

| Jail | Bans an address that... | Default |
|---|---|---|
| `kilo-notfound` | asks for 10 pages that do not exist within 60 seconds (`/.env`, `/.git/config`...) | 1 hour |
| `kilo-auth` | gets 10 wrong codes, or requests refused by the per-address or per-visitor limit, within 10 minutes | 1 hour |

Deliberately NOT banned: a double click on "send code" (the wait), the site-wide cap (it trips for
everybody, so banning the visitors who run into it would punish the innocent), what the limits would
do in `RATELIMIT_MODE=log`, and a log line without a known address (`ip=-`). The thresholds come
from five days of real traffic: of 41 visitors, two scanners sent 295 and 12 requests for unknown
pages within a minute and the others none, and nobody typed more than a few wrong codes.

```bash
sudo cp deploy/fail2ban/filter.d/*.conf /etc/fail2ban/filter.d/
sudo cp deploy/fail2ban/jail.d/kilo.local /etc/fail2ban/jail.d/

# look before you ban: what would each filter catch in the real log? (no ban is made)
fail2ban-regex /var/log/gunicorn/kilo-security.log kilo-notfound
fail2ban-regex /var/log/gunicorn/kilo-security.log kilo-auth

sudo fail2ban-client reload
sudo fail2ban-client status kilo-notfound                    # the banned addresses
sudo fail2ban-client set kilo-notfound unbanip 203.0.113.9   # lift one
```

The jails set only what is specific to Kilo. The ban action, `ignoreip` and the default `bantime`
come from your `[DEFAULT]` section (`jail.local`): check that the action covers ports 80 and 443
and that `ignoreip` holds your own address, so a typo-storm cannot ban you. Everyone behind a shared
address (an office, a mobile carrier) is banned together, hence the generous numbers; for the first
days you may want `bantime = 10m` in `kilo.local` while you watch `fail2ban-client status`.
The filters were tested with `fail2ban-regex` 1.1.0 against lines the app itself wrote, and the
automated tests keep the filters and the app's log format in step (they run the real
`fail2ban-regex` as well when it is installed); run the two `fail2ban-regex` commands above on the
server before you rely on them.

#### Limits on emailed codes

Anyone can type any address into the sign-in form, which makes the server send a mail to it. To
stop that being used to mail-bomb someone, to ruin your mail server's reputation, or to try many
codes, a code is only mailed when it is within these limits (the numbers are in the table above;
`0` turns a limit off):

* one address: one code per minute, and 5 sign-in codes (3 removal codes) an hour;
* one visitor (needs the `X-Real-IP` header, see above): 20 codes an hour. Without a known
  visitor address this limit is skipped and the app says so once in its log;
* the whole site: 60 codes an hour, a circuit breaker for your mail server's reputation. When it
  trips, nobody can request a code until the hour moves on.

The limits are on sending. A code that was already mailed stays valid, and a second request within
the minute does not send or replace anything (the visitor sees the same answer as for a fresh
send), so nobody is locked out of their own address by someone else asking for codes. A refused
request says "Too many requests. Try again in N minutes." in the visitor's language, without saying
which limit it was, and the same for an address with and without an account. Refused requests
are not counted, and a mail that fails to send gives its count back. If the limiter itself fails
(a locked database) the request is let through.

`RATELIMIT_MODE` decides what happens. The default is `log`: nothing is ever refused, but each
request that WOULD have been refused is written to the security log
(`kilo-auth ip=... event=code-would-refuse scope=address`; the scope is `cooldown`, `address`,
`removal`, `ip` or `global`). Run in `log` for a week or two, look at what it says
(`grep code-would-refuse /var/log/gunicorn/kilo-security.log`), adjust the numbers if real visitors
would have been caught, and then set `RATELIMIT_MODE=enforce` (in `kilo.env`, then restart). A
refusal in `enforce` mode is logged as `event=code-refused`. `off` switches it all off. The counts
are in the database (table `rate_event`, shared by all workers and kept for at most a day by the
cleanup below).

#### Cleaning up stale sign-ups

Anyone can create an account row by typing an address into the sign-in form, and
an address that is never verified would stay in the database for ever.
`scripts/cleanup_signups.py` removes accounts that were never verified and have
been idle for 7 days (idle counts from the later of the creation and the latest
sign-in code, so nobody loses a row while typing a code), clears the hashes
of codes that have expired, removes the rate-limit counts older than a day, and
deletes cached avatars of removed or month-long inactive accounts. Verified
accounts, and accounts with weight entries, are never touched. Look first, then run it:

```bash
.venv/bin/python scripts/cleanup_signups.py --dry-run   # only print what would happen
.venv/bin/python scripts/cleanup_signups.py             # do it (--stale-days N changes the 7)
```

It is safe to run at any time and twice in a row. To run it every day, copy the
two units from `deploy/systemd/` (check the paths and user in the `.service` file):

```bash
sudo cp deploy/systemd/flaskapp-kilo-cleanup.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now flaskapp-kilo-cleanup.timer
systemctl list-timers flaskapp-kilo-cleanup.timer      # when it runs next
journalctl -u flaskapp-kilo-cleanup                    # what the last runs did
```

## Test

```bash
pytest
```

The tests also check that the translations are complete and up to date; you can
run that check on its own with `python scripts/check_i18n.py` (see
[TRANSLATING.md](TRANSLATING.md)).

## License

Copyright (C) 2026 Ramón de Vries <ramon@11tools.com>

Kilo Tracker is free software: you can redistribute it and/or modify it under
the terms of the GNU Affero General Public License as published by the Free
Software Foundation, either version 3 of the License, or (at your option) any
later version. See the [LICENSE](LICENSE) file for the full text.

This program is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE.

The Kilo name and logo (the icons and wordmarks in `app/static/kilo-*.svg`)
are not covered by this license. They may not be reused without permission.
