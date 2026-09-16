// components/keymap.js — the declarative key -> action table (06 §5) and
// the "?" overlay markup. `actionForKeyEvent` is a pure function so the
// full keymap can be exercised without a DOM.

import * as copy from "../copy.js";

/**
 * One entry per key documented in 06 §5 / copy.js's KEYMAP_ENTRIES. `key`
 * matches `KeyboardEvent.key` (case-insensitive for letters).
 * `globalOnly: false` entries fire even while a text input is focused
 * (Escape, Enter); everything else is suppressed while typing so letters
 * reach the input instead of triggering a shortcut.
 */
export const KEY_ACTIONS = Object.freeze([
  { key: "/", action: "focus-search", globalOnly: true },
  { key: "j", action: "cursor-down", globalOnly: true },
  { key: "k", action: "cursor-up", globalOnly: true },
  { key: "Enter", action: "open-selected", globalOnly: false },
  { key: "Escape", action: "close-overlay", globalOnly: false },
  { key: "]", action: "next-page", globalOnly: true },
  { key: "[", action: "prev-page", globalOnly: true },
  { key: "1", action: "matrix-toggle-1", globalOnly: true },
  { key: "2", action: "matrix-toggle-2", globalOnly: true },
  { key: "3", action: "matrix-toggle-3", globalOnly: true },
  { key: "4", action: "matrix-toggle-4", globalOnly: true },
  { key: "r", action: "matrix-reset-view", globalOnly: true },
  { key: "ArrowUp", action: "matrix-focus-up", globalOnly: true },
  { key: "ArrowDown", action: "matrix-focus-down", globalOnly: true },
  { key: "ArrowLeft", action: "matrix-focus-left", globalOnly: true },
  { key: "ArrowRight", action: "matrix-focus-right", globalOnly: true },
  { key: "l", action: "nav-leaderboard", globalOnly: true },
  { key: "b", action: "nav-matrix", globalOnly: true },
  { key: "c", action: "nav-compare", globalOnly: true },
  { key: "w", action: "nav-configure", globalOnly: true },
  { key: "m", action: "nav-methodology", globalOnly: true },
  { key: "p", action: "open-persona-menu", globalOnly: true },
  { key: "t", action: "toggle-theme", globalOnly: true },
  { key: "?", action: "toggle-keymap", globalOnly: true },
]);

const ACTIONS_BY_KEY = new Map(KEY_ACTIONS.map((entry) => [entry.key.toLowerCase(), entry]));

/**
 * @param {{key: string}} evt A KeyboardEvent, or an event-shaped test double.
 * @param {{isInputFocused: boolean}} context
 * @returns {string|null} the action name, or null if this key has no binding
 *   (or is suppressed because a text input is focused).
 */
export function actionForKeyEvent(evt, context = { isInputFocused: false, isInteractiveFocused: false }) {
  if (evt.metaKey || evt.ctrlKey || evt.altKey) return null;
  const entry = ACTIONS_BY_KEY.get(String(evt.key).toLowerCase());
  if (!entry) return null;
  if (entry.globalOnly && context.isInputFocused) return null;
  if (entry.action === "open-selected" && context.isInteractiveFocused) return null;
  return entry.action;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

export function renderKeymapOverlay() {
  const rows = copy.KEYMAP_ENTRIES.map(
    (entry) => `<tr><td><kbd>${escapeHtml(entry.keys)}</kbd></td><td>${escapeHtml(entry.action)}</td></tr>`
  ).join("");
  return (
    `<div class="keymap-overlay-backdrop" data-keymap-backdrop="true">` +
    `<div class="keymap-overlay" role="dialog" aria-modal="true" aria-labelledby="keymap-title">` +
    `<h2 id="keymap-title">${escapeHtml(copy.KEYMAP_TITLE)}</h2>` +
    `<table>${rows}</table>` +
    `<button type="button" class="icon-button" data-keymap-close="true">${escapeHtml(copy.KEYMAP_CLOSE_LABEL)}</button>` +
    `</div></div>`
  );
}
