import React from "react";

/**
 * props:
 *  - agents: [{ name, hostname, os, last_seen, status }]
 *            status: "online" | "degraded" | "offline"
 *  - loading
 *  - onSelect     : optional (agent) => void. When given, each row becomes a
 *                   button — the SOC2 report uses it to scope itself to one
 *                   agent. Omit it (as the printed report does) for a plain list.
 *  - selectedNames: Set (or array) of the scoped agents' names. Every row in it
 *                   is marked as pressed.
 */
const DOT = {
  online: "#5a9216",
  degraded: "#e08b0a",
  offline: "#d64545",
};

const BADGE_CLASS = {
  online: "agent-badge-online",
  degraded: "agent-badge-degraded",
  offline: "agent-badge-offline",
};

export default function AgentsTable({
  agents = [],
  loading = false,
  onSelect,
  selectedNames,
}) {
  const isSelected = (name) =>
    selectedNames instanceof Set
      ? selectedNames.has(name)
      : Array.isArray(selectedNames) && selectedNames.includes(name);

  if (loading) {
    return (
      <div className="agents-list">
        {[0, 1, 2, 3].map((i) => (
          <div className="agent-row" key={i}>
            <div className="stat-skel" style={{ height: 14, width: "100%" }} />
          </div>
        ))}
      </div>
    );
  }

  if (agents.length === 0) {
    return <div className="evt-empty">No agents registered.</div>;
  }

  return (
    <div className="agents-list">
      {agents.map((a, i) => {
        const body = (
          <>
            <span className="agent-dot" style={{ background: DOT[a.status] || "#999" }} />
            <span className="agent-name">{a.name}</span>
            <span className="agent-meta">
              {a.hostname} — {a.os} — last seen {a.last_seen}
            </span>
            <span className={`agent-badge ${BADGE_CLASS[a.status] || ""}`}>{a.status}</span>
          </>
        );

        if (!onSelect) {
          return (
            <div className="agent-row" key={a.id ?? i}>
              {body}
            </div>
          );
        }

        const selected = isSelected(a.name);
        return (
          <button
            type="button"
            key={a.id ?? i}
            className={`agent-row agent-row-btn ${selected ? "selected" : ""}`}
            onClick={() => onSelect(a)}
            aria-pressed={selected}
            title={`Report on ${a.name} only`}
          >
            {body}
          </button>
        );
      })}
    </div>
  );
}
