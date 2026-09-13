// fakedom.mjs — the minimal DOM surface components/table.js,
// components/patch.js and views/leaderboard.js need to run under plain
// Node (`node --test`, no jsdom, no npm dependency — this project takes
// none). NOT a general-purpose DOM: only what those three modules
// actually call is implemented, traced by hand against their source.
//
// Every mutating operation records `{el, prop}` onto `doc.mutationLog`
// ONLY when the new value differs from the current one — mirroring real
// DOM semantics for `classList.add/remove/toggle` (a no-op add/remove of
// an already-present/absent token fires no mutation) and matching what
// `tests/test_ui_perf.mjs` verified live in a real browser via
// MutationObserver + `takeRecords()` while building this packet (see
// .agents/W4-5a/handoff.md).

function kebabFromCamel(prop) {
  return prop.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`);
}

class FakeClassList {
  constructor(el) {
    this._el = el;
    this._set = new Set();
  }

  add(token) {
    if (this._set.has(token)) return;
    this._set.add(token);
    this._el._recordMutation("classList.add:" + token);
  }

  remove(token) {
    if (!this._set.has(token)) return;
    this._set.delete(token);
    this._el._recordMutation("classList.remove:" + token);
  }

  toggle(token, force) {
    const has = this._set.has(token);
    const want = force === undefined ? !has : !!force;
    if (want && !has) this.add(token);
    else if (!want && has) this.remove(token);
  }

  contains(token) {
    return this._set.has(token);
  }

  toString() {
    return [...this._set].join(" ");
  }
}

class FakeElement {
  constructor(doc, tagName) {
    this._doc = doc;
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.parentNode = null;
    this._attrs = new Map();
    this._innerHTML = "";
    this._style = {};
    this._hidden = false;
    this._tabIndex = -1;
    this._className = "";
    this.classList = new FakeClassList(this);

    const self = this;
    this.dataset = new Proxy(
      {},
      {
        get(_target, prop) {
          return self._attrs.get(`data-${kebabFromCamel(String(prop))}`);
        },
        set(_target, prop, value) {
          self.setAttribute(`data-${kebabFromCamel(String(prop))}`, value);
          return true;
        },
      }
    );

    this.style = new Proxy(
      {},
      {
        get(_target, prop) {
          return self._style[prop];
        },
        set(_target, prop, value) {
          if (self._style[prop] === value) return true;
          self._style[prop] = value;
          self._recordMutation(`style.${String(prop)}`);
          return true;
        },
      }
    );
  }

  _recordMutation(prop) {
    this._doc.mutationLog.push({ el: this, prop });
  }

  get innerHTML() {
    return this._innerHTML;
  }

  set innerHTML(value) {
    if (this._innerHTML === value) return;
    this._innerHTML = value;
    this._recordMutation("innerHTML");
  }

  get className() {
    return this._className;
  }

  set className(value) {
    if (this._className === value) return;
    this._className = value;
    this._recordMutation("className");
  }

  get tabIndex() {
    return this._tabIndex;
  }

  set tabIndex(value) {
    if (this._tabIndex === value) return;
    this._tabIndex = value;
    this._recordMutation("tabIndex");
  }

  get hidden() {
    return this._hidden;
  }

  set hidden(value) {
    if (this._hidden === !!value) return;
    this._hidden = !!value;
    this._recordMutation("hidden");
  }

  set colSpan(value) {
    this.setAttribute("colspan", String(value));
  }

  get colSpan() {
    return Number(this._attrs.get("colspan") || 0);
  }

  setAttribute(name, value) {
    const strValue = String(value);
    if (this._attrs.get(name) === strValue) return; // real DOM still notifies same-value sets, but every caller in this codebase guards beforehand — matching that lets the "exactly one <tr>" test reflect real usage.
    this._attrs.set(name, strValue);
    this._recordMutation(`attr:${name}`);
  }

  getAttribute(name) {
    return this._attrs.has(name) ? this._attrs.get(name) : null;
  }

  removeAttribute(name) {
    if (!this._attrs.has(name)) return;
    this._attrs.delete(name);
    this._recordMutation(`attr-removed:${name}`);
  }

  hasAttribute(name) {
    return this._attrs.has(name);
  }

  appendChild(node) {
    return this.insertBefore(node, null);
  }

  insertBefore(node, referenceNode) {
    if (node.parentNode) node.parentNode._removeChildSilently(node);
    if (referenceNode === null || referenceNode === undefined) {
      this.children.push(node);
    } else {
      const idx = this.children.indexOf(referenceNode);
      if (idx === -1) throw new Error("referenceNode not a child");
      this.children.splice(idx, 0, node);
    }
    node.parentNode = this;
    this._recordMutation("childList:insert");
    return node;
  }

  removeChild(node) {
    this._removeChildSilently(node);
    this._recordMutation("childList:remove");
    return node;
  }

  _removeChildSilently(node) {
    const idx = this.children.indexOf(node);
    if (idx !== -1) this.children.splice(idx, 1);
    node.parentNode = null;
  }

  get firstChild() {
    return this.children[0] || null;
  }

  get nextSibling() {
    if (!this.parentNode) return null;
    const idx = this.parentNode.children.indexOf(this);
    return this.parentNode.children[idx + 1] || null;
  }

  querySelector() {
    throw new Error("fakedom: querySelector not implemented (not used by the modules under test)");
  }
}

export class FakeDocument {
  constructor() {
    this.mutationLog = [];
  }

  createElement(tag) {
    return new FakeElement(this, tag);
  }
}

/** Walks up from a mutated element to its nearest `<tr>` ancestor (or itself, if it IS a `<tr>`). */
export function trAncestor(el) {
  let node = el;
  while (node && node.tagName !== "TR") node = node.parentNode;
  return node;
}

/** The set of distinct `<tr>` elements touched by the mutations recorded since `fromIndex`. */
export function trsTouchedSince(doc, fromIndex) {
  const trs = new Set();
  for (const record of doc.mutationLog.slice(fromIndex)) {
    const tr = trAncestor(record.el);
    if (tr) trs.add(tr);
  }
  return trs;
}
