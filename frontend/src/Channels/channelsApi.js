/**
 * Communication channel endpoints — the destinations notifications are sent to.
 *
 *   GET    /communication-channels
 *   POST   /add-communication-channels                { type, name, value }
 *   DELETE /delete-communicaiton-channel?communication_channel_id=<id>
 *
 * All three answer with the project's standard envelope, so the body lives at
 * `res.data.data`. All three require a bearer token; add and delete additionally
 * require an admin role, and the server answers a non-admin with 401/403.
 *
 * There is no update endpoint — a channel can only be created and removed.
 *
 * @typedef {Object} Channel
 * @property {number} id
 * @property {string} type   service key, e.g. "email" / "slack"
 * @property {string} name   unique across ALL channels, not just within a type
 * @property {string} value  the destination itself
 */

import api, { absoluteApiUrl } from "../api/api";

export const CHANNELS_PATH = "/communication-channels";
export const ADD_CHANNEL_PATH = "/add-communication-channels";
// "communicaiton" is the spelling the server route actually uses.
export const DELETE_CHANNEL_PATH = "/delete-communicaiton-channel";

export const COMMUNICATION_TEST_CONFIG_PATH = "/communication/test-config";
export const COMMUNICATION_SEND_PATH = "/communication/send";

/** Error carrying what the UI needs to explain a failure. */
function channelsError(message, { status, url, cause } = {}) {
  const err = new Error(message);
  err.name = "ChannelsApiError";
  err.status = status;
  err.url = url;
  err.cause = cause;
  return err;
}

function messageForStatus(status, detail) {
  // The server's own detail is always the better message when it sends one —
  // a duplicate name, for instance, comes back as 401 with an explanation
  // rather than as an authentication problem.
  if (detail) return detail;
  if (status === 401 || status === 403) {
    return "Not allowed. Adding or removing channels needs an admin account.";
  }
  if (status === 404) return "That channel no longer exists.";
  if (status === 422) return "The server rejected these values. Check the fields and retry.";
  if (status >= 500) return "The server failed to answer. Retry in a moment.";
  return "The request failed.";
}

function toApiError(err, url) {
  if (err && (err.code === "ERR_CANCELED" || err.name === "CanceledError")) return err;
  const status = err?.response?.status;
  const detail = err?.response?.data?.detail || err?.response?.data?.message;
  return channelsError(messageForStatus(status, detail), { status, url, cause: err });
}

/**
 * Flatten the grouped response into one list.
 *
 * The endpoint groups by type — `[{ channel_type, channels: [{id, name, value}] }]`
 * — but every screen here works with a flat list and reads the type off each
 * channel, so the grouping is undone once, here.
 *
 * @returns {Channel[]}
 */
function flattenGroups(data) {
  const groups = (data && data.communication_channels) || [];
  const flat = [];
  for (const group of groups) {
    for (const channel of group.channels || []) {
      flat.push({
        id: channel.id,
        type: group.channel_type,
        name: channel.name,
        value: channel.value,
      });
    }
  }
  return flat;
}

/**
 * Every registered channel, newest first.
 *
 * @param {{ signal?: AbortSignal }} [options]
 * @returns {Promise<Channel[]>}
 */
export async function fetchChannels(options = {}) {
  const url = absoluteApiUrl(CHANNELS_PATH);
  try {
    const res = await api.get(CHANNELS_PATH, { signal: options.signal });
    // The server assigns ids in insertion order, so descending id is newest first.
    return flattenGroups(res.data && res.data.data).sort((a, b) => b.id - a.id);
  } catch (err) {
    throw toApiError(err, url);
  }
}

/**
 * Register a channel.
 *
 * `name` must be unique across every channel, whatever its type — the server
 * rejects a repeat with "Channel already exist with this name".
 *
 * @param {{ type: string, name: string, value: string }} channel
 * @returns {Promise<Channel>} the stored channel, including its new id
 */
export async function addChannel({ type, name, value }) {
  const url = absoluteApiUrl(ADD_CHANNEL_PATH);
  try {
    const res = await api.post(ADD_CHANNEL_PATH, { type, name, value });
    return (res.data && res.data.data) || null;
  } catch (err) {
    throw toApiError(err, url);
  }
}

/**
 * Remove a channel by id.
 *
 * @param {number} id
 * @returns {Promise<number>} the id that was removed
 */
export async function deleteChannel(id) {
  const url = absoluteApiUrl(`${DELETE_CHANNEL_PATH}?communication_channel_id=${id}`);
  try {
    await api.delete(DELETE_CHANNEL_PATH, {
      params: { communication_channel_id: id },
    });
    return id;
  } catch (err) {
    throw toApiError(err, url);
  }
}


// NEW FUNCTIONS BELOW

export async function fetchChannelReadiness(options = {}) {
  const url = absoluteApiUrl(COMMUNICATION_TEST_CONFIG_PATH);

  try {
    const res = await api.get(COMMUNICATION_TEST_CONFIG_PATH, {
      signal: options.signal,
    });

    return (res.data && res.data.data && res.data.data.channels) || [];
  } catch (err) {
    throw toApiError(err, url);
  }
}

export async function sendCommunication(payload) {
  const url = absoluteApiUrl(COMMUNICATION_SEND_PATH);

  try {
    const res = await api.post(COMMUNICATION_SEND_PATH, payload);

    return (res.data && res.data.data) || {};
  } catch (err) {
    throw toApiError(err, url);
  }
}