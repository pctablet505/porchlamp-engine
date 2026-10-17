// components/patch.js — a tiny keyed-list DOM patcher. On every data
// change this diffs `items` against the container's current keyed
// children and does the minimum DOM surgery —
// add new nodes, remove stale ones, reorder via `insertBefore` (which
// MOVES an existing node rather than recreating it, preserving its
// listeners/focus/scroll-anchoring), and calls `updateItem` on every node
// that survives so IT decides which of ITS OWN cells actually changed.
//
// This module never touches `innerHTML` on the container itself — only
// `createItem` may build a new node's markup (via a detached template),
// and only for genuinely NEW keys.

/**
 * @param {Element} container the persistent parent (never replaced)
 * @param {any[]} items the new full ordered list of data items
 * @param {(item: any) => string} keyFn stable key per item
 * @param {(item: any) => Element} createItem builds a brand-new DOM node for an item not currently present
 * @param {(node: Element, item: any) => void} updateItem mutates an existing node to match `item` (only touching cells whose value changed is this function's job, not patch.js's)
 */
export function patchRows(container, items, keyFn, createItem, updateItem) {
  const existingByKey = new Map();
  for (const child of Array.from(container.children)) {
    const key = child.dataset ? child.dataset.key : child.getAttribute("data-key");
    if (key !== undefined) existingByKey.set(key, child);
  }

  let cursor = container.firstChild;
  for (const item of items) {
    const key = keyFn(item);
    let node = existingByKey.get(key);
    if (node) {
      updateItem(node, item);
      existingByKey.delete(key);
    } else {
      node = createItem(item);
      if (node.dataset) node.dataset.key = key;
      else node.setAttribute("data-key", key);
    }
    if (cursor === node) {
      cursor = cursor.nextSibling;
    } else {
      container.insertBefore(node, cursor);
      // `cursor` itself did not move; it stays the reference point for the
      // next iteration (insertBefore does not change `cursor`'s position).
    }
  }

  // Anything left in existingByKey was not in the new `items` list at all.
  for (const node of existingByKey.values()) {
    container.removeChild(node);
  }
}
