// Focus release.
// page.evaluate(script, {settleMs}) -> {blurred, tag, still_focused, finished_transitions}
//
// Pages often focus a control on load (autofocus email fields, search boxes focused by script,
// consent buttons focused for accessibility). Measured while focused, a form field reports its
// :focus / :focus-visible / :focus-within styles (an indigo focus border instead of the gray
// resting one) and screenshots show the focus ring. This script blurs the focused element
// (descending into open shadow roots), waits two animation frames so frameworks that track
// focus in JavaScript (onBlur -> remove an "is-focused" class) can re-render, then finishes the
// CSS transitions that started, so computed styles read the resting values right away.
async (args) => {
  "use strict";
  const options = args || {};
  const settleMs = Math.max(0, Number(options.settleMs) || 0);

  const deepActive = () => {
    let active = document.activeElement;
    while (active && active.shadowRoot && active.shadowRoot.activeElement) {
      active = active.shadowRoot.activeElement;
    }
    return active;
  };
  const isRoot = (el) => !el || el === document.body || el === document.documentElement;

  const active = deepActive();
  if (isRoot(active)) {
    return { blurred: false, tag: null, still_focused: false, finished_transitions: 0 };
  }
  // Tree scopes between the focused element and the document (shadow roots first): transitions
  // inside shadow trees are listed by their ShadowRoot, not by the document.
  const scopes = [];
  for (let node = active; node; ) {
    const root = node.getRootNode ? node.getRootNode() : null;
    if (root && root !== document && root.host) {
      scopes.push(root);
      node = root.host;
    } else {
      break;
    }
  }
  scopes.push(document);

  if (typeof active.blur === "function") active.blur();

  // Two frames (with a timeout fallback for throttled pages) for JS-driven focus classes.
  const frame = () =>
    new Promise((resolve) => {
      const timer = setTimeout(resolve, 100);
      requestAnimationFrame(() => {
        clearTimeout(timer);
        resolve();
      });
    });
  await frame();
  await frame();
  if (settleMs > 0) await new Promise((resolve) => setTimeout(resolve, settleMs));

  // getAnimations() flushes pending style changes, so transitions caused by the blur are listed.
  let finished = 0;
  const isTransition = (animation) =>
    (typeof CSSTransition !== "undefined" && animation instanceof CSSTransition) ||
    typeof animation.transitionProperty === "string";
  for (const scope of scopes) {
    let animations = [];
    try {
      animations = typeof scope.getAnimations === "function" ? scope.getAnimations() : [];
    } catch (error) {
      animations = [];
    }
    for (const animation of animations) {
      if (!isTransition(animation)) continue;
      try {
        animation.finish();
        finished += 1;
      } catch (error) {
        // An infinite or detached transition cannot be finished; leave it.
      }
    }
  }
  return {
    blurred: true,
    tag: active.localName || null,
    still_focused: deepActive() === active,
    finished_transitions: finished,
  };
}
