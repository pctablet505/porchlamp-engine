// check.js: the search box of /check/ (scripts/export_static.py --stats writes the page and projects.json).
//
// projects.json is the list of ranked projects as "owner/name" (real spelling) in rank order. The address of a
// project's page is derived from the name by one rule, the same one the page states: projects/<owner>/<name>/
// in lower case. No library, no outside request: the one fetch is the page's own projects.json.

/** The address, relative to the site root, of a project's page. */
export function projectAddress(fullName) {
  const [owner, name] = String(fullName).split("/");
  return `projects/${encodeURIComponent(owner.toLowerCase())}/${encodeURIComponent(name.toLowerCase())}/`;
}

/** What a person may type or paste, reduced to the text to look for: a GitHub address, spaces and case are dropped. */
export function normalizeQuery(text) {
  return String(text || "")
    .trim()
    .toLowerCase()
    .replace(/^(?:https?:\/\/)?(?:www\.)?github\.com\//, "")
    .replace(/\/+$/, "");
}

/** Up to `limit` projects that match, best first: the exact name, then a prefix of owner/name, a prefix of the name, then anywhere. */
export function matchProjects(names, query, limit = 20) {
  const needle = normalizeQuery(query);
  if (!needle) return [];
  const tiers = [[], [], [], []];
  for (const full of names) {
    const lower = full.toLowerCase();
    const at = lower.indexOf("/");
    const repo = lower.slice(at + 1);
    if (lower === needle) tiers[0].push(full);
    else if (lower.startsWith(needle)) tiers[1].push(full);
    else if (repo.startsWith(needle)) tiers[2].push(full);
    else if (lower.includes(needle)) tiers[3].push(full);
  }
  return tiers.flat().slice(0, limit);
}

function escapeText(text) {
  return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** The result list's markup: real links, so a result can be opened in a new tab. */
export function renderResults(matches, query) {
  if (!normalizeQuery(query)) return "";
  if (matches.length === 0) {
    return `<li class="check-empty">No ranked project matches “${escapeText(query.trim())}”.</li>`;
  }
  return matches.map((full) => `<li><a href="../${escapeText(projectAddress(full))}">${escapeText(full)}</a></li>`).join("");
}

async function boot(doc) {
  const input = doc.querySelector("[data-check-input]");
  const list = doc.querySelector("[data-check-results]");
  if (!input || !list) return;
  let names = null;
  const show = () => {
    list.innerHTML = names ? renderResults(matchProjects(names, input.value), input.value) : "";
  };
  input.addEventListener("input", show);
  try {
    const response = await fetch("projects.json");
    names = await response.json();
  } catch (error) {
    list.innerHTML = '<li class="check-empty">The project list could not be loaded. Open the address above instead.</li>';
    return;
  }
  show();
}

if (typeof document !== "undefined" && typeof window !== "undefined") {
  boot(document);
}
