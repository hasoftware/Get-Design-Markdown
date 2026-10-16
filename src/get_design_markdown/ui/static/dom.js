// Small DOM and formatting helpers shared by the UI modules.
// Every string that comes from the server or from an analysed site goes through text nodes
// (textContent / createTextNode), never through innerHTML.

const SVG_NS = "http://www.w3.org/2000/svg";

/**
 * Create an element.
 * props: { class, text, attrs: {name: value}, dataset: {key: value}, on: {event: fn}, hidden }
 * children: nodes, strings (as text nodes), arrays, null/undefined/false (skipped).
 */
export function h(tag, props = {}, ...children) {
  const element = document.createElement(tag);
  if (props.class) element.className = props.class;
  if (props.text !== undefined && props.text !== null) element.textContent = String(props.text);
  if (props.hidden) element.hidden = true;
  for (const [name, value] of Object.entries(props.attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    element.setAttribute(name, value === true ? "" : String(value));
  }
  for (const [key, value] of Object.entries(props.dataset || {})) {
    if (value !== null && value !== undefined) element.dataset[key] = String(value);
  }
  for (const [event, handler] of Object.entries(props.on || {})) {
    element.addEventListener(event, handler);
  }
  append(element, children);
  return element;
}

function append(parent, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) {
      append(parent, child);
    } else if (child instanceof Node) {
      parent.appendChild(child);
    } else {
      parent.appendChild(document.createTextNode(String(child)));
    }
  }
}

/** An inline icon from the sprite in index.html (decorative). */
export function icon(name, extraClass = "") {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", extraClass ? `icon ${extraClass}` : "icon");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.appendChild(use);
  return svg;
}

export function clear(element) {
  while (element.firstChild) element.removeChild(element.firstChild);
}

export function byId(id) {
  const element = document.getElementById(id);
  if (!element) throw new Error(`#${id} is missing from index.html`);
  return element;
}

// ─── Storage (may be unavailable: private mode, blocked site data) ─────────

function storageArea(kind) {
  try {
    return kind === "session" ? window.sessionStorage : window.localStorage;
  } catch {
    return null;
  }
}

export const storage = {
  get(kind, key) {
    try {
      const area = storageArea(kind);
      return area ? area.getItem(key) : null;
    } catch {
      return null;
    }
  },
  set(kind, key, value) {
    try {
      const area = storageArea(kind);
      if (area) area.setItem(key, value);
    } catch {
      // Storage full or blocked: the UI keeps working without remembering.
    }
  },
  remove(kind, key) {
    try {
      const area = storageArea(kind);
      if (area) area.removeItem(key);
    } catch {
      // Same as above.
    }
  },
};

// ─── Formatting (Vietnamese) ─────────────────────────────────────────────────

const numberFormat = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 1 });
const dateFormat = new Intl.DateTimeFormat("vi-VN", {
  day: "numeric",
  month: "numeric",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});
const timeFormat = new Intl.DateTimeFormat("vi-VN", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

export function formatNumber(value) {
  return numberFormat.format(value);
}

export function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${numberFormat.format(value)} ${units[unit]}`;
}

export function parseDate(iso) {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDate(iso) {
  const date = parseDate(iso);
  return date ? dateFormat.format(date) : "";
}

export function formatClock(iso) {
  const date = parseDate(iso);
  return date ? timeFormat.format(date) : "";
}

export function formatDuration(ms) {
  const total = Math.max(0, Math.round(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  if (minutes === 0) return `${seconds} giây`;
  return `${minutes} phút ${String(seconds).padStart(2, "0")} giây`;
}

// ─── URLs ────────────────────────────────────────────────────────────────────

/** "/files/<site>/<path>" with every segment percent-encoded. */
export function filesUrl(site, relativePath) {
  const segments = String(relativePath).split("/").map(encodeURIComponent);
  return `/files/${encodeURIComponent(site)}/${segments.join("/")}`;
}

/** Only http(s) URLs may become links; anything else stays plain text. */
export function safeHttpUrl(value) {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

export function hostOf(value) {
  try {
    return new URL(value).host;
  } catch {
    return value || "";
  }
}

// ─── Clipboard ───────────────────────────────────────────────────────────────

export async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Permission refused or no focus: try the textarea fallback below.
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.top = "-1000px";
  area.style.opacity = "0";
  document.body.appendChild(area);
  const active = document.activeElement;
  area.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  }
  area.remove();
  if (active instanceof HTMLElement) active.focus();
  return copied;
}

// ─── Feedback ────────────────────────────────────────────────────────────────

/** A short message at the bottom right, also read by screen readers (role=status). */
export function toast(message, kind = "info", timeoutMs = 3200) {
  const region = document.getElementById("toasts");
  if (!region) return;
  const item = h(
    "div",
    { class: "toast", dataset: { kind } },
    icon(kind === "error" ? "error" : "check"),
    h("span", { text: message }),
  );
  region.appendChild(item);
  while (region.children.length > 3) region.firstElementChild.remove();
  window.setTimeout(() => item.remove(), timeoutMs);
}

/** Copy `text` and give feedback on `button` (label swap) and in a toast. */
export async function copyWithFeedback(button, text, doneMessage) {
  const ok = await copyText(text);
  if (ok) {
    flashButton(button, "Đã sao chép");
    toast(doneMessage, "success");
  } else {
    toast("Không sao chép được. Hãy dùng nút Tải về rồi mở tệp.", "error", 5000);
  }
  return ok;
}

export function flashButton(button, label, ms = 1800) {
  if (!button) return;
  const span = button.querySelector("span:not(.sr-only)");
  if (!span) return;
  if (!button.dataset.label) button.dataset.label = span.textContent;
  span.textContent = label;
  button.dataset.copied = "true";
  window.clearTimeout(Number(button.dataset.flashTimer || 0));
  const timer = window.setTimeout(() => {
    span.textContent = button.dataset.label;
    delete button.dataset.copied;
  }, ms);
  button.dataset.flashTimer = String(timer);
}

export function debounce(fn, ms) {
  let timer = 0;
  const wrapped = (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => fn(...args), ms);
  };
  wrapped.cancel = () => window.clearTimeout(timer);
  return wrapped;
}

export function isVietnamese(text) {
  const matches = String(text).match(/[ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]/gi);
  return (matches ? matches.length : 0) > 40;
}
