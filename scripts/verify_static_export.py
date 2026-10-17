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
``--snapshot-dir`` and ``--data-dir``, the files the export read, so the API serves the same black-hole evidence);
``--report-only`` skips the request
grid. ``--stats`` also checks the hand-written numbers of ``index.html`` (the static article, its head and the
top-20 table), ``llms.txt`` and ``feed.xml`` against the database, the API and the stats file
(``verify_hand_written``). Whatever the flags, every ``<loc>`` of ``sitemap.xml`` (and, for a sitemap index, of each child sitemap it
names) must have a page in the site.

``verify_projects`` (also run by ``--stats``) proves the project pages and /check/: every ranked project has its page
at ``projects/<owner>/<name>/`` and no other page exists; every value on each page (the two answer sentences, the tier,
the five pillars and their coverage, each lens rank or reason, each parameter's value, count and window, the five similar
projects, the record hash and links) is recomputed from the API and compared; ``sitemap-projects.xml`` lists the top 1,000
by balanced rank; ``check/projects.json`` lists the ranked projects.

Whatever the flags, ``verify_chrome`` proves every page carries the one header, nav and footer
(``web/app/components/chrome.js``, drawn at export by ``web/prerender.mjs``), one Content-Security-Policy and only the
scripts its kind allows, and ``verify_methodology`` proves the methodology page drawn at export: every value it prints
(a ``data-v`` hook) against the API, its headings and links, and byte for byte against a fresh draw from the API's
own answers.

``verify_landing`` (also run by ``--stats``) proves the ten landing pages (six ecosystems, four lenses): each intro sentence
and number, each of the top-50 table rows (rank, owner/name, score, tier and link target), the method links, the titles, and
that the sitemap and ``llms.txt`` list them. ``verify_home`` proves the home page's JSON-LD (no SoftwareApplication, no Dataset,
``about`` names the report's Dataset, every ItemList address is a project page of the export) and its ecosystem links.
"""
from __future__ import annotations

import argparse
import csv
import functools
import hashlib
import html
import importlib.util
import json
import math
import os
import random
import re
import sqlite3
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from html.parser import HTMLParser
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

@functools.cache
def _export_static():
    """``scripts/export_static.py``, loaded by path: putting ``scripts/`` on
    ``sys.path`` would let ``scripts/serve.py`` shadow ``tests/serve`` for
    every later import in the same process."""
    spec = importlib.util.spec_from_file_location("export_static", REPO_ROOT / "scripts" / "export_static.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def find_node() -> Optional[str]:
    """The Node.js interpreter the export draws its pages with (``scripts/export_static.py`` ``find_node``)."""
    return _export_static().find_node()


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

    def __init__(self, db: Path, evidence_files: Any = None) -> None:
        from fastapi.testclient import TestClient

        from porchlamp.serve.app import create_app

        self.client = TestClient(
            create_app(str(db), web_dir=str(db.parent / "__no_web__"), evidence_files=evidence_files)
        )
        self.db, self.evidence_files = db, evidence_files
        self.requests: list[dict] = []
        self.expected: list[Optional[dict]] = []
        self._later: list[tuple[int, tuple]] = []

    def _answer(self, response) -> dict:
        from porchlamp.serve.inprocess import answer_of

        return answer_of(response)

    def get(self, kind: str, fn: str, args: list, path: str, params: Optional[dict] = None) -> dict:
        present = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        answer = self._answer(self.client.get(path, params=present))
        self.requests.append({"kind": kind, "fn": fn, "args": args})
        self.expected.append(answer)
        return answer

    def later(self, kind: str, fn: str, args: list, method: str, path: str,
              params: Optional[dict] = None, body: Any = None) -> None:
        """A request whose answer the grid does not read: :meth:`resolve` answers them all at once."""
        present = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        self.requests.append({"kind": kind, "fn": fn, "args": args})
        self.expected.append(None)
        self._later.append((len(self.expected) - 1, (method, path, present or None, body)))

    def resolve(self, workers: Optional[int] = None) -> None:
        """Every :meth:`later` request's answer, from the API on ``workers`` processes (``None``: one per CPU)."""
        from porchlamp.serve.inprocess import answers

        found = answers(self.db, [request for _i, request in self._later], evidence_files=self.evidence_files,
                        workers=workers)
        for (index, _request), answer in zip(self._later, found, strict=True):
            self.expected[index] = answer
        self._later = []

    def expect(self, kind: str, fn: str, args: list, expected: dict) -> None:
        """A request whose expected answer is not one API call's (it is computed from the database)."""
        self.requests.append({"kind": kind, "fn": fn, "args": args})
        self.expected.append(expected)

    def post_reproject(self, weights: dict, page: Optional[int], page_size: Optional[int], *, later: bool = False):
        params = {k: v for k, v in (("page", page), ("page_size", page_size)) if v is not None}
        options = {k: v for k, v in (("page", page), ("page_size", page_size)) if v is not None}
        args = [weights, options] if options else [weights]
        if later:
            self.later("reproject", "postReproject", args, "POST", "/v1/porchlamp/reproject", params, weights)
            return None
        answer = self._answer(self.client.post("/v1/porchlamp/reproject", params=params, json=weights))
        self.requests.append({"kind": "reproject", "fn": "postReproject", "args": args})
        self.expected.append(answer)
        return answer


def _leaderboard(grid: Grid, params: dict) -> None:
    grid.later("leaderboard", "fetchLeaderboard", [params], "GET", "/v1/porchlamp/leaderboard", params)


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
    grid.later("repo", "fetchRepoDetail", [owner, name], "GET", path)
    grid.later("evidence", "fetchEvidence", [owner, name], "GET", f"{path}/evidence")


def _weights(pillars: tuple[str, ...], values: list[float]) -> dict:
    return {f"weight_{p}": v for p, v in zip(pillars, values)}


def build_grid(
    db: Path, *, quick: bool, seed: int = 20260923, evidence_files: Any = None, workers: Optional[int] = 1,
) -> Grid:
    """The request grid and the API's answer to each: requests whose answer the grid does not read are answered
    together at the end, on ``workers`` processes (``None``: one per CPU, what ``main`` asks for)."""
    from porchlamp.core import constants

    sort_keys = _export_static().sort_keys

    grid = Grid(db, evidence_files)
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
        grid.post_reproject(weights, None, None, later=True)  # what the UI sends: page 1 of the default size
        full = grid.post_reproject(weights, 1, 1000)
        total = full["data"]["total"] if full["ok"] else 0
        for page in range(2, total // 1000 + 3):  # every page, and one past the end
            grid.post_reproject(weights, page, 1000, later=True)
    for values in ([0.2, 0.2, 0.2, 0.2, 0.1], [-0.1, 0.4, 0.3, 0.2, 0.2]):
        grid.post_reproject(_weights(pillars, values), None, None, later=True)
    missing = _weights(pillars, [0.25, 0.25, 0.2, 0.15, 0.15])
    missing.pop(f"weight_{pillars[-1]}")
    grid.post_reproject(missing, None, None, later=True)
    extra = _weights(pillars, [0.25, 0.25, 0.2, 0.15, 0.15]) | {"weight_stars": 0.0}
    grid.post_reproject(extra, None, None, later=True)
    grid.resolve(workers)
    return grid


def run_static(
    node: str, site: Path, requests: list[dict], api_module: str = "api.js", *, digests_out: Optional[Path] = None,
) -> list[dict]:
    """Every request's answer from the site's ``api_module`` under Node; ``digests_out`` also receives the sha256 of
    each answer's JSON text, for :func:`run_static_changed`."""
    with tempfile.TemporaryDirectory() as tmp:
        req_path, out_path = Path(tmp) / "requests.json", Path(tmp) / "results.json"
        req_path.write_text(json.dumps([{"fn": r["fn"], "args": r["args"]} for r in requests]), encoding="utf-8")
        extra = ["--digests-out", str(digests_out)] if digests_out is not None else []
        subprocess.run([node, str(PARITY_JS), str(site), str(req_path), str(out_path), api_module, *extra], check=True)
        return json.loads(out_path.read_text(encoding="utf-8"))


def run_static_changed(node: str, site: Path, requests: list[dict], api_module: str, digests: Path) -> dict[int, dict]:
    """Request index -> answer, for the answers whose JSON text differs from the run that wrote ``digests``. Every
    other answer is byte-identical to that run's, so it is the same value."""
    with tempfile.TemporaryDirectory() as tmp:
        req_path, out_path = Path(tmp) / "requests.json", Path(tmp) / "results.json"
        req_path.write_text(json.dumps([{"fn": r["fn"], "args": r["args"]} for r in requests]), encoding="utf-8")
        subprocess.run([node, str(PARITY_JS), str(site), str(req_path), str(out_path), api_module,
                        "--differing-from", str(digests)], check=True)
        return dict(json.loads(out_path.read_text(encoding="utf-8")))


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


def verify(
    db: Path, site: Path, *, quick: bool = False, mutants: bool = True, evidence_files: Any = None, log=print,
    workers: Optional[int] = 1,
) -> dict:
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
        grid = build_grid(copy, quick=quick, evidence_files=evidence_files, workers=workers)
    kinds: dict[str, int] = {}
    for request in grid.requests:
        kinds[request["kind"]] = kinds.get(request["kind"], 0) + 1
    log(f"verify: {len(grid.requests)} requests: " + ", ".join(f"{k} {n}" for k, n in sorted(kinds.items())))
    errors = sum(1 for e in grid.expected if not e["ok"])
    log(f"verify: {errors} of them are API errors (the static site must fail the same way)")

    with tempfile.TemporaryDirectory() as tmp:
        digests = Path(tmp) / "digests.json"
        baseline = mismatching(grid, run_static(node, site, grid.requests, digests_out=digests))
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
            applied = [(i, old, new) for i, (_name, old, new) in enumerate(MUTANTS) if source_text.count(old) == 1]

            def wrong_alone(item: tuple[int, str, str]) -> int:
                """How many answers the mutant gets wrong that the site gets right. An answer byte-identical
                to the site's is wrong exactly where the site's is, so only the changed ones are compared."""
                i, old, new = item
                mutant = site / f"api.mutant-{i}.js"
                mutant.write_text(source_text.replace(old, new), encoding="utf-8")
                try:
                    changed = run_static_changed(node, site, grid.requests, mutant.name, digests)
                finally:
                    mutant.unlink()
                bad = {j for j, answer in changed.items() if differs(grid.expected[j], answer)}
                return len(bad - baseline.keys())

            # One Node process per mutant, all at once: each run is independent of the others.
            with ThreadPoolExecutor(max_workers=max(1, min(len(applied), os.cpu_count() or 1))) as pool:
                counts = dict(zip((i for i, _old, _new in applied), pool.map(wrong_alone, applied)))
            for i, (name, old, _new) in enumerate(MUTANTS):
                if i not in counts:
                    log(f"MUTANT NOT APPLIED ({name}): target occurs {source_text.count(old)} times in api.js")
                    caught[name] = None
                    continue
                caught[name] = counts[i]
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


_FINDINGS = ("finding-share", "finding-stars", "finding-top100", "finding-correlation", "finding-prs")


def _report_sentences(
    *, sid: str, judged: int, black_holes: int, deadline: int, min_n: int, bands: list, top100_black_holes: int,
    top100_overlap: int, ranked: int, rho: float, unanswered: int, counted: int, corpus: dict, start: Optional[date],
    end: Optional[date], ecosystems: list, undecided: int = 0,
) -> dict[str, str]:
    """Every report sentence that carries a number, rebuilt here from the data (the page's text after tags are
    dropped; a finding ends with its ``#`` link). Written independently of ``report_pages``' templates, so a
    number the page puts in the wrong place, or a template that does, is a mismatch."""
    from porchlamp.serve import report_pages as rp

    fmt, pct0, pct1 = rp.fmt_int, rp.pct0, rp.pct1
    share = pct1(black_holes, judged)
    (k1, b1), (k2, b2), (k3, b3) = bands
    p1, p2, p3 = (pct0(b["black_holes"], b["n"]) for b in (b1, b2, b3))
    l1, l2, l3 = (rp.band_range_text(k) for k in (k1, k2, k3))
    one_in = judged // black_holes + 1  # the smallest N with N * black_holes > judged
    out = {
        "finding-share": (
            f"More than 1 in {one_in}. In Porchlamp's {sid} snapshot, {fmt(black_holes)} of {fmt(judged)} active open-source "
            f"projects ({share}%) left most outside pull requests with no human reply, review or merge within {deadline} "
            "days. #"
        ),
        "finding-stars": (
            f"More stars, more silence. The share rises with stars: {p1}% of projects with {l1} stars, {p2}% at {l2}, "
            f"and {p3}% at {l3}. #"
        ),
        "finding-top100": (
            f"Fame is no guarantee. {top100_black_holes} of the 100 most-starred projects are black holes, and only "
            f"{top100_overlap} of them reach Porchlamp's top 100. #"
        ),
        "finding-correlation": (
            f"Stars barely predict an answer. Across {fmt(ranked)} ranked projects, the rank correlation between stars "
            f"and the Porchlamp score is {rp.rho_text(rho)}. #"
        ),
        "finding-prs": (
            f"Most of those pull requests waited. Inside the black-hole projects, {fmt(unanswered)} of {fmt(counted)} "
            f"counted outside pull requests got no human reply within {deadline} days. #"
        ),
        "description": (
            f"{share}% of active open-source projects left most outside pull requests unanswered for two weeks. The "
            f"share rises with stars. Data on {fmt(corpus['derived_repos'])} GitHub projects."
        ),
        "dataset": (
            f"{fmt(black_holes)} of {fmt(judged)} active open-source projects ({share}%) left most outside pull requests "
            f"with no human reply, review or merge within {deadline} days, in Porchlamp's {sid} snapshot. The share "
            f"rises with stars: {p1}% at {l1} stars, {p2}% at {l2}, {p3}% at {l3}. Includes the {fmt(ranked)} ranked "
            "projects with rank, score, tier, coverage and quadrant."
        ),
        "chart-stars": "Black-hole share by stars: " + "; ".join(
            f"{pct0(b['black_holes'], b['n'])}% of {fmt(b['n'])} projects with {rp.band_range_text(k)} stars" for k, b in bands
        ) + ".",
        "chart-ecosystems": "Black-hole share by package ecosystem, highest first: " + "; ".join(
            f"{name} {pct1(bh, n)}% of {fmt(n)}" for name, n, bh in ecosystems
        ) + ".",
        "bullet-2": (
            f"A project is judged only when it is licensed, maintained software with at least {min_n} counted outside "
            "pull requests."
        ),
        "bullet-3": (
            "It is a black hole when, with 95% confidence, more than half of those pull requests got no substantive "
            f"reply, review or merge within {deadline} days."
        ),
    }
    if undecided:
        out["bullet-undecided"] = (
            f"Another {fmt(undecided)} licensed, maintained projects with at least {min_n} outside pull requests on "
            "record are not judged yet: those records were fetched before drafts and closes were recorded, and the "
            "rules need both."
        )
    if start is not None and end is not None:
        out["bullet-1"] = (
            f"We followed {fmt(corpus['human_outside_prs'])} pull requests from {fmt(corpus['distinct_outside_authors'])} "
            f"outside contributors to {fmt(corpus['derived_repos'])} GitHub projects, from {start.day} {start:%B} to "
            f"{end.day} {end:%B %Y}."
        )
    return out


def _check_stats_file(ledger: "_Ledger", db: Path, stats: dict, root: Path) -> None:
    """The stats file is what ``porchlamp.snapshot stats`` computes from this database and the data root
    ``root`` (its ``raw/github_meta`` star counts and ``derived`` bundles), field by field, so the star
    figures the pages print are not taken on the stats file's word."""
    from porchlamp.snapshot.stats import StatsError, compute_stats

    try:
        recomputed = compute_stats(db, root)
    except StatsError as error:
        ledger.checked += 1
        ledger.errors.append(f"stats file: cannot recompute it from {db} and {root}: {error}")
        return
    for key in sorted(set(stats) | set(recomputed)):
        if key != "db":
            ledger.equal(f"stats file {key} (recomputed from the database and {root})", stats.get(key), recomputed.get(key))


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
LANDING_TOP = 50


def _judged_by_ecosystem(conn: sqlite3.Connection, sid: str) -> dict[str, tuple[int, int]]:
    """``{ecosystem: (judged, black_holes)}``: D-GATE-5 decided projects, by their own SQL on the database."""
    rows = conn.execute(
        "SELECT ecosystem, COUNT(*), SUM(gate_reason = 'black_hole') FROM repositories WHERE snapshot_id = ? "
        "AND (gate_reason IS NULL OR gate_reason IN ('black_hole', 'unrated: no_external_workflow')) "
        "AND COALESCE(gate_undecided, '[]') NOT LIKE '%D-GATE-5%' GROUP BY ecosystem", (sid,),
    ).fetchall()
    return {eco: (n, bh or 0) for eco, n, bh in rows}


def verify_report(db: Path, stats_path: Path, site: Path, *, evidence_files: Any = None, log=print) -> dict:
    """Recompute every number on the report pages and the CSV from the database, the API and the stats file, and
    compare; the methodology page must be the app's page at its address. ``{"checked": n, "errors": [...]}``; passing means no errors.

    The expected values come from sources the exporter's page code does not read in this function: the
    judged / black-hole counts from their own SQL on the database, the ranked count, the leaderboards and
    the black-hole evidence from the API, the star bands, correlation and corpus from the stats file, which
    must itself equal what ``porchlamp.snapshot stats`` recomputes from the database and the data root.
    Every sentence with a number is compared whole, against the sentence rebuilt from those values
    (:func:`_report_sentences`). ``evidence_files`` (:class:`porchlamp.serve.app.PinnedEvidenceFiles`) are
    the files the export read the black-hole evidence from; their data root is also the stats file's."""
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
        client = TestClient(create_app(str(copy), web_dir=str(Path(tmp) / "__no_web__"), evidence_files=evidence_files))

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
            f"only {len(evidence)} of {black_hole_records} black-hole records carry evidence "
            "(pass --snapshot-dir and --data-dir, the files the export read)"
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
    bands = [(key, stats["by_stars"][key]) for key in rp.STAR_BANDS]
    eco_order = sorted(per_eco, key=lambda e: (-Decimal(per_eco[e][1]) / Decimal(per_eco[e][0]), e))
    corpus = stats["corpus"]
    start, end = sorted(window)[0] if window else ("", "")
    ds, de = (rp._iso_date(start, "start"), rp._iso_date(end, "end")) if window else (None, None)
    gate5 = next(g for g in methodology["gates"] if g["id"] == "D-GATE-5")
    min_n, deadline = gate5["cutoffs"]["min_n"], gate5["cutoffs"]["response_deadline_days"]
    db_sha = hashlib.sha256(db.read_bytes()).hexdigest()
    if evidence_files is None:
        ledger.checked += 1
        ledger.errors.append("stats file: no data root to recompute it from (pass --snapshot-dir and --data-dir)")
    else:
        _check_stats_file(ledger, db, stats, evidence_files.data_dir)
    expected = _report_sentences(
        sid=sid, judged=judged, black_holes=black_holes, deadline=deadline, min_n=min_n, bands=bands,
        top100_black_holes=stats["top100_stars_fate"]["black_hole"], top100_overlap=stats["top100_overlap"],
        ranked=ranked, rho=stats["spearman"]["porchlamp"]["rho"], unanswered=unanswered, counted=counted,
        corpus=corpus, start=ds, end=de, ecosystems=[(rp.ECOSYSTEM_NAMES.get(e, e), *per_eco[e]) for e in eco_order],
        undecided=stats.get("gate5_undecided_rule_inputs", 0),
    )
    bullet_ids = ["bullet-1", "bullet-2", *(["bullet-undecided"] if "bullet-undecided" in expected else []), "bullet-3"]
    # the sentences that hold only for some numbers
    band_shares = [Decimal(b["black_holes"]) / Decimal(b["n"]) for _, b in bands]
    ledger.equal("'the share rises with stars' holds", all(a < b for a, b in zip(band_shares, band_shares[1:])), True)
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
        ledger.equal(f"{w} meta description", description, expected["description"])
        # each finding, whole: a number in the wrong place is a different sentence
        for ident in _FINDINGS:
            ledger.equal(f"{w} #{ident}", _by_id(page, ident), expected[ident])
        # the charts' text alternatives and the tables behind them
        ledger.equal(f"{w} stars chart label", _svg_label(page, "chart-stars"), expected["chart-stars"])
        table = _rows(_section(page, "stars"))
        ledger.equal(f"{w} stars table", [r[1:] for r in table], [
            [fmt(b["n"]), fmt(b["black_holes"]), pct0(b["black_holes"], b["n"]) + "%"] for _, b in bands
        ])
        ledger.equal(f"{w} ecosystem chart label", _svg_label(page, "chart-ecosystems"), expected["chart-ecosystems"])
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
        ledger.equal(f"{w} how-we-measured bullet count", len(bullets), len(bullet_ids) + 2)
        if len(bullets) == len(bullet_ids) + 2:
            for ident, bullet in zip(bullet_ids, bullets):
                ledger.equal(f"{w} how-we-measured {ident}", bullet, expected[ident])
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
        ledger.equal(f"{w} Dataset description", dataset["description"], expected["dataset"])
        ledger.equal(f"{w} Article description", nodes["Article"]["description"], expected["description"])
        ledger.equal(f"{w} Article headline", nodes["Article"]["headline"], rp.REPORT_H1)
        ledger.equal(f"{w} Dataset url", dataset["url"], expected_url)
    if "latest" in texts and "frozen" in texts:
        for ident in _FINDINGS:
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

    # the methodology page is the app's own view (web/app/routes.js), drawn in the browser from methodology.json and
    # snapshot.json, which the request grid compares with the API: the page is the app at the view's address
    meth_path = site / "methodology" / "index.html"
    if meth_path.is_file():
        page = meth_path.read_text(encoding="utf-8")
        ledger.equal("methodology canonical", re.search(r'<link rel="canonical" href="([^"]*)"', page).group(1), f"{SITE_ORIGIN}/methodology/")
        ledger.equal("methodology is the app's page", '<script type="module" src="/app.js"></script>' in page, True)
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
# Every page: the one chrome, the one policy, the scripts its kind allows; the methodology view
# --------------------------------------------------------------------------- #
#: The empty frame web/app/404.html gives every page, which web/prerender.mjs fills (web/app/components/chrome.js).
_CHROME_ELEMENTS = (
    ("header", '<header class="app-header" id="app-header-controls">', "</header>"),
    ("nav", '<nav class="app-primary-nav" id="app-primary-nav" aria-label="{nav_label}">', "</nav>"),
    ("footer", '<footer class="app-footer" id="app-footer">', "</footer>"),
)
_CSP = re.compile(r'<meta http-equiv="Content-Security-Policy" content="([^"]*)">')
_SCRIPT_TAG = re.compile(r"<script\b([^>]*)>")


class _PageReader(HTMLParser):
    """A page as a reader without JavaScript gets it, through Python's own HTML parser: every ``data-v`` hook (its id and
    the text inside it), the h1 and h2 headings, every element id and every link."""

    _VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hooks: list[tuple[str, str]] = []
        self.headings: list[tuple[str, str]] = []
        self.ids: set[str] = set()
        self.links: list[str] = []
        self._open: list[tuple[str, Optional[str], list[str]]] = []  # (tag, hook id or heading, text so far)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        found = dict(attrs)
        if found.get("id"):
            self.ids.add(found["id"])
        if tag == "a" and found.get("href") is not None:
            self.links.append(found["href"])
        if tag in self._VOID:
            return
        mark = f"v:{found['data-v']}" if found.get("data-v") is not None else (f"h:{tag}" if tag in ("h1", "h2") else None)
        self._open.append((tag, mark, []))

    def handle_endtag(self, tag: str) -> None:
        if all(open_tag != tag for open_tag, _mark, _text in self._open):
            return  # a stray end tag closes nothing
        while self._open:
            open_tag, mark, parts = self._open.pop()
            text = "".join(parts)
            if mark and mark.startswith("v:"):
                self.hooks.append((mark[2:], text))
            elif mark:
                self.headings.append((mark[2:], re.sub(r"\s+", " ", text).strip()))
            if self._open:
                self._open[-1][2].append(text)
            if open_tag == tag:
                return

    def handle_data(self, data: str) -> None:
        if self._open:
            self._open[-1][2].append(data)


def read_page(text: str) -> _PageReader:
    reader = _PageReader()
    reader.feed(text)
    reader.close()
    return reader


def _js_number(value: Any) -> str:
    """JavaScript's ``String(value)`` for a JSON number (``3.0`` is ``3``, ``1e-7`` is ``1e-7``), or the value's text."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if not isinstance(value, (int, float)):
        return str(value)
    if value == 0:
        return "0"
    exact = Decimal(repr(value)).normalize()
    sign, digits, exponent = exact.as_tuple()
    k, n = len(digits), len(digits) + exponent
    text = "".join(map(str, digits))
    if k <= n <= 21:
        out = text + "0" * (n - k)
    elif 0 < n <= 21:
        out = f"{text[:n]}.{text[n:]}"
    elif -6 < n <= 0:
        out = "0." + "0" * -n + text
    else:
        out = (f"{text[0]}.{text[1:]}" if k > 1 else text) + f"e{'+' if n > 0 else '-'}{abs(n - 1)}"
    return ("-" if sign else "") + out


def _js_percent(fraction: Optional[float]) -> str:
    """``copy.methodologyPercentText``: ``Math.round(fraction * 100)`` and a percent sign, or a dash."""
    if fraction is None:
        return "—"
    return f"{math.floor(Decimal(fraction * 100) + Decimal('0.5'))}%"


def _js_fixed(value: float, places: int) -> str:
    """JavaScript's ``Number.prototype.toFixed``: the exact binary value, halves rounded up."""
    return str(Decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def methodology_hooks(snapshot: dict, methodology: dict, source_db_sha256: Optional[str], pillar_order: list[str]) -> dict[str, str]:
    """Every hook the methodology view prints, with the value it must hold, worked out from the API's answers and the
    database file's digest with this script's own formatters (never from the exported files)."""
    m, s = methodology, snapshot
    hooks = {
        "methodology.benchmark_version": m["benchmark_version"],
        "methodology.schema_version": _js_number(m["schema_version"]),
        "methodology.constants_hash": m["constants_hash"][:12],
        "snapshot.snapshot_id": s["snapshot_id"],
        "snapshot.as_of": s["as_of"],
        "snapshot.reproduce_command": s["reproduce_command"],
    }
    for key in ("manifest_root", "constants_hash", "canonical_dump_hash", "code_commit"):
        if s.get(key) not in (None, ""):
            hooks[f"snapshot.{key}"] = s[key]
    if source_db_sha256:
        hooks["snapshot.source_db_sha256"] = source_db_sha256
    counts = s["counts"]
    total = counts.get("total") or 0
    for key in ("rated", "gated", "unrated"):
        hooks[f"counts.{key}"] = _js_number(counts[key])
        hooks[f"counts.{key}.share"] = _js_fixed(counts[key] * 100 / total if total > 0 else 0, 1) + "%"
    for name, count in (s.get("detector_summary") or {}).items():
        hooks[f"detector.{name}.flagged"] = f"{_js_number(count)} {'row' if count == 1 else 'rows'}"
    for key, items in (
        ("gates", m["gates"]), ("pillars", m["pillars"]), ("parameters", m["parameters"]),
        ("missingness", m["missingness"]["reasons"]), ("personas", m["personas"]["presets"]),
        ("tie_break", m["tie_break_levels"]["levels"]), ("detectors", m["detectors"]),
        ("process_constants", m["not_hashed_process_constants"]), ("rfc_log", m["rfc_log"]),
    ):
        hooks[f"section.{key}.count"] = _js_number(len(items))
    for gate in m["gates"]:
        hooks[f"gate.{gate['id']}.criterion"] = re.sub(r"`([^`]+)`", r"\1", gate["criterion"])
        for key, value in (gate.get("cutoffs") or {}).items():
            hooks[f"gate.{gate['id']}.cutoff.{key}"] = _js_number(value)
    for pillar in m["pillars"]:
        hooks[f"pillar.{pillar['name']}.weight"] = _js_percent(pillar["weight"])
    for p in m["parameters"]:
        hooks[f"parameter.{p['input_id']}.weight"] = _js_percent(p["weight_within_pillar"])
        hooks[f"parameter.{p['input_id']}.min_n"] = "—" if p["min_n"] is None else _js_number(p["min_n"])
        hooks[f"parameter.{p['input_id']}.window"] = "—" if p["window_days"] is None else f"{_js_number(p['window_days'])} days"
    missing = m["missingness"]
    for key, value in (missing.get("coverage_thresholds") or {}).items():
        hooks[f"missingness.{key}"] = _js_percent(value)
    signals = missing.get("openssf_proxy_min_signals")
    hooks["missingness.openssf_proxy_min_signals"] = "—" if signals is None else _js_number(signals)
    for lens, weights in m["personas"]["presets"].items():
        for pillar in pillar_order:
            hooks[f"persona.{lens}.{pillar}"] = _js_percent(weights.get(pillar))
    for gate in m["personas"].get("gates") or []:
        for key, value in (gate.get("thresholds") or {}).items():
            hooks[f"persona.{gate['persona']}.threshold.{key}"] = _js_number(value)
    for detector in m["detectors"]:
        for key, value in (detector.get("thresholds") or {}).items():
            hooks[f"detector.{detector['name']}.threshold.{key}"] = _js_number(value)
    for constant in m["not_hashed_process_constants"]:
        hooks[f"constant.{constant['name']}.value"] = _js_number(constant["value"])
    return hooks


def _compare_hooks(ledger: "_Ledger", where: str, found: list[tuple[str, str]], expected: dict[str, str]) -> None:
    """Each hook id printed at least once, every printing of it equal to its value, and no hook this check does not know."""
    by_id: dict[str, list[str]] = {}
    for ident, value in found:
        by_id.setdefault(ident, []).append(value)
    for ident, value in expected.items():
        ledger.checked += 1
        shown = by_id.get(ident)
        if not shown:
            ledger.errors.append(f"{where}: no hook {ident!r} (expected {value!r})")
        elif any(v != value for v in shown):
            ledger.errors.append(f"{where}: hook {ident!r} shows {sorted(set(shown))!r}, expected {value!r}")
    for ident in sorted(set(by_id) - set(expected)):
        ledger.errors.append(f"{where}: hook {ident!r} has no expected value here")


def _api_answers(db: Path, *paths: str) -> list[Any]:
    """The API's answers to ``paths``, over a private copy of ``db``."""
    from fastapi.testclient import TestClient

    from porchlamp.serve.app import create_app

    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "snapshot.db"
        source, target = sqlite3.connect(f"file:{db}?mode=ro", uri=True), sqlite3.connect(copy)
        source.backup(target)
        source.close()
        target.close()
        client = TestClient(create_app(str(copy), web_dir=str(Path(tmp) / "__no_web__")))
        answers = []
        for path in paths:
            response = client.get(path)
            if response.status_code != 200:
                raise SystemExit(f"verify: GET {path} -> {response.status_code}")
            answers.append(response.json())
    return answers


def _page_kinds(site: Path, report_ids: list[str]) -> dict[str, dict]:
    """Each page of the site (every ``index.html``, and ``404.html``), with what the export drew it as: a page of the
    app (``view``) or another page (``nav``, its nav entry or None) of a script ``kind`` (``report_pages.PAGE_SCRIPTS``)."""
    pages: dict[str, dict] = {"404.html": {"view": "notfound", "kind": "app"}}
    for route in _export_static().app_routes():
        if route["shell"]:
            pages[f"{route['path'].strip('/')}/index.html".lstrip("/")] = {"view": route["id"], "kind": "app"}
    for path in sorted(site.rglob("index.html")):
        rel = path.relative_to(site).as_posix()
        if rel.startswith("data/") or rel in pages:
            continue
        if rel.startswith("black-holes/") and any(rel == f"black-holes/{i}index.html" for i in report_ids):
            pages[rel] = {"nav": "report", "kind": "page"}
        elif rel.startswith("projects/"):
            pages[rel] = {"nav": None, "kind": "project"}
        elif rel == "check/index.html":
            pages[rel] = {"nav": None, "kind": "check"}
        else:
            pages[rel] = {"nav": None, "kind": "page"}
    return pages


def verify_chrome(db: Path, site: Path, *, log=print) -> dict:
    """Every page the site serves carries the one header, nav and footer (``web/app/components/chrome.js``, drawn at
    export by ``web/prerender.mjs``) for its kind and nav entry, from this database's snapshot; the snapshot values the
    chrome prints (hooks) equal the API's; one Content-Security-Policy, the same everywhere (``web/app/404.html``'s);
    ``theme-init.js`` first in ``<head>``; and exactly the scripts its kind allows (``report_pages.PAGE_SCRIPTS``),
    with no inline script but JSON-LD. ``{"checked", "pages", "errors"}``."""
    from porchlamp.serve import report_pages as rp

    ledger = _Ledger()
    node = find_node()
    if node is None:
        raise SystemExit("verify: no Node.js interpreter (PATH, PORCHLAMP_NODE_BIN, ~/.local/opt/node-*)")
    [snapshot] = _api_answers(db, "/v1/porchlamp/snapshot")
    sid = snapshot["snapshot_id"]
    pages = _page_kinds(site, ["", f"{sid}/"])
    policy = _CSP.findall((REPO_ROOT / "web" / "app" / "404.html").read_text(encoding="utf-8"))
    nav_label = html.escape(rp.copy_js_string("PRIMARY_NAV_LABEL"))
    expected_hooks = {
        "header.snapshot_id": sid, "footer.snapshot_id": sid, "footer.benchmark_version": snapshot["benchmark_version"],
        "footer.constants_hash": snapshot["constants_hash"][:12], "footer.manifest_root": snapshot["manifest_root"][:12],
    }
    drawn: dict[str, dict] = {}
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
        for page in pages.values():
            key = json.dumps({k: page[k] for k in ("view", "nav") if k in page}, sort_keys=True)
            if key in drawn:
                continue
            (Path(tmp) / "page.json").write_text(key, encoding="utf-8")
            ran = subprocess.run(
                [node, str(REPO_ROOT / "web" / "prerender.mjs"), "--chrome", str(Path(tmp) / "snapshot.json"), str(Path(tmp) / "page.json")],
                capture_output=True, text=True, timeout=120, check=False,
            )
            if ran.returncode != 0:
                raise SystemExit(f"verify: web/prerender.mjs --chrome failed: {ran.stderr[-1500:]}")
            drawn[key] = json.loads(ran.stdout)
    counts: dict[str, int] = {}
    for rel, page in pages.items():
        path = site / rel
        if not path.is_file():
            ledger.errors.append(f"{rel} is missing")
            continue
        text = path.read_text(encoding="utf-8")
        counts[page["kind"]] = counts.get(page["kind"], 0) + 1
        want = drawn[json.dumps({k: page[k] for k in ("view", "nav") if k in page}, sort_keys=True)]
        for name, opening, closing in _CHROME_ELEMENTS:
            opening = opening.format(nav_label=nav_label)
            ledger.equal(f"{rel} {name}", text.count(opening), 1)
            start = text.find(opening)
            end = text.find(closing, start) if start >= 0 else -1
            inner = text[start + len(opening) : end] if end >= 0 else None
            ledger.equal(f"{rel} {name} is chrome.js's", inner, want[name])
        hooks = [hook for hook in read_page(text).hooks if hook[0].split(".")[0] in ("header", "footer")]
        _compare_hooks(ledger, rel, hooks, expected_hooks)
        ledger.equal(f"{rel} Content-Security-Policy", _CSP.findall(text), policy)
        head = text[: text.find("</head>")]
        theme = head.find('<script src="/theme-init.js"></script>')
        ledger.equal(f"{rel} theme-init.js in <head>, before the stylesheets", 0 <= theme < head.find('<link rel="stylesheet"'), True)
        scripts = [re.search(r'\ssrc="([^"]*)"', attrs) for attrs in _SCRIPT_TAG.findall(text)]
        ledger.equal(f"{rel} scripts", [m.group(1) for m in scripts if m], list(rp.PAGE_SCRIPTS[page["kind"]]))
        inline = [attrs for attrs, m in zip(_SCRIPT_TAG.findall(text), scripts) if not m and 'type="application/ld+json"' not in attrs]
        ledger.equal(f"{rel} inline scripts", inline, [])
    log(
        f"verify: page chrome: {sum(counts.values())} pages ("
        + ", ".join(f"{kind} {n}" for kind, n in sorted(counts.items()))
        + f"), each with the header, nav and footer chrome.js draws, one policy, its kind's scripts; "
        f"{len(expected_hooks)} hooks per page; checked {ledger.checked}"
    )
    for error in ledger.errors[:50]:
        log(f"CHROME MISMATCH {error}")
    if len(ledger.errors) > 50:
        log(f"CHROME MISMATCH ... and {len(ledger.errors) - 50} more")
    return {"checked": ledger.checked, "pages": sum(counts.values()), "errors": ledger.errors}


#: The static methodology page's anchors, which the view keeps (old deep links still land).
METHODOLOGY_FORMER_IDS = ("gates", "pillars", "parameters", "missing-data", "lenses", "tie-break", "limits", "reproduce", "hashes")
#: The view's sections, in page order, by their copy.js label names.
METHODOLOGY_SECTIONS = (
    "SNAPSHOT", "GATES", "PILLARS", "PARAMETERS", "MISSINGNESS", "PERSONAS", "TIE_BREAK", "DETECTORS", "LIMITS",
    "PROCESS_CONSTANTS", "RFC_LOG",
)


def verify_methodology(db: Path, site: Path, *, log=print) -> dict:
    """``/methodology/``, the app's own view, drawn into the page at export so it reads without JavaScript: every hook
    it prints equals the value worked out here from the API and the database file (:func:`methodology_hooks`); its h1
    and h2 are the view's; the lens names link the lens pages, the check line links /check/, the old anchors stay and
    the Limits are copy.js's; and the page is byte for byte what ``web/prerender.mjs`` draws from the API's own answers
    (ENG-17). ``{"checked", "hooks", "errors"}``."""
    from porchlamp.serve import report_pages as rp

    ledger = _Ledger()
    path = site / "methodology" / "index.html"
    if not path.is_file():
        return {"checked": 0, "hooks": 0, "errors": ["methodology/index.html is missing"]}
    text = path.read_text(encoding="utf-8")
    snapshot, methodology = _api_answers(db, "/v1/porchlamp/snapshot", "/v1/porchlamp/methodology")
    db_sha = hashlib.sha256(db.read_bytes()).hexdigest()
    page = read_page(text)
    hooks = [hook for hook in page.hooks if hook[0].split(".")[0] not in ("header", "footer")]  # the chrome's: verify_chrome
    expected = methodology_hooks(snapshot, methodology, db_sha, list(rp.copy_js_map("PILLAR_NAMES")))
    _compare_hooks(ledger, "methodology", hooks, expected)
    labels = [rp.copy_js_string(f"METHODOLOGY_SECTION_{name}") for name in METHODOLOGY_SECTIONS]
    ledger.equal("methodology h1", [t for tag, t in page.headings if tag == "h1"], [rp.copy_js_string("METHODOLOGY_HEADING")])
    ledger.equal("methodology h2", [t for tag, t in page.headings if tag == "h2"], [rp.copy_js_string("WHY_HEADING"), *labels])
    lens_links = [f"/{rp.LENS_SLUGS[lens]}/" for lens in methodology["personas"]["presets"] if lens in rp.LENS_SLUGS]
    ledger.equal("methodology links the lens pages", [link for link in page.links if link in lens_links], lens_links)
    ledger.equal("methodology links /check/", "/check/" in page.links, True)
    gate_ids = [gate["id"].lower() for gate in methodology["gates"]]
    ledger.equal("methodology keeps the old anchors", sorted(set(METHODOLOGY_FORMER_IDS + tuple(gate_ids)) - page.ids), [])
    limits = re.search(r'id="limits">.*?<ul class="spec-rules">(.*?)</ul>', text, re.S)
    ledger.equal("methodology Limits are copy.js's", _items(limits.group(1)) if limits else None, rp.copy_js_list("LIMITS"))
    # the page again, from the API's answers and the verifier's own digest of the database: the export's page
    # skeleton, drawn by the prerender through the site's api.js
    export_static = _export_static()
    node = find_node()
    route = next(r for r in export_static.app_routes() if r["id"] == "methodology")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data = root / "data" / snapshot["snapshot_id"]
        data.mkdir(parents=True)
        for name, payload in (("snapshot", snapshot), ("methodology", methodology), ("manifest", {"source_db_sha256": db_sha})):
            (data / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
        (root / "api.js").write_bytes(export_static.STATIC_API.read_bytes())
        (root / "data-version.js").write_text(export_static.data_version_js(snapshot["snapshot_id"]), encoding="utf-8")
        (root / "methodology").mkdir()
        (root / "methodology" / "index.html").write_text(export_static.view_page(route), encoding="utf-8")
        (root / "job.json").write_text(json.dumps({"pages": [{"path": "methodology/index.html", "view": "methodology"}]}),
                                       encoding="utf-8")
        ran = subprocess.run([node, str(REPO_ROOT / "web" / "prerender.mjs"), str(root), str(root / "job.json")],
                             capture_output=True, text=True, timeout=120, check=False)
        again = (root / "methodology" / "index.html").read_text(encoding="utf-8") if ran.returncode == 0 else ran.stderr
    ledger.equal("methodology is what the prerender draws from the API's answers (bytes)", again == text, True)
    distinct = len({ident for ident, _v in hooks})
    log(f"verify: methodology page: {len(hooks)} hooks ({distinct} distinct), checked {ledger.checked} values; "
        f"re-drawn from the API {'byte-identical' if again == text else 'DIFFERENT'}")
    for error in ledger.errors[:50]:
        log(f"METHODOLOGY MISMATCH {error}")
    return {"checked": ledger.checked, "hooks": len(hooks), "errors": ledger.errors}


# --------------------------------------------------------------------------- #
# The hand-written numbers: the home page's static article, llms.txt, feed.xml
# --------------------------------------------------------------------------- #
#: A count or a plain number, as the hand-written files print one (``11,000``, ``823``, ``9``).
_N = r"(\d{1,3}(?:,\d{3})+|\d+)"
#: A percentage's digits (``26.4`` of ``26.4%``).
_P = r"(\d+(?:\.\d+)?)%"
_BAND_TEXT = r"{p} (?:of projects with|at) 1k\u201310k stars, {p} at 10k\u201350k(?: stars)?, and {p} at 50k"


class _Prefix:
    """A truncated hash (``6e4f3e8e1d0c…``): at least 8 hex digits that begin the full value."""

    def __init__(self, full: str) -> None:
        self.full = full

    def __call__(self, found: str) -> bool:
        return len(found) >= 8 and self.full.startswith(found)

    def __repr__(self) -> str:
        return f"a prefix of {self.full[:16]}…"


class _MoreThanOneIn:
    """``More than 1 in N`` holds: the black-hole share is above 1/N."""

    def __init__(self, black_holes: int, judged: int) -> None:
        self.black_holes, self.judged = black_holes, judged

    def __call__(self, found: str) -> bool:
        return int(found) > 0 and self.black_holes * int(found) > self.judged

    def __repr__(self) -> str:
        return f"N with {self.black_holes} of {self.judged} above 1/N"


def hand_written_facts(db: Path, stats: dict) -> dict:
    """The values the hand-written files state, each from its source: counts by their own SQL on the database,
    the ranked counts per lens, the top 20 and the snapshot's hashes from the API, the star figures and the
    corpus from the stats file (which ``verify_report`` proves equal to its recomputation)."""
    from fastapi.testclient import TestClient

    from porchlamp.core import constants
    from porchlamp.serve import report_pages as rp
    from porchlamp.serve.app import create_app

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

        def one(sql: str) -> int:
            return conn.execute(sql, (sid,)).fetchone()[0] or 0

        total = one("SELECT COUNT(*) FROM repositories WHERE snapshot_id = ?")
        rated = one("SELECT COUNT(*) FROM repositories WHERE snapshot_id = ? AND rated = 1")
        gated = one("SELECT COUNT(*) FROM repositories WHERE snapshot_id = ? AND gate_reason IS NOT NULL "
                    "AND gate_reason NOT LIKE 'unrated:%'")
        unrated_by_reason = dict(conn.execute(
            "SELECT COALESCE(unrated_reason, gate_reason, 'none'), COUNT(*) FROM repositories WHERE snapshot_id = ? "
            "AND rated = 0 AND (gate_reason IS NULL OR gate_reason LIKE 'unrated:%') GROUP BY 1", (sid,)).fetchall())
        judged, black_holes = conn.execute(
            "SELECT COUNT(*), SUM(gate_reason = 'black_hole') FROM repositories WHERE snapshot_id = ? "
            "AND (gate_reason IS NULL OR gate_reason IN ('black_hole', 'unrated: no_external_workflow')) "
            "AND COALESCE(gate_undecided, '[]') NOT LIKE '%D-GATE-5%'", (sid,)).fetchone()
        ecosystems = one("SELECT COUNT(DISTINCT ecosystem) FROM repositories WHERE snapshot_id = ?")
        conn.close()
        lens_ranked = {lens: api("/v1/porchlamp/leaderboard", persona=lens, page=1, page_size=1)["total"]
                       for lens in sorted(constants.PERSONA_PRESETS)}
        top = api("/v1/porchlamp/leaderboard", persona="balanced", page=1, page_size=20)["rows"]
    black_holes = black_holes or 0
    floor = round(constants.EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE * 100)
    bands = [stats["by_stars"][key] for key in rp.STAR_BANDS]
    return {
        "snapshot_id": sid, "benchmark_version": snapshot["benchmark_version"],
        "constants_hash": snapshot["constants_hash"], "manifest_root": snapshot["manifest_root"],
        "reproduce_command": snapshot["reproduce_command"],
        "total": total, "ranked": rated, "gated": gated, "unrated": total - rated - gated,
        "unrated_by_reason": unrated_by_reason, "coverage_floor": floor,
        "below_floor": unrated_by_reason.get("unrated: insufficient_evidence", 0),
        "judged": judged, "black_holes": black_holes, "share": rp.pct1(black_holes, judged) if judged else "",
        "band_percents": [rp.pct0(b["black_holes"], b["n"]) for b in bands],
        "top_band": {"n": bands[-1]["n"], "black_holes": bands[-1]["black_holes"]},
        "rho": rp.rho_text(stats["spearman"]["porchlamp"]["rho"]), "top100_overlap": stats["top100_overlap"],
        "corpus": stats["corpus"], "lens_ranked": lens_ranked, "ecosystems": ecosystems,
        "parameters": len(methodology["parameters"]), "pillars": {p["name"]: p["weight"] for p in methodology["pillars"]},
        "lenses": len(methodology["personas"]["presets"]),
        "top": [
            [str(r["rank"]), f"{r['owner']}/{r['name']}", r["ecosystem"], _tofixed(r["porchlamp"], 2),
             f"{_half_up(r['coverage'], 100)}%",
             f"{r['tier']} (capped)" if r["tier_ceiling"] and r["tier"] == r["tier_ceiling"] else r["tier"],
             r["quadrant"] or ""]
            for r in top
        ],
    }


def _hand_written_claims(name: str, f: dict) -> list[tuple[str, str, tuple]]:
    """``(what, pattern, expected groups)`` for one file. Each statement must be found (a reworded file must
    update its check, never slip past it), and every place it is found must carry the expected values."""
    fmt = lambda n: f"{n:,}"  # noqa: E731
    T, R, G, U = fmt(f["total"]), fmt(f["ranked"]), fmt(f["gated"]), fmt(f["unrated"])
    J, B, S = fmt(f["judged"]), fmt(f["black_holes"]), f["share"]
    c = f["corpus"]
    PRS, AUTH, PROJ = fmt(c["human_outside_prs"]), fmt(c["distinct_outside_authors"]), fmt(c["derived_repos"])
    sid, bv = f["snapshot_id"], f["benchmark_version"]
    lens = {k: fmt(v) for k, v in f["lens_ranked"].items()}
    share = (r"Of (?:the )?" + _N + r" licensed, maintained software projects (?:that received|receiving) at least "
             r"(?:ten|10) outside pull requests in six months, " + _N + r" \(" + _P + r"\)", (J, B, S))
    bands = (_BAND_TEXT.format(p=_P), tuple(f["band_percents"]))
    one_in = (r"More than 1 in (\d+)", (_MoreThanOneIn(f["black_holes"], f["judged"]),))
    top_band_one_in = (r"more than 1 in (\d+) of the most-starred",
                       (_MoreThanOneIn(f["top_band"]["black_holes"], f["top_band"]["n"]),))
    corpus = (_N + r" pull requests from " + _N + r" outside contributors (?:to|tracked across|across) " + _N
              + r" (?:open-source )?(?:GitHub )?(?:projects|repositories)", (PRS, AUTH, PROJ))
    correlation = (r"Across (?:the |all )?" + _N + r" ranked projects,? the rank correlation between (?:GitHub )?stars "
                   r"and the Porchlamp score is (\d\.\d+), and (?:only )?(\d+) of the 100 most-starred projects make "
                   r"the top 100", (R, f["rho"], str(f["top100_overlap"])))
    balanced_sid = (r"\(Balanced [Ss]napshot (\S+?)\)", (sid,))
    pillar_weights = [
        (f"{p.capitalize()} weight", rf"\b{p.capitalize()} \((\d+)%\)", (str(round(w * 100)),))
        for p, w in f["pillars"].items()
    ]
    if name == "index.html":
        claims = [
            ("projects ranked", r"\b" + _N + r" open-source projects ranked", (T,)),
            ("projects evaluated", r"\b" + _N + r" repositories evaluated across (\d+) parameters, (\d+) pillars, and "
             r"(\d+) contributor personas", (T, str(f["parameters"]), str(len(f["pillars"])), str(f["lenses"]))),
            ("ranks N projects", r"\bPorchlamp ranks " + _N + r" open-source projects", (T,)),
            ("black-hole share", *share), ("more than 1 in N", *one_in), ("star bands", *bands),
            ("most-starred band, more than 1 in N", *top_band_one_in),
            ("correlation and top 100", *correlation), ("corpus", *corpus),
            ("parameters, pillars, personas", r"\b(\d+) parameters across (\d+) pillars and (\d+) contributor personas",
             (str(f["parameters"]), str(len(f["pillars"])), str(f["lenses"]))),
            ("universe counts", _N + r" ranked, " + _N + r" gated, and " + _N + r" unrated of " + _N
             + r" evaluated repositories", (R, G, U, T)),
            ("table heading", *balanced_sid),
            ("snapshot line", r"Snapshot (\S+) · benchmark (\S+) · constants ([0-9a-f]+)… · manifest ([0-9a-f]+)… · "
             r"reproduce: (python -m porchlamp\.snapshot reproduce \S+)",
             (sid, bv, _Prefix(f["constants_hash"]), _Prefix(f["manifest_root"]), f["reproduce_command"])),
            *pillar_weights,
        ]
    elif name == "llms.txt":
        claims = [
            ("ranks N projects", r"\bPorchlamp ranks " + _N + r" open-source projects", (T,)),
            ("snapshot heading", r"\(Snapshot (\S+), Benchmark (\S+)\)", (sid, bv)),
            ("corpus", *corpus), ("ecosystems", r"\bacross (\d+) ecosystems", (str(f["ecosystems"]),)),
            ("black-hole share", *share), ("more than 1 in N", *one_in), ("star bands", *bands),
            ("most-starred band, more than 1 in N", *top_band_one_in),
            ("correlation and top 100", *correlation),
            ("universe total", r"Universe Breakdown \(" + _N + r" Total\)", (T,)),
            ("ranked", r"\b" + _N + r" Ranked \(Rated\)", (R,)),
            ("gated", r"\b" + _N + r" Gated \(Stage 0\)", (G,)),
            ("unrated", r"\b" + _N + r" Unrated:", (U,)),
            ("frozen copy", r"frozen for citation \(Snapshot (\S+?)\)", (sid,)),
            ("CSV and check counts", r"(?:CSV of|search over) the " + _N + r" ranked projects", (R,)),
            ("pillars and parameters", r"\b(\d+) Pillars & (\d+) Parameters", (str(len(f["pillars"])), str(f["parameters"]))),
            ("top-20 heading", r"Top (\d+) Ranked Open-Source Repositories", (str(len(f["top"])),)),
            ("top-20 heading snapshot", *balanced_sid),
            ("balanced lens", r"Balanced Leaderboard \(Default, " + _N + r" ranked\)", (lens["balanced"],)),
            ("first-timer lens", r"First-Timer Persona \(" + _N + r" ranked\)", (lens["first_timer"],)),
            ("career-capital lens", r"Career Capital Persona \(" + _N + r" ranked\)", (lens["career_capital"],)),
            ("senior-systems lens", r"Senior Systems Persona \(" + _N + r" ranked\)", (lens["senior_systems"],)),
            ("ai-assisted lens", r"AI-Assisted Persona \(" + _N + r" ranked\)", (lens["ai_assisted"],)),
            ("not-ranked counts", r"\(" + _N + r" gated \+ " + _N + r" unrated\)", (G, U)),
            ("report addresses", r"porchlamp\.org/black-holes/(\d{4}-\d\d-\d\d\w*)/", (sid,)),
            ("data addresses", r"porchlamp\.org/data/([^/\s)]+)/", (sid,)),
            *pillar_weights,
        ]
    elif name == "copy.js":
        # the app's own finding line and "why" paragraph
        claims = [("more than 1 in N", r"[Mm]ore than 1 in (\d+)", (_MoreThanOneIn(f["black_holes"], f["judged"]),))]
    else:  # feed.xml
        claims = [
            ("subtitle corpus", _N + r" pull requests across " + _N + r" repositories", (PRS, PROJ)),
            ("entry dates", r"<updated>(\d{4}-\d\d-\d\d)T", (sid[:10],)),
            ("snapshot entry title", r"Snapshot (\S+) \(Benchmark (\S+)\): " + _N + r" Open-Source Repositories",
             (sid, bv, T)),
            ("snapshot entry id", r"#snapshot-(\S+?)</id>", (sid,)),
            ("corpus", *corpus),
            ("universe counts", _N + r" repositories ranked, " + _N + r" gated at Stage 0 \(including " + _N
             + r" pull-request black holes\), and " + _N + r" unrated", (R, G, B, U)),
            ("report title share", r"Pull Requests: " + _P + r" of Active Open-Source Projects", (S,)),
            ("black-hole share", *share), ("star bands", *bands),
            ("first-timer lens", r"First-Time Contributors \(" + _N + r" Ranked Repositories\)", (lens["first_timer"],)),
            ("ai-assisted lens", r"AI-Assisted Contributors \(" + _N + r" Ranked Repositories\)", (lens["ai_assisted"],)),
            ("parameters", r"\b(\d+) Parameters, Stage-0", (str(f["parameters"]),)),
            ("pillars and parameters", r"\b(\d+) pillars, (\d+) parameters", (str(len(f["pillars"])), str(f["parameters"]))),
            ("hashes", r"constants_hash ([0-9a-f]+)…, manifest_root ([0-9a-f]+)…",
             (_Prefix(f["constants_hash"]), _Prefix(f["manifest_root"]))),
        ]
    return claims


def _below_floor_counts(text: str, f: dict) -> list[tuple[str, str]]:
    """``(count, line)`` for every count a line gives for the projects below the coverage floor: the number
    nearest before ``<60%`` in its line or sentence. Only ``unrated: insufficient_evidence`` projects are
    unrated for coverage; the other unrated reasons are not a coverage shortfall."""
    marker = f"<{f['coverage_floor']}%"
    found = []
    for line in re.split(r"\n|(?<=[.;])\s+(?=[A-Z0-9])", text):
        if marker not in line:
            continue
        numbers = re.findall(r"(?<![\w.\-<>=])(\d{1,3}(?:,\d{3})+|\d+)(?![\w%]|[.,]\d)", line[: line.index(marker)])
        if numbers:
            found.append((numbers[-1], line.strip()))
    return found


def _flatten(name: str, raw: str) -> str:
    """The text a reader sees: markup and entities resolved for HTML and XML, emphasis marks dropped for
    Markdown, whitespace collapsed (newlines kept, for the line-by-line checks)."""
    if name in ("llms.txt", "copy.js"):
        text = raw.replace("`", "").replace("**", "")
    else:
        # a <meta> tag's text is in its content attribute: keep it, one line each
        metas = "\n".join(re.findall(r'<meta [^>]*content="([^"]*)"', raw))
        text = html.unescape(re.sub(r"<(?!updated>|/id>)[^>]+>", " ", raw + "\n" + metas))
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines())


#: The shipped files whose numbers are written by hand, and so checked against the data.
HAND_WRITTEN_FILES = ("index.html", "llms.txt", "feed.xml", "copy.js")


def check_hand_written(site: Path, f: dict) -> dict:
    """Compare every number of the hand-written ``index.html`` (its static article, head and top-20 table),
    ``llms.txt``, ``feed.xml`` and the app's finding line in ``copy.js`` with ``f`` (:func:`hand_written_facts`).
    ``{"checked": n, "errors": [...]}``."""
    ledger = _Ledger()
    for name in HAND_WRITTEN_FILES:
        path = site / name
        if not path.is_file():
            ledger.checked += 1
            ledger.errors.append(f"{name} is missing")
            continue
        raw = path.read_text(encoding="utf-8")
        text = _flatten(name, raw)
        for what, pattern, expected in _hand_written_claims(name, f):
            matches = list(re.finditer(pattern, text))
            ledger.checked += 1
            if not matches:
                ledger.errors.append(f"{name}: {what}: statement not found (update the check with the text)")
                continue
            for match in matches:
                wrong = [(got, want) for got, want in zip(match.groups(), expected)
                         if not (want(got) if callable(want) else got == want)]
                if wrong:
                    ledger.errors.append(
                        f"{name}: {what}: {match.group(0)!r}: " + "; ".join(f"found {g!r}, expected {w!r}" for g, w in wrong))
        for count, line in _below_floor_counts(text, f):
            ledger.checked += 1
            if count != f"{f['below_floor']:,}":
                others = ", ".join(f"{n:,} {reason}" for reason, n in sorted(f["unrated_by_reason"].items())
                                   if reason != "unrated: insufficient_evidence")
                ledger.errors.append(
                    f"{name}: coverage below {f['coverage_floor']}%: the line {line[:160]!r} gives {count}, but "
                    f"{f['below_floor']:,} projects are unrated for coverage below {f['coverage_floor']}% "
                    f"(the other unrated: {others or 'none'})")
        if name == "index.html":
            article = re.search(r'<article class="seo-fallback">(.*?)</article>', raw, re.S)
            rows = _rows(article.group(1)) if article else []
            ledger.equal("index.html top-20 table", rows, f["top"])
        elif name == "llms.txt":
            listed = [list(m.groups()) for m in re.finditer(
                r"^(\d+)\. (\S+/\S+) \((\w+)\) — Score (\d+\.\d\d) · Coverage (\d+%) · Tier (.+?) · (.+)$", text, re.M)]
            ledger.equal("llms.txt top-20 list", listed, f["top"])
    return {"checked": ledger.checked, "errors": ledger.errors}


def verify_hand_written(db: Path, stats_path: Path, site: Path, *, log=print) -> dict:
    """:func:`check_hand_written` with the facts of ``db`` and the stats file."""
    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    result = check_hand_written(site, hand_written_facts(db, stats))
    log(f"verify: hand-written numbers: checked {result['checked']} statements in index.html, llms.txt and feed.xml")
    for error in result["errors"]:
        log(f"HAND-WRITTEN MISMATCH {error}")
    return result


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
        ledger.contains(f"{who} link to the methodology", evid, 'href="../../../methodology/"')
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
        ledger.equal("check scripts", re.findall(r"<script[^>]*\ssrc=\"([^\"]*)\"", text), list(rp.PAGE_SCRIPTS["check"]))
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
        # links to the method, the report and the ranked table; the scripts a page of its kind may load
        method = _section_after(text, "method")
        for target, label in (("../methodology/", "methodology"), ("../black-holes/", "report"),
                              (f"../black-holes/{sid}/{csv_name}", "ranked CSV")):
            ledger.contains(f"{rel} link to the {label}", method, f'href="{target}"')
        ledger.token(f"{rel} ranked count", _plain(method), fmt(ranked))
        ledger.equal(f"{rel} scripts", re.findall(r"<script[^>]*\ssrc=\"([^\"]*)\"", text), list(rp.PAGE_SCRIPTS["page"]))
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
    parser.add_argument(
        "--snapshot-dir", type=Path, default=None,
        help="the snapshot directory the database was stored from, as given to the export (black-hole evidence)",
    )
    parser.add_argument("--data-dir", type=Path, default=None, help="the data root the export read raw/ from")
    args = parser.parse_args(argv)
    site = args.site.resolve()
    if args.report_only and args.stats is None:
        parser.error("--report-only needs --stats")
    if (args.snapshot_dir is None) != (args.data_dir is None):
        parser.error("--snapshot-dir and --data-dir go together")
    evidence_files = None
    if args.snapshot_dir is not None:
        from porchlamp.serve.app import PinnedEvidenceFiles

        evidence_files = PinnedEvidenceFiles(args.snapshot_dir.resolve(), args.data_dir.resolve())
    ok = True
    if not args.report_only:
        ok = passed(verify(args.db.resolve(), site, quick=args.quick, mutants=not args.no_mutants,
                           evidence_files=evidence_files, workers=None))
    problems = check_sitemap(site)
    for problem in problems:
        print(f"SITEMAP {problem}")
    ok = ok and not problems
    ok = not verify_chrome(args.db.resolve(), site)["errors"] and ok
    ok = not verify_methodology(args.db.resolve(), site)["errors"] and ok
    if args.stats is not None:
        ok = not verify_report(args.db.resolve(), args.stats.resolve(), site, evidence_files=evidence_files)["errors"] and ok
        ok = not verify_hand_written(args.db.resolve(), args.stats.resolve(), site)["errors"] and ok
        ok = not verify_projects(args.db.resolve(), site)["errors"] and ok
        ok = not verify_landing(args.db.resolve(), site)["errors"] and ok
        ok = not verify_home(site)["errors"] and ok
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
