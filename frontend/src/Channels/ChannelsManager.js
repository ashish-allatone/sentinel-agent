import React, { useCallback, useEffect, useMemo, useState } from "react";
import "./ChannelsManager.css";
import { addChannel, deleteChannel, fetchChannels } from "./channelsApi";

import {
  addChannelAccount,
  deleteChannelAccount,
  fetchChannelAccounts,
} from "./channelAccountsApi";

/**
 * Channels Manager — backed by the communication-channel API.
 *
 * Users register notification channels. They pick a service (Email, WhatsApp,
 * Slack, …); the form then shows exactly the fields that service needs; they
 * register it. Registered channels can be viewed and removed, any number of
 * them, and they live on the server rather than in this browser.
 *
 * The API stores three strings per channel — type, name, value — while a
 * service here can need several fields, so `packValues` / `unpackValue` below
 * are the one place that translates between the two shapes.
 *
 * There is no update endpoint, so channels are add-and-remove only.
 */

// ── service catalogue: each service declares the fields it needs ──────────────
const SERVICES = [
  {
    id: "email",
    label: "Email",
    icon: "/gmail.png",
    color: "#2563eb",
    blurb: "Send notifications to an inbox",
    primary: "email",
    fields: [
      { name: "label", label: "Label", placeholder: "e.g. Work inbox" },
      {
        name: "email",
        label: "Email Address",
        type: "email",
        placeholder: "you@company.com",
        required: true,
      },
    ],
  },
  {
    id: "outlook",
    label: "Outlook",
    icon: "📧",
    color: "#0078d4",
    blurb: "Send notifications to an Outlook inbox",
    primary: "email",
    fields: [
      {
        name: "label",
        label: "Label",
        placeholder: "e.g. Office Outlook",
      },
      {
        name: "email",
        label: "Email Address",
        type: "email",
        placeholder: "you@company.com",
        required: true,
      },
    ],
  },
  {
    id: "whatsapp",
    label: "WhatsApp",
    icon: "/whatsapp.png",
    color: "#25d366",
    blurb: "Message a WhatsApp number",
    primary: "phone",
    fields: [
      { name: "label", label: "Label", placeholder: "e.g. On-call phone" },
      {
        name: "phone",
        label: "Phone Number",
        type: "tel",
        placeholder: "+91 98765 43210",
        required: true,
      },
    ],
  },
  {
    id: "sms",
    label: "SMS",
    icon: "/sms.png",
    color: "#0891b2",
    blurb: "Text a mobile number",
    primary: "phone",
    fields: [
      { name: "label", label: "Label", placeholder: "e.g. Alerts phone" },
      {
        name: "phone",
        label: "Phone Number",
        type: "tel",
        placeholder: "+91 98765 43210",
        required: true,
      },
    ],
  },
  {
    id: "slack",
    label: "Slack",
    icon: "/slack.png",
    color: "#611f69",
    blurb: "Post to a Slack channel",
    primary: "channel",
    fields: [
      { name: "label", label: "Label", placeholder: "e.g. Team Slack" },
      {
        name: "channel",
        label: "Channel",
        placeholder: "#alerts",
        required: true,
      },
      {
        name: "webhook",
        label: "Incoming Webhook URL",
        type: "url",
        placeholder: "https://hooks.slack.com/services/…",
        required: true,
      },
    ],
  },
  {
    id: "telegram",
    label: "Telegram",
    icon: "/telegram.png",
    color: "#229ed9",
    blurb: "Send to a Telegram chat",
    primary: "chatId",
    fields: [
      { name: "label", label: "Label", placeholder: "e.g. Ops group" },
      {
        name: "chatId",
        label: "Chat ID",
        placeholder: "-1001234567890",
        required: true,
      },
    ],
  },
  {
    id: "discord",
    label: "Discord",
    icon: "/discord.png",
    color: "#5865f2",
    blurb: "Post to a Discord channel",
    primary: "webhook",
    fields: [
      { name: "label", label: "Label", placeholder: "e.g. Server alerts" },
      {
        name: "webhook",
        label: "Webhook URL",
        type: "url",
        placeholder: "https://discord.com/api/webhooks/…",
        required: true,
      },
    ],
  },
  {
    id: "teams",
    label: "Microsoft Teams",
    icon: "/teams.png",
    color: "#5b5fc7",
    blurb: "Post to a Teams channel",
    primary: "webhook",
    fields: [
      { name: "label", label: "Label", placeholder: "e.g. IT Teams" },
      {
        name: "webhook",
        label: "Webhook URL",
        type: "url",
        placeholder: "https://outlook.office.com/webhook/…",
        required: true,
      },
    ],
  },
  {
    id: "jira",
    label: "Jira",
    icon: "/jira.png",
    color: "#0052cc",
    blurb: "Create issues in a project",
    primary: "project",
    fields: [
      { name: "label", label: "Label", placeholder: "e.g. Security board" },
      {
        name: "baseUrl",
        label: "Base URL",
        type: "url",
        placeholder: "https://your-org.atlassian.net",
        required: true,
      },
      {
        name: "project",
        label: "Project Key",
        placeholder: "SEC",
        required: true,
      },
      {
        name: "email",
        label: "Account Email",
        type: "email",
        placeholder: "you@company.com",
        required: true,
      },
      {
        name: "apiToken",
        label: "API Token",
        placeholder: "••••••••",
        required: true,
      },
    ],
  },
];

const serviceById = (id) => SERVICES.find((s) => s.id === id);

/**
 * A channel can arrive with a type this catalogue does not know — it may have
 * been created by another client, or the catalogue may have moved on. Render it
 * rather than dropping it.
 */
const unknownService = (id) => ({
  id,
  label: id || "Channel",
  icon: "📡",
  color: "#64748b",
  blurb: "",
  primary: "value",
  fields: [],
  unknown: true,
});

// ── sender account catalogue ──────────────────────────────────
// These types match the backend ChannelAccount providers.

const SENDER_SERVICES = [
  {
    id: "gmail",
    label: "Gmail",
    icon: "/gmail.png",
    color: "#ea4335",
    blurb: "Send emails from a Gmail account",
    fields: [
      {
        name: "label",
        label: "Sender Name",
        placeholder: "e.g. Security Alerts",
      },
      {
        name: "email",
        label: "Gmail Address",
        type: "email",
        placeholder: "alerts@company.com",
        required: true,
      },
      {
        name: "password",
        label: "App Password",
        type: "password",
        placeholder: "Enter Gmail app password",
        required: true,
      },
    ],
  },

  {
    id: "outlook365",
    label: "Outlook 365",
    icon: "📧",
    color: "#0078d4",
    blurb: "Send emails from Microsoft 365",
    fields: [
      {
        name: "label",
        label: "Sender Name",
        placeholder: "e.g. Microsoft Alerts",
      },
      {
        name: "email",
        label: "Outlook Email",
        type: "email",
        placeholder: "alerts@company.com",
        required: true,
      },
      {
        name: "password",
        label: "Password",
        type: "password",
        placeholder: "Enter password",
        required: true,
      },
    ],
  },

  {
    id: "telegram",
    label: "Telegram",
    icon: "/telegram.png",
    color: "#229ed9",
    blurb: "Send messages using a Telegram bot",
    fields: [
      {
        name: "label",
        label: "Sender Name",
        placeholder: "e.g. Security Bot",
      },
      {
        name: "bot_token",
        label: "Bot Token",
        type: "password",
        placeholder: "123456:ABC-DEF…",
        required: true,
      },
    ],
  },

  {
    id: "whatsapp",
    label: "WhatsApp",
    icon: "/whatsapp.png",
    color: "#25d366",
    blurb: "Send WhatsApp messages through Twilio",
    fields: [
      {
        name: "label",
        label: "Sender Name",
        placeholder: "e.g. WhatsApp Alerts",
      },
      {
        name: "account_sid",
        label: "Twilio Account SID",
        placeholder: "ACxxxxxxxxxxxxxxxx",
        required: true,
      },
      {
        name: "auth_token",
        label: "Twilio Auth Token",
        type: "password",
        placeholder: "Enter auth token",
        required: true,
      },
      {
        name: "from_number",
        label: "From Number",
        type: "tel",
        placeholder: "+14155552671",
        required: true,
      },
    ],
  },

  {
    id: "sms",
    label: "SMS",
    icon: "/sms.png",
    color: "#0891b2",
    blurb: "Send SMS messages through Twilio",
    fields: [
      {
        name: "label",
        label: "Sender Name",
        placeholder: "e.g. SMS Alerts",
      },
      {
        name: "account_sid",
        label: "Twilio Account SID",
        placeholder: "ACxxxxxxxxxxxxxxxx",
        required: true,
      },
      {
        name: "auth_token",
        label: "Twilio Auth Token",
        type: "password",
        placeholder: "Enter auth token",
        required: true,
      },
      {
        name: "from_number",
        label: "From Number",
        type: "tel",
        placeholder: "+14155552671",
        required: true,
      },
    ],
  },

  {
    id: "jira",
    label: "Jira",
    icon: "/jira.png",
    color: "#0052cc",
    blurb: "Create Jira issues from this account",
    fields: [
      {
        name: "label",
        label: "Sender Name",
        placeholder: "e.g. Security Jira",
      },
      {
        name: "base_url",
        label: "Base URL",
        type: "url",
        placeholder: "https://your-org.atlassian.net",
        required: true,
      },
      {
        name: "email",
        label: "Account Email",
        type: "email",
        placeholder: "you@company.com",
        required: true,
      },
      {
        name: "api_token",
        label: "API Token",
        type: "password",
        placeholder: "Enter API token",
        required: true,
      },
      {
        name: "project_key",
        label: "Project Key",
        placeholder: "SEC",
        required: true,
      },
    ],
  },
];

const senderServiceById = (id) =>
  SENDER_SERVICES.find((service) => service.id === id);

const serviceFor = (type) => serviceById(type) || unknownService(type);

// icons can be an image path (e.g. "/slack.png" in public/) or an emoji.
const isImgIcon = (icon) =>
  typeof icon === "string" && /\.(png|svg|jpe?g|webp)$/i.test(icon);

// render either an <img> (for image paths) or the emoji/text as-is.
function renderIcon(icon, alt, size = "lg") {
  if (isImgIcon(icon)) {
    return <img src={icon} alt={alt} className={`ch-icon-img ${size}`} />;
  }
  return icon;
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * Form fields -> the API's single `value` string.
 *
 * Most services carry one destination (an address, a number, a webhook) and it
 * is stored as-is, so the value stays readable in the database and to any other
 * client. Services that need several secrets — Slack's channel + webhook, Jira's
 * four fields — are stored as a JSON object, which {@link unpackValue} reverses.
 *
 * `label` never goes into the value: it becomes the channel's `name`.
 */
function packValues(service, values) {
  const rest = {};
  for (const f of service.fields) {
    if (f.name === "label") continue;
    rest[f.name] = (values[f.name] || "").trim();
  }
  const keys = Object.keys(rest);
  if (keys.length === 1) return rest[keys[0]];
  return JSON.stringify(rest);
}

/** The API's `name` + `value` -> the form fields this UI renders. */
function unpackValue(service, name, value) {
  let fields = null;
  try {
    const parsed = JSON.parse(value);
    // Only an object is a packed field set; a bare number or string that happens
    // to be valid JSON (a phone number, say) is the destination itself.
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed))
      fields = parsed;
  } catch {
    // not packed — value is the destination
  }
  return {
    ...(fields || (service.primary ? { [service.primary]: value } : {})),
    label: name,
  };
}

/** One channel from the API -> the shape the cards and the form read. */
function toUiChannel(apiChannel) {
  const service = serviceFor(apiChannel.type);
  return {
    id: apiChannel.id,
    serviceId: apiChannel.type,
    values: unpackValue(service, apiChannel.name, apiChannel.value),
  };
}

function validateField(field, value) {
  const v = (value || "").trim();
  if (field.required && !v) return `${field.label} is required`;
  if (!v) return null;
  if (field.type === "email" && !EMAIL_RE.test(v))
    return "Enter a valid email address";
  if (field.type === "tel" && v.replace(/\D/g, "").length < 8)
    return "Enter a valid phone number";
  if (field.type === "url" && !/^https?:\/\/.+/i.test(v))
    return "Enter a valid URL (https://…)";
  return null;
}

export default function ChannelsManager() {
  const [channels, setChannels] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [removingId, setRemovingId] = useState(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [step, setStep] = useState("choose"); // "choose" | "form"
  const [serviceId, setServiceId] = useState(null);
  const [values, setValues] = useState({});
  const [errors, setErrors] = useState({});
  const [toast, setToast] = useState(null);
  const [filter, setFilter] = useState("all");

  // ── sender accounts ────────────────────────────────────────────
  const [senderAccounts, setSenderAccounts] = useState([]);
  const [senderLoading, setSenderLoading] = useState(false);
  const [senderLoadError, setSenderLoadError] = useState(null);

  const [senderModalOpen, setSenderModalOpen] = useState(false);
  const [senderListOpen, setSenderListOpen] = useState(false);

  const [senderFilter, setSenderFilter] = useState("all");

  const [senderServiceId, setSenderServiceId] = useState(null);
  const [senderValues, setSenderValues] = useState({});
  const [senderErrors, setSenderErrors] = useState({});
  const [senderSaving, setSenderSaving] = useState(false);
  const [removingSenderId, setRemovingSenderId] = useState(null);

  const load = useCallback(async (signal) => {
    setLoading(true);
    setLoadError(null);
    try {
      const list = await fetchChannels({ signal });
      setChannels(list.map(toUiChannel));
    } catch (err) {
      if (err.name === "CanceledError" || err.code === "ERR_CANCELED") return;
      setLoadError(err.message || "Could not load channels.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 2600);
    return () => clearTimeout(t);
  }, [toast]);

  const loadSenderAccounts = useCallback(async () => {
    setSenderLoading(true);
    setSenderLoadError(null);

    try {
      const list = await fetchChannelAccounts();
      setSenderAccounts(list || []);
    } catch (err) {
      setSenderLoadError(err?.message || "Could not load sender accounts.");
    } finally {
      setSenderLoading(false);
    }
  }, []);

  const service = serviceId ? serviceById(serviceId) : null;

  const counts = useMemo(() => {
    const map = {};
    for (const c of channels) map[c.serviceId] = (map[c.serviceId] || 0) + 1;
    return map;
  }, [channels]);



  const visible = useMemo(
    () =>
      filter === "all"
        ? channels
        : channels.filter((c) => c.serviceId === filter),
    [channels, filter],
  );

  // Built from what is actually stored, not from the catalogue, so a type this
  // build does not know about still gets a chip and a filter.
  const presentServices = useMemo(
    () => Object.keys(counts).map(serviceFor),
    [counts],
  );

  const senderCounts = useMemo(() => {
  const map = {};

  for (const account of senderAccounts) {
    map[account.channel_type] =
      (map[account.channel_type] || 0) + 1;
  }

  return map;
}, [senderAccounts]);

const senderVisible = useMemo(
  () =>
    senderFilter === "all"
      ? senderAccounts
      : senderAccounts.filter(
          (account) => account.channel_type === senderFilter
        ),
  [senderAccounts, senderFilter]
);

const presentSenderServices = useMemo(
  () =>
    Object.keys(senderCounts)
      .map((id) => senderServiceById(id))
      .filter(Boolean),
  [senderCounts]
);

  // ── modal controls ──────────────────────────────────────────────
  const openAdd = () => {
    setServiceId(null);
    setValues({});
    setErrors({});
    setStep("choose");
    setModalOpen(true);
  };

  const pickService = (id) => {
    setServiceId(id);
    setValues({});
    setErrors({});
    setStep("form");
  };

  const closeModal = () => {
    if (saving) return;
    setModalOpen(false);
    setServiceId(null);
    setValues({});
    setErrors({});
  };

  const openAddSender = () => {
    setSenderServiceId(null);
    setSenderValues({});
    setSenderErrors({});
    setSenderModalOpen(true);
  };

  const openSenderList = async () => {
    setSenderFilter("all");
    setSenderListOpen(true);
    await loadSenderAccounts();
  };

  const closeSenderModal = () => {
    if (senderSaving) return;

    setSenderModalOpen(false);
    setSenderServiceId(null);
    setSenderValues({});
    setSenderErrors({});
  };

  const closeSenderList = () => {
    if (removingSenderId) return;
    setSenderListOpen(false);
  };

  const pickSenderService = (id) => {
    setSenderServiceId(id);
    setSenderValues({});
    setSenderErrors({});
  };

  const setSenderField = (name, value) => {
    setSenderValues((current) => ({
      ...current,
      [name]: value,
    }));

    setSenderErrors((current) => ({
      ...current,
      [name]: undefined,
    }));
  };

  const setField = (name, val) => {
    setValues((v) => ({ ...v, [name]: val }));
    setErrors((e) => ({ ...e, [name]: undefined }));
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!service || saving) return;

    // validate
    const next = {};
    for (const f of service.fields) {
      const msg = validateField(f, values[f.name]);
      if (msg) next[f.name] = msg;
    }
    if (Object.keys(next).length) {
      setErrors(next);
      return;
    }

    const clean = {};
    for (const f of service.fields)
      clean[f.name] = (values[f.name] || "").trim();

    // The API's `name` must be unique across every channel. The label is what
    // the user meant to call it; with no label, the destination itself is the
    // most useful name — and the one most likely to already be unique.
    const name = clean.label || packValues(service, clean);

    setSaving(true);
    setErrors({});
    try {
      const created = await addChannel({
        type: service.id,
        name,
        value: packValues(service, clean),
      });
      // Trust the server's copy — it carries the id delete needs.
      setChannels((cur) => [toUiChannel(created), ...cur]);
      setToast(`${service.label} channel registered`);
      setModalOpen(false);
      setServiceId(null);
      setValues({});
    } catch (err) {
      setErrors({ _form: err.message || "Could not register this channel." });
    } finally {
      setSaving(false);
    }
  };

  const submitSender = async (e) => {
    e.preventDefault();

    const service = senderServiceById(senderServiceId);

    if (!service || senderSaving) return;

    const nextErrors = {};

    for (const field of service.fields) {
      const value = senderValues[field.name] || "";
      const message = validateField(field, value);

      if (message) {
        nextErrors[field.name] = message;
      }
    }

    if (Object.keys(nextErrors).length) {
      setSenderErrors(nextErrors);
      return;
    }

    const credentials = {};

    for (const field of service.fields) {
      if (field.name === "label") continue;

      credentials[field.name] = (senderValues[field.name] || "").trim();
    }

    const label =
      (senderValues.label || "").trim() || senderValues.email || service.label;

    setSenderSaving(true);
    setSenderErrors({});

    try {
      const created = await addChannelAccount({
        label,
        channel_type: service.id,
        credentials,
      });

      setSenderAccounts((current) => [created, ...current]);

      setToast(`${service.label} sender registered`);

      setSenderModalOpen(false);
      setSenderServiceId(null);
      setSenderValues({});
      setSenderErrors({});
    } catch (err) {
      setSenderErrors({
        _form: err?.message || "Could not register this sender account.",
      });
    } finally {
      setSenderSaving(false);
    }
  };
  const copyToClipboard = async (value) => {
    const text = String(value ?? "");

    if (!text || text === "—") return;

    try {
      await navigator.clipboard.writeText(text);
      setToast("Copied to clipboard");
    } catch {
      const textarea = document.createElement("textarea");

      textarea.value = text;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";

      document.body.appendChild(textarea);

      textarea.select();

      try {
        document.execCommand("copy");
        setToast("Copied to clipboard");
      } catch {
        setToast("Could not copy to clipboard");
      } finally {
        document.body.removeChild(textarea);
      }
    }
  };

  const removeChannel = async (channel) => {
    const s = serviceFor(channel.serviceId);
    const detail = channel.values[s.primary] || s.label;
    if (!window.confirm(`Remove this ${s.label} channel (${detail})?`)) return;

    setRemovingId(channel.id);
    try {
      await deleteChannel(channel.id);
      setChannels((cur) => cur.filter((c) => c.id !== channel.id));
      setToast("Channel removed");
    } catch (err) {
      setToast(err.message || "Could not remove this channel.");
    } finally {
      setRemovingId(null);
    }
  };

  const removeSender = async (account) => {
    const detail = account.identifier || account.label || account.channel_type;

    if (!window.confirm(`Remove this sender account (${detail})?`)) {
      return;
    }

    setRemovingSenderId(account.id);

    try {
      await deleteChannelAccount(account.id);

      setSenderAccounts((current) =>
        current.filter((item) => item.id !== account.id),
      );

      setToast("Sender account removed");
    } catch (err) {
      setToast(err?.message || "Could not remove sender account.");
    } finally {
      setRemovingSenderId(null);
    }
  };

  const titleFor = (channel, s) =>
    channel.values.label?.trim() || s?.label || "Channel";
  const primaryFor = (channel, s) => channel.values[s?.primary] || "—";
  const primaryLabelFor = (s) =>
    s?.fields.find((f) => f.name === s.primary)?.label || "Destination";

  return (
    <div className="ch-page">
      {/* ── header ─────────────────────────────────────────── */}
      <header className="ch-header">
        <div>
          <h1 className="ch-title">Message Channels</h1>
          <p className="ch-subtitle">
            Register where notifications are delivered — add as many as you
            need.
          </p>
        </div>
        {/* <button className="ch-add-btn" onClick={openAdd}>
          <span className="ch-add-plus">+</span> Add Channel
        </button> */}

        <div className="ch-header-actions">
          <button
            className="ch-header-btn ch-header-btn--primary"
            onClick={openAdd}
          >
            <span>+</span> Add Recipient
          </button>

          <button
            className="ch-header-btn ch-header-btn--sender"
            onClick={openAddSender}
          >
            <span>+</span> Add Sender
          </button>

          <button
            className="ch-header-btn"
            onClick={() => {
              setFilter("all");
            }}
          >
            Recipients
          </button>

          <button className="ch-header-btn" onClick={openSenderList}>
            Senders
          </button>
        </div>
      </header>

      {/* ── stats strip ────────────────────────────────────── */}
      {channels.length > 0 && (
        <div className="ch-stats">
          <div className="ch-stat">
            <span className="ch-stat-num">{channels.length}</span>
            <span className="ch-stat-label">
              Channel{channels.length === 1 ? "" : "s"}
            </span>
          </div>
          <div className="ch-stat-divider" />
          <div className="ch-stat">
            <span className="ch-stat-num">{Object.keys(counts).length}</span>
            <span className="ch-stat-label">Services connected</span>
          </div>
          <div className="ch-stat-services">
            {presentServices.map((s) => (
              <span
                key={s.id}
                className="ch-stat-chip"
                style={{ background: `${s.color}14` }}
                title={`${s.label}: ${counts[s.id]}`}
              >
                {renderIcon(s.icon, s.label, "xs")}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* ── filter chips ───────────────────────────────────── */}
      {channels.length > 0 && (
        <div className="ch-filters">
          <button
            className={`ch-filter ${filter === "all" ? "on" : ""}`}
            onClick={() => setFilter("all")}
          >
            All <span className="ch-filter-n">{channels.length}</span>
          </button>
          {presentServices.map((s) => (
            <button
              key={s.id}
              className={`ch-filter ${filter === s.id ? "on" : ""}`}
              onClick={() => setFilter(s.id)}
            >
              {renderIcon(s.icon, s.label, "xs")}
              <span className="ch-filter-label">{s.label}</span>
              <span className="ch-filter-n">{counts[s.id]}</span>
            </button>
          ))}
        </div>
      )}

      {/* ── channel grid / empty state ─────────────────────── */}
      {loading ? (
        <div className="ch-empty">
          <div className="ch-empty-icon">⏳</div>
          <h2>Loading channels…</h2>
        </div>
      ) : loadError ? (
        <div className="ch-empty">
          <div className="ch-empty-icon">⚠️</div>
          <h2>Could not load channels</h2>
          <p>{loadError}</p>
          <button className="ch-add-btn big" onClick={() => load()}>
            Retry
          </button>
        </div>
      ) : channels.length === 0 ? (
        <div className="ch-empty">
          <div className="ch-empty-icon">📡</div>
          <h2>No channels yet</h2>
          <p>Add your first channel to start delivering notifications.</p>
          <button className="ch-add-btn big" onClick={openAdd}>
            <span className="ch-add-plus">+</span> Add Channel
          </button>
        </div>
      ) : (
        <div className="ch-grid">
          {visible.map((channel) => {
            const s = serviceFor(channel.serviceId);
            return (
              <div
                className="ch-card"
                style={{ "--accent": s?.color }}
                key={channel.id}
              >
                <div className="ch-card-top">
                  <span
                    className="ch-badge"
                    style={{ background: `${s?.color}18` }}
                  >
                    {renderIcon(s?.icon, s?.label, "lg")}
                  </span>
                  <div className="ch-card-head">
                    <div className="ch-card-name">{titleFor(channel, s)}</div>
                    <div className="ch-card-service">{s?.label}</div>
                  </div>
                  {/* No edit button: the API has no update endpoint, so a
                      channel is changed by removing it and adding it again. */}
                  <div className="ch-card-actions">
                    <button
                      className="ch-icon-btn danger"
                      title="Remove"
                      onClick={() => removeChannel(channel)}
                      disabled={removingId === channel.id}
                    >
                      {removingId === channel.id ? "…" : "🗑️"}
                    </button>
                  </div>
                </div>
                {/* <div className="ch-card-field">
                  <span className="ch-card-field-label">{primaryLabelFor(s)}</span>
                  <span className="ch-card-primary">{primaryFor(channel, s)}</span>
                </div> */}
                <div className="ch-card-field">
                  <span className="ch-card-field-label">
                    {primaryLabelFor(s)}
                  </span>

                  <button
                    type="button"
                    className="ch-card-primary"
                    data-full-value={primaryFor(channel, s)}
                    // title={primaryFor(channel, s)}
                    onClick={() => copyToClipboard(primaryFor(channel, s))}
                  >
                    <span className="ch-card-primary-text">
                      {primaryFor(channel, s)}
                    </span>
                  </button>
                </div>
                <div className="ch-card-meta">
                  <span className="ch-status-pill">
                    <span className="ch-dot" /> Active
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* ── add / edit modal ───────────────────────────────── */}
      {modalOpen && (
        <div className="ch-modal-backdrop" onClick={closeModal}>
          <div className="ch-modal" onClick={(e) => e.stopPropagation()}>
            {step === "choose" && (
              <>
                <div className="ch-modal-head">
                  <h2>Add a channel</h2>
                  <button className="ch-close" onClick={closeModal}>
                    ✕
                  </button>
                </div>
                <p className="ch-modal-sub">Choose a service to connect</p>
                <div className="ch-service-grid">
                  {SERVICES.map((s) => (
                    <button
                      key={s.id}
                      className="ch-service-tile"
                      onClick={() => pickService(s.id)}
                    >
                      <span
                        className="ch-tile-icon"
                        style={{ background: `${s.color}18` }}
                      >
                        {renderIcon(s.icon, s.label, "lg")}
                      </span>
                      <span className="ch-tile-name">{s.label}</span>
                      <span className="ch-tile-blurb">{s.blurb}</span>
                    </button>
                  ))}
                </div>
              </>
            )}

            {step === "form" && service && (
              <>
                <div className="ch-modal-head">
                  <div className="ch-modal-title">
                    <button
                      className="ch-back"
                      onClick={() => setStep("choose")}
                      title="Back"
                    >
                      ‹
                    </button>
                    <span
                      className="ch-tile-icon sm"
                      style={{ background: `${service.color}18` }}
                    >
                      {renderIcon(service.icon, service.label, "md")}
                    </span>
                    <h2>New {service.label} channel</h2>
                  </div>
                  <button className="ch-close" onClick={closeModal}>
                    ✕
                  </button>
                </div>

                <form className="ch-form" onSubmit={submit}>
                  {service.fields.map((f) => (
                    <div className="ch-field" key={f.name}>
                      <label className="ch-label">
                        {f.label}
                        {f.required && <span className="ch-req">*</span>}
                      </label>
                      <input
                        className={`ch-input ${errors[f.name] ? "err" : ""}`}
                        type={
                          f.type === "email" || f.type === "url"
                            ? "text"
                            : f.type || "text"
                        }
                        placeholder={f.placeholder}
                        value={values[f.name] || ""}
                        onChange={(e) => setField(f.name, e.target.value)}
                        autoComplete="off"
                      />
                      {errors[f.name] && (
                        <span className="ch-err">{errors[f.name]}</span>
                      )}
                    </div>
                  ))}
                  {errors._form && (
                    <div className="ch-form-err">{errors._form}</div>
                  )}
                  <div className="ch-form-actions">
                    <button
                      type="button"
                      className="ch-btn ghost"
                      onClick={closeModal}
                      disabled={saving}
                    >
                      Cancel
                    </button>
                    <button
                      type="submit"
                      className="ch-btn primary"
                      disabled={saving}
                    >
                      {saving ? "Registering…" : "Register channel"}
                    </button>
                  </div>
                </form>
              </>
            )}
          </div>
        </div>
      )}

      {senderModalOpen && (
        <div className="ch-modal-backdrop" onClick={closeSenderModal}>
          <div className="ch-modal" onClick={(e) => e.stopPropagation()}>
            {!senderServiceId ? (
              <>
                <div className="ch-modal-head">
                  <h2>Add Sender Account</h2>

                  <button className="ch-close" onClick={closeSenderModal}>
                    ✕
                  </button>
                </div>

                <p className="ch-modal-sub">
                  Choose the account you want to use for sending notifications.
                </p>

                <div className="ch-service-grid">
                  {SENDER_SERVICES.map((service) => (
                    <button
                      key={service.id}
                      className="ch-service-tile"
                      onClick={() => pickSenderService(service.id)}
                    >
                      <span
                        className="ch-tile-icon"
                        style={{
                          background: `${service.color}18`,
                        }}
                      >
                        {renderIcon(service.icon, service.label, "lg")}
                      </span>

                      <span className="ch-tile-name">{service.label}</span>

                      <span className="ch-tile-blurb">{service.blurb}</span>
                    </button>
                  ))}
                </div>
              </>
            ) : (
              <>
                <div className="ch-modal-head">
                  <div className="ch-modal-title">
                    <button
                      className="ch-back"
                      onClick={() => setSenderServiceId(null)}
                      title="Back"
                    >
                      ‹
                    </button>

                    <span
                      className="ch-tile-icon sm"
                      style={{
                        background: `${
                          senderServiceById(senderServiceId)?.color
                        }18`,
                      }}
                    >
                      {renderIcon(
                        senderServiceById(senderServiceId)?.icon,
                        senderServiceById(senderServiceId)?.label,
                        "md",
                      )}
                    </span>

                    <h2>
                      Add {senderServiceById(senderServiceId)?.label} Sender
                    </h2>
                  </div>

                  <button className="ch-close" onClick={closeSenderModal}>
                    ✕
                  </button>
                </div>

                <p className="ch-modal-sub">
                  Enter the sender account details. The account will be verified
                  before it is registered.
                </p>

                <form className="ch-form" onSubmit={submitSender}>
                  {senderServiceById(senderServiceId)?.fields.map((field) => (
                    <div className="ch-field" key={field.name}>
                      <label className="ch-label">
                        {field.label}
                        {field.required && <span className="ch-req">*</span>}
                      </label>

                      <input
                        className={`ch-input ${
                          senderErrors[field.name] ? "err" : ""
                        }`}
                        type={field.type || "text"}
                        placeholder={field.placeholder}
                        value={senderValues[field.name] || ""}
                        onChange={(e) =>
                          setSenderField(field.name, e.target.value)
                        }
                        autoComplete="off"
                      />

                      {senderErrors[field.name] && (
                        <span className="ch-err">
                          {senderErrors[field.name]}
                        </span>
                      )}
                    </div>
                  ))}

                  {senderErrors._form && (
                    <div className="ch-form-err">{senderErrors._form}</div>
                  )}

                  <div className="ch-form-actions">
                    <button
                      type="button"
                      className="ch-btn ghost"
                      onClick={closeSenderModal}
                      disabled={senderSaving}
                    >
                      Cancel
                    </button>

                    <button
                      type="submit"
                      className="ch-btn primary"
                      disabled={senderSaving}
                    >
                      {senderSaving
                        ? "Verifying & Registering…"
                        : "Register Sender"}
                    </button>
                  </div>
                </form>
              </>
            )}
          </div>
        </div>
      )}

      {senderListOpen && (
        <div className="ch-modal-backdrop" onClick={closeSenderList}>
          <div
            className="ch-modal ch-modal--wide"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="ch-modal-head">
              <div>
               <h2>Senders</h2>

<p className="ch-modal-sub">
  Manage sender accounts used for sending notifications and reports.
</p>
              </div>

              <button className="ch-close" onClick={closeSenderList}>
                ✕
              </button>
            </div>

            {senderLoading ? (
              <div className="ch-empty ch-empty--modal">
                <div className="ch-empty-icon">⏳</div>
                <h2>Loading sender accounts…</h2>
              </div>
            ) : senderLoadError ? (
              <div className="ch-empty ch-empty--modal">
                <div className="ch-empty-icon">⚠️</div>
                <h2>Could not load senders</h2>

                <p>{senderLoadError}</p>

                <button className="ch-add-btn big" onClick={loadSenderAccounts}>
                  Retry
                </button>
              </div>
            ) : senderAccounts.length === 0 ? (
              <div className="ch-empty ch-empty--modal">
                <div className="ch-empty-icon">📤</div>

                <h2>No sender accounts yet</h2>

                <p>
                  Add a sender account to send reports through communication
                  channels.
                </p>

                <button
                  className="ch-add-btn big"
                  onClick={() => {
                    closeSenderList();
                    openAddSender();
                  }}
                >
                  <span className="ch-add-plus">+</span>
                  Add Sender
                </button>
              </div>
            ) : (
  <>
    <div className="ch-stats">
      <div className="ch-stat">
        <span className="ch-stat-num">
          {senderAccounts.length}
        </span>

        <span className="ch-stat-label">
          Sender{senderAccounts.length === 1 ? "" : "s"}
        </span>
      </div>

      <div className="ch-stat-divider" />

      <div className="ch-stat">
        <span className="ch-stat-num">
          {Object.keys(senderCounts).length}
        </span>

        <span className="ch-stat-label">
          Services connected
        </span>
      </div>

      <div className="ch-stat-services">
        {presentSenderServices.map((service) => (
          <span
            key={service.id}
            className="ch-stat-chip"
            style={{
              background: `${service.color}14`,
            }}
            title={`${service.label}: ${senderCounts[service.id]}`}
          >
            {renderIcon(
              service.icon,
              service.label,
              "xs"
            )}
          </span>
        ))}
      </div>
    </div>

    <div className="ch-filters">
      <button
        className={`ch-filter ${
          senderFilter === "all" ? "on" : ""
        }`}
        onClick={() => setSenderFilter("all")}
      >
        All
        <span className="ch-filter-n">
          {senderAccounts.length}
        </span>
      </button>

      {presentSenderServices.map((service) => (
        <button
          key={service.id}
          className={`ch-filter ${
            senderFilter === service.id ? "on" : ""
          }`}
          onClick={() => setSenderFilter(service.id)}
        >
          {renderIcon(
            service.icon,
            service.label,
            "xs"
          )}

          <span className="ch-filter-label">
            {service.label}
          </span>

          <span className="ch-filter-n">
            {senderCounts[service.id]}
          </span>
        </button>
      ))}
    </div>

    <div className="ch-sender-grid">
                {senderVisible.map((account) => {
                  const service = senderServiceById(account.channel_type);

                  return (
                    <div className="ch-sender-card" key={account.id}>
                      <div className="ch-card-top">
                        <span
                          className="ch-badge"
                          style={{
                            background: `${service?.color || "#4f46e5"}18`,
                          }}
                        >
                          {renderIcon(
                            service?.icon || "📤",
                            service?.label || account.channel_type,
                            "lg",
                          )}
                        </span>

                        <div className="ch-card-head">
                          <div className="ch-card-name">{account.label}</div>

                          <div className="ch-card-service">
                            {service?.label || account.channel_type}
                          </div>
                        </div>

                        <div className="ch-card-actions">
                          <button
                            className="ch-icon-btn danger"
                            title="Remove sender"
                            onClick={() => removeSender(account)}
                            disabled={removingSenderId === account.id}
                          >
                            {removingSenderId === account.id ? "…" : "🗑️"}
                          </button>
                        </div>
                      </div>

                      {/* <div className="ch-card-field">

                  <span className="ch-card-field-label">
                    Sender
                  </span>

                  <span className="ch-card-primary">
                    {account.identifier || "—"}
                  </span>

                </div> */}
                      <div className="ch-card-field">
                        <span className="ch-card-field-label">Sender</span>

                        <button
                          type="button"
                          className="ch-card-primary"
                          data-full-value={account.identifier || "—"}
                          // title={account.identifier || "—"}
                          onClick={() => copyToClipboard(account.identifier)}
                        >
                          <span className="ch-card-primary-text">
                            {account.identifier || "—"}
                          </span>
                        </button>
                      </div>

                      <div className="ch-card-meta">
                        <span className="ch-status-pill">
                          <span className="ch-dot" />

                          {account.is_active && account.is_verified
                            ? "Verified & Active"
                            : "Inactive"}
                        </span>
                      </div>
                    </div>
                  );
                })}
               </div>
            </>
            )}
          </div>
        </div>
      )}

      {toast && <div className="ch-toast">✓ {toast}</div>}
    </div>
  );
}
