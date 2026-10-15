// Hover targeting (stream: collector).
// page.evaluate(script, {index}) scrolls [data-gdm-index=index] to the middle of the viewport
// and returns {found, ok, x, y}: a viewport point where a real pointer would hit the element
// (or one of its descendants), so that page.mouse.move(x, y) triggers its :hover state.
(args) => {
  const el = document.querySelector(`[data-gdm-index="${args.index}"]`);
  if (!el) return { found: false, ok: false, x: 0, y: 0 };
  el.scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" });
  const rect = el.getBoundingClientRect();
  if (rect.width < 1 || rect.height < 1) return { found: true, ok: false, x: 0, y: 0 };
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const points = [
    [0.5, 0.5],
    [0.25, 0.5],
    [0.75, 0.5],
    [0.5, 0.25],
    [0.5, 0.75],
  ];
  for (const [fx, fy] of points) {
    const x = Math.min(Math.max(rect.left + rect.width * fx, 1), vw - 2);
    const y = Math.min(Math.max(rect.top + rect.height * fy, 1), vh - 2);
    const hit = document.elementFromPoint(x, y);
    if (hit && (hit === el || el.contains(hit) || (hit.shadowRoot && hit.contains(el)))) {
      return { found: true, ok: true, x, y };
    }
  }
  return { found: true, ok: false, x: 0, y: 0 };
}
