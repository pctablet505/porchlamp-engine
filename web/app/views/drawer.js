// views/drawer.js — Screen C "Why this score" (06 §3): the side panel's dialog frame. Its body is the project view
// (views/project.js `renderProject`, mode "panel"), the same one that draws a project's own page; this module
// builds the record from the app's state, picks the lens's reading of it, and draws the loading, error and
// not-in-snapshot bodies.
//
// The README badge box (`renderMaintainerBadge`) ends the Composite card when the repository's balanced record
// earns one (copy.js `maintainerBadgeSpec`): the badge is the balanced rating whatever lens is active (U-4), and its
// Markdown points at the public site's address, which app.js passes in.
//
// Focus trap and Esc-to-close-and-restore-focus are DOM concerns handled by
// app.js; this module only produces markup.

import * as copy from "../copy.js";
import { renderNotInSnapshot } from "../components/livepanel.js";
import { projectPath } from "../routes.js";
import { renderProject, deriveTierCeiling } from "./project.js";

// The coverage ceiling band is derived in project.js (it also reads the persona rows); drawer.js hands it on.
export { deriveTierCeiling };

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

/**
 * Locates a repo's record. The drawer's ONLY source is
 * `state.repoDetailByKey` — the result of `GET /v1/porchlamp/repos/{o}/{n}`
 * (`app.js::loadRepoDetail`, called by `openRepo` regardless of which
 * screen opened it) — so the drawer's content is identical from every
 * entry point. Matrix's own point list, a Compare chip's cache and the
 * paginated leaderboard's CURRENT PAGE are only partial views of a repo's
 * data and are never consulted here.
 */
export function findRow(state, owner, name) {
  const key = `${owner}/${name}`;
  return (state.repoDetailByKey && state.repoDetailByKey[key]) || null;
}

/**
 * `GET /v1/porchlamp/repos/{owner}/{name}` does not send `kind` on the wire at
 * all (`porchlamp/serve/contract.py`'s `RepoDetailResponse` is the flat row), so
 * `kind` is computed client-side from the same two fields the store's own
 * rule uses (`porchlamp.store.queries.list_not_ranked`'s docstring: "Gated
 * (eligible=0) and unrated (eligible=1, rated=0)") -- `!eligible` is gated
 * (disqualified before scoring, never rated), `eligible && !rated` is
 * unrated (passed eligibility, failed the coverage floor), anything else
 * is rated.
 */
export function classifyKind(found) {
  if (!found.eligible) return "gated";
  if (!found.rated) return "unrated";
  return "rated";
}

/**
 * Persona-aware classification for the drawer when a non-balanced lens is
 * active (`state.persona !== "balanced"`). Preserves `classifyKind(found)`
 * verbatim for balanced and for the static contract parity test.
 */
export function classifyKindForLens(found, persona) {
  if (!found.eligible) {
    if (typeof found.gate_reason === "string" && found.gate_reason.startsWith("unrated:")) {
      return "unrated";
    }
    return "gated";
  }
  if (persona && persona !== "balanced") {
    if (found.persona_gates && found.persona_gates[persona]) return "persona-gated";
    const pc = found.persona_composites && found.persona_composites[persona];
    if (pc) return pc.rated ? "rated" : "unrated";
  }
  return classifyKind(found);
}

/**
 * Projects the active persona's composite fields (`porchlamp`, `coverage`,
 * `tier`, `tier_ceiling`, `rated`) onto a shallow copy of `found` when a
 * non-balanced persona lens is active and has a `persona_composites` entry.
 */
export function projectRowForLens(found, persona) {
  if (!persona || persona === "balanced" || !found.persona_composites) return found;
  const pc = found.persona_composites[persona];
  if (!pc) return found;
  return {
    ...found,
    porchlamp: pc.porchlamp,
    coverage: pc.coverage,
    tier: pc.tier,
    tier_ceiling: deriveTierCeiling(pc.coverage, pc.tier_ceiling),
    rated: pc.rated,
  };
}

/**
 * The README badge box at the end of the Composite section: a preview of the
 * image, the Markdown to paste and a copy button (app.js copies the
 * `[data-badge-markdown]` text). `canonicalRow` is the balanced record, never
 * the lens projection (U-4); `baseUrl` is the public site's address.
 */
export function renderMaintainerBadge(canonicalRow, baseUrl = "") {
  const spec = copy.maintainerBadgeSpec(canonicalRow);
  if (!spec) return "";
  return (
    `<div class="maintainer-badge-box" data-maintainer-badge="${escapeHtml(spec.id)}">` +
    `<div class="maintainer-badge-head">` +
    `<span class="identity-label">${escapeHtml(copy.MAINTAINER_BADGE_HEADING)}</span>` +
    `<button type="button" class="reproduce-copy" data-badge-copy="true">${escapeHtml(copy.MAINTAINER_BADGE_COPY_LABEL)}</button>` +
    `</div>` +
    // From the site root: the panel is open at its project's address (/projects/<owner>/<name>/).
    `<img class="maintainer-badge-preview" src="${escapeHtml(`/${spec.file}`)}" alt="${escapeHtml(spec.alt)}" height="20">` +
    `<p class="maintainer-badge-note">${escapeHtml(copy.MAINTAINER_BADGE_NOTE)}</p>` +
    `<pre class="maintainer-badge-snippet" data-badge-markdown="true">${escapeHtml(copy.maintainerBadgeMarkdown(canonicalRow, baseUrl))}</pre>` +
    `</div>`
  );
}

/**
 * `opts.siteBaseUrl` is the public site's address for the README badge's
 * Markdown; app.js reads it from index.html, since this module never reads
 * the DOM.
 */
export function renderDrawer(state, opts = {}) {
  if (!state.repo) return "";
  const [owner, name] = state.repo.split("/");
  const found = owner && name ? findRow(state, owner, name) : null;

  if (!found) {
    let body;
    // A 404 from `GET /v1/porchlamp/repos/{o}/{n}` arrives as
    // `state.repoDetailError` like any other non-2xx
    // (api.js/app.js::loadRepoDetail treat them the same way), but it is
    // a genuine not-found: retrying would 404 again, so it gets the
    // not-in-snapshot panel (components/livepanel.js) and no retry button.
    // Every OTHER status (500, timeout, a network failure) keeps the retry banner.
    if (state.repoDetailLoading) {
      body = `<p>${escapeHtml(copy.DRAWER_LOADING_TEXT)}</p>`;
    } else if (state.repoDetailError && state.repoDetailError.status !== 404) {
      const err = state.repoDetailError;
      body =
        `<div class="error-banner" role="alert">${escapeHtml(copy.drawerErrorText(err.endpoint, err.status, err.detail))}` +
        ` <button type="button" class="icon-button" data-repo-detail-retry="true">${escapeHtml(copy.RETRY_LABEL)}</button></div>`;
    } else if (state.repoDetailError) {
      // A confirmed 404 -- not in the snapshot: say so, and offer the opt-in
      // live check (components/livepanel.js). Rendering it fetches nothing.
      body = renderNotInSnapshot(state, state.repo);
    } else {
      // No record and no answer to explain it (a malformed key never fetches).
      body = `<div class="empty-state">${escapeHtml(copy.DRAWER_NOT_FOUND_TEXT)}</div>`;
    }
    const fallbackTitle = name ? `${escapeHtml(owner)}/${escapeHtml(name)}` : escapeHtml(state.repo);
    return (
      `<div class="drawer-backdrop" data-drawer-backdrop="true">` +
      `<div class="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">` +
      `<div class="drawer-header"><h2 id="drawer-title" class="drawer-fallback-title">${fallbackTitle}</h2>` +
      `<button type="button" class="icon-button" data-drawer-close="true">${escapeHtml(copy.DRAWER_CLOSE_LABEL)}</button></div>` +
      body +
      `</div></div>`
    );
  }

  // `found` IS the flat `RepoDetailResponse` -- `kind` is derived under the
  // active persona lens (`classifyKindForLens`), and `row` projects the lens's
  // composite score/coverage/tier when a non-balanced lens is active on a tab
  // that exposes the persona selector (`ranked`, `notranked`, `compare`).
  const lensActiveOnTab =
    !state.tab || state.tab === "ranked" || state.tab === "notranked" || state.tab === "compare";
  const activePersona = lensActiveOnTab ? state.persona : "balanced";
  const kind = classifyKindForLens(found, activePersona);
  const row = projectRowForLens(found, activePersona);

  // The export's extras for this project (the record's file; for a project with a balanced rank, the answer, the lens
  // ranks and the similar projects): null while they load and under the dev API.
  const extras = (state.projectExtrasByKey && state.projectExtrasByKey[state.repo]) || null;
  const evidenceRows = state.evidenceByRepo[state.repo];
  const record = {
    detail: row,
    evidence: evidenceRows,
    answer: extras ? extras.answer : null,
    standings: extras ? extras.standings : null,
    similar: extras ? extras.similar : null,
  };
  const body = renderProject(record, {
    mode: "panel",
    recordFile: extras ? extras.recordFile : null,
    // A project ranked under the balanced lens has its own page at the panel's address (the export writes one per
    // ranked project); a plain link loads it. Any other project's address answers with this panel only.
    pageHref: classifyKind(found) === "rated" ? projectPath(found.owner, found.name) : null,
    kind,
    persona: activePersona,
    badgeHtml: renderMaintainerBadge(found, opts.siteBaseUrl),
    evidenceError: evidenceRows ? null : state.evidenceError,
  });
  return (
    `<div class="drawer-backdrop" data-drawer-backdrop="true">` +
    `<div class="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">` +
    body +
    `</div></div>`
  );
}
