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
import { readFile, writeFile } from "node:fs/promises";
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
const results = [];
for (const { fn, args } of requests) {
  if (typeof api[fn] !== "function") throw new Error(`static_parity: api has no export ${fn}`);
  const result = await api[fn](...args);
  results.push(
    result.ok
      ? { ok: true, data: result.data }
      : { ok: false, status: result.error.status, detail: result.error.detail ?? null },
  );
}
await writeFile(outPath, JSON.stringify(results));
