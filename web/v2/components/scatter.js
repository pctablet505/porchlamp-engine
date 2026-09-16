// components/scatter.js — Screen B's inline SVG (and, above the marker
// budget, canvas) scatter: friction (x) vs leverage (y), 06 §3 B.
//
// Encoding (colour-blind safe, spec §5.1 / U-3's "shape+colour" rule,
// never colour alone):
//   Sweet Spot -> diamond, Crucible -> hexagon, Nursery -> circle,
//   Graveyard -> cross ("x"). Colour from the SAME --quad-* tokens the
//   leaderboard's quadrant glyph already uses (styles/tokens.css).
// Marker SIZE = `stars_audited` (sqrt-scaled area, so the visual size
// difference reflects the actual magnitude ratio), radius 4-12 real
// screen pixels (`MIN_RADIUS_PX`/`MAX_RADIUS_PX`) regardless of the
// plot's rendered width — a `null` value (no v2 producer sends
// `stars_audited` yet) draws at
// `DEFAULT_RADIUS_PX` (6px) and is counted for the "unaudited" legend
// note (`views/matrix.js`'s `MATRIX_UNAUDITED_STARS_NOTE`, which also
// documents this 4-12px scale itself).
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

// Marker RADIUS is 4-12 screen pixels on a sqrt scale of
// `stars_audited`, `null` -> 6px, REGARDLESS of the plot's rendered
// width. These three are real CSS pixels, documented in the legend
// (views/matrix.js's MATRIX_UNAUDITED_STARS_NOTE neighbour).
export const MIN_RADIUS_PX = 4;
export const MAX_RADIUS_PX = 12;
export const DEFAULT_RADIUS_PX = 6;

//: `.matrix-stage`'s own CSS `max-width` (styles/views.css) -- the
//: WORST-CASE (largest) width the SVG viewBox ever actually scales up to;
//: `radiusForStars` converts a target pixel radius into viewBox (domain)
//: units against this fixed reference, so at this width (or any narrower
//: one -- the stage never renders WIDER) the on-screen radius is exactly
//: the intended 4-12px, never more. A narrower viewport renders every
//: marker proportionally SMALLER, which still satisfies the "<=12px at
//: any plot width" requirement (it is a ceiling, not an exact target).
export const REFERENCE_PLOT_WIDTH_PX = 720;

/** Backward-compatible viewBox-unit constants (SVG path only) — kept for callers/tests that still want the domain-unit equivalents at the reference width. */
export const DEFAULT_RADIUS = (DEFAULT_RADIUS_PX / REFERENCE_PLOT_WIDTH_PX) * (DOMAIN_MAX - DOMAIN_MIN);

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
 * Sqrt-scaled radius, in real CSS PIXELS (`MIN_RADIUS_PX`..`MAX_RADIUS_PX`,
 * `DEFAULT_RADIUS_PX` for a null/undefined `starsAudited`), so a marker's
 * AREA (not its diameter) is proportional to `starsAudited` — the
 * perceptually correct scale for a size encoding. This is the single
 * source of truth for the mapping; both rendering paths below derive
 * their own coordinate system's radius from it.
 */
export function radiusPxForStars(starsAudited, domainMin, domainMax) {
  if (starsAudited === null || starsAudited === undefined) return DEFAULT_RADIUS_PX;
  if (!(domainMax > domainMin)) return DEFAULT_RADIUS_PX;
  const t = Math.sqrt(Math.max(0, starsAudited - domainMin) / (domainMax - domainMin));
  return MIN_RADIUS_PX + t * (MAX_RADIUS_PX - MIN_RADIUS_PX);
}

/**
 * `radiusPxForStars`, converted to SVG VIEWBOX (domain) units against
 * `REFERENCE_PLOT_WIDTH_PX` — the value `renderScatterSvg`'s markers use,
 * since their coordinate system is the 0..100 domain, not screen pixels.
 * The canvas path (`drawMatrixCanvas`) uses `radiusPxForStars` directly
 * instead (its coordinates are already real pixels) — see that function's
 * own docstring for why it does NOT go through this conversion.
 */
export function radiusForStars(starsAudited, domainMin, domainMax) {
  const px = radiusPxForStars(starsAudited, domainMin, domainMax);
  return (px / REFERENCE_PLOT_WIDTH_PX) * (DOMAIN_MAX - DOMAIN_MIN);
}

export function starsDomain(points) {
  let min = Infinity;
  let max = -Infinity;
  let found = false;
  for (const p of points) {
    const v = p.stars_audited;
    if (v !== null && v !== undefined && Number.isFinite(v)) {
      if (v < min) min = v;
      if (v > max) max = v;
      found = true;
    }
  }
  if (!found) return { min: 0, max: 0 };
  return { min, max };
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

  const ticks = [25, 50, 75];
  const gridLines = ticks
    .map((t) => {
      const y = toSvgY(t);
      const x = toSvgX(t);
      return (
        `<line class="matrix-grid-line" x1="0" y1="${y}" x2="100" y2="${y}" />` +
        `<line class="matrix-grid-line" x1="${x}" y1="0" x2="${x}" y2="100" />` +
        `<text class="matrix-scale-tick" x="1.5" y="${y - 0.8}">${t}</text>` +
        `<text class="matrix-scale-tick" x="${x + 0.8}" y="98.5">${t}</text>`
      );
    })
    .join("");

  return (
    `<svg class="matrix-svg" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet" role="group" ` +
    `aria-label="${escapeHtml(copy.matrixSvgAriaLabel(axisLabels.x, axisLabels.y))}">` +
    `<g class="matrix-washes">${washes}</g>` +
    `<g class="matrix-grid">${gridLines}</g>` +
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
export function renderFocusOverlaySvg(point, domainMin = 0, domainMax = 0) {
  if (!point) return "";
  const cls = QUAD_CLASS[point.quadrant] || "quad-nursery";
  // `domainMin`/`domainMax` (the caller's `starsDomain(points)`) make the
  // overlay match the REAL marker's own stars-scaled radius, with a small
  // constant bump so the focus ring stays visible over the canvas layer
  // regardless of size — the highlighted marker never changes size on
  // focus/hover.
  const r = radiusForStars(point.stars_audited, domainMin, domainMax) + 0.8;
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
 * Radius uses `radiusPxForStars` DIRECTLY (real pixels, never multiplied
 * by `sx`/`sy`) — canvas coordinates here are already real pixels, so
 * going through the domain-unit `radiusForStars` and re-scaling by the
 * canvas's OWN width/height would let a canvas wider than
 * `REFERENCE_PLOT_WIDTH_PX` exceed the 4-12px guarantee; this way the cap
 * holds regardless of canvas size.
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
    const r = radiusPxForStars(p.stars_audited, starsMin, starsMax);
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
