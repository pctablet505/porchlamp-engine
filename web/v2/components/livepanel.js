// components/livepanel.js — the drawer body for a repository the snapshot
// does not hold (06 §3 C, "Repositories outside the snapshot"). It says so,
// offers the opt-in live check (web/v2/livecheck.js) and renders its result.
// A pure `(state, repoKey) -> string` like every component: it never fetches,
// so opening the drawer contacts nobody until the visitor presses the button.
//
// Everything GitHub returns is untrusted text (a repository description is
// written by whoever owns the repository): every value passes through
// `escapeHtml`, and the only link built from it is `copy.drawerGithubUrl`,
// from a name that has passed `parseRepoSlug`.

import * as copy from "../copy.js";
import { parseRepoSlug, LIVE_REQUESTS_PER_CHECK } from "../livecheck.js";

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

const STATUS_CLASS = Object.freeze({
  pass: "is-pass",
  fail: "is-fail",
  undecided: "is-undecided",
  routed: "is-routed",
  not_checked: "is-not-checked",
});

//: Outcomes a retry cannot change: GitHub said the repository does not exist,
//: is unavailable, or the name is not one it accepts.
const FINAL_ERRORS = new Set(["not_found", "unavailable", "invalid"]);

function isoDate(value) {
  return typeof value === "string" && value.length >= 10 ? value.slice(0, 10) : copy.MISSING_VALUE;
}

function githubLink(fullName) {
  const slug = parseRepoSlug(fullName);
  if (!slug) return "";
  return (
    `<a class="drawer-github-link live-check-github" href="${escapeHtml(copy.drawerGithubUrl(slug.owner, slug.name))}" ` +
    `target="_blank" rel="noopener noreferrer">${escapeHtml(copy.DRAWER_GITHUB_LABEL)}</a>`
  );
}

function renderStart(key) {
  return (
    `<button type="button" class="live-check-start" data-live-check="${escapeHtml(key)}">${escapeHtml(copy.LIVE_CHECK_BUTTON_LABEL)}</button>` +
    `<p class="live-check-note">${escapeHtml(copy.liveCheckNoteText(LIVE_REQUESTS_PER_CHECK))}</p>`
  );
}

function renderResult(result, key) {
  const repo = result.repo;
  const canonical = repo.fullName && repo.fullName !== key && parseRepoSlug(repo.fullName) ? repo.fullName : null;
  const renamed = canonical
    ? `<p class="live-check-renamed">${escapeHtml(copy.liveRenamedText(canonical))} ` +
      `<button type="button" class="link-button" data-live-open="${escapeHtml(canonical)}">${escapeHtml(copy.liveOpenCanonicalLabel(canonical))}</button></p>`
    : "";
  const description = repo.description ? `<p class="live-check-description">${escapeHtml(repo.description)}</p>` : "";
  const facts = [
    [copy.LIVE_FACT_LABELS.stars, repo.stars === null ? copy.MISSING_VALUE : String(repo.stars)],
    [copy.LIVE_FACT_LABELS.language, repo.language || copy.MISSING_VALUE],
    [copy.LIVE_FACT_LABELS.created, isoDate(repo.createdAt)],
    [copy.LIVE_FACT_LABELS.pushed, isoDate(repo.pushedAt)],
    [copy.LIVE_FACT_LABELS.license, repo.licenseSpdx || copy.MISSING_VALUE],
  ]
    .map(([label, value]) => `<div class="live-fact"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`)
    .join("");
  const gates = result.checks
    .map(
      (c) =>
        `<li class="live-gate" data-live-gate="${escapeHtml(c.id)}">` +
        `<div class="live-gate-head"><span class="live-gate-name">${escapeHtml(copy.LIVE_GATE_NAMES[c.id] || c.id)}</span>` +
        `<span class="live-pill ${STATUS_CLASS[c.status] || ""}">${escapeHtml(copy.LIVE_STATUS_LABELS[c.status] || c.status)}</span></div>` +
        `<p class="live-gate-reason">${escapeHtml(copy.liveReasonText(c.reason, c.detail))}</p>` +
        `</li>`
    )
    .join("");
  return (
    `<p class="live-check-disclaimer">${escapeHtml(copy.LIVE_CHECK_DISCLAIMER)}</p>` +
    renamed +
    description +
    `<dl class="live-facts">${facts}</dl>` +
    `<ol class="live-gates">${gates}</ol>` +
    `<p class="live-check-foot">${escapeHtml(copy.liveCheckedAtText(result.checkedAt, result.requests))}</p>` +
    githubLink(canonical || key)
  );
}

function renderError(error, key, nowMs) {
  const retry =
    error && FINAL_ERRORS.has(error.kind)
      ? ""
      : ` <button type="button" class="icon-button" data-live-check="${escapeHtml(key)}">${escapeHtml(copy.RETRY_LABEL)}</button>`;
  return `<div class="error-banner live-check-error" role="alert">${escapeHtml(copy.liveErrorText(error, nowMs))}${retry}</div>`;
}

/**
 * The not-in-snapshot drawer body. Only an already-normalised `owner/name`
 * that GitHub accepts offers a live check (`state.js` normalises URLs and
 * `.git` suffixes before the detail fetch runs); anything else keeps the plain
 * not-found sentence and never makes a claim about a different key.
 */
export function renderNotInSnapshot(state, repoKey, { nowMs = Date.now() } = {}) {
  const slug = parseRepoSlug(repoKey);
  const key = slug ? `${slug.owner}/${slug.name}` : "";
  if (!slug || key !== repoKey) return `<div class="empty-state">${escapeHtml(copy.DRAWER_NOT_FOUND_TEXT)}</div>`;
  const snapshotId = state.snapshot && state.snapshot.snapshot_id;
  const entry = state.liveCheckByKey ? state.liveCheckByKey[key] : undefined;
  let body;
  if (!entry) body = renderStart(key);
  else if (entry.status === "loading") body = `<p class="live-check-status" role="status">${escapeHtml(copy.LIVE_CHECK_LOADING_TEXT)}</p>`;
  else if (entry.outcome && entry.outcome.ok) body = renderResult(entry.outcome.result, key);
  else body = renderError(entry.outcome ? entry.outcome.error : null, key, nowMs);
  return (
    `<section class="live-check" data-live-check-region="true" tabindex="-1" aria-labelledby="live-check-title">` +
    `<p class="live-check-lede">${escapeHtml(copy.liveNotInSnapshotText(key, snapshotId))}</p>` +
    `<h2 class="live-check-title" id="live-check-title">${escapeHtml(copy.LIVE_CHECK_HEADING)}</h2>` +
    body +
    `</section>`
  );
}
