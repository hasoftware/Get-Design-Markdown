// Get Design Markdown — local UI entry point (ES module, no build step).
//
// Views: "start" (URL + options), "job" (progress), "results" (one site), "loading".
// Everything the page shows from the server is inserted as text; rendered Markdown only ever
// goes into sandboxed iframes (frame.js).

import { api, bootToken, forgetToken, onApi } from "./api.js";
import { BROWSER_LABELS, BrowserService, mountInstallWidget } from "./browsers.js";
import { byId, clear, h, prefs, toast } from "./dom.js";
import { HistoryList } from "./history.js";
import { JobController } from "./job.js";
import { ResultsView } from "./results.js";
import { SettingsForm } from "./settings.js";
import { initTheme, onThemeChange } from "./theme.js";

const APP_TITLE = "Get Design Markdown";
const HEARTBEAT_MS = 30000;
const GUIDE_KEY = "gdm.guideDone";
const URL_ERROR_REPEAT_MS = 150;
const VIEWS = ["start", "job", "results", "loading"];

function showExpired() {
  byId("app").hidden = true;
  const page = byId("expired");
  page.hidden = false;
  document.title = `Phiên làm việc đã hết hạn – ${APP_TITLE}`;
  byId("expired-title").focus();
}

class App {
  constructor() {
    this.root = byId("app");
    this.view = "start";
    this.expired = false;
    this.starting = false;
    this.guideDecided = false;
    this.heartbeatTimer = 0;
    this.urlErrorTimer = 0;
    this.urlInput = byId("url-input");
    this.analyzeButton = byId("analyze-button");
    this.browsers = new BrowserService();
    this.settings = new SettingsForm({ onChange: (response, info) => this.onSettings(response, info) });
    this.history = new HistoryList({ onOpen: (site) => this.openResults(site, { focus: true }) });
    this.jobs = new JobController(this);
    this.results = new ResultsView(this);
  }

  async start() {
    this.root.hidden = false;
    onApi("forbidden", () => this.expire());
    onApi("offline", () => this.setOffline(true));
    onApi("online", () => this.setOffline(false));
    onThemeChange(() => this.results.refreshFrames());
    this.bind();
    this.browsers.subscribe(() => this.renderBrowser());
    mountInstallWidget(byId("browser-banner").querySelector("[data-install-slot]"), this.browsers);
    mountInstallWidget(document.querySelector("[data-install-quiet]"), this.browsers, { quiet: true });
    this.startHeartbeat();
    this.showVersion();
    const history = this.history.load().then(() => this.initGuide());

    await Promise.allSettled([this.settings.load(), this.browsers.refresh(), history]);
    if (this.expired) return;
    const resumed = await this.jobs.resume();
    if (this.expired || resumed) return;
    const site = ResultsView.rememberedSite();
    if (site) {
      await this.results.open(site, { focus: false });
    } else {
      this.showView("start", { focus: false });
    }
  }

  // ─── Wiring ────────────────────────────────────────────────────────────────

  bind() {
    byId("url-form").addEventListener("submit", (event) => {
      event.preventDefault();
      this.submitUrl();
    });
    this.urlInput.addEventListener("input", () => this.clearUrlError());
    byId("new-analysis").addEventListener("click", () => this.showStart({ focusUrl: true }));
    byId("help-button").addEventListener("click", () => this.showGuide());
    byId("guide-close").addEventListener("click", () => this.hideGuide());
    byId("busy-show").addEventListener("click", () => this.jobs.show());
    byId("browser-pill").addEventListener("click", () => this.openAdvanced("opt-browser"));
    byId("open-root").addEventListener("click", () => this.openFolder(null));
    byId("open-output").addEventListener("click", () => this.openFolder(null));
    byId("history-toggle").addEventListener("click", () => this.toggleDrawer());
    byId("scrim").addEventListener("click", () => this.toggleDrawer(false, { restoreFocus: true }));
    byId("connection-retry").addEventListener("click", () => this.retryConnection());
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && this.root.dataset.drawer === "open") {
        this.toggleDrawer(false, { restoreFocus: true });
      }
    });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState !== "visible") return;
      this.heartbeat();
      // The user may have installed Edge or Chrome meanwhile.
      if (!this.expired) this.browsers.refresh();
    });
  }

  // ─── Views ─────────────────────────────────────────────────────────────────

  showView(name, { focus = false } = {}) {
    for (const view of VIEWS) byId(`view-${view}`).hidden = view !== name;
    this.view = name;
    if (name !== "results") this.history.setActive(null);
    this.toggleDrawer(false);
    this.updateBusyBanner();
    this.updateTitle();
    if (name !== "loading") window.scrollTo(0, 0);
    if (focus) {
      const heading = byId(`view-${name}`).querySelector(".page-title");
      if (heading) heading.focus();
    }
  }

  updateTitle() {
    let title = APP_TITLE;
    if (this.view === "job") title = `${byId("job-title").textContent} – ${APP_TITLE}`;
    if (this.view === "results" && this.results.results) {
      const entry = this.results.results.entry;
      title = `${entry.name || this.results.site} – ${APP_TITLE}`;
    }
    document.title = title;
  }

  showStart({ url, focusUrl = false } = {}) {
    if (typeof url === "string") this.urlInput.value = url;
    this.results.forget();
    this.showView("start", { focus: false });
    if (focusUrl) this.urlInput.focus();
  }

  openResults(site, options = {}) {
    return this.results.open(site, options);
  }

  setActiveSite(site) {
    this.history.setActive(site);
  }

  refreshHistory() {
    return this.history.load();
  }

  openAdvanced(focusId) {
    this.showStart();
    byId("advanced").open = true;
    const target = byId(focusId);
    target.scrollIntoView({ block: "center" });
    target.focus();
  }

  /**
   * The first-run guide shows until it is dismissed, and never once sites have been analysed:
   * browser data can be cleared or blocked (or belong to another browser), the history stays.
   */
  initGuide() {
    if (this.expired || this.guideDecided) return;
    this.guideDecided = true;
    const dismissed = prefs.get(GUIDE_KEY) === "1";
    const experienced = this.history.loaded && this.history.entries.length > 0;
    if (!dismissed && !experienced) byId("guide").hidden = false;
  }

  showGuide() {
    this.guideDecided = true;
    this.showStart();
    const guide = byId("guide");
    guide.hidden = false;
    guide.scrollIntoView({ block: "start" });
    guide.focus();
  }

  hideGuide() {
    this.guideDecided = true;
    byId("guide").hidden = true;
    prefs.set(GUIDE_KEY, "1");
    this.urlInput.focus();
  }

  toggleDrawer(open, { restoreFocus = false } = {}) {
    const wasOpen = this.root.dataset.drawer === "open";
    const isOpen = open === undefined ? !wasOpen : open;
    if (isOpen === wasOpen) return;
    this.root.dataset.drawer = isOpen ? "open" : "closed";
    const toggle = byId("history-toggle");
    toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
    byId("scrim").hidden = !isOpen;
    if (isOpen) {
      (this.history.firstButton() || byId("new-analysis")).focus();
    } else if (restoreFocus) {
      toggle.focus();
    }
  }

  // ─── Jobs ──────────────────────────────────────────────────────────────────

  async submitUrl() {
    const url = this.urlInput.value.trim();
    if (!url) {
      this.showUrlError("Nhập địa chỉ trang web, ví dụ stripe.com.");
      this.urlInput.focus();
      return;
    }
    this.clearUrlError();
    if (this.jobs.active) {
      this.jobs.show();
      toast("Đang có một lượt phân tích chạy. Chờ nó xong hoặc bấm Hủy.", "info", 4500);
      return;
    }
    await this.startJob(url);
  }

  async startJob(url) {
    if (this.starting) return;
    this.starting = true;
    this.setAnalyzeBusy(true);
    try {
      await this.settings.flush();
      const invalid = this.settings.firstInvalid();
      if (invalid) {
        this.showStart({ url });
        if (invalid.closest("#advanced")) byId("advanced").open = true;
        invalid.focus();
        toast("Một tùy chọn chưa hợp lệ. Sửa chỗ báo đỏ rồi bấm Phân tích lại.", "error", 5000);
        return;
      }
      await this.jobs.start(url);
    } catch (error) {
      if (error.code === "forbidden") return;
      this.showStart({ url });
      this.showUrlError(error.message, error.hint);
      this.urlInput.focus();
    } finally {
      this.starting = false;
      this.setAnalyzeBusy(false);
    }
  }

  setAnalyzeBusy(busy) {
    this.analyzeButton.disabled = busy;
    this.analyzeButton.setAttribute("aria-busy", busy ? "true" : "false");
    this.analyzeButton.textContent = busy ? "Đang bắt đầu…" : "Phân tích";
  }

  onJobChange() {
    this.updateBusyBanner();
    this.updateTitle();
  }

  updateBusyBanner() {
    const banner = byId("busy-banner");
    const job = this.jobs.job;
    const show = this.view === "start" && this.jobs.active;
    banner.hidden = !show;
    if (show) byId("busy-text").textContent = `Đang phân tích ${job.site || job.url}.`;
  }

  /** The error under the URL field (role=alert: read out as soon as it shows). */
  showUrlError(message, hint = null) {
    const box = byId("url-error");
    const key = JSON.stringify([message, hint || ""]);
    const fill = () => {
      clear(box);
      box.appendChild(h("span", { text: message }));
      if (hint) box.appendChild(h("span", { class: "hint", text: hint }));
      box.dataset.text = key;
      box.hidden = false;
    };
    window.clearTimeout(this.urlErrorTimer);
    this.urlInput.setAttribute("aria-invalid", "true");
    if (!box.hidden && box.dataset.text === key) {
      // The same error again: hide it for a moment so that screen readers announce it again.
      box.hidden = true;
      this.urlErrorTimer = window.setTimeout(fill, URL_ERROR_REPEAT_MS);
      return;
    }
    fill();
  }

  clearUrlError() {
    const box = byId("url-error");
    window.clearTimeout(this.urlErrorTimer);
    box.hidden = true;
    clear(box);
    delete box.dataset.text;
    this.urlInput.removeAttribute("aria-invalid");
  }

  // ─── Settings, browsers, folders ───────────────────────────────────────────

  onSettings(_response, info) {
    this.renderBrowser();
    if (info.rootChanged) {
      this.history.load();
      if (this.view === "results") this.showStart();
    }
  }

  renderBrowser() {
    const service = this.browsers;
    const pill = byId("browser-pill");
    const pillText = byId("browser-pill-text");
    const settings = this.settings.settings;
    const choice = settings ? settings.browser : "auto";
    const status = byId("opt-browser-status");
    const list = byId("browser-list");
    if (!service.known) {
      pill.dataset.state = service.loadError ? "missing" : "unknown";
      pillText.textContent = service.loadError
        ? "Không kiểm tra được trình duyệt"
        : "Đang kiểm tra trình duyệt…";
      status.textContent = "";
      byId("browser-banner").hidden = true;
      return;
    }
    const available = service.isAvailable(choice);
    const name = choice === "auto" ? service.autoChoice : choice;
    const info = name ? service.info(name) : null;
    const version = info && info.version ? ` ${info.version}` : "";
    if (available) {
      pill.dataset.state = "ok";
      pillText.textContent = `Trình duyệt: ${BROWSER_LABELS[name]}`;
    } else {
      pill.dataset.state = "missing";
      pillText.textContent =
        choice === "auto" ? "Chưa có trình duyệt" : `Không tìm thấy ${BROWSER_LABELS[choice]}`;
    }
    pill.title = "Chọn trình duyệt trong Tùy chọn nâng cao";
    byId("browser-banner").hidden = Boolean(service.autoChoice);

    if (choice === "auto") {
      status.textContent = available
        ? `Lúc này sẽ dùng: ${BROWSER_LABELS[name]}${version}.`
        : "Chưa tìm thấy trình duyệt nào dùng được trên máy.";
    } else if (available) {
      status.textContent = `Đã tìm thấy ${BROWSER_LABELS[choice]}${version}.`;
    } else {
      const detail = info && info.detail ? info.detail : `Không tìm thấy ${BROWSER_LABELS[choice]}.`;
      status.textContent = `${detail} Chọn “Tự động” hoặc cài trình duyệt này.`;
    }
    status.dataset.state = available ? "ok" : "missing";

    clear(list);
    for (const browser of service.status.browsers) {
      const state = browser.available
        ? browser.version
          ? `có sẵn, bản ${browser.version}`
          : "có sẵn"
        : "chưa có";
      list.appendChild(
        h(
          "li",
          { dataset: { available: browser.available ? "true" : "false" } },
          h("span", { class: "dot", attrs: { "aria-hidden": "true" } }),
          `${browser.label}: ${state}`,
        ),
      );
    }
  }

  async openFolder(site) {
    try {
      const data = await api.post("/api/open-folder", site ? { site } : {});
      toast(`Đã mở thư mục ${data.folder}`, "success", 4000);
    } catch (error) {
      if (error.code === "forbidden") return;
      toast(error.hint ? `${error.message} ${error.hint}` : error.message, "error", 6000);
    }
  }

  // ─── Connection ────────────────────────────────────────────────────────────

  async showVersion() {
    try {
      const health = await api.get("/api/health");
      byId("app-version").textContent = `Phiên bản ${health.version}`;
    } catch {
      // The version is a detail; the rest of the page reports connection problems.
    }
  }

  startHeartbeat() {
    this.heartbeat();
    this.heartbeatTimer = window.setInterval(() => this.heartbeat(), HEARTBEAT_MS);
  }

  heartbeat() {
    if (this.expired) return;
    api.post("/api/heartbeat").catch(() => {});
  }

  setOffline(offline) {
    byId("connection-banner").hidden = !offline;
  }

  async retryConnection() {
    try {
      await api.get("/api/health");
    } catch {
      toast("Vẫn chưa kết nối được. Hãy mở lại Get Design Markdown.", "error", 5000);
      return;
    }
    this.heartbeat();
    await Promise.allSettled([this.settings.load(), this.browsers.refresh(), this.history.load()]);
    toast("Đã kết nối lại.", "success");
  }

  expire() {
    if (this.expired) return;
    this.expired = true;
    window.clearInterval(this.heartbeatTimer);
    this.jobs.stopPolling();
    this.browsers.stop();
    forgetToken();
    showExpired();
  }
}

function main() {
  initTheme();
  if (!bootToken()) {
    showExpired();
    return;
  }
  const app = new App();
  app.start().catch((error) => {
    console.error(error);
  });
}

main();
