// Runs a list of requests against a built static site's api.js under Node,
// for scripts/verify_static_export.py.
//
//   node static_parity.mjs <site-dir> <requests.json> <out.json> [api-module]
//
// <requests.json> is [{fn, args}, ...] naming api.js exports; <out.json>
// receives one result per request: {ok: true, data} or {ok: false, status,
// detail}. `fetch` is replaced by a reader of the site's own files, so the
// module resolves its data exactly as a browser would (relative to its own
// URL) with no server in between.
import { createWriteStream } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const [siteDir, requestsPath, outPath, apiModule = "api.js"] = process.argv.slice(2);

globalThis.fetch = async (input) => {
  const url = new URL(typeof input === "string" ? input : input.url ?? String(input));
  if (url.protocol !== "file:") throw new TypeError(`static_parity: unexpected fetch of ${url.href}`);
  try {
    const body = await readFile(fileURLToPath(url));
    return new Response(body, { status: 200, headers: { "content-type": "application/json" } });
  } catch (err) {
    if (err && err.code === "ENOENT") return new Response("not found", { status: 404 });
    throw err;
  }
};

const api = await import(pathToFileURL(path.resolve(siteDir, apiModule)).href);
const requests = JSON.parse(await readFile(requestsPath, "utf8"));

// The results are written one element at a time. A single JSON.stringify of
// the whole array throws "RangeError: Invalid string length" once the answers
// pass V8's string limit (about 512 MiB): the 11,000-repo snapshot of 2026-10
// has 24,904 answers, about 1 GB. Each answer alone is far below the limit.
const out = createWriteStream(outPath, { encoding: "utf8" });
const failed = new Promise((_, reject) => out.once("error", reject));
const emit = (text) =>
  Promise.race([failed, new Promise((resolve) => (out.write(text) ? resolve() : out.once("drain", resolve)))]);
await emit("[");
let first = true;
for (const { fn, args } of requests) {
  if (typeof api[fn] !== "function") throw new Error(`static_parity: api has no export ${fn}`);
  const result = await api[fn](...args);
  const answer = result.ok
    ? { ok: true, data: result.data }
    : { ok: false, status: result.error.status, detail: result.error.detail ?? null };
  await emit((first ? "" : ",") + JSON.stringify(answer));
  first = false;
}
await emit("]");
await Promise.race([failed, new Promise((resolve) => out.end(resolve))]);
