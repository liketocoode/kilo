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

// The weight chart, its range buttons and the stat boxes' links to the ranges: the dashboard and
// the public chart (/public/<id>) both load this file, after the vendored Chart.js. Everything
// it needs from the server is in the page's JSON block #page-data (see index.html); the strings
// come from #i18n through t() and the number formats from LOCALE (both in base.html).
// The dashboard also loads dashboard-log.js, which uses the functions and the series here.

const PAGE = JSON.parse(document.getElementById('page-data').textContent);

// Numbers and dates follow the page language (LOCALE, set in base.html).
// Weights always show one decimal, like the server-rendered ones.
const weightFormat = new Intl.NumberFormat(LOCALE, {
  style: 'unit', unit: 'kilogram', minimumFractionDigits: 1, maximumFractionDigits: 1,
});
const signedWeightFormat = new Intl.NumberFormat(LOCALE, {
  style: 'unit', unit: 'kilogram', minimumFractionDigits: 1, maximumFractionDigits: 1, signDisplay: 'always',
});
const gramsFormat = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0, signDisplay: 'always' });
const oneDecimalFormat = new Intl.NumberFormat(LOCALE, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
// A weight in an input field: no unit, no grouping, the language's decimal separator.
const inputWeightFormat = new Intl.NumberFormat(LOCALE, {
  minimumFractionDigits: 1, maximumFractionDigits: 2, useGrouping: false,
});
const dateFormat = new Intl.DateTimeFormat(LOCALE, { dateStyle: 'medium' });
// The same date with the weekday in front (the chart tooltip). dateStyle can't
// carry a weekday, so spell out the parts: Intl then orders them the way the
// language does ("Maandag 5 okt 2026", "Montag, 5. Okt. 2026", and in Hungarian
// "2026. okt. 5., hétfő").
const weekdayDateFormat = new Intl.DateTimeFormat(LOCALE, {
  weekday: 'long', day: 'numeric', month: 'short', year: 'numeric',
});

// A local calendar date from 'YYYY-MM-DD' (new Date(iso) would be UTC midnight,
// a day off west of Greenwich when formatted in local time).
function parseIsoDate(iso) {
  const [year, month, day] = iso.split('-').map(Number);
  return new Date(year, month - 1, day);
}
// A LOCAL calendar date as YYYY-MM-DD (toISOString() would be UTC, see applyRange).
function localIso(date) {
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, '0'),
    String(date.getDate()).padStart(2, '0'),
  ].join('-');
}
function fmtDate(iso) {
  return dateFormat.format(parseIsoDate(iso));
}
// Starts with a capital, like a title (most languages write weekdays in lower case).
function fmtWeekdayDate(iso) {
  const text = weekdayDateFormat.format(parseIsoDate(iso));
  return text.charAt(0).toLocaleUpperCase(LOCALE) + text.slice(1);
}
function fmtSignedKg(value) {
  return signedWeightFormat.format(value);
}
function fmtGramsPerDay(value) {
  return t('gramsPerDay', { grams: gramsFormat.format(value) });
}

const rootStyle = getComputedStyle(document.documentElement);
const chartLine = rootStyle.getPropertyValue('--chart-line').trim();
const chartFill = rootStyle.getPropertyValue('--chart-fill').trim();
const dangerColor = rootStyle.getPropertyValue('--chart-red').trim();
const accentColor = rootStyle.getPropertyValue('--chart-green').trim();
const warnColor = rootStyle.getPropertyValue('--chart-yellow').trim();
const gridLineColor = rootStyle.getPropertyValue('--border').trim();
const movingAvgColor = rootStyle.getPropertyValue('--chart-moving-avg').trim();
const overallAvgColor = rootStyle.getPropertyValue('--chart-overall-avg').trim();

// The whole series, from the page data now and from each save's answer later (setSeries).
let fullLabels;
let fullValues;
let fullBmis;
let fullReal;
let fullMovingAvg;
let fullNotes;

// `data` has the keys of routes.overview(): the page data, or save_field's answer.
function setSeries(data) {
  fullLabels = data.chart_labels;
  fullValues = data.chart_values;
  fullBmis = data.chart_bmis;
  fullReal = data.chart_real;
  fullMovingAvg = data.chart_moving_average;
  fullNotes = data.chart_notes;
}
setSeries(PAGE);

let currentBmis = fullBmis;
let currentReal = fullReal;
let currentRange = PAGE.chart_range;
const RANGE_DAYS = PAGE.chart_range_days;

// BMI false-color scale: red at/below 17 and at/above 30, peak green at
// 22.5, green (varying intensity) across 20-25, gradual green->red
// through 17-20 and 25-30. Only used once height (and so BMI) is known.
//
// Chart.js re-invokes scriptable style options (segment.borderColor,
// pointBackgroundColor) for every segment/point on every redraw — which
// fires on every hover/mousemove, not just on data changes. So the color
// math itself is precomputed into a lookup table ONCE at load, and the
// per-point/per-segment results are cached ONCE per data change (see
// rebuildBmiColorCaches) — the scriptable callbacks below do a plain
// array lookup, no math, no allocation, on the hot (per-redraw) path.
function hexToRgb(hex) {
  const clean = hex.replace('#', '');
  const full = clean.length === 3 ? clean.split('').map((c) => c + c).join('') : clean;
  const n = parseInt(full, 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}
function lerpRgb(a, b, t) {
  return {
    r: Math.round(a.r + (b.r - a.r) * t),
    g: Math.round(a.g + (b.g - a.g) * t),
    b: Math.round(a.b + (b.b - a.b) * t),
  };
}
function rgbToCss(c) {
  return `rgb(${c.r}, ${c.g}, ${c.b})`;
}
function rgbToCssAlpha(c, a) {
  return `rgba(${c.r}, ${c.g}, ${c.b}, ${a})`;
}
const RED_RGB = hexToRgb(dangerColor);
const GREEN_PEAK_RGB = hexToRgb(accentColor);
const YELLOW_RGB = hexToRgb(warnColor);

// Traffic-light scale: red / amber / green bands with short blends
// between them, [bmi, color] in ascending order. Green is the healthy
// range (about 19-24.5), amber is a narrow band just outside it (17.5-19
// and 26-27), red is clearly out of range (<= 16, >= 29). Flat outside
// the ends.
const BMI_COLOR_STOPS = [
  [16, RED_RGB],
  [17.5, YELLOW_RGB],
  [19, GREEN_PEAK_RGB],
  [24.5, GREEN_PEAK_RGB],
  [26, YELLOW_RGB],
  [27, YELLOW_RGB],
  [29, RED_RGB],
];

function computeBmiRgb(bmi) {
  const stops = BMI_COLOR_STOPS;
  if (bmi <= stops[0][0]) return stops[0][1];
  for (let i = 1; i < stops.length; i++) {
    if (bmi <= stops[i][0]) {
      const [b0, c0] = stops[i - 1];
      const [b1, c1] = stops[i];
      return lerpRgb(c0, c1, (bmi - b0) / (b1 - b0));
    }
  }
  return stops[stops.length - 1][1];
}

// Precomputed BMI -> color lookup tables (0.1 resolution): one solid
// (line/points), one translucent (area fill, same hue). Outside
// [16, 29] the color is flat red either way, so a modest range with
// clamping is all that's needed.
const BMI_LUT_MIN = 10;
const BMI_LUT_MAX = 40;
const BMI_LUT_STEP = 0.1;
const BMI_FILL_ALPHA = 0.85;
const BMI_COLOR_LUT = [];
const BMI_FILL_LUT = [];
for (let bmi = BMI_LUT_MIN; bmi <= BMI_LUT_MAX + 1e-9; bmi += BMI_LUT_STEP) {
  const rgb = computeBmiRgb(bmi);
  BMI_COLOR_LUT.push(rgbToCss(rgb));
  BMI_FILL_LUT.push(rgbToCssAlpha(rgb, BMI_FILL_ALPHA));
}

function bmiLutIndex(bmi) {
  const clamped = Math.min(BMI_LUT_MAX, Math.max(BMI_LUT_MIN, bmi));
  return Math.round((clamped - BMI_LUT_MIN) / BMI_LUT_STEP);
}

function bmiColor(bmi) {
  if (bmi === null || bmi === undefined || Number.isNaN(bmi)) return chartLine;
  return BMI_COLOR_LUT[bmiLutIndex(bmi)];
}

function bmiFillColor(bmi) {
  if (bmi === null || bmi === undefined || Number.isNaN(bmi)) return chartFill;
  return BMI_FILL_LUT[bmiLutIndex(bmi)];
}

// Per-point colors for the data currently on the chart, rebuilt only
// when that data changes (see rebuildBmiColorCaches).
//
// The line itself is NOT per-segment colored (no segment.borderColor):
// Chart.js draws a new stroke/fill polygon every time a per-segment
// style actually changes between adjacent points (a "style run" break),
// and adjacent polygons can leave hairline antialiasing seams between
// them. On a long/decimated range there are still ~1000 points spanning
// the chart, and BMI drifts continuously along a smoothed trend — so
// even a coarse color-rounding step still breaks the path on nearly
// every point, producing a dense, regular vertical-striping artifact
// (confirmed via screenshots; a coarser rounding step alone wasn't
// enough to fix it). The y-axis BMI gradient on the fill already shows
// the same zone information clearly without any path-continuity issue
// (it's one continuous canvas gradient, not per-segment), so the line
// stays a single flat color and per-point BMI coloring is kept only for
// the dots, which are discrete shapes with no seam risk.
let currentPointColors = null;

function rebuildBmiColorCaches() {
  currentPointColors = currentBmis
    ? currentBmis.map((b) => (b == null ? chartLine : bmiColor(b)))
    : null;
}

function pointBmiColor(ctx) {
  if (!currentPointColors) return chartLine;
  return currentPointColors[ctx.dataIndex] ?? chartLine;
}

// The fill gradient runs along the y-axis (weight/BMI value) instead of
// the x-axis (time/segments): a canvas gradient with stops positioned by
// pixel height for each BMI threshold, so the background reads as fixed
// "zones" (red high / green healthy / red low) regardless of where the
// line's trend actually falls in time.
const heightCm = PAGE.height_cm;
// A public chart whose owner hid the weight: the values are an abstract 0-100 scale (see
// routes._without_weights), so no axis numbers and no tooltip values are shown.
const hideWeights = PAGE.hide_weights;
// One gradient stop per color stop (top of the chart = highest BMI).
const BMI_GRADIENT_STOPS = [BMI_LUT_MAX, ...BMI_COLOR_STOPS.map((s) => s[0]).reverse(), BMI_LUT_MIN];

function weightForBmi(bmi, heightM) {
  return bmi * heightM * heightM;
}

function bmiForWeight(weight) {
  const heightM = heightCm / 100;
  return weight / (heightM * heightM);
}

// Horizontal room taken by the y axes (weight on the left, plus BMI on the
// right once a height is set), for sizing the x axis labels.
const yAxesWidth = heightCm ? 100 : 48;

function verticalBmiGradient(context) {
  if (!heightCm) return chartFill;
  const { ctx, chartArea, scales } = context.chart;
  if (!chartArea) return chartFill;

  const heightM = heightCm / 100;
  const gradient = ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
  let lastOffset = 0;
  BMI_GRADIENT_STOPS.forEach((bmi) => {
    const pixelY = scales.y.getPixelForValue(weightForBmi(bmi, heightM));
    let offset = (pixelY - chartArea.top) / (chartArea.bottom - chartArea.top);
    offset = Math.min(1, Math.max(0, offset));
    offset = Math.max(offset, lastOffset); // guard against float dips breaking stop order
    lastOffset = offset;
    gradient.addColorStop(offset, bmiFillColor(bmi));
  });
  return gradient;
}

// Interpolated (gap-filled) points are plotted so the line stays
// continuous, but they aren't real measurements — no dot for those;
// only real entries get one. (Tooltip visibility is separate, see
// shouldShowTooltip below — decimated views need it to behave
// differently.)
function isRealPoint(dataIndex) {
  return !currentReal || currentReal[dataIndex] !== false;
}

// On a decimated (averaged, zoomed-out) range, virtually every plotted
// point spans more than one source day, so decimateForDisplay marks it
// real: false — correctly, it isn't any single day's real measurement.
// But reusing isRealPoint for the tooltip filter meant that on 10Y+/All,
// where almost nothing survives as a single-day bucket, hover showed no
// tooltip anywhere at all. An averaged point still has a meaningful
// value worth showing on hover, so once a range is actually decimated,
// every point gets a tooltip; the real-only rule only applies at full
// daily resolution, where "not a real measurement" still means
// something distinct from "here's the average around this date".
let currentDecimated = false;

function shouldShowTooltip(dataIndex) {
  return currentDecimated || isRealPoint(dataIndex);
}

function pointRadiusFor(ctx) {
  return isRealPoint(ctx.dataIndex) ? 1 : 0;
}

function pointHoverRadiusFor(ctx) {
  return isRealPoint(ctx.dataIndex) ? 5 : 0;
}

// X-axis tick density depends on the selected range: the default
// category-scale autoSkip just thins ticks by pixel width, which doesn't
// know or care about calendar boundaries — so for a multi-year range it
// can show an arbitrary, uneven mix of dates rather than "one per year".
// Instead we take manual control (autoSkip: false) and only label the
// first point of each calendar unit for the current range.
const yearFormat = new Intl.DateTimeFormat(LOCALE, { year: 'numeric' });
const shortYearFormat = new Intl.DateTimeFormat(LOCALE, { year: '2-digit' });
const monthYearFormat = new Intl.DateTimeFormat(LOCALE, { month: 'short', year: 'numeric' });
const dayOfMonthFormat = new Intl.DateTimeFormat(LOCALE, { day: 'numeric' });
const YEARLY_TICK_RANGES = new Set(['5y', '10y', '15y', '20y']);
const MONTHLY_TICK_RANGES = new Set(['3m', '6m', '1y']);
const DAILY_TICK_RANGES = new Set(['1w', '2w', '1m']);

let currentLabels = fullLabels;

function tickGranularity() {
  if (YEARLY_TICK_RANGES.has(currentRange)) return 'year';
  if (MONTHLY_TICK_RANGES.has(currentRange)) return 'month';
  if (DAILY_TICK_RANGES.has(currentRange)) return 'day';
  // "all" (or any future range) has no fixed span, so infer from what's
  // actually on screen right now.
  if (currentLabels.length < 2) return 'day';
  const spanDays = (new Date(currentLabels[currentLabels.length - 1]) - new Date(currentLabels[0])) / 86400000;
  if (spanDays > 2 * 365) return 'year';
  if (spanDays > 60) return 'month';
  return 'day';
}

// On narrow screens the full 4-digit years crowd each other on the axis,
// so they're shortened to two digits (2025 -> 25, "Jan 2025" -> "Jan '25").
const narrowScreen = window.matchMedia('(max-width: 720px)');

// "Jan 2025" in the page language; on narrow screens only the year part is
// swapped for its apostrophe form ("Jan '25").
function monthYearText(date) {
  return monthYearFormat.formatToParts(date).map((part) => (
    part.type === 'year' && narrowScreen.matches ? "'" + part.value.slice(-2) : part.value
  )).join('');
}

function computeTickText(index) {
  const label = currentLabels[index];
  if (!label) return '';
  const granularity = tickGranularity();
  const prev = index > 0 ? currentLabels[index - 1] : null;

  // The underlying series is a contiguous run of calendar days, so every
  // month/year boundary AFTER the first point necessarily lands exactly
  // on that period's 1st day. The very first point of whatever window is
  // currently shown is the exception — a range cutoff (or the user's
  // very first entry) rarely lands on the 1st of a month/year, so
  // labeling it with that period's full name would claim a complete
  // month/year that isn't actually fully represented. Suppress it there.
  if (granularity === 'year') {
    const year = label.slice(0, 4);
    if (prev && prev.slice(0, 4) === year) return '';
    if (index === 0 && label.slice(5, 10) !== '01-01') return '';
    const yearDate = parseIsoDate(label);
    return (narrowScreen.matches ? shortYearFormat : yearFormat).format(yearDate);
  }
  if (granularity === 'month') {
    const yearMonth = label.slice(0, 7);
    if (prev && prev.slice(0, 7) === yearMonth) return '';
    if (index === 0 && label.slice(8, 10) !== '01') return '';
    const month = Number(label.slice(5, 7)) - 1;
    // Narrow screens, 1Y: label every second month (Jan, Mar, May, ...).
    if (narrowScreen.matches && currentRange === '1y' && month % 2 !== 0) return '';
    return monthYearText(parseIsoDate(label));
  }
  // Narrow screens, 1M: label every second day, counting back from the
  // latest so the newest day is always labeled.
  if (narrowScreen.matches && currentRange === '1m' && (currentLabels.length - 1 - index) % 2 !== 0) return '';
  return dayOfMonthFormat.format(parseIsoDate(label));
}

// Precomputed once per data/range change (see rebuildTickCache), not per
// redraw: grid line/tick-mark styling is a scriptable option too, and
// Chart.js re-evaluates those on every redraw (every hover/mousemove),
// same as the point/segment colors above — so this stays a plain lookup
// on the hot path rather than re-parsing dates each time.
let currentTickTexts = [];

// Width of the plotted area in px (set on every chart resize), used to
// drop axis labels that would otherwise overlap.
let tickPlotWidth = 0;

// Keep only every k-th label when the labels computed for this range don't
// fit side by side at the current chart width (each needs about minPx).
// Counted back from the newest label so the latest one always stays.
function thinTicks(texts) {
  const shown = [];
  texts.forEach((text, i) => { if (text) shown.push(i); });
  const granularity = tickGranularity();
  const minPx = granularity === 'year' ? 34 : granularity === 'month' ? 56 : 22;
  const width = tickPlotWidth || (document.getElementById('weight-chart').clientWidth - yAxesWidth);
  if (width <= 0 || shown.length < 2) return texts;
  const step = Math.ceil((minPx * shown.length) / width);
  if (step <= 1) return texts;
  shown.forEach((i, j) => {
    if ((shown.length - 1 - j) % step !== 0) texts[i] = '';
  });
  return texts;
}

function rebuildTickCache() {
  currentTickTexts = thinTicks(currentLabels.map((_label, i) => computeTickText(i)));
}

function xTickLabel(_value, index) {
  return currentTickTexts[index] ?? '';
}

function xGridStyle(ctx) {
  return currentTickTexts[ctx.index] ? gridLineColor : 'transparent';
}

// Relabel the axis when the window crosses the narrow-screen breakpoint
// (rotating a phone, resizing a desktop window).
// Chart.js draws its axis text on the canvas, so the CSS font sizes don't
// reach it: use a smaller size on narrow screens too.
function applyChartFontSize() {
  Chart.defaults.font.size = narrowScreen.matches ? 10 : 12;
}
applyChartFontSize();
narrowScreen.addEventListener('change', () => {
  applyChartFontSize();
  chart.options.scales.x.ticks.font = { size: Chart.defaults.font.size };
  chart.options.scales.y.ticks.font = { size: Chart.defaults.font.size };
  rebuildTickCache();
  chart.update();
});

// A note as tooltip lines. Chart.js does not wrap text, and a note can be
// NOTE_MAX_LENGTH characters (app/utils.py), so break it at word boundaries
// (and split a very long word, such as a link) into lines of about
// NOTE_LINE_WIDTH characters.
const NOTE_LINE_WIDTH = 36;
function wrapNote(note) {
  if (!note) return [];
  const lines = [];
  note.split(/\r?\n/).forEach((paragraph) => {
    let line = '';
    paragraph.split(/\s+/).filter(Boolean).forEach((word) => {
      let chars = Array.from(word); // code points, so an emoji is never cut in half
      while (chars.length > NOTE_LINE_WIDTH) {
        if (line) { lines.push(line); line = ''; }
        lines.push(chars.slice(0, NOTE_LINE_WIDTH).join(''));
        chars = chars.slice(NOTE_LINE_WIDTH);
      }
      word = chars.join('');
      if (line && Array.from(line + ' ' + word).length > NOTE_LINE_WIDTH) {
        lines.push(line);
        line = word;
      } else {
        line = line ? line + ' ' + word : word;
      }
    });
    if (line) lines.push(line);
  });
  return lines;
}

// BMI on a given date from the moving-average weight of that date; null
// without a height or if the date isn't in the full series. The date ->
// index lookup is rebuilt only when the full series is replaced (after a
// save), not on every hover.
let fullLabelIndex = null;
let fullLabelIndexFor = null;

function movingAvgBmiOn(dateLabel) {
  if (!heightCm) return null;
  if (fullLabelIndexFor !== fullLabels) {
    fullLabelIndex = new Map(fullLabels.map((label, i) => [label, i]));
    fullLabelIndexFor = fullLabels;
  }
  const i = fullLabelIndex.get(dateLabel);
  if (i === undefined || fullMovingAvg[i] == null) return null;
  const heightM = heightCm / 100;
  return fullMovingAvg[i] / (heightM * heightM);
}

const chartCtx = document.getElementById('weight-chart');
const chart = new Chart(chartCtx, {
  type: 'line',
  data: {
    labels: fullLabels,
    datasets: [{
      label: t('weightKg'),
      data: fullValues,
      borderColor: chartLine,
      borderWidth: 1,
      backgroundColor: verticalBmiGradient,
      // No segment.borderColor override — the line stays a single flat
      // color (see the comment above currentPointColors/pointBmiColor
      // for why: per-segment coloring was the actual source of the
      // vertical seam artifact). tension stays 0 regardless, since
      // straight segments are simplest for a chart with this many
      // points either way.
      tension: 0,
      fill: true,
      pointStyle: 'circle',
      pointRadius: pointRadiusFor,
      pointBorderWidth: 0,
      pointBackgroundColor: pointBmiColor,
      pointHoverRadius: pointHoverRadiusFor,
    }, {
      label: t('movingAvg'),
      data: [],
      borderColor: movingAvgColor,
      borderWidth: 1.5,
      // Drawn last, on top of everything. Chart.js draws dataset 0 (the weight line with its
      // semi-transparent BMI fill) over the others by default, which made this line look faded
      // wherever it ran below the weight line. A lower `order` is drawn later.
      order: -2,
      fill: false,
      tension: 0,
      pointRadius: 0,
      pointHoverRadius: 0,
    }, {
      label: t('overallAvg'),
      data: [],
      borderColor: overallAvgColor,
      borderWidth: 1,
      borderDash: [4, 4],
      // Over the weight fill too, but under the moving average (see its `order`).
      order: -1,
      fill: false,
      tension: 0,
      pointRadius: 0,
      pointHoverRadius: 0,
    }]
  },
  options: {
    responsive: true,
    // Number formatting of the axis ticks and tooltips.
    locale: LOCALE,
    // The chart's wrapper is now a flex/grid panel with a dynamic height
    // (not a fixed canvas height), so let the chart fill it exactly.
    maintainAspectRatio: false,
    // Re-thin the x axis labels for the new width before the redraw.
    onResize(_chart, size) {
      tickPlotWidth = size.width - yAxesWidth;
      rebuildTickCache();
    },
    // Data updates (autosave, range switching) happen often; animating a
    // full redraw each time just re-runs the per-point/segment styling
    // across extra frames for no benefit.
    animation: false,
    interaction: { mode: 'nearest', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        enabled: !hideWeights,
        // Interpolated points carry no real value — don't show one. The
        // average lines always have a value at every plotted point
        // though, so that filter only applies to the weight series.
        filter: (item) => item.datasetIndex !== 0 || shouldShowTooltip(item.dataIndex),
        // Weight first, as in the data: the moving average's `order` (drawn on top) would
        // otherwise list it first.
        itemSort: (a, b) => a.datasetIndex - b.datasetIndex,
        // The day's note, under the BMI: in the footer, which has its own spacing
        // and a normal weight and slightly dimmer colour so it reads as a remark.
        // Not italic: the canvas would slant the emoji in a note too.
        footerFont: { weight: 'normal', style: 'normal' },
        footerColor: 'rgba(255, 255, 255, 0.82)',
        footerMarginTop: 8,
        callbacks: {
          title: (items) => (items.length ? fmtWeekdayDate(items[0].label) : ''),
          footer: (items) => (items.length ? wrapNote(fullNotes[items[0].label]) : []),
          // The BMI at the hovered date, from that date's moving-average
          // weight (the same smoothed value the stats boxes use).
          afterBody: (items) => {
            const bmi = items.length ? movingAvgBmiOn(items[0].label) : null;
            return bmi === null ? [] : [t('bmiTooltip', { bmi: oneDecimalFormat.format(bmi) })];
          },
        },
      },
    },
    scales: {
      x: {
        grid: { color: xGridStyle, tickColor: xGridStyle },
        ticks: { autoSkip: false, maxRotation: 0, callback: xTickLabel },
      },
      y: { beginAtZero: false, ticks: { display: !hideWeights } },
      // BMI scale on the right, at the same ticks as the weight scale:
      // BMI is just weight / height^2, so this axis spans the same weight
      // range and only relabels each tick (it has no data of its own).
      // Only shown once a height is set.
      yBmi: {
        display: !!heightCm,
        position: 'right',
        grid: { drawOnChartArea: false },
        afterDataLimits(scale) {
          const weightScale = scale.chart.scales.y;
          if (weightScale) {
            scale.min = weightScale.min;
            scale.max = weightScale.max;
          }
        },
        afterBuildTicks(scale) {
          const weightScale = scale.chart.scales.y;
          if (!weightScale) return;
          scale.ticks = weightScale.ticks.map((t) => ({ value: t.value }));
          // The range must be copied again here: this axis is a linear scale with the
          // default bounds: 'ticks', so Chart.js has just snapped its min/max to the ends
          // of the ticks it generated itself - after afterDataLimits copied them. On a
          // narrow range such as 1W those own ticks end elsewhere (95 instead of 94.8),
          // the right axis then covers a different range and its ticks land at the wrong
          // height, although they carry the right values.
          scale.min = weightScale.min;
          scale.max = weightScale.max;
        },
        ticks: {
          callback: (weight) => oneDecimalFormat.format(bmiForWeight(weight)),
        },
        title: { display: true, text: t('bmi') },
      },
    },
    // A point jumps to its day in the daily log (scrollToDate, dashboard-log.js), which the
    // public page does not have.
    onClick(evt, elements, chartInstance) {
      if (PAGE.public || !elements.length) return;
      const dateStr = chartInstance.data.labels[elements[0].index];
      if (dateStr) scrollToDate(dateStr);
    },
    onHover(evt, elements) {
      const cursor = elements.length && !PAGE.public ? 'pointer' : 'default';
      if (evt.native.target.style.cursor !== cursor) {
        evt.native.target.style.cursor = cursor;
      }
    },
  }
});

// Chart.js only re-measures its container on screen resizes, so without
// this the chart prints at its on-screen size, scaled/clipped inside the
// print layout (see static/print.css). Re-measure when print mode starts
// (the print stylesheet has given the container its paper size by then)
// and again when it ends.
function resizeChart() { chart.resize(); }
window.addEventListener('beforeprint', resizeChart);
window.addEventListener('afterprint', resizeChart);
const printQuery = window.matchMedia('print');
if (printQuery.addEventListener) printQuery.addEventListener('change', resizeChart);

// Long ranges (5Y+/All) can put many thousands of daily points — one per
// calendar day, since gaps are interpolated — across maybe 1500px of
// chart width: several points per pixel. A first attempt at fixing this
// kept each bucket's min AND max, on the theory that real peaks/dips
// should stay visible — but for genuinely volatile day-to-day data that
// just reproduces the same dense zig-zag at a slightly lower density
// ("still a lot of vertical white lines" after that fix shipped). What a
// zoomed-out multi-year view actually needs is a smoothed trend, the same
// way stock charts and analytics dashboards aggregate by average once
// you're looking at a long enough span — so each bucket now contributes
// ONE averaged point instead of two extremes. Full day-by-day resolution
// (spikes included) is exactly what the un-decimated short ranges
// (1W-1Y, all under the cap) already show.
const MAX_PLOT_POINTS = 1000;

// `series` and the result: {labels, values, bmis, reals, movingAvg}, parallel arrays.
function decimateForDisplay(series) {
  const { labels, values, bmis, reals, movingAvg } = series;
  const n = labels.length;
  if (n <= MAX_PLOT_POINTS) return series;

  const bucketSize = n / MAX_PLOT_POINTS;
  const outLabels = [];
  const outValues = [];
  const outBmis = bmis ? [] : null;
  const outReals = [];
  const outMovingAvg = [];

  for (let b = 0; b < MAX_PLOT_POINTS; b++) {
    const start = Math.floor(b * bucketSize);
    const end = Math.min(n, Math.floor((b + 1) * bucketSize));
    if (end <= start) continue;

    let valueSum = 0;
    let bmiSum = 0;
    let bmiCount = 0;
    let movingAvgSum = 0;
    for (let i = start; i < end; i++) {
      valueSum += values[i];
      movingAvgSum += movingAvg[i];
      if (bmis && bmis[i] != null) {
        bmiSum += bmis[i];
        bmiCount++;
      }
    }
    const count = end - start;
    const midIndex = start + Math.floor((count - 1) / 2);

    outLabels.push(labels[midIndex]);
    outValues.push(valueSum / count);
    outMovingAvg.push(movingAvgSum / count);
    if (outBmis) outBmis.push(bmiCount ? bmiSum / bmiCount : null);
    // A bucket spanning more than one source day is now a synthetic
    // average, not an individual measurement — no dot/tooltip for it. A
    // bucket that happens to cover exactly one source day (the un-full
    // final bucket, typically) keeps that day's real/interpolated status.
    outReals.push(count === 1 ? reals[start] : false);
  }

  return { labels: outLabels, values: outValues, bmis: outBmis, reals: outReals, movingAvg: outMovingAvg };
}

// The moving average is only drawn on the short ranges; on 5Y and longer
// it's just noise on top of an already-decimated line.
const MOVING_AVG_RANGES = new Set(['1w', '2w', '1m', '3m', '6m', '1y']);

// The part of the full series in a range of `days` days up to today; the full series
// itself (not a copy) for "all", which has no `days`.
function sliceToRange(days) {
  if (!days) {
    return { labels: fullLabels, values: fullValues, bmis: fullBmis, reals: fullReal, movingAvg: fullMovingAvg };
  }
  // The first day shown: `days` days before today, as a LOCAL calendar
  // date. (toISOString() would convert local midnight to UTC and, east of
  // Greenwich, land on the previous day - one day too many in the chart.)
  const cutoff = new Date();
  cutoff.setHours(0, 0, 0, 0);
  cutoff.setDate(cutoff.getDate() - days);
  const cutoffStr = localIso(cutoff);
  const series = { labels: [], values: [], bmis: fullBmis ? [] : null, reals: [], movingAvg: [] };
  for (let i = 0; i < fullLabels.length; i++) {
    if (fullLabels[i] >= cutoffStr) {
      series.labels.push(fullLabels[i]);
      series.values.push(fullValues[i]);
      if (fullBmis) series.bmis.push(fullBmis[i]);
      series.reals.push(fullReal[i]);
      series.movingAvg.push(fullMovingAvg[i]);
    }
  }
  return series;
}

// The right edge of the chart is always today: when there is no entry for
// today (or the last few days) those days stay on the axis, empty, and the
// line simply stops at the last entry. A decimated range (thousands of days)
// has averaged points that no longer stand for single days, so it is left alone.
// Returns a padded copy (`series` may be the full data, see sliceToRange).
function padToToday(series, days) {
  if (series.labels.length > MAX_PLOT_POINTS || !fullLabels.length) return series;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const todayStr = localIso(today);
  const { labels } = series;
  const next = labels.length ? parseIsoDate(labels[labels.length - 1]) : new Date(today);
  if (labels.length) next.setDate(next.getDate() + 1);
  else next.setDate(next.getDate() - days);
  const padded = {
    labels: labels.slice(),
    values: series.values.slice(),
    bmis: series.bmis && series.bmis.slice(),
    reals: series.reals.slice(),
    movingAvg: series.movingAvg.slice(),
  };
  for (; localIso(next) <= todayStr; next.setDate(next.getDate() + 1)) {
    padded.labels.push(localIso(next));
    padded.values.push(null);
    if (padded.bmis) padded.bmis.push(null);
    padded.reals.push(false);
    padded.movingAvg.push(null);
  }
  return padded;
}

function applyRange(rangeKey) {
  currentRange = rangeKey;
  const days = RANGE_DAYS[rangeKey];
  const inRange = sliceToRange(days);

  // The dotted overall-average line reflects whatever period is
  // currently graphed (the selected range), not all-time history —
  // computed from the range-filtered values before padding and decimation
  // so it stays exact regardless of how coarse the bucketing gets.
  const values = inRange.values;
  const overallAvg = values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;

  const padded = padToToday(inRange, days);
  currentDecimated = padded.labels.length > MAX_PLOT_POINTS;
  const { labels, values: shownValues, bmis, reals, movingAvg } = decimateForDisplay(padded);

  currentBmis = bmis;
  currentReal = reals;
  currentLabels = labels;
  rebuildBmiColorCaches();
  rebuildTickCache();
  chart.data.labels = labels;
  chart.data.datasets[0].data = shownValues;
  chart.data.datasets[1].data = MOVING_AVG_RANGES.has(rangeKey) ? movingAvg : [];
  chart.data.datasets[2].data = overallAvg == null ? [] : labels.map(() => overallAvg);
  chart.update();

  document.querySelectorAll('.range-btn').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.range === rangeKey);
  });
}

function applyAvailableRanges(keys) {
  document.querySelectorAll('.range-btn').forEach((btn) => {
    btn.hidden = !keys.includes(btn.dataset.range);
  });
}

document.querySelectorAll('.range-btn').forEach((btn) => {
  btn.addEventListener('click', async () => {
    applyRange(btn.dataset.range);
    if (PAGE.public) return; // a visitor's choice is not saved
    try {
      await postJson(PAGE.urls.chart_range, { range: btn.dataset.range });
    } catch (err) {
      // best-effort persistence; the chart itself already reflects the choice
    }
  });
});

// Clicking a change box switches the chart to the matching range (7-day
// -> 1W, 14-day -> 2W, ..., total -> All). It clicks the range button so
// the choice is saved like any other; a range whose button is hidden (not
// enough history yet) is ignored.
function showRangeFromStat(card) {
  const btn = document.querySelector('.range-btn[data-range="' + card.dataset.range + '"]');
  if (btn && !btn.hidden) btn.click();
}

const statsPanel = document.querySelector('.stats');
if (statsPanel) {
  statsPanel.addEventListener('click', (e) => {
    const card = e.target.closest('.stat-card-link');
    if (card) showRangeFromStat(card);
  });
  statsPanel.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const card = e.target.closest('.stat-card-link');
    if (!card) return;
    e.preventDefault();
    showRangeFromStat(card);
  });
}

applyRange(currentRange);
