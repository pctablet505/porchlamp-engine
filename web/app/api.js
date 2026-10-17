// api.js — fetch wrappers for the v2 endpoints (06 §6). Every wrapper
// returns a typed result object `{ ok: true, data }` or `{ ok: false,
// error: ApiError }` rather than throwing across the state boundary, so
// callers in state.js can always render an explicit error state (U-7)
// instead of an unhandled rejection or an indefinite spinner (ruling D:
// "never an indefinite Loading…" — every request carries a hard timeout).

const DEFAULT_TIMEOUT_MS = 5000;

export class ApiError extends Error {
  constructor(endpoint, status, message, detail) {
    super(message || `request to ${endpoint} failed with status ${status}`);
    this.name = "ApiError";
    this.endpoint = endpoint;
    // 0: network-level failure (no response at all).
    // "timeout": exceeded DEFAULT_TIMEOUT_MS with no response.
    this.status = status;
    // The API's error body is `{"error": {endpoint, status, detail}}`
    // (mirrored exactly by scripts/serve_ui_fixtures.py's
    // `_send_error_json`) — `detail` is that body's own explanation of what
    // went wrong, shown to the user AFTER the endpoint+status
    // (`copy.errorBannerText`'s third argument), distinct from `message`
    // (this Error's own summary, used for `Error.prototype.toString()`/
    // logging, never rendered directly).
    this.detail = detail;
  }
}

/**
 * Extracts `detail` from a non-2xx response body, per the real API's
 * `{"error": {endpoint, status, detail}}` shape. Never throws: a body that
 * isn't JSON, or doesn't have the expected shape, yields `undefined` and
 * the caller falls back to a bare endpoint+status message.
 */
async function extractErrorDetail(response) {
  try {
    const body = await response.clone().json();
    const err = body && body.error;
    if (err && typeof err === "object") return typeof err.detail === "string" ? err.detail : undefined;
    // Tolerate a bare-string `{"error": "..."}` body too, not only the
    // nested form.
    return typeof err === "string" ? err : undefined;
  } catch {
    return undefined;
  }
}

const JSON_CONTENT_TYPE_RE = /\bapplication\/json\b/i;

async function getJson(endpoint, { timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
  let response;
  try {
    response = await fetch(endpoint, {
      headers: { Accept: "application/json" },
      signal: controller ? controller.signal : undefined,
    });
  } catch (networkError) {
    const timedOut = networkError && networkError.name === "AbortError";
    return { ok: false, error: new ApiError(endpoint, timedOut ? "timeout" : 0, networkError.message) };
  } finally {
    if (timer) clearTimeout(timer);
  }
  if (!response.ok) {
    const detail = await extractErrorDetail(response);
    return { ok: false, error: new ApiError(endpoint, response.status, detail, detail) };
  }
  // A 2xx response whose Content-Type is not JSON is itself an error (a
  // misconfigured proxy, an HTML error page served with a 200, etc.) --
  // checked BEFORE attempting to parse, so the failure is named precisely
  // rather than surfacing as a generic "invalid JSON" parse error.
  const contentType = response.headers.get("content-type") || "";
  if (!JSON_CONTENT_TYPE_RE.test(contentType)) {
    const detail = `expected application/json, got ${contentType || "(no content-type)"}`;
    return { ok: false, error: new ApiError(endpoint, response.status, detail, detail) };
  }
  try {
    const data = await response.json();
    return { ok: true, data };
  } catch (parseError) {
    return { ok: false, error: new ApiError(endpoint, response.status, parseError.message, parseError.message) };
  }
}

function toQueryString(params) {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    qs.set(key, String(value));
  }
  const s = qs.toString();
  return s ? `?${s}` : "";
}

/**
 * @param {{persona?: string, eco?: string, q?: string, sort?: string, page?: number, page_size?: number}} params
 */
export function fetchLeaderboard(params = {}) {
  const endpoint = `/v1/porchlamp/leaderboard${toQueryString(params)}`;
  return getJson(endpoint);
}

export function fetchNotRanked(params = {}) {
  return getJson(`/v1/porchlamp/not-ranked${toQueryString(params)}`);
}

/**
 * The not-ranked list the page pages and filters on the client. Against the API this is `fetchNotRanked`, whose
 * answer is cut at the endpoint's limit and says so (`truncated`); the static build (web/static/api.js) has the
 * whole list and returns it.
 */
export function fetchNotRankedFull(params = {}) {
  return fetchNotRanked(params);
}

export function fetchEvidence(owner, name) {
  return getJson(`/v1/porchlamp/repos/${encodeURIComponent(owner)}/${encodeURIComponent(name)}/evidence`);
}

/**
 * `GET /v1/porchlamp/repos/{owner}/{name}` -> `RepoDetailResponse` (the flat
 * shape, `porchlamp/serve/contract.py`) — the drawer's record fetch,
 * independent of which screen opened it. Both producers of this endpoint
 * (the real API, `porchlamp/serve/app.py`, and the UI fixture stand-in,
 * `scripts/serve_ui_fixtures.py`) send this same shape.
 * `views/drawer.js::classifyKind` derives `kind` client-side from
 * `eligible`/`rated`; it is not on the wire at all.
 */
export function fetchRepoDetail(owner, name) {
  return getJson(`/v1/porchlamp/repos/${encodeURIComponent(owner)}/${encodeURIComponent(name)}`);
}

export function fetchSnapshot() {
  return getJson("/v1/porchlamp/snapshot");
}

export { DEFAULT_TIMEOUT_MS };

/**
 * Screen B (Matrix): `GET /v1/porchlamp/matrix` -> `MatrixResponse`
 * (`porchlamp.serve.contract.MatrixResponse`).
 */
export function fetchMatrix() {
  return getJson("/v1/porchlamp/matrix");
}

/**
 * Screen F (Methodology): `GET /v1/porchlamp/methodology` ->
 * `MethodologyResponse` (`porchlamp.serve.contract.MethodologyResponse`).
 */
export function fetchMethodology() {
  return getJson("/v1/porchlamp/methodology");
}

/**
 * The export's own record of what it published (web/static/api.js answers it on the published site, where the
 * methodology prints the digest of the database file it came from). The API publishes no database file, so it has
 * none: an answer without a request.
 */
export function fetchExportManifest() {
  return Promise.resolve({ ok: false, error: new ApiError("manifest.json", 404, "the API serves no export manifest") });
}

/**
 * Screen D (Configure weights): `POST /v1/porchlamp/reproject` with a
 * five-pillar weight vector (`ReprojectRequest`) -> `ReprojectResponse`.
 * Shares `getJson`'s timeout/typed-error contract (U-15) via a small POST
 * variant rather than a second, drifting error-handling path.
 */
export async function postReproject(weights, { page, page_size, timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  const endpoint = `/v1/porchlamp/reproject${toQueryString({ page, page_size })}`;
  const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
  let response;
  try {
    response = await fetch(endpoint, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(weights),
      signal: controller ? controller.signal : undefined,
    });
  } catch (networkError) {
    const timedOut = networkError && networkError.name === "AbortError";
    return { ok: false, error: new ApiError(endpoint, timedOut ? "timeout" : 0, networkError.message) };
  } finally {
    if (timer) clearTimeout(timer);
  }
  if (!response.ok) {
    const detail = await extractErrorDetail(response);
    return { ok: false, error: new ApiError(endpoint, response.status, detail, detail) };
  }
  const contentType = response.headers.get("content-type") || "";
  if (!JSON_CONTENT_TYPE_RE.test(contentType)) {
    const detail = `expected application/json, got ${contentType || "(no content-type)"}`;
    return { ok: false, error: new ApiError(endpoint, response.status, detail, detail) };
  }
  try {
    const data = await response.json();
    return { ok: true, data };
  } catch (parseError) {
    return { ok: false, error: new ApiError(endpoint, response.status, parseError.message, parseError.message) };
  }
}
