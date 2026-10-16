// Results of one analysed site (GET /api/results/{site}): header, summary, and five tabs
// (preview, DESIGN.md, use with AI, screenshots, files) following the ARIA tab pattern.

import { api } from "./api.js";
import {
  byId,
  clear,
  copyWithFeedback,
  filesUrl,
  formatBytes,
  formatDate,
  h,
  icon,
  safeHttpUrl,
  storage,
  toast,
} from "./dom.js";
import { frontMatterNodes, markdownFrame, refreshMarkdownFrame } from "./frame.js";

const OPEN_SITE_KEY = "gdm.openSite";
const TAB_KEY = "gdm.resultsTab";
const TOKEN_LABELS = {
  colors: "màu",
  typography: "cấp chữ",
  rounded: "mức bo góc",
  spacing: "bước khoảng cách",
  components: "thành phần",
};
const FILE_PURPOSES = {
  design: "Bản mô tả phong cách để đưa cho AI",
  preview: "Trang xem trước màu, chữ và thành phần",
  tokens: "Token thiết kế chuẩn W3C cho công cụ thiết kế hoặc lập trình",
  prompt: "Lời nhắc để AI viết lại phần mô tả",
  analysis: "Số liệu đã phân tích (dành cho lập trình viên)",
  synthesis: "Kết quả tự kiểm tra DESIGN.md",
  raw: "Dữ liệu thô đo được trên trang",
  image: "Ảnh chụp màn hình",
  other: "Tệp khác",
};
const FILE_ICONS = { design: "doc", preview: "eye", image: "image" };
// First warning of a folder whose files come from two runs (server.mixed_runs_note).
const MIXED_RUNS_PREFIX = "Lượt chạy gần nhất của trang này chưa đi hết các bước";

function deviceOf(width) {
  if (width >= 1200) return { size: "desktop", label: "Máy tính" };
  if (width >= 700) return { size: "tablet", label: "Máy tính bảng" };
  return { size: "phone", label: "Điện thoại" };
}

export class ResultsView {
  /** app: { showView(name, opts), showStart(opts), refreshHistory(), setActiveSite(site),
   *          openFolder(site), openAdvanced(focusId), startJob(url) } */
  constructor(app) {
    this.app = app;
    this.results = null;
    this.frames = [];
    this.tabs = Array.from(document.querySelectorAll('#results-tabs [role="tab"]'));
    this.currentTab = storage.get("session", TAB_KEY) || "tab-preview";
    this.bindTabs();
    this.bindButtons();
    this.bindLightbox();
  }

  get site() {
    return this.results ? this.results.site : null;
  }

  // ─── Loading ───────────────────────────────────────────────────────────────

  async open(site, { focus = true } = {}) {
    byId("loading-text").textContent = "Đang mở kết quả…";
    this.app.showView("loading", { focus: false });
    let data;
    try {
      data = await api.get(`/api/results/${encodeURIComponent(site)}`);
    } catch (error) {
      if (error.code === "forbidden") return false;
      storage.remove("session", OPEN_SITE_KEY);
      toast(
        error.code === "site_not_found"
          ? `Không còn kết quả cho ${site}. Có thể thư mục đã bị xóa hoặc di chuyển.`
          : `Không mở được kết quả: ${error.message}`,
        "error",
        6000,
      );
      this.app.refreshHistory();
      this.app.showStart({ focusUrl: focus });
      return false;
    }
    this.results = data;
    storage.set("session", OPEN_SITE_KEY, site);
    this.render();
    this.app.showView("results", { focus });
    this.app.setActiveSite(site);
    return true;
  }

  /** Site remembered from a previous visit in this tab (reload), or null. */
  static rememberedSite() {
    return storage.get("session", OPEN_SITE_KEY);
  }

  forget() {
    storage.remove("session", OPEN_SITE_KEY);
  }

  // ─── Rendering ─────────────────────────────────────────────────────────────

  render() {
    const results = this.results;
    this.frames = [];
    this.renderHeader(results);
    this.renderStats(results);
    this.renderWarnings(results);
    this.renderPreview(results);
    this.renderDesign(results);
    this.renderAi(results);
    this.renderShots(results);
    this.renderFiles(results);
    const tab = this.tabs.find((item) => item.id === this.currentTab) || this.tabs[0];
    this.selectTab(tab, { focus: false });
  }

  renderHeader(results) {
    const entry = results.entry;
    const name = entry.name || results.site;
    byId("results-title").textContent = name;
    const thumb = byId("results-thumb");
    clear(thumb);
    if (entry.thumbnail) {
      thumb.appendChild(
        h("img", { attrs: { src: filesUrl(results.site, entry.thumbnail), alt: "", decoding: "async" } }),
      );
    } else {
      thumb.textContent = name.trim().charAt(0).toUpperCase() || "?";
    }
    const urlLine = byId("results-url");
    clear(urlLine);
    const href = safeHttpUrl(entry.url);
    if (href) {
      urlLine.appendChild(
        h(
          "a",
          { attrs: { href, target: "_blank", rel: "noopener noreferrer" } },
          entry.url,
          icon("external"),
          h("span", { class: "sr-only", text: " (mở trong thẻ mới)" }),
        ),
      );
    } else {
      urlLine.textContent = entry.url || results.site;
    }
    const meta = byId("results-meta");
    clear(meta);
    if (entry.analyzed_at) {
      meta.appendChild(h("span", { text: `Phân tích lúc ${formatDate(entry.analyzed_at)}` }));
    }
    const pages = entry.pages || [];
    if (pages.length) {
      meta.appendChild(h("span", { text: `${pages.length} trang: ${pages.join(", ")}` }));
    }
    const hasDesign = Boolean(results.design);
    for (const id of ["copy-design-top", "copy-design", "copy-design-ai"]) {
      byId(id).disabled = !hasDesign;
    }
  }

  renderStats(results) {
    const list = byId("results-stats");
    clear(list);
    const counts = results.token_counts || {};
    for (const [key, value] of Object.entries(counts)) {
      list.appendChild(
        h("li", {}, h("strong", { text: String(value) }), ` ${TOKEN_LABELS[key] || key}`),
      );
    }
    list.hidden = list.children.length === 0;
  }

  renderWarnings(results) {
    const box = byId("results-warnings");
    const list = byId("results-warnings-list");
    clear(list);
    let warnings = results.warnings || [];
    // Results older than the screenshots / data next to them: said up front, not in the notes.
    const mixed = warnings.length > 0 && warnings[0].startsWith(MIXED_RUNS_PREFIX) ? warnings[0] : null;
    byId("results-mixed-text").textContent = mixed || "";
    byId("results-mixed").hidden = mixed === null;
    if (mixed !== null) warnings = warnings.slice(1);
    for (const warning of warnings) list.appendChild(h("li", { text: warning }));
    byId("results-warnings-title").textContent =
      `Ghi chú khi phân tích (${warnings.length})`;
    box.hidden = warnings.length === 0;
    box.open = false;
  }

  renderPreview(results) {
    const wrap = byId("preview-wrap");
    clear(wrap);
    if (!results.preview_url) {
      wrap.appendChild(
        emptyState(
          "Chưa có trang xem trước",
          "Lần phân tích này chưa tạo preview.html. Hãy phân tích lại trang để có trang xem trước.",
        ),
      );
      return;
    }
    const name = results.entry.name || results.site;
    wrap.appendChild(
      h("iframe", {
        attrs: {
          sandbox: "allow-scripts",
          src: results.preview_url,
          title: `Xem trước phong cách của ${name}`,
          referrerpolicy: "no-referrer",
        },
      }),
    );
  }

  renderDesign(results) {
    const wrap = byId("design-wrap");
    const details = byId("front-matter");
    const code = byId("front-matter-code");
    clear(wrap);
    clear(code);
    const name = results.entry.name || results.site;
    const download = filesUrl(results.site, "DESIGN.md") + "?download=1";
    for (const id of ["download-design", "download-design-ai"]) {
      const link = byId(id);
      link.setAttribute("href", download);
      link.hidden = !results.design;
    }
    if (!results.design) {
      details.hidden = true;
      wrap.appendChild(
        emptyState(
          "Chưa có DESIGN.md",
          "Thư mục này chưa có DESIGN.md, có thể do lần phân tích trước bị dừng giữa chừng. Hãy phân tích lại trang.",
        ),
      );
      return;
    }
    if (results.design.front_matter) {
      code.append(...frontMatterNodes(results.design.front_matter));
      details.hidden = false;
    } else {
      details.hidden = true;
    }
    const frame = markdownFrame(results.design.html, { title: `DESIGN.md của ${name}` });
    this.frames.push({ frame, html: results.design.html });
    wrap.appendChild(frame);
  }

  renderAi(results) {
    const present = byId("prompt-present");
    const absent = byId("prompt-absent");
    const images = byId("prompt-images");
    const wrap = byId("prompt-wrap");
    clear(images);
    clear(wrap);
    if (!results.prompt) {
      present.hidden = true;
      absent.hidden = false;
      return;
    }
    present.hidden = false;
    absent.hidden = true;
    byId("download-prompt").setAttribute(
      "href",
      filesUrl(results.site, "prompt.md") + "?download=1",
    );
    (results.prompt_images || []).forEach((url, index) => {
      const fileName = decodeURIComponent(url.split("/").pop() || `anh-${index + 1}.png`);
      images.appendChild(
        h(
          "li",
          {},
          h(
            "a",
            {
              attrs: { href: url, target: "_blank", rel: "noopener noreferrer" },
              on: {
                click: (event) => {
                  event.preventDefault();
                  this.openLightbox(url, `Ảnh đính kèm: ${fileName}`);
                },
              },
            },
            h("img", { attrs: { src: url, alt: "", loading: "lazy", decoding: "async" } }),
            h("span", { text: fileName }),
          ),
        ),
      );
    });
    images.hidden = images.children.length === 0;
    const name = results.entry.name || results.site;
    const frame = markdownFrame(results.prompt.html, { title: `prompt.md của ${name}` });
    this.frames.push({ frame, html: results.prompt.html });
    wrap.appendChild(frame);
  }

  renderShots(results) {
    const container = byId("shots");
    clear(container);
    const shots = results.screenshots || [];
    if (!shots.length) {
      container.appendChild(
        emptyState(
          "Không có ảnh chụp",
          "Lần phân tích này không lưu ảnh chụp màn hình, hoặc ảnh đã bị xóa khỏi thư mục kết quả.",
        ),
      );
      return;
    }
    const groups = new Map();
    for (const shot of shots) {
      if (!groups.has(shot.page)) groups.set(shot.page, []);
      groups.get(shot.page).push(shot);
    }
    for (const [page, items] of groups) {
      items.sort(
        (a, b) =>
          (a.color_scheme === "dark") - (b.color_scheme === "dark") ||
          b.viewport_width - a.viewport_width,
      );
      const list = h("ul", { class: "shot-grid" });
      for (const shot of items) {
        const device = deviceOf(shot.viewport_width);
        const dark = shot.color_scheme === "dark";
        const caption = `${device.label}, ${shot.viewport_width} px${dark ? ", bản tối" : ""}`;
        list.appendChild(
          h(
            "li",
            {},
            h(
              "button",
              {
                class: "shot",
                attrs: { type: "button", "aria-label": `Xem lớn: trang ${page}, ${caption}` },
                dataset: { size: device.size },
                on: { click: () => this.openLightbox(shot.url, `Trang ${page}: ${caption}`) },
              },
              h(
                "span",
                { class: "shot-frame" },
                h("img", { attrs: { src: shot.url, alt: "", loading: "lazy", decoding: "async" } }),
              ),
              h(
                "span",
                { class: "shot-caption" },
                h("strong", { text: `${device.label}${dark ? ", bản tối" : ""}` }),
                `${shot.viewport_width} px`,
              ),
            ),
          ),
        );
      }
      container.appendChild(
        h("section", { class: "shot-group" }, h("h3", { text: `Trang “${page}”` }), list),
      );
    }
  }

  renderFiles(results) {
    byId("folder-path").textContent = results.folder;
    const body = byId("files-body");
    clear(body);
    for (const file of results.files || []) {
      const slash = file.path.lastIndexOf("/");
      const dir = slash >= 0 ? file.path.slice(0, slash + 1) : "";
      body.appendChild(
        h(
          "tr",
          {},
          h(
            "td",
            {},
            h(
              "span",
              { class: "file-name" },
              icon(FILE_ICONS[file.kind] || "file"),
              h(
                "span",
                {},
                h("span", { class: "file-base", text: file.name }),
                dir ? h("span", { class: "file-dir", text: `trong ${dir.replace(/\/$/, "")}` }) : null,
              ),
            ),
          ),
          h("td", { class: "file-purpose", text: FILE_PURPOSES[file.kind] || FILE_PURPOSES.other }),
          h("td", { class: "num", text: formatBytes(file.size) }),
          h(
            "td",
            {},
            h(
              "span",
              { class: "file-actions" },
              h(
                "a",
                {
                  class: "btn btn-ghost btn-sm",
                  attrs: {
                    href: file.url,
                    target: "_blank",
                    rel: "noopener noreferrer",
                    "aria-label": `Mở ${file.path} trong thẻ mới`,
                  },
                },
                icon("external"),
                h("span", { text: "Mở" }),
              ),
              h(
                "a",
                {
                  class: "btn btn-ghost btn-sm",
                  attrs: {
                    href: `${file.url}?download=1`,
                    download: file.name,
                    "aria-label": `Tải về ${file.path}`,
                  },
                },
                icon("download"),
                h("span", { text: "Tải về" }),
              ),
            ),
          ),
        ),
      );
    }
  }

  /** Rebuild the Markdown frames after a theme change. */
  refreshFrames() {
    for (const { frame, html } of this.frames) {
      if (frame.isConnected) refreshMarkdownFrame(frame, html);
    }
  }

  // ─── Tabs ──────────────────────────────────────────────────────────────────

  bindTabs() {
    for (const tab of this.tabs) {
      tab.addEventListener("click", () => this.selectTab(tab, { focus: false }));
      tab.addEventListener("keydown", (event) => {
        const index = this.tabs.indexOf(tab);
        let next = null;
        if (event.key === "ArrowRight") next = this.tabs[(index + 1) % this.tabs.length];
        else if (event.key === "ArrowLeft") {
          next = this.tabs[(index - 1 + this.tabs.length) % this.tabs.length];
        } else if (event.key === "Home") next = this.tabs[0];
        else if (event.key === "End") next = this.tabs[this.tabs.length - 1];
        if (next) {
          event.preventDefault();
          this.selectTab(next, { focus: true });
        }
      });
    }
  }

  selectTab(tab, { focus }) {
    for (const item of this.tabs) {
      const selected = item === tab;
      item.setAttribute("aria-selected", selected ? "true" : "false");
      item.tabIndex = selected ? 0 : -1;
      byId(item.getAttribute("aria-controls")).hidden = !selected;
    }
    this.currentTab = tab.id;
    storage.set("session", TAB_KEY, tab.id);
    if (focus) tab.focus();
  }

  showTab(id) {
    const tab = this.tabs.find((item) => item.id === id);
    if (tab) this.selectTab(tab, { focus: true });
  }

  // ─── Buttons ───────────────────────────────────────────────────────────────

  bindButtons() {
    const copyDesign = (button) => async () => {
      if (!this.results || !this.results.design) return;
      await copyWithFeedback(
        button,
        this.results.design.markdown,
        "Đã sao chép DESIGN.md. Dán vào trợ lý AI của bạn.",
      );
    };
    for (const id of ["copy-design-top", "copy-design", "copy-design-ai"]) {
      const button = byId(id);
      button.addEventListener("click", copyDesign(button));
    }
    const copyPrompt = byId("copy-prompt");
    copyPrompt.addEventListener("click", async () => {
      if (!this.results || !this.results.prompt) return;
      await copyWithFeedback(copyPrompt, this.results.prompt.markdown, "Đã sao chép prompt.md.");
    });
    const copySample = byId("copy-sample");
    copySample.addEventListener("click", async () => {
      await copyWithFeedback(copySample, byId("sample-text").textContent.trim(), "Đã sao chép lời nhắc mẫu.");
    });
    byId("results-mixed-rerun").addEventListener("click", () => {
      if (!this.results) return;
      const url = this.results.entry.url;
      if (url) this.app.startJob(url);
      else this.app.showStart({ url: this.results.site, focusUrl: true });
    });
    const copyFolder = byId("copy-folder");
    copyFolder.addEventListener("click", async () => {
      if (!this.results) return;
      await copyWithFeedback(copyFolder, this.results.folder, "Đã sao chép đường dẫn thư mục.");
    });
    for (const id of ["open-site-folder", "open-folder-files"]) {
      byId(id).addEventListener("click", () => {
        if (this.results) this.app.openFolder(this.results.site);
      });
    }
    byId("goto-prompt-option").addEventListener("click", () => this.app.openAdvanced("opt-prompt"));
  }

  // ─── Lightbox (full-size screenshot) ───────────────────────────────────────

  bindLightbox() {
    const dialog = byId("lightbox");
    byId("lightbox-close").addEventListener("click", () => dialog.close());
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
    dialog.addEventListener("close", () => {
      byId("lightbox-img").removeAttribute("src");
      if (this.lightboxReturn && this.lightboxReturn.isConnected) this.lightboxReturn.focus();
    });
  }

  openLightbox(url, title) {
    const dialog = byId("lightbox");
    this.lightboxReturn = document.activeElement;
    byId("lightbox-title").textContent = title;
    const image = byId("lightbox-img");
    image.setAttribute("src", url);
    image.setAttribute("alt", title);
    byId("lightbox-open").setAttribute("href", url);
    if (typeof dialog.showModal === "function") {
      dialog.showModal();
      byId("lightbox-close").focus();
    } else {
      window.open(url, "_blank", "noopener");
    }
  }
}

function emptyState(title, text) {
  return h("div", { class: "empty" }, h("strong", { text: title }), h("p", { text }));
}
