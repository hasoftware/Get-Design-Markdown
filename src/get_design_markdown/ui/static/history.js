// History sidebar: the analysed sites under the output folder (GET /api/history).

import { api } from "./api.js";
import { byId, clear, filesUrl, formatDate, h, hostOf } from "./dom.js";

export class HistoryList {
  /** onOpen(site) runs when an entry is chosen. */
  constructor({ onOpen }) {
    this.onOpen = onOpen;
    this.entries = [];
    this.active = null;
    this.list = byId("history-list");
    this.empty = byId("history-empty");
    this.loading = byId("history-loading");
    this.pending = null;
  }

  /** Reload the list (concurrent calls share one request). */
  load() {
    if (this.pending) return this.pending;
    this.pending = this.request().finally(() => {
      this.pending = null;
    });
    return this.pending;
  }

  async request() {
    try {
      const data = await api.get("/api/history");
      this.entries = data.entries || [];
      this.render();
    } catch (error) {
      if (error.code === "forbidden") return;
      this.loading.hidden = false;
      this.loading.textContent = `Không tải được lịch sử: ${error.message}`;
    }
  }

  render() {
    this.loading.hidden = true;
    clear(this.list);
    this.empty.hidden = this.entries.length > 0;
    this.list.hidden = this.entries.length === 0;
    for (const entry of this.entries) {
      const name = entry.name || entry.site;
      const thumb = h("span", { class: "history-thumb", attrs: { "aria-hidden": "true" } });
      if (entry.thumbnail) {
        thumb.appendChild(
          h("img", {
            attrs: {
              src: filesUrl(entry.site, entry.thumbnail),
              alt: "",
              loading: "lazy",
              decoding: "async",
            },
          }),
        );
      } else {
        thumb.textContent = name.trim().charAt(0).toUpperCase() || "?";
      }
      const details = [];
      if (entry.analyzed_at) details.push(formatDate(entry.analyzed_at));
      if (entry.pages && entry.pages.length) details.push(`${entry.pages.length} trang`);
      const button = h(
        "button",
        {
          class: "history-item",
          attrs: {
            type: "button",
            title: entry.pages && entry.pages.length ? `Các trang: ${entry.pages.join(", ")}` : null,
            "aria-current": entry.site === this.active ? "true" : null,
          },
          dataset: { site: entry.site },
          on: { click: () => this.onOpen(entry.site) },
        },
        thumb,
        h(
          "span",
          { class: "history-text" },
          h("span", { class: "history-name", text: name }),
          h("span", { class: "history-host", text: entry.url ? hostOf(entry.url) : entry.site }),
          details.length ? h("span", { class: "history-date", text: details.join(", ") }) : null,
        ),
      );
      this.list.appendChild(h("li", {}, button));
    }
  }

  setActive(site) {
    this.active = site;
    for (const button of this.list.querySelectorAll(".history-item")) {
      if (button.dataset.site === site) {
        button.setAttribute("aria-current", "true");
      } else {
        button.removeAttribute("aria-current");
      }
    }
  }

  firstButton() {
    return this.list.querySelector(".history-item");
  }
}
