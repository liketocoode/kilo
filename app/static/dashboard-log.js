// Kilo Tracker - a small weight-tracking web app.
// Copyright (C) 2026 Ramón de Vries <ramon@11tools.com>
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published
// by the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version. See the LICENSE file for the full text.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// The daily log of the dashboard: saving a day (and Undo), the note editor with its emoji
// picker, the scrolling list of days, the dialogs and the "today" prompt. Loaded after
// dashboard-chart.js, whose PAGE data, series and range functions it uses; not on the public
// chart, which has no daily log and saves nothing.

function setChangeCell(cell, value, format) {
  const missing = value === null || value === undefined;
  cell.textContent = missing ? '—' : format(value);
  cell.classList.toggle('up', !missing && value > 0);
  cell.classList.toggle('down', !missing && value < 0);
}

function updateOverview(data) {
  setSeries(data);
  applyRange(currentRange);
  if (data.available_ranges) applyAvailableRanges(data.available_ranges);

  const stats = data.stats;
  const currentEl = document.getElementById('stat-current');
  if (stats && currentEl) {
    currentEl.textContent = weightFormat.format(stats.current);
    const totalCard = document.getElementById('stat-total-card');
    if (totalCard) {
      document.getElementById('stat-total-label').textContent = stats.total_label;
      setChangeCell(totalCard.querySelector('.change-value'), stats.total.change, fmtSignedKg);
      setChangeCell(totalCard.querySelector('.change-rate'), stats.total.g_per_day, fmtGramsPerDay);
    }
    const firstEl = document.getElementById('stat-first-date');
    if (firstEl) {
      firstEl.textContent = fmtDate(stats.first_date);
      document.getElementById('stat-last-date').textContent = fmtDate(stats.last_date);
      document.getElementById('stat-entry-count').textContent = stats.entry_count;
    }
    stats.changes.forEach((c) => {
      const card = document.querySelector('.stat-card[data-days="' + c.days + '"]');
      if (!card) return;
      setChangeCell(card.querySelector('.change-value'), c.change, fmtSignedKg);
      setChangeCell(card.querySelector('.change-rate'), c.g_per_day, fmtGramsPerDay);
    });
  }

  // Below BMI 22 the change colours flip: a loss is red, a gain green.
  const statsEl = document.querySelector('.stats');
  if (statsEl) statsEl.classList.toggle('bmi-low', data.bmi != null && data.bmi < 22);

  const bmiEl = document.getElementById('stat-bmi');
  if (bmiEl) {
    bmiEl.textContent = data.bmi ? oneDecimalFormat.format(data.bmi) : '—';
  }
}

// ---- Emoji picker for the note editor ------------------------------------
// Most common first: the emoji people use most overall, then the ones that suit
// a weight diary (movement, food and drink, how the day felt). Single code points
// (plus a variation selector) only: no joined sequences that break on older systems.
const EMOJI = [
  '😊', '😂', '👍', '❤️', '😢', '🎉', '🔥', '🙏', '😍', '😅', '😴', '🤔', '😎', '😁', '🥰', '💪',
  '🏃', '🚶', '🚴', '🏋️', '🧘', '🏊', '⚽', '🥾',
  '🍎', '🥗', '🍕', '🍔', '🍟', '🍰', '🍫', '🍺', '🍷', '☕', '💧', '🥤', '🍽️',
  '😋', '😩', '😐', '🙂', '😡', '🤒', '🤢', '🥵', '🥳', '😭', '⭐', '✅', '⚠️', '☀️', '🌧️', '✈️', '🛌', '🎂',
];
const EMOJI_STORAGE_KEY = 'kilo.recentEmoji';
const EMOJI_RECENT_MAX = 8;
const emojiPanel = document.getElementById('emoji-panel');
let emojiTarget = null; // {textarea, button} of the note editor the panel is open in

function loadRecentEmoji() {
  try {
    const saved = JSON.parse(localStorage.getItem(EMOJI_STORAGE_KEY));
    return Array.isArray(saved) ? saved.filter((e) => typeof e === 'string').slice(0, EMOJI_RECENT_MAX) : [];
  } catch (err) {
    return []; // storage unavailable (private window) or not valid JSON
  }
}

function rememberEmoji(emoji) {
  const list = [emoji, ...loadRecentEmoji().filter((e) => e !== emoji)].slice(0, EMOJI_RECENT_MAX);
  try {
    localStorage.setItem(EMOJI_STORAGE_KEY, JSON.stringify(list));
  } catch (err) {
    // not saved: the "recently used" row just stays empty
  }
}

function fillEmojiGrid(grid, emojis) {
  grid.replaceChildren(...emojis.map((emoji) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'emoji-btn';
    button.textContent = emoji;
    return button;
  }));
}

function closeEmojiPicker() {
  emojiPanel.hidden = true;
  if (emojiTarget) emojiTarget.button.setAttribute('aria-expanded', 'false');
  emojiTarget = null;
}

function toggleEmojiPicker(popover, button, textarea) {
  const wasOpenHere = !emojiPanel.hidden && emojiPanel.parentElement === popover;
  closeEmojiPicker();
  if (wasOpenHere) return;
  popover.insertBefore(emojiPanel, popover.querySelector('.note-popover-actions'));
  const recent = loadRecentEmoji();
  fillEmojiGrid(document.getElementById('emoji-recent-grid'), recent);
  document.getElementById('emoji-recent').hidden = recent.length === 0;
  emojiPanel.hidden = false;
  button.setAttribute('aria-expanded', 'true');
  emojiTarget = { textarea, button };
  textarea.focus();
}

// Put an emoji at the cursor (replacing a selection). setRangeText ignores
// maxlength, so the limit is checked here; returns false when it would not fit.
function insertEmoji(textarea, emoji) {
  const selected = textarea.selectionEnd - textarea.selectionStart;
  if (textarea.value.length - selected + emoji.length > textarea.maxLength) return false;
  textarea.setRangeText(emoji, textarea.selectionStart, textarea.selectionEnd, 'end');
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
  textarea.focus();
  return true;
}

fillEmojiGrid(document.getElementById('emoji-grid'), EMOJI);
// Keep the cursor in the note: pressing a button would otherwise take the focus.
emojiPanel.addEventListener('mousedown', (e) => e.preventDefault());
emojiPanel.addEventListener('click', (e) => {
  const button = e.target.closest('.emoji-btn');
  if (!button || !emojiTarget) return;
  if (insertEmoji(emojiTarget.textarea, button.textContent)) rememberEmoji(button.textContent);
});

// The editor hangs to the left of the note icon, which on a phone is close to
// the left edge of the screen: if it would stick out, slide it back inside.
const POPOVER_EDGE_GAP = 8;
function placePopover(popover) {
  popover.style.insetInlineEnd = '';
  const overflow = POPOVER_EDGE_GAP - popover.getBoundingClientRect().left;
  if (overflow > 0) popover.style.insetInlineEnd = `${-overflow}px`;
}

function closeAllPopovers(except) {
  document.querySelectorAll('.note-popover').forEach((p) => {
    if (p !== except) p.hidden = true;
  });
  closeEmojiPicker();
}

// Only one row is ever highlighted at a time: selecting a new one (by
// clicking a chart point, or by clicking into a row's weight field to
// edit it) clears whichever row was highlighted before.
let highlightedRow = null;

function setHighlightedRow(row) {
  if (highlightedRow && highlightedRow !== row) {
    highlightedRow.classList.remove('day-row-highlight');
  }
  row.classList.add('day-row-highlight');
  highlightedRow = row;
}

// Shown when the server flags a weight as an outlier (>10% off from the
// nearest real entry before/after it — see _outlier_warning in
// routes.py). "Save anyway" retries the same save with confirm: true,
// which the server accepts unconditionally.
const outlierPrompt = document.getElementById('outlier-prompt');
const outlierMessage = document.getElementById('outlier-message');
const outlierCorrectBtn = document.getElementById('outlier-correct');
const outlierConfirmBtn = document.getElementById('outlier-confirm');
let pendingOutlierConfirm = null;

function hideOutlierPrompt() {
  outlierPrompt.hidden = true;
  pendingOutlierConfirm = null;
}

function showOutlierPrompt(message, onConfirm, onCorrect) {
  outlierMessage.textContent = message;
  outlierPrompt.hidden = false;
  pendingOutlierConfirm = onConfirm;
  outlierCorrectBtn.onclick = () => {
    hideOutlierPrompt();
    if (onCorrect) onCorrect();
  };
}

// The session (its CSRF token) expired while the page was open: nothing was
// saved, and only reloading the page gets a fresh token.
const sessionExpiredPrompt = document.getElementById('session-expired-prompt');
function showSessionExpired() {
  sessionExpiredPrompt.hidden = false;
  document.getElementById('session-expired-reload').focus();
}
document.getElementById('session-expired-reload').addEventListener('click', () => location.reload());

// Saves one day (the daily log and the "today" prompt both use it) and handles what is the
// same for both: an expired session, the "is this right?" question for an unusual weight and
// the new overview. Resolves to the server's answer after a save; null when the caller has
// nothing more to do. Callbacks: showError(message) for a refusal (message may be empty),
// retry() to save again with confirm (the outlier question's "Save anyway"), refocus() for
// "Correct it",
// and optional quiet() when the save stopped for the session dialog or the outlier question.
// A network error or an unreadable answer is thrown, for the caller's "could not save".
async function saveDay(payload, { showError, retry, refocus, quiet }) {
  const resp = await postJson(PAGE.urls.save_field, payload);
  const data = await resp.json();
  if (!resp.ok) {
    if (data.code === 'csrf_expired') {
      if (quiet) quiet();
      showSessionExpired();
    } else {
      showError(data.error);
    }
    return null;
  }
  if (data.status === 'warning') {
    if (quiet) quiet();
    showOutlierPrompt(data.message, retry, refocus);
    return null;
  }
  updateOverview(data);
  return data;
}

outlierConfirmBtn.addEventListener('click', () => {
  const confirmFn = pendingOutlierConfirm;
  hideOutlierPrompt();
  if (confirmFn) confirmFn();
});

// "Weight of 5 Oct 2026 removed. Undo": one notice for the whole log, for the latest
// removal only, gone after UNDO_SECONDS. Clearing a weight deletes the entry at once, so
// this is the way back from a slip of the finger.
const UNDO_SECONDS = 8;
const undoToast = document.getElementById('undo-toast');
const undoMessage = document.getElementById('undo-message');
let undoTimer = null;
let undoAction = null;
let undoDate = null;

function hideUndo() {
  clearTimeout(undoTimer);
  undoToast.hidden = true;
  undoAction = null;
  undoDate = null;
}

function offerUndo(date, action) {
  clearTimeout(undoTimer);
  undoMessage.textContent = t('weightRemoved', { date: fmtDate(date) });
  undoAction = action;
  undoDate = date;
  undoToast.hidden = false;
  undoTimer = setTimeout(hideUndo, UNDO_SECONDS * 1000);
}

document.getElementById('undo-btn').addEventListener('click', () => {
  const action = undoAction;
  hideUndo();
  if (action) action();
});

// The note editor of one day row: the note button opens it with the row's note, Save hands
// the edited note to onSave(note), Cancel and Escape close it, Ctrl/Cmd+Enter saves.
function bindNoteEditor(row, onSave) {
  const noteValue = row.querySelector('.day-note-value');
  const noteBtn = row.querySelector('.note-btn');
  const popover = row.querySelector('.note-popover');
  const textarea = row.querySelector('.note-textarea');

  popover.querySelector('.note-emoji-btn').addEventListener('click', (e) => {
    e.stopPropagation();
    toggleEmojiPicker(popover, e.currentTarget, textarea);
  });

  noteBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    const opening = popover.hidden;
    closeAllPopovers(opening ? popover : null);
    popover.hidden = !opening;
    if (opening) {
      placePopover(popover);
      textarea.value = noteValue.value;
      textarea.focus();
    }
  });

  popover.querySelector('.note-save').addEventListener('click', (e) => {
    e.stopPropagation();
    popover.hidden = true;
    onSave(textarea.value.trim().slice(0, PAGE.note_max_length));
  });

  popover.querySelector('.note-cancel').addEventListener('click', (e) => {
    e.stopPropagation();
    popover.hidden = true;
  });

  popover.addEventListener('click', (e) => e.stopPropagation());

  textarea.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      popover.hidden = true;
    } else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      popover.querySelector('.note-save').click();
    }
  });
}

// One day of the daily log: its weight field saves on change, its note editor on Save.
function bindRow(row) {
  const weightInput = row.querySelector('.day-weight');
  const noteValue = row.querySelector('.day-note-value');
  const noteBtn = row.querySelector('.note-btn');
  const notePreview = row.querySelector('.note-preview');
  const statusEl = row.querySelector('.day-status');

  function refreshNoteDisplay() {
    const hasNote = !!noteValue.value;
    noteBtn.classList.toggle('has-note', hasNote);
    notePreview.textContent = hasNote ? noteValue.value : t('noNote');
  }
  refreshNoteDisplay();

  let statusTimer = null;

  async function save(confirm) {
    clearTimeout(statusTimer);
    statusEl.textContent = t('saving');
    try {
      const payload = {
        date: row.dataset.date,
        weight: normalizeDecimal(weightInput.value),
        note: noteValue.value,
        confirm: !!confirm,
      };
      const data = await saveDay(payload, {
        showError: (message) => { statusEl.textContent = message || t('errorSaving'); },
        retry: () => save(true),
        refocus: () => weightInput.focus(),
        quiet: () => { statusEl.textContent = ''; },
      });
      if (!data) return;
      statusEl.textContent = t('saved');
      statusTimer = setTimeout(() => { statusEl.textContent = ''; }, 1500);
      if (data.status === 'cleared' && data.removed) {
        // The note went with the entry: show the row as it now is (Undo brings both back).
        // A note typed on a day that had no weight is not touched: it is saved together
        // with the weight once one is entered.
        noteValue.value = '';
        refreshNoteDisplay();
        offerUndo(row.dataset.date, () => restore(data.removed));
      } else if (undoDate === row.dataset.date) {
        hideUndo(); // this day was saved again: nothing left to undo
      }
    } catch (err) {
      statusEl.textContent = t('couldNotSave');
    }
  }

  // Puts a removed weight and its note back (the Undo button). confirm: it was already
  // accepted once, so the outlier check would only ask the same question again.
  function restore(removed) {
    weightInput.value = inputWeightFormat.format(removed.weight);
    noteValue.value = removed.note;
    refreshNoteDisplay();
    save(true);
  }

  // Not `save` directly: the change Event object would otherwise be
  // passed as save's `confirm` argument and (being truthy) silently
  // skip the outlier check on every normal edit.
  weightInput.addEventListener('change', () => save());
  weightInput.addEventListener('focus', () => setHighlightedRow(row));

  bindNoteEditor(row, (note) => {
    noteValue.value = note;
    refreshNoteDisplay();
    save();
  });
}

document.addEventListener('click', () => closeAllPopovers());

document.querySelectorAll('.day-row').forEach(bindRow);

const entriesPanel = document.querySelector('.entries');

// Rows are newest-first, so the top of the panel is the most recent record.
document.getElementById('scroll-top-btn').addEventListener('click', () => {
  entriesPanel.scrollTo({ top: 0, behavior: 'smooth' });
});
const dayRows = document.getElementById('day-rows');
const dayRowTemplate = document.getElementById('day-row-template');
const sentinel = document.getElementById('scroll-sentinel');
const loadingHint = document.getElementById('loading-hint');
let oldest = sentinel.dataset.oldest;
let loading = false;
let exhausted = !oldest;

function appendDay(day) {
  // The server renders one empty, translated row for this (the day_row macro in index.html).
  const row = dayRowTemplate.content.firstElementChild.cloneNode(true);
  row.dataset.date = day.date;
  row.querySelector('.day-date').textContent = fmtDate(day.date);
  row.querySelector('.day-weight').value = day.weight == null ? '' : inputWeightFormat.format(day.weight);
  row.querySelector('.day-note-value').value = day.note ?? '';
  dayRows.appendChild(row);
  bindRow(row);
}

async function loadNextPage() {
  if (loading || exhausted) return false;
  loading = true;
  loadingHint.hidden = false;
  document.body.classList.add('is-loading-entries');
  try {
    const resp = await fetch(PAGE.urls.entries_window + '?before=' + oldest);
    const data = await resp.json();
    if (!data.days || data.days.length === 0) {
      exhausted = true;
      return false;
    }
    data.days.forEach(appendDay);
    oldest = data.days[data.days.length - 1].date;
    return true;
  } finally {
    loading = false;
    loadingHint.hidden = true;
    document.body.classList.remove('is-loading-entries');
  }
}

// .entries now scrolls internally (the page itself no longer does), so
// the observer must watch intersection against that panel, not the
// default (browser viewport) root.
const observer = new IntersectionObserver((observed) => {
  if (observed[0].isIntersecting) loadNextPage();
}, { root: entriesPanel, rootMargin: '300px' });

if (!exhausted) observer.observe(sentinel);

async function scrollToDate(dateStr) {
  let row = dayRows.querySelector('.day-row[data-date="' + dateStr + '"]');
  let guard = 0;
  while (!row && guard < 2000) {
    guard++;
    const loaded = await loadNextPage();
    if (!loaded) break;
    row = dayRows.querySelector('.day-row[data-date="' + dateStr + '"]');
  }
  if (row) {
    row.scrollIntoView({ behavior: 'smooth', block: 'center' });
    setHighlightedRow(row);
  }
}

// Prompt for today's weight if it's still missing, unless the user
// already skipped it today (remembered per-device via localStorage —
// a viewer convenience, not data the server needs to know about).
const todayDate = PAGE.today.date;
const todayEntryMissing = PAGE.today.entry_missing;
// Per user, so skipping as one account doesn't hide the prompt for another
// account used on the same browser.
const SKIP_STORAGE_KEY = 'weightTracker.todayPromptSkippedDate.' + PAGE.today.user_id;

function wasTodayPromptSkipped() {
  try {
    return localStorage.getItem(SKIP_STORAGE_KEY) === todayDate;
  } catch (err) {
    return false;
  }
}

function rememberTodayPromptSkipped() {
  try {
    localStorage.setItem(SKIP_STORAGE_KEY, todayDate);
  } catch (err) {
    // localStorage unavailable (private browsing, etc.) — the prompt
    // will just show again next time, which is an acceptable fallback.
  }
}

const todayPrompt = document.getElementById('today-prompt');
const todayPromptWeight = document.getElementById('today-prompt-weight');
const todayPromptError = document.getElementById('today-prompt-error');
const todayPromptSkipBtn = document.getElementById('today-prompt-skip');
const todayPromptOkBtn = document.getElementById('today-prompt-ok');

function hideTodayPrompt() {
  todayPrompt.hidden = true;
}

function showTodayPrompt() {
  todayPromptError.hidden = true;
  todayPrompt.hidden = false;
  todayPromptWeight.focus();
}

todayPromptSkipBtn.addEventListener('click', () => {
  rememberTodayPromptSkipped();
  hideTodayPrompt();
});

async function saveTodayPrompt(confirm) {
  const weight = normalizeDecimal(todayPromptWeight.value);
  if (!weight) {
    todayPromptWeight.focus();
    return;
  }
  todayPromptOkBtn.disabled = true;
  todayPromptError.hidden = true;
  try {
    const data = await saveDay({ date: todayDate, weight, note: '', confirm: !!confirm }, {
      showError: (message) => {
        todayPromptError.textContent = message || t('couldNotSavePrompt');
        todayPromptError.hidden = false;
      },
      retry: () => saveTodayPrompt(true),
      refocus: () => todayPromptWeight.focus(),
    });
    if (!data) return;
    const todayRow = dayRows.querySelector('.day-row[data-date="' + todayDate + '"]');
    if (todayRow) todayRow.querySelector('.day-weight').value = inputWeightFormat.format(Number(weight));
    hideTodayPrompt();
  } catch (err) {
    todayPromptError.textContent = t('couldNotSavePrompt');
    todayPromptError.hidden = false;
  } finally {
    todayPromptOkBtn.disabled = false;
  }
}

todayPromptOkBtn.addEventListener('click', () => saveTodayPrompt(false));

todayPromptWeight.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') todayPromptOkBtn.click();
});

document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (!outlierPrompt.hidden) {
    hideOutlierPrompt();
  } else if (!todayPrompt.hidden) {
    rememberTodayPromptSkipped();
    hideTodayPrompt();
  }
});

if (todayEntryMissing && !wasTodayPromptSkipped()) showTodayPrompt();
