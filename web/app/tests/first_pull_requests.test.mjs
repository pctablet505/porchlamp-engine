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

test("every list item opens with a bold lead, and one without a lead stops the page", () => {
  const html = renderFirstPullRequests(RECORD);
  const leads = [...html.matchAll(/<li><b>([^<]*)<\/b> (.)/g)];
  assert.equal(leads.length, RECORD.sentences.method.length + RECORD.sentences.not_covered.length);
  for (const [, lead, next] of leads) {
    assert.match(lead, /^[A-Z].*\.$/, "the lead is a whole sentence");
    assert.match(next, /[A-Z0-9<]/, "the next sentence keeps its first letter");
  }
  const rec = copyOf();
  rec.sentences.not_covered[1] = "One sentence with no second one.";
  assert.throws(() => renderFirstPullRequests(rec), /lead sentence/);
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
  assert.ok(RECORD.projects.length >= 5);
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

test("the page leads with who answers, and the old headline is gone", () => {
  const html = renderFirstPullRequests(RECORD);
  const order = ["who-answers", "newcomers-do-best", "waiting", "asking", "first-one", "weekends", "where-to-start", "all-numbers"];
  const places = order.map((id) => html.indexOf(`id="${id}"`));
  assert.ok(places.every((at) => at > 0));
  assert.deepEqual(places, [...places].sort((a, b) => a - b));
  assert.ok(!/1 in \d/.test(html), "no 1-in-N line on the page");
});

test("the first thing under the first heading is the chart, then its text; the tiles follow that section", () => {
  const html = renderFirstPullRequests(RECORD);
  const heading = html.indexOf('id="who-answers-heading"');
  const chart = html.indexOf("fpr-cols-hist");
  const text = html.indexOf("Count who gives the first answer");
  const tiles = html.indexOf('class="fpr-tiles"');
  const next = html.indexOf('id="newcomers-do-best"');
  assert.ok(heading > 0 && heading < chart && chart < text && text < tiles && tiles < next, JSON.stringify({ heading, chart, text, tiles, next }));
});

test("the tile bars are gone: a bar beside its own number says nothing more", () => {
  assert.ok(!renderFirstPullRequests(RECORD).includes("fpr-tile-bar"));
});

test("the first tile doesn't repeat the 50,000-star figure", () => {
  const html = renderFirstPullRequests(RECORD);
  const tile = html.slice(html.indexOf('class="fpr-tiles"'));
  assert.ok(!tile.slice(0, tile.indexOf("</div>")).includes("large_over_half"));
});

test("each of the three charts with no title of its own in the sentences has one before it", () => {
  const html = renderFirstPullRequests(RECORD);
  for (const [mark, title] of [["fpr-cols-hist", "Projects, by the busiest person"], ["data-fpr-curve", "Share merged in the end, by days of silence"], ['aria-label="Line chart of the share answered', "Share answered by each point in time"]]) {
    const at = html.indexOf(mark);
    assert.ok(at > 0, mark);
    const before = html.slice(0, at);
    const h3 = before.lastIndexOf('<h3 class="fpr-h3">');
    assert.ok(h3 > before.lastIndexOf("</section>"), `no title before ${mark}`);
    assert.ok(unescape(before.slice(h3)).includes(title), `${mark}: ${before.slice(h3, h3 + 80)}`);
  }
});

test("the readout says merged, and the slider speaks its reading", () => {
  const html = renderFirstPullRequests(RECORD);
  assert.ok(html.includes("of first pull requests merged"));
  assert.ok(html.includes("of returning merged"));
  const opening = RECORD.curve.findIndex((r) => r.days === OPENING_DAYS);
  const spoken = /aria-valuetext="([^"]*)"/.exec(html)[1];
  assert.ok(spoken.includes(RECORD.curve[opening].first_text) && spoken.includes(RECORD.curve[opening].returning_text), spoken);
  const said = /data-said="([^"]*)"/.exec(html)[1].split("|");
  assert.equal(said.length, RECORD.curve.length);
  RECORD.curve.forEach((row, i) => assert.ok(said[i].includes(row.first_text) && said[i].includes(row.label), said[i]));
});

test("no tip, slider stop or table row says From opening", () => {
  assert.ok(!renderFirstPullRequests(RECORD).includes("From opening"));
});

test("the axes print the labels that fit: no 2 mo beside 3 mo, a bare 0 on the waiting curve", () => {
  const html = renderFirstPullRequests(RECORD);
  const ticks = [...html.matchAll(/<span class="fpr-tick-x[^"]*"[^>]*>([^<]*)<\/span>/g)].map((m) => m[1]);
  assert.ok(ticks.includes("0"));
  assert.ok(!ticks.includes("day 0"));
  assert.ok(ticks.includes("3 days"));
  assert.ok(!ticks.includes("3 d"));
  assert.ok(!ticks.includes("2 mo"));
  assert.ok(ticks.includes("3 mo"));
  const dots = [...html.matchAll(/data-tip="([^"]*)"/g)].map((m) => unescape(m[1]));
  assert.ok(dots.some((tip) => tip.startsWith("2 mo,")), "the dot at two months stays");
});

test("the lines are named on the chart and the legend is gone", () => {
  const html = renderFirstPullRequests(RECORD);
  assert.ok(!html.includes("fpr-legend"));
  const names = [...html.matchAll(/<span class="fpr-line-label[^"]*"[^>]*>([^<]*)<\/span>/g)].map((m) => m[1]);
  assert.deepEqual(names, ["Returning", "First pull request", "Returning", "First pull request"]);
});

test("the answered curve's points are in a table, and the histogram's text alternative lists every band", () => {
  const html = renderFirstPullRequests(RECORD);
  for (const [i, row] of RECORD.answered.entries()) {
    assert.ok(html.includes(`<span data-v="answered.${i}.first_text">${row.first_text}</span>`), `answered row ${i}`);
    assert.ok(html.includes(`<span data-v="answered.${i}.returning_text">${row.returning_text}</span>`), `answered row ${i}`);
  }
  assert.ok(html.includes("Every point is in the table further down.</") || /Line chart of the share answered[^"]*Every point is in the table further down\./.test(html));
  const label = unescape(/class="fpr-cols fpr-cols-hist"[^>]*aria-label="([^"]*)"/.exec(html)[1]);
  assert.ok(/class="fpr-cols fpr-cols-hist"[^>]*role="img"/.test(html));
  for (const band of RECORD.histogram) assert.ok(label.includes(`${band.label}: ${band.projects_text} projects`), band.label);
});

test("a table says how many number columns it has, so a phone can stack its rows", () => {
  const html = renderFirstPullRequests(RECORD);
  assert.deepEqual([...html.matchAll(/<table class="fpr-table" style="--nums:(\d)"/g)].map((m) => m[1]), ["4", "4", "2"]);
});

test("the two band charts share one scale and mark only the top band", () => {
  const html = renderFirstPullRequests(RECORD);
  const heights = (name) => [...html.matchAll(new RegExp(`<span class="fpr-col-value">[^]*?data-v="who_bands\\.(\\d)\\.${name}_text"[^]*?<span class="fpr-col-bar( fpr-col-bar-off)?" style="--f:([0-9.]+)"`, "g"))].map((m) => [Number(m[1]), m[2] === undefined, Number(m[3])]);
  const answered = heights("answered");
  const merged = heights("merged");
  assert.equal(answered.length, RECORD.who_bands.length);
  assert.equal(merged.length, RECORD.who_bands.length);
  assert.deepEqual(answered.map(([, on]) => on), RECORD.who_bands.map((b) => b.top));
  const scale = answered[3][2] / RECORD.who_bands[3].answered;
  assert.ok(Math.abs(merged[3][2] / RECORD.who_bands[3].merged - scale) < 1e-2, "one scale for both charts");
  assert.ok(answered[3][2] <= 1 && answered[3][2] > 0.5);
});
