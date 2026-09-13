// api.js — fetch wrappers for the v2 endpoints (06 §6). Every wrapper
// returns a typed result object `{ ok: true, data }` or `{ ok: false,
// error: ApiError }` rather than throwing across the state boundary, so
// callers in state.js can always render an explicit error state (U-7)
// instead of an unhandled rejection or an indefinite spinner (ruling D:
// "never an indefinite Loading…" — every request carries a hard timeout).

const DEFAULT_TIMEOUT_MS = 5000;

export class ApiError extends Error {
  constructor(endpoint, status, message) {
    super(message || `request to ${endpoint} failed with status ${status}`);
    this.name = "ApiError";
    this.endpoint = endpoint;
    // 0: network-level failure (no response at all).
    // "timeout": exceeded DEFAULT_TIMEOUT_MS with no response.
    this.status = status;
  }
}

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
    return { ok: false, error: new ApiError(endpoint, response.status) };
  }
  try {
    const data = await response.json();
    return { ok: true, data };
  } catch (parseError) {
    return { ok: false, error: new ApiError(endpoint, response.status, parseError.message) };
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
  const endpoint = `/v1/cvi/leaderboard${toQueryString(params)}`;
  return getJson(endpoint);
}

export function fetchNotRanked(params = {}) {
  return getJson(`/v1/cvi/not-ranked${toQueryString(params)}`);
}

export function fetchEvidence(owner, name) {
  return getJson(`/v1/cvi/repos/${encodeURIComponent(owner)}/${encodeURIComponent(name)}/evidence`);
}

export function fetchSnapshot() {
  return getJson("/v1/cvi/snapshot");
}

export { DEFAULT_TIMEOUT_MS };

/**
 * W4-5b Screen B (Matrix): `GET /v1/cvi/matrix` -> `MatrixResponse`
 * (`cvi.serve.contract.MatrixResponse`).
 */
export function fetchMatrix() {
  return getJson("/v1/cvi/matrix");
}

/**
 * W4-5b Screen F (Methodology): `GET /v1/cvi/methodology` ->
 * `MethodologyResponse` (`cvi.serve.contract.MethodologyResponse`).
 */
export function fetchMethodology() {
  return getJson("/v1/cvi/methodology");
}

/**
 * W4-5b Screen D (Configure weights): `POST /v1/cvi/reproject` with a
 * five-pillar weight vector (`ReprojectRequest`) -> `ReprojectResponse`.
 * Shares `getJson`'s timeout/typed-error contract (U-15) via a small POST
 * variant rather than a second, drifting error-handling path.
 */
export async function postReproject(weights, { timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  const endpoint = "/v1/cvi/reproject";
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
    let message;
    try {
      message = (await response.json())?.error;
    } catch {
      // no JSON error body; fall through with the bare status
    }
    return { ok: false, error: new ApiError(endpoint, response.status, message) };
  }
  try {
    const data = await response.json();
    return { ok: true, data };
  } catch (parseError) {
    return { ok: false, error: new ApiError(endpoint, response.status, parseError.message) };
  }
}
