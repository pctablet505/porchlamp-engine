// The lookup's page script, with the browser stood in for: nothing leaves before the button, and what comes back goes through the view.

import assert from "node:assert/strict";
import { test } from "node:test";

import { LIVE_API_BASE } from "../livecheck.js";

let loads = 0;

/** Loads a fresh copy of lookup-page.js on a stand-in page at `search`; `answer(url)` is the network. */
async function open(search, answer = async () => new Response("{}", { status: 404 })) {
  const listeners = {};
  const input = { value: "" };
  const form = { elements: { login: input }, addEventListener: (kind, fn) => { listeners[kind] = fn; } };
  const result = { innerHTML: "" };
  const calls = [];
  const replaced = [];
  globalThis.document = { querySelector: (selector) => (selector === "[data-lookup-form]" ? form : null), getElementById: (id) => (id === "lookup-result" ? result : null) };
  globalThis.location = { search, pathname: "/lookup/" };
  globalThis.history = { replaceState: (_state, _title, url) => replaced.push(url) };
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    return answer(String(url));
  };
  loads += 1;
  await import(`../lookup-page.js?load=${loads}`);
  return {
    input, result, calls, replaced,
    press: () => listeners.submit({ preventDefault() {} }),
  };
}

const json = (body) => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
const github = (url) => url.startsWith(LIVE_API_BASE);

test("opening the page sends nothing anywhere, whatever the address says", async () => {
  for (const search of ["", "?user=ann"]) {
    const page = await open(search);
    assert.deepEqual(page.calls, []);
  }
});

test("the address fills the box and a name that isn't one does not", async () => {
  assert.equal((await open("?user=ann")).input.value, "ann");
  assert.equal((await open("?user=<script>")).input.value, "");
  assert.equal((await open("?user=a/b")).input.value, "");
});

test("a bad name pressed through the button reaches no GitHub address and says so", async () => {
  const page = await open("");
  page.input.value = "a/b";
  await page.press();
  assert.equal(page.calls.filter(github).length, 0);
  assert.ok(page.result.innerHTML.includes("error-banner"));
  assert.deepEqual(page.replaced, []);
});

test("what GitHub says goes through the view, escaped, and the address remembers the name", async () => {
  const item = {
    number: 7, title: "<img src=x onerror=alert(1)>", html_url: "https://github.com/acme/widget/pull/7", state: "open", draft: false,
    created_at: "2026-10-01T00:00:00Z", closed_at: null, author_association: "NONE", user: { login: "ann", type: "User" },
    repository_url: `${LIVE_API_BASE}/repos/acme/widget`, pull_request: { merged_at: null },
  };
  const page = await open("", async (url) => {
    if (url.includes("/rate_limit")) return json({ resources: { core: { remaining: 50 }, search: { remaining: 5 } } });
    if (url.includes("/search/issues")) return json({ items: [item] });
    if (url.includes("/timeline")) return json([]);
    return new Response("{}", { status: 404 });
  });
  page.input.value = "ann";
  await page.press();
  assert.ok(page.result.innerHTML.includes("&lt;img src=x"));
  assert.ok(!page.result.innerHTML.includes("<img"));
  assert.ok(page.calls.some((url) => url.includes("/search/issues")));
  assert.deepEqual(page.replaced, ["/lookup/?user=ann"]);
});
