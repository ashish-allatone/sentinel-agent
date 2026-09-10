/**
 * Channel Account endpoints — sender accounts used to send notifications.
 *
 * GET    /channel-accounts
 * POST   /channel-accounts
 * DELETE /channel-accounts/{account_id}
 *
 * Send message:
 * POST   /channel-accounts/{account_id}/send
 *
 * Send PDF/file:
 * POST   /channel-accounts/{account_id}/send-file
 *
 * Channel Account = SENDER account
 * Communication Channel = RECEIVER / destination
 */

import api, { absoluteApiUrl } from "../api/api";


export const CHANNEL_ACCOUNTS_PATH = "/channel-accounts";


/**
 * Error carrying useful API failure information.
 */
function channelAccountsError(message, { status, url, cause } = {}) {
  const err = new Error(message);

  err.name = "ChannelAccountsApiError";
  err.status = status;
  err.url = url;
  err.cause = cause;

  return err;
}


/**
 * Convert backend/API errors into readable frontend errors.
 */
function messageForStatus(status, detail) {
  if (detail) return detail;

  if (status === 400) {
    return "Invalid request. Please check the entered values.";
  }

  if (status === 401) {
    return "Authentication failed. Please check the sender account credentials.";
  }

  if (status === 403) {
    return "You are not allowed to perform this action.";
  }

  if (status === 404) {
    return "The requested sender account was not found.";
  }

  if (status === 422) {
    return "The server rejected the submitted values. Please check the fields.";
  }

  if (status === 502) {
    return "The message could not be sent through the selected provider.";
  }

  if (status >= 500) {
    return "The server failed to process the request. Please try again.";
  }

  return "The request failed.";
}


/**
 * Convert Axios errors into ChannelAccountsApiError.
 */
function toApiError(err, url) {
  if (
    err &&
    (err.code === "ERR_CANCELED" || err.name === "CanceledError")
  ) {
    return err;
  }

  const status = err?.response?.status;

  const detail =
    err?.response?.data?.detail ||
    err?.response?.data?.message;

  return channelAccountsError(
    messageForStatus(status, detail),
    {
      status,
      url,
      cause: err,
    }
  );
}


/* ============================================================
   GET ALL SENDER ACCOUNTS
   ============================================================ */

/**
 * Fetch all configured sender accounts.
 *
 * Backend:
 * GET /channel-accounts
 *
 * Example response:
 *
 * {
 *   "accounts": [
 *     {
 *       "id": 1,
 *       "label": "My Gmail",
 *       "channel_type": "gmail",
 *       "identifier": "abc@gmail.com",
 *       "is_verified": true,
 *       "is_active": true
 *     }
 *   ]
 * }
 *
 * @param {{ signal?: AbortSignal }} options
 * @returns {Promise<Array>}
 */
export async function fetchChannelAccounts(options = {}) {
  const url = absoluteApiUrl(CHANNEL_ACCOUNTS_PATH);

  try {
    const res = await api.get(CHANNEL_ACCOUNTS_PATH, {
      signal: options.signal,
    });

    return (res.data && res.data.accounts) || [];
  } catch (err) {
    throw toApiError(err, url);
  }
}


/* ============================================================
   ADD SENDER ACCOUNT
   ============================================================ */

/**
 * Add and verify a sender account.
 *
 * Backend:
 * POST /channel-accounts
 *
 * @param {{
 *   label: string,
 *   channel_type: string,
 *   credentials: Object
 * }} account
 *
 * Gmail example:
 *
 * {
 *   label: "My Gmail",
 *   channel_type: "gmail",
 *   credentials: {
 *     email: "example@gmail.com",
 *     password: "gmail-app-password"
 *   }
 * }
 *
 * @returns {Promise<Object>}
 */
export async function addChannelAccount({
  label,
  channel_type,
  credentials,
}) {
  const url = absoluteApiUrl(CHANNEL_ACCOUNTS_PATH);

  try {
    const res = await api.post(CHANNEL_ACCOUNTS_PATH, {
      label,
      channel_type,
      credentials,
    });

    return res.data || null;
  } catch (err) {
    throw toApiError(err, url);
  }
}


/* ============================================================
   DELETE SENDER ACCOUNT
   ============================================================ */

/**
 * Delete a sender account.
 *
 * Backend:
 * DELETE /channel-accounts/{account_id}
 *
 * @param {number} accountId
 * @returns {Promise<Object>}
 */
export async function deleteChannelAccount(accountId) {
  const path = `${CHANNEL_ACCOUNTS_PATH}/${accountId}`;
  const url = absoluteApiUrl(path);

  try {
    const res = await api.delete(path);

    return res.data || {
      id: accountId,
      deleted: true,
    };
  } catch (err) {
    throw toApiError(err, url);
  }
}


/* ============================================================
   SEND NORMAL MESSAGE
   ============================================================ */

/**
 * Send a normal message using a selected sender account.
 *
 * Backend:
 * POST /channel-accounts/{account_id}/send
 *
 * You can provide either:
 *
 * recipient
 *
 * OR
 *
 * communication_channel_id
 *
 * @param {number} accountId
 *
 * @param {{
 *   recipient?: string,
 *   communication_channel_id?: number,
 *   subject?: string,
 *   body?: string
 * }} payload
 *
 * @returns {Promise<Object>}
 */
export async function sendViaChannelAccount(accountId, payload) {
  const path = `${CHANNEL_ACCOUNTS_PATH}/${accountId}/send`;
  const url = absoluteApiUrl(path);

  try {
    const res = await api.post(path, payload);

    return res.data || {};
  } catch (err) {
    throw toApiError(err, url);
  }
}


/* ============================================================
   SEND PDF / FILE
   ============================================================ */

/**
 * Send a PDF/file using a selected sender account.
 *
 * Backend:
 * POST /channel-accounts/{account_id}/send-file
 *
 * IMPORTANT:
 * This endpoint requires multipart/form-data.
 *
 * Backend accepts:
 *
 * subject
 * body
 * recipient (optional)
 * communication_channel_id (optional)
 * file (required)
 *
 * Either recipient OR communication_channel_id must be supplied.
 *
 * @param {number} accountId
 *
 * @param {{
 *   subject?: string,
 *   body?: string,
 *   recipient?: string,
 *   communication_channel_id?: number,
 *   file: Blob|File
 * }} data
 *
 * @returns {Promise<Object>}
 */
export async function sendFileViaChannelAccount(
  accountId,
  {
    subject = "",
    body = "message",
    recipient = null,
    communication_channel_id = null,
    file,
  }
) {
  const path = `${CHANNEL_ACCOUNTS_PATH}/${accountId}/send-file`;
  const url = absoluteApiUrl(path);

  try {
    if (!file) {
      throw channelAccountsError(
        "No file was provided for sending.",
        { url }
      );
    }

    const formData = new FormData();

    formData.append("subject", subject);
    formData.append("body", body);

    /*
     * Backend accepts either:
     *
     * recipient
     *
     * OR
     *
     * communication_channel_id
     */

    if (recipient) {
      formData.append("recipient", recipient);
    }

    if (
      communication_channel_id !== null &&
      communication_channel_id !== undefined
    ) {
      formData.append(
        "communication_channel_id",
        String(communication_channel_id)
      );
    }

    /*
     * File is required by FastAPI:
     *
     * file: UploadFile = File(...)
     */
    formData.append(
      "file",
      file,
      file.name || "report.pdf"
    );

    /*
     * IMPORTANT:
     *
     * Do NOT manually set:
     *
     * Content-Type: multipart/form-data
     *
     * Axios/browser automatically adds the correct boundary.
     */
    const res = await api.post(path, formData);

    return res.data || {};
  } catch (err) {
    /*
     * If we ourselves created the error above,
     * don't unnecessarily wrap it again.
     */
    if (err?.name === "ChannelAccountsApiError") {
      throw err;
    }

    throw toApiError(err, url);
  }
}


/* ============================================================
   HELPER FUNCTIONS
   ============================================================ */

/**
 * Returns only active and verified sender accounts.
 *
 * Useful for the Capacity Dashboard sender dropdown.
 *
 * @param {Array} accounts
 * @returns {Array}
 */
export function getAvailableSenderAccounts(accounts = []) {
  return accounts.filter(
    (account) =>
      account.is_active === true &&
      account.is_verified === true
  );
}


/**
 * Sender account types currently capable of sending PDF attachments.
 *
 * Based on backend:
 *
 * SEND_WITH_ATTACHMENT = {
 *   "gmail": ...,
 *   "outlook365": ...,
 *   "telegram": ...
 * }
 */
export const PDF_SUPPORTED_ACCOUNT_TYPES = [
  "gmail",
  "outlook365",
  "telegram",
];


/**
 * Check whether the selected sender account supports PDF attachments.
 *
 * @param {Object} account
 * @returns {boolean}
 */
export function senderSupportsPdf(account) {
  if (!account) return false;

  return PDF_SUPPORTED_ACCOUNT_TYPES.includes(
    account.channel_type
  );
}


/**
 * Returns sender accounts that can send PDF attachments.
 *
 * @param {Array} accounts
 * @returns {Array}
 */
export function getPdfCapableSenderAccounts(accounts = []) {
  return accounts.filter(
    (account) =>
      account.is_active === true &&
      account.is_verified === true &&
      senderSupportsPdf(account)
  );
}