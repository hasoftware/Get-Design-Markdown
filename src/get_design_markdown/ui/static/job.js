// One analysis run: start it, poll its events every 500 ms, show the four steps, the live log,
// robots.txt warnings, cancellation and the outcome (with help that depends on the error).

import { api } from "./api.js";
import { mountInstallWidget } from "./browsers.js";
import {
  byId,
  clear,
  formatClock,
  formatDuration,
  formatNumber,
  h,
  icon,
  parseDate,
  storage,
} from "./dom.js";

const POLL_MS = 500;
const RETRY_MS = 1500;
const MAX_EVENTS_PER_RESPONSE = 500;
const MAX_LOG_LINES = 800;
const STEP_COUNT = 4;
const RESUME_WINDOW_MS = 15 * 60 * 1000;
const ACK_KEY = "gdm.ackJob";
const FINISHED = new Set(["succeeded", "failed", "cancelled"]);
const LOGGED = new Set(["step", "info", "success", "warning", "error"]);
const LOG_ICONS = { step: "chevron", info: "info", success: "ok", warning: "alert", error: "error" };
const ROBOTS_RE = /robots\.txt/i;

export function isFinished(job) {
  return Boolean(job && FINISHED.has(job.status));
}

export class JobController {
  /**
   * app: {
   *   showView(name, {focus}), showStart({url, focusUrl}), openResults(site, opts),
   *   refreshHistory(), startJob(url), settings (SettingsForm), browsers (BrowserService),
   *   onJobChange(job)
   * }
   */
  constructor(app) {
    this.app = app;
    this.job = null;
    this.lastSeq = 0;
    this.timer = 0;
    this.clock = 0;
    this.lines = 0;
    this.resumedFinished = false;
    this.title = byId("job-title");
    this.urlText = byId("job-url");
    this.elapsed = byId("job-elapsed");
    this.ruler = byId("job-ruler");
    this.rulerFill = byId("ruler-fill");
    this.steps = Array.from(byId("job-steps").children);
    this.progress = byId("job-progress");
    this.robots = byId("job-robots");
    this.robotsText = byId("job-robots-text");
    this.outcome = byId("job-outcome");
    this.log = byId("job-log");
    this.cancelButton = byId("cancel-button");
    this.cancelLabel = byId("cancel-label");
    this.cancelButton.addEventListener("click", () => this.cancel());
  }

  get active() {
    return Boolean(this.job) && !isFinished(this.job);
  }

  // ─── Starting / attaching ──────────────────────────────────────────────────

  /** POST /api/jobs; resolves to true when a job is shown, else throws the ApiError. */
  async start(url) {
    try {
      const data = await api.post("/api/jobs", { url });
      this.attach(data.job, { focus: true });
      return true;
    } catch (error) {
      if (error.code === "busy" && error.details && error.details.job_id) {
        const data = await api.get(`/api/jobs/${encodeURIComponent(error.details.job_id)}`);
        this.attach({ ...data.job, events: [] }, { focus: true });
        return true;
      }
      throw error;
    }
  }

  /** Show a job and poll it from its first event. */
  attach(snapshot, { focus = true, resumedFinished = false } = {}) {
    this.stopPolling();
    this.job = { ...snapshot, events: [] };
    this.lastSeq = 0;
    this.lines = 0;
    this.resumedFinished = resumedFinished;
    clear(this.log);
    clear(this.outcome);
    this.outcome.hidden = true;
    this.robots.hidden = true;
    this.robotsText.textContent = "";
    this.progress.textContent = "";
    this.render();
    this.app.showView("job", { focus });
    this.app.onJobChange(this.job);
    this.startClock();
    this.poll();
  }

  /** After a reload: show the running job, or one that finished unseen a moment ago. */
  async resume() {
    let data;
    try {
      data = await api.get("/api/jobs/current");
    } catch {
      return false;
    }
    const job = data && data.job;
    if (!job) return false;
    if (!isFinished(job)) {
      this.attach(job, { focus: false });
      return true;
    }
    const finished = parseDate(job.finished_at);
    const recent = finished && Date.now() - finished.getTime() < RESUME_WINDOW_MS;
    if (!recent || storage.get("session", ACK_KEY) === job.id) return false;
    this.attach(job, { focus: false, resumedFinished: true });
    return true;
  }

  show() {
    if (this.job) this.app.showView("job", { focus: true });
  }

  // ─── Polling ───────────────────────────────────────────────────────────────

  stopPolling() {
    window.clearTimeout(this.timer);
    this.timer = 0;
    window.clearInterval(this.clock);
    this.clock = 0;
  }

  async poll() {
    const job = this.job;
    if (!job) return;
    const id = job.id;
    let data;
    try {
      data = await api.get(`/api/jobs/${encodeURIComponent(id)}?since=${this.lastSeq}`);
    } catch (error) {
      if (!this.job || this.job.id !== id) return;
      if (error.code === "job_not_found") {
        this.lost();
        return;
      }
      if (error.code === "forbidden") return;
      this.timer = window.setTimeout(() => this.poll(), RETRY_MS);
      return;
    }
    if (!this.job || this.job.id !== id) return;
    const snapshot = data.job;
    this.apply(snapshot);
    if (isFinished(snapshot)) {
      if (snapshot.events.length >= MAX_EVENTS_PER_RESPONSE) {
        this.timer = window.setTimeout(() => this.poll(), 0);
        return;
      }
      this.finish();
      return;
    }
    const delay = snapshot.events.length >= MAX_EVENTS_PER_RESPONSE ? 0 : POLL_MS;
    this.timer = window.setTimeout(() => this.poll(), delay);
  }

  apply(snapshot) {
    for (const event of snapshot.events || []) {
      if (event.seq <= this.lastSeq) continue;
      this.lastSeq = event.seq;
      this.handleEvent(event);
    }
    this.job = { ...snapshot, events: [] };
    this.render();
  }

  handleEvent(event) {
    if (event.kind === "progress" || event.kind === "step") {
      this.progress.textContent = event.message;
    }
    if (event.kind === "warning" && ROBOTS_RE.test(event.message)) {
      this.robotsText.textContent = event.message;
      this.robots.hidden = false;
    }
    if (LOGGED.has(event.kind)) this.addLogLine(event);
  }

  addLogLine(event) {
    const nearBottom = this.log.scrollHeight - this.log.scrollTop - this.log.clientHeight < 48;
    const text =
      event.kind === "step" && event.step
        ? `Bước ${event.step}/${event.total || STEP_COUNT}: ${event.message}`
        : event.message;
    const line = h(
      "li",
      { class: "log-line", dataset: { kind: event.kind, seq: event.seq } },
      icon(LOG_ICONS[event.kind] || "info", event.kind === "step" ? "step-icon" : ""),
      h("time", { class: "log-time", text: formatClock(event.at), attrs: { datetime: event.at } }),
      h(
        "span",
        { class: "log-text" },
        text,
        event.hint ? h("span", { class: "log-hint", text: event.hint }) : null,
      ),
    );
    this.log.appendChild(line);
    this.lines += 1;
    while (this.lines > MAX_LOG_LINES && this.log.firstElementChild) {
      this.log.firstElementChild.remove();
      this.lines -= 1;
    }
    if (nearBottom) this.log.scrollTop = this.log.scrollHeight;
  }

  lost() {
    this.stopPolling();
    const job = this.job;
    this.job = { ...job, status: "failed", error: null };
    this.render();
    this.showOutcome({
      kind: "danger",
      title: "Không còn theo dõi được lượt phân tích này.",
      hint: "Có thể giao diện đã được mở lại. Hãy chạy lại phân tích.",
      actions: [
        { label: "Phân tích lại", primary: true, run: () => this.app.startJob(job.url) },
        { label: "Về trang đầu", run: () => this.app.showStart({ url: job.url, focusUrl: true }) },
      ],
    });
    this.app.onJobChange(this.job);
  }

  // ─── Rendering ─────────────────────────────────────────────────────────────

  render() {
    const job = this.job;
    if (!job) return;
    const site = job.site || job.url;
    const titles = {
      queued: `Đang chuẩn bị phân tích ${site}`,
      running: `Đang phân tích ${site}`,
      succeeded: `Đã phân tích xong ${site}`,
      failed: `Không phân tích được ${site}`,
      cancelled: `Đã hủy phân tích ${site}`,
    };
    this.title.textContent = titles[job.status] || titles.running;
    this.urlText.textContent = job.url;
    this.renderElapsed();
    this.renderRuler();
    const running = !isFinished(job);
    this.cancelButton.hidden = !running;
    this.cancelButton.disabled = Boolean(job.cancel_requested);
    this.cancelButton.setAttribute("aria-busy", job.cancel_requested ? "true" : "false");
    this.cancelLabel.textContent = job.cancel_requested ? "Đang hủy…" : "Hủy phân tích";
    if (job.cancel_requested && running) {
      this.progress.textContent =
        "Đang hủy… Công cụ dừng ở bước kế tiếp, có thể mất vài giây nếu trang đang tải.";
    }
  }

  renderElapsed() {
    const job = this.job;
    const start = parseDate(job.started_at) || parseDate(job.created_at);
    if (!start) {
      this.elapsed.textContent = "";
      return;
    }
    const end = parseDate(job.finished_at);
    const ms = (end ? end.getTime() : Date.now()) - start.getTime();
    this.elapsed.textContent = end ? `Mất ${formatDuration(ms)}` : `Đã chạy ${formatDuration(ms)}`;
  }

  renderRuler() {
    const job = this.job;
    const status = job.status;
    const current = job.step || (status === "queued" ? 0 : 1);
    let fill;
    if (status === "succeeded") fill = 100;
    else if (current === 0) fill = 2;
    else fill = ((current - 0.5) / STEP_COUNT) * 100;
    this.rulerFill.style.width = `${Math.min(100, fill)}%`;
    this.ruler.dataset.state = status === "queued" ? "running" : status;
    this.steps.forEach((item, index) => {
      const number = index + 1;
      let state = "todo";
      if (status === "succeeded" || number < current) state = "done";
      else if (number === current) state = status === "failed" ? "failed" : "current";
      item.dataset.state = state;
      if (state === "current" || state === "failed") {
        item.setAttribute("aria-current", "step");
      } else {
        item.removeAttribute("aria-current");
      }
      const label = item.querySelector(".ruler-name");
      const statusText = { done: "xong", current: "đang làm", failed: "lỗi", todo: "chưa làm" }[state];
      item.setAttribute("aria-label", `${label ? label.textContent : ""}: ${statusText}`);
    });
  }

  startClock() {
    window.clearInterval(this.clock);
    if (!this.active) return;
    this.clock = window.setInterval(() => {
      if (!this.job || isFinished(this.job)) {
        window.clearInterval(this.clock);
        return;
      }
      this.renderElapsed();
    }, 1000);
  }

  // ─── Cancel / finish ───────────────────────────────────────────────────────

  async cancel() {
    const job = this.job;
    if (!job || isFinished(job)) return;
    this.cancelButton.disabled = true;
    this.cancelLabel.textContent = "Đang hủy…";
    try {
      const data = await api.post(`/api/jobs/${encodeURIComponent(job.id)}/cancel`);
      if (this.job && this.job.id === job.id && !isFinished(this.job)) {
        this.job = { ...this.job, cancel_requested: Boolean(data.job.cancel_requested) };
        this.render();
      }
    } catch (error) {
      this.cancelButton.disabled = false;
      this.cancelLabel.textContent = "Hủy phân tích";
      if (error.code === "job_not_found") this.lost();
    }
  }

  finish() {
    this.stopPolling();
    const job = this.job;
    this.render();
    this.progress.textContent = "";
    storage.set("session", ACK_KEY, job.id);
    this.app.onJobChange(job);
    this.app.refreshHistory();
    if (job.status === "succeeded") {
      const openAfter = Boolean(job.settings && job.settings.open_after);
      this.showOutcome({
        kind: "ok",
        title: "Đã xong. DESIGN.md và các tệp đi kèm đã nằm trong thư mục kết quả.",
        actions: [
          {
            label: "Xem kết quả",
            primary: true,
            run: () => this.app.openResults(job.site, { focus: true }),
          },
          { label: "Phân tích trang khác", run: () => this.app.showStart({ focusUrl: true }) },
        ],
      });
      if (openAfter && !this.resumedFinished) {
        this.app.openResults(job.site, { focus: true });
      }
      return;
    }
    if (job.status === "cancelled") {
      const error = job.error || {};
      this.showOutcome({
        kind: "info",
        title: error.message || "Đã hủy phân tích theo yêu cầu của bạn.",
        hint: error.hint,
        actions: [
          { label: "Phân tích lại", primary: true, run: () => this.app.startJob(job.url) },
          { label: "Sửa địa chỉ", run: () => this.app.showStart({ url: job.url, focusUrl: true }) },
        ],
      });
      return;
    }
    const error = job.error || { code: "unexpected", message: "Đã xảy ra lỗi.", hint: null };
    // For a missing browser the panel's own help (Edge/Chrome or "Tải Chromium") replaces the
    // command-line hint, and downloading Chromium is the main action.
    const browserMissing = error.code === "browser_missing";
    this.showOutcome({
      kind: "danger",
      title: error.message,
      hint: browserMissing ? null : error.hint,
      code: error.code,
      actions: [
        { label: "Thử lại", primary: !browserMissing, run: () => this.app.startJob(job.url) },
        { label: "Sửa địa chỉ", run: () => this.app.showStart({ url: job.url, focusUrl: true }) },
      ],
    });
    if (browserMissing) this.app.browsers.refresh();
  }

  showOutcome({ kind, title, hint = null, code = null, actions = [] }) {
    const icons = { ok: "ok", danger: "error", info: "info" };
    const body = h(
      "div",
      { class: "callout-body" },
      h("h2", { class: "callout-title", text: title }),
      hint ? h("p", { class: "outcome-hint", text: hint }) : null,
    );
    const extra = code ? this.errorHelp(code) : null;
    if (extra) body.appendChild(extra);
    const buttons = h("div", { class: "outcome-actions" });
    for (const action of actions) {
      buttons.appendChild(
        h(
          "button",
          {
            class: action.primary ? "btn btn-primary" : "btn btn-secondary",
            attrs: { type: "button" },
            on: { click: action.run },
          },
          action.label,
        ),
      );
    }
    body.appendChild(buttons);
    const panel = h(
      "div",
      {
        class: `callout callout-${kind === "info" ? "info outcome-info" : kind}`,
        attrs: { role: kind === "danger" ? "alert" : "status" },
        dataset: { outcome: kind, code: code || "" },
      },
      icon(icons[kind] || "info", "callout-icon"),
      body,
    );
    this.outcome.replaceChildren(panel);
    this.outcome.hidden = false;
    const slot = panel.querySelector("[data-install-slot]");
    if (slot) mountInstallWidget(slot, this.app.browsers);
  }

  /** Extra, plain-language help for a JobError code (null when there is nothing to add). */
  errorHelp(code) {
    const extra = (...children) => h("div", { class: "extra" }, ...children);
    switch (code) {
      case "browser_missing":
        return extra(
          h("p", { text: "Máy chưa có trình duyệt mà công cụ dùng được. Chọn một trong hai cách:" }),
          h(
            "ul",
            {},
            h("li", {
              text: "Cài Microsoft Edge hoặc Google Chrome. Công cụ tự nhận ra, không cần chỉnh gì thêm.",
            }),
            h("li", { text: "Hoặc tải Chromium ngay tại đây (khoảng 150 MB, vài phút), rồi bấm Thử lại." }),
          ),
          h("div", { class: "install-slot", attrs: { "data-install-slot": "" } }),
        );
      case "bot_blocked":
        return extra(
          h("p", {
            text:
              "Captcha và trang kiểm tra trình duyệt chỉ cho người thật đi qua. Trang chủ thường chặn chặt hơn " +
              "các trang như giới thiệu, bảng giá hay blog: thử dán địa chỉ của một trang như vậy.",
          }),
        );
      case "login_required":
        return extra(
          h("p", {
            text:
              "Công cụ chỉ đọc được trang công khai. Hãy dùng trang chủ hoặc một trang giới thiệu không cần đăng nhập.",
          }),
        );
      case "timeout": {
        const settings = this.app.settings.settings;
        const limits = this.app.settings.limits;
        const current = settings ? settings.timeout_s : 45;
        const max = limits ? limits.timeout_s[1] : 600;
        const next = Math.min(max, Math.max(90, current * 2));
        const children = [
          h("p", {
            text: `Trang tải lâu hơn thời gian chờ hiện tại (${formatNumber(current)} giây). Mạng chậm hoặc trang quá nặng thường gây ra lỗi này.`,
          }),
        ];
        if (next > current) {
          children.push(
            h(
              "div",
              { class: "outcome-actions" },
              h(
                "button",
                {
                  class: "btn btn-secondary",
                  attrs: { type: "button" },
                  on: {
                    click: async () => {
                      const ok = await this.app.settings.saveNow({ timeout_s: next });
                      if (ok && this.job) this.app.startJob(this.job.url);
                    },
                  },
                },
                `Tăng thời gian chờ lên ${formatNumber(next)} giây rồi thử lại`,
              ),
            ),
          );
        }
        return extra(...children);
      }
      case "invalid_url":
        return extra(
          h("p", {
            text:
              "Kiểm tra lại địa chỉ: chỉ cần tên miền như stripe.com, hoặc một liên kết đầy đủ bắt đầu bằng https://.",
          }),
        );
      case "navigation":
        return extra(
          h("p", {
            text:
              "Không mở được trang. Kiểm tra địa chỉ có gõ đúng không, máy có kết nối Internet không, rồi thử lại.",
          }),
        );
      case "unexpected":
        return extra(
          h("p", { text: "Đây là lỗi ngoài dự kiến. Khi báo lỗi, hãy gửi kèm nhật ký chi tiết:" }),
          h(
            "ul",
            {},
            h("li", {}, "Dùng ứng dụng (.exe): tệp ", h("code", { text: logPath() }), "."),
            h("li", { text: "Chạy bằng lệnh gdm ui: các dòng thông báo trong cửa sổ dòng lệnh." }),
          ),
        );
      default:
        return null;
    }
  }
}

function logPath() {
  const platform = (
    (navigator.userAgentData && navigator.userAgentData.platform) ||
    navigator.platform ||
    ""
  ).toLowerCase();
  if (platform.startsWith("mac")) return "~/Library/Logs/get-design-markdown/ui.log";
  if (platform.includes("linux")) return "~/.local/state/get-design-markdown/logs/ui.log";
  return "%LOCALAPPDATA%\\get-design-markdown\\logs\\ui.log";
}
