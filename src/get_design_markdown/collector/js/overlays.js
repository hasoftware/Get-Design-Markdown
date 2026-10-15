// Overlay detection (stream: collector).
// page.evaluate(script, {mode: "detect"}) marks cookie/consent banners, modals and their
// backdrops with data-gdm-overlay="<n>" and returns [{kind, selector, text}].
// page.evaluate(script, {mode: "hide"}) hides every marked overlay (visibility:hidden,
// no pointer events) so hover, layout and screenshots see the page without them.
(args) => {
  const mode = (args && args.mode) || "detect";
  const ATTR = "data-gdm-overlay";
  const STYLE_ID = "gdm-hide-overlays";

  if (mode === "hide") {
    if (!document.querySelector(`[${ATTR}]`)) return 0;
    let style = document.getElementById(STYLE_ID);
    if (!style) {
      style = document.createElement("style");
      style.id = STYLE_ID;
      (document.head || document.documentElement).appendChild(style);
    }
    let css =
      `[${ATTR}], [${ATTR}] * { visibility: hidden !important; pointer-events: none !important; ` +
      "animation: none !important; transition: none !important; }";
    if (args && args.unlockScroll) {
      const rootOverflow = getComputedStyle(document.documentElement).overflowY;
      const bodyOverflow = document.body ? getComputedStyle(document.body).overflowY : "visible";
      if (rootOverflow === "hidden" || bodyOverflow === "hidden") {
        css += " html, body { overflow-y: auto !important; }";
      }
    }
    style.textContent = css;
    return document.querySelectorAll(`[${ATTR}]`).length;
  }

  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const TEXT_RE = /cookie|consent|privacy|gdpr|accept/i;
  const STRONG_TEXT_RE = /cookie|consent|gdpr/i;
  const KNOWN = [
    "#onetrust-consent-sdk",
    "#onetrust-banner-sdk",
    "#CybotCookiebotDialog",
    "#usercentrics-root",
    "#didomi-host",
    "#truste-consent-track",
    "#qc-cmp2-container",
    "#cookiescript_injected",
    "#hs-eu-cookie-confirmation",
    ".osano-cm-window",
    ".cc-window",
    ".fc-consent-root",
  ];
  const GENERIC =
    '[id*="cookie" i], [class*="cookie" i], [id*="consent" i], [class*="consent" i], ' +
    '[aria-modal="true"], [role="dialog"], [role="alertdialog"], dialog[open]';
  const LANDMARK_TAGS = new Set(["html", "body", "header", "nav", "main", "footer", "aside"]);
  const LANDMARK_ROLES = new Set(["banner", "navigation", "main", "contentinfo", "complementary"]);

  const isLandmark = (el) =>
    LANDMARK_TAGS.has(el.localName) || LANDMARK_ROLES.has((el.getAttribute("role") || "").trim());

  const rendered = (el) => {
    const style = getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden" || parseFloat(style.opacity) <= 0.01) {
      return false;
    }
    const rect = el.getBoundingClientRect();
    return rect.width > 1 && rect.height > 1;
  };

  // The element itself or one of its descendants is rendered (fixed children of a static
  // wrapper such as #onetrust-consent-sdk give the wrapper a zero-size box).
  const renderedDeep = (el) => {
    if (rendered(el)) return true;
    const descendants = el.querySelectorAll("*");
    const limit = Math.min(descendants.length, 400);
    for (let i = 0; i < limit; i += 1) {
      if (rendered(descendants[i])) return true;
    }
    return false;
  };

  const zIndex = (style) => {
    const value = parseInt(style.zIndex, 10);
    return Number.isFinite(value) ? value : 0;
  };

  const containsNavigation = (el) =>
    !!el.querySelector('nav, [role="navigation"], header, [role="banner"], main, [role="main"]');

  // Nearest ancestor-or-self (up to 6 levels, never body/html) with position fixed.
  const fixedContainer = (el) => {
    let node = el;
    for (let depth = 0; node && depth < 6; depth += 1) {
      if (node === document.body || node === document.documentElement) return null;
      if (getComputedStyle(node).position === "fixed") return node;
      node = node.parentElement;
    }
    return null;
  };

  const textOf = (el) => ((el.innerText || el.textContent || "").replace(/\s+/g, " ").trim());

  const roots = new Map(); // element -> kind

  const add = (el, kind) => {
    if (!el || el === document.body || el === document.documentElement) return;
    if (!roots.has(el)) roots.set(el, kind);
  };

  // 1. Known consent-manager containers.
  for (const selector of KNOWN) {
    let matches = [];
    try {
      matches = document.querySelectorAll(selector);
    } catch (error) {
      matches = [];
    }
    for (const el of matches) {
      if (!isLandmark(el) && renderedDeep(el)) add(el, "cookie");
    }
  }

  // 2. Generic cookie/consent containers and dialogs that float above the page.
  for (const el of document.querySelectorAll(GENERIC)) {
    if (isLandmark(el) || !renderedDeep(el)) continue;
    const isDialog =
      el.localName === "dialog" ||
      el.getAttribute("aria-modal") === "true" ||
      /dialog/.test(el.getAttribute("role") || "");
    const container = fixedContainer(el);
    if (!container || isLandmark(container) || containsNavigation(container)) continue;
    const text = textOf(container);
    if (text.length > 3000) continue;
    if (isDialog) {
      add(container, STRONG_TEXT_RE.test(text) ? "cookie" : "dialog");
    } else if (TEXT_RE.test(text) || STRONG_TEXT_RE.test(`${el.id} ${el.className}`)) {
      add(container, "cookie");
    }
  }

  // 3. Any fixed, high z-index box at the bottom or center with consent wording.
  const fixed = [];
  for (const el of document.body ? document.body.querySelectorAll("*") : []) {
    const style = getComputedStyle(el);
    if (style.position !== "fixed" && style.position !== "sticky") continue;
    fixed.push([el, style]);
  }
  for (const [el, style] of fixed) {
    if (isLandmark(el) || containsNavigation(el) || !rendered(el)) continue;
    if (zIndex(style) < 1000 && style.position !== "fixed") continue;
    const rect = el.getBoundingClientRect();
    const text = textOf(el);
    if (!text || text.length > 2000) continue;
    const atBottom = rect.bottom >= vh - 48 && rect.top > vh * 0.3;
    const centered =
      Math.abs(rect.top + rect.height / 2 - vh / 2) < vh * 0.25 &&
      Math.abs(rect.left + rect.width / 2 - vw / 2) < vw * 0.25;
    const high = zIndex(style) >= 1000;
    const strong = STRONG_TEXT_RE.test(text);
    // "privacy" / "accept" alone are common words: only short boxes with a button count.
    const weak =
      TEXT_RE.test(text) &&
      text.length < 800 &&
      !!el.querySelector('button, [role="button"], input[type="button"], input[type="submit"]');
    if (high && (atBottom || centered) && (strong || weak)) add(el, "cookie");
    else if ((atBottom || high) && strong) add(el, "cookie");
  }

  // 4. Backdrops of the overlays found above: fixed, (almost) full-viewport, no text.
  if (roots.size) {
    for (const [el, style] of fixed) {
      if (roots.has(el) || style.position !== "fixed" || !rendered(el)) continue;
      const rect = el.getBoundingClientRect();
      if (rect.width < vw * 0.9 || rect.height < vh * 0.9) continue;
      if (textOf(el) || el.querySelector("img, video, canvas, svg, iframe")) continue;
      if (zIndex(style) < 100) continue;
      add(el, "backdrop");
    }
  }

  // Keep only outermost roots.
  const all = [...roots.keys()];
  const outer = all.filter((el) => !all.some((other) => other !== el && other.contains(el)));
  const describe = (el) => {
    let selector = el.localName;
    if (el.id) selector += `#${el.id}`;
    const classes = typeof el.className === "string" ? el.className.trim().split(/\s+/) : [];
    for (const name of classes.slice(0, 2)) if (name) selector += `.${name}`;
    return selector;
  };
  return outer.map((el, index) => {
    el.setAttribute(ATTR, String(index + 1));
    return { kind: roots.get(el), selector: describe(el), text: textOf(el).slice(0, 80) };
  });
}
