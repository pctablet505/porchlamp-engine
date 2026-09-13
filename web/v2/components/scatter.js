// components/scatter.js — Screen B's inline SVG (and, above the marker
// budget, canvas) scatter: friction (x) vs leverage (y), 06 §3 B.
//
// Encoding (colour-blind safe, spec §5.1 / U-3's "shape+colour" rule,
// never colour alone):
//   Sweet Spot -> diamond, Crucible -> hexagon, Nursery -> circle,
//   Graveyard -> cross ("x"). Colour from the SAME --quad-* tokens the
//   leaderboard's quadrant glyph already uses (styles/tokens.css).
// Marker SIZE = `stars_audited` (sqrt-scaled area, so the visual size
// difference reflects the actual magnitude ratio) — a `null` value (no
// v2 producer yet, W1-10 gap) draws at DEFAULT_RADIUS and is counted for
// the "unaudited" legend note the view renders alongside this component.
//
// Performance switch (06 §7 "matrix render < 150ms for 10k markers"):
// `SVG_MARKER_BUDGET` markers or fewer render as real SVG `<*>` elements
// (each independently focusable/hoverable, real DOM accessibility);
// above that, `drawMatrixCanvas` paints every marker on a single
// `<canvas>` 2D context (one draw call per marker, no per-marker DOM
// node) and the SAME `renderFocusOverlaySvg` draws ONLY the
// currently-focused/hovered marker as a tiny SVG overlay on top, so
// keyboard/hover interaction keeps working past the SVG budget without
// paying its per-node DOM cost for the other 9,999 markers.

import * as copy from "../copy.js";

export const DOMAIN_MIN = 0;
export const DOMAIN_MAX = 100;
export const QUADRANT_THRESHOLD = 50;
export const SVG_MARKER_BUDGET = 2000;
// Radii are in VIEWBOX USER UNITS (the plot area is a 100x100 domain,
// module docstring) -- NOT screen pixels; the browser scales the whole
// <svg> up to the `.matrix-stage` element's actual rendered size.
export const DEFAULT_RADIUS = 1.6;
const MIN_RADIUS = 1.1;
const MAX_RADIUS = 3.6;

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

export function pointKey(point) {
  return `${point.owner}/${point.name}`;
}

/** Domain (friction/leverage, 0..100) -> SVG viewBox coordinates (y flipped: high leverage draws near the top). */
export function toSvgX(friction) {
  return friction;
}
export function toSvgY(leverage) {
  return DOMAIN_MAX - leverage;
}

/**
 * Sqrt-scaled radius so a marker's AREA (not its diameter) is proportional
 * to `starsAudited` — the perceptually correct scale for a size encoding.
 * `null`/`undefined` -> :data:`DEFAULT_RADIUS` (the "unaudited" case).
 */
export function radiusForStars(starsAudited, domainMin, domainMax) {
  if (starsAudited === null || starsAudited === undefined) return DEFAULT_RADIUS;
  if (!(domainMax > domainMin)) return DEFAULT_RADIUS;
  const t = Math.sqrt(Math.max(0, starsAudited - domainMin) / (domainMax - domainMin));
  return MIN_RADIUS + t * (MAX_RADIUS - MIN_RADIUS);
}

export function starsDomain(points) {
  const values = points.map((p) => p.stars_audited).filter((v) => v !== null && v !== undefined);
  if (values.length === 0) return { min: 0, max: 0 };
  return { min: Math.min(...values), max: Math.max(...values) };
}

const QUAD_CLASS = {
  "Sweet Spot": "quad-sweet-spot",
  Crucible: "quad-crucible",
  Nursery: "quad-nursery",
  Graveyard: "quad-graveyard",
};

/** One marker's shape as an inline SVG fragment (no wrapping `<g>` — the caller adds the transform/attributes). Colour comes from CSS (`class`, `currentColor`); shape is the dual encoding U-3 requires. */
function markerShapeSvg(quadrant, r) {
  if (quadrant === "Sweet Spot") {
    // Diamond
    return `<path d="M 0 ${-r} L ${r} 0 L 0 ${r} L ${-r} 0 Z" />`;
  }
  if (quadrant === "Crucible") {
    // Hexagon
    const pts = [0, 60, 120, 180, 240, 300].map((deg) => {
      const rad = (deg * Math.PI) / 180;
      return `${(r * Math.sin(rad)).toFixed(2)},${(-r * Math.cos(rad)).toFixed(2)}`;
    });
    return `<polygon points="${pts.join(" ")}" />`;
  }
  if (quadrant === "Graveyard") {
    const s = r * 0.72;
    return (
      `<line x1="${-s}" y1="${-s}" x2="${s}" y2="${s}" stroke-width="${Math.max(0.5, r * 0.35)}" />` +
      `<line x1="${-s}" y1="${s}" x2="${s}" y2="${-s}" stroke-width="${Math.max(0.5, r * 0.35)}" />`
    );
  }
  // Nursery (and any unrecognised quadrant): circle
  return `<circle r="${r}" />`;
}

/**
 * The full inline SVG for the SVG-budget path (`points.length <=
 * SVG_MARKER_BUDGET`): quadrant washes, axis labels, one marker per
 * point. Each marker is focusable (`tabindex="0"`) and carries
 * `data-owner`/`data-name` so app.js's existing delegated click/keydown
 * handlers (the same ones the leaderboard rows use) work unchanged.
 */
export function renderScatterSvg(points, { quadrantsHidden = {}, focusedKey = null, hoveredKey = null, axisLabels = { x: copy.MATRIX_AXIS_X_LABEL, y: copy.MATRIX_AXIS_Y_LABEL } } = {}) {
  const { min: starsMin, max: starsMax } = starsDomain(points);
  const visible = points.filter((p) => !quadrantsHidden[p.quadrant]);
  const washes = [
    { quadrant: "Nursery", x: 0, y: QUADRANT_THRESHOLD, w: QUADRANT_THRESHOLD, h: QUADRANT_THRESHOLD },
    { quadrant: "Graveyard", x: QUADRANT_THRESHOLD, y: QUADRANT_THRESHOLD, w: QUADRANT_THRESHOLD, h: QUADRANT_THRESHOLD },
    { quadrant: "Sweet Spot", x: 0, y: 0, w: QUADRANT_THRESHOLD, h: QUADRANT_THRESHOLD },
    { quadrant: "Crucible", x: QUADRANT_THRESHOLD, y: 0, w: QUADRANT_THRESHOLD, h: QUADRANT_THRESHOLD },
  ]
    .map(
      (w) =>
        `<rect class="matrix-wash matrix-wash-${QUAD_CLASS[w.quadrant]}" x="${w.x}" y="${w.y}" width="${w.w}" height="${w.h}" />`
    )
    .join("");

  const markers = visible
    .map((p) => {
      const cls = QUAD_CLASS[p.quadrant] || "quad-nursery";
      const r = radiusForStars(p.stars_audited, starsMin, starsMax);
      const key = pointKey(p);
      const focused = key === focusedKey || key === hoveredKey;
      const shape = markerShapeSvg(p.quadrant, r);
      return (
        `<g class="matrix-marker ${cls}${focused ? " marker-focused" : ""}" ` +
        `transform="translate(${toSvgX(p.friction)},${toSvgY(p.leverage)})" ` +
        `tabindex="0" role="img" data-owner="${escapeHtml(p.owner)}" data-name="${escapeHtml(p.name)}" ` +
        `data-key="${escapeHtml(key)}" aria-label="${escapeHtml(`${p.owner}/${p.name}`)}">${shape}</g>`
      );
    })
    .join("");

  return (
    `<svg class="matrix-svg" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet" role="group" ` +
    `aria-label="${escapeHtml(copy.matrixSvgAriaLabel(axisLabels.x, axisLabels.y))}">` +
    `<g class="matrix-washes">${washes}</g>` +
    `<line class="matrix-axis-line" x1="${QUADRANT_THRESHOLD}" y1="0" x2="${QUADRANT_THRESHOLD}" y2="100" />` +
    `<line class="matrix-axis-line" x1="0" y1="${QUADRANT_THRESHOLD}" x2="100" y2="${QUADRANT_THRESHOLD}" />` +
    `<g class="matrix-markers">${markers}</g>` +
    `</svg>`
  );
}

/**
 * The SVG overlay for JUST the focused/hovered marker, used above the SVG
 * budget where the bulk of the markers are canvas-only (documented switch,
 * module docstring). Same shape/colour encoding as `renderScatterSvg`'s
 * per-marker fragment.
 */
export function renderFocusOverlaySvg(point) {
  if (!point) return "";
  const cls = QUAD_CLASS[point.quadrant] || "quad-nursery";
  const r = DEFAULT_RADIUS + 0.8; // slightly larger so the focus ring is visible over the canvas layer
  return (
    `<svg class="matrix-svg matrix-focus-overlay" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet" aria-hidden="true">` +
    `<g class="matrix-marker ${cls} marker-focused" transform="translate(${toSvgX(point.friction)},${toSvgY(point.leverage)})">` +
    markerShapeSvg(point.quadrant, r) +
    `</g></svg>`
  );
}

/**
 * Canvas 2D path for the > SVG_MARKER_BUDGET case: one fill per marker, no
 * per-marker DOM node. `colors` maps quadrant name -> a resolved CSS colour
 * string (canvas cannot read `var(--x)`/`currentColor` itself — the caller
 * resolves the tokens once via `getComputedStyle` and passes plain colours).
 */
export function drawMatrixCanvas(ctx, points, { quadrantsHidden = {}, colors, width, height }) {
  ctx.clearRect(0, 0, width, height);
  const visible = points.filter((p) => !quadrantsHidden[p.quadrant]);
  const { min: starsMin, max: starsMax } = starsDomain(points);
  const sx = width / 100;
  const sy = height / 100;
  for (const p of visible) {
    const x = toSvgX(p.friction) * sx;
    const y = toSvgY(p.leverage) * sy;
    const r = radiusForStars(p.stars_audited, starsMin, starsMax) * Math.min(sx, sy);
    ctx.fillStyle = colors[p.quadrant] || colors.Nursery;
    ctx.beginPath();
    if (p.quadrant === "Sweet Spot") {
      ctx.moveTo(x, y - r);
      ctx.lineTo(x + r, y);
      ctx.lineTo(x, y + r);
      ctx.lineTo(x - r, y);
      ctx.closePath();
    } else if (p.quadrant === "Crucible") {
      for (let i = 0; i < 6; i++) {
        const deg = (i * 60 * Math.PI) / 180;
        const px = x + r * Math.sin(deg);
        const py = y - r * Math.cos(deg);
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      }
      ctx.closePath();
    } else if (p.quadrant === "Graveyard") {
      const s = r * 0.72;
      ctx.lineWidth = Math.max(1, r * 0.35);
      ctx.strokeStyle = ctx.fillStyle;
      ctx.moveTo(x - s, y - s);
      ctx.lineTo(x + s, y + s);
      ctx.moveTo(x - s, y + s);
      ctx.lineTo(x + s, y - s);
      ctx.stroke();
      continue;
    } else {
      ctx.arc(x, y, r, 0, Math.PI * 2);
    }
    ctx.fill();
  }
}
