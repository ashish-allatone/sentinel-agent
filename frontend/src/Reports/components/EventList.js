import React from "react";

import useCopyFlash from "../../hooks/useCopyFlash";

/**
 * props:
 *  - events  : [{ severity, message, messageFull, category, timestamp, copyValue }]
 *              `copyValue` is the raw path the message mentions; a row that
 *              carries one becomes click-to-copy, copying the path alone
 *              rather than the whole sentence.
 *  - maxItems: cap number rendered (default 5)
 */
const DOT_COLORS = {
  critical: "#d64545",
  high: "#e08b0a",
  medium: "#2b7fd0",
  low: "#5a9216",
};

export default function EventList({ events = [], maxItems = 5 }) {
  const [flash, copy] = useCopyFlash();
  const items = events.slice(0, maxItems);

  if (items.length === 0) {
    return <div className="evt-empty">No events for this period.</div>;
  }

  return (
    <ul className="evt-list">
      {items.map((e, i) => {
        const id = String(i);
        const state = flash.id === id ? (flash.ok ? "copied" : "failed") : "";

        const body = (
          <>
            <span
              className="evt-dot"
              style={{ background: DOT_COLORS[e.severity] || "#999" }}
            />
            <span className="evt-msg" title={e.messageFull || e.message}>{e.message}</span>
            {e.category && (
              <span className={`sev-badge cat-badge cat-${e.category}`}>{e.category}</span>
            )}
            <span className="evt-time">{e.timestamp}</span>
          </>
        );

        // A row that names a path is clickable end to end — the message is
        // elided, so aiming at the path itself is not a fair ask.
        if (!e.copyValue) {
          return (
            <li className="evt-item" key={i}>
              {body}
            </li>
          );
        }

        return (
          <li key={i}>
            <button
              type="button"
              className={`evt-item evt-copyrow ${state}`}
              onClick={() => copy(id, e.copyValue)}
              aria-label={`Copy ${e.copyValue}`}
              title={e.copyValue}
            >
              {body}
              <span className="soc2-copy-flag" aria-hidden="true">
                {state === "copied" ? "✓" : state === "failed" ? "⚠" : "⧉"}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
