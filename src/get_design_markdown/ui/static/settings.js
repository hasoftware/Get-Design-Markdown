// The options form: filled from GET /api/settings, every change saved with PUT /api/settings
// (one field per request, debounced while typing). A 422 answer is shown next to its field.

import { api, ApiError } from "./api.js";
import { byId, formatNumber } from "./dom.js";

const NUMERIC = { pages: "int", max_elements: "int", timeout_s: "float", delta_e: "float" };
const TYPING_DELAY_MS = 600;
const PATH_DELAY_MS = 900;
const IDLE_STATUS = "Được lưu tự động cho các lần sau.";
/** Fields "Đặt lại" restores (the output folder is left alone on purpose). */
const RESETTABLE = [
  "pages",
  "dark",
  "lang",
  "prompt_only",
  "examples",
  "max_elements",
  "timeout_s",
  "delta_e",
  "browser",
  "open_after",
];

export class SettingsForm {
  /** onChange(response, {fields, rootChanged}) runs after every successful save or load. */
  constructor({ onChange }) {
    this.onChange = onChange;
    this.response = null;
    this.pending = new Map();
    this.timers = new Map();
    this.errors = new Map();
    this.chain = Promise.resolve();
    this.statusTimer = 0;
    this.status = byId("settings-status");
    this.loadError = byId("settings-error");
    this.controls = Array.from(document.querySelectorAll("[data-setting]"));
    this.pagesInput = byId("opt-pages");
    this.outputInput = byId("opt-output");
    this.bind();
  }

  get settings() {
    return this.response ? this.response.settings : null;
  }

  get limits() {
    return this.response ? this.response.limits : null;
  }

  get locked() {
    return Boolean(this.response && this.response.output_root_locked);
  }

  async load() {
    try {
      const response = await api.get("/api/settings");
      const previousRoot = this.response ? this.response.output_root : null;
      this.response = response;
      this.loadError.hidden = true;
      this.fill();
      this.onChange(response, {
        fields: Object.keys(response.settings),
        rootChanged: previousRoot !== null && previousRoot !== response.output_root,
      });
      return response;
    } catch (error) {
      byId("settings-error-text").textContent =
        `Không tải được tùy chọn: ${error.message}` + (error.hint ? ` ${error.hint}` : "");
      this.loadError.hidden = false;
      throw error;
    }
  }

  // ─── Form <-> settings ─────────────────────────────────────────────────────

  controlsFor(name) {
    return this.controls.filter((control) => control.dataset.setting === name);
  }

  /** Put the saved values into the controls (fields with a visible error keep the input). */
  fill(only = null) {
    if (!this.response) return;
    const { settings, limits } = this.response;
    for (const control of this.controls) {
      const name = control.dataset.setting;
      if (only && !only.includes(name)) continue;
      if (this.errors.has(name) && !only) continue;
      const value = settings[name];
      if (control.type === "checkbox") {
        control.checked = Boolean(value);
      } else if (control.type === "radio") {
        control.checked = control.value === value;
      } else if (name === "output_dir") {
        if (document.activeElement !== control || only) control.value = String(value ?? "");
      } else {
        control.value = value === null || value === undefined ? "" : String(value);
      }
      if (limits && limits[name]) {
        control.min = String(limits[name][0]);
        control.max = String(limits[name][1]);
      }
    }
    const locked = this.locked;
    this.outputInput.readOnly = locked;
    byId("opt-output-locked").hidden = !locked;
    byId("opt-output-help").hidden = locked;
    if (locked && (!only || only.includes("output_dir"))) {
      this.outputInput.value = this.response.output_root;
    }
    this.updateStepper();
  }

  updateStepper() {
    const limits = this.limits ? this.limits.pages : [0, 10];
    const value = Number(this.pagesInput.value);
    for (const button of document.querySelectorAll(".stepper [data-step]")) {
      const step = Number(button.dataset.step);
      button.disabled =
        Number.isFinite(value) && this.pagesInput.value !== ""
          ? (step < 0 && value <= limits[0]) || (step > 0 && value >= limits[1])
          : false;
    }
  }

  /** {value} from a control, or {error} when it cannot be sent, or {skip} while typing. */
  read(control, { committing }) {
    const name = control.dataset.setting;
    if (control.type === "checkbox") return { value: control.checked };
    if (control.type === "radio") return { value: control.value };
    if (NUMERIC[name]) {
      const text = control.value.trim();
      if (text === "" || Number.isNaN(Number(text))) {
        return committing ? { error: this.rangeMessage(name) } : { skip: true };
      }
      return { value: Number(text) };
    }
    if (name === "output_dir") {
      const text = control.value.trim();
      if (!text) {
        return committing ? { error: "Nhập đường dẫn của một thư mục." } : { skip: true };
      }
      return { value: text };
    }
    return { value: control.value };
  }

  rangeMessage(name) {
    const limits = this.limits && this.limits[name];
    if (!limits) return "Nhập một số hợp lệ.";
    const kind = NUMERIC[name] === "int" ? "một số nguyên" : "một số";
    return `Nhập ${kind} từ ${formatNumber(limits[0])} đến ${formatNumber(limits[1])}.`;
  }

  bind() {
    for (const control of this.controls) {
      const name = control.dataset.setting;
      const immediate = control.type === "checkbox" || control.type === "radio" || control.tagName === "SELECT";
      if (immediate) {
        control.addEventListener("change", () => this.handle(control, true, 0));
        continue;
      }
      const delay = name === "output_dir" ? PATH_DELAY_MS : TYPING_DELAY_MS;
      control.addEventListener("input", () => {
        if (name === "pages") this.updateStepper();
        this.handle(control, false, delay);
      });
      control.addEventListener("change", () => this.handle(control, true, 0));
    }
    for (const button of document.querySelectorAll(".stepper [data-step]")) {
      button.addEventListener("click", () => {
        const limits = this.limits ? this.limits.pages : [0, 10];
        const current = Number(this.pagesInput.value) || 0;
        const next = Math.min(limits[1], Math.max(limits[0], Math.round(current) + Number(button.dataset.step)));
        this.pagesInput.value = String(next);
        this.updateStepper();
        this.handle(this.pagesInput, true, 250);
      });
    }
    byId("settings-retry").addEventListener("click", () => {
      this.load().catch(() => {});
    });
    byId("settings-reset").addEventListener("click", () => this.reset());
  }

  handle(control, committing, delay) {
    const name = control.dataset.setting;
    if (name === "output_dir" && this.locked) return;
    const result = this.read(control, { committing });
    if (result.skip) {
      this.cancel(name);
      return;
    }
    if (result.error) {
      this.cancel(name);
      this.showError(name, result.error);
      return;
    }
    if (!committing && NUMERIC[name]) this.clearError(name);
    this.queue(name, result.value, delay);
  }

  // ─── Saving ────────────────────────────────────────────────────────────────

  queue(name, value, delay) {
    this.pending.set(name, value);
    window.clearTimeout(this.timers.get(name));
    this.timers.set(
      name,
      window.setTimeout(() => this.send(name), delay),
    );
  }

  cancel(name) {
    window.clearTimeout(this.timers.get(name));
    this.timers.delete(name);
    this.pending.delete(name);
  }

  send(name) {
    if (!this.pending.has(name)) return this.chain;
    const value = this.pending.get(name);
    this.cancel(name);
    this.chain = this.chain.then(() => this.put({ [name]: value }));
    return this.chain;
  }

  /** Send every pending change now and wait for the saves to finish. */
  async flush() {
    for (const name of Array.from(this.pending.keys())) this.send(name);
    await this.chain;
  }

  /** Save `changes` right away (e.g. a longer timeout from the error panel); true on success. */
  async saveNow(changes) {
    await this.flush();
    let ok = false;
    this.chain = this.chain.then(async () => {
      ok = await this.put(changes, { refill: true });
    });
    await this.chain;
    return ok;
  }

  async reset() {
    if (!this.response) return;
    const defaults = this.response.defaults;
    const changes = {};
    for (const name of RESETTABLE) changes[name] = defaults[name];
    for (const name of RESETTABLE) {
      this.cancel(name);
      this.clearError(name);
    }
    const ok = await this.saveNow(changes);
    if (ok) this.setStatus("saved", "Đã đặt lại tùy chọn về mặc định.");
  }

  async put(changes, { refill = false } = {}) {
    const fields = Object.keys(changes);
    try {
      const response = await api.put("/api/settings", changes);
      const previousRoot = this.response ? this.response.output_root : null;
      this.response = response;
      for (const name of fields) this.clearError(name);
      if (refill || fields.includes("output_dir")) this.fill(fields);
      this.setStatus("saved", "Đã lưu.");
      this.onChange(response, {
        fields,
        rootChanged: previousRoot !== null && previousRoot !== response.output_root,
      });
      return true;
    } catch (error) {
      const field =
        error instanceof ApiError && error.details && typeof error.details.field === "string"
          ? error.details.field.split(".")[0]
          : fields[0];
      const target = this.controlsFor(field).length ? field : fields[0];
      if (error instanceof ApiError && error.code === "invalid" && NUMERIC[target]) {
        this.showError(target, this.rangeMessage(target));
      } else {
        this.showError(target, error.message, error.hint);
      }
      this.setStatus("error", "Chưa lưu được: xem chỗ báo đỏ bên dưới.");
      return false;
    }
  }

  setStatus(state, text) {
    window.clearTimeout(this.statusTimer);
    this.status.dataset.state = state;
    this.status.textContent = text;
    if (state === "saved") {
      this.statusTimer = window.setTimeout(() => {
        delete this.status.dataset.state;
        this.status.textContent = IDLE_STATUS;
      }, 2500);
    }
  }

  // ─── Field errors ──────────────────────────────────────────────────────────

  errorElement(name) {
    return document.getElementById(`opt-${name}-error`);
  }

  showError(name, message, hint = null) {
    const element = this.errorElement(name);
    if (!element) return;
    this.errors.set(name, message);
    element.textContent = message;
    if (hint) {
      const span = document.createElement("span");
      span.className = "hint";
      span.textContent = hint;
      element.appendChild(span);
    }
    element.hidden = false;
    for (const control of this.controlsFor(name)) control.setAttribute("aria-invalid", "true");
    const option = element.closest(".option");
    if (option) option.dataset.invalid = "true";
  }

  clearError(name) {
    const element = this.errorElement(name);
    this.errors.delete(name);
    if (!element) return;
    element.hidden = true;
    element.textContent = "";
    for (const control of this.controlsFor(name)) control.removeAttribute("aria-invalid");
    const option = element.closest(".option");
    if (option) delete option.dataset.invalid;
  }

  /** The first control whose value could not be saved, or null. */
  firstInvalid() {
    for (const name of this.errors.keys()) {
      const control = this.controlsFor(name)[0];
      if (control) return control;
    }
    return null;
  }
}
