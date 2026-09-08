import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import "./SOC2Report.css";

import StatCard from "./components/StatCard";
import ProgressBar from "./components/ProgressBar";
import EventList from "./components/EventList";
import IncidentsTable from "./components/IncidentsTable";
import AgentsTable from "./components/AgentsTable";
import useCopyFlash from "../hooks/useCopyFlash";
import CriteriaScores from "./components/CriteriaScores";
import TimeSeriesChart from "./components/TimeSeriesChart";

import { fetchSoc2Report, fetchAgents, SOC2_SECTIONS } from "./soc2Api";
import { buildSoc2View, fmtInt, fmtIst } from "./soc2Transform";
import { lastHoursInputs, istInputToApi } from "./timeRange";

import {
  LuShieldCheck,
  LuUserRound,
  LuClock3,
  LuPlay,
  LuFileDown,
} from "react-icons/lu";

// ─── controls ──────────────────────────────────────────────────

// Quick ranges, in hours. Anything past two days is bucketed by day by default,
// because an hourly series over a month is unreadable.
const PRESET_HOURS = [12, 24, 48, 168, 720];

function presetLabel(hours) {
  if (hours < 24) return `Last ${hours} hours`;
  const days = hours / 24;
  return days === 1 ? "Last 24 hours" : `Last ${days} days`;
}

function defaultBucket(hours) {
  return hours > 48 ? "day" : "hour";
}

/** Length in hours of a window given as two IST datetime-local values. */
function spanHours(fromLocal, toLocal) {
  const from = Date.parse(`${fromLocal}:00Z`);
  const to = Date.parse(`${toLocal}:00Z`);
  if (Number.isNaN(from) || Number.isNaN(to) || to <= from) return 12;
  return (to - from) / (60 * 60 * 1000);
}

const TABS = [
  { key: "overview", label: "Overview" },
  { key: "auth", label: "Access control" },
  { key: "process", label: "System ops" },
  { key: "file", label: "Change mgmt" },
  { key: "network", label: "Network" },
  { key: "usb", label: "Removable media" },
  { key: "incidents", label: "Incidents" },
  { key: "agents", label: "Agents" },
  { key: "recommendations", label: "Recommendations" },
];

function ShieldIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M12 2 4 5v6c0 5 3.4 8.5 8 11 4.6-2.5 8-6 8-11V5l-8-3Z"
        fill="#185FA5"
        opacity="0.15"
      />
      <path
        d="M12 2 4 5v6c0 5 3.4 8.5 8 11 4.6-2.5 8-6 8-11V5l-8-3Z"
        stroke="#185FA5"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function isCanceled(err) {
  return Boolean(err) && (err.code === "ERR_CANCELED" || err.name === "CanceledError");
}

/**
 * What a selection covers, in words. Naming one agent is worth doing; naming
 * nine is not, so past a single agent it is a count.
 */
function agentScopeLabel(names, total) {
  const list = (names || []).filter(Boolean);
  if (list.length === 0) return "All agents";
  if (total > 0 && list.length === total) return `All agents (${total})`;
  if (list.length === 1) return list[0];
  return `${list.length} agents`;
}

export default function SOC2Report() {
  const initialWindow = useMemo(() => lastHoursInputs(12), []);

  // `?agent=<name>` scopes the report on arrival — that is how the dashboard's
  // agent table hands an agent over. The parameter may repeat, one per agent,
  // and `agent_name` is accepted too, for URLs written by hand against the
  // API's own parameter name. Naming none means every agent.
  const [searchParams, setSearchParams] = useSearchParams();
  const initialAgents = useMemo(
    () =>
      [
        ...searchParams.getAll("agent"),
        ...searchParams.getAll("agent_name"),
      ]
        .map((n) => n.trim())
        .filter(Boolean),
    // read once, on arrival: later edits come from the controls, not the URL
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  );

  // `?from=`/`?to=` (IST datetime-local values, "YYYY-MM-DDTHH:mm") arrive with
  // the agent when the dashboard's "Create SOC2 report" form hands a window over.
  // Anything malformed falls back to the default last-12-hours window.
  const initialRange = useMemo(() => {
    const from = (searchParams.get("from") || "").trim();
    const to = (searchParams.get("to") || "").trim();
    const shape = /^d{4}-d{2}-d{2}Td{2}:d{2}$/;
    if (shape.test(from) && shape.test(to) && from < to) return { from, to, custom: true };
    return { from: initialWindow.from, to: initialWindow.to, custom: false };
    // read once, on arrival: later edits come from the controls, not the URL
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The agents the report covers, by name. Every agent is ticked once the
  // list arrives (see below), unless the URL named specific ones.
  const [selectedNames, setSelectedNames] = useState(() => new Set(initialAgents));
  const [agentPickerOpen, setAgentPickerOpen] = useState(false);
  // a window that came in on the URL is by definition not one of the presets
  const [preset, setPreset] = useState(initialRange.custom ? "custom" : "12");
  const [fromLocal, setFromLocal] = useState(initialRange.from);
  const [toLocal, setToLocal] = useState(initialRange.to);
  const [bucket, setBucket] = useState(() =>
    defaultBucket(spanHours(initialRange.from, initialRange.to))
  );

  const [agents, setAgents] = useState([]);
  const [agentsLoading, setAgentsLoading] = useState(true);
  const [activeTab, setActiveTab] = useState("overview");
  const [status, setStatus] = useState("idle"); // idle | loading | success | error
  const [report, setReport] = useState(null);
  // The parameters the report on screen was actually built from — the controls
  // can be edited without pressing Generate, so they are not the same thing.
  const [loadedParams, setLoadedParams] = useState(null);
  // One entry per request: "loading" | "ok" | "error". This is what the loader
  // strip renders, and what tells a still-loading domain from a failed one.
  const [sectionStatus, setSectionStatus] = useState({});
  const [sectionErrors, setSectionErrors] = useState([]);
  const [errorMsg, setErrorMsg] = useState("");
  const [exporting, setExporting] = useState(false);

  const abortRef = useRef(null);
  const runRef = useRef(0);
  const pickerRef = useRef(null);

  const allSelected = agents.length > 0 && selectedNames.size === agents.length;

  // A dropdown that stays open once the pointer has moved on is a nuisance,
  // and Escape is what people try first.
  useEffect(() => {
    if (!agentPickerOpen) return undefined;
    const onDown = (e) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target)) {
        setAgentPickerOpen(false);
      }
    };
    const onKey = (e) => {
      if (e.key === "Escape") setAgentPickerOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [agentPickerOpen]);

  /**
   * The agents to actually name in the request.
   *
   * Every agent ticked is the default scope, and the API reads a missing
   * `agent_name` as every agent — so nothing is sent rather than listing the
   * whole estate back at it. Untick any agent and the rest are sent as a list.
   */
  const scopeNames = useCallback(
    (names) => (agents.length > 0 && names.length === agents.length ? [] : names),
    [agents.length]
  );

  /** The current controls as the API's parameter object. */
  const toParams = useCallback(
    () => ({
      agentNames: scopeNames([...selectedNames]),
      fromDt: istInputToApi(fromLocal, "00"),
      toDt: istInputToApi(toLocal, "59"),
      bucket,
    }),
    [scopeNames, selectedNames, fromLocal, toLocal, bucket]
  );

  /**
   * Run the five reports for one window.
   *
   * Each request has its own loader: the strip under the toolbar shows all five
   * spinning, and every domain is rebuilt the moment its own response lands, so
   * the fast reports are readable while the slow ones are still running.
   */
  const load = useCallback(async (params) => {
    if (!params.fromDt || !params.toDt) {
      setErrorMsg("Pick a valid From and To date/time.");
      setStatus("error");
      return;
    }
    if (params.fromDt >= params.toDt) {
      setErrorMsg("From must be before To.");
      setStatus("error");
      return;
    }

    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    // A superseded run must not write over the current one's state.
    const runId = runRef.current + 1;
    runRef.current = runId;
    const isCurrent = () => runRef.current === runId;

    setStatus("loading");
    setErrorMsg("");
    setSectionErrors([]);
    setSectionStatus(SOC2_SECTIONS.reduce((acc, s) => ({ ...acc, [s]: "loading" }), {}));
    setLoadedParams(params);
    // the shell, with every domain marked pending, so loaders show immediately
    setReport(buildSoc2View({}, params, { pending: SOC2_SECTIONS }));

    const arrived = {};
    const waiting = new Set(SOC2_SECTIONS);

    try {
      const { errors } = await fetchSoc2Report(params, {
        signal: controller.signal,
        onSection: ({ section, data, error }) => {
          if (!isCurrent()) return;
          waiting.delete(section);
          if (data) arrived[section] = data;
          setSectionStatus((prev) => ({ ...prev, [section]: error ? "error" : "ok" }));
          setReport(buildSoc2View(arrived, params, { pending: Array.from(waiting) }));
        },
      });
      if (!isCurrent()) return;

      setSectionErrors(errors);

      // Every one of the five failed — there is nothing to render.
      if (errors.length === SOC2_SECTIONS.length) {
        setReport(null);
        setErrorMsg(errors[0].message);
        setStatus("error");
        return;
      }

      setStatus("success");
    } catch (err) {
      if (isCanceled(err) || !isCurrent()) return;
      setErrorMsg(err.message || "Failed to generate the report.");
      setStatus("error");
    }
  }, []);

  // First render loads the default window (last 12 hours) for whichever agent
  // the URL named — every agent when it named none — so the page shows real data
  // without waiting on a click.
  useEffect(() => {
    load({
      agentNames: initialAgents,
      fromDt: istInputToApi(initialRange.from, "00"),
      toDt: istInputToApi(initialRange.to, "59"),
      bucket: defaultBucket(spanHours(initialRange.from, initialRange.to)),
    });
    return () => {
      if (abortRef.current) abortRef.current.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The agent picker and the Agents tab. Failure here is not fatal: the picker
  // is then empty, and sending no agent name means "all agents" anyway.
  useEffect(() => {
    let alive = true;
    setAgentsLoading(true);
    fetchAgents()
      .then((list) => {
        if (!alive) return;
        setAgents(list);
        // Default is everything ticked. A URL that named agents wins, so a
        // link from the dashboard still arrives scoped to what it asked for.
        if (initialAgents.length === 0) {
          setSelectedNames(new Set(list.map((a) => a.name)));
        }
      })
      .catch(() => {})
      .finally(() => {
        if (alive) setAgentsLoading(false);
      });
    return () => {
      alive = false;
    };
    // initialAgents is read once, on arrival — see the useMemo above
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const applyPreset = (hours) => {
    const next = lastHoursInputs(hours);
    setPreset(String(hours));
    setFromLocal(next.from);
    setToLocal(next.to);
    setBucket(defaultBucket(hours));
  };

  /**
   * Mirror the scope — agent and window — into the URL, so a refresh or a shared
   * link keeps it, and so a window that arrived on the URL cannot go stale once
   * the controls move on.
   */
  const syncUrl = useCallback(
    (names, from, to) => {
      const next = new URLSearchParams(searchParams);
      next.delete("agent_name"); // normalise the alias away
      next.delete("agent");
      // Every agent selected is the default, so it is left off the URL — the
      // link stays short and still means the same thing.
      const list = names || [];
      if (list.length && list.length !== agents.length) {
        list.forEach((n) => next.append("agent", n));
      }
      if (from && to) {
        next.set("from", from);
        next.set("to", to);
      }
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams, agents.length]
  );

  const onSubmit = (e) => {
    e.preventDefault();
    const params = toParams();
    syncUrl(params.agentNames, fromLocal, toLocal);
    load(params);
  };

  /**
   * Clicking a row in the Agents table narrows the report to that agent alone,
   * which is the useful move from a list you are reading. Clicking the row that
   * is already the only one selected goes back to every agent.
   */
  const selectAgent = (agent) => {
    const only = selectedNames.size === 1 && selectedNames.has(agent.name);
    const next = only ? agents.map((a) => a.name) : [agent.name];
    setSelectedNames(new Set(next));
    syncUrl(next, fromLocal, toLocal);
    load({ ...toParams(), agentNames: scopeNames(next) });
  };

  /** Tick / untick one agent in the picker. */
  const toggleAgent = (name) => {
    setSelectedNames((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  /** One click for the whole list — the picker's header control. */
  const toggleAllAgents = () => {
    setSelectedNames((prev) =>
      prev.size === agents.length ? new Set() : new Set(agents.map((a) => a.name))
    );
  };

  /** Left/right (and Home/End) move between tabs, as a tablist should. */
  const onTabKeyDown = (e) => {
    const keys = TABS.map((t) => t.key);
    const i = keys.indexOf(activeTab);
    let next = null;
    if (e.key === "ArrowRight") next = keys[(i + 1) % keys.length];
    else if (e.key === "ArrowLeft") next = keys[(i - 1 + keys.length) % keys.length];
    else if (e.key === "Home") next = keys[0];
    else if (e.key === "End") next = keys[keys.length - 1];
    if (!next) return;
    e.preventDefault();
    setActiveTab(next);
    const el = document.getElementById(`soc2-tab-${next}`);
    if (el) el.focus();
  };

  const handleExportPdf = () => {
    if (!report) return;
    setExporting(true);
    window.requestAnimationFrame(() => {
      window.print();
      setExporting(false);
    });
  };

  const loading = status === "loading";
  const periodText = `${fromLocal.replace("T", " ")} – ${toLocal.replace("T", " ")} IST`;

  // What the report on screen covers: the loaded parameters once there is a
  // report, the pending controls before that.
  const scopeAgent = loadedParams
    ? agentScopeLabel(loadedParams.agentNames, agents.length)
    : agentScopeLabel([...selectedNames], agents.length);
  const scopeWindow = loadedParams
    ? `${fmtIst(loadedParams.fromDt)} – ${fmtIst(loadedParams.toDt)} IST`
    : periodText;
  const scopeBucket = `${loadedParams ? loadedParams.bucket : bucket}ly buckets`;
  const scopeText = `${scopeAgent} · ${scopeWindow} · ${scopeBucket}`;
  // How many of the five requests are still open. Each tab shows its own state;
  // this is only for the one-line count next to the scope.
  const pendingCount = report ? report.summary.pendingCount : 0;

  const renderTab = () => {
    if (status === "error" && !report) {
      return (
        <div className="soc2-empty-state">
          <div className="soc2-empty-title soc2-error-title">Could not load report</div>
          <div className="soc2-empty-sub">{errorMsg}</div>
          <button className="soc2-btn soc2-btn-primary" onClick={() => load(toParams())}>
            Retry
          </button>
        </div>
      );
    }

    if (!report) {
      return (
        <div className="soc2-empty-state">
          <div className="soc2-empty-icon">
            <ShieldIcon />
          </div>
          <div className="soc2-empty-title">{loading ? "Generating report…" : "No report generated yet"}</div>
          <div className="soc2-empty-sub">Set the agent and window, then click Generate.</div>
        </div>
      );
    }

    // How many of the five are still open — each tab shows its own loader off
    // this, rather than blanking the whole page while one request finishes.
    const pending = report.summary.pendingCount;
    const nothingYet = report.summary.loadedCount === 0;

    switch (activeTab) {
      case "overview":
        return (
          <OverviewTab
            summary={report.summary}
            views={report.views}
            onOpenDomain={setActiveTab}
            loading={nothingYet && pending > 0}
          />
        );
      case "incidents":
        return (
          <div className="soc2-tab-body">
            <PendingNote pending={pending} what="incidents" />
            <IncidentsTable incidents={report.incidents} loading={nothingYet && pending > 0} />
          </div>
        );
      case "agents":
        return (
          <AgentsTab
            agents={agents}
            loading={agentsLoading}
            onSelect={selectAgent}
            selectedNames={selectedNames}
          />
        );
      case "recommendations":
        return (
          <div className="soc2-tab-body">
            <PendingNote pending={pending} what="findings" />
            <RecommendationsTab items={report.recommendations} loading={nothingYet && pending > 0} />
          </div>
        );
      default:
        return <SectionView view={report.views[activeTab]} />;
    }
  };

  return (
    <div className="soc2-page">
      {/* ── top bar ── */}
      <div className="soc2-topbar">
        <div className="soc2-title">
          {/* the app header already carries the brand, so this names the page */}
          <span className="soc2-title-icon">
            <ShieldIcon />
          </span>
          <span className="soc2-title-text">
            <span className="soc2-title-main">SOC2 report</span>
            <span className="soc2-title-sub">
              Trust service criteria evidence · times in IST
            </span>
          </span>
        </div>

        <form className="soc2-controls" onSubmit={onSubmit}>
          <div className="soc2-field soc2-agentpicker" ref={pickerRef}>
            <span className="soc2-field-label">Agents</span>
            <button
              type="button"
              className="soc2-input soc2-agentpicker-toggle"
              onClick={() => setAgentPickerOpen((open) => !open)}
              aria-expanded={agentPickerOpen}
              aria-haspopup="true"
              disabled={agentsLoading && agents.length === 0}
            >
              <span className="soc2-agentpicker-value">
                {agentsLoading && agents.length === 0
                  ? "Loading agents…"
                  : agentScopeLabel([...selectedNames], agents.length)}
              </span>
              <span className="soc2-agentpicker-caret" aria-hidden="true">
                ▾
              </span>
            </button>

            {agentPickerOpen && (
              <div className="soc2-agentpicker-menu" role="group" aria-label="Agents to report on">
                <div className="soc2-agentpicker-head">
                  <label className="soc2-agentpicker-row">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      // some but not all: a dash rather than a tick
                      ref={(el) => {
                        if (el)
                          el.indeterminate =
                            !allSelected && selectedNames.size > 0;
                      }}
                      onChange={toggleAllAgents}
                    />
                    <span className="soc2-agentpicker-name">
                      {allSelected ? "Clear all" : "Select all"}
                    </span>
                  </label>
                  <span className="soc2-agentpicker-count">
                    {selectedNames.size}/{agents.length}
                  </span>
                </div>

                <div className="soc2-agentpicker-list">
                  {agents.length === 0 ? (
                    <div className="soc2-agentpicker-empty">No agents registered.</div>
                  ) : (
                    agents.map((a) => (
                      <label key={a.id} className="soc2-agentpicker-row">
                        <input
                          type="checkbox"
                          checked={selectedNames.has(a.name)}
                          onChange={() => toggleAgent(a.name)}
                        />
                        <span className="soc2-agentpicker-name" title={a.name}>
                          {a.name}
                        </span>
                        <span className={`soc2-agentpicker-dot soc2-dot-${a.status}`} />
                      </label>
                    ))
                  )}
                </div>
              </div>
            )}
          </div>

          <label className="soc2-field">
            <span className="soc2-field-label">Range</span>
            <select
              className="soc2-select"
              value={preset}
              onChange={(e) =>
                e.target.value === "custom" ? setPreset("custom") : applyPreset(Number(e.target.value))
              }
            >
              {PRESET_HOURS.map((h) => (
                <option key={h} value={String(h)}>
                  {presetLabel(h)}
                </option>
              ))}
              <option value="custom">Custom…</option>
            </select>
          </label>

          {/* the two date fields are the rare case, so they stay out of the way
              until the range is actually set by hand */}
          {preset === "custom" && (
            <>
              <label className="soc2-field">
                <span className="soc2-field-label">From (IST)</span>
                <input
                  type="datetime-local"
                  className="soc2-date"
                  value={fromLocal}
                  max={toLocal}
                  onChange={(e) => setFromLocal(e.target.value)}
                />
              </label>

              <label className="soc2-field">
                <span className="soc2-field-label">To (IST)</span>
                <input
                  type="datetime-local"
                  className="soc2-date"
                  value={toLocal}
                  min={fromLocal}
                  onChange={(e) => setToLocal(e.target.value)}
                />
              </label>

              <label className="soc2-field">
                <span className="soc2-field-label">Buckets</span>
                <select
                  className="soc2-select"
                  value={bucket}
                  onChange={(e) => setBucket(e.target.value)}
                >
                  <option value="hour">Hourly</option>
                  <option value="day">Daily</option>
                </select>
              </label>
            </>
          )}

          <div className="soc2-actions">
            <button
  className="soc2-btn soc2-btn-primary"
  type="submit"
  disabled={loading}
>
  <LuPlay />
  {loading ? "Generating…" : "Generate"}
</button>
            <button
              className="soc2-btn"
              type="button"
              onClick={handleExportPdf}
              disabled={!report || exporting}
            >
              {exporting ? "…" : "Export PDF"}
            </button>
          </div>
        </form>
      </div>

      <div className="soc2-scope">
        <span className="soc2-scope-label">Agent</span>
        <strong className="soc2-scope-agent">{scopeAgent}</strong>
        <span className="soc2-scope-rest">
          {scopeWindow} · {scopeBucket}
        </span>
        {/* the per-report loaders live on the tabs; this is just the count */}
        {pendingCount > 0 && (
          <span className="soc2-scope-loading" role="status" aria-live="polite">
            <Spinner />
            Loading {pendingCount} of {SOC2_SECTIONS.length} reports…
          </span>
        )}
      </div>

      {/* A refresh that failed outright, with an earlier report still on screen. */}
      {status === "error" && report && (
        <div className="soc2-errorbar" role="alert">
          <span>{errorMsg} Showing the last window that loaded.</span>
          <button className="soc2-btn soc2-btn-sm" type="button" onClick={() => load(toParams())}>
            Retry
          </button>
        </div>
      )}

      {/* Sections that failed while others loaded — the report still renders. */}
      {sectionErrors.length > 0 && status !== "loading" && (
        <div className="soc2-warnbar" role="alert">
          {sectionErrors.map((e) => (
            <div className="soc2-warnbar-row" key={e.section}>
              <strong>{e.label}</strong> report unavailable
              {e.status ? ` (HTTP ${e.status})` : ""} — {e.message}
            </div>
          ))}
        </div>
      )}

      {/* ── tab bar — each domain tab carries its own request's state ── */}
      <div className="soc2-tabbar" role="tablist" aria-label="Report sections" onKeyDown={onTabKeyDown}>
        {TABS.map((t) => {
          const state = sectionStatus[t.key];
          const selected = activeTab === t.key;
          return (
            <button
              key={t.key}
              id={`soc2-tab-${t.key}`}
              role="tab"
              type="button"
              aria-selected={selected}
              aria-controls="soc2-tabpanel"
              // only the active tab is in the tab order; the arrows move between
              // them, which is how a tablist is expected to behave
              tabIndex={selected ? 0 : -1}
              className={`soc2-tab ${selected ? "active" : ""}`}
              onClick={() => setActiveTab(t.key)}
            >
              {t.label}
              {state === "loading" && <Spinner label={`${t.label} report loading`} />}
              {state === "error" && (
                <span className="soc2-tab-dot" title="This report did not load" />
              )}
            </button>
          );
        })}
      </div>

      {/* ── tab content ── */}
      <div
        className="soc2-content"
        id="soc2-tabpanel"
        role="tabpanel"
        aria-labelledby={`soc2-tab-${activeTab}`}
      >
        {renderTab()}
      </div>

      {/* ── footer: what this page is showing, in one line ── */}
      <div className="soc2-exportbar">
        <span className="soc2-period">
          {report && report.meta.generatedAt !== "—"
            ? `Generated ${report.meta.generatedAt} IST · ${scopeWindow} · ${scopeBucket}`
            : `${scopeWindow} · ${scopeBucket}`}
        </span>
      </div>

      {/* ── printable full report (all tabs) — visible only when printing ── */}
      {report && (
        <PrintableReport report={report} agents={agents} scopeText={scopeText} />
      )}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════
//  Printable report — every tab stacked, shown only in print / PDF
// ════════════════════════════════════════════════════════════════

/**
 * The printed report is read by people who did not run it — an auditor, a
 * manager, someone new to the platform. Each section therefore opens with a
 * plain-English explanation of what it shows and how to read it, and starts on
 * its own page so a section can be pulled out and circulated on its own.
 */
function printSections(report, agents) {
  return [
    {
      title: "Overview",
      lead: `Everything the agents recorded in this window, summarised across the five monitored
        domains. The compliance figure is the average of the domain scores below; each domain
        starts at 100 and loses points in proportion to how many of its events were high
        severity, matched a threat indicator, were flagged as anomalous, or failed. A domain
        that recorded nothing at all is listed but not scored — an empty window is an absence
        of evidence, not proof that the control worked.`,
      body: <OverviewTab summary={report.summary} loading={false} />,
    },
    {
      title: "Access control (CC6)",
      lead: `Who signed in, from where, and what was refused. SOC 2 criterion CC6 covers logical
        access. The evidence here is the balance of successful against failed authentication,
        the accounts and source addresses behind the failures, and every use of elevated
        (administrator or sudo) privilege in the window — each of which an auditor expects to
        tie back to an approved request.`,
      body: <SectionView view={report.views.auth} expanded />,
    },
    {
      title: "System operations (CC7)",
      lead: `What actually ran on the monitored hosts. CC7 covers system operation and
        monitoring: which programs executed, under which account, the resources they consumed,
        and anything the agent judged abnormal or matched against a threat indicator. CPU
        percentages are summed across processor cores, so figures above 100% are normal on a
        multi-core machine — the bars are drawn relative to the window's own peak.`,
      body: <SectionView view={report.views.process} expanded />,
    },
    {
      title: "Change management (CC8)",
      lead: `What changed on disk. CC8 covers change management. The evidence is which files
        were created, modified or deleted, in which directories, by which account, and which of
        those changes altered a file's contents or location in a way that warrants review.
        Unexpected changes to configuration or executable files are the ones to explain.`,
      body: <SectionView view={report.views.file} expanded />,
    },
    {
      title: "Network (CC9)",
      lead: `What the hosts talked to. CC9 covers risk mitigation at the network boundary:
        how many connections were made, to which addresses and ports, how much data moved in
        each direction, and which destinations were public rather than internal. Reconcile the
        external destinations against whatever egress is approved for these machines.`,
      body: <SectionView view={report.views.network} expanded />,
    },
    {
      title: "Removable media",
      lead: `USB drives and other removable devices: which were attached, by vendor, model and
        serial number, and how much data moved to or from them. This is the one channel by
        which data can leave a host without crossing the network, so any transfer here should
        match an authorised business need under the data-handling policy.`,
      body: <SectionView view={report.views.usb} expanded />,
    },
    {
      title: "Incidents",
      lead: `Every notable and anomalous event from the five domains, merged and listed newest
        first. "Notable" means the agent rated the event high or critical; "anomalous" means it
        departed from the host's normal pattern. Each row names the domain it came from and,
        where the agent identified one, the MITRE ATT&CK technique — the industry catalogue of
        attacker behaviours.`,
      body: <IncidentsTable incidents={report.incidents} loading={false} />,
    },
    {
      title: "Agents",
      lead: `The agents registered with the platform and the state of their connection. This is
        the coverage statement for everything above: an agent that was offline reported nothing,
        so any window overlapping its downtime has gaps that the counts in this report cannot
        show.`,
      body: <AgentsTab agents={agents} loading={false} />,
    },
    {
      title: "Recommendations",
      lead: `Findings derived from this window's own numbers, most serious first. These are
        prompts for review rather than conclusions: each one names a figure an auditor would
        expect to see explained, evidenced, or remediated before the next reporting period.`,
      body: <RecommendationsTab items={report.recommendations} loading={false} />,
    },
  ];
}

// function PrintableReport({ report, agents, scopeText }) {
//   const sections = printSections(report, agents);

//   return (
//     <div className="soc2-print-root" aria-hidden="true">
//       {/* ── cover page ── */}
//       <section className="soc2-print-cover">
//         <div className="soc2-print-header">
//           <div className="soc2-print-brand">
//             <ShieldIcon />
//             <span className="soc2-print-title">Guardlynx — SOC 2 evidence report</span>
//           </div>
//           <div className="soc2-print-period">{scopeText}</div>
//         </div>

//         <p className="soc2-print-lead">
//           This report collects the activity the Guardlynx agents recorded for the period above,
//           arranged against the SOC 2 trust service criteria. It is generated from the agents'
//           own telemetry — nothing in it is entered by hand — and each section states what it
//           covers and how to read it. All times are India Standard Time (UTC+5:30).
//         </p>

//         <div className="soc2-print-meta">
//           <div>
//             <span className="soc2-print-meta-key">Scope</span>
//             <span className="soc2-print-meta-val">{report.meta.agentName || "All agents"}</span>
//           </div>
//           <div>
//             <span className="soc2-print-meta-key">Generated</span>
//             <span className="soc2-print-meta-val">
//               {report.meta.generatedAt !== "—" ? `${report.meta.generatedAt} IST` : "—"}
//             </span>
//           </div>
//           <div>
//             <span className="soc2-print-meta-key">Events</span>
//             <span className="soc2-print-meta-val">{fmtInt(report.summary.totalEvents)}</span>
//           </div>
//         </div>

//         <div className="soc2-print-toc">
//           <div className="soc2-print-toc-head">Contents</div>
//           <ol className="soc2-print-toc-list">
//             {sections.map((s) => (
//               <li key={s.title}>{s.title}</li>
//             ))}
//           </ol>
//         </div>
//       </section>

//       {sections.map((s, i) => (
//         <PrintSection key={s.title} number={i + 1} title={s.title} lead={s.lead}>
//           {s.body}
//         </PrintSection>
//       ))}
//     </div>
//   );
// }

function PrintableReport({ report, agents, scopeText }) {
  const sections = printSections(report, agents);

  return (
    <div className="soc2-print-root" aria-hidden="true">

      

      {/* =========================================================
          COVER PAGE
      ========================================================= */}
      <section className="soc2-print-cover">

        <div className="soc2-print-cover-top">
          <div className="soc2-print-brand">
            <span className="soc2-print-logo">
              <ShieldIcon />
            </span>

            <div>
              <div className="soc2-print-brand-name">
                GUARDLYNX
              </div>

              <div className="soc2-print-brand-subtitle">
                Security & Compliance Monitoring
              </div>
            </div>
          </div>

          <div className="soc2-print-classification">
            CONFIDENTIAL
          </div>
        </div>

        <div className="soc2-print-cover-main">

          <div className="soc2-print-eyebrow">
            COMPLIANCE EVIDENCE REPORT
          </div>

          <h1 className="soc2-print-cover-title">
            SOC 2 Evidence Report
          </h1>

          <p className="soc2-print-cover-description">
            Security and compliance evidence collected from
            Guardlynx monitored agents during the selected
            reporting period.
          </p>

          <div className="soc2-print-period-card">
            <div className="soc2-print-period-label">
              REPORTING PERIOD
            </div>

            <div className="soc2-print-period-value">
              {scopeText}
            </div>
          </div>

          <div className="soc2-print-meta-grid">

            <div className="soc2-print-meta-box">
              <span className="soc2-print-meta-key">
                Agent Scope
              </span>

              <span className="soc2-print-meta-val">
                {report.meta.agentName || "All agents"}
              </span>
            </div>

            <div className="soc2-print-meta-box">
              <span className="soc2-print-meta-key">
                Generated
              </span>

              <span className="soc2-print-meta-val">
                {report.meta.generatedAt !== "—"
                  ? `${report.meta.generatedAt} IST`
                  : "—"}
              </span>
            </div>

            <div className="soc2-print-meta-box">
              <span className="soc2-print-meta-key">
                Total Events
              </span>

              <span className="soc2-print-meta-val">
                {fmtInt(report.summary.totalEvents)}
              </span>
            </div>

            <div className="soc2-print-meta-box">
              <span className="soc2-print-meta-key">
                Timezone
              </span>

              <span className="soc2-print-meta-val">
                IST (UTC+5:30)
              </span>
            </div>

          </div>

        </div>

        <div className="soc2-print-cover-bottom">
          <div>
            Generated from Guardlynx agent telemetry.
          </div>

          <div>
            SOC 2 Evidence Report
          </div>
        </div>

      </section>


      {/* =========================================================
          EXECUTIVE SUMMARY
      ========================================================= */}
      <PrintExecutiveSummary report={report} />


      {/* =========================================================
          TABLE OF CONTENTS
      ========================================================= */}
      <section className="soc2-print-toc-page">

        <div className="soc2-print-page-eyebrow">
          REPORT STRUCTURE
        </div>

        <h2 className="soc2-print-toc-title">
          Contents
        </h2>

        <p className="soc2-print-toc-description">
          Sections included in this SOC 2 evidence report.
        </p>

        <ol className="soc2-print-toc-list">

          <li>
            <span className="soc2-toc-number">01</span>
            <span className="soc2-toc-label">
              Executive Summary
            </span>
          </li>

          {sections.map((s, i) => (
            <li key={s.title}>
              <span className="soc2-toc-number">
                {String(i + 2).padStart(2, "0")}
              </span>

              <span className="soc2-toc-label">
                {s.title}
              </span>
            </li>
          ))}

        </ol>

      </section>


      {/* =========================================================
          REPORT SECTIONS
      ========================================================= */}
      {sections.map((s, i) => (
        <PrintSection
          key={s.title}
          number={i + 2}
          title={s.title}
          lead={s.lead}
        >
          {s.body}
        </PrintSection>
      ))}

    </div>
  );
}

function PrintExecutiveSummary({ report }) {
  const summary = report?.summary || {};

  const compliance = summary.complianceScore;

  const complianceStatus =
    compliance == null
      ? "No score available"
      : compliance >= 90
        ? "Good standing"
        : compliance >= 75
          ? "Needs attention"
          : "Critical attention";

  return (
    <section className="soc2-print-executive">

      <div className="soc2-print-page-eyebrow">
        01 · EXECUTIVE SUMMARY
      </div>

      <h2 className="soc2-print-executive-title">
        Executive Summary
      </h2>

      <p className="soc2-print-executive-lead">
        This summary provides a high-level view of the security
        and compliance evidence recorded during the selected
        reporting period.
      </p>


      {/* =======================================================
          KPI AREA
      ======================================================= */}
      <div className="soc2-print-kpi-grid">

        <div className="soc2-print-kpi">
          <span className="soc2-print-kpi-label">
            Total Events
          </span>

          <strong className="soc2-print-kpi-value">
            {fmtInt(summary.totalEvents)}
          </strong>

          <span className="soc2-print-kpi-sub">
            Across all monitored domains
          </span>
        </div>


        <div className="soc2-print-kpi">
          <span className="soc2-print-kpi-label">
            High Severity
          </span>

          <strong className="soc2-print-kpi-value">
            {fmtInt(summary.highSeverity)}
          </strong>

          <span
            className={`soc2-print-kpi-sub ${
              Number(summary.highSeverity)
                ? "danger"
                : "success"
            }`}
          >
            {Number(summary.highSeverity)
              ? "Requires review"
              : "None recorded"}
          </span>
        </div>


        <div className="soc2-print-kpi">
          <span className="soc2-print-kpi-label">
            Anomalies
          </span>

          <strong className="soc2-print-kpi-value">
            {fmtInt(summary.anomalies)}
          </strong>

          <span className="soc2-print-kpi-sub">
            {fmtInt(summary.iocMatches)} IOC matches
          </span>
        </div>


        <div className="soc2-print-kpi soc2-print-kpi-score">
          <span className="soc2-print-kpi-label">
            Compliance
          </span>

          <strong className="soc2-print-kpi-score-value">
            {compliance != null
              ? `${compliance}%`
              : "—"}
          </strong>

          <span
            className={`soc2-print-kpi-sub ${
              compliance == null
                ? ""
                : compliance >= 90
                  ? "success"
                  : compliance >= 75
                    ? "warning"
                    : "danger"
            }`}
          >
            {complianceStatus}
          </span>
        </div>

      </div>


      {/* =======================================================
          COMPLIANCE OVERVIEW
      ======================================================= */}
      <div className="soc2-print-summary-grid">

        <div className="soc2-print-summary-card">

          <div className="soc2-print-card-eyebrow">
            COMPLIANCE OVERVIEW
          </div>

          <div className="soc2-print-compliance">

            <div className="soc2-print-compliance-score">
              {compliance != null
                ? `${compliance}%`
                : "—"}
            </div>

            <div className="soc2-print-compliance-copy">

              <strong>
                Overall compliance score
              </strong>

              <span>
                Mean score across scored trust service
                criteria for the selected reporting window.
              </span>

            </div>

          </div>

          {compliance != null && (
            <div className="soc2-print-compliance-track">
              <div
                className="soc2-print-compliance-fill"
                style={{
                  width: `${Math.max(
                    0,
                    Math.min(100, Number(compliance))
                  )}%`,
                }}
              />
            </div>
          )}

        </div>


        <div className="soc2-print-summary-card">

          <div className="soc2-print-card-eyebrow">
            REPORT SCOPE
          </div>

          <div className="soc2-print-scope-row">
            <span>Agent</span>

            <strong>
              {report.meta.agentName || "All agents"}
            </strong>
          </div>

          <div className="soc2-print-scope-row">
            <span>Events</span>

            <strong>
              {fmtInt(summary.totalEvents)}
            </strong>
          </div>

          <div className="soc2-print-scope-row">
            <span>Timezone</span>

            <strong>
              IST (UTC+5:30)
            </strong>
          </div>

          <div className="soc2-print-scope-row">
            <span>Generated</span>

            <strong>
              {report.meta.generatedAt !== "—"
                ? `${report.meta.generatedAt} IST`
                : "—"}
            </strong>
          </div>

        </div>

      </div>


      {/* =======================================================
          TRUST SERVICE CRITERIA
      ======================================================= */}
      <div className="soc2-print-summary-section">

        <div className="soc2-print-card-eyebrow">
          TRUST SERVICE CRITERIA
        </div>

        <h3 className="soc2-print-summary-heading">
          Compliance by monitored domain
        </h3>

        {(summary.criteria || []).length > 0 ? (
          <div className="soc2-print-criteria">
            <CriteriaScores scores={summary.criteria} />
          </div>
        ) : (
          <div className="soc2-print-empty">
            No scored domains were recorded in this window.
          </div>
        )}

      </div>


      {/* =======================================================
          NOTE
      ======================================================= */}
      {(summary.silentDomains || []).length > 0 && (
        <div className="soc2-print-summary-note">
          <strong>Note:</strong>{" "}
          The following domains were not scored because
          no events were recorded during this window:{" "}
          {summary.silentDomains.join(", ")}.
        </div>
      )}

    </section>
  );
}

// function PrintSection({ number, title, lead, children }) {
//   return (
//     <section className="soc2-print-section">
//       <h2 className="soc2-print-h2">
//         {number}. {title}
//       </h2>
//       {lead && <p className="soc2-print-lead">{lead}</p>}
//       {children}
//     </section>
//   );
// }

function PrintSection({ number, title, lead, children }) {

  const sectionClass =
    title === "System operations (CC7)"
      ? "soc2-print-section soc2-print-section-process"
      : title === "Change management (CC8)"
        ? "soc2-print-section soc2-print-section-change"
        : "soc2-print-section";
  return (
    <section className={sectionClass}>

      <div className="soc2-print-section-number">
        {String(number).padStart(2, "0")}
      </div>

      <div className="soc2-print-section-heading">

        <div className="soc2-print-page-eyebrow">
          SOC 2 EVIDENCE
        </div>

        <h2 className="soc2-print-h2">
          {title}
        </h2>

        {lead && (
          <p className="soc2-print-lead">
            {lead}
          </p>
        )}

      </div>

      <div className="soc2-print-section-body">
        {children}
      </div>

    </section>
  );
}

// ════════════════════════════════════════════════════════════════
//  Building blocks
// ════════════════════════════════════════════════════════════════

/**
 * A folded-away block — the detail that would otherwise bury the page.
 *
 * Native <details>, so it needs no state and stays keyboard- and
 * find-in-page-friendly. `open` starts it expanded; the printed report passes it
 * so paper carries the full evidence.
 */
function Disclosure({ title, hint, children, open = false }) {
  return (
    <details className="soc2-fold" open={open}>
      <summary className="soc2-fold-head">
        <span className="soc2-fold-chevron" aria-hidden="true" />
        <span className="soc2-fold-title">{title}</span>
        {hint && <span className="soc2-fold-hint">{hint}</span>}
      </summary>
      <div className="soc2-fold-body">{children}</div>
    </details>
  );
}

/** Small inline spinner, for anything that is waiting on a request. */
function Spinner({ label }) {
  return <span className="soc2-spinner" role="img" aria-label={label || "Loading"} />;
}

/**
 * "Still loading N of the five reports" — hidden once nothing is pending.
 *
 * Each request's own state lives on its tab; this only explains why a total on
 * the page is lower than it will be in a moment.
 */
function PendingNote({ pending, what = "data" }) {
  if (!pending) return null;
  return (
    <div className="soc2-note soc2-note-loading">
      <Spinner />
      Loading {pending} of {SOC2_SECTIONS.length} reports — {what} will fill in as they arrive.
    </div>
  );
}

function Section({ title, children, wide = false }) {
  return (
    <div className={`soc2-card ${wide ? "soc2-card-wide" : ""}`}>
      {title && <div className="soc2-card-title">{title}</div>}
      {children}
    </div>
  );
}

function StatRow({ children }) {
  return <div className="soc2-statrow">{children}</div>;
}

/** Top-N list from the API's {label, count} breakdowns, scaled to its own max. */
export function BreakdownList({ items = [], copyable = false }) {
  const [flash, copy] = useCopyFlash();
  const max = items.reduce((m, i) => Math.max(m, Number(i.count) || 0), 0);
  if (items.length === 0) return <div className="evt-empty">Nothing recorded.</div>;

  return (
    <div className="soc2-bd-list">
      {items.map((i, idx) => {
        // The API returns real rows with an empty label (890 process events with
        // no executable path, for one). Naming them keeps the row readable
        // instead of rendering a bar against blank space.
        const blank = String(i.label == null ? "" : i.label).trim() === "";
        const label = blank ? "(not reported)" : i.label;
        // "(not reported)" is a placeholder, not a path: nothing to copy.
        const canCopy = copyable && !blank;
        const id = `${idx}-${i.label}`;
        const state = flash.id === id ? (flash.ok ? "copied" : "failed") : "";

        const body = (
          <>
            <span
              className={`soc2-bd-label ${blank ? "soc2-bd-blank" : ""}`}
              title={String(label)}
            >
              {label}
            </span>
            <span className="soc2-bd-track">
              <span
                className="soc2-bd-fill"
                style={{ width: `${max ? ((Number(i.count) || 0) / max) * 100 : 0}%` }}
              />
            </span>
            <span className="soc2-bd-count">{fmtInt(i.count)}</span>
          </>
        );

        // The whole row is the target: the label is often short, and the bar
        // and the count are the easiest things on the row to aim at.
        if (!canCopy) {
          return (
            <div className="soc2-bd-row" key={`${i.label}-${idx}`}>
              {body}
            </div>
          );
        }

        return (
          <button
            type="button"
            key={`${i.label}-${idx}`}
            className={`soc2-bd-row soc2-copyrow ${state}`}
            onClick={() => copy(id, i.label)}
            aria-label={`Copy ${i.label}`}
          >
            {body}
            <span className="soc2-copy-flag" aria-hidden="true">
              {state === "copied" ? "✓" : state === "failed" ? "⚠" : "⧉"}
            </span>
          </button>
        );
      })}
    </div>
  );
}

/** Detail table for the row-level evidence (privileged access, transfers, …). */
// function DataTable({ columns = [], rows = [], maxRows = 50 }) {
//   const shown = rows.slice(0, maxRows);
//   if (rows.length === 0) return <div className="evt-empty">Nothing recorded.</div>;

//   return (
//     <div className="inc-table-wrap">
//       <table className="inc-table soc2-tbl">
//         <thead>
//           <tr>
//             {columns.map((c) => (
//               <th key={c.key}>{c.label}</th>
//             ))}
//           </tr>
//         </thead>
//         <tbody>
//           {shown.map((r, i) => (
//             <tr key={i}>
//               {columns.map((c) => (
//                 <td key={c.key} title={String(r[c.key] == null ? "" : r[c.key])}>
//                   {r[c.key] == null || r[c.key] === "" ? "—" : r[c.key]}
//                 </td>
//               ))}
//             </tr>
//           ))}
//         </tbody>
//       </table>
//       {rows.length > shown.length && (
//         <div className="soc2-tbl-more">
//           Showing {shown.length} of {fmtInt(rows.length)} rows.
//         </div>
//       )}
//     </div>
//   );
// }

export function DataTable({ columns = [], rows = [], maxRows = 50 }) {
  const shown = rows.slice(0, maxRows);
  // which cell just flashed, as `${row}-${column}`
  const [flash, copyCell] = useCopyFlash();

  if (rows.length === 0) {
    return <div className="evt-empty">Nothing recorded.</div>;
  }

  // The cells worth copying: paths, mount points, command lines, hashes and
  // serials. All of them are long, and all of them end up in a shell or a
  // ticket rather than being read off the screen.
  const isPathColumn = (key, label) => {
    const value = `${key || ""} ${label || ""}`.toLowerCase();

    return (
      value.includes("path") ||
      value.includes("file") ||
      value.includes("directory") ||
      value.includes("mount") ||
      value.includes("command") ||
      value.includes("sha") ||
      value.includes("hash") ||
      value.includes("serial") ||
      value.includes("ioc")
    );
  };

  const isSeverityColumn = (key, label) => {
    const value = `${key || ""} ${label || ""}`.toLowerCase();

    return (
      value.includes("severity") ||
      value === "severity" ||
      value.includes("priority")
    );
  };

  const isCategoryColumn = (key, label) => {
    const value = `${key || ""} ${label || ""}`.toLowerCase();

    return (
      value.includes("category") ||
      value.includes("domain")
    );
  };

  const normalizeSeverity = (value) => {
    const normalized = String(value || "")
      .trim()
      .toLowerCase();

    if (normalized === "critical") return "critical";
    if (normalized === "high") return "high";
    if (normalized === "medium") return "medium";
    if (normalized === "low") return "low";

    return "";
  };

  const normalizeCategory = (value) => {
    const normalized = String(value || "")
      .trim()
      .toLowerCase()
      .replace(/\s+/g, "-");

    if (
      normalized.includes("auth") ||
      normalized.includes("access")
    ) {
      return "auth";
    }

    if (normalized.includes("network")) {
      return "network";
    }

    if (
      normalized.includes("file") ||
      normalized.includes("change")
    ) {
      return "file";
    }

    if (
      normalized.includes("process") ||
      normalized.includes("system")
    ) {
      return "process";
    }

    if (
      normalized.includes("usb") ||
      normalized.includes("removable")
    ) {
      return "usb";
    }

    return "";
  };

  return (
    <div className="inc-table-wrap">
      <table className="inc-table soc2-tbl">

        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>

        <tbody>
          {shown.map((r, i) => (
            <tr key={i}>

              {columns.map((c) => {
                const rawValue = r[c.key];
                const displayValue =
                  rawValue == null || rawValue === ""
                    ? "—"
                    : rawValue;

                const severity =
                  isSeverityColumn(c.key, c.label)
                    ? normalizeSeverity(rawValue)
                    : "";

                const category =
                  isCategoryColumn(c.key, c.label)
                    ? normalizeCategory(rawValue)
                    : "";

                // `<key>Full` carries the value behind an abbreviated cell, when
                // the two differ (a USB transfer shows the file name, copies the
                // path). An empty one means the row does not have that variant,
                // so fall back on the cell itself — `??` would keep the blank and
                // leave the cell with neither a copy nor a tooltip.
                const fullValue = r[`${c.key}Full`] || rawValue;
                const copyable =
                  isPathColumn(c.key, c.label) &&
                  fullValue != null &&
                  fullValue !== "" &&
                  fullValue !== "—";
                const cellId = `${i}-${c.key}`;

                if (copyable) {
                  const state =
                    flash.id === cellId ? (flash.ok ? "copied" : "failed") : "";
                  return (
                    <td key={c.key} className="soc2-table-copy-cell">
                      <button
                        type="button"
                        className={`soc2-copy ${state}`}
                        onClick={() => copyCell(cellId, fullValue)}
                        title={String(fullValue)}
                      >
                        <span className="soc2-copy-text">{displayValue}</span>
                        <span className="soc2-copy-flag" aria-hidden="true">
                          {state === "copied" ? "✓" : state === "failed" ? "⚠" : "⧉"}
                        </span>
                      </button>
                      <span className="soc2-sr-only" aria-live="polite">
                        {state === "copied" ? `Copied ${fullValue}` : ""}
                      </span>
                    </td>
                  );
                }

                return (
                  <td
                    key={c.key}
                    title={String(fullValue)}
                    className={
                      severity
                        ? "soc2-table-severity-cell"
                        : category
                          ? "soc2-table-category-cell"
                          : ""
                    }
                  >
                    {severity ? (
                      <span
                        className={`sev-badge sev-${severity}`}
                      >
                        {String(rawValue).toUpperCase()}
                      </span>
                    ) : category ? (
                      <span
                        className={`cat-badge cat-${category}`}
                      >
                        {String(rawValue)}
                      </span>
                    ) : (
                      displayValue
                    )}
                  </td>
                );
              })}

            </tr>
          ))}
        </tbody>

      </table>

      {rows.length > shown.length && (
        <div className="soc2-tbl-more">
          Showing {shown.length} of {fmtInt(rows.length)} rows.
        </div>
      )}

    </div>
  );
}

function SkelLines({ n = 4 }) {
  return (
    <div className="soc2-skel-lines">
      {Array.from({ length: n }).map((_, i) => (
        <div key={i} className="stat-skel" style={{ height: 14, margin: "8px 0" }} />
      ))}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════
//  Tab bodies
// ════════════════════════════════════════════════════════════════

/**
 * One domain report (auth / process / file / network / usb). Every section comes
 * out of soc2Transform in the same shape, so this renders all five.
 */
function SectionView({ view, expanded = false }) {
  if (!view) return <SkelLines n={6} />;

  // This domain's own request is still open: skeletons in place of its numbers,
  // so the wait is visible on the tab itself and not only on the loader strip.
  if (view.pending) {
    return (
      <div className="soc2-tab-body soc2-domain-section">
        {/* <div className="soc2-section-head">{view.heading}</div> */}
        <div className="soc2-section-head soc2-domain-heading">
  <span className="soc2-domain-eyebrow">
    SOC 2 EVIDENCE
  </span>

  <span className="soc2-domain-title">
    {view.heading}
  </span>
</div>
        <div className="soc2-note soc2-note-loading">
          <Spinner />
          Loading this report…
        </div>

        <div className="soc2-print-domain-label">
  KEY METRICS
</div>
        <StatRow>
          {[0, 1, 2, 3].map((i) => (
            <StatCard key={i} loading />
          ))}
        </StatRow>
        <div className="soc2-two-col">
          <Section title="">
            <SkelLines n={4} />
          </Section>
          <Section title="">
            <SkelLines n={5} />
          </Section>
        </div>
      </div>
    );
  }

  // The domain's request failed. Showing its zeroed stats would read as a clean
  // control, so the numbers are withheld entirely.
  if (view.unavailable) {
    return (
      <div className="soc2-tab-body">
        <div className="soc2-section-head">{view.heading}</div>
        <div className="soc2-note">
          This domain's report did not load, so it is excluded from the scores and the
          incident list — treat the window as having no evidence for this control rather
          than as a clean result.{" "}
          <span className="soc2-screen-only">Click Generate to try the window again.</span>
        </div>
      </div>
    );
  }

  // Past the two branches above the payload is in hand, so nothing here is in a
  // loading state.
  const charts = (view.charts || []).filter((c) => c.series.some((s) => s.data.length > 0));

  return (
    <div className="soc2-tab-body">
      <div className="soc2-section-head">{view.heading}</div>

      <StatRow>
        {view.stats.map((s) => (
          <StatCard key={s.label} label={s.label} value={s.value} sub={s.sub} subColor={s.subColor} />
        ))}
      </StatRow>

      {view.total === 0 && (
        <div className="soc2-note">No events of this type were recorded in the selected window.</div>
      )}

      <div className="soc2-print-domain-label">
  KEY ACTIVITY
</div>

      <div className="soc2-two-col">
        <Section title={view.bars.title}>
          {view.bars.items.map((b) => (
            <ProgressBar
              key={b.label}
              label={b.label}
              value={b.value}
              count={b.count}
              colorOverride={b.colorOverride}
            />
          ))}
        </Section>
        <Section title={view.events.title}>
          <EventList events={view.events.items} maxItems={7} />
        </Section>
      </div>

      {charts.length > 0 && (
  <div className="soc2-print-domain-label">
    EVIDENCE
  </div>
)}

      {charts.map((c) => (
        <Section key={c.title} title={c.title} wide>
          <TimeSeriesChart series={c.series} yMax={c.yMax} unit={c.unit} height={200}  minimap={false}/>
        </Section>
      ))}

      {/* The detail is the bulk of the page, so it stays folded away until it is
          asked for — the printed report opens everything instead. */}
      {view.breakdowns.length > 0 && (
         <>
    <div className="soc2-print-domain-label">
      BREAKDOWNS
    </div>
        <Disclosure
          title="Breakdowns"
          hint={`${view.breakdowns.length} lists`}
          open={expanded}
        >
          <div className="soc2-card-grid">
            {view.breakdowns.map((b) => (
              <Section key={b.title} title={b.title}>
                {/* every breakdown row copies its label on click — paths,
                    users, techniques alike; they all get pasted somewhere */}
                <BreakdownList items={b.items} copyable />
              </Section>
            ))}
          </div>
        </Disclosure>
        </>
      )}

      {view.tables.length > 0 && (
  <div className="soc2-print-domain-label">
    EVIDENCE DETAILS
  </div>
)}

      {view.tables.map((t) => (
        <Disclosure
          key={t.title}
          title={t.title}
          hint={`${fmtInt(t.rows.length)} ${t.rows.length === 1 ? "row" : "rows"}`}
          open={expanded}
        >
          <DataTable columns={t.columns} rows={t.rows} />
        </Disclosure>
      ))}
    </div>
  );
}

/**
 * Overview — the landing tab.
 *
 * `views` and `onOpenDomain` are optional: the printed report has no tabs to
 * jump to, so it renders the same page without the domain shortcuts.
 */
function OverviewTab({ summary, views, onOpenDomain, loading }) {
  const m = summary || {};
  return (
    <div className="soc2-tab-body">
      <PendingNote pending={m.pendingCount} what="the totals and scores" />
      <StatRow>
        <StatCard loading={loading} label="Total events" value={fmtInt(m.totalEvents)} sub="all five domains" subColor="muted" />
        <StatCard
          loading={loading}
          label="High severity"
          value={fmtInt(m.highSeverity)}
          sub={Number(m.highSeverity) ? "needs review" : "none recorded"}
          subColor={Number(m.highSeverity) ? "danger" : "success"}
        />
        <StatCard
          loading={loading}
          label="Anomalies"
          value={fmtInt(m.anomalies)}
          sub={`${fmtInt(m.iocMatches)} IOC matches`}
          subColor={Number(m.anomalies) || Number(m.iocMatches) ? "warning" : "success"}
        />
        <StatCard
          loading={loading}
          label="Compliance"
          value={m.complianceScore != null ? `${m.complianceScore}%` : "—"}
          sub={m.complianceScore != null ? "mean of scored domains" : "no events to score"}
          subColor={
            m.complianceScore == null
              ? "muted"
              : m.complianceScore >= 90
                ? "success"
                : m.complianceScore >= 75
                  ? "warning"
                  : "danger"
          }
        />
      </StatRow>

      <div className="soc2-two-col">
        <Section title="Trust service criteria scores">
          {loading ? (
            <SkelLines n={5} />
          ) : (
            <>
              {(m.criteria || []).length > 0 ? (
                <CriteriaScores scores={m.criteria} />
              ) : (
                <div className="evt-empty">No domain recorded any events in this window.</div>
              )}
              {/* an empty domain is an absence of evidence, not a passing control */}
              {(m.silentDomains || []).length > 0 && (
                <div className="soc2-hint soc2-hint-foot">
                  Not scored — no events in this window: {m.silentDomains.join(", ")}.
                </div>
              )}
            </>
          )}
        </Section>
        <Section title={onOpenDomain ? "Domains — open one for the detail" : "Events by domain"}>
          {loading ? (
            <SkelLines n={5} />
          ) : onOpenDomain ? (
            <DomainList views={views} onOpen={onOpenDomain} />
          ) : (
            <BreakdownList items={m.byDomain || []} />
          )}
        </Section>
      </div>

      <Section title="Most recent notable events" wide>
        {loading ? <SkelLines n={6} /> : <EventList events={m.recentEvents || []} maxItems={10} />}
      </Section>
    </div>
  );
}

/**
 * The five domains as shortcuts: event count, state, and a click that opens the
 * domain's own tab. It replaces a plain "events by domain" bar list, which said
 * the same thing without going anywhere.
 */
function DomainList({ views = {}, onOpen }) {
  const domains = TABS.filter((t) => views[t.key]);
  if (domains.length === 0) return <div className="evt-empty">No domain reports yet.</div>;

  return (
    <div className="soc2-domains">
      {domains.map((t) => {
        const v = views[t.key];
        const state = v.pending ? "pending" : v.unavailable ? "error" : "ok";
        return (
          <button
            type="button"
            key={t.key}
            className="soc2-domain"
            onClick={() => onOpen(t.key)}
            title={`Open ${t.label}`}
          >
            <span className="soc2-domain-name">{t.label}</span>
            <span className="soc2-domain-meta">
              {state === "pending" && <Spinner />}
              {state === "error" && <span className="soc2-domain-failed">did not load</span>}
              {state === "ok" && (
                <>
                  <span className="soc2-domain-count">{fmtInt(v.total)} events</span>
                  {v.score != null && (
                    <span className={`soc2-domain-score soc2-domain-score-${scoreTone(v.score)}`}>
                      {v.score}%
                    </span>
                  )}
                </>
              )}
            </span>
            <span className="soc2-domain-go" aria-hidden="true" />
          </button>
        );
      })}
    </div>
  );
}

function scoreTone(score) {
  if (score >= 90) return "good";
  if (score >= 75) return "warn";
  return "bad";
}

function AgentsTab({ agents, loading, onSelect, selectedNames }) {
  const counts = useMemo(() => {
    const c = { total: agents.length, online: 0, degraded: 0, offline: 0 };
    agents.forEach((a) => {
      if (a.status === "online") c.online += 1;
      else if (a.status === "degraded") c.degraded += 1;
      else if (a.status === "offline") c.offline += 1;
    });
    return c;
  }, [agents]);

  return (
    <div className="soc2-tab-body">
      <div className="soc2-section-head">Agent status and health</div>
      <StatRow>
        <StatCard loading={loading} label="Total agents" value={counts.total} sub="registered" subColor="muted" />
        <StatCard loading={loading} label="Online" value={counts.online} sub="active" subColor="success" />
        <StatCard loading={loading} label="Pending" value={counts.degraded} sub="watch" subColor="warning" />
        <StatCard loading={loading} label="Offline" value={counts.offline} sub="needs attention" subColor="danger" />
      </StatRow>
      <Section title="Agent inventory" wide>
        {onSelect && (
          <div className="soc2-hint">
            {selectedNames && selectedNames.size === 1
              ? `Reporting on ${[...selectedNames][0]} only — click its row again to include every agent.`
              : "Click an agent to report on that agent only."}
          </div>
        )}
        <AgentsTable
          agents={agents}
          loading={loading}
          onSelect={onSelect}
          selectedNames={selectedNames}
        />
      </Section>
    </div>
  );
}

// function RecommendationsTab({ items, loading }) {
//   if (loading) return <SkelLines n={6} />;
//   return (
//     <div className="soc2-tab-body">
//       <div className="soc2-section-head">Recommendations and remediation</div>
//       <ol className="soc2-recs">
//         {(items || []).map((r, i) => (
//           <li key={i} className={`soc2-rec soc2-rec-${r.priority || "info"}`}>
//             <span className="soc2-rec-num">{String(i + 1).padStart(2, "0")}</span>
//             <span className="soc2-rec-text">{r.text}</span>
//           </li>
//         ))}
//       </ol>
//     </div>
//   );
// }
// function RecommendationsTab({ items, loading }) {
//   if (loading) return <SkelLines n={6} />;

//   const recommendations = items || [];

//   if (recommendations.length === 0) {
//     return (
//       <div className="soc2-tab-body">
//         <div className="soc2-section-head">
//           Recommendations and remediation
//         </div>

//         <div className="soc2-recommendation-empty">
//           No recommendations were generated for the selected reporting window.
//         </div>
//       </div>
//     );
//   }

//   return (
//     <div className="soc2-tab-body">
//       <div className="soc2-section-head">
//         Recommendations and remediation
//       </div>

//       <div className="soc2-recommendation-list">
//         {recommendations.map((r, i) => {
//           const priority = String(r.priority || "info").toLowerCase();

//           const priorityLabel =
//             priority === "critical"
//               ? "CRITICAL"
//               : priority === "high"
//                 ? "HIGH"
//                 : priority === "medium"
//                   ? "MEDIUM"
//                   : priority === "low"
//                     ? "LOW"
//                     : "INFO";

//           return (
//             <article
//               key={i}
//               className={`soc2-recommendation-card soc2-recommendation-${priority}`}
//             >
//               <div className="soc2-recommendation-top">
//                 <div className="soc2-recommendation-finding">
//                   FINDING {String(i + 1).padStart(2, "0")}
//                 </div>

//                 <span className={`soc2-recommendation-priority soc2-priority-${priority}`}>
//                   {priorityLabel}
//                 </span>
//               </div>

//               <div className="soc2-recommendation-divider" />

//               <div className="soc2-recommendation-block">
//                 <div className="soc2-recommendation-label">
//                   Finding
//                 </div>

//                 <div className="soc2-recommendation-text">
//                   {r.text || "No finding description available."}
//                 </div>
//               </div>

//               <div className="soc2-recommendation-block">
//                 <div className="soc2-recommendation-label">
//                   Recommended Action
//                 </div>

//                 <div className="soc2-recommendation-action">
//                   Review the finding, validate the underlying evidence, and
//                   take appropriate remediation action based on the identified
//                   risk.
//                 </div>
//               </div>
//             </article>
//           );
//         })}
//       </div>
//     </div>
//   );
// }

function RecommendationsTab({ items, loading }) {
  if (loading) return <SkelLines n={6} />;

  const recommendations = items || [];

  if (recommendations.length === 0) {
    return (
      <div className="soc2-tab-body">
        <div className="soc2-section-head">
          Recommendations and remediation
        </div>

        <div className="soc2-recommendation-empty">
          No recommendations were generated for the selected reporting window.
        </div>
      </div>
    );
  }

  return (
    <div className="soc2-tab-body">
      <div className="soc2-section-head">
        Recommendations and remediation
      </div>

      <div className="soc2-recommendation-list">
        {recommendations.map((r, i) => {
          const priority = String(r.priority || "info").toLowerCase();

          const priorityLabel =
            priority === "critical"
              ? "CRITICAL"
              : priority === "warning"
                ? "HIGH"
                : priority === "info"
                  ? "INFO"
                  : priority.toUpperCase();

          return (
            <article
              key={i}
              className={`soc2-recommendation-card soc2-recommendation-${priority}`}
            >
              <div className="soc2-recommendation-top">
                <div>
                  <div className="soc2-recommendation-finding">
                    FINDING {String(i + 1).padStart(2, "0")}
                  </div>

                  {r.category && (
                    <div className="soc2-recommendation-category">
                      {r.category}
                    </div>
                  )}
                </div>

                <span
                  className={`soc2-recommendation-priority soc2-priority-${priority}`}
                >
                  {priorityLabel}
                </span>
              </div>

              <div className="soc2-recommendation-divider" />

              <div className="soc2-recommendation-block">
                <div className="soc2-recommendation-label">
                  Finding
                </div>

                <div className="soc2-recommendation-text">
                  {r.finding || r.text || "No finding description available."}
                </div>
              </div>

              <div className="soc2-recommendation-block">
                <div className="soc2-recommendation-label">
                  Evidence
                </div>

                <div className="soc2-recommendation-evidence">
                  {r.evidence || "No supporting evidence was recorded."}
                </div>
              </div>

              <div className="soc2-recommendation-block">
                <div className="soc2-recommendation-label">
                  Recommended Action
                </div>

                <div className="soc2-recommendation-action">
                  {r.recommendedAction ||
                    "Review the finding and take appropriate remediation action."}
                </div>
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}
