// Light / dark theme: follows the system by default, with a manual choice remembered on this
// machine (prefs: kept between launches although the server port changes).

import { prefs } from "./dom.js";

const THEME_KEY = "gdm.theme";
const MODES = ["auto", "light", "dark"];
const media = window.matchMedia("(prefers-color-scheme: dark)");
const listeners = new Set();

export function themeMode() {
  const saved = prefs.get(THEME_KEY);
  return MODES.includes(saved) ? saved : "auto";
}

/** The scheme actually shown: "light" or "dark". */
export function currentScheme() {
  const mode = themeMode();
  if (mode === "auto") return media.matches ? "dark" : "light";
  return mode;
}

function apply(mode) {
  const root = document.documentElement;
  if (mode === "auto") {
    delete root.dataset.theme;
  } else {
    root.dataset.theme = mode;
  }
  for (const handler of listeners) handler(currentScheme());
}

export function onThemeChange(handler) {
  listeners.add(handler);
}

export function setThemeMode(mode) {
  const value = MODES.includes(mode) ? mode : "auto";
  if (value === "auto") {
    prefs.remove(THEME_KEY);
  } else {
    prefs.set(THEME_KEY, value);
  }
  apply(value);
}

export function initTheme() {
  const mode = themeMode();
  apply(mode);
  const radios = document.querySelectorAll('input[name="theme"]');
  for (const radio of radios) {
    radio.checked = radio.value === mode;
    radio.addEventListener("change", () => {
      if (radio.checked) setThemeMode(radio.value);
    });
  }
  media.addEventListener("change", () => {
    if (themeMode() === "auto") apply("auto");
  });
}

/** Current value of a CSS custom property of the page (resolved for the active theme). */
export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
