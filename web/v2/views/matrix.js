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
import { deriveTierCeiling } from "./drawer.js";
import {
  renderScatterSvg,
  renderScatterBackdropSvg,
  renderFocusOverlaySvg,
  markerShapeSvg,
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
 * Converts pointer `(clientX, clientY)` on a `<canvas>` bounding rect into
 * view-adjusted domain `(0..100)` coordinates and a 15px hit tolerance in
 * domain units, accounting for any active `view` pan/zoom (`x`, `y`, `scale`).
 */
export function clientToDomainCoords(clientX, clientY, rect, view = DEFAULT_VIEW) {
  const scale = view && view.scale ? view.scale : 1;
  const span = (DOMAIN_MAX - DOMAIN_MIN) / scale;
  const vx = view && typeof view.x === "number" ? Math.min(Math.max(view.x, DOMAIN_MIN), DOMAIN_MAX - span) : 0;
  const vy = view && typeof view.y === "number" ? Math.min(Math.max(view.y, DOMAIN_MIN), DOMAIN_MAX - span) : 0;
  const width = rect && rect.width ? rect.width : 1;
  const height = rect && rect.height ? rect.height : 1;
  const domainX = vx + ((clientX - rect.left) / width) * span;
  const domainY = vy + ((clientY - rect.top) / height) * span;
  const toleranceDomainUnits = (15 / width) * span;
  return { domainX, domainY, toleranceDomainUnits };
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
  return (
    `<div class="error-banner" role="alert">${escapeHtml(copy.matrixErrorText(error.endpoint, error.status, error.detail))}` +
    ` <button type="button" class="icon-button" data-matrix-retry="true">${escapeHtml(copy.RETRY_LABEL)}</button></div>`
  );
}

export function renderEmpty(state = {}) {
  const q = state && state.query ? state.query.trim() : "";
  const eco = state && state.ecosystem ? state.ecosystem.trim() : "";
  if (q || eco) {
    return (
      `<div class="empty-state"><p>${escapeHtml(copy.emptyFilteredText(q, eco, "rated"))}</p>` +
      `<p class="empty-actions"><button type="button" class="link-button" data-clear-filters="true">${escapeHtml(copy.CLEAR_FILTERS_LABEL)}</button></p></div>`
    );
  }
  return `<div class="empty-state">${escapeHtml(copy.MATRIX_EMPTY_TEXT)}</div>`;
}

function renderLegend(quadrantsHidden, points = []) {
  const counts = { "Sweet Spot": 0, Crucible: 0, Nursery: 0, Graveyard: 0 };
  for (const p of points) {
    if (counts[p.quadrant] !== undefined) counts[p.quadrant] += 1;
  }
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
        `aria-pressed="${!hidden}" title="${escapeHtml(copy.matrixQuadrantToggleLabel(name))} (${i + 1}): ${escapeHtml(copy.QUADRANT_DESCRIPTIONS[name])}">` +
        `<span aria-hidden="true">${glyph}</span> ${escapeHtml(copy.QUADRANT_LABELS[name])}` +
        `<span class="matrix-legend-count">${counts[name] || 0}</span></button>`
      );
    })
    .join("");
  return `<div class="matrix-legend" role="group" aria-label="${escapeHtml(copy.MATRIX_HEADING)}">${chips}</div>`;
}

function renderQuadrantCornerLabels(quadrantsHidden = {}) {
  const corners = [
    ["Sweet Spot", "top-left"],
    ["Crucible", "top-right"],
    ["Nursery", "bottom-left"],
    ["Graveyard", "bottom-right"],
  ];
  return corners
    .map(([name, pos]) => {
      const cls = `quad-${name.toLowerCase().replace(/\s+/g, "-")}`;
      const hidden = !!quadrantsHidden[name];
      return (
        `<div class="matrix-corner-label matrix-corner-${pos} ${cls}"${hidden ? " hidden" : ""} aria-hidden="true">` +
        `<span class="matrix-corner-title">${copy.QUADRANT_GLYPHS[name]} ${escapeHtml(copy.QUADRANT_LABELS[name])}</span>` +
        `<span class="matrix-corner-desc">${escapeHtml(copy.QUADRANT_DESCRIPTIONS[name])}</span>` +
        `</div>`
      );
    })
    .join("");
}

/**
 * A spotlight row's tier, worded like the leaderboard ("B (capped)"). Matrix
 * points carry `coverage` but no `tier_ceiling`, so the ceiling is derived
 * the way Compare and the drawer derive it. Plain text, never
 * `renderTierCell`: its capped pill is a tab stop, and this sits inside a
 * <button>.
 */
function spotlightTierHtml(point) {
  if (!point.tier) return "";
  const ceiling = deriveTierCeiling(point.coverage, point.tier_ceiling);
  const capped = !!ceiling && point.tier === ceiling;
  const title = capped ? ` title="${escapeHtml(copy.TIER_CAPPED_EXPLANATION)}"` : "";
  return `<span class="tier-pill${capped ? " tier-capped" : ""}"${title}>${escapeHtml(copy.tierLabel(point.tier, ceiling))}</span>`;
}

function renderQuadrantSpotlight(points, quadrantsHidden = {}, activeKey = null) {
  const quadrantOrder = ["Sweet Spot", "Crucible", "Nursery", "Graveyard"];
  const total = points.length;
  const cards = quadrantOrder
    .map((name) => {
      const cls = `quad-${name.toLowerCase().replace(/\s+/g, "-")}`;
      const hidden = !!quadrantsHidden[name];
      const qPoints = points
        .filter((p) => p.quadrant === name)
        .sort((a, b) => (b.porchlamp ?? -1) - (a.porchlamp ?? -1));
      const sharePct = total > 0 ? Math.round((qPoints.length / total) * 100) : 0;
      const topRepos = qPoints.slice(0, 5);
      const repoItems = topRepos.length
        ? `<ul class="matrix-top-repos" role="list">` +
          topRepos
            .map((p) => {
              const key = pointKey(p);
              const isFocused = key === activeKey;
              const scoreText = typeof p.porchlamp === "number" ? p.porchlamp.toFixed(2) : copy.MISSING_VALUE;
              return (
                `<li>` +
                `<button type="button" class="matrix-top-repo-btn${isFocused ? " is-active" : ""}" ` +
                `data-owner="${escapeHtml(p.owner)}" data-name="${escapeHtml(p.name)}" ` +
                `data-matrix-spotlight-key="${escapeHtml(key)}">` +
                `<span class="matrix-top-repo-name">${escapeHtml(p.owner)}/<wbr>${escapeHtml(p.name)}</span>` +
                `<span class="matrix-top-repo-meta">${spotlightTierHtml(p)}<span class="matrix-top-repo-score">${escapeHtml(scoreText)}</span></span>` +
                `</button></li>`
              );
            })
            .join("") +
          `</ul>`
        : `<p class="matrix-quad-empty">${escapeHtml(copy.MATRIX_QUADRANT_EMPTY)}</p>`;

      return (
        `<article class="matrix-quad-card ${cls}${hidden ? " is-quad-hidden" : ""}">` +
        `<div class="matrix-quad-card-head">` +
        `<button type="button" class="matrix-quad-card-toggle ${cls}" data-matrix-quadrant-toggle="${escapeHtml(name)}" aria-pressed="${!hidden}">` +
        `<span aria-hidden="true">${copy.QUADRANT_GLYPHS[name]}</span> ` +
        `<span class="matrix-quad-card-title">${escapeHtml(copy.QUADRANT_LABELS[name])}</span>` +
        `</button>` +
        `<span class="matrix-quad-card-count">${escapeHtml(copy.matrixQuadrantCountText(qPoints.length, total))}</span>` +
        `</div>` +
        `<div class="matrix-quad-bar" aria-hidden="true"><span class="matrix-quad-bar-fill ${cls}" style="width:${sharePct}%"></span></div>` +
        `<p class="matrix-quad-axes">${escapeHtml(copy.QUADRANT_DESCRIPTIONS[name])}</p>` +
        `<p class="matrix-quad-guidance">${escapeHtml(copy.MATRIX_QUADRANT_GUIDANCE[name] || "")}</p>` +
        `<div class="matrix-quad-top-section">` +
        `<p class="matrix-quad-top-label">${escapeHtml(copy.MATRIX_TOP_REPOS_LABEL)}</p>` +
        repoItems +
        `</div>` +
        `</article>`
      );
    })
    .join("");

  return (
    `<aside class="matrix-spotlight" aria-label="${escapeHtml(copy.MATRIX_SPOTLIGHT_HEADING)}">` +
    `<div class="matrix-spotlight-head">` +
    `<h2 class="matrix-spotlight-title">${escapeHtml(copy.MATRIX_SPOTLIGHT_HEADING)}</h2>` +
    `<p class="matrix-spotlight-sub">${escapeHtml(copy.MATRIX_SPOTLIGHT_SUBTITLE)}</p>` +
    `</div>` +
    `<div class="matrix-quad-grid">${cards}</div>` +
    `</aside>`
  );
}

function renderCaptions(matrix, hasStars) {
  const parts = [`<p class="matrix-caption">${escapeHtml(copy.matrixExcludedCaption(matrix.excluded_count))}</p>`];
  for (const [axisName, entry] of Object.entries(matrix.axis_coverage || {})) {
    const text = copy.matrixAxisCoverageCaption(axisName, entry);
    if (text) parts.push(`<p class="matrix-caption">${escapeHtml(text)}</p>`);
  }
  parts.push(`<p class="matrix-caption">${escapeHtml(copy.MATRIX_HOW_TO_READ)}</p>`);
  const starsNote = hasStars ? copy.MATRIX_UNAUDITED_STARS_NOTE : copy.MATRIX_NO_STARS_NOTE;
  parts.push(`<p class="matrix-caption matrix-caption-muted">${escapeHtml(starsNote)}</p>`);
  return `<div class="matrix-captions">${parts.join("")}</div>`;
}

export function filterMatrixPoints(points, state) {
  if (!points) return [];
  const eco = state && state.ecosystem ? state.ecosystem.trim().toLowerCase() : "";
  const q = state && state.query ? state.query.trim().toLowerCase() : "";
  if (!eco && !q) return points;
  return points.filter((p) => {
    if (eco && String(p.ecosystem || "").toLowerCase() !== eco) return false;
    if (q && !`${p.owner}/${p.name}`.toLowerCase().includes(q)) return false;
    return true;
  });
}

export function renderHoverCard(point, view = DEFAULT_VIEW) {
  if (!point) return `<div class="matrix-hover-card" hidden></div>`;
  const scale = view && view.scale ? view.scale : 1;
  const span = (DOMAIN_MAX - DOMAIN_MIN) / scale;
  const vx = view && typeof view.x === "number" ? Math.min(Math.max(view.x, DOMAIN_MIN), DOMAIN_MAX - span) : 0;
  const vy = view && typeof view.y === "number" ? Math.min(Math.max(view.y, DOMAIN_MIN), DOMAIN_MAX - span) : 0;
  const rawX = ((toSvgX(point.friction ?? 50) - vx) / span) * 100;
  const rawY = ((toSvgY(point.leverage ?? 50) - vy) / span) * 100;
  const x = Math.max(0, Math.min(100, rawX));
  const y = Math.max(0, Math.min(100, rawY));
  const placeLeft = x > 50;
  const placeAbove = y > 50;
  const horizStyle = placeLeft
    ? `right: ${Math.max(2, Math.min(85, 100 - x + 2)).toFixed(1)}%; left: auto;`
    : `left: ${Math.max(2, Math.min(55, x + 2)).toFixed(1)}%; right: auto;`;
  const vertStyle = placeAbove
    ? `bottom: ${Math.max(2, Math.min(82, 100 - y + 3)).toFixed(1)}%; top: auto;`
    : `top: ${Math.max(2, Math.min(68, y + 3)).toFixed(1)}%; bottom: auto;`;
  const posClass = placeLeft && !placeAbove ? " matrix-hover-card--top-left" : "";
  return (
    `<div class="matrix-hover-card${posClass}" data-matrix-hover-card="true" style="${horizStyle} ${vertStyle}">` +
    `<p class="repo-name">${escapeHtml(point.owner)}/${escapeHtml(point.name)}</p>` +
    `<p class="cell-numeric">Score ${point.porchlamp != null ? point.porchlamp.toFixed(2) : copy.MISSING_VALUE} · ${renderCoverageBadge(point.coverage)} · ${renderTierCell(point.tier, point.tier_ceiling)}</p>` +
    `<p class="hint">${escapeHtml(copy.MATRIX_OPEN_HINT)}</p>` +
    `</div>`
  );
}

/**
 * The full Screen B markup for one state snapshot. `visiblePoints.length` decides
 * SVG vs. canvas (module docstring / `components/scatter.js`); the canvas
 * path still renders an SVG overlay for the focused/hovered marker only.
 */
export function renderMatrixBody(state) {
  const matrix = state.matrix;
  const points = filterMatrixPoints(matrix.points, state);
  const visiblePoints = points.filter((p) => !state.matrixQuadrantsHidden[p.quadrant]);
  const usesCanvas = visiblePoints.length > SVG_MARKER_BUDGET;
  const activeKey = state.matrixHoveredKey || state.matrixFocusedKey;
  const focused = visiblePoints.find((p) => pointKey(p) === activeKey);
  const { min: overlayStarsMin, max: overlayStarsMax } = starsDomain(points);
  const hasStars = points.some((p) => p.stars_audited !== null && p.stars_audited !== undefined);
  const surfaceLabel = copy.matrixSvgAriaLabel(copy.MATRIX_AXIS_X_LABEL, copy.MATRIX_AXIS_Y_LABEL);
  const view = state.matrixView || DEFAULT_VIEW;
  const viewBox = computeViewBox(view);
  const surface = usesCanvas
    ? `<div class="matrix-canvas-wrap">${renderScatterBackdropSvg(viewBox)}<canvas class="matrix-canvas" data-matrix-canvas="true" width="800" height="800" role="img" aria-label="${escapeHtml(surfaceLabel)}"></canvas>${renderFocusOverlaySvg(focused, overlayStarsMin, overlayStarsMax, viewBox)}</div>`
    : renderScatterSvg(points, {
        quadrantsHidden: state.matrixQuadrantsHidden,
        focusedKey: state.matrixFocusedKey,
        hoveredKey: state.matrixHoveredKey,
        viewBox,
      });
  return (
    `<div class="matrix-toolbar">` +
    renderLegend(state.matrixQuadrantsHidden, points) +
    `<div class="matrix-toolbar-controls">` +
    `<span class="matrix-plotted-pill">${escapeHtml(copy.matrixPlottedCountText(visiblePoints.length, points.length))}</span>` +
    `<button type="button" class="icon-button" data-matrix-reset-view="true">${escapeHtml(copy.MATRIX_RESET_VIEW_LABEL)}</button>` +
    `<button type="button" class="icon-button" data-matrix-zoom-in="true" aria-label="${escapeHtml(copy.MATRIX_ZOOM_IN_LABEL)}">+</button>` +
    `<button type="button" class="icon-button" data-matrix-zoom-out="true" aria-label="${escapeHtml(copy.MATRIX_ZOOM_OUT_LABEL)}">−</button>` +
    `</div>` +
    `</div>` +
    `<div class="matrix-layout">` +
    `<div class="matrix-plot-col">` +
    `<div class="matrix-axis-labels"><span>${escapeHtml(copy.MATRIX_AXIS_Y_LABEL)} ↑</span><span>${escapeHtml(copy.MATRIX_AXIS_X_LABEL)} →</span></div>` +
    `<div class="matrix-stage" data-matrix-stage="true" tabindex="0" aria-label="${escapeHtml(`${surfaceLabel}. ${copy.MATRIX_STAGE_LABEL}`)}">${surface}${renderQuadrantCornerLabels(state.matrixQuadrantsHidden)}${renderHoverCard(focused, view)}</div>` +
    renderCaptions(matrix, hasStars) +
    `</div>` +
    renderQuadrantSpotlight(points, state.matrixQuadrantsHidden, activeKey) +
    `</div>`
  );
}

export function shellShape(state) {
  if (state.matrixLoading && !state.matrix) return "loading";
  if (state.matrixError && !state.matrix) return "error";
  if (state.matrix && filterMatrixPoints(state.matrix.points, state).length === 0) {
    return `empty:${state.ecosystem || ""}:${state.query || ""}`;
  }
  if (state.matrix) {
    // A quadrant toggle or ecosystem/query filter changes WHICH markers
    // `renderMatrixBody` includes (filtered before rendering, not hidden
    // after the fact) — folding the hidden set and active filters into the
    // shape key is what makes `syncView` rebuild the SVG/canvas surface on
    // `1`-`4` or filter changes instead of silently leaving stale markers.
    const hiddenKey = Object.keys(state.matrixQuadrantsHidden).sort().join(",");
    return `body:${hiddenKey}:${state.ecosystem || ""}:${state.query || ""}`;
  }
  return "loading";
}

export function renderShell(state) {
  const shape = shellShape(state);
  const heading = `<h1 class="view-heading">${escapeHtml(copy.MATRIX_HEADING)}</h1>`;
  if (shape === "loading") return heading + renderLoading();
  if (shape === "error") return heading + renderError(state.matrixError);
  if (shape.startsWith("empty")) return heading + renderEmpty(state);
  return heading + renderMatrixBody(state);
}

/** Applies the current `viewBox` to all rendered SVG surfaces (including the canvas backdrop and focus overlay) and rescales SVG marker geometries without a full re-render. */
export function syncViewBox(main, view) {
  const viewBox = computeViewBox(view);
  for (const svg of main.querySelectorAll(".matrix-svg")) {
    svg.setAttribute("viewBox", viewBox);
  }
  const vbParts = String(viewBox).trim().split(/\s+/).map(Number);
  const span = vbParts.length === 4 && Number.isFinite(vbParts[2]) && vbParts[2] > 0 ? vbParts[2] : 100;
  const zoomRatio = span / 100;
  for (const marker of main.querySelectorAll(".matrix-marker[data-base-r]")) {
    const baseR = Number(marker.getAttribute("data-base-r"));
    const quadrant = marker.getAttribute("data-quadrant") || "Nursery";
    if (Number.isFinite(baseR) && baseR > 0) {
      const nextShape = markerShapeSvg(quadrant, baseR * zoomRatio);
      if (marker.innerHTML !== nextShape) marker.innerHTML = nextShape;
    }
  }
}

/**
 * Patches JUST the focused/hovered marker's highlight class and the hover
 * card's content — never a shell rebuild (hovering/focusing never changes
 * `shellShape`'s result, so `syncView` calls this on every state change
 * while the matrix body is showing, exactly like the leaderboard's
 * per-field syncs).
 */
export function syncMatrixHover(main, state) {
  const activeKey = state.matrixHoveredKey || state.matrixFocusedKey;
  const points = state.matrix
    ? filterMatrixPoints(state.matrix.points, state).filter((p) => !state.matrixQuadrantsHidden[p.quadrant])
    : [];
  for (const marker of main.querySelectorAll(".matrix-marker")) {
    const isActive = marker.dataset.key === state.matrixFocusedKey || marker.dataset.key === state.matrixHoveredKey;
    marker.classList.toggle("marker-focused", isActive);
  }
  for (const btn of main.querySelectorAll("[data-matrix-spotlight-key]")) {
    btn.classList.toggle("is-active", btn.dataset.matrixSpotlightKey === activeKey);
  }
  const point = points.find((p) => pointKey(p) === activeKey) || null;
  const view = state.matrixView || DEFAULT_VIEW;
  const card = main.querySelector(".matrix-hover-card");
  if (card) card.outerHTML = renderHoverCard(point, view);

  const overlayHost = main.querySelector(".matrix-canvas-wrap");
  if (overlayHost) {
    const existingOverlay = overlayHost.querySelector(".matrix-focus-overlay");
    if (existingOverlay) existingOverlay.remove();
    const { min: overlayStarsMin, max: overlayStarsMax } = starsDomain(points);
    const viewBox = computeViewBox(view);
    const nextOverlay = renderFocusOverlaySvg(point, overlayStarsMin, overlayStarsMax, viewBox);
    if (nextOverlay) overlayHost.insertAdjacentHTML("beforeend", nextOverlay);
  }
}
