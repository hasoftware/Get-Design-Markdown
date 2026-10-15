// Layout snapshot (stream: collector).
// page.evaluate(script) at scroll position 0 -> fields of models.raw.LayoutSnapshot
// (viewport_width is filled in by Python). Elements hidden by display/visibility, opacity
// (own or inherited) or ancestor overflow clipping do not count.
() => {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const docEl = document.documentElement;
  const body = document.body || docEl;
  const scrollY = window.scrollY;

  const opacityCache = new Map();
  const effectiveOpacity = (el) => {
    if (!el || el.nodeType !== 1) return 1;
    if (opacityCache.has(el)) return opacityCache.get(el);
    const own = parseFloat(getComputedStyle(el).opacity);
    const value = (Number.isFinite(own) ? own : 1) * effectiveOpacity(el.parentElement);
    opacityCache.set(el, value);
    return value;
  };

  // True when an ancestor with overflow clipping hides the element completely (menus inside a
  // 0-height overflow:hidden panel, off-screen carousel slides...). Absolutely positioned
  // elements escape static ancestors up to their containing block; fixed ones escape all.
  const SMALL_SCROLLER = vh * 0.5;
  const clippedAway = (el, rect) => {
    const own = getComputedStyle(el).position;
    if (own === "fixed") return false;
    let absolute = own === "absolute";
    for (let node = el.parentElement; node && node !== body && node !== docEl; node = node.parentElement) {
      const style = getComputedStyle(node);
      const containing = style.position !== "static" || style.transform !== "none" || style.filter !== "none";
      if (!absolute || containing) {
        const box = node.getBoundingClientRect();
        const ox = style.overflowX;
        const oy = style.overflowY;
        const clipX = ox !== "visible";
        const clipY = oy === "hidden" || oy === "clip" || (oy !== "visible" && box.height < SMALL_SCROLLER);
        if (clipX && (rect.right <= box.left + 0.5 || rect.left >= box.right - 0.5)) return true;
        if (clipY && (rect.bottom <= box.top + 0.5 || rect.top >= box.bottom - 0.5)) return true;
      }
      if (containing) {
        if (style.position === "fixed") return false;
        absolute = style.position === "absolute";
      }
    }
    return false;
  };

  const visibleCache = new Map();
  const visible = (el) => {
    if (visibleCache.has(el)) return visibleCache.get(el);
    const rect = el.getBoundingClientRect();
    let result = rect.width > 1 && rect.height > 1 && rect.right > 0 && rect.left < vw;
    if (result) {
      const style = getComputedStyle(el);
      result =
        style.display !== "none" &&
        style.visibility === "visible" &&
        effectiveOpacity(el) > 0.05 &&
        !clippedAway(el, rect);
    }
    visibleCache.set(el, result);
    return result;
  };

  const docTop = (el) => el.getBoundingClientRect().top + scrollY;
  const textOf = (el) => ((el.innerText || el.textContent || "").replace(/\s+/g, " ").trim());
  const px = (value) => {
    const number = parseFloat(value);
    return Number.isFinite(number) ? number : 0;
  };
  const inLandmark = (el, selector) => !!el.closest(selector);
  const HEADER_SEL = 'header, [role="banner"]';
  const FOOTER_SEL = 'footer, [role="contentinfo"]';
  const NAV_SEL = 'nav, [role="navigation"]';

  // ── Navigation ──
  const isHomeLink = (a) => {
    try {
      const url = new URL(a.href, document.baseURI);
      return url.origin === location.origin && (url.pathname === "/" || url.pathname === "") && !url.hash;
    } catch (error) {
      return false;
    }
  };
  const ownBackground = (el) => {
    const bg = getComputedStyle(el).backgroundColor;
    return !(bg === "transparent" || /^rgba\(.*,\s*0\)$/.test(bg));
  };
  const headerNavs = [...document.querySelectorAll(NAV_SEL)].filter(
    (nav) => visible(nav) && (inLandmark(nav, HEADER_SEL) || docTop(nav) < 200)
  );
  const navItems = new Set();
  for (const nav of headerNavs) {
    for (const item of nav.querySelectorAll('a[href], button, [role="button"], [role="menuitem"]')) {
      if (visible(item) && textOf(item) && docTop(item) < 200) navItems.add(item);
    }
  }
  const header = document.querySelector(HEADER_SEL);
  if (header && visible(header)) {
    for (const link of header.querySelectorAll("a[href]")) {
      if (navItems.has(link) || link.closest(NAV_SEL)) continue;
      if (!visible(link) || !textOf(link) || isHomeLink(link) || ownBackground(link)) continue;
      if (docTop(link) < 200 && headerNavs.length) navItems.add(link);
    }
  }
  const visibleNavLinks = navItems.size;

  // ── Hamburger ──
  const STRONG_MENU =
    /hamburger|burger|mobile[-_ ]?(menu|nav)|nav(igation)?[-_ ]?(toggle|trigger|button|open)|menu[-_ ]?(toggle|trigger|button|open|icon)|open (the )?(main )?(menu|navigation)|toggle (the )?(menu|navigation)/i;
  const WEAK_MENU = /\bmenu\b|navigation|\bnav\b|toggle/i;
  const NOT_MENU = /theme|dark|light|color|mode|search|lang|locale|region|cart|account|user|profile|close|notification|cookie|dismiss|logo|brand|home/i;
  let hamburger = false;
  for (const el of document.querySelectorAll('button, a, [role="button"], summary')) {
    if (!visible(el) || (el.localName === "a" && isHomeLink(el))) continue;
    const rect = el.getBoundingClientRect();
    if (rect.top + scrollY >= 120 || rect.width > 160 || rect.height > 120) continue;
    const text = textOf(el);
    const label = [el.getAttribute("aria-label") || "", el.getAttribute("title") || "", text].join(" ");
    const names = [
      typeof el.className === "string" ? el.className : "",
      el.id || "",
      el.getAttribute("data-testid") || "",
    ].join(" ");
    const iconOnly = !text || text.length <= 1;
    const expandable = el.hasAttribute("aria-expanded") || el.hasAttribute("aria-controls");
    if (STRONG_MENU.test(label) || STRONG_MENU.test(names)) {
      hamburger = true;
      break;
    }
    if (NOT_MENU.test(label) || NOT_MENU.test(names)) continue;
    // Weak words ("menu", "nav", "toggle"): in the accessible label of a short control, or in
    // the class/id of an icon-only control ("nav-link" on a text link is not a hamburger).
    if (WEAK_MENU.test(label) && (iconOnly || text.length <= 12)) {
      hamburger = true;
      break;
    }
    const buttonLike =
      el.localName !== "a" || expandable || (el.getAttribute("href") || "").startsWith("#");
    if (WEAK_MENU.test(names) && iconOnly && buttonLike) {
      hamburger = true;
      break;
    }
    if (iconOnly && expandable && inLandmark(el, HEADER_SEL)) {
      hamburger = true;
      break;
    }
  }

  // ── Sidebar: a narrow, tall column at the left that is pinned (sticky/fixed) or starts at
  // the top of the page. Product mock-ups (aria-hidden/inert, static in a hero) do not count.
  const pinned = (el) => {
    let node = el;
    for (let depth = 0; node && node !== body && depth < 4; depth += 1) {
      const position = getComputedStyle(node).position;
      if (position === "sticky" || position === "fixed") return true;
      node = node.parentElement;
    }
    return false;
  };
  let sidebar = false;
  for (const el of document.querySelectorAll('aside, [role="complementary"], nav, [role="navigation"]')) {
    if (!visible(el) || el.closest('[aria-hidden="true"], [inert]')) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width < 120 || rect.width >= 400 || rect.width > vw * 0.5 || rect.left > vw * 0.3) continue;
    const tall = rect.height > vh * 0.5 || el.scrollHeight > vh * 0.5;
    if (tall && (pinned(el) || rect.top + scrollY < 200)) {
      sidebar = true;
      break;
    }
  }

  // ── Content area ──
  const main = document.querySelector('main, [role="main"]');
  const scope = main && visible(main) ? main : body;
  const excluded = (el) =>
    scope === body && !!el.closest(`${HEADER_SEL}, ${FOOTER_SEL}, ${NAV_SEL}, aside`);

  // ── Columns: siblings side by side (same top +-4 px), each >= 15% of the viewport ──
  let columns = 1;
  const minWidth = vw * 0.15;
  const parents = [scope, ...scope.querySelectorAll("*")];
  for (const parent of parents) {
    if (parent.childElementCount < 2 || excluded(parent)) continue;
    const boxes = [];
    for (const child of parent.children) {
      const rect = child.getBoundingClientRect();
      if (rect.width < minWidth || rect.height < 48) continue;
      if (rect.left < -1 || rect.right > vw + 1) continue;
      // Content columns carry text; logo strips and image rows do not.
      if (!visible(child) || !textOf(child)) continue;
      boxes.push(rect);
    }
    if (boxes.length < 2) continue;
    const rows = [];
    for (const rect of boxes) {
      const row = rows.find((item) => Math.abs(item.top - rect.top) <= 4);
      if (row) row.boxes.push(rect);
      else rows.push({ top: rect.top, boxes: [rect] });
    }
    for (const row of rows) {
      const sorted = row.boxes.sort((a, b) => a.left - b.left);
      let count = 1;
      for (let i = 1; i < sorted.length; i += 1) {
        if (sorted[i].left >= sorted[i - 1].right - 2) count += 1;
      }
      columns = Math.max(columns, count);
    }
  }

  // ── Horizontal scrollers ──
  let scrollers = 0;
  for (const el of body.querySelectorAll("*")) {
    if (el.scrollWidth <= el.clientWidth + 4 || el.clientWidth < vw * 0.5) continue;
    const overflow = getComputedStyle(el).overflowX;
    if ((overflow === "auto" || overflow === "scroll") && visible(el)) scrollers += 1;
  }
  if (docEl.scrollWidth > docEl.clientWidth + 4) scrollers += 1;

  // ── Smallest tap target in the first three screens ──
  let minTap = null;
  const INTERACTIVE =
    'a[href], button, input:not([type="hidden"]), select, textarea, summary, [role="button"], [role="link"], [tabindex]:not([tabindex="-1"])';
  for (const el of document.querySelectorAll(INTERACTIVE)) {
    const rect = el.getBoundingClientRect();
    if (rect.top + scrollY >= vh * 3 || rect.width <= 1 || rect.height <= 1) continue;
    if (!visible(el)) continue;
    const size = Math.min(rect.width, rect.height);
    if (minTap === null || size < minTap) minTap = size;
  }

  // ── Container: dominant centered element constrained by a px max-width ──
  const containerGroups = new Map();
  for (const el of [scope, ...scope.querySelectorAll("*")]) {
    if (excluded(el) && !inLandmark(el, HEADER_SEL)) continue;
    const style = getComputedStyle(el);
    const maxWidth = style.maxWidth;
    if (!maxWidth.endsWith("px")) continue;
    const limit = px(maxWidth);
    if (limit < 480) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width < vw * 0.5 || rect.height < 1) continue;
    if (Math.abs(rect.left - (vw - rect.right)) > 2) continue;
    if (!visible(el)) continue;
    const group = containerGroups.get(limit) || { limit, area: 0, count: 0, widths: [], gutters: [] };
    group.area += rect.width * Math.min(rect.height, vh * 3);
    group.count += 1;
    group.widths.push(rect.width);
    const padding = px(style.paddingLeft);
    group.gutters.push(padding > 0 ? padding : rect.left < 64 ? rect.left : 0);
    containerGroups.set(limit, group);
  }
  if (header && visible(header)) {
    for (const el of header.querySelectorAll("*")) {
      const style = getComputedStyle(el);
      if (!style.maxWidth.endsWith("px")) continue;
      const limit = px(style.maxWidth);
      const group = containerGroups.get(limit);
      if (!group) continue;
      const rect = el.getBoundingClientRect();
      if (Math.abs(rect.left - (vw - rect.right)) > 2 || !visible(el)) continue;
      group.area += rect.width * rect.height;
    }
  }
  const median = (values) => {
    const sorted = [...values].sort((a, b) => a - b);
    return sorted.length ? sorted[Math.floor(sorted.length / 2)] : null;
  };
  let container = null;
  let gutter = null;
  const best = [...containerGroups.values()].sort((a, b) => b.area - a.area)[0];
  if (best) {
    container = median(best.widths);
    gutter = median(best.gutters);
  } else {
    // No max-width container: widest centered block narrower than the viewport.
    for (const el of scope.querySelectorAll("*")) {
      if (excluded(el)) continue;
      const rect = el.getBoundingClientRect();
      if (rect.width < vw * 0.5 || rect.width > vw - 16 || rect.height < 48) continue;
      if (Math.abs(rect.left - (vw - rect.right)) > 2 || !visible(el)) continue;
      if (container === null || rect.width > container) {
        container = rect.width;
        gutter = rect.left + px(getComputedStyle(el).paddingLeft);
      }
    }
    if (container === null) container = vw;
  }

  // ── Largest heading / display text in the first two screens ──
  let maxHeading = null;
  const headingCandidates = document.querySelectorAll('h1, h2, [role="heading"], p, span, div, a');
  for (const el of headingCandidates) {
    const isHeading = /^h[12]$/.test(el.localName) || el.getAttribute("role") === "heading";
    if (!isHeading) {
      let own = false;
      for (const child of el.childNodes) {
        if (child.nodeType === 3 && child.data.trim()) {
          own = true;
          break;
        }
      }
      if (!own) continue;
    }
    const rect = el.getBoundingClientRect();
    if (rect.top + scrollY >= vh * 2 || el.closest(FOOTER_SEL)) continue;
    const size = px(getComputedStyle(el).fontSize);
    if (!isHeading && size < 24) continue;
    if (!visible(el)) continue;
    if (maxHeading === null || size > maxHeading) maxHeading = size;
  }

  const round = (value) => (value === null ? null : Math.round(value * 100) / 100);
  return {
    viewport_height: vh,
    document_height: Math.max(docEl.scrollHeight, body.scrollHeight || 0),
    nav_visible: visibleNavLinks >= 3,
    visible_nav_links: visibleNavLinks,
    hamburger_visible: hamburger,
    sidebar_visible: sidebar,
    main_columns: columns,
    horizontal_scrollers: scrollers,
    min_tap_target_px: round(minTap),
    container_width_px: round(container),
    gutter_px: round(gutter),
    max_heading_px: round(maxHeading),
  };
}
