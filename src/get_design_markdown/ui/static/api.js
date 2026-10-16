// Talking to the local server: session token, JSON requests, error envelope.
//
// The page is opened at http://127.0.0.1:<port>/#token=<token>. The token is moved to
// sessionStorage (so a reload keeps working), removed from the address bar, and sent as the
// X-GDM-Token header on every /api request.

import { storage } from "./dom.js";

export const TOKEN_HEADER = "X-GDM-Token";
const TOKEN_KEY = "gdm.token";

let token = null;
const listeners = { forbidden: new Set(), offline: new Set(), online: new Set() };
let offline = false;

/** Read the token from the URL fragment (then strip it) or from sessionStorage. */
export function bootToken() {
  const fragment = window.location.hash.replace(/^#/, "");
  const fromHash = new URLSearchParams(fragment).get("token");
  if (fromHash) {
    token = fromHash;
    storage.set("session", TOKEN_KEY, fromHash);
    const clean = window.location.pathname + window.location.search;
    window.history.replaceState(window.history.state, "", clean);
  } else {
    token = storage.get("session", TOKEN_KEY);
  }
  return token;
}

export function hasToken() {
  return Boolean(token);
}

export function forgetToken() {
  token = null;
  storage.remove("session", TOKEN_KEY);
}

/** Subscribe to "forbidden" (session expired), "offline" and "online" (server reachable). */
export function onApi(event, handler) {
  listeners[event].add(handler);
  return () => listeners[event].delete(handler);
}

function emit(event, payload) {
  for (const handler of listeners[event]) {
    try {
      handler(payload);
    } catch (error) {
      console.warn(error);
    }
  }
}

export class ApiError extends Error {
  constructor(status, error) {
    super(error && error.message ? error.message : "Đã xảy ra lỗi.");
    this.name = "ApiError";
    this.status = status;
    this.code = (error && error.code) || "unknown";
    this.hint = (error && error.hint) || null;
    this.details = (error && error.details) || null;
  }
}

const NETWORK_ERROR = {
  code: "network",
  message: "Không kết nối được với Get Design Markdown.",
  hint: "Có thể ứng dụng đã tắt. Mở lại bằng lệnh gdm ui hoặc ứng dụng Get-Design-Markdown.",
};

export async function request(method, path, body) {
  const headers = { [TOKEN_HEADER]: token || "", Accept: "application/json" };
  const init = { method, headers, cache: "no-store", credentials: "same-origin" };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch {
    if (!offline) {
      offline = true;
      emit("offline", NETWORK_ERROR);
    }
    throw new ApiError(0, NETWORK_ERROR);
  }
  if (offline) {
    offline = false;
    emit("online");
  }
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (!response.ok) {
    const error = new ApiError(response.status, data && data.error);
    if (response.status === 403 && (error.code === "forbidden" || error.code === "bad_host")) {
      emit("forbidden", error);
    }
    throw error;
  }
  return data;
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body = {}) => request("POST", path, body),
  put: (path, body) => request("PUT", path, body),
};

export function isOffline() {
  return offline;
}
