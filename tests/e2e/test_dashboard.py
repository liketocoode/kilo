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

"""The dashboard in a real browser: the page script's flows that pytest alone cannot run.

Saving a day, the "is this right?" question for an unusual weight, Undo after clearing, the
note editor, the "today" prompt, the range buttons, dark mode and the session-expired dialog.
Run with `pytest -m e2e` (see requirements-e2e.txt).
"""

from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

# Ten days of weights up to yesterday, about 80 kg; today is left to each test.
HISTORY = {days_ago: 80.0 + (days_ago % 3) / 10 for days_ago in range(1, 11)}


def day(days_ago=0):
    return (date.today() - timedelta(days=days_ago)).isoformat()


@pytest.fixture
def dashboard(live_server, context, page):
    """Open the dashboard signed in; returns (page, user id). Today already has an entry, so
    the "today" prompt stays away unless a test wants it."""

    def open_(weights=None, notes=None, **columns):
        user_id = live_server.add_user(weights={0: 80.2, **HISTORY} if weights is None else weights,
                                       notes=notes, **columns)
        live_server.sign_in(context, user_id)
        page.goto(live_server.url + "/")
        return user_id

    return open_


def row(page, days_ago=0):
    return page.locator(f'.day-row[data-date="{day(days_ago)}"]')


def type_weight(page, text, days_ago=0):
    """Type into a day's weight field and leave it, as a person does (change fires on leaving)."""
    field = row(page, days_ago).locator(".day-weight")
    field.fill(text)
    field.press("Tab")


def test_a_weight_typed_in_the_log_is_saved(dashboard, live_server, page):
    user_id = dashboard()
    type_weight(page, "80,4")  # a comma is fine in every language
    expect(row(page).locator(".day-status")).to_have_text("Saved")
    assert live_server.entry(user_id) == (80.4, None)
    expect(page.locator("#stat-current")).not_to_be_empty()
    page.reload()
    expect(row(page).locator(".day-weight")).to_have_value("80.4")


def test_an_unusual_weight_is_saved_only_after_save_anyway(dashboard, live_server, page):
    user_id = dashboard()
    type_weight(page, "95")
    expect(page.locator("#outlier-prompt")).to_be_visible()
    expect(page.locator("#outlier-message")).to_contain_text("95")
    assert live_server.entry(user_id) == (80.2, None)  # nothing saved yet
    page.click("#outlier-confirm")
    expect(page.locator("#outlier-prompt")).to_be_hidden()
    expect(row(page).locator(".day-status")).to_have_text("Saved")
    assert live_server.entry(user_id) == (95.0, None)


def test_correct_it_goes_back_to_the_field_and_saves_nothing(dashboard, live_server, page):
    user_id = dashboard()
    type_weight(page, "95")
    page.click("#outlier-correct")
    expect(page.locator("#outlier-prompt")).to_be_hidden()
    expect(row(page).locator(".day-weight")).to_be_focused()
    assert live_server.entry(user_id) == (80.2, None)


def test_a_cleared_weight_comes_back_with_its_note_after_undo(dashboard, live_server, page):
    user_id = dashboard(notes={0: "after the party"})
    type_weight(page, "")
    expect(page.locator("#undo-toast")).to_be_visible()
    expect(page.locator("#undo-message")).to_contain_text("removed")
    assert live_server.entry(user_id) is None
    page.click("#undo-btn")
    expect(row(page).locator(".day-status")).to_have_text("Saved")
    expect(page.locator("#undo-toast")).to_be_hidden()
    assert live_server.entry(user_id) == (80.2, "after the party")
    page.reload()
    expect(row(page).locator(".day-weight")).to_have_value("80.2")
    expect(row(page).locator(".note-btn")).to_have_class("note-btn has-note")


def test_a_note_is_written_and_saved_in_the_note_editor(dashboard, live_server, page):
    user_id = dashboard()
    today = row(page)
    today.locator(".note-btn").click()
    expect(today.locator(".note-popover")).to_be_visible()
    today.locator(".note-textarea").fill("long walk 🚶")
    today.locator(".note-save").click()
    expect(today.locator(".note-popover")).to_be_hidden()
    expect(today.locator(".day-status")).to_have_text("Saved")
    assert live_server.entry(user_id) == (80.2, "long walk 🚶")


def test_escape_closes_the_note_editor_without_saving(dashboard, live_server, page):
    user_id = dashboard()
    today = row(page)
    today.locator(".note-btn").click()
    today.locator(".note-textarea").fill("not this")
    today.locator(".note-textarea").press("Escape")
    expect(today.locator(".note-popover")).to_be_hidden()
    assert live_server.entry(user_id) == (80.2, None)


def test_the_today_prompt_saves_todays_weight(dashboard, live_server, page):
    user_id = dashboard(weights=HISTORY)  # nothing today
    expect(page.locator("#today-prompt")).to_be_visible()
    page.fill("#today-prompt-weight", "80,1")
    page.click("#today-prompt-ok")
    expect(page.locator("#today-prompt")).to_be_hidden()
    expect(row(page).locator(".day-weight")).to_have_value("80.1")
    assert live_server.entry(user_id) == (80.1, None)


def test_a_skipped_today_prompt_stays_away_for_the_day(dashboard, page):
    dashboard(weights=HISTORY)
    page.click("#today-prompt-skip")
    expect(page.locator("#today-prompt")).to_be_hidden()
    page.reload()
    page.wait_for_load_state("load")
    expect(page.locator("#today-prompt")).to_be_hidden()


def test_a_range_button_changes_the_chart_and_is_remembered(dashboard, live_server, page):
    user_id = dashboard(weights=dict.fromkeys(range(40), 80.0))
    with page.expect_response(lambda response: response.url.endswith("/chart-range")) as saved:
        page.click('.range-btn[data-range="1m"]')
    assert saved.value.ok
    expect(page.locator('.range-btn[data-range="1m"]')).to_have_class("range-btn active")
    assert page.evaluate("chart.data.labels.length") == 31  # today minus 30 days .. today
    page.reload()
    expect(page.locator('.range-btn[data-range="1m"]')).to_have_class("range-btn active")
    assert live_server.user(user_id).chart_range == "1m"


def test_dark_mode_switches_at_once_and_is_remembered(dashboard, live_server, page):
    user_id = dashboard()
    expect(page.locator("html")).to_have_attribute("data-theme", "light")
    page.click("#account-trigger")
    with page.expect_response(lambda response: response.url.endswith("/settings/dark-mode")) as saved:
        page.locator("#account-dropdown .toggle-switch").click()
    assert saved.value.ok
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    page.reload()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    assert live_server.user(user_id).dark_mode is True


@pytest.mark.expected_http_errors  # the refused save is a 400
def test_an_expired_session_shows_the_dialog_instead_of_saving(dashboard, live_server, context, page):
    user_id = dashboard()
    context.clear_cookies()  # as when the session has run out
    type_weight(page, "79,9")
    expect(page.locator("#session-expired-prompt")).to_be_visible()
    expect(row(page).locator(".day-status")).to_have_text("")
    assert live_server.entry(user_id) == (80.2, None)
