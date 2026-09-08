/**
 * Raw /soc2-report/* payloads -> the view model the SOC2 report renders.
 *
 * Every domain gets the same shape (a "section view"), so one renderer covers
 * all five and the printable report needs no special cases:
 *
 *   {
 *     key, heading, total,
 *     stats:      [{label, value, sub, subColor}]      -> StatCard row
 *     bars:       {title, items: [{label, value, count, colorOverride}]}
 *     charts:     [{title, unit, yMax, series}]        -> TimeSeriesChart
 *     breakdowns: [{title, items: [{label, count}]}]   -> BreakdownList
 *     tables:     [{title, columns, rows}]             -> DataTable
 *     events:     {title, items: [{severity, message, category, timestamp}]}
 *   }
 *
 * The API reports counts and shares; the compliance scores are derived here (see
 * {@link domainScore}) because the endpoints do not return one.
 */

// The report shows India Standard Time (UTC+5:30, no DST); the API returns
// UTC — naive for event rows, offset-bearing for `generated_at`.
const IST_OFFSET_MS = 330 * 60 * 1000;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const COLOR = {
  green: "green",
  amber: "amber",
  red: "red",
  blue: "blue",
  // ProgressBar takes either one of its named steps or a raw hex; grey marks the
  // bars that are neither good nor bad (events the agent left unclassified).
  grey: "#9a9891",
};

// Chart series hexes — the page is light-themed, so these are the same steps the
// bars and badges use, which are already validated against the white card.
const SERIES = {
  events: "#2b7fd0",
  good: "#5a9216",
  warn: "#e08b0a",
  bad: "#d64545",
  bytesOut: "#5B4FD6",
  bytesIn: "#0F7FA6",
};

/** Section heading, shown above each domain's tab and in the printed report. */
const HEADINGS = {
  auth: "CC6 — Logical access controls",
  process: "CC7 — System operations",
  file: "CC8 — Change management",
  network: "CC9 — Network and risk mitigation",
  usb: "CC6.7 — Removable media",
};

/** Which summary key carries the event total, per section. */
const TOTAL_KEYS = {
  auth: "total_auth_events",
  process: "total_process_events",
  file: "total_file_events",
  network: "total_network_events",
  usb: "total_usb_events",
};

// ─── formatting ────────────────────────────────────────────────

function pad(n) {
  return String(n).padStart(2, "0");
}

/**
 * Milliseconds for an API timestamp. Naive strings (no zone) are UTC by
 * contract, so they are read as such rather than as browser-local time.
 */
export function parseApiTs(iso) {
  const s = String(iso || "");
  if (!s) return null;
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/.test(s);
  const ms = Date.parse(hasZone ? s : `${s}Z`);
  return Number.isNaN(ms) ? null : ms;
}

/** UTC instant -> IST wall clock, read through the UTC getters after shifting. */
function istParts(iso) {
  const ms = parseApiTs(iso);
  if (ms == null) return null;
  return new Date(ms + IST_OFFSET_MS);
}

/** "Jul 30, 04:58" in IST — the label used in event lists and tables. */
export function fmtIst(iso) {
  const d = istParts(iso);
  if (!d) return "—";
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}, ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

/**
 * A bucket's chart label, in IST.
 *
 * TimeSeriesChart formats a tick by pulling `T(hh):(mm):(ss)` out of the string
 * and falling back to the raw string when there is no match. Hourly buckets
 * therefore get the full timestamp (ticks read "14:00:00"), while daily buckets
 * get a bare date — otherwise every day in the window would render the same
 * "05:30:00" tick, since a daily bucket is midnight UTC shifted into IST.
 */
function istIso(iso, bucket) {
  const d = istParts(iso);
  if (!d) return "";
  const date = `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
  if (bucket === "day") return date;
  return `${date}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
}

/** Thousands-separated integer, or an em dash when there is no number. */
export function fmtInt(n) {
  if (n == null || n === "" || Number.isNaN(Number(n))) return "—";
  return Number(n).toLocaleString("en-US");
}

/** Byte count -> "1.4 GB". Binary units, matching how the agents report sizes. */
export function fmtBytes(bytes) {
  const n = Number(bytes);
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB", "PB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

/** Percent share of `total`, 0-100, one decimal. */
function share(part, total) {
  const t = Number(total) || 0;
  if (!t) return 0;
  return Math.round(((Number(part) || 0) / t) * 1000) / 10;
}

function pct(value) {
  return value == null ? "—" : `${value}%`;
}

/**
 * Round a series maximum up to a readable axis top.
 *
 * Always a multiple of 4 and at least 4: TimeSeriesChart draws five ticks at
 * quarters of the top and rounds each label to an integer, so a smaller or
 * non-divisible top would render repeated labels.
 */
function niceMax(values) {
  const max = values.reduce((m, v) => Math.max(m, Number(v) || 0), 0);
  if (max <= 0) return 4;
  const mag = 10 ** Math.floor(Math.log10(max));
  const stepped = Math.ceil(max / mag) * mag;
  return Math.max(4, Math.ceil(stepped / 4) * 4);
}

/** "1 event" / "3 events" — the counts land in prose, so they have to agree. */
function plural(n, word) {
  return `${fmtInt(n)} ${word}${Number(n) === 1 ? "" : "s"}`;
}

/** Verb agreement for the same counts. */
function verb(n, singular, pluralForm) {
  return Number(n) === 1 ? singular : pluralForm;
}


/**
 * Join the parts of a description that are actually present.
 *
 * Real events routinely carry blanks — a process row can have `command_line:
 * null` and `executable: ""` — and a naive template then renders a dangling
 * "name — " with nothing after the dash.
 */
function joinParts(parts) {
  return parts.filter((p) => p != null && String(p).trim() !== "").join(" ");
}

/** One process event as a sentence, skipping whatever the agent left blank. */
function describeProcess(e) {
  const detail = e.command_line || e.executable;
  return joinParts([
    e.process_name || "unknown process",
    detail ? `— ${detail}` : "",
    e.user ? `(${e.user})` : "",
    e.ioc_match ? `· IOC ${e.ioc_match}` : "",
  ]);
}

/** Severity the event lists / badges understand. */
function sev(raw) {
  const s = String(raw || "").toLowerCase();
  return s === "critical" || s === "high" || s === "medium" || s === "low" ? s : "medium";
}

// ─── shared builders ───────────────────────────────────────────

/**
 * A domain's compliance score: start at 100 and subtract weighted penalties for
 * the share of its events that were high-severity, IOC-matched, anomalous or
 * failed. The weights sum to 100, so a domain in which every event is bad
 * scores 0.
 *
 * A domain with no events returns 100 only as a neutral default — callers must
 * not present it as a passing control. {@link scoreStat} shows those as "—" and
 * {@link buildSoc2View} leaves them out of the headline entirely, because an
 * empty window is an absence of evidence, not evidence of compliance.
 */
function domainScore({ total, high, ioc, anomalies, failures }) {
  const t = Number(total) || 0;
  if (!t) return 100;
  const frac = (n) => Math.min(1, (Number(n) || 0) / t);
  const penalty =
    frac(high) * 40 + frac(ioc) * 30 + frac(anomalies) * 20 + frac(failures) * 10;
  return Math.max(0, Math.round(100 - penalty));
}

/** The four signals {@link domainScore} weighs, read out of a section payload. */
function signalsFor(section, payload) {
  const s = (payload && payload.summary) || {};
  const total = Number(s[TOTAL_KEYS[section]]) || 0;
  return {
    total,
    high: Number(s.high_severity_events) || 0,
    ioc: Number(s.ioc_matches) || 0,
    anomalies: Number(s.anomalies) || 0,
    failures: Number(section === "auth" ? s.failed_auths : s.failed) || 0,
  };
}

function scoreColor(score) {
  if (score >= 90) return "success";
  if (score >= 75) return "warning";
  return "danger";
}

/**
 * The score stat card for a domain.
 *
 * A domain that recorded nothing is not shown as a perfect score: an empty
 * window is an absence of evidence, and the overview leaves it out of the
 * headline for the same reason.
 */
function scoreStat(label, score, total, sub) {
  if (!total) {
    return { label, value: "—", sub: "no events to score", subColor: "muted" };
  }
  return { label, value: pct(score), sub, subColor: scoreColor(score) };
}

/**
 * The events whose outcome the agent did not record.
 *
 * Agents emit `outcome: "unknown"` for a lot of telemetry (3129 process events
 * with 2019 successes and 0 failures is a real answer), so success + failure
 * rarely accounts for the whole window. Naming the remainder keeps the bars
 * honest instead of leaving a silent gap.
 */
function unrecordedOutcomes(total, successful, failed) {
  const rest = (Number(total) || 0) - (Number(successful) || 0) - (Number(failed) || 0);
  return rest > 0 ? rest : 0;
}

/** Turn {label, count, color} entries into ProgressBar props, as a share of `total`. */
function barItems(total, entries) {
  return entries
    .filter((e) => e)
    .map((e) => ({
      label: e.label,
      value: e.value != null ? e.value : share(e.count, total),
      count: e.count != null ? fmtInt(e.count) : e.display,
      colorOverride: e.color,
    }));
}

/** Drop breakdowns the window produced no rows for, so cards are never empty. */
function breakdowns(entries) {
  return entries.filter((b) => b && Array.isArray(b.items) && b.items.length > 0);
}

function tables(entries) {
  return entries.filter((t) => t && Array.isArray(t.rows) && t.rows.length > 0);
}

/** One timeseries key -> a TimeSeriesChart series. */
function seriesFrom(rows, valueKey, { key, name, color, area }, bucket) {
  return {
    key,
    name,
    color,
    area,
    data: (rows || []).map((r) => ({ t: istIso(r.t, bucket), value: Number(r[valueKey]) || 0 })),
  };
}

/** Wrap series into a chart spec, with the axis top taken from the data. */
function chart(title, series, unit = "") {
  const values = series.flatMap((s) => s.data.map((d) => d.value));
  return { title, unit, yMax: niceMax(values), series };
}

/**
 * A chart of byte counts, scaled to whichever unit keeps the numbers readable
 * (the axis labels are integers, so bytes plotted as MB would flatten to zero).
 *
 * @param {string} title
 * @param {Array} rows            the raw timeseries rows
 * @param {Array<{key: string, name: string, valueKey: string, color: string, area?: boolean}>} defs
 */
function bytesChart(title, rows, defs, bucket) {
  const all = rows || [];
  const maxBytes = all.reduce(
    (m, r) => defs.reduce((mm, d) => Math.max(mm, Number(r[d.valueKey]) || 0), m),
    0
  );
  const scale =
    maxBytes >= 1048576
      ? { div: 1048576, label: " MB" }
      : maxBytes >= 1024
        ? { div: 1024, label: " KB" }
        : { div: 1, label: " B" };

  const series = defs.map((d) => ({
    key: d.key,
    name: d.name,
    color: d.color,
    area: d.area,
    data: all.map((r) => ({
      t: istIso(r.t, bucket),
      value: Math.round(((Number(r[d.valueKey]) || 0) / scale.div) * 100) / 100,
    })),
  }));

  return {
    title: `${title} (${scale.label.trim()})`,
    unit: scale.label,
    yMax: niceMax(series.flatMap((s) => s.data.map((d) => d.value))),
    series,
  };
}

// ─── section views ─────────────────────────────────────────────

/** CC6 — authentication and access control. */
function buildAuth(payload, bucket) {
  const p = payload || {};
  const s = p.summary || {};
  const total = Number(s.total_auth_events) || 0;
  const score = domainScore(signalsFor("auth", p));
  const unrecorded = unrecordedOutcomes(total, s.successful_auths, s.failed_auths);
  const events = (p.notable_events || []).map((e) => ({
    severity: sev(e.severity),
    category: "auth",
    message: `${e.username || "unknown user"} — ${e.action || "auth"} (${e.outcome || "?"})${
      e.failure_reason ? `: ${e.failure_reason}` : ""
    }${e.source_ip ? ` from ${e.source_ip}` : ""}`,
    timestamp: fmtIst(e.timestamp),
  }));

  return {
    key: "auth",
    heading: HEADINGS.auth,
    total,
    score,
    stats: [
      { label: "Auth events", value: fmtInt(total), sub: `${fmtInt(s.successful_auths)} successful`, subColor: "muted" },
      {
        label: "Failed auths",
        value: fmtInt(s.failed_auths),
        sub: `${s.failure_rate_pct != null ? s.failure_rate_pct : 0}% failure rate`,
        subColor: Number(s.failed_auths) ? "danger" : "success",
      },
      {
        label: "Privileged actions",
        value: fmtInt(s.privileged_actions),
        sub: "sudo / elevation",
        subColor: Number(s.privileged_actions) ? "warning" : "muted",
      },
      scoreStat("CC6 score", score, total, `${fmtInt(s.unique_users)} users, ${fmtInt(s.unique_source_ips)} IPs`),
    ],
    bars: {
      title: "Login activity breakdown",
      items: barItems(total, [
        { label: "Successful auths", count: s.successful_auths, color: COLOR.green },
        { label: "Failed auths", count: s.failed_auths, color: COLOR.red },
        { label: "Privileged actions", count: s.privileged_actions, color: COLOR.amber },
        { label: "High severity", count: s.high_severity_events, color: COLOR.blue },
        unrecorded > 0 && { label: "Outcome not recorded", count: unrecorded, color: COLOR.grey },
      ]),
    },
    charts: [
      chart("Authentications per bucket", [
        seriesFrom(p.auth_timeseries, "successful", { key: "succ", name: "Successful", color: SERIES.good, area: true }, bucket),
        seriesFrom(p.auth_timeseries, "failed", { key: "fail", name: "Failed", color: SERIES.bad }, bucket),
      ]),
    ],
    breakdowns: breakdowns([
      { title: "Failed logins by user", items: p.failed_by_user },
      { title: "Failed logins by source IP", items: p.failed_by_source_ip },
      { title: "Failure reasons", items: p.failure_reasons },
      { title: "Auth methods", items: p.auth_methods },
      { title: "Session types", items: p.session_types },
      { title: "Most active users", items: p.top_active_users },
      { title: "Severity distribution", items: p.severity_distribution },
    ]),
    tables: tables([
      {
        title: "Privileged access (sudo)",
        columns: [
          { key: "time", label: "Time (IST)" },
          { key: "user", label: "User" },
          { key: "command", label: "Command" },
          { key: "outcome", label: "Outcome" },
          { key: "ip", label: "Source IP" },
        ],
        rows: (p.privileged_access || []).map((r) => ({
          time: fmtIst(r.timestamp),
          user: r.username || "—",
          command: r.sudo_command || "—",
          commandFull: r.sudo_command || "",
          outcome: r.outcome || "—",
          ip: r.source_ip || "—",
        })),
      },
    ]),
    events: { title: "High-severity auth events", items: events },
  };
}

/** CC7 — process execution and workload. */
function buildProcess(payload, bucket) {
  const p = payload || {};
  const s = p.summary || {};
  const total = Number(s.total_process_events) || 0;
  const score = domainScore(signalsFor("process", p));
  // The CPU bars are drawn against whichever is larger, so neither overflows.
  const cpuPeak = Math.max(Number(s.max_cpu_percent) || 0, Number(s.avg_cpu_percent) || 0, 1);
  const unrecorded = unrecordedOutcomes(total, s.successful, s.failed);
  const events = (p.notable_events || []).map((e) => ({
    severity: sev(e.severity),
    category: "process",
    message: describeProcess(e),
    timestamp: fmtIst(e.timestamp),
  }));

  return {
    key: "process",
    heading: HEADINGS.process,
    total,
    score,
    stats: [
      { label: "Process events", value: fmtInt(total), sub: `${fmtInt(s.unique_processes)} unique processes`, subColor: "muted" },
      {
        label: "Anomalies",
        value: fmtInt(s.anomalies),
        sub: `${fmtInt(s.ioc_matches)} IOC matches`,
        subColor: Number(s.anomalies) || Number(s.ioc_matches) ? "warning" : "success",
      },
      {
        label: "Avg CPU",
        value: s.avg_cpu_percent != null ? `${s.avg_cpu_percent}MB` : "—",
        sub: `peak ${s.max_cpu_percent != null ? `${s.max_cpu_percent}MB` : "—"}`,
        subColor: Number(s.max_cpu_percent) > 85 ? "warning" : "success",
      },
      scoreStat("CC7 score", score, total, `avg mem ${s.avg_memory_rss_mb != null ? `${s.avg_memory_rss_mb} MB` : "—"}`),
    ],
    bars: {
      // Agents report CPU summed across cores, so a busy Windows host answers
      // with figures well over 100 (761% is real). Charting those against a
      // 0-100 track would peg every bar full, so CPU is drawn against the
      // window's own peak and the true percentage is shown alongside.
      title: "Resource use (relative to the peak) and exceptions",
      items: barItems(total, [
        {
          label: "CPU — average",
          value: share(s.avg_cpu_percent, cpuPeak),
          display: s.avg_cpu_percent != null ? `${s.avg_cpu_percent}%` : "—",
        },
        {
          label: "CPU — peak",
          value: s.max_cpu_percent != null ? 100 : 0,
          display: s.max_cpu_percent != null ? `${s.max_cpu_percent}%` : "—",
        },
        { label: "Anomalous events", count: s.anomalies, color: COLOR.amber },
        { label: "High severity", count: s.high_severity_events, color: COLOR.red },
        unrecorded > 0 && { label: "Outcome not recorded", count: unrecorded, color: COLOR.grey },
      ]),
    },
    charts: [
      chart("Process events per bucket", [
        seriesFrom(p.process_timeseries, "events", { key: "events", name: "Events", color: SERIES.events, area: true }, bucket),
        seriesFrom(p.process_timeseries, "anomalies", { key: "anom", name: "Anomalies", color: SERIES.warn }, bucket),
        seriesFrom(p.process_timeseries, "high_severity", { key: "high", name: "High severity", color: SERIES.bad }, bucket),
      ]),
    ],
    breakdowns: breakdowns([
      { title: "Top processes", items: p.top_process_names },
      { title: "Top executables", items: p.top_executables },
      { title: "Top users", items: p.top_users },
      { title: "Working directories", items: p.top_working_dirs },
      { title: "Severity distribution", items: p.severity_distribution },
      { title: "IOC matches", items: p.ioc_matches },
      { title: "MITRE tactics", items: p.mitre_tactics },
      { title: "MITRE techniques", items: p.mitre_techniques },
    ]),
    tables: tables([
      {
        title: "Highest CPU events",
        columns: [
          { key: "time", label: "Time (IST)" },
          { key: "process", label: "Process" },
          { key: "pid", label: "PID" },
          { key: "user", label: "User" },
          { key: "cpu", label: "CPU (MB)" },
          { key: "mem", label: "Memory" },
        ],
        rows: (p.top_cpu_events || []).map((r) => ({
          time: fmtIst(r.timestamp),
          process: r.process_name || "—",
          pid: r.pid != null ? r.pid : "—",
          user: r.user || "—",
          cpu: r.cpu_percent != null ? `${r.cpu_percent}MB` : "—",
          mem: r.memory_rss_mb != null ? `${r.memory_rss_mb} MB` : "—",
        })),
      },
      {
        title: "Anomalous executions",
        columns: [
          { key: "time", label: "Time (IST)" },
          { key: "process", label: "Process" },
          { key: "cmd", label: "Command line" },
          { key: "user", label: "User" },
          { key: "risk", label: "Risk" },
          { key: "mitre", label: "MITRE" },
        ],
        rows: (p.anomalous_events || []).map((r) => ({
          time: fmtIst(r.timestamp),
          process: r.process_name || "—",
          cmd: r.command_line || "—",
          cmdFull: r.command_line || "",
          user: r.user || "—",
          risk: r.risk_score != null ? r.risk_score : "—",
          mitre: [r.mitre_tactic, r.mitre_technique].filter(Boolean).join(" / ") || "—",
        })),
      },
      {
        title: "Flagged hashes",
        columns: [
          { key: "time", label: "Time (IST)" },
          { key: "process", label: "Process" },
          { key: "sha", label: "SHA-256" },
          { key: "ioc", label: "IOC" },
          { key: "user", label: "User" },
        ],
        rows: (p.flagged_hashes || []).map((r) => ({
          time: fmtIst(r.timestamp),
          process: r.process_name || "—",
          sha: r.sha256 || "—",
          shaFull: r.sha256 || "",
          ioc: r.ioc_match || "—",
          user: r.user || "—",
        })),
      },
    ]),
    events: { title: "High-severity process events", items: events },
  };
}

/** CC8 — file integrity and change management. */
function buildFile(payload, bucket) {
  const p = payload || {};
  const s = p.summary || {};
  const total = Number(s.total_file_events) || 0;
  const score = domainScore(signalsFor("file", p));
  const unrecorded = unrecordedOutcomes(total, s.successful, s.failed);
  const events = (p.notable_events || []).map((e) => ({
    severity: sev(e.severity),
    category: "file",
    message: `${e.action || "change"} ${e.file_path || ""}${e.user_name ? ` (${e.user_name})` : ""}`,
    // the untruncated path behind the message — EventList copies this, not
    // the sentence it is embedded in
    copyValue: e.file_path || "",
    timestamp: fmtIst(e.timestamp),
  }));

  return {
    key: "file",
    heading: HEADINGS.file,
    total,
    score,
    stats: [
      { label: "File events", value: fmtInt(total), sub: `${fmtInt(s.unique_files)} unique files`, subColor: "muted" },
      {
        label: "Anomalies",
        value: fmtInt(s.anomalies),
        sub: `${fmtInt(s.ioc_matches)} IOC matches`,
        subColor: Number(s.anomalies) || Number(s.ioc_matches) ? "warning" : "success",
      },
      {
        label: "High severity",
        value: fmtInt(s.high_severity_events),
        sub: `max risk ${s.max_risk_score != null ? s.max_risk_score : "—"}`,
        subColor: Number(s.high_severity_events) ? "danger" : "success",
      },
      scoreStat("CC8 score", score, total, `${fmtInt(s.unique_users)} users`),
    ],
    bars: {
      title: "File change breakdown",
      items: barItems(total, [
        { label: "Successful changes", count: s.successful, color: COLOR.green },
        { label: "Failed changes", count: s.failed, color: COLOR.red },
        { label: "Anomalous changes", count: s.anomalies, color: COLOR.amber },
        { label: "High severity", count: s.high_severity_events, color: COLOR.blue },
        unrecorded > 0 && { label: "Outcome not recorded", count: unrecorded, color: COLOR.grey },
      ]),
    },
    charts: [
      chart("File events per bucket", [
        seriesFrom(p.file_timeseries, "events", { key: "events", name: "Events", color: SERIES.events, area: true }, bucket),
        seriesFrom(p.file_timeseries, "anomalies", { key: "anom", name: "Anomalies", color: SERIES.warn }, bucket),
        seriesFrom(p.file_timeseries, "high_severity", { key: "high", name: "High severity", color: SERIES.bad }, bucket),
      ]),
    ],
    breakdowns: breakdowns([
      { title: "Actions", items: p.top_actions },
      { title: "File extensions", items: p.file_extensions },
      { title: "Top directories", items: p.top_directories },
      { title: "Most active files", items: p.most_active_files },
      { title: "Top users", items: p.top_users },
      { title: "Severity distribution", items: p.severity_distribution },
      { title: "IOC matches", items: p.ioc_matches },
      { title: "MITRE tactics", items: p.mitre_tactics },
      { title: "MITRE techniques", items: p.mitre_techniques },
    ]),
    tables: tables([
      {
        title: "Integrity changes (renames / hash changes)",
        columns: [
          { key: "time", label: "Time (IST)" },
          { key: "path", label: "File" },
          { key: "oldPath", label: "Previous path" },
          { key: "sha", label: "SHA-256" },
          { key: "oldSha", label: "Previous SHA-256" },
          { key: "user", label: "User" },
        ],
        rows: (p.integrity_changes || []).map((r) => ({
          time: fmtIst(r.timestamp),
          path: r.file_path || "—",
          pathFull: r.file_path || "",
          oldPath: r.old_path || "—",
          oldPathFull: r.old_path || "",
          sha: r.sha256 || "—",
          shaFull: r.sha256 || "",
          oldSha: r.old_sha256 || "—",
          oldShaFull: r.old_sha256 || "",
          user: r.user_name || "—",
        })),
      },
      {
        title: "Anomalous file changes",
        columns: [
          { key: "time", label: "Time (IST)" },
          { key: "action", label: "Action" },
          { key: "path", label: "File" },
          { key: "user", label: "User" },
          { key: "risk", label: "Risk" },
          { key: "mitre", label: "MITRE" },
        ],
        rows: (p.anomalous_events || []).map((r) => ({
          time: fmtIst(r.timestamp),
          action: r.action || "—",
          path: r.file_path || "—",
          pathFull: r.file_path || "",
          user: r.user_name || "—",
          risk: r.risk_score != null ? r.risk_score : "—",
          mitre: [r.mitre_tactic, r.mitre_technique].filter(Boolean).join(" / ") || "—",
        })),
      },
    ]),
    events: { title: "High-severity file changes", items: events },
  };
}

/** CC9 — network boundary and data transfer. */
function buildNetwork(payload, bucket) {
  const p = payload || {};
  const s = p.summary || {};
  const total = Number(s.total_network_events) || 0;
  const score = domainScore(signalsFor("network", p));
  const unrecorded = unrecordedOutcomes(total, s.successful, s.failed);
  const events = (p.notable_events || []).map((e) => ({
    severity: sev(e.severity),
    category: "network",
    message: `${e.direction || "conn"} ${e.source_ip || "?"} → ${e.dest_ip || "?"}${
      e.dest_port ? `:${e.dest_port}` : ""
    }${e.protocol ? ` (${e.protocol})` : ""}${e.process_name ? ` · ${e.process_name}` : ""}`,
    timestamp: fmtIst(e.timestamp),
  }));

  return {
    key: "network",
    heading: HEADINGS.network,
    total,
    score,
    stats: [
      { label: "Network events", value: fmtInt(total), sub: `${fmtInt(s.unique_dest_ips)} destinations`, subColor: "muted" },
      { label: "Data sent", value: fmtBytes(s.total_bytes_sent), sub: `received ${fmtBytes(s.total_bytes_received)}`, subColor: "muted" },
      {
        label: "High severity",
        value: fmtInt(s.high_severity_events),
        sub: `${fmtInt(s.anomalies)} anomalies, ${fmtInt(s.ioc_matches)} IOC`,
        subColor: Number(s.high_severity_events) ? "danger" : "success",
      },
      scoreStat("CC9 score", score, total, `${fmtInt(s.unique_dns_queries)} DNS queries`),
    ],
    bars: {
      title: "Connection breakdown",
      items: barItems(total, [
        { label: "Successful connections", count: s.successful, color: COLOR.green },
        { label: "Failed connections", count: s.failed, color: COLOR.amber },
        { label: "Anomalous connections", count: s.anomalies, color: COLOR.red },
        { label: "High severity", count: s.high_severity_events, color: COLOR.blue },
        unrecorded > 0 && { label: "Outcome not recorded", count: unrecorded, color: COLOR.grey },
      ]),
    },
    charts: [
      chart("Connections per bucket", [
        seriesFrom(p.network_timeseries, "events", { key: "events", name: "Events", color: SERIES.events, area: true }, bucket),
        seriesFrom(p.network_timeseries, "anomalies", { key: "anom", name: "Anomalies", color: SERIES.bad }, bucket),
      ]),
      bytesChart("Data transferred per bucket", p.network_timeseries, [
        { key: "sent", name: "Sent", valueKey: "bytes_sent", color: SERIES.bytesOut, area: true },
        { key: "recv", name: "Received", valueKey: "bytes_received", color: SERIES.bytesIn },
      ], bucket),
    ],
    breakdowns: breakdowns([
      { title: "Top destination IPs", items: p.top_dest_ips },
      { title: "Top destination ports", items: p.top_dest_ports },
      { title: "Top source IPs", items: p.top_source_ips },
      { title: "Protocols", items: p.protocols },
      { title: "Transports", items: p.transports },
      { title: "Directions", items: p.directions },
      { title: "Connection status", items: p.connection_status },
      { title: "Top DNS queries", items: p.top_dns_queries },
      { title: "Top processes", items: p.top_processes },
      { title: "Severity distribution", items: p.severity_distribution },
      { title: "MITRE tactics", items: p.mitre_tactics },
    ]),
    tables: tables([
      {
        title: "Top talkers (by bytes)",
        columns: [
          { key: "ip", label: "Destination" },
          { key: "bytes", label: "Total bytes" },
          { key: "conns", label: "Connections" },
        ],
        rows: (p.top_talkers || []).map((r) => ({
          ip: r.dest_ip || "—",
          bytes: fmtBytes(r.total_bytes),
          conns: fmtInt(r.connections),
        })),
      },
      {
        title: "External (public) destinations",
        columns: [
          { key: "ip", label: "Destination" },
          { key: "count", label: "Connections" },
        ],
        rows: (p.external_connections || []).map((r) => ({
          ip: r.dest_ip || "—",
          count: fmtInt(r.count),
        })),
      },
    ]),
    events: { title: "High-severity network events", items: events },
  };
}

/** Removable media — device usage and data egress. */
function buildUsb(payload, bucket) {
  const p = payload || {};
  const s = p.summary || {};
  const total = Number(s.total_usb_events) || 0;
  const score = domainScore(signalsFor("usb", p));
  const unrecorded = unrecordedOutcomes(total, s.successful, s.failed);
  const events = (p.notable_events || []).map((e) => ({
    severity: sev(e.severity),
    category: "usb",
    message: `${e.action || "usb"} — ${[e.vendor, e.model].filter(Boolean).join(" ") || "device"}${
      e.serial_number ? ` (${e.serial_number})` : ""
    }${e.file_name ? ` · ${e.file_name}` : ""}`,
    timestamp: fmtIst(e.timestamp),
  }));

  return {
    key: "usb",
    heading: HEADINGS.usb,
    total,
    score,
    stats: [
      { label: "USB events", value: fmtInt(total), sub: `${fmtInt(s.unique_devices)} devices`, subColor: "muted" },
      {
        label: "Data transferred",
        value: fmtBytes(s.total_bytes_transferred),
        sub: "to / from removable media",
        subColor: Number(s.total_bytes_transferred) ? "warning" : "muted",
      },
      {
        label: "High severity",
        value: fmtInt(s.high_severity_events),
        sub: `${fmtInt(s.anomalies)} anomalies, ${fmtInt(s.ioc_matches)} IOC`,
        subColor: Number(s.high_severity_events) ? "danger" : "success",
      },
      scoreStat("Media score", score, total, `${fmtInt(s.unique_vendors)} vendors`),
    ],
    bars: {
      title: "Device activity breakdown",
      items: barItems(total, [
        { label: "Successful operations", count: s.successful, color: COLOR.green },
        { label: "Failed operations", count: s.failed, color: COLOR.amber },
        { label: "Anomalous operations", count: s.anomalies, color: COLOR.red },
        { label: "High severity", count: s.high_severity_events, color: COLOR.blue },
        unrecorded > 0 && { label: "Outcome not recorded", count: unrecorded, color: COLOR.grey },
      ]),
    },
    charts: [
      chart("USB events per bucket", [
        seriesFrom(p.usb_timeseries, "events", { key: "events", name: "Events", color: SERIES.events, area: true }, bucket),
        seriesFrom(p.usb_timeseries, "anomalies", { key: "anom", name: "Anomalies", color: SERIES.bad }, bucket),
      ]),
      bytesChart("Bytes transferred per bucket", p.usb_timeseries, [
        { key: "bytes", name: "Transferred", valueKey: "bytes_transferred", color: SERIES.bytesOut, area: true },
      ], bucket),
    ],
    breakdowns: breakdowns([
      { title: "Vendors", items: p.top_vendors },
      { title: "Models", items: p.top_models },
      { title: "Devices (serial)", items: p.top_devices },
      { title: "Filesystem types", items: p.filesystem_types },
      { title: "Actions", items: p.top_actions },
      { title: "Severity distribution", items: p.severity_distribution },
      { title: "MITRE tactics", items: p.mitre_tactics },
    ]),
    tables: tables([
      {
        title: "Device activity",
        columns: [
          { key: "time", label: "Time (IST)" },
          { key: "action", label: "Action" },
          { key: "device", label: "Device" },
          { key: "serial", label: "Serial" },
          { key: "mount", label: "Mount" },
          { key: "fs", label: "FS" },
          { key: "size", label: "Size" },
        ],
        rows: (p.device_activity || []).map((r) => ({
          time: fmtIst(r.timestamp),
          action: r.action || "—",
          device: [r.vendor, r.model].filter(Boolean).join(" ") || r.label || "—",
          serial: r.serial_number || "—",
          serialFull: r.serial_number || "",
          mount: r.mountpoint || "—",
          mountFull: r.mountpoint || "",
          fs: r.fstype || "—",
          size: fmtBytes(r.size_bytes),
        })),
      },
      {
        title: "Data transfers",
        columns: [
          { key: "time", label: "Time (IST)" },
          { key: "device", label: "Device" },
          { key: "file", label: "File" },
          { key: "bytes", label: "Transferred" },
        ],
        rows: (p.data_transfers || []).map((r) => ({
          time: fmtIst(r.timestamp),
          device: [r.vendor, r.model].filter(Boolean).join(" ") || r.serial_number || "—",
          file: r.file_name || r.file_path || "—",
          fileFull: r.file_path || r.file_name || "",
          bytes: fmtBytes(r.transfer_bytes),
        })),
      },
    ]),
    events: { title: "High-severity media events", items: events },
  };
}

const BUILDERS = {
  auth: buildAuth,
  process: buildProcess,
  file: buildFile,
  network: buildNetwork,
  usb: buildUsb,
};

const CRITERIA_LABELS = {
  auth: "CC6 — Access control",
  process: "CC7 — System operations",
  file: "CC8 — Change management",
  network: "CC9 — Network security",
  usb: "CC6.7 — Removable media",
};

// ─── incidents and recommendations ─────────────────────────────

/**
 * Every notable and anomalous event across the five domains, newest first, as
 * IncidentsTable rows. `sortTs` keeps the table's date sort on the real instant
 * rather than the formatted label.
 */
/**
 * The scope as a single label: one agent by name, several as a count, none
 * (every agent) as an empty string so callers can fall back to "All agents".
 */
function scopeName(names) {
  const list = Array.isArray(names) ? names.filter(Boolean) : names ? [names] : [];
  if (list.length === 0) return "";
  if (list.length === 1) return list[0];
  return `${list.length} agents`;
}

function buildIncidents(sections, agentName) {
  const rows = [];
  const push = (section, e, description, severity, mitre) => {
    rows.push({
      date: fmtIst(e.timestamp),
      sortTs: parseApiTs(e.timestamp) || 0,
      severity: sev(severity),
      category: section,
      description: String(description == null ? "" : description),
      agent_name: agentName || "all agents",
      mitre_technique: mitre || "—",
    });
  };

  const auth = sections.auth || {};
  (auth.notable_events || []).forEach((e) =>
    push("auth", e, `${e.username || "unknown"} — ${e.action || "auth"} ${e.outcome || ""}${e.failure_reason ? `: ${e.failure_reason}` : ""}`, e.severity)
  );

  const process = sections.process || {};
  (process.notable_events || []).forEach((e) => push("process", e, describeProcess(e), e.severity));
  (process.anomalous_events || []).forEach((e) =>
    push(
      "process",
      e,
      joinParts(["Anomalous execution:", e.process_name || "unknown", e.command_line]),
      "medium",
      e.mitre_technique
    )
  );

  const file = sections.file || {};
  (file.notable_events || []).forEach((e) =>
    push("file", e, `${e.action || "change"} ${e.file_path || ""}`, e.severity)
  );
  (file.anomalous_events || []).forEach((e) =>
    push("file", e, `Anomalous file change: ${e.action || ""} ${e.file_path || ""}`, "medium", e.mitre_technique)
  );

  const network = sections.network || {};
  (network.notable_events || []).forEach((e) =>
    push("network", e, `${e.direction || "conn"} ${e.source_ip || "?"} → ${e.dest_ip || "?"}${e.dest_port ? `:${e.dest_port}` : ""}`, e.severity)
  );

  const usb = sections.usb || {};
  (usb.notable_events || []).forEach((e) =>
    push("usb", e, `${e.action || "usb"} ${[e.vendor, e.model].filter(Boolean).join(" ")}${e.file_name ? ` · ${e.file_name}` : ""}`, e.severity)
  );

  return rows.sort((a, b) => b.sortTs - a.sortTs);
}

/**
 * Findings worth acting on, derived from the window's own numbers.
 *
 * `pendingCount` only changes the "nothing found" fallback: with requests still
 * open, a clean bill of health would be premature.
 */
// function buildRecommendations(sections, views, pendingCount = 0) {
//   const recs = [];
//   const auth = (sections.auth && sections.auth.summary) || {};

//   if (Number(auth.failed_auths) > 0 && Number(auth.failure_rate_pct) >= 20) {
//     recs.push({
//       priority: "critical",
//       text: `${plural(auth.failed_auths, "failed authentication")} (${auth.failure_rate_pct}% of all attempts). Review the top failing users and source IPs, and confirm lockout thresholds are enforced.`,
//     });
//   } else if (Number(auth.failed_auths) > 0) {
//     recs.push({
//       priority: "info",
//       text: `${plural(auth.failed_auths, "failed authentication")} (${auth.failure_rate_pct || 0}%). Within tolerance — keep the failure trend under review.`,
//     });
//   }

//   if (Number(auth.privileged_actions) > 0) {
//     recs.push({
//       priority: "warning",
//       text: `${plural(auth.privileged_actions, "privileged (sudo) action")} ${verb(auth.privileged_actions, "was", "were")} recorded. Confirm each maps to an approved change or ticket.`,
//     });
//   }

//   Object.keys(BUILDERS).forEach((key) => {
//     const sum = (sections[key] && sections[key].summary) || {};
//     const label = CRITERIA_LABELS[key];
//     if (Number(sum.ioc_matches) > 0) {
//       recs.push({
//         priority: "critical",
//         text: `${label}: ${plural(sum.ioc_matches, "event")} matched a threat indicator. Triage these first — they are the highest-signal findings in the window.`,
//       });
//     }
//     if (Number(sum.high_severity_events) > 0) {
//       recs.push({
//         priority: "warning",
//         text: `${label}: ${plural(sum.high_severity_events, "high or critical event")} ${verb(sum.high_severity_events, "needs", "need")} documented review to evidence the control.`,
//       });
//     }
//     if (Number(sum.anomalies) > 0) {
//       recs.push({
//         priority: "warning",
//         text: `${label}: ${plural(sum.anomalies, "anomalous event")} ${verb(sum.anomalies, "was", "were")} flagged. Confirm each is expected behaviour or raise an incident.`,
//       });
//     }
//   });

//   const usb = (sections.usb && sections.usb.summary) || {};
//   if (Number(usb.total_bytes_transferred) > 0) {
//     recs.push({
//       priority: "warning",
//       text: `${fmtBytes(usb.total_bytes_transferred)} moved across removable media on ${plural(usb.unique_devices, "device")}. Verify the transfers were authorised under the data-handling policy.`,
//     });
//   }

//   const external = (sections.network && sections.network.external_connections) || [];
//   if (external.length > 0) {
//     recs.push({
//       priority: "info",
//       text: `${plural(external.length, "public destination")} ${verb(external.length, "was", "were")} contacted. Reconcile the list against the approved egress allowlist.`,
//     });
//   }

//   const weakest = Object.values(views)
//     .filter((v) => v && !v.unavailable && v.total > 0)
//     .sort((a, b) => a.score - b.score)[0];
//   if (weakest && weakest.score < 90) {
//     recs.push({
//       priority: weakest.score < 75 ? "critical" : "warning",
//       text: `${weakest.heading} scored ${weakest.score}% — the lowest of the domains with events in this window. Prioritise its exceptions in the next remediation cycle.`,
//     });
//   }

//   if (recs.length === 0) {
//     recs.push({
//       priority: "info",
//       text: pendingCount
//         ? `Still collecting ${plural(pendingCount, "domain report")} — findings will appear as they arrive.`
//         : "No control exceptions were recorded in this window. Keep the evidence for the audit period and re-run the report for the next window.",
//     });
//   }

//   return recs;
// }


function buildRecommendations(sections, views, pendingCount = 0) {
  const recs = [];

  const addFinding = ({
    priority,
    category,
    finding,
    evidence,
    recommendedAction,
  }) => {
    recs.push({
      priority,
      category,
      finding,
      evidence,
      recommendedAction,
      // Keep text for backward compatibility with any existing consumer.
      text: `${finding} ${evidence}`,
    });
  };

  const auth = (sections.auth && sections.auth.summary) || {};

  // ------------------------------------------------------------
  // Authentication failures
  // ------------------------------------------------------------
  if (Number(auth.failed_auths) > 0 && Number(auth.failure_rate_pct) >= 20) {
    addFinding({
      priority: "critical",
      category: "Access Control",
      finding: "Excessive authentication failures detected.",
      evidence: `${plural(auth.failed_auths, "failed authentication")} (${auth.failure_rate_pct}% of all attempts).`,
      recommendedAction:
        "Review the top failing users and source IPs, investigate repeated failures, and confirm account lockout thresholds are enforced.",
    });
  } else if (Number(auth.failed_auths) > 0) {
    addFinding({
      priority: "info",
      category: "Access Control",
      finding: "Authentication failures were recorded during the reporting window.",
      evidence: `${plural(auth.failed_auths, "failed authentication")} (${auth.failure_rate_pct || 0}% of all attempts).`,
      recommendedAction:
        "Keep the authentication failure trend under review and investigate any recurring users, source IPs, or unusual patterns.",
    });
  }

  // ------------------------------------------------------------
  // Privileged actions
  // ------------------------------------------------------------
  if (Number(auth.privileged_actions) > 0) {
    addFinding({
      priority: "warning",
      category: "Privileged Access",
      finding: "Privileged actions were recorded.",
      evidence: `${plural(auth.privileged_actions, "privileged (sudo) action")} ${verb(
        auth.privileged_actions,
        "was",
        "were"
      )} recorded.`,
      recommendedAction:
        "Confirm each privileged action maps to an approved change, ticket, or documented administrative activity.",
    });
  }

  // ------------------------------------------------------------
  // Domain-level findings
  // ------------------------------------------------------------
  Object.keys(BUILDERS).forEach((key) => {
    const sum = (sections[key] && sections[key].summary) || {};
    const label = CRITERIA_LABELS[key];

    // IOC matches
    if (Number(sum.ioc_matches) > 0) {
      addFinding({
        priority: "critical",
        category: label,
        finding: "Threat-indicator matches require immediate review.",
        evidence: `${plural(sum.ioc_matches, "event")} matched a threat indicator.`,
        recommendedAction:
          "Prioritise these events for triage, validate the associated indicators, investigate affected assets, and document the resulting response.",
      });
    }

    // High / critical severity
    if (Number(sum.high_severity_events) > 0) {
      addFinding({
        priority: "warning",
        category: label,
        finding: "High-severity events require documented review.",
        evidence: `${plural(
          sum.high_severity_events,
          "high or critical event"
        )} ${verb(sum.high_severity_events, "was", "were")} recorded.`,
        recommendedAction:
          "Review each high-severity event, document the investigation outcome, and retain supporting evidence to demonstrate control operation.",
      });
    }

    // Anomalies
    if (Number(sum.anomalies) > 0) {
      addFinding({
        priority: "warning",
        category: label,
        finding: "Anomalous activity was detected.",
        evidence: `${plural(sum.anomalies, "anomalous event")} ${verb(
          sum.anomalies,
          "was",
          "were"
        )} flagged.`,
        recommendedAction:
          "Validate whether each anomaly represents expected behaviour. Escalate unexpected activity as an incident and retain the investigation evidence.",
      });
    }
  });

  // ------------------------------------------------------------
  // Removable media
  // ------------------------------------------------------------
  const usb = (sections.usb && sections.usb.summary) || {};

  if (Number(usb.total_bytes_transferred) > 0) {
    addFinding({
      priority: "warning",
      category: "Removable Media",
      finding: "Data was transferred through removable media.",
      evidence: `${fmtBytes(
        usb.total_bytes_transferred
      )} moved across removable media on ${plural(
        usb.unique_devices,
        "device"
      )}.`,
      recommendedAction:
        "Verify that the transfers were authorised under the applicable data-handling policy and retain evidence of the approval where required.",
    });
  }

  // ------------------------------------------------------------
  // External network destinations
  // ------------------------------------------------------------
  const external =
    (sections.network && sections.network.external_connections) || [];

  if (external.length > 0) {
    addFinding({
      priority: "info",
      category: "Network Security",
      finding: "Public network destinations were contacted.",
      evidence: `${plural(external.length, "public destination")} ${verb(
        external.length,
        "was",
        "were"
      )} contacted.`,
      recommendedAction:
        "Reconcile the observed destinations against the approved egress allowlist and investigate any destination that cannot be justified.",
    });
  }

  // ------------------------------------------------------------
  // Lowest scoring domain
  // ------------------------------------------------------------
  const weakest = Object.values(views)
    .filter((v) => v && !v.unavailable && v.total > 0)
    .sort((a, b) => a.score - b.score)[0];

  if (weakest && weakest.score < 90) {
    addFinding({
      priority: weakest.score < 75 ? "critical" : "warning",
      category: weakest.heading,
      finding: "This domain has the lowest compliance score in the reporting window.",
      evidence: `${weakest.heading} scored ${weakest.score}%.`,
      recommendedAction:
        "Prioritise the exceptions identified in this domain during the next remediation cycle and retain evidence demonstrating the corrective actions.",
    });
  }

  // ------------------------------------------------------------
  // No findings
  // ------------------------------------------------------------
  if (recs.length === 0) {
    addFinding({
      priority: "info",
      category: "Overall",
      finding: pendingCount
        ? "The report is still collecting domain evidence."
        : "No control exceptions were recorded in this reporting window.",
      evidence: pendingCount
        ? `${plural(pendingCount, "domain report")} is still pending.`
        : "No generated recommendation criteria were triggered.",
      recommendedAction: pendingCount
        ? "Wait for the remaining domain evidence before treating the report as complete."
        : "Retain the evidence for the audit period and re-run the report for the next reporting window.",
    });
  }

  return recs;
}

// ─── entry point ───────────────────────────────────────────────

/**
 * Placeholder for a domain with no payload yet.
 *
 * `state` is "pending" while its request is in flight and "unavailable" once it
 * has failed. Both withhold the numbers; only the message differs, so a loading
 * domain is never mistaken for a broken one (or for a clean 100%).
 */
function placeholderView(key, state) {
  return {
    key,
    heading: HEADINGS[key],
    total: 0,
    score: null,
    pending: state === "pending",
    unavailable: state === "unavailable",
    stats: [],
    bars: { title: "", items: [] },
    charts: [],
    breakdowns: [],
    tables: [],
    events: { title: "", items: [] },
  };
}

/**
 * Build the whole report view model.
 *
 * Safe to call while requests are still open: the report is assembled from the
 * sections that have arrived, so each domain appears as its own request lands.
 *
 * @param {Object<string, Object|null>} sections the five raw payloads, keyed by
 *   section; a `null` slot yields a placeholder view rather than a hole in the
 *   report — pending or failed, per `options.pending`.
 * @param {{agentNames?: string[], fromDt?: string, toDt?: string, bucket?: string}} params
 * @param {{pending?: string[]}} [options] sections whose request is still open
 */
export function buildSoc2View(sections, params = {}, options = {}) {
  const raw = sections || {};
  const pending = new Set(options.pending || []);
  const views = {};
  Object.keys(BUILDERS).forEach((key) => {
    // A domain with no payload is flagged rather than built: zeroed stats would
    // read as a clean 100% when nothing was actually checked.
    if (raw[key]) {
      views[key] = { ...BUILDERS[key](raw[key], params.bucket), pending: false, unavailable: false };
    } else {
      views[key] = placeholderView(key, pending.has(key) ? "pending" : "unavailable");
    }
  });

  const loaded = Object.keys(views).filter((key) => Boolean(raw[key]));
  // A domain that answered with no events at all is not evidence of a working
  // control — it is an absence of evidence. Those are listed separately instead
  // of being scored 100% and pulling the headline up with them.
  const scored = loaded.filter((key) => views[key].total > 0);
  const silent = loaded.filter((key) => views[key].total === 0);

  const criteria = scored.map((key) => ({
    key,
    label: CRITERIA_LABELS[key],
    value: views[key].score,
  }));

  const totals = Object.keys(views).reduce(
    (acc, key) => {
      const sig = signalsFor(key, raw[key]);
      acc.events += sig.total;
      acc.high += sig.high;
      acc.anomalies += sig.anomalies;
      acc.ioc += sig.ioc;
      return acc;
    },
    { events: 0, high: 0, anomalies: 0, ioc: 0 }
  );

  // Score the period over the domains that actually produced events, so neither
  // a failed request nor a silent domain can inflate the headline.
  const complianceScore = scored.length
    ? Math.round(scored.reduce((sum, key) => sum + views[key].score, 0) / scored.length)
    : null;

  const incidents = buildIncidents(raw, scopeName(params.agentNames));

  // The overview's event feed: the same merged incident stream, newest first, in
  // the shape EventList reads.
  const recentEvents = incidents.slice(0, 40).map((inc) => ({
    severity: inc.severity,
    category: inc.category,
    message: inc.description,
    timestamp: inc.date,
  }));

  const meta = (raw.auth && raw.auth.meta) || (raw.process && raw.process.meta) || {};

  return {
    meta: {
      agentName: scopeName(params.agentNames) || scopeName(meta.agent_name),
      fromDt: params.fromDt || meta.from_dt || "",
      toDt: params.toDt || meta.to_dt || "",
      bucket: params.bucket || "hour",
      generatedAt: fmtIst(meta.generated_at),
    },
    summary: {
      totalEvents: totals.events,
      highSeverity: totals.high,
      anomalies: totals.anomalies,
      iocMatches: totals.ioc,
      complianceScore,
      criteria,
      recentEvents,
      byDomain: loaded.map((key) => ({
        label: CRITERIA_LABELS[key],
        count: views[key].total,
      })),
      // domains that answered with an empty window — shown next to the scores,
      // because "no events" is not the same as "no findings"
      silentDomains: silent.map((key) => CRITERIA_LABELS[key]),
      // progress, so the overview can say what it is still waiting for
      loadedCount: loaded.length,
      pendingCount: pending.size,
      totalCount: Object.keys(views).length,
    },
    views,
    incidents,
    recommendations: buildRecommendations(raw, views, pending.size),
  };
}

export { CRITERIA_LABELS };
