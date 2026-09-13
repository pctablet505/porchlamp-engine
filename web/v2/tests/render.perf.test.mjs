// tests/render.perf.test.mjs — Node test for the lead's 2026-09-13
// UX-rework ruling, docs/v2/06-UX-DESIGN.md §6.1 U-12/U-13/U-19.
//
// NOT EXECUTED ON THIS BOX: `node` is not installed here (see
// .agents/W4-5a/handoff.md "Node availability"). This file is written to
// run cleanly under `node --test` (Node >= 18, stdlib `node:test` +
// `node:assert`, zero npm dependencies) the moment Node is available, and
// its assertions mirror EXACTLY what was verified live in a real browser
// (Chromium via the Claude Browser tool) while building this packet — see
// the handoff for those real, browser-measured numbers, which are the
// actual evidence behind this packet's acceptance claim, not this file's
// (unexecuted) output.
//
// Run: node --test web/v2/tests/render.perf.test.mjs

import test from "node:test";
import assert from "node:assert/strict";

import { FakeDocument, trsTouchedSince } from "./fakedom.mjs";
import { patchRows } from "../components/patch.js";
import {
  createRowElement,
  updateRowElement,
  createSpacerElement,
  updateSpacerElement,
  computeVisibleRange,
  columnCount,
  rowKey,
  ROW_HEIGHT_PX,
} from "../components/table.js";
import { synthesizeRows } from "../perf-fixtures.js";

const FRAME_BUDGET_MS = 16;
const FRAME_BUDGET_MARGIN = 3; // U-19: "3x margin, numbers reported"

function patchWindow(doc, tbody, rows, range, opts) {
  const colCount = columnCount(opts.showPersonaDelta);
  const topHeight = range.start * ROW_HEIGHT_PX;
  const bottomHeight = Math.max(0, rows.length - range.end) * ROW_HEIGHT_PX;
  const windowed = [{ kind: "spacer-top", height: topHeight }];
  for (let i = range.start; i < range.end; i++) windowed.push({ kind: "row", row: rows[i], index: i });
  windowed.push({ kind: "spacer-bottom", height: bottomHeight });

  patchRows(
    tbody,
    windowed,
    (item) => (item.kind === "row" ? rowKey(item.row) : `__${item.kind}__`),
    (item) =>
      item.kind === "row"
        ? createRowElement(doc, item.row, {
            previousRow: item.index > 0 ? rows[item.index - 1] : null,
            showPersonaDelta: opts.showPersonaDelta,
          })
        : createSpacerElement(doc, `__${item.kind}__`, colCount),
    (node, item) => {
      if (item.kind !== "row") {
        updateSpacerElement(node, item.height);
        return;
      }
      updateRowElement(node, item.row, {
        previousRow: item.index > 0 ? rows[item.index - 1] : null,
        showPersonaDelta: opts.showPersonaDelta,
        selected: opts.selectedRepoKey === rowKey(item.row),
      });
    }
  );
}

function visibleRanksIn(tbody) {
  return tbody.children
    .filter((tr) => !tr.hasAttribute("aria-hidden"))
    .map((tr) => Number(tr.children[0].innerHTML));
}

test("U-13/U-19: 1,000 synthetic rows (the max page size) — initial windowed patch stays inside budget", () => {
  const rows = synthesizeRows(1000);
  const doc = new FakeDocument();
  const table = doc.createElement("table");
  const tbody = doc.createElement("tbody");
  table.appendChild(tbody);

  const range = computeVisibleRange(0, 600, rows.length);
  const t0 = performance.now();
  patchWindow(doc, tbody, rows, range, { showPersonaDelta: false, selectedRepoKey: null });
  const elapsed = performance.now() - t0;

  const rendered = tbody.children.filter((tr) => !tr.hasAttribute("aria-hidden"));
  assert.ok(rendered.length > 0 && rendered.length < 100, `windowed slice should be small, got ${rendered.length}`);
  assert.ok(
    elapsed < FRAME_BUDGET_MS * FRAME_BUDGET_MARGIN,
    `initial patch of 1,000 rows took ${elapsed.toFixed(2)}ms, budget ${FRAME_BUDGET_MS}ms x${FRAME_BUDGET_MARGIN} margin = ${FRAME_BUDGET_MS * FRAME_BUDGET_MARGIN}ms`
  );
  console.log(`[measured] initial windowed patch of 1,000 rows: ${elapsed.toFixed(2)}ms`);
});

test("U-12/U-13: 60 simulated scroll steps — no patch exceeds the frame budget, container is never rebuilt, the rendered slice advances", () => {
  const rows = synthesizeRows(1000);
  const doc = new FakeDocument();
  const table = doc.createElement("table");
  const tbody = doc.createElement("tbody");
  table.appendChild(tbody);

  let scrollTop = 0;
  const viewportHeight = 600;
  const frameTimes = [];
  let previousFirstRank = -1;
  let advanced = 0;

  patchWindow(doc, tbody, rows, computeVisibleRange(scrollTop, viewportHeight, rows.length), {
    showPersonaDelta: false,
    selectedRepoKey: null,
  });

  for (let step = 0; step < 60; step++) {
    scrollTop += ROW_HEIGHT_PX; // one row per step, matching the browser reproduction of the reported defect
    const range = computeVisibleRange(scrollTop, viewportHeight, rows.length);

    // U-12: the container itself is never replaced — the same tbody/table
    // nodes persist for the whole simulated scroll (this is the literal
    // fix for "each frame recreated the container and reset scrollTop").
    assert.equal(tbody.parentNode, table, `tbody was detached from its table at step ${step}`);
    assert.ok(table.children.includes(tbody), `tbody was removed from table.children at step ${step}`);

    const t0 = performance.now();
    patchWindow(doc, tbody, rows, range, { showPersonaDelta: false, selectedRepoKey: null });
    frameTimes.push(performance.now() - t0);

    const ranks = visibleRanksIn(tbody);
    const firstRank = Math.min(...ranks);
    if (firstRank > previousFirstRank) advanced++;
    previousFirstRank = firstRank;
  }

  const maxFrame = Math.max(...frameTimes);
  const avgFrame = frameTimes.reduce((a, b) => a + b, 0) / frameTimes.length;
  console.log(
    `[measured] 60 scroll steps: max=${maxFrame.toFixed(2)}ms avg=${avgFrame.toFixed(2)}ms ` +
      `(budget ${FRAME_BUDGET_MS}ms, 3x margin ${FRAME_BUDGET_MS * FRAME_BUDGET_MARGIN}ms)`
  );

  assert.ok(
    maxFrame < FRAME_BUDGET_MS * FRAME_BUDGET_MARGIN,
    `a scroll-step patch took ${maxFrame.toFixed(2)}ms, over the ${FRAME_BUDGET_MS * FRAME_BUDGET_MARGIN}ms (3x) margin`
  );
  // The rendered slice must actually have moved on most steps (not every
  // single step necessarily changes the integer first-rank at a 1-row
  // scroll delta with an 8-row buffer, but it must not be STUCK — this is
  // the literal "the rendered slice advances" requirement from the
  // coordinator's addendum).
  assert.ok(advanced >= 50, `expected the visible slice to advance on most of 60 steps, only did on ${advanced}`);
});

test("U-19: a one-row data change patches exactly one <tr>", () => {
  const rows = synthesizeRows(1000);
  const doc = new FakeDocument();
  const table = doc.createElement("table");
  const tbody = doc.createElement("tbody");
  table.appendChild(tbody);
  const range = computeVisibleRange(2400, 600, rows.length); // scrolled well past the first page, matching the live-browser check

  patchWindow(doc, tbody, rows, range, { showPersonaDelta: false, selectedRepoKey: null });

  const targetIndex = Math.floor((range.start + range.end) / 2);
  const changedRows = rows.slice();
  changedRows[targetIndex] = { ...changedRows[targetIndex], cvi: changedRows[targetIndex].cvi + 1 };

  const beforeLen = doc.mutationLog.length;
  patchWindow(doc, tbody, changedRows, range, { showPersonaDelta: false, selectedRepoKey: null });
  const touched = trsTouchedSince(doc, beforeLen);

  assert.equal(touched.size, 1, `expected exactly one <tr> touched, got ${touched.size}`);
  const [tr] = touched;
  assert.equal(tr.dataset.owner, changedRows[targetIndex].owner);
  assert.equal(tr.dataset.name, changedRows[targetIndex].name);
});

test("patchRows: add, remove, reorder and no-op are each the minimum DOM surgery", () => {
  const doc = new FakeDocument();
  const container = doc.createElement("tbody");
  const keyFn = (item) => item.key;
  const create = (item) => {
    const el = doc.createElement("tr");
    el.dataset.key = item.key;
    el.innerHTML = item.value;
    return el;
  };
  const update = (el, item) => {
    if (el.innerHTML !== item.value) el.innerHTML = item.value;
  };

  patchRows(container, [{ key: "a", value: "1" }, { key: "b", value: "2" }, { key: "c", value: "3" }], keyFn, create, update);
  assert.deepEqual(container.children.map((c) => c.dataset.key), ["a", "b", "c"]);
  const nodeA = container.children[0];
  const nodeB = container.children[1];
  const nodeC = container.children[2];

  // No-op re-patch with identical data must touch nothing.
  const before = doc.mutationLog.length;
  patchRows(container, [{ key: "a", value: "1" }, { key: "b", value: "2" }, { key: "c", value: "3" }], keyFn, create, update);
  assert.equal(doc.mutationLog.length, before, "an unchanged patch must not mutate the DOM at all");
  assert.equal(container.children[0], nodeA, "unchanged node identity must survive a no-op patch");

  // Reorder: b moves to the front. Nodes are MOVED (same identity), not recreated.
  patchRows(container, [{ key: "b", value: "2" }, { key: "a", value: "1" }, { key: "c", value: "3" }], keyFn, create, update);
  assert.deepEqual(container.children.map((c) => c.dataset.key), ["b", "a", "c"]);
  assert.equal(container.children[0], nodeB);
  assert.equal(container.children[1], nodeA);
  assert.equal(container.children[2], nodeC);

  // Remove "a", add "d".
  patchRows(container, [{ key: "b", value: "2" }, { key: "d", value: "4" }, { key: "c", value: "3" }], keyFn, create, update);
  assert.deepEqual(container.children.map((c) => c.dataset.key), ["b", "d", "c"]);
  assert.equal(container.children[0], nodeB, "b's node identity must survive across the patch that removed a and added d");
  assert.equal(container.children[2], nodeC, "c's node identity must survive too");

  // Update: b's value changes in place, no other node touched.
  const beforeUpdate = doc.mutationLog.length;
  patchRows(container, [{ key: "b", value: "22" }, { key: "d", value: "4" }, { key: "c", value: "3" }], keyFn, create, update);
  const touched = trsTouchedSince(doc, beforeUpdate);
  assert.equal(touched.size, 1);
  assert.equal([...touched][0].dataset.key, "b");
});
