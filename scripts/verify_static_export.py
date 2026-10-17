"""Check an exported static site against the API it was exported from.

For a grid of requests -- /leaderboard for every persona x every sort (both
directions, and none) x ecosystem/search filters x pages; /not-ranked for
every persona x ecosystem x search; /repos/{owner}/{name} and its /evidence
for every repo (or a sample, --quick) plus unknown ones; /snapshot,
/matrix, /methodology; /reproject for the persona presets, one-hot and
seeded random weight vectors over EVERY page of the full custom ranking;
and the error cases the UI can reach -- this asks the API itself
(Starlette's ``TestClient`` on ``porchlamp.serve.app.create_app`` over the same
database) and the site's ``api.js`` (under Node, reading the exported
files through ``scripts/static_parity.mjs``), and requires identical
answers, compared strictly (``true`` is not ``1``; a missing key is not
``null``).

It then re-runs the grid against deliberately broken copies of ``api.js``
(``MUTANTS``) and requires every one to be caught: a comparison that
cannot fail proves nothing. A mutant whose target text is absent is an
error, never a pass.

    PYTHONPATH=<clean worktree> .venv/bin/python scripts/verify_static_export.py \\
        --db cvi-L4a2.db --site ../cvi-scratch/static-site

With ``--stats`` it also proves the report and methodology pages (``verify_report``): every number on
``black-holes/``, ``black-holes/<snapshot_id>/`` and ``methodology/`` and every row of the CSV is recomputed
from the database, the API and the stats file and compared with what the page prints (set
``PORCHLAMP_DATA_ROOT`` so the API can serve the black-hole evidence); ``--report-only`` skips the request
grid. Whatever the flags, every ``<loc>`` of ``sitemap.xml`` (and, for a sitemap index, of each child sitemap it
names) must have a page in the site.

``verify_projects`` (also run by ``--stats``) proves the project pages and /check/: every ranked project has its page
at ``projects/<owner>/<name>/`` and no other page exists; every value on each page (the two answer sentences, the tier,
the five pillars and their coverage, each lens rank or reason, each parameter's value, count and window, the five similar
projects, the record hash and links) is recomputed from the API and compared; ``sitemap-projects.xml`` lists the top 1,000
by balanced rank; ``check/projects.json`` lists the ranked projects.

``verify_landing`` (also run by ``--stats``) proves the ten landing pages (six ecosystems, four lenses): each intro sentence
and number, each of the top-50 table rows (rank, owner/name, score, tier and link target), the method links, the titles, and
that the sitemap and ``llms.txt`` list them. ``verify_home`` proves the home page's JSON-LD (no SoftwareApplication, no Dataset,
``about`` names the report's Dataset, every ItemList address is a project page of the export) and its ecosystem links.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import importlib.util
import json
import math
import os
import pwd
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[1]
PARITY_JS = REPO_ROOT / "scripts" / "static_parity.mjs"

#: (name, text in api.js, replacement). Each breaks one behaviour the grid
#: is meant to pin; each must be caught.
MUTANTS: tuple[tuple[str, str, str], ...] = (
    ("search ignores case",
     "sqlLower(row.owner).includes(needle) || sqlLower(row.name).includes(needle) || sqlLower(`${row.owner}/${row.name}`).includes(needle)",
     "row.owner.includes(needle) || row.name.includes(needle) || `${row.owner}/${row.name}`.includes(needle)"),
    ("ecosystem filter ignored",
     "(!present(eco) || row.ecosystem === eco)", "true"),
    ("sort order ignored",
     "const order = sort === null ? null : board.orders[sort];", "const order = null;"),
    ("not-ranked never truncated",
     "matching.slice(0, list.max_rows)", "matching.slice(0)"),
    ("not-ranked full list cut at max_rows",
     "const matching = list.rows.filter((entry) => keep(entry.row));\n    const group = (name) => matching.filter(",
     "const matching = list.rows.filter((entry) => keep(entry.row)).slice(0, list.max_rows);\n    const group = (name) => matching.filter("),
    ("repo looked up in the wrong bucket",
     "h = Math.imul(h, 0x85ebca6b) >>> 0;", "h = Math.imul(h, 0x85ebca6d) >>> 0;"),
    ("custom coverage divided by the measured weight",
     "const coverage = Math.min(1, cov / totalWeight);", "const coverage = Math.min(1, cov / (den || totalWeight));"),
    ("custom coverage not clamped to 1",
     "const coverage = Math.min(1, cov / totalWeight);", "const coverage = cov / totalWeight;"),
    ("custom tier ignores the coverage ceiling",
     "base.tier_order[Math.min(scoreTier, ceiling)]", "base.tier_order[scoreTier]"),
    ("custom composite truncated instead of rounded",
     "return x === null ? null : Number(x.toFixed(n));",
     "return x === null ? null : Math.trunc(x * 10 ** n) / 10 ** n;"),
    ("custom ties broken by coverage ascending",
     "descNullsLast(a.coverage, b.coverage)", "ascNullsLast(a.coverage, b.coverage)"),
    ("custom weight total summed left to right (not CPython's compensated sum)",
     "const totalWeight = pySum(w);", "const totalWeight = w.reduce((a, b) => a + b, 0);"),
    ("custom tie groups keep singletons",
     "members.get(v.porchlampDisplay) === 1 ? null : denseRank.get(v.porchlampDisplay)", "denseRank.get(v.porchlampDisplay)"),
)

_NODE_GLOBS = (
    ".local/opt/node-*/bin/node",
    "tools/node-*/bin/node",
    ".nvm/versions/node/*/bin/node",
    "oss-contributions/node-*/node-*/bin/node",
    "node-*/bin/node",
)


def _export_static():
    """``scripts/export_static.py``, loaded by path: putting ``scripts/`` on
    ``sys.path`` would let ``scripts/serve.py`` shadow ``tests/serve`` for
    every later import in the same process."""
    spec = importlib.util.spec_from_file_location("export_static", REPO_ROOT / "scripts" / "export_static.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def find_node() -> Optional[str]:
    """``PATH``, then ``PORCHLAMP_NODE_BIN``, then the usual per-user installs
    (``tests/test_ui_node_suite.py``'s ``find_node``)."""
    on_path = shutil.which("node")
    if on_path:
        return on_path
    override = os.environ.get("PORCHLAMP_NODE_BIN")
    if override and Path(override).is_file():
        return override
    homes = [Path.home()]
    try:
        real = Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:
        real = None
    if real is not None and real not in homes:
        homes.append(real)  # a sandboxed job runs with a throwaway $HOME
    for home in homes:
        for pattern in _NODE_GLOBS:
            for candidate in sorted(home.glob(pattern), reverse=True):
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate)
    for sys_path in ("/opt/homebrew/bin/node", "/usr/local/bin/node"):
        if Path(sys_path).is_file() and os.access(sys_path, os.X_OK):
            return sys_path
    return None


def differs(a: Any, b: Any, path: str = "$") -> Optional[str]:
    """Where two JSON values differ, or ``None``. Strict about types a loose
    ``==`` conflates: a bool only equals a bool, and ``1 == 1.0`` is the
    only cross-type equality allowed (JSON cannot tell those apart)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return None if type(a) is type(b) and a == b else f"{path}: {a!r} != {b!r}"
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return None if a == b else f"{path}: {a!r} != {b!r}"
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            return f"{path}: keys differ: {sorted(set(a) ^ set(b))}"
        for key in a:
            found = differs(a[key], b[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path}: {len(a)} items != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            found = differs(x, y, f"{path}[{i}]")
            if found:
                return found
        return None
    return None if type(a) is type(b) and a == b else f"{path}: {a!r} != {b!r}"


class Grid:
    """Requests, each with the API's own answer."""

    def __init__(self, db: Path) -> None:
        from fastapi.testclient import TestClient

        from porchlamp.serve.app import create_app

        self.client = TestClient(create_app(str(db), web_dir=str(db.parent / "__no_web__")))
        self.requests: list[dict] = []
        self.expected: list[dict] = []

    def _answer(self, response) -> dict:
        if response.status_code == 200:
            return {"ok": True, "data": response.json()}
        return {"ok": False, "status": response.status_code, "detail": response.json()["error"]["detail"]}

    def get(self, kind: str, fn: str, args: list, path: str, params: Optional[dict] = None) -> dict:
        present = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        answer = self._answer(self.client.get(path, params=present))
        self.requests.append({"kind": kind, "fn": fn, "args": args})
        self.expected.append(answer)
        return answer

    def expect(self, kind: str, fn: str, args: list, expected: dict) -> None:
        """A request whose expected answer is not one API call's (it is computed from the database)."""
        self.requests.append({"kind": kind, "fn": fn, "args": args})
        self.expected.append(expected)

    def post_reproject(self, weights: dict, page: Optional[int], page_size: Optional[int]) -> dict:
        params = {k: v for k, v in (("page", page), ("page_size", page_size)) if v is not None}
        answer = self._answer(self.client.post("/v1/porchlamp/reproject", params=params, json=weights))
        options = {k: v for k, v in (("page", page), ("page_size", page_size)) if v is not None}
        self.requests.append({"kind": "reproject", "fn": "postReproject", "args": [weights, options] if options else [weights]})
        self.expected.append(answer)
        return answer


def _leaderboard(grid: Grid, params: dict) -> None:
    grid.get("leaderboard", "fetchLeaderboard", [params], "/v1/porchlamp/leaderboard", params)


def _not_ranked(grid: Grid, params: dict) -> dict:
    return grid.get("not-ranked", "fetchNotRanked", [params], "/v1/porchlamp/not-ranked", params)


def _not_ranked_full(grid: Grid, conn: sqlite3.Connection, snapshot_id: str, params: dict, served: dict) -> None:
    """``fetchNotRankedFull`` returns every matching row. The API cuts its answer at ``MAX_LIMIT``: where it did
    not cut, the two answers are identical; where it did, the whole list comes from the store query and the
    classification the endpoint itself runs (the same ones ``export_static`` uses), with the API's own counts."""
    if not served["ok"]:
        grid.expect("not-ranked-full", "fetchNotRankedFull", [params], served)
        return
    if not served["data"]["truncated"]:
        grid.expect("not-ranked-full", "fetchNotRankedFull", [params], {"ok": True, "data": served["data"]})
        return
    from porchlamp.serve import app as serve_app
    from porchlamp.store import queries

    persona = params.get("persona", "balanced")
    records: list[dict] = []
    while True:
        page, _counts, total = queries.list_not_ranked(
            conn, snapshot_id, persona=persona, ecosystem=params.get("eco"), q=params.get("q"),
            limit=queries.MAX_LIMIT, offset=len(records),
        )
        records.extend(page)
        if len(records) >= total or not page:
            break
    groups: dict[str, list] = {"gated": [], "unrated": [], "persona_hidden": []}
    for record in records:
        group, reason = serve_app._classify_not_ranked(record, persona=persona)
        groups[group].append(serve_app._not_ranked_row(record, group=group, reason=reason).model_dump(mode="json"))
    grid.expect(
        "not-ranked-full", "fetchNotRankedFull", [params],
        {"ok": True, "data": {**groups, "counts": served["data"]["counts"], "truncated": False}},
    )


def _repo(grid: Grid, owner: str, name: str) -> None:
    path = f"/v1/porchlamp/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
    grid.get("repo", "fetchRepoDetail", [owner, name], path)
    grid.get("evidence", "fetchEvidence", [owner, name], f"{path}/evidence")


def _weights(pillars: tuple[str, ...], values: list[float]) -> dict:
    return {f"weight_{p}": v for p, v in zip(pillars, values)}


def build_grid(db: Path, *, quick: bool, seed: int = 20260923) -> Grid:
    from porchlamp.core import constants

    sort_keys = _export_static().sort_keys

    grid = Grid(db)
    pillars = constants.PILLAR_NAMES
    snapshot = grid.get("snapshot", "fetchSnapshot", [], "/v1/porchlamp/snapshot")["data"]
    grid.get("matrix", "fetchMatrix", [], "/v1/porchlamp/matrix")
    grid.get("methodology", "fetchMethodology", [], "/v1/porchlamp/methodology")
    personas = sorted(snapshot["persona_visible_counts"])

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    sid = snapshot["snapshot_id"]
    ecosystems = [e for (e,) in conn.execute(
        "SELECT DISTINCT ecosystem FROM repositories WHERE snapshot_id = ? ORDER BY ecosystem", (sid,))]
    repos = conn.execute(
        "SELECT owner, name FROM repositories WHERE snapshot_id = ? ORDER BY lower(owner || '/' || name)", (sid,)
    ).fetchall()
    rng = random.Random(seed)

    eco_a = ecosystems[0] if ecosystems else "none"
    eco_b = ecosystems[-1] if ecosystems else "none"
    filters = [(None, None), (eco_a, None), (None, "rust"), (eco_b, "a"), (None, "zzz-no-such-repo"), (None, "Go")]
    pages = [(None, None), (2, 50), (1, 1000), (3, 500)]
    sorts = [None] + list(sort_keys(pillars))
    if quick:
        filters, pages = filters[:4], pages[:3]
    for persona in personas:
        for sort in sorts:
            for eco, q in filters:
                for page, page_size in pages:
                    params = {"persona": persona, "eco": eco, "q": q, "sort": sort, "page": page, "page_size": page_size}
                    _leaderboard(grid, {k: v for k, v in params.items() if v is not None})
    for bad in ({"persona": "nope"}, {"sort": "stars"}, {"sort": "-nope", "persona": "nope"},
                {"page": 0}, {"page_size": 1001}, {"page_size": 0}):
        _leaderboard(grid, bad)
    # the Compare screen's own two calls (app.js searchCompare/loadCompareRepo):
    _leaderboard(grid, {"q": "a", "page_size": 20})
    _leaderboard(grid, {"q": f"{repos[0][0]}/{repos[0][1]}" if repos else "x", "page_size": 1000})

    queries = [None, "a", "rust", "zzz", "e", "Go"]
    for persona in personas:
        for eco in [None] + ecosystems:
            for q in queries[: 3 if quick else None]:
                nr_params = {k: v for k, v in (("persona", persona), ("eco", eco), ("q", q)) if v is not None}
                _not_ranked_full(grid, conn, sid, nr_params, _not_ranked(grid, nr_params))
    nope = {"persona": "nope"}
    _not_ranked_full(grid, conn, sid, nope, _not_ranked(grid, nope))
    conn.close()

    sample = repos if not quick else rng.sample(repos, min(len(repos), 150))
    for owner, name in sample:
        _repo(grid, owner, name)
    unknown = [("no-such-owner", "no-such-repo")]
    if repos:
        owner, name = repos[0]
        unknown += [(owner.upper() if owner.upper() != owner else owner + "X", name), (owner, name + "-missing")]
    for owner, name in unknown:
        _repo(grid, owner, name)

    presets = [[constants.PERSONA_PRESETS[p][q] for q in pillars] for p in sorted(constants.PERSONA_PRESETS)]
    one_hot = [[1.0 if i == j else 0.0 for i in range(len(pillars))] for j in range(len(pillars))]
    #: Terms that add up to 1.0000000000000002 left to right but to exactly 1.0 compensated: for a repo measured in
    #: full on every pillar, the coverage is 1.0 only if it is clamped (the API refuses a coverage over 1).
    over_one = [0.25, 0.05, 0.55, 0.05, 0.1]
    vectors = presets + one_hot + [[0.2] * len(pillars), over_one]
    for _ in range(12 if quick else 60):
        raw = [rng.random() ** 2 for _ in pillars]
        if rng.random() < 0.3:  # some pillars weighted out entirely
            raw[rng.randrange(len(raw))] = 0.0
        total = sum(raw)
        vectors.append([x / total for x in raw])
    for values in vectors:
        weights = _weights(pillars, values)
        grid.post_reproject(weights, None, None)  # what the UI sends: page 1 of the default size
        full = grid.post_reproject(weights, 1, 1000)
        total = full["data"]["total"] if full["ok"] else 0
        for page in range(2, total // 1000 + 3):  # every page, and one past the end
            grid.post_reproject(weights, page, 1000)
    for values in ([0.2, 0.2, 0.2, 0.2, 0.1], [-0.1, 0.4, 0.3, 0.2, 0.2]):
        grid.post_reproject(_weights(pillars, values), None, None)
    missing = _weights(pillars, [0.25, 0.25, 0.2, 0.15, 0.15])
    missing.pop(f"weight_{pillars[-1]}")
    grid.post_reproject(missing, None, None)
    extra = _weights(pillars, [0.25, 0.25, 0.2, 0.15, 0.15]) | {"weight_stars": 0.0}
    grid.post_reproject(extra, None, None)
    return grid


def run_static(node: str, site: Path, requests: list[dict], api_module: str = "api.js") -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        req_path, out_path = Path(tmp) / "requests.json", Path(tmp) / "results.json"
        req_path.write_text(json.dumps([{"fn": r["fn"], "args": r["args"]} for r in requests]), encoding="utf-8")
        subprocess.run([node, str(PARITY_JS), str(site), str(req_path), str(out_path), api_module], check=True)
        return json.loads(out_path.read_text(encoding="utf-8"))


def mismatching(grid: Grid, actual: list[dict]) -> dict[int, str]:
    """Request index -> where its answer differs from the API's."""
    return {
        i: where
        for i, (expected, got) in enumerate(zip(grid.expected, actual, strict=True))
        if (where := differs(expected, got))
    }


def compare(grid: Grid, bad: dict[int, str]) -> dict[str, list[str]]:
    """:func:`mismatching`'s result by request kind: each is the request and where it differs."""
    found: dict[str, list[str]] = {}
    for i, where in bad.items():
        request = grid.requests[i]
        found.setdefault(request["kind"], []).append(f"{request['fn']}{json.dumps(request['args'])[:160]}: {where}")
    return found


def verify(db: Path, site: Path, *, quick: bool = False, mutants: bool = True, log=print) -> dict:
    """``{"requests": n, "mismatches": {kind: [...]}, "mutants": {name: answers, or None if its target is
    absent}}``, where a mutant's answers are those it gets wrong that the site itself gets right -- an
    answer the site already gets wrong cannot show a mutant was seen. Passing means no mismatches and
    every mutant caught (:func:`passed`)."""
    node = find_node()
    if node is None:
        raise SystemExit("verify: no Node.js interpreter (PATH, PORCHLAMP_NODE_BIN, ~/.local/opt/node-*)")
    with tempfile.TemporaryDirectory() as tmp:
        # A private copy: the database may be the one being served.
        copy = Path(tmp) / "snapshot.db"
        source, target = sqlite3.connect(f"file:{db}?mode=ro", uri=True), sqlite3.connect(copy)
        source.backup(target)
        source.close()
        target.close()
        grid = build_grid(copy, quick=quick)
    kinds: dict[str, int] = {}
    for request in grid.requests:
        kinds[request["kind"]] = kinds.get(request["kind"], 0) + 1
    log(f"verify: {len(grid.requests)} requests: " + ", ".join(f"{k} {n}" for k, n in sorted(kinds.items())))
    errors = sum(1 for e in grid.expected if not e["ok"])
    log(f"verify: {errors} of them are API errors (the static site must fail the same way)")

    baseline = mismatching(grid, run_static(node, site, grid.requests))
    mismatches = compare(grid, baseline)
    for kind, items in sorted(mismatches.items()):
        log(f"MISMATCH {kind}: {len(items)} of {kinds[kind]}")
        for item in items[:5]:
            log(f"  {item}")
    if not mismatches:
        log("verify: every answer identical to the API's")

    caught: dict[str, Optional[int]] = {}
    if mutants:
        source_text = (site / "api.js").read_text(encoding="utf-8")
        for name, old, new in MUTANTS:
            if source_text.count(old) != 1:
                log(f"MUTANT NOT APPLIED ({name}): target occurs {source_text.count(old)} times in api.js")
                caught[name] = None
                continue
            mutant = site / "api.mutant.js"
            mutant.write_text(source_text.replace(old, new), encoding="utf-8")
            try:
                bad = mismatching(grid, run_static(node, site, grid.requests, "api.mutant.js"))
            finally:
                mutant.unlink()
            caught[name] = len(bad.keys() - baseline.keys())
            log(f"mutant {'caught' if caught[name] else 'SURVIVED'}: {name} ({caught[name]} answers it alone gets wrong)")
    return {"requests": len(grid.requests), "mismatches": mismatches, "mutants": caught}


def passed(result: dict) -> bool:
    return not result["mismatches"] and all(result["mutants"].values())


# --------------------------------------------------------------------------- #
# The report and methodology pages
# --------------------------------------------------------------------------- #
SITE_ORIGIN = "https://porchlamp.org"


def _check_locs(site: Path, locs: list[str], where: str) -> list[str]:
    problems = []
    for loc in locs:
        if not loc.startswith(SITE_ORIGIN + "/") or "?" in loc or "#" in loc:
            problems.append(f"{where} <loc> {loc} is not a canonical address of {SITE_ORIGIN}/")
            continue
        path = loc[len(SITE_ORIGIN) + 1:]
        page = site / path / "index.html" if (path == "" or path.endswith("/")) else site / path
        if not page.is_file():
            problems.append(f"{where} <loc> {loc} has no page in the export ({page.relative_to(site)} is missing)")
    return problems


def check_sitemap(site: Path) -> list[str]:
    """Every ``<loc>`` of ``sitemap.xml`` is a page of the export: the root -> ``index.html``, ``/a/b/`` ->
    ``a/b/index.html``. When ``sitemap.xml`` is a sitemap index, each child sitemap it names is a file of the
    site and every ``<loc>`` of each child is checked the same way. Returns the problems."""
    sitemap = site / "sitemap.xml"
    if not sitemap.is_file():
        return ["sitemap.xml is missing"]
    text = sitemap.read_text(encoding="utf-8")
    locs = re.findall(r"<loc>([^<]+)</loc>", text)
    problems = [] if locs else ["sitemap.xml lists no <loc>"]
    if "<sitemapindex" not in text:
        return problems + _check_locs(site, locs, "sitemap")
    problems += _check_locs(site, locs, "sitemap.xml")
    for loc in locs:
        child = site / loc[len(SITE_ORIGIN) + 1:]
        if not child.is_file() or not loc.endswith(".xml"):
            continue
        child_locs = re.findall(r"<loc>([^<]+)</loc>", child.read_text(encoding="utf-8"))
        if not child_locs:
            problems.append(f"{child.name} lists no <loc>")
        problems += _check_locs(site, child_locs, child.name)
    return problems


def _token_in(text: str, token: str) -> bool:
    """``token`` stands alone in ``text`` (``1,133`` is not found inside ``11,133`` or ``1,1334``)."""
    return re.search(r"(?<![\d,.])" + re.escape(token) + r"(?!\d|[,.]\d)", text) is not None


def _plain(fragment: str) -> str:
    """The text a reader sees: tags become spaces, except ``<wbr>``, which is only a place the line may break."""
    fragment = re.sub(r"<wbr\s*/?>", "", fragment)
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment))).strip()


def _by_id(page: str, ident: str) -> str:
    match = re.search(rf'<(p|svg)[^>]* id="{re.escape(ident)}"[^>]*>(.*?)</\1>', page, re.S)
    return _plain(match.group(2)) if match else ""


def _svg_label(page: str, ident: str) -> str:
    match = re.search(rf'<svg[^>]* id="{re.escape(ident)}"[^>]* aria-label="([^"]*)"', page)
    return html.unescape(match.group(1)) if match else ""


def _section(page: str, ident: str) -> str:
    match = re.search(rf'<h2 id="{re.escape(ident)}">(.*?)(?=<h2 id=|<footer)', page, re.S)
    return match.group(1) if match else ""


def _rows(fragment: str) -> list[list[str]]:
    body = re.search(r"<tbody>(.*?)</tbody>", fragment, re.S)
    rows = re.findall(r"<tr>(.*?)</tr>", body.group(1), re.S) if body else []
    return [[_plain(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S)] for row in rows]


def _items(fragment: str) -> list[str]:
    return [_plain(li) for li in re.findall(r"<li>(.*?)</li>", fragment, re.S)]


class _Ledger:
    """Counts every number compared and keeps the mismatches."""

    def __init__(self) -> None:
        self.checked = 0
        self.errors: list[str] = []

    def token(self, where: str, text: str, token: str) -> None:
        self.checked += 1
        if not _token_in(text, token):
            self.errors.append(f"{where}: expected {token!r}, not found in {text[:140]!r}")

    def contains(self, where: str, text: str, part: str) -> None:
        self.checked += 1
        if part not in text:
            self.errors.append(f"{where}: expected {part!r}, not found in {text[:140]!r}")

    def equal(self, where: str, found: Any, expected: Any) -> None:
        self.checked += 1
        if found != expected:
            self.errors.append(f"{where}: expected {expected!r}, found {found!r}")


#: The ten landing pages, typed again here (double entry against ``porchlamp/serve/landing_pages.py``):
#: slug -> (ecosystem key, name in the title, registry or name in "the judged ... projects").
LANDING_ECOSYSTEMS = {
    "rust": ("crates", "Rust", "crates.io"),
    "python": ("pypi", "Python", "PyPI"),
    "javascript": ("npm", "JavaScript and TypeScript", "npm"),
    "go": ("go", "Go", "Go"),
    "java": ("maven", "Java", "Maven"),
    "c-cpp": ("systems", "C, C++ and Zig", "C, C++ and Zig"),
}
#: slug -> (lens key, the phrase after "projects for").
LANDING_LENSES = {
    "first-timers": ("first_timer", "first-time contributors"),
    "career": ("career_capital", "building career capital"),
    "senior-systems": ("senior_systems", "senior systems engineers"),
    "ai-assisted": ("ai_assisted", "AI-assisted contributors"),
}
ECOSYSTEM_PAGE_SLUGS = {key: slug for slug, (key, _n, _r) in LANDING_ECOSYSTEMS.items()}
LENS_PAGE_SLUGS = {key: slug for slug, (key, _p) in LANDING_LENSES.items()}
LANDING_TOP = 50


def _judged_by_ecosystem(conn: sqlite3.Connection, sid: str) -> dict[str, tuple[int, int]]:
    """``{ecosystem: (judged, black_holes)}``: D-GATE-5 decided projects, by their own SQL on the database."""
    rows = conn.execute(
        "SELECT ecosystem, COUNT(*), SUM(gate_reason = 'black_hole') FROM repositories WHERE snapshot_id = ? "
        "AND (gate_reason IS NULL OR gate_reason IN ('black_hole', 'unrated: no_external_workflow')) "
        "AND COALESCE(gate_undecided, '[]') NOT LIKE '%D-GATE-5%' GROUP BY ecosystem", (sid,),
    ).fetchall()
    return {eco: (n, bh or 0) for eco, n, bh in rows}


def verify_report(db: Path, stats_path: Path, site: Path, *, log=print) -> dict:
    """Recompute every number on the report pages, the methodology page and the CSV from the database, the
    API and the stats file, and compare. ``{"checked": n, "errors": [...]}``; passing means no errors.

    The expected values come from sources the exporter's page code does not read in this function: the
    judged / black-hole counts from their own SQL on the database, the ranked count, the leaderboards and
    the black-hole evidence from the API, the star bands, correlation and corpus from the stats file."""
    from fastapi.testclient import TestClient

    from porchlamp.core import constants
    from porchlamp.serve import report_pages as rp
    from porchlamp.serve.app import create_app

    ledger = _Ledger()
    csv_cells = 0
    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "snapshot.db"
        source, target = sqlite3.connect(f"file:{db}?mode=ro", uri=True), sqlite3.connect(copy)
        source.backup(target)
        source.close()
        target.close()
        conn = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        client = TestClient(create_app(str(copy), web_dir=str(Path(tmp) / "__no_web__")))

        def api(path: str, **params: Any) -> Any:
            response = client.get(path, params=params)
            if response.status_code != 200:
                raise SystemExit(f"verify: GET {path} -> {response.status_code}")
            return response.json()

        snapshot = api("/v1/porchlamp/snapshot")
        methodology = api("/v1/porchlamp/methodology")
        sid = snapshot["snapshot_id"]
        per_eco = _judged_by_ecosystem(conn, sid)
        conn.close()
        judged, black_holes = sum(n for n, _ in per_eco.values()), sum(b for _, b in per_eco.values())
        boards = {}
        for lens in sorted(constants.PERSONA_PRESETS):
            collected, page_no = [], 1
            while True:
                body = api("/v1/porchlamp/leaderboard", persona=lens, page=page_no, page_size=1000)
                collected.extend(body["rows"])
                if len(collected) >= body["total"] or not body["rows"]:
                    break
                page_no += 1
            boards[lens] = collected
        ranked = len(boards["balanced"])
        evidence = []
        for row in conn_names(copy, sid):
            detail = api(f"/v1/porchlamp/repos/{quote(row[0], safe='')}/{quote(row[1], safe='')}")
            if detail["black_hole_evidence"]:
                evidence.append(detail["black_hole_evidence"])
        black_hole_records = len(conn_names(copy, sid))
    if len(evidence) != black_hole_records:
        ledger.errors.append(
            f"only {len(evidence)} of {black_hole_records} black-hole records carry evidence (set PORCHLAMP_DATA_ROOT)"
        )
    unanswered, counted = sum(e["unanswered"] for e in evidence), sum(e["population_n"] for e in evidence)
    window = {(e["window_start"][:10], e["window_end"][:10]) for e in evidence}
    ledger.equal("black-hole records", black_hole_records, black_holes)
    ledger.equal("evidence windows", len(window), 1)
    if len(window) != 1:
        for error in ledger.errors:
            log(f"REPORT MISMATCH {error}")
        return {"checked": ledger.checked, "csv_cells": 0, "errors": ledger.errors}

    fmt, pct0, pct1 = rp.fmt_int, rp.pct0, rp.pct1
    share = pct1(black_holes, judged)
    bands = [(key, stats["by_stars"][key]) for key in rp.STAR_BANDS]
    eco_order = sorted(per_eco, key=lambda e: -per_eco[e][1] / per_eco[e][0])
    corpus = stats["corpus"]
    start, end = sorted(window)[0] if window else ("", "")
    ds, de = (rp._iso_date(start, "start"), rp._iso_date(end, "end")) if window else (None, None)
    gate5 = next(g for g in methodology["gates"] if g["id"] == "D-GATE-5")
    min_n, deadline = gate5["cutoffs"]["min_n"], gate5["cutoffs"]["response_deadline_days"]
    db_sha = hashlib.sha256(db.read_bytes()).hexdigest()
    pages = {"black-holes/index.html": "latest", f"black-holes/{sid}/index.html": "frozen"}
    texts = {}
    for rel, label in pages.items():
        path = site / rel
        if not path.is_file():
            ledger.errors.append(f"{rel} is missing")
            continue
        page = path.read_text(encoding="utf-8")
        texts[label] = page
        w = rel
        when = f"snapshot {sid}" if label == "frozen" else f"{rp._iso_date(sid, 'snapshot id'):%B %Y}"
        ledger.contains(f"{w} <title>", _plain(re.search(r"<title>(.*?)</title>", page, re.S).group(1)), f"({when})")
        description = html.unescape(re.search(r'<meta name="description" content="([^"]*)"', page).group(1))
        ledger.token(f"{w} meta description share", description, share + "%")
        ledger.token(f"{w} meta description projects", description, fmt(corpus["derived_repos"]))
        finding = lambda ident, page=page: _by_id(page, ident)  # noqa: E731
        for token in (fmt(black_holes), fmt(judged), share + "%"):
            ledger.token(f"{w} #finding-share", finding("finding-share"), token)
        ledger.token(f"{w} #finding-share", finding("finding-share"), str(deadline))
        for _key, band in bands:
            ledger.token(f"{w} #finding-stars", finding("finding-stars"), pct0(band["black_holes"], band["n"]) + "%")
        for token in sorted({fmt(n) for key, _ in bands for n in rp._band_bounds(key) if n is not None}):
            ledger.token(f"{w} #finding-stars", finding("finding-stars"), token)
        ledger.token(f"{w} #finding-top100", finding("finding-top100"), str(stats["top100_stars_fate"]["black_hole"]))
        ledger.token(f"{w} #finding-top100", finding("finding-top100"), str(stats["top100_overlap"]))
        ledger.token(f"{w} #finding-correlation", finding("finding-correlation"), fmt(ranked))
        ledger.token(f"{w} #finding-correlation", finding("finding-correlation"), rp.rho_text(stats["spearman"]["porchlamp"]["rho"]))
        ledger.token(f"{w} #finding-prs", finding("finding-prs"), fmt(unanswered))
        ledger.token(f"{w} #finding-prs", finding("finding-prs"), fmt(counted))
        # the charts' text alternatives and the tables behind them
        label_stars = _svg_label(page, "chart-stars")
        for _key, band in bands:
            ledger.token(f"{w} stars chart label", label_stars, pct0(band["black_holes"], band["n"]) + "%")
            ledger.token(f"{w} stars chart label", label_stars, fmt(band["n"]))
        table = _rows(_section(page, "stars"))
        ledger.equal(f"{w} stars table", [r[1:] for r in table], [
            [fmt(b["n"]), fmt(b["black_holes"]), pct0(b["black_holes"], b["n"]) + "%"] for _, b in bands
        ])
        label_eco = _svg_label(page, "chart-ecosystems")
        for eco in eco_order:
            n, bh = per_eco[eco]
            ledger.token(f"{w} ecosystem chart label ({eco})", label_eco, pct1(bh, n) + "%")
            ledger.token(f"{w} ecosystem chart label ({eco})", label_eco, fmt(n))
        table = _rows(_section(page, "ecosystems"))
        ledger.equal(f"{w} ecosystem table", [r[1:] for r in table], [
            [fmt(per_eco[e][0]), fmt(per_eco[e][1]), pct1(per_eco[e][1], per_eco[e][0]) + "%"] for e in eco_order
        ])
        eco_links = re.findall(r'<th scope="row"><a href="([^"]*)">', _section(page, "ecosystems"))
        ledger.equal(f"{w} ecosystem table links to the ecosystem pages", eco_links, [
            f"{'../' if label == 'latest' else '../../'}{ECOSYSTEM_PAGE_SLUGS[e]}/" for e in eco_order
        ])
        # how we measured
        bullets = _items(_section(page, "how-we-measured"))
        ledger.equal(f"{w} how-we-measured bullet count", len(bullets), 5)
        if len(bullets) == 5:
            for token in (fmt(corpus["human_outside_prs"]), fmt(corpus["distinct_outside_authors"]), fmt(corpus["derived_repos"]),
                          str(ds.day), str(de.day), str(de.year)):
                ledger.token(f"{w} how-we-measured bullet 1", bullets[0], token)
            ledger.token(f"{w} how-we-measured bullet 2", bullets[1], str(min_n))
            ledger.token(f"{w} how-we-measured bullet 3", bullets[2], str(deadline))
            ledger.token(f"{w} how-we-measured bullet 3", bullets[2], "95%")
            for name, mon in ((bullets[0], ds.strftime("%B")), (bullets[0], de.strftime("%B"))):
                ledger.checked += 1
                if mon not in name:
                    ledger.errors.append(f"{w} how-we-measured bullet 1: month {mon} not found")
        ledger.token(f"{w} download", _plain(_section(page, "download")), fmt(ranked))
        ledger.contains(f"{w} download", _plain(_section(page, "download")), f"porchlamp-{sid}-ranked.csv")
        cite = _plain(_section(page, "cite"))
        for token in (sid, str(ds.year if ds else "")):
            ledger.token(f"{w} cite", cite, token)
        ledger.equal(f"{w} cite: database sha256", db_sha in cite, True)
        ledger.equal(f"{w} cite: constants hash", snapshot["constants_hash"] in cite, True)
        # canonical, JSON-LD
        canonical = re.search(r'<link rel="canonical" href="([^"]*)"', page).group(1)
        expected_url = f"{SITE_ORIGIN}/black-holes/" + (f"{sid}/" if label == "frozen" else "")
        ledger.equal(f"{w} canonical", canonical, expected_url)
        ld = json.loads(re.search(r'<script type="application/ld\+json">\s*(.*?)\s*</script>', page, re.S).group(1))
        nodes = {n["@type"]: n for n in ld["@graph"]}
        dataset = nodes["Dataset"]
        ledger.equal(f"{w} Dataset license", dataset["license"], "https://creativecommons.org/licenses/by/4.0/")
        ledger.equal(f"{w} Dataset temporalCoverage", dataset["temporalCoverage"], f"{start}/{end}")
        ledger.equal(f"{w} Dataset CSV link", [d["contentUrl"] for d in dataset["distribution"]],
                     [f"{SITE_ORIGIN}/black-holes/{sid}/porchlamp-{sid}-ranked.csv"])
        ledger.equal(f"{w} Dataset encodingFormat", dataset["distribution"][0]["encodingFormat"], "text/csv")
        ledger.token(f"{w} Dataset description share", dataset["description"], share + "%")
        ledger.token(f"{w} Dataset description", dataset["description"], fmt(black_holes))
        ledger.token(f"{w} Dataset description", dataset["description"], fmt(judged))
        ledger.equal(f"{w} Article headline", nodes["Article"]["headline"], rp.REPORT_H1)
        ledger.equal(f"{w} Dataset url", dataset["url"], expected_url)
    if "latest" in texts and "frozen" in texts:
        for ident in ("finding-share", "finding-stars", "finding-top100", "finding-correlation", "finding-prs"):
            ledger.equal(f"latest and frozen agree on #{ident}", _by_id(texts["latest"], ident), _by_id(texts["frozen"], ident))

    # the CSV: every ranked project, each lens rank, no one else
    csv_path = site / "black-holes" / sid / f"porchlamp-{sid}-ranked.csv"
    if csv_path.is_file():
        with csv_path.open(newline="", encoding="utf-8") as handle:
            table = list(csv.reader(handle))
        lenses = sorted(boards)
        ledger.equal("CSV header", table[0], list(rp.CSV_COLUMNS) + [f"rank_{lens}" for lens in lenses])
        ledger.equal("CSV data rows", len(table) - 1, ranked)
        lens_rank = {lens: {(r["owner"], r["name"]): str(r["rank"]) for r in boards[lens]} for lens in lenses}
        wrong = 0
        for row, expected in zip(table[1:], boards["balanced"]):
            want = [str(expected["rank"]), expected["owner"], expected["name"], expected["ecosystem"], str(expected["porchlamp"]),
                    expected["tier"] or "", str(expected["coverage"]), expected["quadrant"] or ""]
            want += [lens_rank[lens].get((expected["owner"], expected["name"]), "") for lens in lenses]
            if row != want:
                wrong += 1
                if wrong <= 3:
                    ledger.errors.append(f"CSV row {row[:3]}: expected {want!r}, found {row!r}")
        csv_cells = ranked * (len(rp.CSV_COLUMNS) + len(lenses))
        if wrong > 3:
            ledger.errors.append(f"CSV: {wrong} rows differ from the API's leaderboards")
    else:
        ledger.errors.append(f"{csv_path.relative_to(site)} is missing")

    # the methodology page
    meth_path = site / "methodology" / "index.html"
    if meth_path.is_file():
        page = meth_path.read_text(encoding="utf-8")
        ledger.equal("methodology canonical", re.search(r'<link rel="canonical" href="([^"]*)"', page).group(1), f"{SITE_ORIGIN}/methodology/")
        text = _plain(page)
        for gate in methodology["gates"]:
            squeeze = lambda s: re.sub(r"[\s`]", "", s)  # noqa: E731
            ledger.equal(f"methodology gate {gate['id']} criterion", squeeze(gate["criterion"]) in squeeze(text), True)
        for key in ("constants_hash", "manifest_root", "canonical_dump_hash", "code_commit", "reproduce_command"):
            ledger.equal(f"methodology {key}", snapshot[key] in text, True)
        ledger.equal("methodology database sha256", db_sha in text, True)
        ledger.contains(
            "methodology check-a-project line", page,
            "To check a project, open https://porchlamp.org/projects/&lt;owner&gt;/&lt;name&gt;/ (lower case), "
            'or search on the <a href="../check/">check page</a>.',
        )
        pillars = _rows(_section(page, "pillars"))
        ledger.equal("methodology pillar weights", [r[1] for r in pillars], [f"{round(p['weight'] * 100)}%" for p in methodology["pillars"]])
        lenses = _rows(_section(page, "lenses"))
        ledger.equal("methodology lens weights", [r[1:] for r in lenses],
                     [[f"{round(w[p['name']] * 100)}%" for p in methodology["pillars"]] for w in methodology["personas"]["presets"].values()])
        ledger.equal("methodology lens names link to the lens pages",
                     re.findall(r'<th scope="row"><a href="([^"]*)">', _section(page, "lenses")),
                     [f"../{LENS_PAGE_SLUGS[name]}/" for name in methodology["personas"]["presets"] if name in LENS_PAGE_SLUGS])
        params = _rows(_section(page, "parameters"))
        ledger.equal("methodology parameter count", len(params), len(methodology["parameters"]))
        ledger.equal("methodology parameter weights", [r[2] for r in params],
                     [f"{round(p['weight_within_pillar'] * 100)}%" for p in methodology["parameters"]])
        ledger.equal("methodology parameter min counts", [r[3] for r in params], ["n/a" if p["min_n"] is None else str(p["min_n"]) for p in methodology["parameters"]])
    else:
        ledger.errors.append("methodology/index.html is missing")

    titles = []
    for rel in (*pages, "methodology/index.html"):
        if (site / rel).is_file():
            titles.append(re.search(r"<title>(.*?)</title>", (site / rel).read_text(encoding="utf-8"), re.S).group(1))
    ledger.equal("the three titles differ", len(set(titles)), 3)
    log(
        f"verify: report pages: checked {ledger.checked} numbers and values on {len(pages) + 1} pages, "
        f"and {csv_cells} CSV cells"
    )
    for error in ledger.errors:
        log(f"REPORT MISMATCH {error}")
    return {"checked": ledger.checked, "csv_cells": csv_cells, "errors": ledger.errors}


# --------------------------------------------------------------------------- #
# The project pages, /check/ and the sitemaps
# --------------------------------------------------------------------------- #
PROJECT_PAGE_LIMIT_IN_SITEMAP = 1000
_BAD_TEXT = re.compile(r"\b(?:null|None|NaN|undefined)\b")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")


def _tofixed(x: float, places: int) -> str:
    """JavaScript's ``toFixed`` for a positive number: the double's exact value, half up (exact rational arithmetic)."""
    from fractions import Fraction

    scaled = math.floor(Fraction(x) * 10 ** places + Fraction(1, 2))
    digits = f"{scaled:0{places + 1}d}"
    return f"{digits[:-places]}.{digits[-places:]}" if places else digits


def _half_up(x: float, scale: int) -> int:
    """``x * scale`` rounded half up, from the digits the record prints (``0.575`` is 57.5%, not 57.49999...%)."""
    from fractions import Fraction

    return math.floor(Fraction(repr(x)) * scale + Fraction(1, 2))


def _tenths(x: float) -> str:
    """``x`` rounded half up to one decimal, a trailing ``.0`` dropped."""
    tenths = _half_up(x, 10)
    return f"{tenths // 10}" if tenths % 10 == 0 else f"{tenths // 10}.{tenths % 10}"


def _reply_time(hours: float) -> str:
    if hours < 48:
        shown = _tenths(hours)
        if shown == "0":
            return "under 0.1 hours"
        return f"{shown} {'hour' if shown == '1' else 'hours'}"
    return f"{_tenths(hours / 24)} days"


def _date_range(start: str, end: str) -> str:
    a, b = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
    left = f"{a.day} {_MONTHS[a.month - 1]}" + ("" if a.year == b.year else f" {a.year}")
    return f"{left} to {b.day} {_MONTHS[b.month - 1]} {b.year}"


def _param_value(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if v == 0:
            return "0"
        if float(v).is_integer():
            return str(int(v))
        if abs(v) < 0.0001:
            return f"{v:.4g}"
        return f"{math.floor(v * 10000 + 0.5) / 10000:.4f}".rstrip("0").rstrip(".")
    return str(v)


def _expected_answer(detail: dict, evidence: list[dict], min_n: dict[str, int]) -> tuple[str, str, str]:
    """``(first sentence, the rest, description)`` of the reply answer, from the API's own evidence rows and the
    methodology's minimum samples."""
    ev = {e["param_id"]: e for e in evidence}
    t, g = ev["ttfhr_hours"], ev["gr14_ghost_rate"]
    t_ok, g_ok = t["unmeasured_reason"] is None, g["unmeasured_reason"] is None
    name = detail["name"]

    def prs(n: int) -> str:
        return f"{n:,} pull request" + ("" if n == 1 else "s")

    if t_ok:
        bracket = f" ({prs(t['n'])}, {_date_range(t['window_start'], t['window_end'])})"
        reply = f"Outside pull requests to {name} got a first human reply in a median of {_reply_time(t['value'])}"
        if g_ok:
            first, rest = f"{reply}{bracket}; {_half_up(g['value'], 100)}% got none within 14 days.", ""
        else:
            first = f"{reply}{bracket}."
            rest = f"The share with no reply within 14 days has too small a sample to measure ({prs(g['n'])} counted; {min_n['gr14_ghost_rate']} needed)."
    elif g_ok:
        bracket = ""
        first = f"The reply time has too small a sample to measure ({prs(t['n'])} counted; {min_n['ttfhr_hours']} needed)."
        noun = "pull request" if g["n"] == 1 else "pull requests"
        rest = f"{_half_up(g['value'], 100)}% of {g['n']:,} outside {noun} got no reply within 14 days ({_date_range(g['window_start'], g['window_end'])})."
    else:
        bracket = ""
        lo, hi = min_n["ttfhr_hours"], min_n["gr14_ghost_rate"]
        needed = f"{lo} needed" if lo == hi else f"{lo} and {hi} needed"
        first = f"The reply time and the share with no reply within 14 days both have too small a sample to measure ({t['n']:,} and {prs(g['n'])} counted; {needed})."
        rest = ""
    if len(first) < 160:
        return first, rest, first
    short = first.replace(bracket, "") if bracket else first
    if len(short) < 160:
        return first, rest, short
    if ";" in short and len(short.split(";")[0] + ".") < 160:
        return first, rest, short.split(";")[0] + "."
    return first, rest, short[:158].rsplit(" ", 1)[0].rstrip(" ;,:") + "\u2026"


def check_sitemap_files(site: Path) -> list[str]:
    """Each child sitemap the index names holds at most 50,000 addresses."""
    sitemap = site / "sitemap.xml"
    if not sitemap.is_file():
        return []
    problems = []
    for loc in re.findall(r"<loc>([^<]+)</loc>", sitemap.read_text(encoding="utf-8")):
        child = site / loc[len(SITE_ORIGIN) + 1:]
        if loc.endswith(".xml") and child.is_file():
            count = len(re.findall(r"<loc>", child.read_text(encoding="utf-8")))
            if count > 50000:
                problems.append(f"{child.name} lists {count} addresses; the limit is 50,000")
    return problems


def verify_projects(db: Path, site: Path, *, log=print) -> dict:
    """Recompute every value on every project page from the API (the leaderboards, ``/repos/{owner}/{name}`` and its
    ``/evidence``) and compare with what the page prints; check the page set, the sitemaps and /check/.
    ``{"checked": n, "pages": m, "errors": [...]}``."""
    from fastapi.testclient import TestClient

    from porchlamp.core import constants
    from porchlamp.serve import project_pages as pp
    from porchlamp.serve import report_pages as rp
    from porchlamp.serve.app import create_app

    ledger = _Ledger()
    export_static = _export_static()
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "snapshot.db"
        source, target = sqlite3.connect(f"file:{db}?mode=ro", uri=True), sqlite3.connect(copy)
        source.backup(target)
        source.close()
        target.close()
        client = TestClient(create_app(str(copy), web_dir=str(Path(tmp) / "__no_web__")))

        def api(path: str, **params: Any) -> Any:
            response = client.get(path, params=params)
            if response.status_code != 200:
                raise SystemExit(f"verify: GET {path} -> {response.status_code}")
            return response.json()

        snapshot = api("/v1/porchlamp/snapshot")
        methodology = api("/v1/porchlamp/methodology")
        boards = {}
        for lens in sorted(constants.PERSONA_PRESETS):
            collected, page_no = [], 1
            while True:
                body = api("/v1/porchlamp/leaderboard", persona=lens, page=page_no, page_size=1000)
                collected.extend(body["rows"])
                if len(collected) >= body["total"] or not body["rows"]:
                    break
                page_no += 1
            boards[lens] = (collected, body["total"])
        records = {}
        for row in boards["balanced"][0]:
            path = f"/v1/porchlamp/repos/{quote(row['owner'], safe='')}/{quote(row['name'], safe='')}"
            records[(row["owner"], row["name"])] = (api(path), api(path + "/evidence"))

    sid, ranked = snapshot["snapshot_id"], boards["balanced"][0]
    as_of = snapshot["as_of"][:10]
    copy_pillars, copy_personas = rp.copy_js_map("PILLAR_NAMES"), rp.copy_js_map("PERSONA_NAMES")
    copy_params, copy_unmeasured = rp.copy_js_map("PARAM_NAMES"), rp.copy_js_map("UNMEASURED_REASON_NAMES")
    gate_copy = rp.copy_js_map("PERSONA_GATE_COPY")
    insufficient = rp.copy_js_string("PERSONA_INSUFFICIENT_EVIDENCE_TEXT")
    capped_text = rp.copy_js_string("TIER_CAPPED_EXPLANATION")
    band_labels = rp.copy_js_map("COVERAGE_BAND_LABELS")
    order = [p["input_id"] for p in methodology["parameters"]]
    min_n = {p["input_id"]: p["min_n"] for p in methodology["parameters"]}
    lens_rank = {lens: {(r["owner"], r["name"]): r["rank"] for r in rows} for lens, (rows, _t) in boards.items()}
    lens_total = {lens: total for lens, (_r, total) in boards.items()}
    by_eco: dict[str, list[int]] = {}  # ecosystem -> places (in the balanced order) of its ranked projects
    for place, r in enumerate(ranked):
        by_eco.setdefault(r["ecosystem"], []).append(place)
    place_of = {(r["owner"], r["name"]): place for place, r in enumerate(ranked)}
    rank_of = {(r["owner"], r["name"]): r["rank"] for r in ranked}

    # The set of pages
    expected_dirs = {f"projects/{o.lower()}/{n.lower()}" for o, n in rank_of}
    ledger.equal("ranked projects equal the balanced leaderboard total", len(rank_of), lens_total["balanced"])
    ledger.equal("no two ranked projects share a lower-case address", len(expected_dirs), len(rank_of))
    found_dirs = {p.parent.relative_to(site).as_posix() for p in (site / "projects").rglob("index.html")} if (site / "projects").is_dir() else set()
    ledger.equal("project pages in the export", len(found_dirs), len(expected_dirs))
    for missing in sorted(expected_dirs - found_dirs)[:5]:
        ledger.errors.append(f"project page {missing}/ is missing")
    for extra in sorted(found_dirs - expected_dirs)[:5]:
        ledger.errors.append(f"project page {extra}/ is not a ranked project")

    titles, canonicals = [], []
    for (owner, name), (detail, evidence) in records.items():
        rel = f"projects/{owner.lower()}/{name.lower()}/index.html"
        who = rel
        path = site / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        plain = _plain(text)
        ledger.checked += 1
        scanned = " ".join(_plain(part) for part in (
            _by_id(text, "answer"), re.search(r'<ul class="facts">(.*?)</ul>', text, re.S).group(1),
            _section(text, "pillars"), _section(text, "lenses")))
        found_bad = _BAD_TEXT.search(scanned.replace(f"{owner}/{name}", "").replace(name, ""))  # a project may be called "null"
        if found_bad:
            ledger.errors.append(f"{who}: the page prints {found_bad.group(0)!r}")
        canonical = re.search(r'<link rel="canonical" href="([^"]*)"', text).group(1)
        ledger.equal(f"{who} canonical", canonical, f"{SITE_ORIGIN}/projects/{owner.lower()}/{name.lower()}/")
        canonicals.append(canonical)
        title = html.unescape(re.search(r"<title>(.*?)</title>", text, re.S).group(1))
        titles.append(title)
        ledger.equal(f"{who} <title>", title, f"{owner}/{name}: pull request response time and contributor experience | Porchlamp")
        ledger.equal(f"{who} <h1>", _plain(re.search(r"<h1>(.*?)</h1>", text, re.S).group(1)), f"{owner}/{name}")
        s1, s1_rest, description = _expected_answer(detail, evidence, min_n)
        found_description = html.unescape(re.search(r'<meta name="description" content="([^"]*)"', text).group(1))
        ledger.equal(f"{who} meta description", found_description, description)
        ledger.equal(f"{who} meta description is under 160 characters", len(found_description) < 160, True)

        # the two answer sentences
        bal = lens_rank["balanced"][(owner, name)]
        first_rank = lens_rank["first_timer"].get((owner, name))
        if first_rank is not None:
            s2 = f"It ranks {bal:,} of {lens_total['balanced']:,} overall and {first_rank:,} of {lens_total['first_timer']:,} for first-time contributors."
        else:
            code = detail["persona_gates"]["first_timer"]
            reason = insufficient if code.startswith("insufficient_evidence:") else gate_copy[code]
            reason = reason.rstrip(".")
            s2 = f"It ranks {bal:,} of {lens_total['balanced']:,} overall. It is not ranked for first-time contributors: {reason[:1].lower() + reason[1:]}."
        ledger.equal(f"{who} #answer", _by_id(text, "answer"), " ".join(part for part in (s1, s1_rest, s2) if part))
        ledger.equal(f"{who} lede", _plain(re.search(r'<p class="lede">(.*?)</p>', text, re.S).group(1)),
                     f"What happens to a stranger's pull request here, in the Porchlamp snapshot of {int(sid[8:10])} {_MONTHS[int(sid[5:7]) - 1]} {sid[:4]}.")

        # rating
        facts = _items(re.search(r'<ul class="facts">(.*?)</ul>', text, re.S).group(1))
        cov = detail["coverage"]
        band = "Full" if cov >= 0.95 else band_labels["A"] if cov >= 0.8 else band_labels["B"] if cov >= 0.6 else "Below floor"
        capped = bool(detail["tier_ceiling"]) and detail["tier"] == detail["tier_ceiling"]
        ledger.equal(f"{who} rating", facts, [
            f"Score: {_tofixed(detail['porchlamp'], 2)}",
            f"Coverage: {math.floor(cov * 100 + 0.5)}% · {band}",
            f"Tier: {detail['tier']}{' (capped)' if capped else ''}",
            f"Quadrant: {detail['quadrant']}",
            f"Ecosystem: {rp.ECOSYSTEM_NAMES[detail['ecosystem']]}",
        ])
        ledger.contains(
            f"{who} ecosystem link", text,
            f'<li>Ecosystem: <a href="../../../{ECOSYSTEM_PAGE_SLUGS[detail["ecosystem"]]}/">',
        )
        ledger.equal(f"{who} capped note", capped_text in plain, capped)
        pillars = _rows(_section(text, "pillars"))
        ledger.equal(f"{who} pillar scores and coverage", pillars, [
            [copy_pillars[p], _tofixed(detail[f"p_{p}"], 1) if detail[f"p_{p}"] is not None else "—",
             f"{math.floor((detail[f'c_{p}'] or 0) * 100 + 0.5)}%"]
            for p in copy_pillars
        ])

        # lens ranks
        expected_lenses = []
        for lens in pp.LENS_ORDER:
            r = lens_rank[lens].get((owner, name))
            if r is not None:
                status = f"Rank {r:,} of {lens_total[lens]:,}"
            else:
                code = detail["persona_gates"][lens]
                status = "Not ranked: " + (insufficient if code.startswith("insufficient_evidence:") else gate_copy[code])
            expected_lenses.append([copy_personas[lens], status])
        ledger.equal(f"{who} lens ranks", _rows(_section(text, "lenses")), expected_lenses)

        # parameters
        details_html = re.search(r"<details>(.*?)</details>", text, re.S).group(1)
        by_id = {e["param_id"]: e for e in evidence}
        expected_params = []
        for pid in order:
            e = by_id[pid]
            if e["unmeasured_reason"]:
                expected_params.append([copy_params[pid], f"Not shown: {copy_unmeasured[e['unmeasured_reason']]}", "—", "—", pp.PARAM_MEANINGS[pid]])
            else:
                window = _date_range(e["window_start"], e["window_end"]) if e["window_start"] and e["window_end"] else "—"
                expected_params.append([copy_params[pid], _param_value(e["value"]), f"{e['n']:,}" if e["n"] else "—", window, pp.PARAM_MEANINGS[pid]])
        ledger.equal(f"{who} parameters", _rows(details_html), expected_params)

        # similar projects: the five nearest by balanced rank in the same ecosystem
        here = place_of[(owner, name)]
        near = sorted((q for q in by_eco[detail["ecosystem"]] if q != here), key=lambda q: (abs(q - here), q))[:5]
        expected_similar = [f"projects/{ranked[q]['owner'].lower()}/{ranked[q]['name'].lower()}/" for q in sorted(near)]
        similar = _section(text, "similar")
        ledger.equal(f"{who} similar projects", re.findall(r'<li><a href="\.\./\.\./\.\./([^"]+)">', similar), expected_similar)
        ledger.equal(f"{who} similar projects' ranks", [int(n.replace(",", "")) for n in re.findall(r"\(rank ([\d,]+)\)", similar)], [ranked[q]["rank"] for q in sorted(near)])
        ledger.equal(f"{who} similar projects' names", re.findall(r'">([^<]+)</a> \(rank', similar), [f"{ranked[q]['owner']}/{ranked[q]['name']}" for q in sorted(near)])

        # evidence and the correction link
        evid = _section(text, "evidence")
        ledger.contains(f"{who} record hash", evid, f"<code>{detail['raw_hash']}</code>")
        bucket = export_static.bucket_of(owner, name)
        ledger.contains(f"{who} JSON record link", evid, f'href="../../../data/{sid}/repos/{bucket}.json"')
        ledger.contains(f"{who} data date", evid, f"data as of {int(as_of[8:10])} {_MONTHS[int(as_of[5:7]) - 1]} {as_of[:4]}")
        ledger.contains(f"{who} issue link", text, 'href="https://github.com/pctablet505/porchlamp/issues/new?title=' + quote(f"Porchlamp record: {owner}/{name}", safe="") + '"')
        for back in ('href="../../../"', 'href="../../../black-holes/"', 'href="../../../methodology/"'):
            ledger.contains(f"{who} link back", text, back)
        ld = json.loads(re.search(r'<script type="application/ld\+json">\s*(.*?)\s*</script>', text, re.S).group(1))
        ledger.equal(f"{who} breadcrumb", ld, {
            "@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Porchlamp", "item": f"{SITE_ORIGIN}/"},
                {"@type": "ListItem", "position": 2, "name": f"{owner}/{name}", "item": canonical}]})
    ledger.equal("project page titles are all different", len(set(titles)), len(titles))
    ledger.equal("project page canonicals are all different", len(set(canonicals)), len(canonicals))

    # sitemaps
    projects_sitemap = site / "sitemap-projects.xml"
    if projects_sitemap.is_file():
        locs = re.findall(r"<loc>([^<]+)</loc>", projects_sitemap.read_text(encoding="utf-8"))
        ledger.equal("sitemap-projects.xml lists the top 1,000 by balanced rank", locs, [
            f"{SITE_ORIGIN}/projects/{r['owner'].lower()}/{r['name'].lower()}/" for r in ranked[:PROJECT_PAGE_LIMIT_IN_SITEMAP]])
        lastmods = set(re.findall(r"<lastmod>([^<]+)</lastmod>", projects_sitemap.read_text(encoding="utf-8")))
        ledger.equal("sitemap-projects.xml lastmod is the snapshot's as_of date", lastmods, {as_of})
    else:
        ledger.errors.append("sitemap-projects.xml is missing")
    for problem in check_sitemap_files(site):
        ledger.errors.append(problem)

    # /check/
    check_page = site / "check" / "index.html"
    if check_page.is_file() and (site / "check" / "projects.json").is_file():
        text = check_page.read_text(encoding="utf-8")
        ledger.equal("check canonical", re.search(r'<link rel="canonical" href="([^"]*)"', text).group(1), f"{SITE_ORIGIN}/check/")
        ledger.contains("check address pattern", _plain(text), "To check a project, open https://porchlamp.org/projects/<owner>/<name>/ (lower case).")
        ledger.equal("check scripts", re.findall(r"<script[^>]*\ssrc=\"([^\"]*)\"", text), ["../check.js"])
        listed = json.loads((site / "check" / "projects.json").read_text(encoding="utf-8"))
        ledger.equal("check index lists the ranked projects in rank order", listed, [f"{r['owner']}/{r['name']}" for r in ranked])
        ledger.equal("check.js ships with the site", (site / "check.js").is_file(), True)
    else:
        ledger.errors.append("check/index.html or check/projects.json is missing")

    log(f"verify: project pages: checked {ledger.checked} values on {len(found_dirs & expected_dirs)} of {len(expected_dirs)} pages")
    for error in ledger.errors[:50]:
        log(f"PROJECT MISMATCH {error}")
    if len(ledger.errors) > 50:
        log(f"PROJECT MISMATCH ... and {len(ledger.errors) - 50} more")
    return {"checked": ledger.checked, "pages": len(found_dirs & expected_dirs), "errors": ledger.errors}


# --------------------------------------------------------------------------- #
# The ten landing pages and the home page
# --------------------------------------------------------------------------- #
_NUMBER_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}


def _weight_percent(weight: float) -> str:
    from decimal import ROUND_HALF_UP, Decimal

    return f"{(Decimal(repr(weight)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)}%"


def _joined(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _landing_table(text: str) -> list[tuple[list[str], Optional[str]]]:
    """``(cells, link target or None)`` of each row of the page's top-50 table."""
    table = re.search(r'<table class="landing-table">.*?</table>', text, re.S)
    body = re.search(r"<tbody>(.*?)</tbody>", table.group(0), re.S) if table else None
    out = []
    for tr in re.findall(r"<tr>(.*?)</tr>", body.group(1), re.S) if body else []:
        cells = [_plain(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S)]
        href = re.search(r'<a href="([^"]*)"', tr)
        out.append((cells, html.unescape(href.group(1)) if href else None))
    return out


def verify_landing(db: Path, site: Path, *, log=print) -> dict:
    """Recompute every number and every table row of the ten landing pages from the database and the API and compare.
    ``{"checked": values, "rows": table rows, "pages": n, "errors": [...]}``."""
    from fastapi.testclient import TestClient

    from porchlamp.core import constants
    from porchlamp.serve import report_pages as rp
    from porchlamp.serve.app import create_app

    ledger = _Ledger()
    table_rows = 0
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "snapshot.db"
        source, target = sqlite3.connect(f"file:{db}?mode=ro", uri=True), sqlite3.connect(copy)
        source.backup(target)
        source.close()
        target.close()
        client = TestClient(create_app(str(copy), web_dir=str(Path(tmp) / "__no_web__")))

        def api(path: str, **params: Any) -> Any:
            response = client.get(path, params=params)
            if response.status_code != 200:
                raise SystemExit(f"verify: GET {path} -> {response.status_code}")
            return response.json()

        snapshot, methodology = api("/v1/porchlamp/snapshot"), api("/v1/porchlamp/methodology")
        sid = snapshot["snapshot_id"]
        conn = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        per_eco = _judged_by_ecosystem(conn, sid)
        conn.close()
        boards = {}
        for lens in sorted(constants.PERSONA_PRESETS):
            collected, page_no = [], 1
            while True:
                body = api("/v1/porchlamp/leaderboard", persona=lens, page=page_no, page_size=1000)
                collected.extend(body["rows"])
                if len(collected) >= body["total"] or not body["rows"]:
                    break
                page_no += 1
            boards[lens] = (collected, body["total"])

    fmt, pct1 = rp.fmt_int, rp.pct1
    month_year = f"{_MONTHS[int(sid[5:7]) - 1]} {sid[:4]}"
    snapshot_day = f"{int(sid[8:10])} {month_year}"
    overall = pct1(sum(b for _, b in per_eco.values()), sum(n for n, _ in per_eco.values()))
    in_balanced = {(r["owner"], r["name"]) for r in boards["balanced"][0]}
    pillar_names = rp.copy_js_map("PILLAR_NAMES")
    pillar_order = [p["name"] for p in methodology["pillars"]]
    csv_name = f"porchlamp-{sid}-ranked.csv"
    ranked = boards["balanced"][1]

    def tier_text(row: dict) -> str:
        if not row["tier"]:
            return "—"
        return f"{row['tier']} (capped)" if row["tier_ceiling"] and row["tier"] == row["tier_ceiling"] else row["tier"]

    expected: dict[str, dict] = {}
    for slug, (key, name, judged_name) in LANDING_ECOSYSTEMS.items():
        cut = [r for r in boards["balanced"][0] if r["ecosystem"] == key]
        judged, holes = per_eco[key]
        rows = [(i + 1, r) for i, r in enumerate(cut[:LANDING_TOP])]
        intro = (
            f"Porchlamp ranks {fmt(len(cut))} {name} projects by what happens to a stranger's pull request. "
            f"Among the {fmt(judged)} judged {judged_name} projects, {pct1(holes, judged)}% left most outside pull requests "
            f"unanswered for two weeks, against {overall}% across all {_NUMBER_WORDS[len(per_eco)]} ecosystems."
        )
        expected[slug] = {
            "title": f"{name} open-source projects that answer outside pull requests ({month_year}) | Porchlamp",
            "h1": f"{name} projects that answer outside pull requests", "intro": intro, "rows": rows, "scope": "by the balanced ranking",
            "numbers": [fmt(len(cut)), fmt(judged), pct1(holes, judged) + "%", overall + "%"],
        }
    for slug, (lens, phrase) in LANDING_LENSES.items():
        weights = methodology["personas"]["presets"][lens]
        ordered = sorted((p for p in pillar_order if weights.get(p)), key=lambda p: (-weights[p], pillar_order.index(p)))
        weights_text = _joined([f"{pillar_names[p].lower()} {_weight_percent(weights[p])}" for p in ordered])
        rows_all, total = boards[lens]
        intro = f"This view ranks {fmt(total)} projects for {phrase}. It weighs {weights_text}."
        expected[slug] = {
            "title": f"Open-source projects for {phrase}, ranked by who answers ({month_year}) | Porchlamp",
            "h1": f"Open-source projects for {phrase}", "intro": intro, "scope": "in this view",
            "rows": [(r["rank"], r) for r in rows_all[:LANDING_TOP]],
            "numbers": [fmt(total)] + [_weight_percent(weights[p]) for p in ordered],
        }

    titles = []
    for slug, want in expected.items():
        rel = f"{slug}/index.html"
        path = site / rel
        if not path.is_file():
            ledger.errors.append(f"{rel} is missing")
            continue
        text = path.read_text(encoding="utf-8")
        ledger.checked += 1
        canonical = re.search(r'<link rel="canonical" href="([^"]*)"', text).group(1)
        ledger.equal(f"{rel} canonical", canonical, f"{SITE_ORIGIN}/{slug}/")
        title = html.unescape(re.search(r"<title>(.*?)</title>", text, re.S).group(1))
        titles.append(title)
        ledger.equal(f"{rel} <title>", title, want["title"])
        ledger.equal(f"{rel} <h1>", _plain(re.search(r"<h1>(.*?)</h1>", text, re.S).group(1)), want["h1"])
        ledger.equal(f"{rel} lede", _plain(re.search(r'<p class="lede">(.*?)</p>', text, re.S).group(1)),
                     f"In the Porchlamp snapshot of {snapshot_day}.")
        intro = _by_id(text, "intro")
        for number in want["numbers"]:
            ledger.token(f"{rel} #intro", intro, number)
        ledger.equal(f"{rel} #intro", intro, want["intro"])
        ledger.equal(f"{rel} meta description", html.unescape(re.search(r'<meta name="description" content="([^"]*)"', text).group(1)), want["intro"])

        # the table: every row, in order
        rows = _landing_table(text)
        ledger.equal(f"{rel} table row count", len(rows), len(want["rows"]))
        names_without_page = []
        for found, (rank, row) in zip(rows, want["rows"]):
            cells, href = found
            owner, name = row["owner"], row["name"]
            linked = (owner, name) in in_balanced
            if not linked:
                names_without_page.append(f"{owner}/{name}")
            who = f"{rel} row {rank} ({owner}/{name})"
            ledger.equal(f"{who} cell count", len(cells), 4)
            cells = (cells + [""] * 4)[:4]
            for label, got, want_cell in (
                ("rank", cells[0], fmt(rank)), ("owner/name", cells[1], f"{owner}/{name}"),
                ("score", cells[2], _tofixed(row["porchlamp"], 2)), ("tier", cells[3], tier_text(row)),
            ):
                ledger.equal(f"{who} {label}", got, want_cell)
            ledger.equal(f"{who} link target", href, f"../projects/{owner.lower()}/{name.lower()}/" if linked else None)
            table_rows += 1
        verb, cause = ("has", "it is") if len(names_without_page) == 1 else ("have", "they are")
        note = f"The table lists the top {fmt(len(want['rows']))} {want['scope']}."
        note += (
            " Each name opens that project's page."
            if not names_without_page
            else f" Each name opens that project's page, except {_joined(names_without_page)}, which {verb} no page "
                 f"because {cause} not in the balanced ranking."
        )
        ledger.equal(f"{rel} #table-note", _by_id(text, "table-note"), note)
        # what the reader is told is also what the page shows
        scanned = " ".join([intro, _by_id(text, "table-note")] + [c for cells, _h in rows for c in (cells[0], *cells[2:])])
        found_bad = _BAD_TEXT.search(scanned)
        if found_bad:
            ledger.errors.append(f"{rel}: the page prints {found_bad.group(0)!r}")
        # links to the method, the report and the ranked table; no script
        method = _section_after(text, "method")
        for target, label in (("../methodology/", "methodology"), ("../black-holes/", "report"),
                              (f"../black-holes/{sid}/{csv_name}", "ranked CSV")):
            ledger.contains(f"{rel} link to the {label}", method, f'href="{target}"')
        ledger.token(f"{rel} ranked count", _plain(method), fmt(ranked))
        ledger.equal(f"{rel} loads no script", re.findall(r"<script[^>]*\ssrc=", text), [])
        ld = json.loads(re.search(r'<script type="application/ld\+json">\s*(.*?)\s*</script>', text, re.S).group(1))
        ledger.equal(f"{rel} breadcrumb", ld, {
            "@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Porchlamp", "item": f"{SITE_ORIGIN}/"},
                {"@type": "ListItem", "position": 2, "name": want["h1"], "item": canonical}]})
    ledger.equal("the ten landing titles differ", len(set(titles)), len(expected))

    # the sitemap and llms.txt list all ten
    sitemap = site / "sitemap-pages.xml"
    locs = re.findall(r"<loc>([^<]+)</loc>", sitemap.read_text(encoding="utf-8")) if sitemap.is_file() else []
    llms = (site / "llms.txt").read_text(encoding="utf-8") if (site / "llms.txt").is_file() else ""
    for slug in expected:
        address = f"{SITE_ORIGIN}/{slug}/"
        ledger.equal(f"sitemap-pages.xml lists {address}", locs.count(address), 1)
        ledger.contains("llms.txt", llms, address)

    log(f"verify: landing pages: checked {ledger.checked} values and {table_rows} table rows on {len(titles)} of {len(expected)} pages")
    for error in ledger.errors[:50]:
        log(f"LANDING MISMATCH {error}")
    if len(ledger.errors) > 50:
        log(f"LANDING MISMATCH ... and {len(ledger.errors) - 50} more")
    return {"checked": ledger.checked, "rows": table_rows, "pages": len(titles), "errors": ledger.errors}


def _section_after(page: str, ident: str) -> str:
    match = re.search(rf'<h2 id="{re.escape(ident)}">(.*?)(?=<footer)', page, re.S)
    return match.group(1) if match else ""


_PROJECT_ADDRESS = re.compile(r"https://porchlamp\.org/projects/([a-z0-9._-]+)/([a-z0-9._-]+)/")


def verify_home(site: Path, *, log=print) -> dict:
    """The home page's JSON-LD and ecosystem links: no SoftwareApplication, no Dataset (the report page carries it),
    the WebPage's ``about`` names the report's Dataset ``@id``, every ItemList address is a project page of this export;
    the static article links the six ecosystem pages."""
    ledger = _Ledger()
    home = site / "index.html"
    report = site / "black-holes" / "index.html"
    if not home.is_file() or not report.is_file():
        return {"checked": 0, "errors": ["index.html or black-holes/index.html is missing"]}
    text = home.read_text(encoding="utf-8")
    ld = json.loads(re.search(r'<script type="application/ld\+json">\s*(.*?)\s*</script>', text, re.S).group(1))
    graph = ld["@graph"]
    types = [n["@type"] for n in graph]
    for gone in ("SoftwareApplication", "Dataset"):
        ledger.equal(f"home JSON-LD has no {gone}", gone in types, False)
    report_ld = json.loads(re.search(r'<script type="application/ld\+json">\s*(.*?)\s*</script>', report.read_text(encoding="utf-8"), re.S).group(1))
    dataset_ids = [n["@id"] for n in report_ld["@graph"] if n["@type"] == "Dataset"]
    ledger.equal("the report page carries one Dataset", len(dataset_ids), 1)
    webpage = next((n for n in graph if n["@type"] == "WebPage"), {})
    ledger.equal("home WebPage about names the report's Dataset @id", webpage.get("about"), {"@id": dataset_ids[0]} if dataset_ids else None)
    items = next((n for n in graph if n["@type"] == "ItemList"), {}).get("itemListElement", [])
    ledger.equal("home ItemList has items", len(items) > 0, True)
    ledger.equal("home ItemList numberOfItems", next((n for n in graph if n["@type"] == "ItemList"), {}).get("numberOfItems"), len(items))
    for item in items:
        url = item.get("url", "")
        match = _PROJECT_ADDRESS.fullmatch(url)
        ledger.equal(f"home ItemList address {url} is a project page address", match is not None, True)
        if match:
            ledger.equal(f"home ItemList address {url} has a page in the export",
                         (site / "projects" / match.group(1) / match.group(2) / "index.html").is_file(), True)
    article = re.search(r'<article class="seo-fallback">(.*?)</article>', text, re.S)
    browse = re.search(r"Browse by ecosystem:(.*?)</p>", article.group(1), re.S) if article else None
    ledger.equal("home article has a 'Browse by ecosystem' line", browse is not None, True)
    if browse:
        ledger.equal("home 'Browse by ecosystem' links", re.findall(r'<a href="([^"]*)">([^<]*)</a>', browse.group(1)), [
            ("rust/", "Rust"), ("python/", "Python"), ("javascript/", "JavaScript and TypeScript"), ("go/", "Go"),
            ("java/", "Java"), ("c-cpp/", "C, C++ and Zig")])
    log(f"verify: home page: checked {ledger.checked} values")
    for error in ledger.errors:
        log(f"HOME MISMATCH {error}")
    return {"checked": ledger.checked, "errors": ledger.errors}


def conn_names(db: Path, snapshot_id: str) -> list[tuple[str, str]]:
    """``(owner, name)`` of the snapshot's black-hole records."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute(
            "SELECT owner, name FROM repositories WHERE snapshot_id = ? AND gate_reason = 'black_hole' ORDER BY owner, name",
            (snapshot_id,),
        ).fetchall()
    finally:
        conn.close()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--quick", action="store_true", help="a smaller grid (a repo sample, fewer filters)")
    parser.add_argument("--no-mutants", action="store_true")
    parser.add_argument("--stats", type=Path, default=None, help="stats file: also prove the report and methodology pages")
    parser.add_argument("--report-only", action="store_true", help="with --stats: only the report pages and the sitemap")
    args = parser.parse_args(argv)
    site = args.site.resolve()
    if args.report_only and args.stats is None:
        parser.error("--report-only needs --stats")
    ok = True
    if not args.report_only:
        ok = passed(verify(args.db.resolve(), site, quick=args.quick, mutants=not args.no_mutants))
    problems = check_sitemap(site)
    for problem in problems:
        print(f"SITEMAP {problem}")
    ok = ok and not problems
    if args.stats is not None:
        ok = not verify_report(args.db.resolve(), args.stats.resolve(), site)["errors"] and ok
        ok = not verify_projects(args.db.resolve(), site)["errors"] and ok
        ok = not verify_landing(args.db.resolve(), site)["errors"] and ok
        ok = not verify_home(site)["errors"] and ok
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
