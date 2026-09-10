import axios from "axios";

// ========================
// 🍪 COOKIE HELPERS
// ========================

export const setCookie = (name, value, days = 7) => {
  try {
    const date = new Date();
    date.setTime(date.getTime() + days * 24 * 60 * 60 * 1000);
    const expires = date.toUTCString();
    const cookieString = `${name}=${encodeURIComponent(value)}; expires=${expires}; path=/; SameSite=Lax`;
    document.cookie = cookieString;
    console.log(`[✅ COOKIE] Set ${name}`);
  } catch (err) {
    console.error("[❌ COOKIE] Error setting cookie:", err);
  }
};

export const getCookie = (name) => {
  try {
    const nameEQ = name + "=";
    const cookies = document.cookie.split(";");
    for (let i = 0; i < cookies.length; i++) {
      const cookie = cookies[i].trim();
      if (cookie.indexOf(nameEQ) === 0) {
        const value = decodeURIComponent(cookie.substring(nameEQ.length));
        console.log(`[🔍 COOKIE] Found ${name} in cookies`);
        return value;
      }
    }
  } catch (err) {
    console.error("[❌ COOKIE] Error getting cookie:", err);
  }
  return null;
};

export const deleteCookie = (name) => {
  try {
    document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;`;
    console.log(`[🗑️ COOKIE] Deleted ${name}`);
  } catch (err) {
    console.error("[❌ COOKIE] Error deleting cookie:", err);
  }
};

export const clearAuthCookies = () => {
  deleteCookie("token");
  deleteCookie("access_token");
  deleteCookie("refresh_token");
  localStorage.removeItem("token");
  localStorage.removeItem("auth_email");
  console.log("[🗑️ COOKIE] Cleared all auth cookies");
};

export const logout = () => {
  clearAuthCookies();
  console.log("[🚪 AUTH] Logging out...");
  window.location.href = "/app/login";
};

// ========================
// 🎯 LOADING STATE MANAGEMENT
// ========================

let loadingCallbacks = {
  showLoader: null,
  hideLoader: null,
};

// Requests still in flight. The loader is reference-counted because pages fire
// several calls at once (the SOC2 report fires five): without the count, the
// first response would hide the loader while the rest were still running.
let inFlight = 0;

export const registerLoaderCallbacks = (showLoader, hideLoader) => {
  loadingCallbacks.showLoader = showLoader;
  loadingCallbacks.hideLoader = hideLoader;
  console.log("[🎯 LOADER] Callbacks registered");
};

export const triggerShowLoader = () => {
  inFlight += 1;
  if (inFlight === 1 && loadingCallbacks.showLoader) {
    loadingCallbacks.showLoader();
  }
};

export const triggerHideLoader = () => {
  // Nothing open: an unbalanced hide. Ignore it rather than firing a hide that
  // pairs with no show — the count must never go negative or the next real
  // request would leave the loader stuck on screen.
  if (inFlight === 0) return;
  inFlight -= 1;
  if (inFlight === 0 && loadingCallbacks.hideLoader) {
    loadingCallbacks.hideLoader();
  }
};

/** How many requests are currently open — 0 when the app is idle. */
export const pendingRequestCount = () => inFlight;

/**
 * Does this request want the full-screen loader?
 *
 * Pass `skipGlobalLoader: true` in a request's config when the calling screen
 * renders its own progress (the SOC2 report shows one loader per report, which
 * the blocking overlay would sit on top of). Everything else keeps the overlay.
 */
const skipsGlobalLoader = (config) => Boolean(config && config.skipGlobalLoader);

// ========================
// 🔌 AXIOS INSTANCE
// ========================

/**
 * Every endpoint on this backend lives under /api/v1, so the prefix belongs to
 * the client rather than to each call site. Call sites pass the path only:
 *
 *   api.get("/get-agents")            ->  <origin>/api/v1/get-agents
 *   api.get("/soc2-report/auth")      ->  <origin>/api/v1/soc2-report/auth
 */
export const API_PREFIX = "/api/v1";

/**
 * Where the API lives, without the prefix.
 *
 * Set REACT_APP_API_ORIGIN per environment (see .env.development /
 * .env.production). Leave it EMPTY to call the same origin the app is served
 * from — the right setting when a reverse proxy in front of the site forwards
 * /api/v1 to the backend. Unset falls back to the deployed API host, so an
 * environment with no configuration keeps working.
 */
const DEFAULT_API_ORIGIN = "http://80.225.239.163:8000";
const configuredOrigin =
  process.env.REACT_APP_API_ORIGIN === undefined
    ? DEFAULT_API_ORIGIN
    : process.env.REACT_APP_API_ORIGIN;

// trailing slashes would double up against the prefix
export const API_ORIGIN = String(configuredOrigin).replace(/\/+$/, "");

/** The prefix every request is built on, e.g. "http://host:8000/api/v1". */
export const API_BASE_URL = `${API_ORIGIN}${API_PREFIX}`;

/** The same base as a browser-absolute URL, for showing the user what failed. */
export const absoluteApiUrl = (path = "") =>
  API_ORIGIN
    ? `${API_BASE_URL}${path}`
    : `${typeof window !== "undefined" ? window.location.origin : ""}${API_BASE_URL}${path}`;

console.log("[🔌 API] Base URL:", API_BASE_URL);

const api = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true,
  headers: {
    "Content-Type": "application/json",
  },
});

// ========================
// 📤 REQUEST INTERCEPTOR - ADD TOKEN TO EVERY REQUEST
// ========================

api.interceptors.request.use(
  (config) => {
    console.log("\n[📤 REQUEST] URL:", config.url);
    console.log("[📤 REQUEST] Method:", config.method.toUpperCase());

    // Show loader on request start, unless the screen draws its own progress
    if (!skipsGlobalLoader(config)) {
      triggerShowLoader();
    }

    // Get token from cookie (most reliable source)
    let token = getCookie("token");

    // If no token in "token" cookie, try "access_token" cookie
    if (!token) {
      token = getCookie("access_token");
    }

    // Last resort: try localStorage
    if (!token) {
      const storageToken = localStorage.getItem("token");
      if (storageToken) {
        token = storageToken;
        console.log("[📤 REQUEST] Token from localStorage");
      }
    }

    // CRITICAL: Add Authorization header if token exists
    if (token && token.length > 0) {
      config.headers.Authorization = `Bearer ${token}`;
      console.log(
        "[✅ REQUEST] Authorization header SET with token:",
        token.substring(0, 40) + "...",
      );
      console.log("[✅ REQUEST] Headers:", config.headers);
    } else {
      console.error(
        "[❌ REQUEST] NO TOKEN FOUND - Authorization header NOT SET!",
      );
      console.error(
        "[❌ REQUEST] Cookie 'token':",
        getCookie("token") ? "YES" : "NO",
      );
      console.error(
        "[❌ REQUEST] Cookie 'access_token':",
        getCookie("access_token") ? "YES" : "NO",
      );
      console.error(
        "[❌ REQUEST] localStorage 'token':",
        localStorage.getItem("token") ? "YES" : "NO",
      );
    }

     // ============================================================
    // IMPORTANT: Allow FormData requests to use multipart/form-data
    // ============================================================
    if (config.data instanceof FormData) {
      delete config.headers["Content-Type"]
    }  

    return config;
  },
  (error) => {
    console.error("[❌ REQUEST ERROR]:", error);
    // Only balance a show that actually happened — a request that opted out of
    // the overlay, or one that failed before the show, must not decrement it.
    if (error && error.config && !skipsGlobalLoader(error.config)) {
      triggerHideLoader();
    }
    return Promise.reject(error);
  },
);

// ========================
// 📥 RESPONSE INTERCEPTOR
// ========================

api.interceptors.response.use(
  (response) => {
    console.log(
      "[✅ RESPONSE] Status:",
      response.status,
      "URL:",
      response.config.url,
    );
    if (!skipsGlobalLoader(response.config)) {
      triggerHideLoader();
    }
    return response;
  },
  (error) => {
    console.error("[❌ RESPONSE ERROR] Status:", error.response?.status);
    console.error("[❌ RESPONSE ERROR] URL:", error.config?.url);
    console.error(
      "[❌ RESPONSE ERROR] Message:",
      error.response?.data?.message,
    );
    if (!skipsGlobalLoader(error.config)) {
      triggerHideLoader();
    }
    return Promise.reject(error);
  },
);

export default api;
