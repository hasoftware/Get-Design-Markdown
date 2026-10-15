// Page probe for blocker detection (stream: collector).
// page.evaluate(script, {markers}) -> {title, text, markers, password_inputs, text_elements}
// Used by collector/browser.py:probe_page() right after navigation.
(args) => {
  const markers = (args && args.markers) || [];
  const body = document.body;
  const rawText = body ? (body.innerText || body.textContent || "") : "";
  const text = rawText.replace(/\s+/g, " ").trim();
  const html = document.documentElement ? document.documentElement.outerHTML.toLowerCase() : "";
  const found = markers.filter((marker) => html.includes(marker));

  const rendered = (el) => {
    const rect = el.getBoundingClientRect();
    if (rect.width <= 1 || rect.height <= 1) return false;
    const style = getComputedStyle(el);
    return style.visibility !== "hidden" && style.display !== "none" && parseFloat(style.opacity) > 0.01;
  };

  let passwordInputs = 0;
  for (const input of document.querySelectorAll("input[type=password]")) {
    if (rendered(input)) passwordInputs += 1;
  }

  // Count visible elements that render their own text; stop early, only "< 40" matters.
  let textElements = 0;
  if (body) {
    const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
    const seen = new Set();
    let node = walker.nextNode();
    while (node && textElements < 200) {
      const parent = node.parentElement;
      if (parent && !seen.has(parent) && node.data.trim()) {
        seen.add(parent);
        const tag = parent.localName;
        if (tag !== "script" && tag !== "style" && tag !== "noscript" && tag !== "template" && rendered(parent)) {
          textElements += 1;
        }
      }
      node = walker.nextNode();
    }
  }

  return {
    title: document.title || "",
    text: text.slice(0, 5000),
    markers: found,
    password_inputs: passwordInputs,
    text_elements: textElements,
  };
}
