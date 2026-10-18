// views/notfound.js — the view at an address no route claims (routes.js "notfound"). On the static site
// GitHub Pages answers such an address with 404.html, which runs the app: a mistyped or retired address still
// offers a search over the ranked projects and the way back to the rankings.

import * as copy from "../copy.js";
import { routePath } from "../routes.js";
import { escapeHtml } from "../escape.js";

/** The search is a plain form to the leaderboard's address (`?q=`), so it works before the app has started. */
export function renderShell(state) {
  const home = escapeHtml(routePath("ranked"));
  return (
    `<section class="not-found" aria-labelledby="not-found-heading">` +
    `<h1 id="not-found-heading" class="view-heading">${escapeHtml(copy.NOT_FOUND_HEADING)}</h1>` +
    `<p>${escapeHtml(copy.notFoundText(state.missingPath || "/"))}</p>` +
    `<form class="not-found-search" action="${home}" method="get" role="search">` +
    `<label class="field-label" for="not-found-q">${escapeHtml(copy.NOT_FOUND_SEARCH_LABEL)}</label>` +
    `<input class="control" id="not-found-q" type="search" name="q" autocomplete="off">` +
    `<button type="submit" class="icon-button">${escapeHtml(copy.NOT_FOUND_SEARCH_BUTTON)}</button>` +
    `</form>` +
    `<p><a href="${home}" data-route-tab="ranked">${escapeHtml(copy.NOT_FOUND_HOME_LINK)}</a></p>` +
    `</section>`
  );
}
