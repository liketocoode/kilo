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

"""The signed-in user's Gravatar, fetched by the server and kept in a disk cache.

The browser asks Kilo for `/avatar`, never gravatar.com, so Gravatar does not see the
visitor's address, browser or visit times; it only sees the server, about once a day per
active user. The email hash doubles as the Gravatar id (see `utils.hash_email`).

The cache is a folder (AVATAR_CACHE_DIR, default `instance/avatars`) with one file per user:

* `<hash>.png`, `.jpg`, `.gif` or `.webp`: the avatar;
* `<hash>.none`: Gravatar has no avatar for this hash (or could not be reached, see below).

A file is fresh for CACHE_SECONDS after its modification time. When Gravatar cannot be
reached, a cached avatar keeps being served, and otherwise a `.none` file that expires after
RETRY_SECONDS stops every page load from waiting for the timeout again (gunicorn's sync
workers would be blocked meanwhile). Without an avatar the page shows a local placeholder.

Only a fixed URL is fetched (a hash of 64 hex characters from the database, never input
from the visitor), redirects are not followed, and only PNG, JPEG, GIF and WebP up to
MAX_BYTES are accepted, checked by their first bytes as well. Never SVG: served from Kilo's
own address it could run scripts.
"""

import os
import re
import tempfile
import time
import urllib.error
import urllib.request

from flask import current_app

GRAVATAR_URL = "https://www.gravatar.com/avatar/{hash}?s={size}&d=404"
# The header shows the avatar at 20 CSS pixels; 40 keeps it sharp on high-density screens.
SIZE = 40
TIMEOUT_SECONDS = 3
MAX_BYTES = 100 * 1024
CACHE_SECONDS = 24 * 60 * 60
RETRY_SECONDS = 15 * 60
# Cache files of accounts that no longer exist are removed by the daily cleanup; any other
# file this old is too (its owner has not visited for a month).
KEEP_SECONDS = 30 * 24 * 60 * 60

_HASH = re.compile(r"[0-9a-f]{64}")
# Extension -> (content type, test on the first bytes).
_FORMATS = {
    "png": ("image/png", lambda b: b.startswith(b"\x89PNG\r\n\x1a\n")),
    "jpg": ("image/jpeg", lambda b: b.startswith(b"\xff\xd8\xff")),
    "gif": ("image/gif", lambda b: b[:6] in (b"GIF87a", b"GIF89a")),
    "webp": ("image/webp", lambda b: b[:4] == b"RIFF" and b[8:12] == b"WEBP"),
}
_TYPE_TO_EXT = {content_type: ext for ext, (content_type, _check) in _FORMATS.items()}
_MISSING = "none"


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Treat a redirect as an error: only the fixed Gravatar URL is ever fetched."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirects)


def cache_dir():
    """The cache folder from the configuration (created when something is written)."""
    return current_app.config["AVATAR_CACHE_DIR"]


def _paths(email_hash):
    folder = cache_dir()
    return {ext: os.path.join(folder, f"{email_hash}.{ext}") for ext in [*_FORMATS, _MISSING]}


def _download(email_hash):
    """(content type, bytes) of the avatar, None when Gravatar has none; raises OSError when
    Gravatar cannot be reached or answers with something unusable."""
    url = GRAVATAR_URL.format(hash=email_hash, size=SIZE)
    request = urllib.request.Request(url, headers={"User-Agent": "Kilo Tracker"})
    try:
        with _opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            content_type = response.headers.get_content_type()
            data = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise
    if len(data) > MAX_BYTES:
        raise OSError("avatar too large")
    ext = _TYPE_TO_EXT.get(content_type)
    if ext is None or not _FORMATS[ext][1](data):
        raise OSError(f"unexpected avatar type {content_type}")
    return content_type, data


def _write(path, data, mtime=None):
    """Write `path` atomically (a reader never sees half a file), optionally with an older mtime."""
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if mtime is not None:
            os.utime(tmp, (mtime, mtime))
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _retry_mtime(now):
    """A modification time that makes a cache file stale RETRY_SECONDS from `now`."""
    return now - CACHE_SECONDS + RETRY_SECONDS


def _unlink_quietly(path):
    """Delete `path`; True if it was deleted, False if it could not be (or was not there)."""
    try:
        os.unlink(path)
    except OSError:
        return False
    return True


def _remove_others(paths, keep):
    for ext, path in paths.items():
        if ext != keep and os.path.exists(path):
            os.unlink(path)


def get(email_hash, now=None):
    """(content type, bytes) of the avatar for `email_hash`, or None to show the placeholder."""
    if not _HASH.fullmatch(email_hash or "") or not current_app.config["GRAVATAR_ENABLED"]:
        return None
    now = time.time() if now is None else now
    paths = _paths(email_hash)
    cached = None
    for ext, path in paths.items():
        try:
            age = now - os.path.getmtime(path)
        except OSError:
            continue
        cached = (ext, path, age)
        break

    if cached and cached[2] < CACHE_SECONDS:
        return _read(*cached[:2])

    try:
        result = _download(email_hash)
    except OSError as error:
        current_app.logger.warning("Could not fetch a Gravatar: %s", error)
        if cached and cached[0] != _MISSING:
            # An old avatar is better than none; ask Gravatar again in RETRY_SECONDS.
            try:
                os.utime(cached[1], (_retry_mtime(now),) * 2)
            except OSError:
                pass
            return _read(*cached[:2])
        try:
            # Try again in RETRY_SECONDS rather than on every page load.
            _write(paths[_MISSING], b"", mtime=_retry_mtime(now))
            _remove_others(paths, _MISSING)
        except OSError:
            pass
        return None

    keep = _MISSING if result is None else _TYPE_TO_EXT[result[0]]
    try:
        _write(paths[keep], b"" if result is None else result[1])
        _remove_others(paths, keep)
    except OSError as error:
        current_app.logger.warning("Could not write the avatar cache: %s", error)
    return result


def _read(ext, path):
    if ext == _MISSING:
        return None
    try:
        with open(path, "rb") as f:
            return _FORMATS[ext][0], f.read()
    except OSError:
        return None


def forget(email_hash):
    """Delete the cached avatar of `email_hash` (when its account is removed)."""
    if not _HASH.fullmatch(email_hash or ""):
        return
    for path in _paths(email_hash).values():
        _unlink_quietly(path)


def prune(known_hashes, now=None, dry_run=False):
    """Delete cache files of accounts not in `known_hashes`, and any file older than
    KEEP_SECONDS; returns how many files that is (or would be, with `dry_run`)."""
    folder = cache_dir()
    if not os.path.isdir(folder):
        return 0
    now = time.time() if now is None else now
    count = 0
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        stem, _dot, ext = name.partition(".")
        try:
            age = now - os.path.getmtime(path)
        except OSError:
            continue
        if name.startswith(".tmp-"):
            old = age > 60 * 60  # left behind by a write that was interrupted
        elif _HASH.fullmatch(stem) and ext in [*_FORMATS, _MISSING]:
            old = age > KEEP_SECONDS
        else:
            continue  # not ours
        if not old and (name.startswith(".tmp-") or stem in known_hashes):
            continue
        if dry_run or _unlink_quietly(path):
            count += 1
    return count
