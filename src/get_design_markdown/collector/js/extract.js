// In-page extraction script.
//
// page.evaluate(script, {mode: "extract", maxElements})
//   -> {title, meta_description, lang, document_height, root_font_size_px, css_variables,
//       elements_total, elements, timing_ms}
//   Element objects use the exact snake_case field names of models.raw.ElementSample /
//   ComputedStyle (four-sided values as [top, right, bottom, left]; border-radius as
//   [top-left, top-right, bottom-right, bottom-left]) so collector/dom.py:parse_extraction()
//   can validate them directly. Every sampled element gets data-gdm-index="<index>".
//
// page.evaluate(script, {mode: "style", index})
//   -> {styles, transition_ms} of the element [data-gdm-index=index] (hover capture), or null.
(args) => {
  "use strict";
  const options = args || {};
  const mode = options.mode || "extract";
  const INDEX_ATTR = "data-gdm-index";
  const OVERLAY_ATTR = "data-gdm-overlay";

  const shortenImage = (value) => {
    if (!value || value === "none") return value || "none";
    let text = value.replace(/url\((["']?)data:[^)]{80,}?\1\)/g, 'url("data:…")');
    if (text.length > 2000) text = text.slice(0, 2000);
    return text;
  };

  const readStyles = (cs) => ({
    color: cs.color,
    background_color: cs.backgroundColor,
    background_image: shortenImage(cs.backgroundImage),
    font_family: cs.fontFamily,
    font_size: cs.fontSize,
    font_weight: cs.fontWeight,
    line_height: cs.lineHeight,
    letter_spacing: cs.letterSpacing,
    text_transform: cs.textTransform,
    text_decoration_line: cs.textDecorationLine || "none",
    text_align: cs.textAlign,
    padding: [cs.paddingTop, cs.paddingRight, cs.paddingBottom, cs.paddingLeft],
    margin: [cs.marginTop, cs.marginRight, cs.marginBottom, cs.marginLeft],
    row_gap: cs.rowGap,
    column_gap: cs.columnGap,
    border_radius: [
      cs.borderTopLeftRadius,
      cs.borderTopRightRadius,
      cs.borderBottomRightRadius,
      cs.borderBottomLeftRadius,
    ],
    border_width: [cs.borderTopWidth, cs.borderRightWidth, cs.borderBottomWidth, cs.borderLeftWidth],
    border_style: [cs.borderTopStyle, cs.borderRightStyle, cs.borderBottomStyle, cs.borderLeftStyle],
    border_color: [cs.borderTopColor, cs.borderRightColor, cs.borderBottomColor, cs.borderLeftColor],
    box_shadow: cs.boxShadow,
    backdrop_filter: cs.backdropFilter || cs.webkitBackdropFilter || "none",
    opacity: cs.opacity,
    display: cs.display,
    position: cs.position,
    z_index: cs.zIndex,
    max_width: cs.maxWidth,
    cursor: cs.cursor,
    overflow_x: cs.overflowX,
  });

  const timeListMs = (value) =>
    (value || "0s").split(",").map((part) => {
      const text = part.trim();
      const number = parseFloat(text);
      if (!Number.isFinite(number)) return 0;
      return text.endsWith("ms") ? number : number * 1000;
    });

  const transitionMs = (cs) => {
    const durations = timeListMs(cs.transitionDuration);
    const delays = timeListMs(cs.transitionDelay);
    let longest = 0;
    for (let i = 0; i < durations.length; i += 1) {
      longest = Math.max(longest, durations[i] + (delays[i % delays.length] || 0));
    }
    return longest;
  };

  if (mode === "style") {
    const el = document.querySelector(`[${INDEX_ATTR}="${options.index}"]`);
    if (!el) return null;
    const cs = getComputedStyle(el);
    return { styles: readStyles(cs), transition_ms: transitionMs(cs) };
  }

  const started = performance.now();
  const docEl = document.documentElement;
  const body = document.body || docEl;
  const scrollX = window.scrollX;
  const scrollY = window.scrollY;
  const docWidth = Math.max(docEl.scrollWidth, body.scrollWidth || 0, window.innerWidth);
  const maxElements = Math.max(2, options.maxElements || 3000);

  const SKIP_TAGS = new Set([
    "script", "style", "noscript", "template", "link", "meta", "br", "wbr", "head", "title",
    "base",
  ]);
  const LEAF_TAGS = new Set(["svg", "math", "iframe", "object", "embed", "canvas", "video", "audio", "select"]);
  const LANDMARK_TAGS = { header: "header", nav: "nav", main: "main", footer: "footer", aside: "aside", form: "form", dialog: "dialog" };
  const LANDMARK_ROLES = {
    banner: "header", navigation: "nav", main: "main", contentinfo: "footer",
    complementary: "aside", search: "form", form: "form", dialog: "dialog", alertdialog: "dialog",
  };
  const SECTIONING = new Set(["article", "aside", "main", "nav", "section"]);
  const INTERACTIVE_TAGS = new Set(["a", "button", "input", "select", "textarea", "summary", "label"]);
  const INTERACTIVE_ROLES = new Set(["button", "link", "tab", "menuitem", "switch", "checkbox", "radio", "option", "combobox", "searchbox", "textbox"]);
  const HEADING_TAGS = new Set(["h1", "h2", "h3", "h4", "h5", "h6"]);

  const roleOf = (el) => {
    const role = el.getAttribute("role");
    if (!role) return null;
    const first = role.trim().split(/\s+/)[0].toLowerCase();
    return first || null;
  };

  const hasOwnText = (el) => {
    for (const child of el.childNodes) {
      if (child.nodeType === 3 && child.data.trim()) return true;
    }
    return false;
  };

  const transparent = (color) =>
    !color || color === "transparent" || /^rgba\(.*,\s*0\)$/.test(color) || /\/\s*0\)$/.test(color);

  const decorated = (cs) =>
    !transparent(cs.backgroundColor) ||
    cs.backgroundImage !== "none" ||
    cs.boxShadow !== "none" ||
    cs.borderTopLeftRadius !== "0px" ||
    ((cs.borderTopStyle !== "none" && cs.borderTopWidth !== "0px") ||
      (cs.borderBottomStyle !== "none" && cs.borderBottomWidth !== "0px") ||
      (cs.borderLeftStyle !== "none" && cs.borderLeftWidth !== "0px") ||
      (cs.borderRightStyle !== "none" && cs.borderRightWidth !== "0px"));

  // ── Pass 1: cheap visibility walk (document order, open shadow roots included) ──
  const nodes = [];
  const htmlStyle = getComputedStyle(docEl);
  const bodyStyle = getComputedStyle(body);
  const pushNode = (el, tag, cs, rect, parentV, selfLandmark, landmark, overlay) => {
    nodes.push({ el, tag, cs, rect, parentV, landmark, overlay, selfLandmark, ownText: hasOwnText(el) });
    return nodes.length - 1;
  };
  const htmlIndex = pushNode(docEl, "html", htmlStyle, docEl.getBoundingClientRect(), -1, false, null, false);
  let bodyIndex = htmlIndex;
  if (body !== docEl) {
    bodyIndex = pushNode(body, "body", bodyStyle, body.getBoundingClientRect(), htmlIndex, false, null, false);
  }
  const rootOpacity = (parseFloat(htmlStyle.opacity) || 0) * (parseFloat(bodyStyle.opacity) || 0);

  const childrenOf = (el) => {
    const list = [];
    if (el.shadowRoot) for (const child of el.shadowRoot.children) list.push(child);
    for (const child of el.children) list.push(child);
    return list;
  };

  const stack = [];
  const pushChildren = (el, ctx) => {
    const children = childrenOf(el);
    for (let i = children.length - 1; i >= 0; i -= 1) stack.push([children[i], ctx]);
  };
  // Clip rectangles in document coordinates ({l, t, r, b}; null = unclipped). `clip` applies to
  // in-flow descendants, `clipAbs` to absolutely positioned ones (they escape the clipping of
  // static ancestors outside their containing block); position:fixed escapes both.
  const SMALL_SCROLLER = window.innerHeight * 0.5;
  const intersect = (clip, rect, axisX, axisY) => {
    const base = clip || { l: -Infinity, t: -Infinity, r: Infinity, b: Infinity };
    return {
      l: axisX ? Math.max(base.l, rect.left + scrollX) : base.l,
      r: axisX ? Math.min(base.r, rect.right + scrollX) : base.r,
      t: axisY ? Math.max(base.t, rect.top + scrollY) : base.t,
      b: axisY ? Math.min(base.b, rect.bottom + scrollY) : base.b,
    };
  };
  const clipsAxis = (overflow, size, small) =>
    overflow === "hidden" || overflow === "clip" || ((overflow === "auto" || overflow === "scroll") && size < small);
  const insideClip = (clip, rect) => {
    if (!clip) return true;
    const width = Math.min(clip.r, rect.right + scrollX) - Math.max(clip.l, rect.left + scrollX);
    const height = Math.min(clip.b, rect.bottom + scrollY) - Math.max(clip.t, rect.top + scrollY);
    return width > 0.5 && height > 0.5;
  };
  pushChildren(body, {
    parentV: bodyIndex, opacity: rootOpacity, landmark: null, sectioning: false, overlay: false,
    clip: null, clipAbs: null,
  });

  while (stack.length) {
    const [el, ctx] = stack.pop();
    const tag = el.localName;
    if (SKIP_TAGS.has(tag)) continue;
    const cs = getComputedStyle(el);
    if (cs.display === "none") continue;
    const opacity = ctx.opacity * (parseFloat(cs.opacity) || 0);
    const rect = el.getBoundingClientRect();
    const role = roleOf(el);
    const overlayRoot = el.hasAttribute(OVERLAY_ATTR);
    const overlay = ctx.overlay || overlayRoot;
    let own = LANDMARK_ROLES[role] || null;
    if (!own && LANDMARK_TAGS[tag]) {
      if ((tag === "header" || tag === "footer") && ctx.sectioning) own = null;
      else if (tag === "form" && rect.height > window.innerHeight * 2) own = null;
      else own = LANDMARK_TAGS[tag];
    }
    if (!own && overlayRoot) own = "dialog";
    const landmark = own || ctx.landmark;
    const position = cs.position;
    const clip = position === "fixed" ? null : position === "absolute" ? ctx.clipAbs : ctx.clip;
    const visible =
      rect.width > 0 &&
      rect.height > 0 &&
      !(rect.width <= 1 && rect.height <= 1) &&
      cs.visibility !== "hidden" &&
      cs.visibility !== "collapse" &&
      opacity > 0.01 &&
      rect.right + scrollX > 0 &&
      rect.left + scrollX < docWidth &&
      rect.bottom + scrollY > 0 &&
      insideClip(clip, rect);
    let parentV = ctx.parentV;
    if (visible) {
      parentV = pushNode(el, tag, cs, rect, ctx.parentV, !!own, landmark, overlay);
    }
    if (LEAF_TAGS.has(tag)) continue;
    const clipX = clipsAxis(cs.overflowX, 0, Infinity);
    const clipY = clipsAxis(cs.overflowY, rect.height, SMALL_SCROLLER);
    const childClip = clipX || clipY ? intersect(clip, rect, clipX, clipY) : clip;
    const containing = position !== "static" || cs.transform !== "none" || cs.filter !== "none";
    pushChildren(el, {
      parentV,
      opacity,
      landmark,
      sectioning: ctx.sectioning || SECTIONING.has(tag) || role === "main" || role === "navigation" || role === "complementary",
      overlay,
      clip: childClip,
      clipAbs: containing ? childClip : position === "fixed" ? null : ctx.clipAbs,
    });
  }
  const walkedMs = performance.now() - started;

  // ── Selection under max_elements (priority tiers, ancestor closure, document order) ──
  const total = nodes.length;
  const selected = new Uint8Array(total);
  if (total <= maxElements) {
    selected.fill(1);
  } else {
    const tierOf = (node, index) => {
      if (index === htmlIndex || index === bodyIndex || node.selfLandmark) return 0;
      const role = roleOf(node.el);
      if (INTERACTIVE_TAGS.has(node.tag) || INTERACTIVE_ROLES.has(role) || node.el.hasAttribute("tabindex")) return 1;
      if (HEADING_TAGS.has(node.tag) || role === "heading") return 2;
      if (node.ownText) return 3;
      if (decorated(node.cs)) return 4;
      return 5;
    };
    const order = nodes.map((node, index) => [tierOf(node, index), index]);
    order.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    let count = 0;
    for (const [, index] of order) {
      if (count >= maxElements) break;
      if (selected[index]) continue;
      const chain = [index];
      let parent = nodes[index].parentV;
      while (parent >= 0 && !selected[parent]) {
        chain.push(parent);
        parent = nodes[parent].parentV;
      }
      if (count + chain.length > maxElements) continue;
      for (const item of chain) selected[item] = 1;
      count += chain.length;
    }
  }

  // ── Pass 2: full records for the selected elements ──
  for (const old of document.querySelectorAll(`[${INDEX_ATTR}]`)) old.removeAttribute(INDEX_ATTR);
  // innerText-like text built from text nodes whose parent element is visible (clipped or
  // hidden duplicates such as animated button labels are left out).
  const visibleMap = new Map(nodes.map((node) => [node.el, node]));
  const SVG_NS = "http://www.w3.org/2000/svg";
  const SVG_NON_TEXT = new Set(["title", "desc", "style", "script"]);
  const isInline = (node) => node.cs.display.startsWith("inline") || node.cs.display === "contents";
  const visibleText = (el) => {
    let out = "";
    let previous = null;
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node && out.length < 400; node = walker.nextNode()) {
      const parent = node.parentElement;
      let info = visibleMap.get(parent);
      if (!info && parent && parent.namespaceURI === SVG_NS && !SVG_NON_TEXT.has(parent.localName)) {
        // SVG <text> (e.g. masked button labels): visible when its <svg> is.
        const svg = parent.closest("svg");
        info = svg ? visibleMap.get(svg) : undefined;
      }
      if (!info) continue;
      const data = node.data;
      if (!data.trim()) {
        if (out && !out.endsWith(" ")) out += " ";
        continue;
      }
      if (out && !out.endsWith(" ")) {
        const joined =
          previous && !/^\s/.test(data) && (info === previous || (isInline(info) && isInline(previous)));
        if (!joined) out += " ";
      }
      out += data;
      previous = info;
    }
    return out;
  };
  const round = (value) => Math.round(value * 100) / 100;
  const sampledIndex = new Int32Array(total).fill(-1);
  const elements = [];
  for (let v = 0; v < total; v += 1) {
    if (!selected[v]) continue;
    const node = nodes[v];
    const el = node.el;
    const tag = node.tag;
    const index = elements.length;
    sampledIndex[v] = index;
    let parent = node.parentV;
    while (parent >= 0 && sampledIndex[parent] < 0) parent = nodes[parent].parentV;
    const parentIndex = parent >= 0 ? sampledIndex[parent] : null;
    let text = "";
    let ownText = node.ownText;
    let inputType = null;
    if (tag === "input" || tag === "button" || tag === "select" || tag === "textarea") {
      inputType = (el.type || tag).toLowerCase();
    }
    if (tag === "input") {
      if (["submit", "button", "reset"].includes(inputType) && el.value) {
        text = el.value;
        ownText = true;
      }
    } else if (tag === "select") {
      const option = el.selectedOptions && el.selectedOptions[0];
      text = option ? option.text : "";
    } else if (tag !== "textarea") {
      text = visibleText(el) || "";
    }
    text = text.replace(/\s+/g, " ").trim().slice(0, 200);
    let href = null;
    if (tag === "a" || tag === "area") {
      const raw = typeof el.href === "string" ? el.href : el.getAttribute("href") || el.getAttribute("xlink:href");
      if (raw) {
        try {
          href = new URL(raw, document.baseURI).href.slice(0, 500);
        } catch (error) {
          href = String(raw).slice(0, 500);
        }
      }
    }
    let placeholderColor = null;
    if ((tag === "input" || tag === "textarea") && el.getAttribute("placeholder")) {
      placeholderColor = getComputedStyle(el, "::placeholder").color || null;
    }
    const rect = node.rect;
    const classes = el.classList ? Array.from(el.classList).slice(0, 24) : [];
    elements.push({
      index,
      tag,
      role: roleOf(el),
      classes,
      id: el.id ? String(el.id).slice(0, 120) : null,
      text,
      has_own_text: ownText,
      href,
      input_type: inputType,
      placeholder_color: placeholderColor,
      box: {
        x: round(rect.left + scrollX),
        y: round(rect.top + scrollY),
        width: round(rect.width),
        height: round(rect.height),
      },
      depth: parentIndex === null ? 0 : elements[parentIndex].depth + 1,
      parent_index: parentIndex,
      child_count: 0,
      landmark: node.landmark,
      in_overlay: node.overlay,
      styles: readStyles(node.cs),
    });
    if (parentIndex !== null) elements[parentIndex].child_count += 1;
    el.setAttribute(INDEX_ATTR, String(index));
  }
  const extractedMs = performance.now() - started;

  // ── CSS custom properties of the root element ──
  const variableNames = new Set();
  const rootSelector = /^(?::root|html)(?![\w-])[^\s>+~]*$/i;
  const collectRules = (rules, depth) => {
    if (!rules || depth > 6) return;
    for (const rule of rules) {
      if (rule.style && rule.selectorText) {
        const targetsRoot = rule.selectorText.split(",").some((part) => rootSelector.test(part.trim()));
        if (targetsRoot) {
          for (let i = 0; i < rule.style.length; i += 1) {
            const name = rule.style[i];
            if (name.startsWith("--")) variableNames.add(name);
          }
        }
      }
      if (rule.cssRules) collectRules(rule.cssRules, depth + 1);
    }
  };
  const sheets = [...document.styleSheets, ...(document.adoptedStyleSheets || [])];
  for (const sheet of sheets) {
    let rules = null;
    try {
      rules = sheet.cssRules;
    } catch (error) {
      rules = null; // cross-origin: unreadable, covered by the computed-style enumeration
    }
    collectRules(rules, 0);
  }
  for (let i = 0; i < htmlStyle.length; i += 1) {
    const name = htmlStyle[i];
    if (name.startsWith("--")) variableNames.add(name);
  }
  const names = [...variableNames].slice(0, 2000);
  const declaredOf = (name) => htmlStyle.getPropertyValue(name).trim();
  const probeHost = document.createElement("div");
  probeHost.setAttribute("aria-hidden", "true");
  probeHost.style.cssText =
    "position:absolute;left:-10000px;top:0;width:1000px;visibility:hidden;pointer-events:none;contain:strict;";
  const probes = [];
  const supports = (property, value) => {
    try {
      return CSS.supports(property, value);
    } catch (error) {
      return false;
    }
  };
  const NUMBER_RE = /^-?(\d+\.?\d*|\.\d+)(e-?\d+)?$/i;
  // Channel triples used as hsl(var(--x)) / rgb(var(--x)) (shadcn/ui, Tailwind themes).
  const HSL_TRIPLE = /^-?[\d.]+(deg|turn|rad|grad)?\s+[\d.]+%\s+[\d.]+%(\s*\/\s*[\d.]+%?)?$/i;
  const RGB_TRIPLE = /^[\d.]+\s+[\d.]+\s+[\d.]+(\s*\/\s*[\d.]+%?)?$/;
  const COLOR_HINT = /^(#|rgb|hsl|hwb|lab|lch|oklab|oklch|color\(|color-mix\(|transparent$|currentcolor$|[a-z]+$)/i;
  const PROPERTIES = [
    ["color", "color"],
    ["width", "width"],
    ["letterSpacing", "letter-spacing"],
    ["boxShadow", "box-shadow"],
    ["backgroundImage", "background-image"],
    ["transitionDuration", "transition-duration"],
    ["transitionTimingFunction", "transition-timing-function"],
    ["fontFamily", "font-family"],
  ];
  const variables = names.map((name) => {
    const declared = declaredOf(name);
    const record = { name, declared: declared || null, resolved: null, source: "root" };
    if (!declared) return record;
    if (NUMBER_RE.test(declared)) {
      record.resolved = declared;
      return record;
    }
    const triple = HSL_TRIPLE.test(declared) ? "hsl" : RGB_TRIPLE.test(declared) ? "rgb" : null;
    if (triple) {
      const probe = document.createElement("div");
      probe.style.setProperty("color", `${triple}(var(${name}))`);
      probeHost.appendChild(probe);
      probes.push([record, probe, "color"]);
      return record;
    }
    for (const [jsName, cssName] of PROPERTIES) {
      if (cssName === "color" && !COLOR_HINT.test(declared)) continue;
      if ((cssName === "width" || cssName === "letter-spacing") && (/%/.test(declared) || !/\d/.test(declared))) continue;
      if (cssName === "font-family" && !/[a-z]/i.test(declared)) continue;
      if (!supports(cssName, declared)) continue;
      if (cssName === "font-family" && /^[a-z-]+$/i.test(declared) && supports("color", declared)) continue;
      const probe = document.createElement("div");
      probe.style.setProperty(cssName, `var(${name})`);
      probeHost.appendChild(probe);
      probes.push([record, probe, jsName]);
      break;
    }
    return record;
  });
  if (probes.length) {
    (document.body || docEl).appendChild(probeHost);
    for (const [record, probe, jsName] of probes) {
      const value = getComputedStyle(probe)[jsName];
      record.resolved = value ? String(value).slice(0, 1000) : null;
    }
    probeHost.remove();
  }
  for (const record of variables) {
    if (record.declared && record.declared.length > 1000) record.declared = record.declared.slice(0, 1000);
  }

  const metaDescription =
    document.querySelector('meta[name="description" i]') ||
    document.querySelector('meta[property="og:description" i]');
  return {
    title: (document.title || "").trim(),
    meta_description: metaDescription ? (metaDescription.getAttribute("content") || "").trim() || null : null,
    lang: (docEl.getAttribute("lang") || "").trim() || null,
    document_height: Math.max(docEl.scrollHeight, body.scrollHeight || 0),
    root_font_size_px: parseFloat(htmlStyle.fontSize) || 16,
    css_variables: variables,
    elements_total: total,
    elements,
    timing_ms: {
      walk: Math.round(walkedMs),
      elements: Math.round(extractedMs - walkedMs),
      variables: Math.round(performance.now() - started - extractedMs),
    },
  };
}
