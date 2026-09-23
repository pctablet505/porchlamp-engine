// brand.js — the header's brand block: Porchlamp's symbol, name and slogan.
// The symbol is a porch lamp with its light on (a lamp left on tells a visitor
// they are expected); it is decorative -- the name beside it carries the
// meaning -- so it is hidden from assistive technology.
// web/v2/favicon.svg is the same drawing with fixed colours.
import * as copy from "../copy.js";
import { escapeHtml } from "./table.js";

export const BRAND_MARK_SVG =
  `<svg class="brand-mark" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" ` +
  `stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">` +
  `<rect x="2.2" y="13.2" width="2.4" height="8.4" rx="1" fill="currentColor" stroke="none"/>` +
  `<path d="M4.6 17.6H12.4V15.2"/>` +
  `<path d="M8.6 7.6L12.4 4.2L16.2 7.6Z" fill="currentColor"/>` +
  `<circle cx="12.4" cy="2.9" r="0.8" fill="currentColor" stroke="none"/>` +
  `<rect x="9.4" y="7.6" width="6" height="7.6" rx="0.9"/>` +
  `<rect class="brand-mark-glow" x="10.9" y="9.1" width="3" height="4.6" rx="1.5" stroke="none"/>` +
  `<path class="brand-mark-rays" d="M18.6 9.2L20.8 8.1M18.6 11.4H21.2M18.6 13.6L20.8 14.7"/>` +
  `</svg>`;

export function renderBrand() {
  return (
    `<span class="brand">${BRAND_MARK_SVG}${escapeHtml(copy.BRAND_NAME)}</span>` +
    `<span class="brand-tagline">${escapeHtml(copy.BRAND_TAGLINE)}</span>`
  );
}
