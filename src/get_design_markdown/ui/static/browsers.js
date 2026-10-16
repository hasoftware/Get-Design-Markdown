// Which browsers the capture can use (GET /api/browser/status) and the Chromium download
// (POST /api/browser/install, then GET /api/browser/install every second while it runs).

import { api } from "./api.js";
import { h, icon } from "./dom.js";

const INSTALL_POLL_MS = 1000;

export const BROWSER_LABELS = {
  auto: "Tự động",
  chromium: "Chromium (Playwright)",
  msedge: "Microsoft Edge",
  chrome: "Google Chrome",
};

export class BrowserService {
  constructor() {
    this.status = null;
    this.install = { state: "idle", percent: null, message: "", log_tail: [], error: null };
    this.listeners = new Set();
    this.timer = 0;
    this.loadError = null;
  }

  subscribe(handler) {
    this.listeners.add(handler);
    handler(this);
    return () => this.listeners.delete(handler);
  }

  emit() {
    for (const handler of Array.from(this.listeners)) handler(this);
  }

  async refresh() {
    try {
      const data = await api.get("/api/browser/status");
      this.status = data;
      this.loadError = null;
      if (data.install) this.install = data.install;
      this.emit();
      if (this.install.state === "running") this.schedulePoll();
      return data;
    } catch (error) {
      this.loadError = error;
      this.emit();
      return null;
    }
  }

  get known() {
    return this.status !== null;
  }

  /** What "auto" would use now (a browser name), or null. */
  get autoChoice() {
    return this.status ? this.status.auto : null;
  }

  info(name) {
    if (!this.status) return null;
    return this.status.browsers.find((browser) => browser.name === name) || null;
  }

  /** True / false when known, null while the status is unknown. */
  isAvailable(choice) {
    if (!this.status) return null;
    if (choice === "auto") return Boolean(this.status.auto);
    const info = this.info(choice);
    return Boolean(info && info.available);
  }

  get chromiumMissing() {
    const info = this.info("chromium");
    return Boolean(info && !info.available);
  }

  async startInstall() {
    try {
      const data = await api.post("/api/browser/install");
      this.install = data.install;
    } catch (error) {
      if (error.code === "install_running" && error.details && error.details.install) {
        this.install = error.details.install;
      } else {
        this.install = {
          state: "failed",
          percent: null,
          message: error.message,
          log_tail: [],
          error: { message: error.message, hint: error.hint },
        };
        this.emit();
        return;
      }
    }
    this.emit();
    if (this.install.state === "running") this.schedulePoll();
  }

  schedulePoll() {
    window.clearTimeout(this.timer);
    this.timer = window.setTimeout(() => this.poll(), INSTALL_POLL_MS);
  }

  async poll() {
    try {
      const data = await api.get("/api/browser/install");
      this.install = data.install;
    } catch {
      // Keep the last known progress and try again.
    }
    this.emit();
    if (this.install.state === "running") {
      this.schedulePoll();
    } else if (this.install.state === "succeeded") {
      await this.refresh();
    }
  }

  stop() {
    window.clearTimeout(this.timer);
  }
}

/**
 * Mount the "Tải Chromium" widget into `container` (replacing what is there). It follows the
 * shared install state and unsubscribes itself once removed from the page.
 * quiet: a secondary button (inside the advanced options) instead of a primary one.
 */
export function mountInstallWidget(container, service, { quiet = false } = {}) {
  const root = h("div", { class: "install" });
  container.replaceChildren(root);
  let lastState = null;
  let wantFocus = false;
  const unsubscribe = service.subscribe(() => {
    if (!root.isConnected && lastState !== null) {
      unsubscribe();
      return;
    }
    render();
  });

  function render() {
    const install = service.install || { state: "idle" };
    const state = install.state || "idle";
    const chromiumReady = service.known && !service.chromiumMissing;
    root.replaceChildren();
    lastState = state;

    if (state === "running") {
      const percent = typeof install.percent === "number" ? install.percent : null;
      const bar = h("progress", {
        attrs: {
          max: "100",
          "aria-label": "Tiến độ tải Chromium",
          ...(percent === null ? {} : { value: String(percent) }),
        },
      });
      const message = h("p", {
        class: "install-message",
        text: install.message || "Đang tải Chromium…",
        attrs: { role: "status", tabindex: "-1" },
        dataset: { state },
      });
      root.append(
        h("div", { class: "install-progress" }, bar, message),
        h("p", {
          class: "help",
          text: "Có thể mất vài phút. Bạn vẫn dùng được các phần khác trong lúc chờ.",
        }),
      );
      if (wantFocus) {
        wantFocus = false;
        message.focus();
      }
      return;
    }

    if (state === "succeeded") {
      root.append(
        h("p", {
          class: "install-message",
          text: install.message || "Đã tải xong Chromium.",
          attrs: { role: "status" },
          dataset: { state },
        }),
      );
      return;
    }

    if (state === "failed") {
      const error = install.error || {};
      root.append(
        h("p", {
          class: "install-message",
          text: error.message || install.message || "Không tải được Chromium.",
          attrs: { role: "alert" },
          dataset: { state },
        }),
      );
      if (error.hint) root.append(h("p", { class: "help", text: error.hint }));
    }

    if (chromiumReady && state !== "failed") return;
    const button = h(
      "button",
      {
        class: quiet ? "btn btn-secondary btn-sm" : "btn btn-primary",
        attrs: { type: "button" },
        on: {
          click: () => {
            wantFocus = true;
            button.disabled = true;
            service.startInstall();
          },
        },
      },
      icon("download"),
      h("span", { text: state === "failed" ? "Thử tải lại Chromium" : "Tải Chromium" }),
    );
    root.append(button);
    if (quiet) {
      root.append(
        h("p", {
          class: "help",
          text: "Chromium là trình duyệt riêng cho công cụ (tải về khoảng 125 MB, chiếm khoảng 300 MB trên ổ đĩa), cho kết quả ổn định nhất.",
        }),
      );
    }
  }

  return root;
}
