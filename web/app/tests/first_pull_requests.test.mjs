// The first-pull-requests report's view, over a record the page's own Python wrote (cases/first_pull_requests.json).

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { OPENING_DAYS, answeredX, curveX, rangeAxis, renderFirstPullRequests, roundUp } from "../views/first_pull_requests.js";

const RECORD = JSON.parse(readFileSync(new URL("./cases/first_pull_requests.json", import.meta.url), "utf8"));
const copyOf = () => structuredClone(RECORD);
const hooks = (html) => [...html.matchAll(/<span data-v="([^"]*)">([^<]*)<\/span>/g)].map((m) => [m[1], m[2]]);
const unescape = (text) => text.replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">");

test("every value the view prints is a hook of the record's own string", () => {
  const html = renderFirstPullRequests(RECORD);
  const printed = hooks(html);
  assert.ok(printed.length > 100, "the page prints its numbers as hooks");
  for (const [id, text] of printed) {
    const [head, index, field] = id.split(".");
    const expected = field ? RECORD[head][Number(index)][field] : RECORD.values[id];
    assert.equal(unescape(text), String(expected), id);
  }
});

test("every value of the record is printed", () => {
  const printed = new Set(hooks(renderFirstPullRequests(RECORD)).map(([id]) => id));
  for (const id of Object.keys(RECORD.values)) assert.ok(printed.has(id), `${id} is in the record and not on the page`);
});

test("a sentence with a slot the record has no value for stops the page", () => {
  const rec = copyOf();
  rec.sentences.lede = "A sentence with {no_such_value}.";
  assert.throws(() => renderFirstPullRequests(rec), /no_such_value/);
});

test("the chart comes from the rows, so a changed row moves the chart", () => {
  const rec = copyOf();
  const before = renderFirstPullRequests(rec);
  rec.curve[0].returning = rec.curve[0].returning / 2;
  assert.notEqual(renderFirstPullRequests(rec), before);
});

test("the page opens on the two-week reading, with the slider hidden until the script shows it", () => {
  const html = renderFirstPullRequests(RECORD);
  const opening = RECORD.curve.findIndex((row) => row.days === OPENING_DAYS);
  assert.ok(opening > 0);
  assert.ok(html.includes(`value="${opening}"`));
  assert.ok(html.includes('data-fpr-slide="true" hidden'));
  assert.ok(html.includes(`<span data-v="curve.${opening}.first_text">${RECORD.curve[opening].first_text}</span>`));
  const rec = copyOf();
  rec.curve = rec.curve.filter((row) => row.days !== OPENING_DAYS);
  assert.throws(() => renderFirstPullRequests(rec), /no reading at 14 days/);
});

test("the slider's lists hold one entry per reading, in order", () => {
  const html = renderFirstPullRequests(RECORD);
  const attr = (name) => new RegExp(`data-${name}="([^"]*)"`).exec(html)[1].split("|");
  assert.deepEqual(attr("first"), RECORD.curve.map((row) => row.first_text));
  assert.deepEqual(attr("returning"), RECORD.curve.map((row) => row.returning_text));
  assert.equal(attr("x").length, RECORD.curve.length);
  assert.equal(attr("x")[0], "0.00%");
  assert.equal(attr("x").at(-1), "100.00%");
});

test("the axes: straight days, logarithmic hours, whole steps", () => {
  assert.equal(curveX(14, 60), 14 / 60);
  assert.equal(answeredX(1, 1, 2160), 0);
  assert.equal(answeredX(2160, 1, 2160), 1);
  assert.ok(Math.abs(answeredX(Math.sqrt(2160), 1, 2160) - 0.5) < 1e-9);
  assert.ok(Math.abs(roundUp(0.5826, 0.2) - 0.6) < 1e-9);
  assert.ok(Math.abs(roundUp(0.6, 0.2) - 0.6) < 1e-9, "a top already on a step stays");
});

test("the range axis holds zero and every range, in fives", () => {
  const axis = rangeAxis([{ low: 0.046, high: 0.14 }, { low: -0.022, high: 0.0165 }]);
  assert.deepEqual(axis.ticks, [-5, 0, 5, 10, 15]);
  assert.ok(Math.abs(axis.x(0) - 0.25) < 1e-9);
  assert.ok(Math.abs(axis.x(0.15) - 1) < 1e-9);
  assert.deepEqual(rangeAxis([{ low: 0.06, high: 0.09 }]).ticks, [0, 5, 10], "zero is always on the axis");
});

test("each named project links its own page; the page links the lookup and its data", () => {
  const html = renderFirstPullRequests(RECORD);
  assert.equal(RECORD.projects.length, 10);
  for (const project of RECORD.projects) {
    assert.ok(html.includes(`<a href="${project.path}">`), project.name);
    assert.equal(project.path, project.path.toLowerCase(), "project addresses are lower case");
  }
  assert.ok(html.includes('href="/lookup/"'));
  assert.ok(html.includes(`href="${RECORD.data_file}"`));
});

test("the words keep the house rules: no long dash, no employer, no external address, and every chart has a text alternative", () => {
  const html = renderFirstPullRequests(RECORD);
  assert.ok(!html.includes("—"), "no em dash");
  assert.ok(!/google|keras|rl alpha/i.test(html));
  assert.ok(!/<(img|iframe|script|link)\b/.test(html), "nothing to fetch");
  assert.ok(!/href="https?:/.test(html), "no link off the site");
  const charts = [...html.matchAll(/<figure class="fpr-chart" role="img" aria-label="([^"]*)"/g)];
  assert.equal(charts.length, 2);
  for (const chart of charts) assert.ok(chart[1].length > 20);
});

test("the black-holes note appears only when the record carries it", () => {
  assert.ok(renderFirstPullRequests(RECORD).includes("black-holes report"));
  const rec = copyOf();
  delete rec.sentences.black_holes_differ;
  assert.ok(!renderFirstPullRequests(rec).includes("black-holes report"));
});
