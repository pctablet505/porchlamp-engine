// views/matrix.js — Screen B (06 §3 B): the friction/leverage scatter,
// quadrant toggles, hover card, zoom/pan, and this screen's four explicit
// states (loading/empty/error/partial — U-7). Pure render functions
// (`renderShell`) plus a handful of pure geometry helpers app.js's event
// handlers call directly (`zoomView`/`panView`/`nearestMarkerInDirection`)
// so the interaction logic itself is hand-traceable/unit-testable without
// a DOM (06 §6).
//
// Keyboard scheme (06 §3 B: "zoom/pan by wheel and keys ... keyboard focus
// moves between markers (Tab/arrow keys)" — the two are reconciled here,
// documented since 06 does not spell out the disambiguation): Tab cycles
// DOM order; an arrow key moves focus to the nearest marker in that
// direction WHEN a marker currently has focus (or, above SVG_MARKER_BUDGET,
// always — there is no per-marker DOM node in canvas mode to check focus
// on); arrow keys pan the view (wheel-only otherwise) when the SVG
// container itself — not a marker — has focus and the SVG path is active.
// Zoom itself is wheel + the on-screen `+`/`−` BUTTONS
// (`[data-matrix-zoom-in/out]`), not keyboard shortcuts. `1`-`4` toggle a
// quadrant; `R` resets the view; Enter opens the drawer for the focused/
// virtually-focused marker; canvas-mode clicks are hit-tested
// (hitTestPoint) since there is no per-marker DOM node for the browser's
// own click targeting.

import * as copy from "../copy.js";
import { renderCoverageBadge, renderTierCell } from "../components/badge.js";
import {
  renderScatterSvg,
  renderFocusOverlaySvg,
  pointKey,
  toSvgX,
  toSvgY,
  starsDomain,
  SVG_MARKER_BUDGET,
  DOMAIN_MIN,
  DOMAIN_MAX,
} from "../components/scatter.js";

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

export const DEFAULT_VIEW = Object.freeze({ x: 0, y: 0, scale: 1 });
const MIN_SCALE = 1;
const MAX_SCALE = 8;

/** `view` (pan offset in domain units + scale) -> an SVG `viewBox` string. Clamped so the visible window never leaves the [0,100]x[0,100] domain. */
export function computeViewBox(view) {
  const span = (DOMAIN_MAX - DOMAIN_MIN) / view.scale;
  const x = Math.min(Math.max(view.x, DOMAIN_MIN), DOMAIN_MAX - span);
  const y = Math.min(Math.max(view.y, DOMAIN_MIN), DOMAIN_MAX - span);
  return `${x} ${y} ${span} ${span}`;
}

/** Zoom by `factor` (>1 zooms in), keeping `center` (domain coords, default the view's own center) stationary. Clamped to [MIN_SCALE, MAX_SCALE]. */
export function zoomView(view, factor, center) {
  const nextScale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, view.scale * factor));
  const span = (DOMAIN_MAX - DOMAIN_MIN) / view.scale;
  const cx = center ? center.x : view.x + span / 2;
  const cy = center ? center.y : view.y + span / 2;
  const nextSpan = (DOMAIN_MAX - DOMAIN_MIN) / nextScale;
  return {
    scale: nextScale,
    x: Math.min(Math.max(cx - nextSpan / 2, DOMAIN_MIN), DOMAIN_MAX - nextSpan),
    y: Math.min(Math.max(cy - nextSpan / 2, DOMAIN_MIN), DOMAIN_MAX - nextSpan),
  };
}

/** Pan by `(dx, dy)` domain units, clamped to keep the viewBox inside the domain. */
export function panView(view, dx, dy) {
  const span = (DOMAIN_MAX - DOMAIN_MIN) / view.scale;
  return {
    scale: view.scale,
    x: Math.min(Math.max(view.x + dx, DOMAIN_MIN), DOMAIN_MAX - span),
    y: Math.min(Math.max(view.y + dy, DOMAIN_MIN), DOMAIN_MAX - span),
  };
}

/**
 * Arrow-key spatial navigation between markers: the closest OTHER point
 * strictly in `direction` from the currently focused one (by simple axis
 * dominance + Euclidean tie-break), or `null` if none exists that way.
 */
export function nearestMarkerInDirection(points, currentKey, direction) {
  const current = points.find((p) => pointKey(p) === currentKey);
  if (!current) return points[0] || null;
  // Screen-space vectors (via toSvgX/toSvgY, which already flips the Y axis
  // so "up" is smaller svgY): "up"/"down"/"left"/"right" mean literal
  // on-screen directions, never raw leverage/friction sign (leverage is
  // Y-flipped on screen, so "up" is actually HIGHER leverage but SMALLER
  // svgY -- computing in screen space once avoids re-deriving that flip
  // here and getting it backwards).
  const cx = toSvgX(current.friction);
  const cy = toSvgY(current.leverage);
  const [dx, dy] = { up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0] }[direction];
  let best = null;
  let bestDist = Infinity;
  for (const p of points) {
    if (pointKey(p) === currentKey) continue;
    const vx = toSvgX(p.friction) - cx;
    const vy = toSvgY(p.leverage) - cy;
    const along = vx * dx + vy * dy;
    if (along <= 0) continue; // not in this direction at all
    const across = Math.abs(vx * dy - vy * dx); // perpendicular deviation from the pure axis
    const dist = along + across * 2; // penalise off-axis candidates more than distance itself
    if (dist < bestDist) {
      bestDist = dist;
      best = p;
    }
  }
  return best;
}

/**
 * Finds the point nearest a CLICK on the canvas surface: above
 * `SVG_MARKER_BUDGET` markers have no per-marker DOM node, so the
 * browser's own click hit-testing has nothing to land on and this simple
 * nearest-point search within `toleranceDomainUnits` stands in for it.
 * `domainX`/`domainY` are already-converted domain (0..100) coordinates,
 * matching `toSvgX`/`toSvgY`'s own space; the caller converts the raw
 * pixel click offset before calling this (see `app.js`'s canvas click
 * handler).
 * Returns `null` when nothing is within tolerance (a click on empty
 * space is not an accidental open).
 */
export function hitTestPoint(points, domainX, domainY, toleranceDomainUnits) {
  let best = null;
  let bestDist = Infinity;
  for (const p of points) {
    const dx = toSvgX(p.friction) - domainX;
    const dy = toSvgY(p.leverage) - domainY;
    const dist = Math.sqrt(dx * dx + dy * dy);
    if (dist <= toleranceDomainUnits && dist < bestDist) {
      bestDist = dist;
      best = p;
    }
  }
  return best;
}

export function renderLoading() {
  return `<div class="empty-state" aria-busy="true">${escapeHtml(copy.MATRIX_LOADING_TEXT)}</div>`;
}

export function renderError(error) {
  return `<div class="error-banner" role="alert">${escapeHtml(copy.matrixErrorText(error.endpoint, error.status, error.detail))}</div>`;
}

export function renderEmpty() {
  return `<div class="empty-state">${escapeHtml(copy.MATRIX_EMPTY_TEXT)}</div>`;
}

function renderLegend(quadrantsHidden) {
  const entries = [
    ["Sweet Spot", copy.QUADRANT_GLYPHS["Sweet Spot"]],
    ["Crucible", copy.QUADRANT_GLYPHS.Crucible],
    ["Nursery", copy.QUADRANT_GLYPHS.Nursery],
    ["Graveyard", copy.QUADRANT_GLYPHS.Graveyard],
  ];
  const chips = entries
    .map(([name, glyph], i) => {
      const cls = `quad-${name.toLowerCase().replace(/\s+/g, "-")}`;
      const hidden = !!quadrantsHidden[name];
      return (
        `<button type="button" class="matrix-legend-chip ${cls}" data-matrix-quadrant-toggle="${escapeHtml(name)}" ` +
        `aria-pressed="${!hidden}" title="${escapeHtml(copy.matrixQuadrantToggleLabel(name))} (${i + 1})">` +
        `<span aria-hidden="true">${glyph}</span> ${escapeHtml(copy.QUADRANT_LABELS[name])}</button>`
      );
    })
    .join("");
  return `<div class="matrix-legend" role="group" aria-label="${escapeHtml(copy.MATRIX_HEADING)}">${chips}</div>`;
}

function renderCaptions(matrix) {
  const parts = [`<p class="matrix-caption">${escapeHtml(copy.matrixExcludedCaption(matrix.excluded_count))}</p>`];
  for (const [axisName, entry] of Object.entries(matrix.axis_coverage || {})) {
    const text = copy.matrixAxisCoverageCaption(axisName, entry);
    if (text) parts.push(`<p class="matrix-caption">${escapeHtml(text)}</p>`);
  }
  parts.push(`<p class="matrix-caption matrix-caption-muted">${escapeHtml(copy.MATRIX_UNAUDITED_STARS_NOTE)}</p>`);
  return parts.join("");
}

function renderHoverCard(point) {
  if (!point) return `<div class="matrix-hover-card" hidden></div>`;
  const isRight = (point.friction ?? 0) > 45;
  const isTop = (point.leverage ?? 0) > 45;
  const posClass = isRight && isTop ? " matrix-hover-card--top-left" : "";
  return (
    `<div class="matrix-hover-card${posClass}" data-matrix-hover-card="true">` +
    `<p class="repo-name">${escapeHtml(point.owner)}/${escapeHtml(point.name)}</p>` +
    `<p class="cell-numeric">Score ${point.porchlamp != null ? point.porchlamp.toFixed(2) : copy.MISSING_VALUE} · ${renderCoverageBadge(point.coverage)} · ${renderTierCell(point.tier, point.tier_ceiling)}</p>` +
    `<p class="hint">${escapeHtml(copy.MATRIX_OPEN_HINT)}</p>` +
    `</div>`
  );
}

/**
 * The full Screen B markup for one state snapshot. `points.length` decides
 * SVG vs. canvas (module docstring / `components/scatter.js`); the canvas
 * path still renders an SVG overlay for the focused/hovered marker only.
 */
export function renderMatrixBody(state) {
  const matrix = state.matrix;
  const points = matrix.points;
  const usesCanvas = points.length > SVG_MARKER_BUDGET;
  const focused = points.find((p) => pointKey(p) === (state.matrixFocusedKey || state.matrixHoveredKey));
  const { min: overlayStarsMin, max: overlayStarsMax } = starsDomain(points);
  const surface = usesCanvas
    ? `<div class="matrix-canvas-wrap"><canvas class="matrix-canvas" data-matrix-canvas="true" width="800" height="800"></canvas>${renderFocusOverlaySvg(focused, overlayStarsMin, overlayStarsMax)}` +
      `<p class="matrix-caption">${escapeHtml(copy.MATRIX_CANVAS_FALLBACK_NOTE)}</p></div>`
    : renderScatterSvg(points, {
        quadrantsHidden: state.matrixQuadrantsHidden,
        focusedKey: state.matrixFocusedKey,
        hoveredKey: state.matrixHoveredKey,
      });
  return (
    `<div class="matrix-toolbar">` +
    renderLegend(state.matrixQuadrantsHidden) +
    `<button type="button" class="icon-button" data-matrix-reset-view="true">${escapeHtml(copy.MATRIX_RESET_VIEW_LABEL)}</button>` +
    `<button type="button" class="icon-button" data-matrix-zoom-in="true" aria-label="${escapeHtml(copy.MATRIX_ZOOM_IN_LABEL)}">+</button>` +
    `<button type="button" class="icon-button" data-matrix-zoom-out="true" aria-label="${escapeHtml(copy.MATRIX_ZOOM_OUT_LABEL)}">−</button>` +
    `</div>` +
    `<div class="matrix-axis-labels"><span>${escapeHtml(copy.MATRIX_AXIS_Y_LABEL)} ↑</span><span>${escapeHtml(copy.MATRIX_AXIS_X_LABEL)} →</span></div>` +
    `<div class="matrix-stage" data-matrix-stage="true" tabindex="0">${surface}${renderHoverCard(focused)}</div>` +
    renderCaptions(matrix)
  );
}

export function shellShape(state) {
  if (state.matrixLoading && !state.matrix) return "loading";
  if (state.matrixError && !state.matrix) return "error";
  if (state.matrix && state.matrix.points.length === 0) return "empty";
  if (state.matrix) {
    // A quadrant toggle changes WHICH markers `renderMatrixBody` includes
    // (filtered before rendering, not hidden after the fact) — folding the
    // hidden set into the shape key is what makes `syncView` rebuild the
    // SVG/canvas surface on `1`-`4` instead of the toggle silently doing
    // nothing to the already-rendered markers (a real bug caught during
    // live-browser verification, see the handoff). This is a deliberate,
    // infrequent, discrete action (never a per-frame one like scroll), so
    // a full rebuild here does not touch U-12's actual target (hover/
    // scroll-driven churn), which stays on `syncMatrixHover`/`syncViewBox`.
    const hiddenKey = Object.keys(state.matrixQuadrantsHidden).sort().join(",");
    return `body:${hiddenKey}`;
  }
  return "loading";
}

export function renderShell(state) {
  const shape = shellShape(state);
  const heading = `<h1 class="view-heading">${escapeHtml(copy.MATRIX_HEADING)}</h1>`;
  if (shape === "loading") return heading + renderLoading();
  if (shape === "error") return heading + renderError(state.matrixError);
  if (shape === "empty") return heading + renderEmpty();
  return heading + renderMatrixBody(state);
}

/** Applies the current `viewBox` to an already-rendered SVG surface without a full re-render (pan/zoom only ever touches this one attribute). */
export function syncViewBox(main, view) {
  const svg = main.querySelector(".matrix-svg:not(.matrix-focus-overlay)");
  if (svg) svg.setAttribute("viewBox", computeViewBox(view));
}

/**
 * Patches JUST the focused/hovered marker's highlight class and the hover
 * card's content — never a shell rebuild (hovering/focusing never changes
 * `shellShape`'s result, so `syncView` calls this on every state change
 * while the matrix body is showing, exactly like the leaderboard's
 * per-field syncs).
 */
export function syncMatrixHover(main, state) {
  const activeKey = state.matrixFocusedKey || state.matrixHoveredKey;
  const points = state.matrix ? state.matrix.points : [];
  for (const marker of main.querySelectorAll(".matrix-marker")) {
    const isActive = marker.dataset.key === activeKey;
    marker.classList.toggle("marker-focused", isActive);
  }
  const point = points.find((p) => pointKey(p) === activeKey) || null;
  // Rebuilding this one small card (never an ancestor of a focused MARKER —
  // it is a sibling element) on every hover/focus change is well within
  // budget; `outerHTML` replaces just this node, not the SVG/canvas surface.
  const card = main.querySelector(".matrix-hover-card");
  if (card) card.outerHTML = renderHoverCard(point);

  const overlayHost = main.querySelector(".matrix-canvas-wrap");
  if (overlayHost) {
    const existingOverlay = overlayHost.querySelector(".matrix-focus-overlay");
    if (existingOverlay) existingOverlay.remove();
    const { min: overlayStarsMin, max: overlayStarsMax } = starsDomain(points);
    const nextOverlay = renderFocusOverlaySvg(point, overlayStarsMin, overlayStarsMax);
    if (nextOverlay) overlayHost.insertAdjacentHTML("beforeend", nextOverlay);
  }
}
