/**
 * SOC2 report endpoints — five per-domain reports over one shared time window.
 *
 *   GET /soc2-report/file     file integrity / change management
 *   GET /soc2-report/network  network boundary / data transfer
 *   GET /soc2-report/process  process execution / workload
 *   GET /soc2-report/auth     authentication / access control
 *   GET /soc2-report/usb      removable media
 *
 * Every one takes the same query: from_dt, to_dt, agent_name (optional, and
 * repeated once per agent — omit
 * for all agents) and bucket ("hour" | "day"). Each answers with the project's
 * standard envelope, so the report body is `res.data.data`.
 *
 * @typedef {Object} Soc2Params
 * @property {string} fromDt     naive ISO, no timezone, e.g. "2026-07-29T16:58:00"
 * @property {string} toDt       naive ISO, no timezone, e.g. "2026-07-30T04:58:59"
 * @property {string[]} [agentNames] the agents to report on, sent as one
 *   `agent_name` parameter each; empty/omitted means every agent
 * @property {string} [agentName] a single agent — shorthand for a one-item `agentNames`
 * @property {"hour"|"day"} [bucket] timeseries resolution (default "hour")
 */

import api, { absoluteApiUrl } from "../api/api";

/** The five report domains, in the order they are shown in the UI. */
export const SOC2_SECTIONS = ["auth", "process", "file", "network", "usb"];

export const SOC2_BASE_PATH = "/soc2-report";

/** Human label per section, used in error messages. */
export const SECTION_LABELS = {
  auth: "Authentication",
  process: "Process",
  file: "File integrity",
  network: "Network",
  usb: "Removable media",
};

/** @param {string} section */
export function sectionPath(section) {
  return `${SOC2_BASE_PATH}/${section}`;
}

/**
 * camelCase params -> the wire query object the endpoints expect.
 *
 * This is the ONLY place the snake_case names are produced, so the request and
 * the URL reported on failure cannot drift apart. `agent_name` is left out
 * entirely when no agent is selected — the API reads that as "all agents".
 *
 * @param {Soc2Params} params
 */
function toQuery(params) {
  const p = params || {};
  const query = new URLSearchParams();
  query.set("from_dt", p.fromDt ?? "");
  query.set("to_dt", p.toDt ?? "");
  // One `agent_name` per selected agent — the endpoints read them as a list.
  // Nothing selected sends nothing at all, which the API reads as every agent.
  agentNamesOf(p).forEach((name) => query.append("agent_name", name));
  query.set("bucket", p.bucket === "day" ? "day" : "hour");
  return query;
}

/**
 * The agents a params object scopes to, de-duplicated and blank-free.
 * Accepts either `agentNames` (the array) or the single-agent `agentName`.
 */
export function agentNamesOf(params) {
  const p = params || {};
  const list = Array.isArray(p.agentNames) ? p.agentNames : [];
  const all = p.agentName ? [...list, p.agentName] : list;
  return [...new Set(all.map((n) => String(n || "").trim()).filter(Boolean))];
}

/**
 * Wire query object -> query string.
 *
 * URLSearchParams percent-encodes the colons in the timestamps
 * (T16%3A58%3A00), which is what the endpoints' contract asks for; axios'
 * default serializer un-escapes them, so it is replaced with this.
 */
function serializeQuery(query) {
  return query instanceof URLSearchParams
    ? query.toString()
    : new URLSearchParams(query).toString();
}

/** Path + query, relative to the API base. */
export function sectionUrl(section, params) {
  return `${sectionPath(section)}?${serializeQuery(toQuery(params))}`;
}

/** The full URL, for showing the user exactly what failed. */
export function absoluteSectionUrl(section, params) {
  return absoluteApiUrl(sectionUrl(section, params));
}

function isCanceled(err) {
  return Boolean(err) && (err.code === "ERR_CANCELED" || err.name === "CanceledError");
}

/** Error carrying the bits the error state needs to render. */
function soc2Error(message, { section, status, url, cause }) {
  const err = new Error(message);
  err.name = "Soc2ApiError";
  err.section = section;
  err.status = status;
  err.url = url;
  err.cause = cause;
  return err;
}

function messageForStatus(status, detail) {
  if (detail) return detail;
  if (status === 400) return "The API rejected the window. Check that From is before To.";
  if (status === 401 || status === 403) return "Session expired. Sign in again, then retry.";
  if (status === 404) return "The report endpoint was not found on the server.";
  if (status === 422) return "The API rejected the parameters. Check the dates and the agent name.";
  if (status >= 500) return "The report service failed to answer. Retry, or try a shorter range.";
  return "The request failed.";
}

/**
 * Fetch one domain report.
 *
 * Goes through the project's shared axios client so the auth interceptor
 * attaches the bearer token.
 *
 * @param {string} section one of {@link SOC2_SECTIONS}
 * @param {Soc2Params} params
 * @param {{ signal?: AbortSignal }} [options]
 * @returns {Promise<Object>} the report body (the envelope's `data`)
 */
export async function fetchSection(section, params, options = {}) {
  const url = absoluteSectionUrl(section, params);
  try {
    const res = await api.get(sectionPath(section), {
      params: toQuery(params),
      paramsSerializer: { serialize: serializeQuery },
      signal: options.signal,
      // The report draws a loader per section; the app's blocking overlay would
      // cover them and stay up until the slowest of the five answered.
      skipGlobalLoader: true,
    });
    return (res.data && res.data.data) || {};
  } catch (cause) {
    if (isCanceled(cause)) throw cause;

    const status = cause && cause.response ? cause.response.status : 0;
    const detail = cause && cause.response && cause.response.data && cause.response.data.detail;
    const message = status
      ? messageForStatus(status, typeof detail === "string" ? detail : "")
      : "No response from the API. Check the network and that the service is up.";

    throw soc2Error(message, { section, status, url, cause });
  }
}

/**
 * Fetch all five domain reports for one window, concurrently.
 *
 * A section that fails does not sink the others: its slot comes back `null` and
 * the failure is listed in `errors`, so the report can render whatever loaded.
 * Cancellation is the one thing that propagates.
 *
 * `options.onSection` is called the moment each request settles, with
 * `{section, data}` or `{section, error}`. That is what drives the report's
 * per-request loaders — the caller does not have to wait for all five.
 *
 * @param {Soc2Params} params
 * @param {{ signal?: AbortSignal, onSection?: (result: {section: string, data?: Object, error?: Error}) => void }} [options]
 * @returns {Promise<{sections: Object<string, Object|null>, errors: Array<{section: string, label: string, message: string, status: number, url: string}>}>}
 */
export async function fetchSoc2Report(params, options = {}) {
  const report = (result) => {
    if (options.onSection) options.onSection(result);
    return result;
  };

  const settled = await Promise.all(
    SOC2_SECTIONS.map(async (section) => {
      try {
        return report({ section, data: await fetchSection(section, params, options) });
      } catch (err) {
        if (isCanceled(err)) throw err;
        return report({ section, error: err });
      }
    })
  );

  const sections = {};
  const errors = [];
  settled.forEach(({ section, data, error }) => {
    sections[section] = data || null;
    if (error) {
      errors.push({
        section,
        label: SECTION_LABELS[section] || section,
        message: error.message,
        status: error.status || 0,
        url: error.url || "",
      });
    }
  });

  return { sections, errors };
}

/**
 * The registered agents, for the agent picker.
 *
 * Best-effort: an empty list just means the picker offers no suggestions, and
 * the user can still type an agent name by hand.
 *
 * @param {{ signal?: AbortSignal }} [options]
 * @returns {Promise<Array<{id: number|string, name: string, hostname: string, os: string, status: string, last_seen: string}>>}
 */
export async function fetchAgents(options = {}) {
  try {
    // The Agents tab renders its own skeleton rows while this is open.
    const res = await api.get("/get-agents", {
      signal: options.signal,
      skipGlobalLoader: true,
    });
    const list = (res.data && res.data.data && res.data.data.agents) || [];
    return list.map((a, i) => ({
      id: a.id ?? i,
      name: a.agent_name || `agent_${i}`,
      hostname: a.host_name || a.main_ip || "unknown-host",
      os: `${a.os || ""} ${a.release || ""}`.trim() || "—",
      // /get-agents reports connection state, not a timestamp.
      last_seen: a.status || "unknown",
      status: agentStatus(a),
    }));
  } catch (err) {
    if (isCanceled(err)) throw err;
    return [];
  }
}

/** Backend connection state -> the three states AgentsTable renders. */
function agentStatus(agent) {
  const raw = String((agent && agent.status) || "").toLowerCase();
  if (raw === "active") return "online";
  if (raw === "pending") return "degraded";
  if (raw === "disconnected" || raw === "never_connected") return "offline";
  return agent && agent.is_active ? "online" : "offline";
}
