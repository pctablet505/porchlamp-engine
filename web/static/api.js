// api.js, static build — the same exports as web/v2/api.js, answered from
// the JSON files scripts/export_static.py wrote under data/<DATA_VERSION>/
// instead of the v2 API. scripts/export_static.py copies this file over
// web/v2/api.js in the built site; nothing else in the UI changes.
//
// Every answer has the shape and the values the API gives for the same
// request (scripts/verify_static_export.py checks that against the API,
// request by request). The exported files ARE API responses; what this
// module adds is only what a static host cannot precompute:
//   - /leaderboard: ecosystem + search filtering and paging over the
//     persona's full ranked list, in the order the API returns for `sort`
//     (exported as index lists: the ORDER BY is never re-implemented here);
//   - /not-ranked: the same filtering, then the endpoint's own truncation
//     to its first `max_rows` rows and split into groups;
//   - /reproject (custom weights): the one real computation — a port of
//     porchlamp.store.queries.reproject's SQL over the stored pillar columns.

import { DATA_VERSION } from "./data-version.js";

const DEFAULT_TIMEOUT_MS = 5000;
/** A data file is fetched once and cached; the first fetch of a large one can exceed the API's 5 s budget on a slow link. */
const DATA_TIMEOUT_MS = 30000;
const DATA_ROOT = new URL(`./data/${DATA_VERSION}/`, import.meta.url);
const BUCKETS = 256;

export class ApiError extends Error {
  constructor(endpoint, status, message, detail) {
    super(message || `request to ${endpoint} failed with status ${status}`);
    this.name = "ApiError";
    this.endpoint = endpoint;
    // 0: network-level failure (no response at all).
    // "timeout": exceeded the time budget with no response.
    this.status = status;
    this.detail = detail;
  }
}

export { DEFAULT_TIMEOUT_MS };

// ------------------------------------------------------------ data files
const cache = new Map();

/** A data file failed to load: carries what the UI's error banner shows. */
class DataFileError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

function loadJson(relativePath) {
  if (!cache.has(relativePath)) {
    const url = new URL(relativePath, DATA_ROOT);
    const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    const timer = controller ? setTimeout(() => controller.abort(), DATA_TIMEOUT_MS) : null;
    const pending = fetch(url, { headers: { Accept: "application/json" }, signal: controller ? controller.signal : undefined })
      .then(
        async (response) => {
          if (!response.ok) {
            throw new DataFileError(response.status, `static data file ${relativePath} returned HTTP ${response.status}`);
          }
          return response.json();
        },
        (networkError) => {
          const timedOut = networkError && networkError.name === "AbortError";
          throw new DataFileError(timedOut ? "timeout" : 0, `static data file ${relativePath}: ${networkError.message}`);
        },
      )
      .finally(() => {
        if (timer) clearTimeout(timer);
      });
    // A failed load is not cached: the UI's Retry must fetch again.
    pending.catch(() => cache.delete(relativePath));
    cache.set(relativePath, pending);
  }
  return cache.get(relativePath);
}

/** Runs `compute` and wraps the outcome in api.js's `{ ok, data }` / `{ ok: false, error }` contract. */
async function answer(endpoint, compute) {
  try {
    return { ok: true, data: await compute() };
  } catch (err) {
    if (err instanceof ApiError) return { ok: false, error: err };
    if (err instanceof DataFileError) return { ok: false, error: new ApiError(endpoint, err.status, err.detail, err.detail) };
    const detail = `static site error: ${err && err.message ? err.message : String(err)}`;
    return { ok: false, error: new ApiError(endpoint, 500, detail, detail) };
  }
}

function unprocessable(endpoint, detail) {
  return new ApiError(endpoint, 422, detail, detail);
}

// --------------------------------------------------------------- params
/** api.js's toQueryString drops these, so the API sees the parameter as absent. */
function present(value) {
  return value !== undefined && value !== null && value !== "";
}

function toQueryString(params) {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (!present(value)) continue;
    qs.set(key, String(value));
  }
  const s = qs.toString();
  return s ? `?${s}` : "";
}

/** Python's repr() of a str, for error details that quote the caller's value. */
function pyRepr(value) {
  const s = String(value);
  return s.includes("'") && !s.includes('"') ? `"${s}"` : `'${s.replace(/\\/g, "\\\\").replace(/'/g, "\\'")}'`;
}

/** FastAPI's own `int` query parsing: an integer or an integer-valued string. */
function queryInt(endpoint, name, value, fallback) {
  if (!present(value)) return fallback;
  const s = String(value).trim();
  if (!/^[+-]?\d+$/.test(s)) {
    throw unprocessable(endpoint, `query.${name}: Input should be a valid integer, unable to parse string as an integer`);
  }
  return Number(s);
}

/** porchlamp.store.queries._validate_persona; the persona list is the snapshot's own. */
function validatePersona(endpoint, persona, snapshot) {
  const known = Object.keys(snapshot.persona_visible_counts).sort();
  if (!known.includes(persona)) {
    throw unprocessable(endpoint, `unknown persona ${pyRepr(persona)}; expected one of [${known.map(pyRepr).join(", ")}]`);
  }
}

/** porchlamp.store.queries._validate_paging. */
function validatePaging(endpoint, limit, offset, maxLimit) {
  if (!Number.isInteger(limit) || limit < 1 || limit > maxLimit) {
    throw unprocessable(endpoint, `limit must be an integer in 1..${maxLimit}; got ${limit}`);
  }
  if (!Number.isInteger(offset) || offset < 0) {
    throw unprocessable(endpoint, `offset must be a non-negative integer; got ${offset}`);
  }
}

/** SQLite's lower(): ASCII letters only (no ICU), as the API's search filter applies it. */
function sqlLower(s) {
  return s.replace(/[A-Z]/g, (c) => c.toLowerCase());
}

/** porchlamp.store.queries._ecosystem_filter + _search_filter: exact ecosystem; `q` a case-insensitive owner or name substring. */
function rowFilter(eco, q) {
  const needle = present(q) ? sqlLower(String(q)) : null;
  return (row) =>
    (!present(eco) || row.ecosystem === eco) &&
    (needle === null || sqlLower(row.owner).includes(needle) || sqlLower(row.name).includes(needle));
}

// ------------------------------------------------------- /leaderboard
/**
 * @param {{persona?: string, eco?: string, q?: string, sort?: string, page?: number, page_size?: number}} params
 */
export function fetchLeaderboard(params = {}) {
  const endpoint = `/v1/porchlamp/leaderboard${toQueryString(params)}`;
  return answer(endpoint, async () => {
    const snapshot = await loadJson("snapshot.json");
    const persona = present(params.persona) ? String(params.persona) : "balanced";
    const page = queryInt(endpoint, "page", params.page, 1);
    const pageSize = queryInt(endpoint, "page_size", params.page_size, 20);
    const sort = present(params.sort) ? String(params.sort) : null;
    const board = await rankedList(endpoint, persona, sort, snapshot);
    validatePaging(endpoint, pageSize, (page - 1) * pageSize, board.max_page_size);
    const keep = rowFilter(params.eco, params.q);
    const order = sort === null ? null : board.orders[sort];
    const matching = [];
    const n = board.rows.length;
    for (let i = 0; i < n; i++) {
      const row = board.rows[order ? order[i] : i];
      if (keep(row)) matching.push(row);
    }
    const offset = (page - 1) * pageSize;
    return {
      rows: matching.slice(offset, offset + pageSize),
      total: matching.length,
      counts: snapshot.counts,
      page,
      page_size: pageSize,
      persona,
      ecosystem: present(params.eco) ? String(params.eco) : null,
    };
  });
}

/** porchlamp.serve.app._translate_sort's allow-list check, then the persona check, then the persona's exported list. */
async function rankedList(endpoint, persona, sort, snapshot) {
  if (sort !== null) {
    const manifest = await loadJson("manifest.json");
    const raw = sort.startsWith("-") ? sort.slice(1) : sort;
    if (!manifest.sort_fields.includes(raw)) {
      throw unprocessable(
        endpoint,
        `sort must be one of (${manifest.sort_fields.map(pyRepr).join(", ")}) (optionally prefixed with '-'); got ${pyRepr(sort)}`,
      );
    }
  }
  validatePersona(endpoint, persona, snapshot);
  return loadJson(`leaderboard-${persona}.json`);
}

// -------------------------------------------------------- /not-ranked
export function fetchNotRanked(params = {}) {
  const endpoint = `/v1/porchlamp/not-ranked${toQueryString(params)}`;
  return answer(endpoint, async () => {
    const snapshot = await loadJson("snapshot.json");
    const persona = present(params.persona) ? String(params.persona) : "balanced";
    if (persona !== "balanced") validatePersona(endpoint, persona, snapshot);
    const list = await loadJson(`not-ranked-${persona}.json`);
    const keep = rowFilter(params.eco, params.q);
    const matching = list.rows.filter((entry) => keep(entry.row));
    const shown = matching.slice(0, list.max_rows);
    const group = (name) => shown.filter((entry) => entry.group === name).map((entry) => entry.row);
    return {
      gated: group("gated"),
      unrated: group("unrated"),
      persona_hidden: group("persona_hidden"),
      counts: list.counts,
      truncated: matching.length > shown.length,
    };
  });
}

// ------------------------------------------------ /repos/{owner}/{name}
function bucketOf(owner, name) {
  // FNV-1a (32-bit) of "owner/name"'s UTF-8 bytes, then murmur3's fmix32 —
  // scripts/export_static.py's bucket_of, which says why the finalizer is there.
  let h = 0x811c9dc5;
  for (const byte of new TextEncoder().encode(`${owner}/${name}`)) {
    h ^= byte;
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  h = (h ^ (h >>> 16)) >>> 0;
  h = Math.imul(h, 0x85ebca6b) >>> 0;
  h = (h ^ (h >>> 13)) >>> 0;
  h = Math.imul(h, 0xc2b2ae35) >>> 0;
  h = (h ^ (h >>> 16)) >>> 0;
  return (h % BUCKETS).toString(16).padStart(2, "0");
}

async function repoRecord(endpoint, owner, name) {
  const records = await loadJson(`repos/${bucketOf(owner, name)}.json`);
  const key = `${owner}/${name}`;
  if (Object.prototype.hasOwnProperty.call(records, key)) return records[key];
  const snapshot = await loadJson("snapshot.json");
  const detail = `no repository ${owner}/${name} in snapshot ${snapshot.snapshot_id}`;
  throw new ApiError(endpoint, 404, detail, detail);
}

function repoPath(owner, name) {
  return `/v1/porchlamp/repos/${encodeURIComponent(owner)}/${encodeURIComponent(name)}`;
}

export function fetchEvidence(owner, name) {
  const endpoint = `${repoPath(owner, name)}/evidence`;
  return answer(endpoint, async () => (await repoRecord(endpoint, owner, name)).evidence);
}

/**
 * `GET /v1/porchlamp/repos/{owner}/{name}` -> `RepoDetailResponse`, the API's
 * own exported answer.
 */
export function fetchRepoDetail(owner, name) {
  const endpoint = repoPath(owner, name);
  return answer(endpoint, async () => (await repoRecord(endpoint, owner, name)).detail);
}

// ------------------------------------------- snapshot / matrix / methodology
export function fetchSnapshot() {
  return answer("/v1/porchlamp/snapshot", () => loadJson("snapshot.json"));
}

export function fetchMatrix() {
  return answer("/v1/porchlamp/matrix", () => loadJson("matrix.json"));
}

export function fetchMethodology() {
  return answer("/v1/porchlamp/methodology", () => loadJson("methodology.json"));
}

// ---------------------------------------------------------- /reproject
/**
 * SQLite's ROUND(x, n) for the values a composite takes (0..100). Exact
 * half-up rounding (Number.prototype.toFixed) equals SQLite 3.53's ROUND
 * at n = 2 — measured on 1.6 M doubles, including every neighbour of a
 * .xx5 boundary — and at n = 12 (the tier-cutoff comparison) differs only
 * for a value within 5e-13 of a cutoff whose 15th-19th digits are a 4999…
 * run; the verifier's full-list comparisons would show such a case.
 */
function sqlRound(x, n) {
  return x === null ? null : Number(x.toFixed(n));
}

function descNullsLast(a, b) {
  if (a === null) return b === null ? 0 : 1;
  if (b === null) return -1;
  return a > b ? -1 : a < b ? 1 : 0;
}

function ascNullsLast(a, b) {
  if (a === null) return b === null ? 0 : 1;
  if (b === null) return -1;
  return a < b ? -1 : a > b ? 1 : 0;
}

/** porchlamp.store.queries._order_by_sql over the custom composite/coverage: D-TIE-1's 8 levels. */
function tieBreakOrder(a, b) {
  return (
    descNullsLast(a.porchlampDisplay, b.porchlampDisplay) ||
    descNullsLast(a.coverage, b.coverage) ||
    descNullsLast(a.row.p_responsiveness, b.row.p_responsiveness) ||
    ascNullsLast(a.row.friction, b.row.friction) ||
    descNullsLast(a.row.leverage, b.row.leverage) ||
    ascNullsLast(a.row.ttfhr_p75, b.row.ttfhr_p75) ||
    descNullsLast(a.row.last_human_commit_at, b.row.last_human_commit_at) ||
    (a.lowerKey < b.lowerKey ? -1 : a.lowerKey > b.lowerKey ? 1 : 0)
  );
}

/**
 * CPython's (3.12+) sum() over floats: Neumaier-compensated, not a plain
 * left-to-right fold. The API divides custom coverage by
 * `float(sum(weights.values()))`, so a plain fold is off by an ulp for some
 * weight vectors — and so is every coverage divided by it.
 */
function pySum(values) {
  if (values.length === 0) return 0;
  let total = values[0]; // sum() starts from int 0; 0 + x is x
  let c = 0;
  for (let i = 1; i < values.length; i++) {
    const x = values[i];
    const t = total + x;
    c += Math.abs(total) >= Math.abs(x) ? total - t + x : x - t + total;
    total = t;
  }
  return c !== 0 && Number.isFinite(c) ? total + c : total;
}

/** The whole custom projection, ranked: porchlamp.store.queries.reproject without its LIMIT/OFFSET. */
function reprojectAll(base, weights) {
  const pillars = base.pillars;
  const w = pillars.map((p) => weights[p]);
  const totalWeight = pySum(w); // porchlamp.store.queries.reproject: float(sum(weights.values()))
  const tierIndex = (label) => base.tier_order.indexOf(label);
  const visible = [];
  for (const row of base.rows) {
    // Left to right in pillar order, as SQLite evaluates `t1 + t2 + ... + t5`.
    let num = 0;
    let den = 0;
    let cov = 0;
    for (let i = 0; i < pillars.length; i++) {
      const p = row[`p_${pillars[i]}`];
      const c = row[`c_${pillars[i]}`];
      if (c !== null && c > 0 && p !== null) {
        num += w[i] * p;
        den += w[i];
      }
      cov += w[i] * (c === null ? 0 : c);
    }
    const composite = den === 0 ? null : num / den;
    const coverage = cov / totalWeight;
    if (composite === null || !(coverage >= base.rated_min_coverage)) continue;
    const scored = sqlRound(composite, base.ratio_decimals);
    let scoreTier = tierIndex("D");
    for (const [cutoff, label] of base.tier_cutoffs) {
      if (scored >= cutoff) {
        scoreTier = tierIndex(label);
        break;
      }
    }
    const ceiling =
      coverage >= base.no_ceiling_min_coverage ? tierIndex("S+") : coverage >= base.ceiling_a_min_coverage ? tierIndex("A") : tierIndex("B");
    visible.push({
      row,
      porchlampDisplay: sqlRound(composite, 2),
      coverage,
      tier: base.tier_order[Math.min(scoreTier, ceiling)],
      lowerKey: sqlLower(`${row.owner}/${row.name}`),
    });
  }
  visible.sort(tieBreakOrder);
  // _tie_group_sql: DENSE_RANK over ROUND(composite, 2) DESC, NULL for a value only one row has.
  const members = new Map();
  for (const v of visible) members.set(v.porchlampDisplay, (members.get(v.porchlampDisplay) || 0) + 1);
  const denseRank = new Map([...members.keys()].sort((a, b) => b - a).map((value, i) => [value, i + 1]));
  visible.forEach((v, i) => {
    v.rank = i + 1;
    v.tieGroup = members.get(v.porchlampDisplay) === 1 ? null : denseRank.get(v.porchlampDisplay);
  });
  return visible;
}

/** porchlamp.serve.app._no_ceiling_or_band. */
function tierCeiling(base, coverage) {
  if (coverage >= base.no_ceiling_min_coverage) return null;
  if (coverage >= base.ceiling_a_min_coverage) return "A";
  return "B";
}

/** porchlamp.serve.app._reproject_row: a ReprojectRow, fields in the contract's order. */
function reprojectRow(base, v) {
  const r = v.row;
  const out = {
    rank: v.rank,
    tie_group: v.tieGroup,
    owner: r.owner,
    name: r.name,
    ecosystem: r.ecosystem,
    porchlamp: v.porchlampDisplay,
    coverage: v.coverage,
    tier: v.tier,
    tier_ceiling: tierCeiling(base, v.coverage),
    quadrant: r.quadrant,
    leverage: r.leverage,
    friction: r.friction,
    leverage_coverage: r.leverage_coverage,
    friction_coverage: r.friction_coverage,
  };
  for (const p of base.pillars) {
    out[`p_${p}`] = r[`p_${p}`];
    out[`c_${p}`] = r[`c_${p}`];
  }
  Object.assign(out, {
    persona_gates: r.persona_gates,
    persona_composites: r.persona_composites,
    suspicion: r.suspicion,
    stars_audited: r.stars_audited,
    snapshot_id: r.snapshot_id,
    benchmark_version: r.benchmark_version,
    raw_hash: r.raw_hash,
    balanced_rank: r.balanced_rank,
    rank_delta: r.balanced_rank === null ? null : r.balanced_rank - v.rank,
    balanced_tie_group: r.balanced_tie_group,
    balanced_porchlamp: r.balanced_porchlamp,
    balanced_coverage: r.balanced_coverage,
    balanced_tier: r.balanced_tier,
  });
  return out;
}

/** ReprojectRequest's pydantic validation, then porchlamp.core.aggregate.validate_pillar_weights. */
function validatedWeights(endpoint, body, base) {
  if (body === null || typeof body !== "object" || Array.isArray(body)) {
    throw unprocessable(endpoint, "body: Input should be a valid dictionary or object to extract fields from");
  }
  const fields = base.pillars.map((p) => `weight_${p}`);
  const problems = [];
  for (const key of Object.keys(body)) {
    if (!fields.includes(key)) problems.push(`body.${key}: Extra inputs are not permitted`);
  }
  for (const field of fields) {
    if (!(field in body)) problems.push(`body.${field}: Field required`);
    else if (typeof body[field] !== "number" || !Number.isFinite(body[field])) problems.push(`body.${field}: Input should be a valid number`);
  }
  if (problems.length) throw unprocessable(endpoint, problems.join("; "));
  const values = fields.map((f) => body[f]);
  for (const v of values) {
    if (v < 0) throw unprocessable(endpoint, `body: Value error, weights must be non-negative, got ${v}`);
  }
  const total = pySum(values); // the pydantic validator's own sum(weights)
  if (Math.abs(total - 1.0) > base.weight_sum_tolerance) {
    throw unprocessable(endpoint, `body: Value error, weights must sum to 1.0, got ${total}`);
  }
  return Object.fromEntries(base.pillars.map((p, i) => [p, values[i]]));
}

/**
 * Screen D (Configure weights): `POST /v1/porchlamp/reproject` with a five-pillar
 * weight vector -> `ReprojectResponse`, computed here. `page`/`page_size`
 * mirror the endpoint's query parameters (the UI sends neither: page 1 of
 * the default page size, as against the API).
 */
export async function postReproject(weights, { page = 1, page_size: pageSize } = {}) {
  const endpoint = "/v1/porchlamp/reproject";
  return answer(endpoint, async () => {
    const base = await loadJson("reproject.json");
    const size = pageSize === undefined ? base.default_page_size : pageSize;
    const w = validatedWeights(endpoint, weights, base);
    validatePaging(endpoint, size, (page - 1) * size, base.max_limit);
    const ranked = reprojectAll(base, w);
    const offset = (page - 1) * size;
    return {
      rows: ranked.slice(offset, offset + size).map((v) => reprojectRow(base, v)),
      total: ranked.length,
      lens: "custom",
      page,
      page_size: size,
    };
  });
}
