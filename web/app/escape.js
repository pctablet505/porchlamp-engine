// escape.js — the one escape for text the app writes into HTML. Every module that draws markup imports it, so a
// value from the data (a project's name, an evidence source, an address) shows as text and is never read as markup.

/** `value` as HTML text, safe in an element or a quoted attribute: `&`, `<`, `>`, `"` and `'` as entities. */
export function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}
