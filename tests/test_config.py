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

"""Tests for the on/off settings that create_app() reads from the environment."""

import pytest

from app import create_app

# Each flag and its value when the variable is not set.
FLAGS = {
    "MAIL_USE_TLS": False,
    "MAIL_SUPPRESS_SEND": True,
    "CHECK_EMAIL_MX": True,
    "GRAVATAR_ENABLED": True,
    "SESSION_COOKIE_SECURE": False,
}


def make_app():
    return create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})


@pytest.mark.parametrize("name, default", FLAGS.items())
def test_an_unset_flag_has_its_default(monkeypatch, name, default):
    monkeypatch.delenv(name, raising=False)
    assert make_app().config[name] is default


@pytest.mark.parametrize("name", FLAGS)
@pytest.mark.parametrize("value, expected", [("1", True), ("0", False), ("yes", False), ("", False)])
def test_a_flag_is_on_only_when_it_is_1(monkeypatch, name, value, expected):
    monkeypatch.setenv(name, value)
    assert make_app().config[name] is expected
