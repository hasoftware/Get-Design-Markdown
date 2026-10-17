// Rendered Markdown (DESIGN.md, prompt.md) is shown in <iframe sandbox srcdoc>: no scripts,
// no same-origin access, so nothing in the document can reach the page or its token.
//
// Before it goes into srcdoc, the server's HTML fragment is parsed into an inert document
// (DOMParser: no script runs, nothing loads) and tidied: links to other sites become plain
// text (a sandboxed frame cannot open them), remote images are dropped (the page's CSP would
// block them), and every hex colour gets a small swatch, which is what a designer looks for.

import { cssVar } from "./theme.js";
import { isVietnamese } from "./dom.js";

const HEX_RE = /#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6})(?![0-9a-zA-Z_-])/g;
const DROP = "script, style, iframe, frame, object, embed, link, meta, base, form, svg, math";

function tidy(doc) {
  for (const element of doc.querySelectorAll(DROP)) element.remove();
  for (const element of doc.body.querySelectorAll("*")) {
    for (const attribute of Array.from(element.attributes)) {
      const name = attribute.name.toLowerCase();
      if (name.startsWith("on") || name === "style" || name === "srcset") {
        element.removeAttribute(attribute.name);
      }
    }
  }
  for (const link of doc.querySelectorAll("a")) {
    const href = link.getAttribute("href") || "";
    if (href.startsWith("#")) continue;
    const span = doc.createElement("span");
    span.className = "link";
    if (/^https?:\/\//i.test(href)) span.setAttribute("title", href);
    span.append(...Array.from(link.childNodes));
    link.replaceWith(span);
  }
  for (const image of doc.querySelectorAll("img")) {
    const src = image.getAttribute("src") || "";
    if (!src.startsWith("data:image/")) {
      image.replaceWith(doc.createTextNode(image.getAttribute("alt") || ""));
    }
  }
}

function addSwatches(doc) {
  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
  const targets = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    HEX_RE.lastIndex = 0;
    if (HEX_RE.test(node.nodeValue)) targets.push(node);
  }
  for (const node of targets) {
    const text = node.nodeValue;
    const fragment = doc.createDocumentFragment();
    let last = 0;
    HEX_RE.lastIndex = 0;
    for (let match = HEX_RE.exec(text); match; match = HEX_RE.exec(text)) {
      if (match.index > last) fragment.append(text.slice(last, match.index));
      const swatch = doc.createElement("span");
      swatch.className = "sw";
      swatch.setAttribute("aria-hidden", "true");
      swatch.style.backgroundColor = match[0];
      fragment.append(swatch, match[0]);
      last = match.index + match[0].length;
    }
    if (last < text.length) fragment.append(text.slice(last));
    node.replaceWith(fragment);
  }
}

function escapeText(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function frameCss() {
  const v = (name, fallback) => cssVar(name) || fallback;
  const scheme = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme
    : window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  return `
:root { color-scheme: ${scheme}; }
html { background: ${v("--surface", "#ffffff")}; }
body { margin: 0; color: ${v("--ink", "#000000")}; font: 15px/1.62 ${v("--font", "system-ui, sans-serif")};
  -webkit-font-smoothing: antialiased; font-kerning: normal; overflow-wrap: break-word; }
main { max-width: 78ch; margin: 0 auto; padding: 32px 32px 64px; }
h1, h2, h3, h4 { font-family: ${v("--font-display", "system-ui, sans-serif")}; font-weight: 540; line-height: 1.25; }
h1 { font-size: 30px; font-weight: 340; line-height: 1.12; margin: 0 0 20px; letter-spacing: -0.022em; }
h2 { font-size: 21px; margin: 40px 0 12px; padding-bottom: 8px; letter-spacing: -0.01em;
  border-bottom: 1px solid ${v("--line", "#e6e6e6")}; }
h3 { font-size: 16.5px; margin: 26px 0 8px; }
h4 { font-size: 15px; margin: 20px 0 6px; }
p, ul, ol, table, blockquote, pre { margin: 0 0 14px; }
ul, ol { padding-left: 1.4em; }
li + li { margin-top: 3px; }
strong { font-weight: 600; }
code { font-family: ${v("--font-mono", "monospace")}; font-size: 0.86em; background: ${v("--sunken", "#f1f1f1")};
  border-radius: 4px; padding: 0.08em 0.35em; }
pre { background: ${v("--surface-2", "#f7f7f5")}; border-radius: 12px; padding: 14px 16px; overflow: auto;
  font-size: 12.5px; line-height: 1.55; }
pre code { background: none; padding: 0; font-size: inherit; }
table { border-collapse: collapse; display: block; overflow-x: auto; max-width: 100%; font-size: 13.5px; }
th, td { border-bottom: 1px solid ${v("--line", "#e6e6e6")}; padding: 8px 12px; text-align: left; vertical-align: top; }
th { background: ${v("--surface-2", "#f7f7f5")}; font-weight: 600; }
blockquote { padding: 16px 20px; margin-left: 0; margin-right: 0; border-radius: 16px;
  background: ${v("--block-cream", "#f4ecd6")}; color: #000000; }
blockquote p:last-child { margin-bottom: 0; }
blockquote .link { color: #000000; }
blockquote code { background: rgba(0, 0, 0, 0.08); color: #000000; }
hr { border: 0; border-top: 1px solid ${v("--line", "#e6e6e6")}; margin: 28px 0; }
.link { color: ${v("--accent-ink", "#000000")}; text-decoration: underline; text-decoration-thickness: 1px;
  text-underline-offset: 3px; }
.sw { display: inline-block; width: 0.9em; height: 0.9em; margin-right: 0.3em; vertical-align: -0.1em;
  border-radius: 4px; box-shadow: inset 0 0 0 1px rgba(127, 127, 127, 0.5); }
`;
}

/** srcdoc for a rendered Markdown fragment (from the server) in the current theme. */
export function markdownSrcdoc(fragment, { title }) {
  const doc = new DOMParser().parseFromString(String(fragment || ""), "text/html");
  tidy(doc);
  addSwatches(doc);
  const body = doc.body.innerHTML;
  const lang = isVietnamese(doc.body.textContent || "") ? "vi" : "en";
  return (
    `<!DOCTYPE html><html lang="${lang}"><head><meta charset="utf-8">` +
    `<title>${escapeText(title)}</title><style>${frameCss()}</style></head>` +
    `<body><main>${body}</main></body></html>`
  );
}

/** An <iframe sandbox=""> showing rendered Markdown; returns the element. */
export function markdownFrame(fragment, { title }) {
  const frame = document.createElement("iframe");
  frame.setAttribute("sandbox", "");
  frame.setAttribute("title", title);
  frame.setAttribute("referrerpolicy", "no-referrer");
  frame.srcdoc = markdownSrcdoc(fragment, { title });
  frame.dataset.fragment = "1";
  return frame;
}

/** Re-render a frame made by markdownFrame() after a theme change. */
export function refreshMarkdownFrame(frame, fragment) {
  frame.srcdoc = markdownSrcdoc(fragment, { title: frame.getAttribute("title") || "" });
}

/** Lines of a YAML front matter as DOM nodes, with a swatch before each hex colour. */
export function frontMatterNodes(text) {
  const nodes = [];
  for (const line of String(text).replace(/\n$/, "").split("\n")) {
    const row = document.createElement("span");
    row.className = "code-line";
    let last = 0;
    HEX_RE.lastIndex = 0;
    for (let match = HEX_RE.exec(line); match; match = HEX_RE.exec(line)) {
      if (match.index > last) row.append(line.slice(last, match.index));
      const swatch = document.createElement("span");
      swatch.className = "swatch";
      swatch.setAttribute("aria-hidden", "true");
      swatch.style.backgroundColor = match[0];
      row.append(swatch, match[0]);
      last = match.index + match[0].length;
    }
    if (last < line.length) row.append(line.slice(last));
    nodes.push(row);
  }
  return nodes;
}
