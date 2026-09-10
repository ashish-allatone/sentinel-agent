import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useSearchParams } from "react-router-dom";
import {
  Area,
  AreaChart,
  Brush,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { absoluteOverviewUrl, fetchOverview } from "./capacityApi";
import {
  buildRows,
  clampRange as clampToBounds,
  findGaps,
  formatClock,
  formatDuration,
  formatFull,
} from "./capacityTransform";
import { CHART_CHROME, GAP_COLOR, SERIES_COLORS } from "./colors";
import { istInputToApi, lastHoursInputs } from "./timeRange";
import "./CapacityDashboard.css";

// import {
//   fetchChannels,
//   fetchChannelReadiness,
//   sendCommunication,
// } from "../Channels/channelsApi";

import { fetchChannels } from "../Channels/channelsApi";

import {
  fetchChannelAccounts,
  sendFileViaChannelAccount,
} from "../Channels/channelAccountsApi";

import html2canvas from "html2canvas";
import { jsPDF } from "jspdf";

// ── zoom/pan constants ─────────────────────────────────────────
const MIN_SPAN = 8; // never zoom tighter than 8 samples
const ZOOM_IN = 0.8;
const ZOOM_OUT = 1.25;
const MIN_BOX_PX = 12; // ignore box-zoom smudges

// The default query window, and the presets the "Range" dropdown offers (hours).
const DEFAULT_WINDOW_HOURS = 12;
const WIDEN_WINDOW_HOURS = 24;
const PRESET_HOURS = [1, 2, 3, 6, 12, 24];
const presetLabel = (h) => `Last ${h} hour${h > 1 ? "s" : ""}`;

/**
 * Theme-toggle glyph as inline SVG (currentColor) — the previous Unicode
 * ☀/☾ rendered as an ambiguous asterisk in the monospace font on some
 * platforms. `mode` is the CURRENT theme; the icon shows the target the click
 * moves to (sun while dark, moon while light).
 */
function ThemeIcon({ mode }) {
  const common = {
    className: "capacity-dash__icon",
    viewBox: "0 0 24 24",
    width: 16,
    height: 16,
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": true,
  };
  if (mode === "dark") {
    return (
      <svg {...common}>
        <circle cx="12" cy="12" r="4.2" />
        <path d="M12 2.5v2.2M12 19.3v2.2M4.6 4.6l1.6 1.6M17.8 17.8l1.6 1.6M2.5 12h2.2M19.3 12h2.2M4.6 19.4l1.6-1.6M17.8 6.2l1.6-1.6" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <path d="M20 14.6A8 8 0 1 1 9.4 4 6.2 6.2 0 0 0 20 14.6z" />
    </svg>
  );
}

// Recharts lays the plot out inside these; the pointer maths needs the plot
// box, not the container box, or the cursor anchor drifts.
const CHART_MARGIN = { top: 10, right: 14, bottom: 4, left: 4 };
const Y_AXIS_WIDTH = 44;
const PLOT_INSET_LEFT = CHART_MARGIN.left + Y_AXIS_WIDTH;
const PLOT_INSET_RIGHT = CHART_MARGIN.right;

// ── pane definitions ───────────────────────────────────────────
// Storage steps because its jumps (82.4 -> 86.3 -> 82.4) are real deploy /
// cleanup events; smoothing them would state something untrue.
//
// A line's `key` is both its row field and its slot in the palette, so colour
// follows the entity and stays put when the theme changes.
// `lead` is the plain-English explanation printed under each chart's heading in
// the PDF — the report is read by people who did not run it. The screen ignores
// it; the interactive pane titles are enough there.
const PANES = [
  {
    id: "host-cpu",
    title: "Host CPU",
    unit: "%",
    lines: [{ key: "cpu", name: "Host CPU", type: "monotone" }],
    lead: `The share of the machine's processor capacity in use, sampled by the agent. Brief
      spikes are normal — a program starting, a scan running. What matters is a line that sits
      high for long stretches: that is a host with no headroom left for new work, and the point
      at which everything else on it starts to slow down.`,
  },
  {
    id: "host-mem",
    title: "Host memory",
    unit: "%",
    lines: [{ key: "mem", name: "Host memory", type: "monotone" }],
    lead: `The share of physical memory (RAM) in use on the host. Once this stays high the
      machine begins moving memory to disk to cope, which shows up to users as sluggishness long
      before anything actually fails. A steadily rising line with no dips is worth investigating
      even while the percentage still looks safe.`,
  },
  {
    id: "host-sto",
    title: "Host storage",
    unit: "%",
    lines: [{ key: "sto", name: "Storage", type: "stepAfter" }],
    lead: `How full the host's disks are. Under normal use this only moves one way, so the slope
      matters more than the current value: extend the line to the right and you have the date the
      disk runs out. A disk that fills stops logs being written, which is what makes this a
      monitoring concern and not just a housekeeping one.`,
  },
  {
    id: "agent-cpu",
    title: "Agent CPU",
    unit: "%",
    lines: [{ key: "acpu", name: "Agent CPU", type: "monotone" }],
    lead: `Processor time used by the Guardlynx agent itself, as a share of the host's capacity.
      This is the cost of being monitored: it should stay a small fraction of the host CPU line
      above. If the two rise together, the agent is contributing to the load rather than just
      observing it.`,
  },
  // Mb, not percent — its own pane, so it never shares the 0-100 axis above.
  // Agent memory used to ride the Agent-CPU pane on a shared % axis; once its
  // unit is Mb that would misstate it, the same reason bandwidth stands alone.
  {
    id: "agent-mem",
    title: "Agent memory",
    unit: "Mb",
    lines: [{ key: "amem", name: "Agent memory", type: "monotone" }],
    lead: `Memory held by the agent process, in megabytes — an absolute figure, not a percentage,
      which is why it has an axis of its own. Healthy behaviour is a line that rises and falls as
      work comes and goes; a line that only ever climbs, never returning to its earlier level, is
      the signature of a memory leak.`,
  },
  // Mbps, not percent — hence its own pane. Sharing the 0-100 axis above would
  // flatten a 0.04 Mbps line onto the baseline and imply it is a percentage.
  {
    id: "agent-bw",
    title: "Agent bandwidth",
    unit: "Mbps",
    lines: [{ key: "bw", name: "Agent bandwidth", type: "monotone" }],
    lead: `Network throughput used by the agent to ship its telemetry, in megabits per second.
      This is the load monitoring places on the site's link — compare it against what the
      connection can spare, particularly for hosts on a metered or shared line.`,
  },
];

/**
 * Values on this page span 0-100 percentages and sub-1 Mbps readings, so the
 * decimal count follows the magnitude — %.toFixed(1) would render 0.04 Mbps as
 * "0.0" and the whole bandwidth pane would read as zero.
 */
function formatValue(value, unit) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  const decimals = abs >= 10 ? 1 : abs >= 1 ? 2 : 3;
  return unit === "%"
    ? `${n.toFixed(decimals)}%`
    : `${n.toFixed(decimals)} ${unit}`;
}

/** Axis ticks: same magnitude rule, without the unit suffix. */
function formatTick(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "";
  if (n === 0) return "0"; // a bare zero, never "0.00" — the baseline on a % axis
  const abs = Math.abs(n);
  if (abs >= 10) return String(Math.round(n));
  if (abs >= 1) return n.toFixed(1);
  return n.toFixed(2);
}

/** Resolve a pane's lines against the active theme's palette. */
function paintLines(lines, theme) {
  const palette = SERIES_COLORS[theme];
  return lines.map((line) => ({ ...line, color: palette[line.key] }));
}

// ── print layout ───────────────────────────────────────────────
// A4 portrait at 96dpi minus 12mm margins is ~715px of usable width. The print
// charts are given explicit pixel sizes rather than a ResponsiveContainer:
// ResponsiveContainer measures the DOM through a ResizeObserver, which does not
// re-measure for the print box, so it would print the charts at screen size.
const PRINT_W = 700;
// const PRINT_H = 430;
const PRINT_H = 520;

const STATS = [
  { key: "avg_cpu_percent", label: "Avg CPU", unit: "%", modifier: "cpu" },
  { key: "avg_memory", label: "Avg memory", unit: "MB", modifier: "mem" },
  {
    key: "avg_agent_cpu_percent",
    label: "Avg Agent CPU",
    unit: "%",
    modifier: "acpu",
  },
  {
    key: "avg_agent_memory",
    label: "Avg Agent memory",
    unit: "Mb",
    modifier: "amem",
  },
  {
    key: "avg_bandwidth_mbps",
    label: "Avg Bandwidth",
    unit: "Mbps",
    modifier: "bw",
  },
];

// ── range helpers ──────────────────────────────────────────────
function clamp01(n) {
  return Math.min(1, Math.max(0, n));
}

/** Bind the shared MIN_SPAN to the pure clamp from capacityTransform. */
function clampRange(start, end, lastIndex) {
  return clampToBounds(start, end, lastIndex, MIN_SPAN);
}

/** Zoom a window about its own centre by `factor` (<1 in, >1 out). */
function zoomAround([start, end], factor, lastIndex) {
  const centre = (start + end) / 2;
  return clampRange(
    centre - (centre - start) * factor,
    centre + (end - centre) * factor,
    lastIndex,
  );
}

const fullRange = (lastIndex) => [0, Math.max(0, lastIndex)];

const THEME_KEY = "capacity-dash-theme";

/** Saved choice wins; otherwise follow the OS. */
function initialTheme() {
  try {
    const saved = window.localStorage.getItem(THEME_KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch (_) {
    /* storage can be blocked; fall through to the OS preference */
  }
  try {
    if (
      window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: light)").matches
    ) {
      return "light";
    }
  } catch (_) {
    /* matchMedia missing */
  }
  return "dark";
}

/** datetime-local input values for the last 12 hours, in IST (see timeRange.js). */
function defaultLocalRange() {
  return lastHoursInputs(DEFAULT_WINDOW_HOURS);
}

// ── zoom / pan / box-select ────────────────────────────────────
function useZoomPan({ range, setRange, lastIndex }) {
  const containerRef = useRef(null);
  const dragRef = useRef(null);
  const rangeRef = useRef(range);
  rangeRef.current = range;

  const [selection, setSelection] = useState(null);

  const geometry = useCallback(() => {
    const rect = containerRef.current.getBoundingClientRect();
    const left = rect.left + PLOT_INSET_LEFT;
    const width = Math.max(1, rect.width - PLOT_INSET_LEFT - PLOT_INSET_RIGHT);
    return { rect, left, width };
  }, []);

  // Wheel must be a native non-passive listener: React's onWheel is passive, so
  // preventDefault there is ignored and the page scrolls under the cursor.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return undefined;

    const onWheel = (event) => {
      if (lastIndex <= 0) return;
      event.preventDefault();

      const { left, width } = geometry();
      const [start, end] = rangeRef.current;
      // The sample under the pointer has to stay under the pointer, so zoom
      // about that index rather than the middle of the pane.
      const anchor =
        start + clamp01((event.clientX - left) / width) * (end - start);
      const factor = event.deltaY < 0 ? ZOOM_IN : ZOOM_OUT;

      setRange(
        clampRange(
          anchor - (anchor - start) * factor,
          anchor + (end - anchor) * factor,
          lastIndex,
        ),
      );
    };

    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [geometry, lastIndex, setRange]);

  const onPointerDown = (event) => {
    if (lastIndex <= 0 || event.button !== 0) return;
    const { rect } = geometry();
    const x = event.clientX - rect.left;

    try {
      containerRef.current.setPointerCapture(event.pointerId);
    } catch (_) {
      /* capture is best-effort */
    }

    if (event.shiftKey) {
      dragRef.current = { mode: "box", x0: x };
      setSelection({ x0: x, x1: x });
    } else {
      dragRef.current = {
        mode: "pan",
        startX: event.clientX,
        range: rangeRef.current,
      };
    }
  };

  const onPointerMove = (event) => {
    const drag = dragRef.current;
    if (!drag) return;
    const { rect, width } = geometry();

    if (drag.mode === "pan") {
      const [start, end] = drag.range;
      const deltaIndex =
        ((event.clientX - drag.startX) / width) * (end - start);
      setRange(clampRange(start - deltaIndex, end - deltaIndex, lastIndex));
    } else {
      const x = event.clientX - rect.left;
      setSelection((current) =>
        current ? { x0: current.x0, x1: x } : current,
      );
    }
  };

  const finishDrag = (event) => {
    const drag = dragRef.current;
    dragRef.current = null;
    try {
      containerRef.current.releasePointerCapture(event.pointerId);
    } catch (_) {
      /* nothing captured */
    }
    if (!drag || drag.mode !== "box") return;

    const { rect, left, width } = geometry();
    const x = event.clientX - rect.left;
    const x0 = Math.min(drag.x0, x);
    const x1 = Math.max(drag.x0, x);
    setSelection(null);

    if (x1 - x0 < MIN_BOX_PX) return;

    const [start, end] = rangeRef.current;
    const toIndex = (px) =>
      start + clamp01((px + rect.left - left) / width) * (end - start);
    const nextStart = toIndex(x0);
    const nextEnd = toIndex(x1);
    if (nextEnd - nextStart < MIN_SPAN) return;

    setRange(clampRange(nextStart, nextEnd, lastIndex));
  };

  const onDoubleClick = () => setRange([0, Math.max(0, lastIndex)]);

  return {
    containerRef,
    selection,
    handlers: {
      onPointerDown,
      onPointerMove,
      onPointerUp: finishDrag,
      onPointerCancel: finishDrag,
      onDoubleClick,
    },
  };
}

/**
 * A sample whose neighbours are both null has nothing to draw a segment to, so
 * with connectNulls={false} it renders as literally nothing. That happens
 * whenever a series reports on a coarser cadence than the joined timeline — the
 * agent series against the host series, for instance — and it silently blanks
 * the whole pane. Dots are drawn for those points only: a lone reading stays
 * visible, and a run of samples still costs zero dots.
 */
function makeDotRenderer(dataKey, color, data) {
  return function renderDot(props) {
    const { cx, cy, index } = props;
    if (cx == null || cy == null || data[index] == null) return null;
    const prev = index > 0 ? data[index - 1][dataKey] : null;
    const next = index < data.length - 1 ? data[index + 1][dataKey] : null;
    if (prev != null || next != null) return null; // already part of a segment
    return <circle cx={cx} cy={cy} r={1.7} fill={color} stroke="none" />;
  };
}

// ── tooltip ────────────────────────────────────────────────────
function CapacityTooltip({ active, payload, lines, unit }) {
  if (!active || !payload || !payload.length) return null;
  const row = payload[0].payload;

  return (
    <div className="capacity-dash__tooltip">
      <div className="capacity-dash__tooltip-time">
        {formatFull(row.ms)} IST
      </div>
      {lines.map((line) => {
        const value = row[line.key];
        return (
          <div className="capacity-dash__tooltip-row" key={line.key}>
            <span className="capacity-dash__tooltip-key">
              <span
                className="capacity-dash__swatch"
                style={{ backgroundColor: line.color }}
                aria-hidden="true"
              />
              {line.name}
            </span>
            <span className="capacity-dash__tooltip-value">
              {value == null ? "no sample" : formatValue(value, unit)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/**
 * The chart itself, shared by the interactive panes and the printed pages.
 *
 * Pass `width`/`height` to get a fixed-size chart (print); omit them for a
 * ResponsiveContainer (screen).
 *
 * No syncId: each pane now zooms to its own window, so a shared crosshair would
 * point at a different sample in every pane. Hover is per-pane, like the zoom.
 */
function ChartBody({
  lines,
  data,
  rows,
  gaps,
  start,
  end,
  theme,
  height,
  width,
  unit,
}) {
  const chrome = CHART_CHROME[theme];
  const gapColor = GAP_COLOR[theme];
  const print = width != null;

  const chart = (
    <LineChart
      data={data}
      margin={CHART_MARGIN}
      width={width}
      height={print ? height : undefined}
    >
      <CartesianGrid
        stroke={chrome.grid}
        strokeDasharray="0"
        vertical={false}
      />
      <XAxis
        dataKey="i"
        type="number"
        domain={[start, end]}
        allowDataOverflow
        tickFormatter={(i) => (rows[i] ? formatClock(rows[i].ms) : "")}
        // minTickGap={40}
        minTickGap={50}
        stroke={chrome.axis}
        // tick={{ fill: chrome.tick, fontSize: 10 }}
        tick={{
          fill: chrome.tick,
          fontSize: print ? 13 : 10,
          fontWeight: print ? 600 : 400,
        }}
      />
      <YAxis
        width={Y_AXIS_WIDTH}
        domain={["auto", "auto"]}
        stroke={chrome.axis}
        // tick={{ fill: chrome.tick, fontSize: 10 }}
        tick={{
          fill: chrome.tick,
          fontSize: print ? 13 : 10,
          fontWeight: print ? 600 : 400,
        }}
        tickFormatter={formatTick}
      />
      {!print && (
        <Tooltip
          content={<CapacityTooltip lines={lines} unit={unit} />}
          cursor={{ stroke: chrome.cursor, strokeWidth: 1 }}
          isAnimationActive={false}
        />
      )}
      {false &&
        gaps.map((gap) => (
          <ReferenceLine
            key={gap.at}
            x={gap.at}
            stroke={gapColor}
            strokeDasharray="4 3"
            strokeWidth={1.5}
            label={{
              value: `no data · ${formatDuration(gap.ms)}`,
              position: "insideTop",
              fill: gapColor,
              fontSize: 11,
              fontWeight: 600,
            }}
          />
        ))}
      {lines.map((line) => (
        <Line
          key={line.key}
          type={line.type}
          dataKey={line.key}
          name={line.name}
          stroke={line.color}
          strokeWidth={1.6}
          dot={makeDotRenderer(line.key, line.color, data)}
          activeDot={print ? false : { r: 2.6, strokeWidth: 0 }}
          isAnimationActive={false}
          connectNulls={false}
        />
      ))}
    </LineChart>
  );

  if (print) return chart;
  return (
    <ResponsiveContainer width="100%" height={height}>
      {chart}
    </ResponsiveContainer>
  );
}

/** Pane title + unit + toggleable legend, shared by screen and print. */
function PaneHeader({ pane, lines, hidden, onToggle }) {
  return (
    <header className="capacity-dash__pane-header">
      <div className="capacity-dash__pane-titles">
        <h2 className="capacity-dash__pane-title">{pane.title}</h2>
        <span className="capacity-dash__pane-unit">{pane.unit}</span>
      </div>
      <ul className="capacity-dash__legend">
        {lines.map((line) => {
          const off = hidden ? Boolean(hidden[line.key]) : false;
          const swatch = (
            <span
              className="capacity-dash__swatch"
              style={{ backgroundColor: line.color }}
              aria-hidden="true"
            />
          );
          return (
            <li key={line.key}>
              {onToggle ? (
                <button
                  type="button"
                  className={`capacity-dash__legend-item${
                    off ? " capacity-dash__legend-item--off" : ""
                  }`}
                  onClick={() => onToggle(line.key)}
                  aria-pressed={!off}
                >
                  {swatch}
                  {line.name}
                </button>
              ) : (
                <span className="capacity-dash__legend-item">
                  {swatch}
                  {line.name}
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </header>
  );
}

// ── one chart pane ─────────────────────────────────────────────
// Each pane owns its zoom window. `masterRange` is the shared window driven by
// the Full range strip and its buttons; the pane adopts it whenever it changes,
// but zooming THIS pane (wheel, drag, box, its own buttons) only touches local
// state — so the graphs zoom independently, and Full range still moves them all.
function ChartPane({
  pane,
  rows,
  masterRange,
  gaps,
  lastIndex,
  hidden,
  onToggle,
  loading,
  theme,
}) {
  const [range, setRange] = useState(masterRange);

  // Adopt the shared window when it changes (Full range brush / global buttons /
  // data reload). Local-only zoom leaves masterRange untouched, so this stays
  // quiet and the pane keeps its own window.
  useEffect(() => {
    setRange(masterRange);
  }, [masterRange]);

  const { containerRef, selection, handlers } = useZoomPan({
    range,
    setRange,
    lastIndex,
  });

  const [start, end] = range;
  // Slicing is what makes the Y axis rescale to the window instead of the whole run.
  const visible = useMemo(() => rows.slice(start, end + 1), [rows, start, end]);
  const visibleGaps = useMemo(
    () => gaps.filter((gap) => gap.at > start && gap.at < end),
    [gaps, start, end],
  );

  const painted = useMemo(
    () => paintLines(pane.lines, theme),
    [pane.lines, theme],
  );
  const shown = painted.filter((line) => !hidden[line.key]);

  const windowed = start > 0 || end < lastIndex;
  const from = rows[start] && rows[start].ms;
  const to = rows[end] && rows[end].ms;

  return (
    <section className="capacity-dash__pane">
      <PaneHeader
        pane={pane}
        lines={painted}
        hidden={hidden}
        onToggle={onToggle}
      />

      <div className="capacity-dash__pane-tools">
        <span className="capacity-dash__pane-window">
          {windowed
            ? `${formatClock(from)}–${formatClock(to)} · ${end - start + 1} pts`
            : "full range"}
        </span>
        <div className="capacity-dash__pane-btns">
          <button
            className="capacity-dash__btn capacity-dash__btn--sm"
            type="button"
            onClick={() => setRange(zoomAround(range, ZOOM_IN, lastIndex))}
            title="Zoom in"
            aria-label={`Zoom in on ${pane.title}`}
          >
            +
          </button>
          <button
            className="capacity-dash__btn capacity-dash__btn--sm"
            type="button"
            onClick={() => setRange(zoomAround(range, ZOOM_OUT, lastIndex))}
            title="Zoom out"
            aria-label={`Zoom out on ${pane.title}`}
          >
            −
          </button>
          <button
            className="capacity-dash__btn capacity-dash__btn--sm"
            type="button"
            onClick={() => setRange(fullRange(lastIndex))}
            disabled={!windowed}
            title="Reset this graph"
          >
            Reset
          </button>
        </div>
      </div>

      <div
        className="capacity-dash__plot"
        ref={containerRef}
        {...handlers}
        role="presentation"
      >
        <ChartBody
          lines={shown}
          data={visible}
          rows={rows}
          gaps={visibleGaps}
          start={start}
          end={end}
          theme={theme}
          unit={pane.unit}
          height={215}
        />

        {selection && (
          <div
            className="capacity-dash__selection"
            style={{
              left: Math.min(selection.x0, selection.x1),
              width: Math.abs(selection.x1 - selection.x0),
            }}
          />
        )}
        {loading && <div className="capacity-dash__pane-veil" />}
      </div>
    </section>
  );
}

// ════════════════════════════════════════════════════════════════
//  Printable report — one chart per page, screen-hidden
// ════════════════════════════════════════════════════════════════
// function PrintReport({ payload, rows, gaps, stats, periodText, theme }) {
//   const summary = (payload && payload.summary) || {};
//   const lastIndex = Math.max(0, rows.length - 1);
//   // the cover is page 1, so the charts start at 2
//   const pageCount = PANES.length + 1;

//   return (
//     <div className="capacity-dash__print" aria-hidden="true">
//       {/* ── cover: what this is, what it covers, and what is in it ── */}
//       <section className="capacity-dash__print-page capacity-dash__print-cover">
//         <header className="capacity-dash__print-head">
//           <span className="capacity-dash__print-brand">Capacity report</span>
//           <span className="capacity-dash__print-meta">
//             {payload ? payload.agent_name : "—"} · {periodText} · IST
//           </span>
//         </header>

//         <h1 className="capacity-dash__print-h1">Guardlynx — capacity report</h1>

//         <p className="capacity-dash__print-lead">
//           This report shows how much of the machine's capacity the monitored host and the
//           Guardlynx agent used over the period above. Every figure is sampled by the agent
//           itself — nothing here is entered by hand. Each chart that follows covers the whole
//           window at once and starts on its own page, and opens with a note on what the metric
//           means and how to read it. All times are India Standard Time (UTC+5:30).
//         </p>

//         <table className="capacity-dash__print-table">
//           <tbody>
//             <tr>
//               {stats.map((stat) => (
//                 <th key={stat.key}>{stat.label}</th>
//               ))}
//               <th>Samples</th>
//             </tr>
//             <tr>
//               {stats.map((stat) => (
//                 <td key={stat.key}>
//                   {summary[stat.key] == null ? "—" : Number(summary[stat.key]).toFixed(2)}{" "}
//                   {stat.unit}
//                 </td>
//               ))}
//               <td>{payload && payload.sample_count != null ? payload.sample_count : "—"}</td>
//             </tr>
//           </tbody>
//         </table>

//         <p className="capacity-dash__print-note">
//           Every figure in that row is the mean across the whole window, so a short spike barely
//           moves it — read the averages with the charts, not instead of them.
//           {gaps.length
//             ? ` The agent stopped reporting ${gaps.length} time${gaps.length > 1 ? "s" : ""} during
//                this window; each break is marked on the charts, and nothing was measured while it
//                lasted.`
//             : " The agent reported without interruption for the whole window."}
//         </p>

//         <div className="capacity-dash__print-toc">
//           <div className="capacity-dash__print-toc-head">Contents</div>
//           <ol className="capacity-dash__print-toc-list">
//             {PANES.map((pane) => (
//               <li key={pane.id}>
//                 {pane.title} <span>({pane.unit})</span>
//               </li>
//             ))}
//           </ol>
//         </div>

//         <footer className="capacity-dash__print-foot">Page 1 of {pageCount}</footer>
//       </section>

//       {PANES.map((pane, index) => {
//         const lines = paintLines(pane.lines, theme);
//         return (
//           <section className="capacity-dash__print-page" key={pane.id}>
//             <header className="capacity-dash__print-head">
//               <span className="capacity-dash__print-brand">Capacity report</span>
//               <span className="capacity-dash__print-meta">
//                 {payload ? payload.agent_name : "—"} · {periodText} · IST
//               </span>
//             </header>

//             <h2 className="capacity-dash__print-h2">
//               {index + 1}. {pane.title} <span>({pane.unit})</span>
//             </h2>

//             {pane.lead && <p className="capacity-dash__print-lead">{pane.lead}</p>}

//             <PaneHeader pane={pane} lines={lines} />
//             <ChartBody
//               lines={lines}
//               data={rows}
//               rows={rows}
//               gaps={gaps}
//               start={0}
//               end={lastIndex}
//               theme={theme}
//               unit={pane.unit}
//               width={PRINT_W}
//               height={PRINT_H}
//             />

//             <p className="capacity-dash__print-note">
//               Full range, {rows.length} samples.
//               {gaps.length
//                 ? ` ${gaps.length} reporting gap${gaps.length > 1 ? "s" : ""} marked on the chart —
//                    the agent was not reporting, so nothing was measured there.`
//                 : " No reporting gaps."}
//             </p>
//             <footer className="capacity-dash__print-foot">
//               Page {index + 2} of {pageCount}
//             </footer>
//           </section>
//         );
//       })}
//     </div>
//   );
// }

function PrintReport({ payload, rows, gaps, stats, periodText, theme }) {
  const summary = (payload && payload.summary) || {};
  const lastIndex = Math.max(0, rows.length - 1);

  // Cover = page 1, each metric gets its own page after that.
  const pageCount = PANES.length + 2;

  const sampleCount =
    payload && payload.sample_count != null
      ? payload.sample_count
      : rows.length;

  const generatedAt = new Date().toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

  return (
    <div className="capacity-dash__print" aria-hidden="true">
      {/* =======================================================
          PAGE 01 — COVER / EXECUTIVE OVERVIEW
          ======================================================= */}
      <section className="capacity-dash__print-page capacity-dash__print-cover">
        <div className="capacity-dash__print-cover-top">
          <div>
            <div className="capacity-dash__print-logo">GUARDLYNX</div>

            <div className="capacity-dash__print-logo-sub">
              Security &amp; Compliance Monitoring
            </div>
          </div>

          <div className="capacity-dash__print-confidential">CONFIDENTIAL</div>
        </div>

        <div className="capacity-dash__print-cover-rule" />

        <div className="capacity-dash__print-cover-content">
          <div className="capacity-dash__print-eyebrow">
            CAPACITY MONITORING
          </div>

          <h1 className="capacity-dash__print-cover-title">
            Capacity &amp; Resource
            <br />
            Utilization Report
          </h1>

          <p className="capacity-dash__print-cover-lead">
            A consolidated view of host and Guardlynx agent resource utilization
            across the selected reporting window.
          </p>

          <div className="capacity-dash__print-meta-grid">
            <div className="capacity-dash__print-meta-card">
              <div className="capacity-dash__print-meta-label">Agent</div>

              <div className="capacity-dash__print-meta-value">
                {payload ? payload.agent_name : "—"}
              </div>
            </div>

            <div className="capacity-dash__print-meta-card">
              <div className="capacity-dash__print-meta-label">
                Reporting Period
              </div>

              <div className="capacity-dash__print-meta-value capacity-dash__print-meta-value--small">
                {periodText}
              </div>
            </div>

            <div className="capacity-dash__print-meta-card">
              <div className="capacity-dash__print-meta-label">Generated</div>

              <div className="capacity-dash__print-meta-value capacity-dash__print-meta-value--small">
                {generatedAt} IST
              </div>
            </div>

            <div className="capacity-dash__print-meta-card">
              <div className="capacity-dash__print-meta-label">Samples</div>

              <div className="capacity-dash__print-meta-value">
                {sampleCount}
              </div>
            </div>
          </div>

          {/* <div className="capacity-dash__print-section-label">
            KEY METRICS
          </div> */}

          {/* <div className="capacity-dash__print-kpis">

            {stats.map((stat) => (
              <div
                className="capacity-dash__print-kpi"
                key={stat.key}
              >
                <div className="capacity-dash__print-kpi-label">
                  {stat.label}
                </div>

                <div className="capacity-dash__print-kpi-value">
                  {summary[stat.key] == null
                    ? "—"
                    : Number(summary[stat.key]).toFixed(2)}

                  {summary[stat.key] != null && (
                    <span className="capacity-dash__print-kpi-unit">
                      {stat.unit}
                    </span>
                  )}
                </div>
              </div>
            ))}

          </div> */}

          {/* <div className="capacity-dash__print-summary-note">
            <strong>How to read this report.</strong>{" "}
            These values are averages across the complete reporting
            window. Short-lived spikes may have little effect on an
            average, so the summary metrics should always be reviewed
            together with the evidence charts that follow.

            {gaps.length
              ? ` The agent stopped reporting ${gaps.length} ${
                  gaps.length === 1 ? "time" : "times"
                } during this window. Reporting gaps are marked in the charts.`
              : " The agent reported without interruption throughout the selected window."}
          </div> */}

          {/* <div className="capacity-dash__print-section-label">
            REPORT CONTENTS
          </div>


          <div className="capacity-dash__print-contents">

            {PANES.map((pane, index) => (
              <div
                className="capacity-dash__print-content-row"
                key={pane.id}
              >
                <span className="capacity-dash__print-content-number">
                  {String(index + 2).padStart(2, "0")}
                </span>

                <span className="capacity-dash__print-content-title">
                  {pane.title}
                </span>

                <span className="capacity-dash__print-content-unit">
                  {pane.unit}
                </span>
              </div>
            ))}

          </div> */}

          <div className="capacity-dash__print-section-label">
            REPORT CONTENTS
          </div>

          <div className="capacity-dash__print-contents">
            <div className="capacity-dash__print-content-row">
              <span className="capacity-dash__print-content-number">02</span>

              <span className="capacity-dash__print-content-title">
                Executive Summary
              </span>

              <span className="capacity-dash__print-content-unit">
                Overview
              </span>
            </div>

            {PANES.map((pane, index) => (
              <div className="capacity-dash__print-content-row" key={pane.id}>
                <span className="capacity-dash__print-content-number">
                  {String(index + 3).padStart(2, "0")}
                </span>

                <span className="capacity-dash__print-content-title">
                  {pane.title}
                </span>

                <span className="capacity-dash__print-content-unit">
                  {pane.unit}
                </span>
              </div>
            ))}
          </div>
        </div>

        <div className="capacity-dash__print-cover-bottom">
          <span>Generated from Guardlynx agent telemetry.</span>

          <span>Capacity Monitoring Report · Page 1 of {pageCount}</span>
        </div>
      </section>

      {/* =======================================================
    PAGE 02 — EXECUTIVE SUMMARY
    ======================================================= */}

      <section className="capacity-dash__print-page capacity-dash__print-summary-page">
        <div className="capacity-dash__print-page-number">02</div>

        <div className="capacity-dash__print-page-heading">
          <div className="capacity-dash__print-eyebrow">CAPACITY OVERVIEW</div>

          <h2 className="capacity-dash__print-summary-title">
            Executive Summary
          </h2>

          <div className="capacity-dash__print-summary-subtitle">
            Consolidated resource utilization overview for the selected
            monitoring window.
          </div>
        </div>

        {/* REPORT INFORMATION */}

        <div className="capacity-dash__print-section-label">
          REPORT INFORMATION
        </div>

        <div className="capacity-dash__print-summary-meta">
          <div className="capacity-dash__print-summary-meta-card">
            <span>Monitoring Agent</span>
            <strong>{payload ? payload.agent_name : "—"}</strong>
          </div>

          <div className="capacity-dash__print-summary-meta-card">
            <span>Reporting Period</span>
            <strong>{periodText}</strong>
          </div>

          <div className="capacity-dash__print-summary-meta-card">
            <span>Total Samples</span>
            <strong>{sampleCount}</strong>
          </div>
        </div>

        {/* AVERAGE UTILIZATION */}

        <div className="capacity-dash__print-section-label">
          AVERAGE UTILIZATION
        </div>

        <div className="capacity-dash__print-kpis">
          {stats.map((stat) => (
            <div className="capacity-dash__print-kpi" key={stat.key}>
              <div className="capacity-dash__print-kpi-label">{stat.label}</div>

              <div className="capacity-dash__print-kpi-value">
                {summary[stat.key] == null
                  ? "—"
                  : Number(summary[stat.key]).toFixed(2)}

                {summary[stat.key] != null && (
                  <span className="capacity-dash__print-kpi-unit">
                    {stat.unit}
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>

        {/* MONITORING COVERAGE */}

        <div className="capacity-dash__print-section-label">
          MONITORING COVERAGE
        </div>

        <div className="capacity-dash__print-coverage">
          {PANES.map((pane) => (
            <div className="capacity-dash__print-coverage-row" key={pane.id}>
              <span className="capacity-dash__print-coverage-name">
                {pane.title}
              </span>

              <span className="capacity-dash__print-coverage-unit">
                {pane.unit}
              </span>

              <span className="capacity-dash__print-coverage-status">
                Available
              </span>
            </div>
          ))}
        </div>

        {/* INTERPRETATION NOTE */}

        <div className="capacity-dash__print-summary-note">
          <strong>How to read this summary.</strong> The values above are
          averages across the complete reporting window. Short-lived spikes may
          have limited effect on an average, so the summary should be reviewed
          together with the detailed evidence charts on the following pages.
          {gaps.length
            ? ` The agent stopped reporting ${gaps.length} ${
                gaps.length === 1 ? "time" : "times"
              } during this window. Reporting gaps are identified on the
        corresponding evidence pages.`
            : " The agent reported without interruption throughout the selected window."}
        </div>

        <div className="capacity-dash__print-page-footer">
          <span>GUARDLYNX · CAPACITY MONITORING</span>

          <span>CONFIDENTIAL · Page 2 of {pageCount}</span>
        </div>
      </section>

      {/* =======================================================
          EVIDENCE PAGES
          ======================================================= */}
      {PANES.map((pane, index) => {
        const lines = paintLines(pane.lines, theme);

        return (
          <section
            className="capacity-dash__print-page capacity-dash__print-evidence-page"
            key={pane.id}
          >
            <div className="capacity-dash__print-page-number">
              {String(index + 3).padStart(2, "0")}
            </div>

            <div className="capacity-dash__print-page-heading">
              <div className="capacity-dash__print-eyebrow">
                CAPACITY EVIDENCE
              </div>

              <h2 className="capacity-dash__print-evidence-title">
                {pane.title}
              </h2>

              <div className="capacity-dash__print-evidence-unit">
                Measurement unit: {pane.unit}
              </div>
            </div>

            {pane.lead && (
              <p className="capacity-dash__print-evidence-lead">{pane.lead}</p>
            )}

            <div className="capacity-dash__print-section-label">EVIDENCE</div>

            <div className="capacity-dash__print-chart-card">
              <PaneHeader pane={pane} lines={lines} />

              <ChartBody
                lines={lines}
                data={rows}
                rows={rows}
                gaps={gaps}
                start={0}
                end={lastIndex}
                theme={theme}
                unit={pane.unit}
                width={PRINT_W}
                height={PRINT_H}
              />
            </div>

            <div className="capacity-dash__print-evidence-note">
              <div>
                <span className="capacity-dash__print-evidence-note-label">
                  Window
                </span>
                Full reporting range
              </div>

              <div>
                <span className="capacity-dash__print-evidence-note-label">
                  Samples
                </span>

                {rows.length}
              </div>

              <div>
                <span className="capacity-dash__print-evidence-note-label">
                  Reporting gaps
                </span>

                {gaps.length}
              </div>
            </div>

            {gaps.length > 0 && (
              <p className="capacity-dash__print-gap-note">
                {gaps.length} reporting{" "}
                {gaps.length === 1 ? "gap was" : "gaps were"} detected. During
                these intervals the agent was not reporting, so no measurements
                were available.
              </p>
            )}

            <div className="capacity-dash__print-page-footer">
              <span>GUARDLYNX · CAPACITY MONITORING</span>

              <span>
                CONFIDENTIAL · Page {index + 3} of {pageCount}
              </span>
            </div>
          </section>
        );
      })}
    </div>
  );
}

// ── page ───────────────────────────────────────────────────────
export default function CapacityDashboard() {
  const initial = defaultLocalRange();

  // `?agent=<name>` scopes the report on arrival — that is how the dashboard's
  // agent table hands an agent over. `agent_name` is accepted too, for URLs
  // written by hand against the API's own parameter name.
  const [searchParams, setSearchParams] = useSearchParams();
  const initialAgent = useMemo(
    () =>
      (
        searchParams.get("agent") ||
        searchParams.get("agent_name") ||
        ""
      ).trim(),
    // read once, on arrival: later edits come from the controls, not the URL
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const [agentName, setAgentName] = useState(
    initialAgent || "UpdatedWindowAgent",
  );
  const [fromLocal, setFromLocal] = useState(initial.from);
  const [toLocal, setToLocal] = useState(initial.to);
  // the selected "Range" preset in hours ("12"), or "custom" once From/To is edited
  const [preset, setPreset] = useState(String(DEFAULT_WINDOW_HOURS));
  // graphs per row: 1 (stacked) or 2 (side by side)
  const [perRow, setPerRow] = useState(2);

  const [payload, setPayload] = useState(null);
  const [status, setStatus] = useState("loading"); // loading | success | error
  const [error, setError] = useState(null);
  const [hidden, setHidden] = useState({});
  // the shared window the Full range strip drives; each pane copies it, then may
  // zoom on its own from there
  const [masterRange, setMasterRange] = useState([0, 0]);

  const [theme, setTheme] = useState(initialTheme);
  const [printing, setPrinting] = useState(false);

  const [sendModalOpen, setSendModalOpen] = useState(false);

  const [sendSubject, setSendSubject] = useState("Capacity Monitoring Report");
  const [sendBody, setSendBody] = useState("");

  // Recipient communication channels
  const [channels, setChannels] = useState([]);

  // Configured sender accounts
  const [channelAccounts, setChannelAccounts] = useState([]);

  // Loading sender accounts + recipients
  const [channelsLoading, setChannelsLoading] = useState(false);

  // Selected sender account
  const [selectedAccountId, setSelectedAccountId] = useState(null);

  // Selected recipients
  const [selectedChannelIds, setSelectedChannelIds] = useState([]);

  const [sendingPdf, setSendingPdf] = useState(false);
  const [sendError, setSendError] = useState("");
  const [sendSuccess, setSendSuccess] = useState("");

  const [sendReportReady, setSendReportReady] = useState(false);

  const abortRef = useRef(null);
  const requestRef = useRef(0);
  const themeBeforePrint = useRef(theme);

  const sendReportRef = useRef(null);

  const rows = useMemo(() => buildRows(payload), [payload]);
  const gaps = useMemo(() => findGaps(rows), [rows]);
  const lastIndex = Math.max(0, rows.length - 1);

  // The inputs are IST wall clock; the API wants naive UTC — istInputToApi bridges
  // it and adds the seconds (00 for the window start, 59 for the end).
  const toParams = useCallback(
    () => ({
      agentName: agentName.trim(),
      fromDt: istInputToApi(fromLocal, "00"),
      toDt: istInputToApi(toLocal, "59"),
    }),
    [agentName, fromLocal, toLocal],
  );

  const load = useCallback(async (params) => {
    if (!params.agentName) {
      setError({ message: "Enter an agent name to load.", status: 0, url: "" });
      setStatus("error");
      return;
    }
    if (params.fromDt >= params.toDt) {
      setError({
        message: "From must be earlier than To.",
        status: 0,
        url: "",
      });
      setStatus("error");
      return;
    }

    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const ticket = ++requestRef.current;

    setStatus("loading");
    setError(null);

    try {
      const data = await fetchOverview(params, { signal: controller.signal });
      if (ticket !== requestRef.current) return; // a newer load won
      setPayload(data);
      setStatus("success");
    } catch (err) {
      if (err && (err.code === "ERR_CANCELED" || err.name === "CanceledError"))
        return;
      if (ticket !== requestRef.current) return;
      // The last good payload stays in state, so the charts stay on screen.
      setError({
        message: err.message,
        status: err.status || 0,
        url: err.url || absoluteOverviewUrl(params),
      });
      setStatus("error");
    }
  }, []);

  // Reset the shared window whenever the underlying run changes size — every
  // pane adopts it, so a fresh load starts them all at full range.
  useEffect(() => {
    setMasterRange(fullRange(rows.length - 1));
  }, [rows.length]);

  useEffect(() => {
    load(toParams());
    return () => {
      if (abortRef.current) abortRef.current.abort();
    };
    // Load once on mount; every later load is driven by the Load button.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // persist the theme choice
  useEffect(() => {
    try {
      window.localStorage.setItem(THEME_KEY, theme);
    } catch (_) {
      /* storage blocked — the choice just will not survive a reload */
    }
  }, [theme]);

  /**
   * Export to PDF via the browser's own print-to-PDF, so there is no PDF
   * dependency to ship. The print tree only mounts while `printing` is true —
   * mounting it always would render four extra 283-point charts on every load.
   * The dialog is opened one frame after the commit so the charts are in the DOM
   * and laid out before the browser snapshots the page.
   */
  useEffect(() => {
    if (!printing) return undefined;

    let raf2 = 0;
    const raf1 = window.requestAnimationFrame(() => {
      raf2 = window.requestAnimationFrame(() => {
        try {
          window.print();
        } finally {
          setPrinting(false);
          setTheme(themeBeforePrint.current);
        }
      });
    });
    return () => {
      window.cancelAnimationFrame(raf1);
      if (raf2) window.cancelAnimationFrame(raf2);
    };
  }, [printing]);

  const exportPdf = () => {
    if (!rows.length || printing) return;
    // Paper is white: print the light palette whatever the screen is showing,
    // then put the user's theme back.
    themeBeforePrint.current = theme;
    setTheme("light");
    setPrinting(true);
  };

  const openSendModal = async () => {
    if (!rows.length || sendingPdf) return;

    setSendModalOpen(true);

    setSendError("");
    setSendSuccess("");

    setChannelsLoading(true);

    try {
      const [channelList, accountList] = await Promise.all([
        fetchChannels(),
        fetchChannelAccounts(),
      ]);

      setChannels(channelList || []);

      // Only active and verified sender accounts should be selectable.
      setChannelAccounts(
        (accountList || []).filter(
          (account) =>
            account.is_active !== false && account.is_verified !== false,
        ),
      );

      // Reset previous selections every time modal opens.
      setSelectedAccountId(null);
      setSelectedChannelIds([]);
      setSendSubject("Capacity Monitoring Report");

      setSendBody(
        `Capacity Monitoring Report for ${
          payload?.agent_name || agentName
        }. Reporting period: ${`${fromLocal.replace("T", " ")} – ${toLocal.replace("T", " ")}`}.`,
      );
    } catch (err) {
      setSendError(
        err?.message ||
          "Unable to load sender accounts or communication channels.",
      );
    } finally {
      setChannelsLoading(false);
    }
  };

  /**
   * Close the send modal.
   *
   * A PDF send cannot be cancelled halfway through because the backend may
   * already be delivering to some channels.
   */
  const closeSendModal = () => {
    if (sendingPdf) return;

    setSendModalOpen(false);
    setSendError("");
    setSendSuccess("");
  };

  /**
   * Select or unselect one specific communication-channel row.
   */
  const toggleChannelSelection = (channelId) => {
    setSelectedChannelIds((current) =>
      current.includes(channelId)
        ? current.filter((id) => id !== channelId)
        : [...current, channelId],
    );
  };

  const handleAccountSelection = (accountId) => {
    const nextAccountId = Number(accountId);

    setSelectedAccountId(nextAccountId);

    // Sender change hone par old recipients clear kar do,
    // because sender type may be different.
    setSelectedChannelIds([]);

    setSendError("");
    setSendSuccess("");
  };

  const selectedSenderAccount = channelAccounts.find(
    (account) => account.id === Number(selectedAccountId),
  );

  const compatibleChannels = useMemo(() => {
    if (!selectedSenderAccount) {
      return [];
    }

    const senderType = String(
      selectedSenderAccount.channel_type || "",
    ).toLowerCase();

    return channels.filter((channel) => {
      const recipientType = String(channel.type || "").toLowerCase();

      // Gmail sender -> email recipient
      if (senderType === "gmail") {
        return ["email", "gmail"].includes(recipientType);
      }

      // Outlook sender -> outlook recipient
      if (senderType === "outlook365") {
        return ["outlook"].includes(recipientType);
      }

      // Telegram sender -> telegram recipient
      if (senderType === "telegram") {
        return recipientType === "telegram";
      }

      // WhatsApp sender -> WhatsApp recipient
      if (senderType === "whatsapp") {
        return recipientType === "whatsapp";
      }

      // SMS sender -> SMS recipient
      if (senderType === "sms") {
        return recipientType === "sms";
      }

      // Jira sender
      if (senderType === "jira") {
        return recipientType === "jira";
      }

      return false;
    });
  }, [channels, selectedSenderAccount]);

  /**
   * Wait for the hidden PrintReport to render its charts before capturing it.
   */
  const waitForReportRender = () =>
    new Promise((resolve) => {
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(() => {
          // Recharts can finish SVG layout one tick after React commits.
          window.setTimeout(resolve, 250);
        });
      });
    });

  /**
   * Capture the existing PrintReport and build a multi-page A4 PDF.
   *
   * IMPORTANT:
   * - Existing Export PDF / window.print() is NOT used here.
   * - The same PrintReport component is reused.
   * - Each `.capacity-dash__print-page` becomes one PDF page.
   */
  const buildCapacityPdfBlob = async () => {
    setSendReportReady(true);

    await waitForReportRender();

    const reportElement = sendReportRef.current;

    if (!reportElement) {
      throw new Error("The report could not be prepared for sending.");
    }

    const pages = Array.from(
      reportElement.querySelectorAll(".capacity-dash__print-page"),
    );

    if (!pages.length) {
      throw new Error("No report pages were available to create the PDF.");
    }

    const pdf = new jsPDF({
      orientation: "portrait",
      unit: "mm",
      format: "a4",
      compress: true,
    });

    for (let index = 0; index < pages.length; index += 1) {
      const page = pages[index];

      const canvas = await html2canvas(page, {
        scale: 2,
        useCORS: true,
        backgroundColor: "#ffffff",
        logging: false,

        windowWidth: page.scrollWidth || page.offsetWidth,
        windowHeight: page.scrollHeight || page.offsetHeight,

        onclone: (clonedDocument) => {
          let printCss = "";

          Array.from(document.styleSheets).forEach((styleSheet) => {
            try {
              Array.from(styleSheet.cssRules).forEach((rule) => {
                if (
                  rule instanceof CSSMediaRule &&
                  rule.media &&
                  rule.media.mediaText.includes("print")
                ) {
                  Array.from(rule.cssRules).forEach((printRule) => {
                    printCss += `${printRule.cssText}\n`;
                  });
                }
              });
            } catch (error) {
              // Ignore inaccessible stylesheets
            }
          });

          const printStyle = clonedDocument.createElement("style");

          printStyle.setAttribute(
            "data-capacity-send-pdf-print-styles",
            "true",
          );

          printStyle.textContent = printCss;

          clonedDocument.head.appendChild(printStyle);

          /*
           * Override PRINT SAFETY rule.
           */
          const overrideStyle = clonedDocument.createElement("style");

          overrideStyle.setAttribute(
            "data-capacity-send-pdf-overrides",
            "true",
          );

          overrideStyle.textContent = `
      .capacity-dash__send-overlay {
        display: none !important;
      }

      .capacity-dash__send-report-capture {
        display: block !important;
        visibility: visible !important;
        position: fixed !important;
        top: 0 !important;
        left: 0 !important;
        width: 210mm !important;
        height: auto !important;
        opacity: 1 !important;
        overflow: visible !important;
        z-index: auto !important;
        background: #ffffff !important;
      }

      .capacity-dash__send-report-capture .capacity-dash__print {
        display: block !important;
        visibility: visible !important;
        position: relative !important;
        width: 210mm !important;
        height: auto !important;
      }

      .capacity-dash__send-report-capture .capacity-dash__print-page {
        display: block !important;
        visibility: visible !important;
        position: relative !important;
        width: 210mm !important;
        min-height: 260mm !important;
        height: auto !important;
        overflow: visible !important;
      }
    `;

          clonedDocument.head.appendChild(overrideStyle);
        },
      });

      /*
       * Safety check:
       * html2canvas ne valid canvas generate kiya hai ya nahi.
       */
      if (
        !canvas.width ||
        !canvas.height ||
        !Number.isFinite(canvas.width) ||
        !Number.isFinite(canvas.height)
      ) {
        throw new Error(
          `Could not generate page ${index + 1}. ` +
            `Canvas size: ${canvas.width} × ${canvas.height}`,
        );
      }

      const imageData = canvas.toDataURL("image/jpeg", 0.92);

      const pdfWidth = 210;
      const pdfHeight = 297;

      const canvasWidth = canvas.width;
      const canvasHeight = canvas.height;

      const imageRatio = canvasWidth / canvasHeight;
      const pageRatio = pdfWidth / pdfHeight;

      let renderWidth;
      let renderHeight;

      if (imageRatio > pageRatio) {
        renderWidth = pdfWidth;
        renderHeight = pdfWidth / imageRatio;
      } else {
        renderHeight = pdfHeight;
        renderWidth = pdfHeight * imageRatio;
      }

      const x = (pdfWidth - renderWidth) / 2;
      const y = (pdfHeight - renderHeight) / 2;

      /*
       * Final safety check before jsPDF.addImage()
       */
      if (
        !Number.isFinite(x) ||
        !Number.isFinite(y) ||
        !Number.isFinite(renderWidth) ||
        !Number.isFinite(renderHeight) ||
        renderWidth <= 0 ||
        renderHeight <= 0
      ) {
        throw new Error(
          `Invalid PDF coordinates on page ${index + 1}: ` +
            `x=${x}, y=${y}, width=${renderWidth}, height=${renderHeight}`,
        );
      }

      if (index > 0) {
        pdf.addPage();
      }

      pdf.addImage(
        imageData,
        "JPEG",
        x,
        y,
        renderWidth,
        renderHeight,
        undefined,
        "FAST",
      );
    }

    return pdf.output("blob");
  };

  const sendCapacityPdf = async () => {
    if (!rows.length) {
      setSendError("Load Capacity Dashboard data before sending a report.");
      return;
    }

    if (!sendSubject.trim()) {
      setSendError("Enter a subject.");
      return;
    }

    if (!sendBody.trim()) {
      setSendError("Enter a message body.");
      return;
    }

    if (!selectedAccountId) {
      setSendError("Select a sender account.");
      return;
    }

    if (!selectedChannelIds.length) {
      setSendError("Select at least one communication channel.");
      return;
    }

    if (!selectedSenderAccount) {
      setSendError("The selected sender account is no longer available.");
      return;
    }

    setSendingPdf(true);
    setSendError("");
    setSendSuccess("");

    try {
      /*
       * Existing PDF generation remains exactly the same.
       */
      const pdfBlob = await buildCapacityPdfBlob();

      const safeAgentName = (
        payload?.agent_name ||
        agentName ||
        "capacity-report"
      )
        .trim()
        .replace(/[^\w.-]+/g, "_");

      const filename = `capacity_report_${safeAgentName}_${Date.now()}.pdf`;

      /*
       * Convert generated PDF Blob into File.
       */
      const pdfFile = new File([pdfBlob], filename, {
        type: "application/pdf",
      });

      // const subject = "Capacity Monitoring Report";

      // const body = `Capacity Monitoring Report for ${
      //   payload?.agent_name || agentName
      // }. Reporting period: ${periodText}.`;

      /*
       * Backend send-file API currently sends to ONE recipient
       * per request.
       *
       * Therefore loop through selected communication channels.
       */
      const results = [];

      for (const channelId of selectedChannelIds) {
        try {
          const result = await sendFileViaChannelAccount(selectedAccountId, {
            subject: sendSubject.trim(),
            body: sendBody.trim(),
            communication_channel_id: channelId,
            file: pdfFile,
          });

          results.push({
            channelId,
            success: true,
            result,
          });
        } catch (err) {
          results.push({
            channelId,
            success: false,
            error: err?.message || "Unable to send report.",
          });
        }
      }

      const successful = results.filter((item) => item.success);

      const failed = results.filter((item) => !item.success);

      if (failed.length === 0) {
        setSendSuccess(
          `${successful.length} of ${selectedChannelIds.length} report${
            selectedChannelIds.length === 1 ? "" : "s"
          } sent successfully.`,
        );
      } else if (successful.length > 0) {
        setSendSuccess(
          `${successful.length} of ${
            selectedChannelIds.length
          } reports sent successfully.`,
        );

        setSendError(
          `${failed.length} recipient${
            failed.length === 1 ? "" : "s"
          } could not receive the report.`,
        );
      } else {
        const firstError = failed[0]?.error || "Unable to send the report.";

        setSendError(firstError);
      }
    } catch (err) {
      setSendError(
        err?.message ||
          "Unable to generate or send the Capacity Monitoring Report.",
      );
    } finally {
      setSendingPdf(false);

      /*
       * Remove hidden PrintReport after capture.
       */
      setSendReportReady(false);
    }
  };

  /** Mirror the scope into the URL, so a refresh or a shared link keeps it. */
  const syncUrl = useCallback(
    (name) => {
      const next = new URLSearchParams(searchParams);
      next.delete("agent_name"); // normalise the alias away
      if (name) next.set("agent", name);
      else next.delete("agent");
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const onSubmit = (event) => {
    event.preventDefault();
    const params = toParams();
    syncUrl(params.agentName);
    load(params);
  };

  // Seed From/To to the last `hours` (IST) and load immediately — the dropdown.
  const applyPreset = (hours) => {
    const next = lastHoursInputs(hours);
    setFromLocal(next.from);
    setToLocal(next.to);
    setPreset(String(hours));
    syncUrl(agentName.trim());
    load({
      agentName: agentName.trim(),
      fromDt: istInputToApi(next.from, "00"),
      toDt: istInputToApi(next.to, "59"),
    });
  };

  // Editing a field by hand means the window no longer matches a preset.
  const onFromChange = (value) => {
    setFromLocal(value);
    setPreset("custom");
  };
  const onToChange = (value) => {
    setToLocal(value);
    setPreset("custom");
  };

  const widenTo24h = () => applyPreset(WIDEN_WINDOW_HOURS);

  const toggle = (key) => setHidden((h) => ({ ...h, [key]: !h[key] }));

  // These drive the SHARED window from the Full range strip, so they move every
  // graph together. Per-graph zoom lives inside each ChartPane.
  const zoomAll = (factor) =>
    setMasterRange((r) => zoomAround(r, factor, lastIndex));
  const showLastAll = (n) =>
    setMasterRange(clampRange(lastIndex - n, lastIndex, lastIndex));
  const resetAll = () => setMasterRange(fullRange(lastIndex));

  const loading = status === "loading";
  const summary = (payload && payload.summary) || {};
  const isEmpty = status !== "loading" && payload != null && rows.length === 0;

  const periodText = `${fromLocal.replace("T", " ")} – ${toLocal.replace("T", " ")}`;

  return (
    <div className={`capacity-dash capacity-dash--${theme}`}>
      <header className="capacity-dash__topbar">
        <div className="capacity-dash__brand">
          <h1 className="capacity-dash__title">Capacity monitoring</h1>
          <p className="capacity-dash__subtitle">
            {payload
              ? `${payload.agent_name} · ${rows.length} samples · times in IST`
              : "times in IST"}
          </p>
        </div>

        <form className="capacity-dash__controls" onSubmit={onSubmit}>
          <label className="capacity-dash__field">
            <span className="capacity-dash__field-label">Agent name</span>
            <input
              className="capacity-dash__input"
              type="text"
              value={agentName}
              onChange={(e) => setAgentName(e.target.value)}
              placeholder="Please enter an agent name"
              autoComplete="off"
            />
          </label>
          <label className="capacity-dash__field">
            <span className="capacity-dash__field-label">Range</span>
            <select
              className="capacity-dash__select"
              value={preset}
              onChange={(e) => {
                if (e.target.value !== "custom")
                  applyPreset(Number(e.target.value));
              }}
              disabled={loading}
              aria-label="Quick range"
            >
              {PRESET_HOURS.map((h) => (
                <option key={h} value={String(h)}>
                  {presetLabel(h)}
                </option>
              ))}
              {preset === "custom" && <option value="custom">Custom</option>}
            </select>
          </label>
          <label className="capacity-dash__field">
            <span className="capacity-dash__field-label">From</span>
            <input
              className="capacity-dash__input"
              type="datetime-local"
              value={fromLocal}
              max={toLocal}
              onChange={(e) => onFromChange(e.target.value)}
            />
          </label>
          <label className="capacity-dash__field">
            <span className="capacity-dash__field-label">To</span>
            <input
              className="capacity-dash__input"
              type="datetime-local"
              value={toLocal}
              min={fromLocal}
              onChange={(e) => onToChange(e.target.value)}
            />
          </label>
          <button
            className="capacity-dash__btn capacity-dash__btn--primary"
            type="submit"
            disabled={loading}
          >
            {loading ? "Loading…" : "Load"}
          </button>

          <button
            className="capacity-dash__btn"
            type="button"
            onClick={exportPdf}
            disabled={!rows.length || printing}
            title={
              rows.length
                ? "Open the print dialog — choose Save as PDF"
                : "Load data first"
            }
          >
            {printing ? "Preparing…" : "Export PDF"}
          </button>

          <button
            className="capacity-dash__btn capacity-dash__btn--primary"
            type="button"
            onClick={openSendModal}
            disabled={!rows.length || printing || sendingPdf}
            title={
              rows.length
                ? "Send the Capacity Monitoring Report through communication channels"
                : "Load data first"
            }
          >
            {sendingPdf ? "Sending…" : "Send PDF"}
          </button>

          <button
            className="capacity-dash__btn capacity-dash__btn--icon"
            type="button"
            onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
            aria-pressed={theme === "light"}
            title={
              theme === "dark" ? "Switch to light mode" : "Switch to dark mode"
            }
          >
            <ThemeIcon mode={theme} />
            <span className="capacity-dash__sr-only">
              {theme === "dark"
                ? "Switch to light mode"
                : "Switch to dark mode"}
            </span>
          </button>
        </form>
      </header>

      {status === "error" && error && (
        <div className="capacity-dash__error" role="alert">
          <div className="capacity-dash__error-body">
            <p className="capacity-dash__error-msg">
              {error.status ? `HTTP ${error.status} — ` : ""}
              {error.message}
            </p>
            {error.url && (
              <code className="capacity-dash__error-url">{error.url}</code>
            )}
            {payload && (
              <p className="capacity-dash__error-note">
                Showing the last window that loaded.
              </p>
            )}
          </div>
          <button
            className="capacity-dash__btn"
            type="button"
            onClick={() => load(toParams())}
            disabled={loading}
          >
            Retry
          </button>
        </div>
      )}

      <div
        className={`capacity-dash__stats${loading ? " capacity-dash__stats--loading" : ""}`}
      >
        {STATS.map((stat) => {
          const value = summary[stat.key];
          return (
            <div
              className={`capacity-dash__stat capacity-dash__stat--${stat.modifier}`}
              key={stat.key}
            >
              <span className="capacity-dash__stat-label">{stat.label}</span>
              <span className="capacity-dash__stat-value">
                {value == null ? "—" : Number(value).toFixed(2)}
                <span className="capacity-dash__stat-unit">{stat.unit}</span>
              </span>
            </div>
          );
        })}
        <div className="capacity-dash__stat capacity-dash__stat--samples">
          <span className="capacity-dash__stat-label">Samples</span>
          <span className="capacity-dash__stat-value">
            {payload && payload.sample_count != null
              ? payload.sample_count
              : "—"}
            <span className="capacity-dash__stat-unit">pts</span>
          </span>
        </div>
      </div>

      {isEmpty ? (
        <div className="capacity-dash__empty">
          <p className="capacity-dash__empty-title">
            No samples in this window
          </p>
          <p className="capacity-dash__empty-sub">
            The agent reported nothing between {fromLocal.replace("T", " ")} and{" "}
            {toLocal.replace("T", " ")}.
          </p>
          <button
            className="capacity-dash__btn"
            type="button"
            onClick={widenTo24h}
          >
            Widen to last 24h
          </button>
        </div>
      ) : (
        <>
          <div className="capacity-dash__paneshead">
            <p className="capacity-dash__hint">
              Each graph zooms on its own — wheel to zoom, drag to pan,
              shift-drag to box zoom, double-click to reset. Use{" "}
              <strong>Full range</strong> below to zoom every graph at once.
            </p>
            <div
              className="capacity-dash__layout"
              role="group"
              aria-label="Graphs per row"
            >
              <span className="capacity-dash__layout-label">Per row</span>
              <button
                type="button"
                className={`capacity-dash__btn capacity-dash__btn--sm${
                  perRow === 1 ? " capacity-dash__btn--active" : ""
                }`}
                onClick={() => setPerRow(1)}
                aria-pressed={perRow === 1}
                title="One graph per row"
              >
                1
              </button>
              <button
                type="button"
                className={`capacity-dash__btn capacity-dash__btn--sm${
                  perRow === 2 ? " capacity-dash__btn--active" : ""
                }`}
                onClick={() => setPerRow(2)}
                aria-pressed={perRow === 2}
                title="Two graphs per row"
              >
                2
              </button>
            </div>
          </div>

          <div
            className="capacity-dash__panes capacity-dash__panes--cols"
            style={{ "--cap-cols": perRow }}
          >
            {PANES.map((pane) => (
              <ChartPane
                key={pane.id}
                pane={pane}
                rows={rows}
                gaps={gaps}
                masterRange={masterRange}
                lastIndex={lastIndex}
                hidden={hidden}
                onToggle={toggle}
                loading={loading}
                theme={theme}
              />
            ))}
          </div>

          <section className="capacity-dash__overview">
            <header className="capacity-dash__pane-header">
              <div className="capacity-dash__pane-titles">
                <h2 className="capacity-dash__pane-title">Full range</h2>
                <span className="capacity-dash__pane-unit">
                  zooms every graph ·{" "}
                  {formatClock(rows[masterRange[0]] && rows[masterRange[0]].ms)}
                  –
                  {formatClock(rows[masterRange[1]] && rows[masterRange[1]].ms)}{" "}
                  · {masterRange[1] - masterRange[0] + 1} pts
                </span>
              </div>
              <div className="capacity-dash__pane-btns">
                <button
                  className="capacity-dash__btn capacity-dash__btn--sm"
                  type="button"
                  onClick={() => zoomAll(ZOOM_IN)}
                >
                  Zoom in
                </button>
                <button
                  className="capacity-dash__btn capacity-dash__btn--sm"
                  type="button"
                  onClick={() => zoomAll(ZOOM_OUT)}
                >
                  Zoom out
                </button>
                <button
                  className="capacity-dash__btn capacity-dash__btn--sm"
                  type="button"
                  onClick={() => showLastAll(30)}
                >
                  Last 30
                </button>
                <button
                  className="capacity-dash__btn capacity-dash__btn--sm"
                  type="button"
                  onClick={() => showLastAll(100)}
                >
                  Last 100
                </button>
                <button
                  className="capacity-dash__btn capacity-dash__btn--sm"
                  type="button"
                  onClick={resetAll}
                >
                  All
                </button>
              </div>
            </header>
            <ResponsiveContainer width="100%" height={92}>
              <AreaChart
                data={rows}
                margin={{ top: 4, right: 14, bottom: 0, left: 4 }}
              >
                <YAxis hide domain={["auto", "auto"]} />
                <XAxis dataKey="i" type="number" domain={[0, lastIndex]} hide />
                <Area
                  type="monotone"
                  dataKey="cpu"
                  stroke={SERIES_COLORS[theme].cpu}
                  fill={SERIES_COLORS[theme].cpu}
                  fillOpacity={0.14}
                  strokeWidth={1.2}
                  dot={false}
                  isAnimationActive={false}
                  connectNulls={false}
                />
                {rows.length > 1 && (
                  <Brush
                    dataKey="i"
                    height={22}
                    travellerWidth={8}
                    stroke={CHART_CHROME[theme].cursor}
                    fill="transparent"
                    startIndex={masterRange[0]}
                    endIndex={masterRange[1]}
                    onChange={(next) => {
                      if (
                        !next ||
                        next.startIndex == null ||
                        next.endIndex == null
                      )
                        return;
                      if (
                        next.startIndex === masterRange[0] &&
                        next.endIndex === masterRange[1]
                      )
                        return;
                      setMasterRange(
                        clampRange(next.startIndex, next.endIndex, lastIndex),
                      );
                    }}
                    tickFormatter={(i) =>
                      rows[i] ? formatClock(rows[i].ms) : ""
                    }
                  />
                )}
              </AreaChart>
            </ResponsiveContainer>
          </section>
        </>
      )}

      {sendModalOpen && (
        <div
          className="capacity-dash__send-overlay"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              closeSendModal();
            }
          }}
        >
          <section
            className="capacity-dash__send-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="capacity-send-title"
          >
            <header className="capacity-dash__send-header">
              <div>
                <p className="capacity-dash__send-eyebrow">
                  COMMUNICATION CHANNELS
                </p>

                <h2
                  className="capacity-dash__send-title"
                  id="capacity-send-title"
                >
                  Send Capacity Report
                </h2>

                <p className="capacity-dash__send-subtitle">
                  Select the channels that should receive this Capacity
                  Monitoring Report.
                </p>
              </div>

              <button
                className="capacity-dash__send-close"
                type="button"
                onClick={closeSendModal}
                disabled={sendingPdf}
                aria-label="Close send report dialog"
              >
                ×
              </button>
            </header>

            <div className="capacity-dash__send-body">
              {sendError && (
                <div className="capacity-dash__send-error" role="alert">
                  {sendError}
                </div>
              )}

              {sendSuccess && (
                <div className="capacity-dash__send-success" role="status">
                  {sendSuccess}
                </div>
              )}

              {channelsLoading ? (
                <div className="capacity-dash__send-loading">
                  Loading sender accounts and communication channels…
                </div>
              ) : (
                <>
                  <section className="capacity-dash__message-section">
                    <div className="capacity-dash__send-section-heading">
  <div className="capacity-dash__step-number">1</div>

  <div>
    <h3 className="capacity-dash__send-section-title">
      Message
    </h3>

    <p className="capacity-dash__send-section-description">
      Add the subject and message that will accompany your PDF report.
    </p>
  </div>
</div>

                    <label className="capacity-dash__message-field">
                      <span className="capacity-dash__message-label">
                        Subject
                      </span>

                      <input
                        className="capacity-dash__message-input"
                        type="text"
                        value={sendSubject}
                        onChange={(e) => setSendSubject(e.target.value)}
                        placeholder="Enter subject"
                        disabled={sendingPdf}
                      />
                    </label>

                    <label className="capacity-dash__message-field">
                      <span className="capacity-dash__message-label">Body</span>

                      <textarea
                        className="capacity-dash__message-textarea"
                        value={sendBody}
                        onChange={(e) => setSendBody(e.target.value)}
                        placeholder="Enter message"
                        rows={5}
                        disabled={sendingPdf}
                      />
                    </label>
                  </section>
                  {/* ───────────────────────────────────────────────
          SENDER ACCOUNT
      ─────────────────────────────────────────────── */}

                  <section className="capacity-dash__sender-section">
                    <div className="capacity-dash__send-section-heading">
  <div className="capacity-dash__step-number">2</div>

  <div>
    <h3 className="capacity-dash__send-section-title">
      Sender account
    </h3>

    <p className="capacity-dash__send-section-description">
      Choose the verified account you want to send this report from.
    </p>
  </div>
</div>

                    {channelAccounts.length === 0 ? (
                      <div className="capacity-dash__send-empty">
                        No verified sender accounts have been configured yet.
                      </div>
                    ) : (
                      <div className="capacity-dash__sender-list">
                        {channelAccounts.map((account) => (
                          <label
                            key={account.id}
                            className={`capacity-dash__sender-account ${
                              Number(selectedAccountId) === account.id
                                ? "capacity-dash__sender-account--selected"
                                : ""
                            }`}
                          >
                            <input
                              type="radio"
                              name="capacity-sender-account"
                              value={account.id}
                              checked={Number(selectedAccountId) === account.id}
                              onChange={() =>
                                handleAccountSelection(account.id)
                              }
                              disabled={sendingPdf}
                            />

                            <div className="capacity-dash__sender-main">
                              <div className="capacity-dash__sender-name">
                                {account.label}
                              </div>

                              <div className="capacity-dash__sender-identifier">
                                {account.identifier}
                              </div>

                              <div className="capacity-dash__sender-type">
                                {account.channel_type}
                              </div>
                            </div>
                          </label>
                        ))}
                      </div>
                    )}
                  </section>

                  {/* ───────────────────────────────────────────────
          RECIPIENT CHANNELS
      ─────────────────────────────────────────────── */}

                  <section className="capacity-dash__recipient-section">
                    <div className="capacity-dash__send-section-heading">
  <div className="capacity-dash__step-number">3</div>

  <div>
    <h3 className="capacity-dash__send-section-title">
      Recipients
    </h3>

    <p className="capacity-dash__send-section-description">
      {!selectedSenderAccount
        ? "Select a sender account first."
        : "Choose one or more communication channels for this report."}
    </p>
  </div>

  {selectedSenderAccount && (
    <span className="capacity-dash__recipient-count">
      {selectedChannelIds.length} selected
    </span>
  )}
</div>

                    {!selectedSenderAccount ? (
                      <div className="capacity-dash__send-empty">
                        Select a sender account to view compatible recipients.
                      </div>
                    ) : compatibleChannels.length === 0 ? (
                      <div className="capacity-dash__send-empty">
                        No compatible communication channels are available for
                        this sender account.
                      </div>
                    ) : (
                      <div className="capacity-dash__channel-list">
                        {compatibleChannels.map((channel) => (
                          <label
                            className={`capacity-dash__channel ${
                              selectedChannelIds.includes(channel.id)
                                ? "capacity-dash__channel--selected"
                                : ""
                            }`}
                            key={channel.id}
                          >
                            <input
                              type="checkbox"
                              checked={selectedChannelIds.includes(channel.id)}
                              onChange={() =>
                                toggleChannelSelection(channel.id)
                              }
                              disabled={sendingPdf}
                            />

                            <div className="capacity-dash__channel-main">
                              <div className="capacity-dash__channel-name">
                                {channel.name}
                              </div>

                              <div className="capacity-dash__channel-value">
                                {channel.value}
                              </div>

                              <div className="capacity-dash__channel-meta">
                                <span className="capacity-dash__channel-type">
                                  {channel.type}
                                </span>
                              </div>
                            </div>
                          </label>
                        ))}
                      </div>
                    )}
                  </section>
                </>
              )}
            </div>

            <footer className="capacity-dash__send-footer">
              {/* <div className="capacity-dash__send-selection">
          {selectedChannelIds.length} selected
        </div> */}
              <div className="capacity-dash__send-selection">
                <div>
                  Sender:{" "}
                  <strong>
                    {selectedSenderAccount
                      ? selectedSenderAccount.label
                      : "Not selected"}
                  </strong>
                </div>

                <div>Recipients: {selectedChannelIds.length}</div>
              </div>

              <div className="capacity-dash__send-actions">
                <button
                  className="capacity-dash__btn"
                  type="button"
                  onClick={closeSendModal}
                  disabled={sendingPdf}
                >
                  Cancel
                </button>

                <button
                  className="capacity-dash__btn capacity-dash__btn--primary"
                  type="button"
                  onClick={sendCapacityPdf}
                  disabled={
                    channelsLoading ||
                    !selectedAccountId ||
                    !selectedChannelIds.length ||
                    !sendSubject.trim() ||
                    !sendBody.trim() ||
                    sendingPdf
                  }
                >
                  {sendingPdf ? "Generating & Sending…" : "Send Report"}
                </button>
              </div>
            </footer>
          </section>
        </div>
      )}

      {printing && (
        <PrintReport
          payload={payload}
          rows={rows}
          gaps={gaps}
          stats={STATS}
          periodText={periodText}
          theme={theme}
        />
      )}

      {sendReportReady && (
        <div
          ref={sendReportRef}
          className="capacity-dash__send-report-capture"
          aria-hidden="true"
        >
          <PrintReport
            payload={payload}
            rows={rows}
            gaps={gaps}
            stats={STATS}
            periodText={periodText}
            theme="light"
          />
        </div>
      )}
    </div>
  );
}
