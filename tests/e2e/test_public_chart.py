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

"""The public chart in a real browser: it draws, with or without the kilograms on it."""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

WEIGHTS = {days_ago: 80.0 + days_ago / 10 for days_ago in range(0, 20)}


def share(live_server, **columns):
    live_server.add_user(weights=WEIGHTS, public_enabled=True, public_id="mine", **columns)


def test_the_public_chart_draws_for_a_visitor(live_server, page):
    share(live_server)
    page.goto(live_server.url + "/public/mine")
    assert page.evaluate("chart.data.labels.length") > 0
    box = page.locator("#weight-chart").bounding_box()
    assert box["width"] > 200 and box["height"] > 200
    assert page.evaluate("chart.options.scales.y.ticks.display") is True
    expect(page.locator("#range-buttons")).to_be_visible()


def test_without_the_weight_the_chart_has_no_numbers_on_its_axis(live_server, page):
    share(live_server, public_show_weight=False)
    page.goto(live_server.url + "/public/mine")
    assert page.evaluate("chart.data.labels.length") > 0
    assert page.evaluate("chart.options.scales.y.ticks.display") is False
    assert page.evaluate("chart.options.plugins.tooltip.enabled") is False


def test_a_saved_range_and_theme_apply_without_buttons(live_server, page):
    share(live_server, public_dark_mode=True, public_range="1w")
    page.goto(live_server.url + "/public/mine")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    expect(page.locator("#range-buttons")).to_have_count(0)
    assert page.evaluate("chart.data.labels.length") == 8  # today minus 7 days .. today
