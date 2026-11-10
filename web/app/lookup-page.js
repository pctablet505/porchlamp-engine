// lookup-page.js — the one script of /lookup/ (views/lookup.js): the form. Nothing leaves the browser until the visitor
// presses the button; an address's `?user=` only fills the box.

import { fetchEvidence, fetchFirstPullRequests } from "./api.js";
import { parseLogin, runLookup } from "./lookup.js";
import { lookupStatus, renderLookupLoading, renderLookupResult } from "./views/lookup.js";

const form = document.querySelector("[data-lookup-form]");
const result = document.getElementById("lookup-result");
const status = document.getElementById("lookup-status");

/** How far down the screen a new result may start before the page scrolls to it: a phone's keyboard hides the rest. */
const RESULT_IN_VIEW = 0.6;

/** A project's typical first reply from the site's own files; null when the snapshot does not hold it or measured none. */
async function projectReply(owner, name) {
  const answer = await fetchEvidence(owner, name);
  if (!answer.ok || !Array.isArray(answer.data)) return null;
  const row = answer.data.find((r) => r.param_id === "ttfhr_hours");
  return row && !row.unmeasured_reason && typeof row.value === "number" ? row.value : null;
}

if (form && result && status) {
  const input = form.elements.login;
  const asked = parseLogin(new URLSearchParams(location.search).get("user") || "");
  if (asked) input.value = asked;
  let running = false;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (running) return;
    running = true;
    input.blur();
    status.textContent = "";
    result.innerHTML = renderLookupLoading();
    const report = await fetchFirstPullRequests();
    const outcome = await runLookup(input.value, { curve: report.ok ? report.data.curve : null, projectReply });
    result.innerHTML = renderLookupResult(outcome);
    status.textContent = lookupStatus(outcome);
    if (result.getBoundingClientRect().top > innerHeight * RESULT_IN_VIEW) result.scrollIntoView({ block: "start" });
    if (outcome.ok) history.replaceState(null, "", `${location.pathname}?user=${encodeURIComponent(outcome.result.login)}`);
    running = false;
  });
}
