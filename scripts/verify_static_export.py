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
grid. Whatever the flags, every ``<loc>`` of ``sitemap.xml`` (and, for a sitemap index, of each child sitemap it
names) must have a page in the site.

``verify_projects`` (also run by ``--stats``) proves the project pages and the /check/ stub: every ranked project has its page
at ``projects/<owner>/<name>/`` and no other page exists but a not-ranked project's; every value each page prints (a ``data-v`` hook: the answer
sentences, the key facts, the rating, the five pillars and their coverage, each parameter's value with its unit, score,
count and window, each lens rank, the similar projects, the record's identity) is recomputed from the API with this
file's own formatters and compared; so are the head (the title, and a description that names the project and its rank in
at most 155 characters), the outline (one h1, no skipped level, nothing collapsed, the anchors), and the links;
``sitemap-projects.xml`` lists every ranked project in balanced-rank order; ``/check/`` is a stub that sends the address
on to ``/`` (a meta refresh and the canonical address ``/``), with no script and no place in the sitemap. Any other page
under ``projects/`` must be the page of a project the snapshot holds and does not rank, which says it is not ranked and
why (a head from the reason's sentence, the banner, the same values and links); with ``--previous-site`` (the site as
last published) every project that site had a page for still has one.

Whatever the flags, ``verify_chrome`` proves every page carries the one header, nav and footer
(``web/app/components/chrome.js``, drawn at export by ``web/prerender.mjs``), one Content-Security-Policy and only the
scripts its kind allows, and ``verify_methodology`` proves the methodology page drawn at export: every value it prints
(a ``data-v`` hook) against the API, its headings and links, and byte for byte against a fresh draw from the API's
own answers.

``verify_home`` (also run by ``--stats``) proves the files generated from the
snapshot's data (``porchlamp/serve/home_pages.py``): the home page's description and JSON-LD, ``llms.txt`` and ``feed.xml``
are byte for byte what the generators write from facts worked out here (the API's snapshot, methodology and first page
of the balanced leaderboard, and the report's values from the stats file); the prerendered home holds the findings line
and the first page of rows, and is byte for byte what ``web/prerender.mjs`` draws from those facts.
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
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional
from collections import deque
from urllib.parse import quote, urljoin, urlsplit

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
    classification the endpoint itself runs (the same ones ``export_static`` uses, a black hole's numbers from the
    grid's evidence files), with the API's own counts."""
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
        counts = serve_app._black_hole_counts(conn, snapshot_id, record, grid.evidence_files)
        groups[group].append(serve_app._not_ranked_row(record, group=group, reason=reason, counts=counts).model_dump(mode="json"))
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


#: The mismatches a check prints for one page, and the pages it prints them for: a fault that moves every row after it
#: on one page prints this many and a count, and the faults on the other pages still show.
MISMATCHES_PER_PAGE, MISMATCH_PAGES = 5, 40


def _log_mismatches(log, kind: str, errors: list[str]) -> None:
    """``errors`` (a check's ledger) as ``<kind> MISMATCH`` lines, grouped by their first word, which names the page,
    file or project a check is about (a check of the whole site starts with a word of its own): the first
    :data:`MISMATCHES_PER_PAGE` of each of the first :data:`MISMATCH_PAGES` groups, then how many each leaves out."""
    counts: dict[str, int] = {}
    for error in errors:
        page = error.split(" ", 1)[0].rstrip(":")
        counts[page] = counts.get(page, 0) + 1
        if counts[page] <= MISMATCHES_PER_PAGE and len(counts) <= MISMATCH_PAGES:
            log(f"{kind} MISMATCH {error}")
    shown = list(counts)[:MISMATCH_PAGES]
    for page in shown:
        if counts[page] > MISMATCHES_PER_PAGE:
            log(f"{kind} MISMATCH {page}: {counts[page] - MISMATCHES_PER_PAGE} more not printed")
    if len(counts) > len(shown):
        rest = list(counts.values())[len(shown):]
        log(f"{kind} MISMATCH {len(rest)} more pages, with {sum(rest)} mismatches, not printed")


#: The report's findings, in page order: the first is the page's lead figure, the others its Key findings.
_FINDINGS = ("finding-share", "finding-stars", "finding-top100", "finding-correlation", "finding-prs")


def _thousands(value: int) -> str:
    return f"{value:,}"


def _share_text(numerator: int, denominator: int, places: int) -> str:
    """``numerator / denominator`` as a percentage, rounded half up to ``places`` decimals (26.4 for 1,133 of 4,297)."""
    step = Decimal(1).scaleb(-places)
    return str((Decimal(numerator) * 100 / Decimal(denominator)).quantize(step, rounding=ROUND_HALF_UP))


def _band_text(key: str) -> str:
    """A star band's key in the stats file as the page names it: ``[1000,10000)`` -> ``1,000 to 10,000``."""
    low, high = re.fullmatch(r"\[(\d+),(\d+|inf)\)", key).groups()
    return f"{int(low):,} or more" if high == "inf" else f"{int(low):,} to {int(high):,}"


def report_hooks(
    *, sid: str, judged: int, black_holes: int, deadline: int, min_n: int, confidence: int, bands: list,
    below: int, lowest: int, top100_black_holes: int, top100_overlap: int, ranked: int, rho: float, unanswered: int,
    counted: int, corpus: dict, start: date, end: date, undecided: int, ecosystems: list, names: dict[str, str],
    constants_hash: str, db_sha: str, undecided_cut: int = 0, pr_cap: int | None = None, sampled: int = 0,
    full_max: int | None = None, sample_size: int | None = None,
) -> dict[str, str]:
    """Every ``data-v`` hook the report prints (``web/app/views/report.js``), with the value worked out here from
    the database, the API and the stats file, in this file's own formatting: the page's hook count per type is the
    size of this map, so a hook that goes missing is a failure. ``bands``: ``[(key, stats band)]`` in page order;
    ``ecosystems``: ``[(key, judged, black_holes)]`` highest share first."""
    day = date.fromisoformat(sid)
    out = {
        "snapshot_id": sid,
        "snapshot_date": f"{day.day} {day:%B %Y}",
        "year": str(day.year),
        "one_in": _thousands(judged // black_holes + 1),  # the smallest N with N * black_holes > judged
        "black_holes": _thousands(black_holes),
        "judged": _thousands(judged),
        "share": _share_text(black_holes, judged, 1),
        "deadline_days": _thousands(deadline),
    }
    for i, (key, band) in enumerate(bands, 1):
        out[f"band{i}_share"] = _share_text(band["black_holes"], band["n"], 0)
        out[f"band{i}_label"] = _band_text(key)
    out.update({
        "top100_black_holes": _thousands(top100_black_holes),
        "top100_overlap": _thousands(top100_overlap),
        "ranked": _thousands(ranked),
        "rho": ("0.00" if abs(Decimal(str(rho))) < Decimal("0.005") else str(Decimal(str(rho)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))),
        "unanswered": _thousands(unanswered),
        "counted": _thousands(counted),
        "corpus_prs": _thousands(corpus["human_outside_prs"]),
        "corpus_authors": _thousands(corpus["distinct_outside_authors"]),
        "corpus_projects": _thousands(corpus["derived_repos"]),
        "window_start": f"{start.day} {start:%B}",
        "window_end": f"{end.day} {end:%B %Y}",
        "min_n": _thousands(min_n),
        "ci_percent": _thousands(confidence),
        "undecided_rule_inputs": _thousands(undecided),
        "below_bands_judged": _thousands(below),
        "lowest_band": _thousands(lowest),
        "csv_name": f"porchlamp-{sid}-ranked.csv",
        "frozen_url": f"{SITE_ORIGIN}/black-holes/{sid}/",
        "source_db_sha256": db_sha,
        "constants_hash": constants_hash,
    })
    for i, (key, band) in enumerate(bands):
        out[f"bands.{i}.label"] = _band_text(key)
        out[f"bands.{i}.judged"] = _thousands(band["n"])
        out[f"bands.{i}.black_holes"] = _thousands(band["black_holes"])
        out[f"bands.{i}.share"] = _share_text(band["black_holes"], band["n"], 0)
    for i, (key, n, holes) in enumerate(ecosystems):
        out[f"ecosystems.{i}.name"] = names[key]
        out[f"ecosystems.{i}.judged"] = _thousands(n)
        out[f"ecosystems.{i}.black_holes"] = _thousands(holes)
        out[f"ecosystems.{i}.share"] = _share_text(holes, n, 1)
    if not below:
        del out["below_bands_judged"], out["lowest_band"]  # the "not charted" note is left out
    if not undecided:
        del out["undecided_rule_inputs"]  # the measurement bullet is left out
    if undecided_cut:  # the cut-record bullet, only when it is printed
        out["undecided_cut"] = _thousands(undecided_cut)
        out["pr_cap"] = _thousands(pr_cap)
    if sampled:  # the sampled-record bullet, only when it is printed
        out["sampled"] = _thousands(sampled)
        out["full_max"] = _thousands(full_max)
        out["sample_size"] = _thousands(sample_size)
    return out


def _report_sentences(hooks: dict[str, str], names: dict[str, str]) -> dict[str, str]:
    """Every report sentence that carries a number or a claim, worded here again from the hooks' values (the text a
    reader sees, tags dropped). Written independently of ``report_pages``' templates, so a number the page puts in
    the wrong place, or a template that does, is a mismatch."""
    v = hooks
    rows = sorted({int(k.split(".")[1]) for k in v if k.startswith("ecosystems.")})
    bands = sorted({int(k.split(".")[1]) for k in v if k.startswith("bands.")})
    out = {
        "finding-share": f"More than 1 in {v['one_in']}.",
        "finding-share text": (
            f"In Porchlamp's {v['snapshot_id']} snapshot, {v['black_holes']} of {v['judged']} active open-source projects "
            f"({v['share']}%) left most outside pull requests with no human reply, review, merge or close within "
            f"{v['deadline_days']} days or since."
        ),
        "finding-stars": "More stars, more silence.",
        "finding-stars text": (
            f"The share rises with stars: {v['band1_share']}% of projects with {v['band1_label']} stars, "
            f"{v['band2_share']}% at {v['band2_label']}, and {v['band3_share']}% at {v['band3_label']}."
        ),
        "finding-top100": "Fame is no guarantee.",
        "finding-top100 text": (
            f"{v['top100_black_holes']} of the 100 most-starred projects are black holes, and only {v['top100_overlap']} "
            "of them reach Porchlamp's top 100."
        ),
        "finding-correlation": "Stars barely predict an answer.",
        "finding-correlation text": (
            f"Across {v['ranked']} ranked projects, the rank correlation between stars and the Porchlamp score is {v['rho']}."
        ),
        "finding-prs": "Most of those pull requests waited.",
        "finding-prs text": (
            f"Inside the black-hole projects, {v['unanswered']} of {v['counted']} counted outside pull requests got no "
            f"human reply within {v['deadline_days']} days."
        ),
        "lede": (
            "The state of open-source black holes: how often a stranger's pull request goes unanswered, in the "
            f"Porchlamp snapshot of {v['snapshot_date']}."
        ),
        "intro": "You send a small fix to a project you use every day. Two weeks later, nobody has replied.",
        "stars answer": "They answer less often, on average. The black-hole share climbs at every step up in stars.",
        "ecosystems answer": (
            f"{names['pypi']} and {names['npm']} projects go quiet more often than {names['crates']}, {names['go']}, "
            f"{names['maven']} or {names['systems']} projects. Each share is computed within its own ecosystem."
        ),
        "stars caption": "Black-hole share by stars: " + "; ".join(
            f"{v[f'bands.{i}.share']}% of {v[f'bands.{i}.judged']} projects with {v[f'bands.{i}.label']} stars" for i in bands
        ) + ".",
        "ecosystems caption": "Black-hole share by package ecosystem, highest first: " + "; ".join(
            f"{v[f'ecosystems.{i}.name']} {v[f'ecosystems.{i}.share']}% of {v[f'ecosystems.{i}.judged']}" for i in rows
        ) + ".",
        "download": (
            f"{v['csv_name']}: the {v['ranked']} ranked projects with rank, owner, name, ecosystem, score, tier, coverage, "
            "quadrant and the rank in each lens. Released under CC BY 4.0."
        ),
        "cite": (
            f"Porchlamp ({v['year']}). Stars don't review pull requests: the state of open-source black holes. Snapshot "
            f"{v['snapshot_id']}. {v['frozen_url']}"
        ),
        "hashes": (
            f"Data licence: CC BY 4.0. Snapshot database SHA-256: {v['source_db_sha256']}. Methodology constants hash: "
            f"{v['constants_hash']}."
        ),
        "description": (
            f"{v['share']}% of active open-source projects left most outside pull requests unanswered for two weeks. The "
            f"share rises with stars. Data on {v['corpus_projects']} GitHub projects."
        ),
        "dataset": (
            f"{v['black_holes']} of {v['judged']} active open-source projects ({v['share']}%) left most outside pull "
            f"requests with no human reply, review, merge or close within {v['deadline_days']} days or since, in Porchlamp's "
            f"{v['snapshot_id']} snapshot. The share rises with stars: {v['band1_share']}% at {v['band1_label']} stars, "
            f"{v['band2_share']}% at {v['band2_label']}, {v['band3_share']}% at {v['band3_label']}. Includes the "
            f"{v['ranked']} ranked projects with rank, score, tier, coverage and quadrant."
        ),
    }
    method = [
        f"We followed {v['corpus_prs']} pull requests from {v['corpus_authors']} outside contributors to "
        f"{v['corpus_projects']} GitHub projects, from {v['window_start']} to {v['window_end']}.",
        f"A project is judged only when it is licensed, maintained software with at least {v['min_n']} counted outside "
        "pull requests.",
    ]
    if "undecided_rule_inputs" in v:
        method.append(
            f"Another {v['undecided_rule_inputs']} licensed, maintained projects with at least {v['min_n']} outside pull "
            "requests on record are not judged yet: those records were fetched before drafts and closes were recorded, "
            "and the rules need both."
        )
    if "undecided_cut" in v:
        method.append(
            f"We could not judge {v['undecided_cut']} busy projects yet: we hold only the newest {v['pr_cap']} pull "
            f"requests of each, and they do not reach back to {v['window_start']}."
        )
    if "sampled" in v:
        projects = "project" if v["sampled"] == "1" else "projects"
        method.append(
            f"{v['sampled']} {projects} had more than {v['full_max']} pull requests in that time. For each, we read "
            f"replies and reviews on an even random sample of {v['sample_size']} of them; every pull request counts for "
            "decisions and merges."
        )
    method.append(
        f"It is a black hole when, with {v['ci_percent']}% confidence, more than half of those pull requests got no "
        f"substantive reply, review, merge or close within {v['deadline_days']} days or since."
    )
    out["method"] = method
    if "below_bands_judged" in v:
        noun = "project" if v["below_bands_judged"] == "1" else "projects"
        out["stars note"] = f"Not charted: {v['below_bands_judged']} judged {noun} with fewer than {v['lowest_band']} stars."
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


#: Each ecosystem's page, typed again here (double entry against ``web/app/routes.js``'s ecosystem presets).
ECOSYSTEM_PAGE_SLUGS = {"crates": "rust", "pypi": "python", "npm": "javascript", "go": "go", "maven": "java", "systems": "c-cpp"}


def _judged_by_ecosystem(conn: sqlite3.Connection, sid: str) -> dict[str, tuple[int, int]]:
    """``{ecosystem: (judged, black_holes)}``: D-GATE-5 decided projects, by their own SQL on the database."""
    rows = conn.execute(
        "SELECT ecosystem, COUNT(*), SUM(gate_reason = 'black_hole') FROM repositories WHERE snapshot_id = ? "
        "AND (gate_reason IS NULL OR gate_reason IN ('black_hole', 'unrated: no_external_workflow')) "
        "AND COALESCE(gate_undecided, '[]') NOT LIKE '%D-GATE-5%' GROUP BY ecosystem", (sid,),
    ).fetchall()
    return {eco: (n, bh or 0) for eco, n, bh in rows}


def _report_main(text: str) -> Optional[str]:
    """The report page's ``<main>`` content: what the report view drew (``web/app/views/report.js``)."""
    found = re.search(r'<main class="app-main" id="app-main" tabindex="-1"[^>]*>(.*?)\n *</main>', text, re.S)
    return found.group(1) if found else None


def _shipped_stylesheet(site: Path) -> str:
    """What the frozen copy's ``styles.css`` must hold: the stylesheets the site's app page links, in its order, from
    the site itself, each after a line naming it (ENG-14)."""
    hrefs = re.findall(r'<link rel="stylesheet" href="(/styles/[^"]*)">', (site / "404.html").read_text(encoding="utf-8"))
    return "".join(f"/* {href} */\n" + (site / href.lstrip("/")).read_text(encoding="utf-8").rstrip("\n") + "\n" for href in hrefs)


def verify_report(db: Path, stats_path: Path, site: Path, *, evidence_files: Any = None, log=print) -> dict:
    """The report (``/black-holes/``, the app's report view drawn into the page at export) and its frozen copy, then
    the CSV, against the database, the API and the stats file. ``{"checked", "hooks", "csv_cells", "errors",
    "record", "black_hole_numbers"}`` (each black hole's ``(unanswered, counted)`` as ``/repos`` serves its evidence);
    passing means no errors.

    The expected values come from sources the export's report code does not read here: the judged / black-hole
    counts from their own SQL on the database, the ranked count, the leaderboards and the black-hole evidence from
    the API, the star bands, correlation and corpus from the stats file, which must itself equal what
    ``porchlamp.snapshot stats`` recomputes from the database and the data root, and the database digest from this
    file's own sha256 of the database file. They are formatted here (:func:`report_hooks`), never read from
    ``data/``. On each page every ``data-v`` hook must equal its value, every hook must be there (the page type's hook
    count) and no other may be; every sentence with a number is compared whole (:func:`_report_sentences`); the
    page's ``<main>`` is byte for byte what ``web/prerender.mjs`` draws from a record holding the values worked out
    here (ENG-17); the frozen copy loads no app and one stylesheet, the site's own stylesheets joined (ENG-14); and
    every file the older frozen copies a publish carries link from the site root is in the site
    (``export_static.CARRIED_FROZEN_ASSETS``). ``evidence_files`` (:class:`porchlamp.serve.app.PinnedEvidenceFiles`) are the files the export read the
    black-hole evidence from; their data root is also the stats file's."""
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
        numbers: dict[tuple[str, str], tuple[int, int]] = {}
        for row in conn_names(copy, sid):
            detail = api(f"/v1/porchlamp/repos/{quote(row[0], safe='')}/{quote(row[1], safe='')}")
            if detail["black_hole_evidence"]:
                evidence.append(detail["black_hole_evidence"])
                numbers[(row[0], row[1])] = (detail["black_hole_evidence"]["unanswered"], detail["black_hole_evidence"]["population_n"])
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
        return {"checked": ledger.checked, "hooks": 0, "csv_cells": 0, "errors": ledger.errors}

    if evidence_files is None:
        ledger.checked += 1
        ledger.errors.append("stats file: no data root to recompute it from (pass --snapshot-dir and --data-dir)")
    else:
        _check_stats_file(ledger, db, stats, evidence_files.data_dir)
    names = rp.copy_js_map("ECOSYSTEM_NAMES")  # the one list of names (copy.js), which every page uses
    bands = [(key, stats["by_stars"][key]) for key in rp.STAR_BANDS]
    lowest = min(int(re.match(r"\[(\d+),", key).group(1)) for key, _ in bands)
    below = sum(b["n"] for key, b in stats["by_stars"].items() if int(re.match(r"\[(\d+),", key).group(1)) < lowest)
    eco_order = sorted(per_eco, key=lambda e: (-Decimal(per_eco[e][1]) / Decimal(per_eco[e][0]), e))
    (start, end), = window
    gate5 = next(g for g in methodology["gates"] if g["id"] == "D-GATE-5")
    db_sha = hashlib.sha256(db.read_bytes()).hexdigest()
    rho = stats["spearman"]["porchlamp"]["rho"]
    top100_black_holes = stats["top100_stars_fate"]["black_hole"]
    hooks = report_hooks(
        sid=sid, judged=judged, black_holes=black_holes, deadline=gate5["cutoffs"]["response_deadline_days"],
        min_n=gate5["cutoffs"]["min_n"], confidence=95, bands=bands, below=below, lowest=lowest,
        top100_black_holes=top100_black_holes, top100_overlap=stats["top100_overlap"], ranked=ranked, rho=rho,
        unanswered=unanswered, counted=counted, corpus=stats["corpus"], start=date.fromisoformat(start),
        end=date.fromisoformat(end), undecided=stats.get("gate5_undecided_rule_inputs", 0),
        undecided_cut=stats.get("gate5_undecided_cut_record", 0), pr_cap=(stats.get("gate5_undecided_cut_record_caps") or [None])[0],
        sampled=stats.get("pr_window_sampled", 0), full_max=((stats.get("pr_window_sampled_caps") or [[None, None]])[0])[0],
        sample_size=((stats.get("pr_window_sampled_caps") or [[None, None]])[0])[1],
        ecosystems=[(e, *per_eco[e]) for e in eco_order], names=names, constants_hash=snapshot["constants_hash"],
        db_sha=db_sha,
    )
    expected = _report_sentences(hooks, names)
    # the sentences that hold only for some numbers
    band_shares = [Decimal(b["black_holes"]) / Decimal(b["n"]) for _, b in bands]
    ledger.equal("'the share rises with stars' holds", all(a < b for a, b in zip(band_shares, band_shares[1:])), True)
    shares = {e: Decimal(per_eco[e][1]) / Decimal(per_eco[e][0]) for e in per_eco}
    ledger.equal(
        "'Python and JavaScript go quiet more often than the other four' holds",
        all(shares[q] > shares[b] for q in ("pypi", "npm") for b in ("crates", "go", "maven", "systems")), True,
    )
    ledger.equal("'Stars barely predict an answer' holds (|rho| < 0.2)", abs(Decimal(str(rho))) < Decimal("0.2"), True)
    ledger.equal("'Fame is no guarantee' holds (a black hole in the 100 most-starred)", top100_black_holes > 0, True)
    ledger.equal("'Most of those pull requests waited' holds", unanswered * 2 > counted, True)
    if stats.get("gate5_undecided_cut_record"):
        caps = stats.get("gate5_undecided_cut_record_caps")
        ledger.equal("'we hold only the newest N pull requests of each' holds (the cut records name one cap)",
                     isinstance(caps, list) and len(caps) == 1 and type(caps[0]) is int, True)
    if stats.get("pr_window_sampled"):
        pairs = stats.get("pr_window_sampled_caps")
        ledger.equal("'more than N pull requests ... a sample of M of them' holds (the sampled records name one pair)",
                     isinstance(pairs, list) and len(pairs) == 1 and isinstance(pairs[0], list) and len(pairs[0]) == 2
                     and all(type(n) is int for n in pairs[0]), True)

    h2_expected = [rp.copy_js_string(n) for n in (
        "REPORT_FINDINGS_HEADING", "REPORT_STARS_HEADING", "REPORT_ECOSYSTEMS_HEADING", "REPORT_METHOD_HEADING",
        "REPORT_LIMITS_HEADING", "REPORT_DOWNLOAD_HEADING", "REPORT_CITE_HEADING",
    )]
    old_ids = [*_FINDINGS, "key-findings", "stars", "ecosystems", "how-we-measured", "what-this-is-not", "download", "cite"]
    pages = {"black-holes/index.html": "latest", f"black-holes/{sid}/index.html": "frozen"}
    texts: dict[str, str] = {}
    counts: dict[str, int] = {}
    for rel, label in pages.items():
        path = site / rel
        if not path.is_file():
            ledger.errors.append(f"{rel} is missing")
            continue
        text = path.read_text(encoding="utf-8")
        texts[label] = text
        w = rel
        page = read_page(text, elements=True)
        found = [hook for hook in page.hooks if hook[0].split(".")[0] not in ("header", "footer")]  # the chrome's: verify_chrome
        _compare_hooks(ledger, w, found, hooks)
        counts[label] = len(found)
        ledger.equal(f"{w} h1", [t for tag, t in page.headings if tag == "h1"], [rp.copy_js_string("REPORT_H1")])
        ledger.equal(f"{w} h2", [t for tag, t in page.headings if tag == "h2"], h2_expected)
        ledger.equal(f"{w} keeps the old anchors", sorted(set(old_ids) - page.ids), [])
        # every sentence with a number, whole
        lead = page.element(id="finding-share")
        ledger.equal(f"{w} #finding-share figure", page.text(id="finding-share-lead"), expected["finding-share"])
        ledger.equal(f"{w} #finding-share sentence", page.text(cls="report-figure-text", within=lead), expected["finding-share text"])
        for ident in _FINDINGS[1:]:
            box = page.element(id=ident)
            ledger.equal(f"{w} #{ident} lead", page.text(tag="h3", within=box), expected[ident])
            ledger.equal(f"{w} #{ident}", page.text(tag="p", within=box), expected[f"{ident} text"])
        ledger.equal(f"{w} lede", page.text(cls="report-lede"), expected["lede"])
        ledger.equal(f"{w} intro (it ends at 'nobody has replied.')", page.text(cls="report-intro"), expected["intro"])
        answers = [e["text"] for e in page.find(cls="report-answer")]
        ledger.equal(f"{w} the charts' answers", answers, [expected["stars answer"], expected["ecosystems answer"]])
        captions = [e["text"] for e in page.find(tag="caption")]
        ledger.equal(f"{w} the charts' captions", captions, [expected["stars caption"], expected["ecosystems caption"]])
        notes = [e["text"] for e in page.find(cls="report-note", within=page.element(tag="section", aria="stars"))]
        ledger.equal(f"{w} stars note", notes, [expected["stars note"]] if "stars note" in expected else [])
        method = [e["text"] for e in page.find(tag="li", within=page.element(tag="section", aria="how-we-measured"))]
        ledger.equal(f"{w} how-we-measured", method[: len(expected["method"])], expected["method"])
        limits = [e["text"] for e in page.find(tag="li", within=page.element(tag="section", aria="what-this-is-not"))]
        ledger.equal(f"{w} Limits are copy.js's", limits[:-1], rp.copy_js_list("LIMITS"))
        download = page.element(tag="section", aria="download")
        ledger.equal(f"{w} download", page.text(tag="p", within=download), expected["download"])
        ledger.equal(f"{w} cite", page.text(cls="report-cite"), expected["cite"])
        ledger.equal(f"{w} hashes", page.text(cls="report-hashes"), expected["hashes"])
        # the links: the frozen copy (its only inbound link), the CSV, each ecosystem's page, the method, the rankings
        links = set(page.links)
        for href in (f"/black-holes/{sid}/", f"/black-holes/{sid}/{hooks['csv_name']}", "/methodology/", "/"):
            ledger.equal(f"{w} links {href}", href in links, True)
        eco_links = [e["href"] for e in page.find(tag="a", within=page.element(tag="section", aria="ecosystems"))]
        ledger.equal(f"{w} ecosystem rows link their pages", eco_links, [f"/{ECOSYSTEM_PAGE_SLUGS[e]}/" for e in eco_order])
        # head: title, description, canonical, JSON-LD
        when = f"snapshot {sid}" if label == "frozen" else f"{date.fromisoformat(sid):%B %Y}"
        ledger.contains(f"{w} <title>", _plain(re.search(r"<title>(.*?)</title>", text, re.S).group(1)), f"({when})")
        description = html.unescape(re.search(r'<meta name="description" content="([^"]*)"', text).group(1))
        ledger.equal(f"{w} meta description", description, expected["description"])
        canonical = re.search(r'<link rel="canonical" href="([^"]*)"', text).group(1)
        expected_url = f"{SITE_ORIGIN}/black-holes/" + (f"{sid}/" if label == "frozen" else "")
        ledger.equal(f"{w} canonical", canonical, expected_url)
        ld = json.loads(re.search(r'<script type="application/ld\+json">\s*(.*?)\s*</script>', text, re.S).group(1))
        nodes = {n["@type"]: n for n in ld["@graph"]}
        dataset = nodes["Dataset"]
        ledger.equal(f"{w} Dataset license", dataset["license"], "https://creativecommons.org/licenses/by/4.0/")
        ledger.equal(f"{w} Dataset temporalCoverage", dataset["temporalCoverage"], f"{start}/{end}")
        ledger.equal(f"{w} Dataset CSV link", [d["contentUrl"] for d in dataset["distribution"]],
                     [f"{SITE_ORIGIN}/black-holes/{sid}/porchlamp-{sid}-ranked.csv"])
        ledger.equal(f"{w} Dataset encodingFormat", dataset["distribution"][0]["encodingFormat"], "text/csv")
        ledger.equal(f"{w} Dataset description", dataset["description"], expected["dataset"])
        ledger.equal(f"{w} Article description", nodes["Article"]["description"], expected["description"])
        ledger.equal(f"{w} Article headline", nodes["Article"]["headline"], rp.copy_js_string("REPORT_H1"))
        ledger.equal(f"{w} Dataset url", dataset["url"], expected_url)
        # what each page loads: the latest is the app's page; the frozen copy, no app and its own stylesheet
        sheets = re.findall(r'<link rel="stylesheet" href="([^"]*)">', text)
        if label == "frozen":
            ledger.equal(f"{w} stylesheets", sheets, [f"/black-holes/{sid}/styles.css"])
            ledger.equal(f"{w} loads no app", "/app.js" in text, False)
            ledger.equal(f"{w} is no page the app takes over", "data-prerendered" in text, False)
        else:
            ledger.equal(f"{w} stylesheets", sheets, re.findall(r'<link rel="stylesheet" href="([^"]*)">', (site / "404.html").read_text(encoding="utf-8")))
            ledger.equal(f"{w} is the app's page", '<script type="module" src="/app.js"></script>' in text, True)
    if "latest" in texts and "frozen" in texts:
        ledger.equal("latest and frozen draw the same report", _report_main(texts["frozen"]), _report_main(texts["latest"]))
    styles = site / "black-holes" / sid / "styles.css"
    ledger.equal(
        f"black-holes/{sid}/styles.css is the site's stylesheets, joined",
        styles.read_text(encoding="utf-8") if styles.is_file() else None, _shipped_stylesheet(site),
    )
    # the older frozen copies a publish carries from the published tree are never drawn again: what they link is here
    ledger.equal("the files the carried frozen copies link (export_static.CARRIED_FROZEN_ASSETS), missing",
                 [asset for asset in _export_static().CARRIED_FROZEN_ASSETS if not (site / asset).is_file()], [])

    # the page again, from a record that holds the values worked out here: the view's own bytes (ENG-17)
    record_path = site / "data" / sid / "report.json"
    again = None
    if record_path.is_file() and "latest" in texts:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        values = {k: v for k, v in hooks.items() if "." not in k and k != "source_db_sha256"}
        ledger.equal("report.json values are the ones worked out here", record.get("values"), values)
        # the record's words (its templates) with this file's numbers: a wrong number in report.json cannot agree
        record.update({
            "values": values,
            "counts": {"judged": judged, "black_holes": black_holes},
            "bands": [
                {"key": key, "label": hooks[f"bands.{i}.label"], "judged": band["n"], "black_holes": band["black_holes"],
                 "judged_text": hooks[f"bands.{i}.judged"], "black_holes_text": hooks[f"bands.{i}.black_holes"],
                 "share_text": hooks[f"bands.{i}.share"]}
                for i, (key, band) in enumerate(bands)
            ],
            "ecosystems": [
                {"key": eco, "name": hooks[f"ecosystems.{i}.name"], "path": f"/{ECOSYSTEM_PAGE_SLUGS[eco]}/",
                 "judged": per_eco[eco][0], "black_holes": per_eco[eco][1], "judged_text": hooks[f"ecosystems.{i}.judged"],
                 "black_holes_text": hooks[f"ecosystems.{i}.black_holes"], "share_text": hooks[f"ecosystems.{i}.share"]}
                for i, eco in enumerate(eco_order)
            ],
        })
        again = _redraw_report(site, sid, snapshot, methodology, db_sha, record)
        ledger.equal("the report is what the prerender draws from the values worked out here (bytes)", again, _report_main(texts["latest"]))
    else:
        ledger.errors.append(f"data/{sid}/report.json is missing")

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
        for row, want_row in zip(table[1:], boards["balanced"]):
            want = [str(want_row["rank"]), want_row["owner"], want_row["name"], want_row["ecosystem"], str(want_row["porchlamp"]),
                    want_row["tier"] or "", str(want_row["coverage"]), want_row["quadrant"] or ""]
            want += [lens_rank[lens].get((want_row["owner"], want_row["name"]), "") for lens in lenses]
            if row != want:
                wrong += 1
                if wrong <= 3:
                    ledger.errors.append(f"CSV row {row[:3]}: expected {want!r}, found {row!r}")
        csv_cells = ranked * (len(rp.CSV_COLUMNS) + len(lenses))
        if wrong > 3:
            ledger.errors.append(f"CSV: {wrong} rows differ from the API's leaderboards")
    else:
        ledger.errors.append(f"{csv_path.relative_to(site)} is missing")

    # the methodology page is the app's own view (web/app/routes.js), checked by verify_methodology: here, that it is
    # the app at the view's address
    meth_path = site / "methodology" / "index.html"
    if meth_path.is_file():
        page_text = meth_path.read_text(encoding="utf-8")
        ledger.equal("methodology canonical", re.search(r'<link rel="canonical" href="([^"]*)"', page_text).group(1), f"{SITE_ORIGIN}/methodology/")
        ledger.equal("methodology is the app's page", '<script type="module" src="/app.js"></script>' in page_text, True)
    else:
        ledger.errors.append("methodology/index.html is missing")

    titles = []
    for rel in (*pages, "methodology/index.html"):
        if (site / rel).is_file():
            titles.append(re.search(r"<title>(.*?)</title>", (site / rel).read_text(encoding="utf-8"), re.S).group(1))
    ledger.equal("the three titles differ", len(set(titles)), 3)
    log(
        f"verify: report pages: {len(hooks)} hooks per page (" + ", ".join(f"{label} {n} printed" for label, n in counts.items())
        + f"), checked {ledger.checked} values; re-drawn from the values worked out here "
        f"{'byte-identical' if again is not None and again == _report_main(texts.get('latest', '')) else 'DIFFERENT'}; "
        f"{csv_cells} CSV cells"
    )
    _log_mismatches(log, "REPORT", ledger.errors)
    return {"checked": ledger.checked, "hooks": len(hooks), "csv_cells": csv_cells, "errors": ledger.errors,
            "record": record if again is not None else None, "black_hole_numbers": numbers}


def _redraw_report(site: Path, sid: str, snapshot: dict, methodology: dict, db_sha: str, record: dict) -> Optional[str]:
    """The report's ``<main>`` as ``web/prerender.mjs`` draws it from ``record`` (with the API's snapshot and
    methodology, and this file's digest of the database), into the export's own page skeleton for the view."""
    export_static = _export_static()
    node = find_node()
    route = next(r for r in export_static.app_routes() if r["id"] == "report")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data = root / "data" / sid
        data.mkdir(parents=True)
        for name, payload in (("snapshot", snapshot), ("methodology", methodology), ("manifest", {"source_db_sha256": db_sha}),
                              ("report", record)):
            (data / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
        (root / "api.js").write_bytes(export_static.STATIC_API.read_bytes())
        (root / "data-version.js").write_text(export_static.data_version_js(sid), encoding="utf-8")
        (root / "black-holes").mkdir()
        (root / "black-holes" / "index.html").write_text(export_static.view_page(route), encoding="utf-8")
        (root / "job.json").write_text(json.dumps({"pages": [{"path": "black-holes/index.html", "view": "report"}]}), encoding="utf-8")
        ran = subprocess.run([node, str(REPO_ROOT / "web" / "prerender.mjs"), str(root), str(root / "job.json")],
                             capture_output=True, text=True, timeout=120, check=False)
        if ran.returncode != 0:
            return f"prerender failed: {ran.stderr[-600:]}"
        return _report_main((root / "black-holes" / "index.html").read_text(encoding="utf-8"))


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
    the text inside it), the h1, h2 and h3 headings, every element id and every link; with ``elements``, every element
    too (tag, id, classes, ``aria-labelledby``, href, parent, text), for :meth:`find`."""

    _VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"})

    def __init__(self, *, elements: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self.hooks: list[tuple[str, str]] = []
        self.headings: list[tuple[str, str]] = []
        self.ids: set[str] = set()
        self.links: list[str] = []
        self.elements: Optional[list[dict]] = [] if elements else None
        # (tag, hook id or heading, text so far, index in self.elements)
        self._open: list[tuple[str, Optional[str], list[str], Optional[int]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        found = dict(attrs)
        if found.get("id"):
            self.ids.add(found["id"])
        if tag == "a" and found.get("href") is not None:
            self.links.append(found["href"])
        index = None
        if self.elements is not None:
            parent = next((entry[3] for entry in reversed(self._open) if entry[3] is not None), None)
            index = len(self.elements)
            self.elements.append({
                "index": index, "tag": tag, "id": found.get("id"), "class": (found.get("class") or "").split(),
                "aria": found.get("aria-labelledby"), "href": found.get("href"), "parent": parent, "text": "",
            })
        if tag in self._VOID:
            return
        mark = f"v:{found['data-v']}" if found.get("data-v") is not None else (f"h:{tag}" if tag in ("h1", "h2", "h3") else None)
        self._open.append((tag, mark, [], index))

    def handle_endtag(self, tag: str) -> None:
        if all(entry[0] != tag for entry in self._open):
            return  # a stray end tag closes nothing
        while self._open:
            open_tag, mark, parts, index = self._open.pop()
            text = "".join(parts)
            if index is not None:
                self.elements[index]["text"] = re.sub(r"\s+", " ", text).strip()
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

    def _within(self, element: dict, ancestor: dict) -> bool:
        parent = element["parent"]
        while parent is not None:
            if parent == ancestor["index"]:
                return True
            parent = self.elements[parent]["parent"]
        return False

    def find(self, *, tag: Optional[str] = None, id: Optional[str] = None, cls: Optional[str] = None,
             aria: Optional[str] = None, within: Optional[dict] = None) -> list[dict]:
        """The elements that match every criterion given, in page order (``within``: inside that element)."""
        return [
            e for e in self.elements
            if (tag is None or e["tag"] == tag) and (id is None or e["id"] == id) and (cls is None or cls in e["class"])
            and (aria is None or e["aria"] == aria) and (within is None or self._within(e, within))
        ]

    def element(self, **criteria: Any) -> dict:
        """The first element that matches, or an empty stand-in (so a missing one reads as no text, a mismatch)."""
        found = self.find(**criteria)
        return found[0] if found else {"index": -1, "text": None, "parent": None}

    def text(self, **criteria: Any) -> Optional[str]:
        return self.element(**criteria)["text"]


def read_page(text: str, *, elements: bool = False) -> _PageReader:
    reader = _PageReader(elements=elements)
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


def _chrome_key(page: dict) -> str:
    """What a page's chrome depends on: its view or nav entry, and for a list view its route (the lens and ecosystem
    its header marks)."""
    return json.dumps({k: page[k] for k in ("view", "nav", "route") if k in page}, sort_keys=True)


def _page_kinds(site: Path, snapshot_id: str) -> dict[str, dict]:
    """Each page of the site (every ``index.html``, and ``404.html``), with what the export drew it as: a page of the
    app (``view``), the report's frozen copy (the report view, on a page the app does not load), or another page
    (``nav``, its nav entry or None), each of a script ``kind`` (``report_pages.PAGE_SCRIPTS``). A view whose page needs
    ``--stats`` (the report) is a page of the site only when the export wrote it."""
    export_static = _export_static()
    pages: dict[str, dict] = {"404.html": {"view": "notfound", "kind": "app"}}
    for route in export_static.app_routes():
        if route["shell"] and route.get("fixed"):  # a list view: the leaderboard at each address the export wrote
            for path in sorted(site.glob(route["path"].strip("/").replace("{page}", "*") + "/index.html")):
                address = "/" + path.parent.relative_to(site).as_posix() + "/"
                pages[path.relative_to(site).as_posix()] = {"view": route["view"], "address": address, "route": route["id"], "kind": "app"}
            continue
        rel = f"{route['path'].strip('/')}/index.html".lstrip("/") if route["shell"] else None
        if rel and (route["id"] not in export_static.VIEWS_WRITTEN_ELSEWHERE or (site / rel).is_file()):
            pages[rel] = {"view": route["id"], "kind": "app"}
    frozen = f"black-holes/{snapshot_id}/index.html"
    if (site / frozen).is_file():
        pages[frozen] = {"view": "report", "kind": "page"}
    for path in sorted(site.rglob("index.html")):
        rel = path.relative_to(site).as_posix()
        if rel.startswith("data/") or rel in pages:
            continue
        if rel.startswith("projects/"):
            pages[rel] = {"nav": None, "kind": "project"}
        elif rel == "check/index.html":
            continue  # the /check/ stub has no chrome: verify_projects checks it
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
    pages = _page_kinds(site, sid)
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
            key = _chrome_key(page)
            if key in drawn:
                continue
            # a list's header marks its lens and ecosystem: drawn once per list route, from one of its addresses
            spec = json.dumps({k: page[k] for k in ("view", "nav", "address") if k in page}, sort_keys=True)
            (Path(tmp) / "page.json").write_text(spec, encoding="utf-8")
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
        want = drawn[_chrome_key(page)]
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
    _log_mismatches(log, "CHROME", ledger.errors)
    return {"checked": ledger.checked, "pages": sum(counts.values()), "errors": ledger.errors}


#: The static methodology page's anchors, which the view keeps (old deep links still land).
METHODOLOGY_FORMER_IDS = ("gates", "pillars", "parameters", "missing-data", "lenses", "tie-break", "limits", "reproduce", "hashes")
#: The view's sections, in page order, by their copy.js label names.
METHODOLOGY_SECTIONS = (
    "SNAPSHOT", "GATES", "PILLARS", "PARAMETERS", "MISSINGNESS", "PERSONAS", "TIE_BREAK", "DETECTORS", "LIMITS",
    "PROCESS_CONSTANTS", "RFC_LOG",
)


def verify_methodology(db: Path, site: Path, *, report: Optional[dict] = None, log=print) -> dict:
    """``/methodology/``, the app's own view, drawn into the page at export so it reads without JavaScript: every hook
    it prints equals the value worked out here from the API and the database file (:func:`methodology_hooks`); its h1
    and h2 are the view's; the lens names link the lens pages, the check line links /check/, the old anchors stay and
    the Limits are copy.js's; and the page is byte for byte what ``web/prerender.mjs`` draws from the API's own answers
    (ENG-17). With ``report`` (the report's record, holding the values :func:`verify_report` worked out), the "Why
    Porchlamp" Finding prints its values. ``{"checked", "hooks", "errors"}``."""
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
    if report is not None:
        expected |= {key: report["values"][key] for key in _FINDING_SLOTS}
    _compare_hooks(ledger, "methodology", hooks, expected)
    labels = [rp.copy_js_string(f"METHODOLOGY_SECTION_{name}") for name in METHODOLOGY_SECTIONS]
    ledger.equal("methodology h1", [t for tag, t in page.headings if tag == "h1"], [rp.copy_js_string("METHODOLOGY_HEADING")])
    ledger.equal("methodology h2", [t for tag, t in page.headings if tag == "h2"], [rp.copy_js_string("WHY_HEADING"), *labels])
    lens_links = [f"/{rp.LENS_SLUGS[lens]}/" for lens in methodology["personas"]["presets"] if lens in rp.LENS_SLUGS]
    ledger.equal("methodology links the lens pages", [link for link in page.links if link in lens_links], lens_links)
    ledger.contains("methodology says how to check a project", _plain(text), rp.copy_js_string("METHODOLOGY_CHECK_LINE"))
    ledger.equal("methodology links no /check/", "/check/" in page.links, False)
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
        records = [("snapshot", snapshot), ("methodology", methodology), ("manifest", {"source_db_sha256": db_sha})]
        for name, payload in records + ([("report", report)] if report is not None else []):
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
    _log_mismatches(log, "METHODOLOGY", ledger.errors)
    return {"checked": ledger.checked, "hooks": len(hooks), "errors": ledger.errors}


# --------------------------------------------------------------------------- #
# The project pages, /check/ and the sitemaps
# --------------------------------------------------------------------------- #
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


def _round_half_up(exact: Fraction, places: int) -> str:
    """``exact`` (0 or more) rounded half up to ``places`` decimals, the decimals kept (``3.85`` -> ``3.9``)."""
    scaled = math.floor(exact * 10 ** places + Fraction(1, 2))
    digits = f"{scaled:0{places + 1}d}"
    return f"{digits[:-places]}.{digits[-places:]}" if places else digits


def _printed(x: float) -> Fraction:
    """The decimal a record prints for ``x`` (Python's ``repr``, JavaScript's ``String``), exactly."""
    return Fraction(repr(x))


def _trimmed(text: str) -> str:
    """A trailing ``.0`` dropped (``12.0`` -> ``12``), as the sentences print a rounded value."""
    return text[:-2] if text.endswith(".0") else text


def _days(hours: float) -> str:
    """Hours as days to one decimal, a trailing ``.0`` dropped: the printed hours over 24, exactly, rounded half up."""
    return _trimmed(_round_half_up(_printed(hours) / 24, 1))


def _reply_time(hours: float) -> str:
    """Hours under 48 (``3.9 hours``, ``1 hour``, ``under 0.1 hours``), else days (``6.2 days``)."""
    if hours < 48:
        shown = _trimmed(_round_half_up(_printed(hours), 1))
        if shown == "0":
            return "under 0.1 hours"
        return f"{shown} {'hour' if shown == '1' else 'hours'}"
    return f"{_days(hours)} days"


def _date_range(start: str, end: str) -> str:
    a, b = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
    left = f"{a.day} {_MONTHS[a.month - 1]}" + ("" if a.year == b.year else f" {a.year}")
    return f"{left} to {b.day} {_MONTHS[b.month - 1]} {b.year}"


def _long_date(iso: str) -> str:
    day = date.fromisoformat(iso[:10])
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"


def _js_fixed(x: float, places: int) -> str:
    """JavaScript's ``toFixed`` for any finite number: the sign, then the magnitude's exact double rounded half up."""
    return f"-{_tofixed(-x, places)}" if x < 0 else _tofixed(x, places)


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


#: The parameters the project view prints with a unit: hours (with days), shares (a percent), counts (grouped).
_HOURS_PARAMS = frozenset({"ttfhr_hours", "ttm_p90_hours"})
_SHARE_PARAMS = frozenset({
    "dr90_decision_rate", "mr_first_rate", "gr14_ghost_rate", "substantive_review_rate", "newcomer_conversion", "rr90_return_rate",
})
_COUNT_PARAMS = frozenset({"dependents_deduped", "elephant_factor", "newcomer_issue_supply"})
#: The parameters whose value is a code the view prints as copy.js's plain label, by the name of that label map.
_ENUM_LABELS = {"legal_ip": "LEGAL_IP_LABELS", "ai_posture": "AI_POSTURE_LABELS", "promotion_ladder": "PROMOTION_LADDER_LABELS"}


def _param_text(pid: str, value: Any, words: dict[str, str], labels: dict[str, dict[str, str]]) -> str:
    """A parameter's value as the project view prints it: with its unit, or an enum code's plain label."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        if pid in _HOURS_PARAMS:
            return words["hours_and_days"].replace("{hours}", _round_half_up(_printed(value), 1)).replace("{days}", _days(value))
        if pid in _SHARE_PARAMS:
            return words["share"].replace("{percent}", _round_half_up(_printed(value) * 100, 1))
        if pid == "downloads_monthly" and float(value).is_integer():
            if value >= 1_000_000:
                return words["downloads_million"].replace("{count}", _tofixed(value / 1e6, 1))
            return words["downloads"].replace("{count}", f"{int(value):,}")
        if pid in _COUNT_PARAMS and float(value).is_integer():
            return f"{int(value):,}"
    if pid in labels and value in labels[pid]:
        return labels[pid][value]
    return _param_value(value)


def _key_fact_reply(hours: float, words: dict[str, str]) -> str:
    """The key facts' median first reply: hours under 48, else days with the hours after them."""
    if hours < 48:
        shown = _trimmed(_round_half_up(_printed(hours), 1))
        if shown == "0":
            return words["under_tenth_hour"]
        return words["hour" if shown == "1" else "hours"].replace("{n}", shown)
    return words["days_and_hours"].replace("{days}", _days(hours)).replace("{hours}", _round_half_up(_printed(hours), 1))


def _rank_band(rank: int, total: int) -> str:
    """Where a rank sits, rounded up so that the claim holds: the top N% in the upper half, else the bottom N%."""
    if 2 * rank <= total:
        return f"the top {math.ceil(Fraction(100 * rank, total))}%"
    return f"the bottom {math.ceil(Fraction(100 * (total - rank + 1), total))}%"


def _expected_reply(detail: dict, evidence: list[dict], min_n: dict[str, int]) -> tuple[str, str, str]:
    """``(first sentence, the rest, the first sentence as the head says it)`` of the reply answer, from the API's own
    evidence rows and the methodology's minimum samples. The first sentence names the project; the head's form does
    not, and has no bracket. A value unmeasured for ``collector_failure`` is one whose PR record does not cover the
    window (RFC-0013); any other reason is a too-small sample."""
    ev = {e["param_id"]: e for e in evidence}
    t, g = ev["ttfhr_hours"], ev["gr14_ghost_rate"]
    t_ok, g_ok = t["unmeasured_reason"] is None, g["unmeasured_reason"] is None
    t_gone, g_gone = t["unmeasured_reason"] == "collector_failure", g["unmeasured_reason"] == "collector_failure"
    who = f"{detail['owner']}/{detail['name']}"
    gone = "we do not hold a complete record of its pull requests for the window"

    def prs(n: int) -> str:
        return f"{n:,} pull request" + ("" if n == 1 else "s")

    def g_missing() -> str:
        if g_gone:
            return f"The share with no reply within 14 days is not measured: {gone}."
        return f"The share with no reply within 14 days has too small a sample to measure ({prs(g['n'])} counted; {min_n['gr14_ghost_rate']} needed)."

    if t_ok:
        median = f"got a first human reply in a median of {_reply_time(t['value'])}"
        bracket = f" ({prs(t['n'])}, {_date_range(t['window_start'], t['window_end'])})"
        if g_ok:
            tail = f"; {_half_up(g['value'], 100)}% got none within 14 days."
            return f"Outside pull requests to {who} {median}{bracket}{tail}", "", f"Outside pull requests {median}{tail}"
        return f"Outside pull requests to {who} {median}{bracket}.", g_missing(), f"Outside pull requests {median}."
    if t_gone and g_gone:
        both = "the reply time and the share with no reply within 14 days are not measured"
        return f"For {who}, {both}: {gone}.", "", f"T{both[1:]}."
    if not t_gone and not g_ok and not g_gone:
        lo, hi = min_n["ttfhr_hours"], min_n["gr14_ghost_rate"]
        needed = f"{lo} needed" if lo == hi else f"{lo} and {hi} needed"
        both = "the reply time and the share with no reply within 14 days both have too small a sample to measure"
        return f"For {who}, {both} ({t['n']:,} and {prs(g['n'])} counted; {needed}).", "", f"T{both[1:]}."
    if t_gone:
        first, short = f"For {who}, the reply time is not measured: {gone}.", "The reply time is not measured."
    else:
        first = f"For {who}, the reply time has too small a sample to measure ({prs(t['n'])} counted; {min_n['ttfhr_hours']} needed)."
        short = "The reply time has too small a sample to measure."
    if g_ok:
        noun = "pull request" if g["n"] == 1 else "pull requests"
        rest = f"{_half_up(g['value'], 100)}% of {g['n']:,} outside {noun} got no reply within 14 days ({_date_range(g['window_start'], g['window_end'])})."
    else:
        rest = g_missing()
    return first, rest, short


#: The longest description a project page may have.
_DESCRIPTION_LIMIT = 155


def _expected_description(who: str, rank: int, total: int, short: str) -> str:
    """The project's name and rank, then the reply answer's head form; at most 155 characters: whole, else up to its
    ``;``, else cut at a word with an ellipsis, never into the name and rank."""
    lead = f"{who} ranks {rank:,} of {total:,}."
    whole = f"{lead} {short}"
    if len(whole) <= _DESCRIPTION_LIMIT:
        return whole
    clause = f"{lead} {short.split(';', 1)[0]}."
    if ";" in short and len(clause) <= _DESCRIPTION_LIMIT:
        return clause
    cut = whole[: _DESCRIPTION_LIMIT - 1].rsplit(" ", 1)[0].rstrip(" ;,:.")
    return f"{cut}\u2026" if len(cut) > len(lead) else lead


def _project_copy() -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    """The words the project view fills its numbers into (copy.js): the unit and reply-time templates, the coverage band
    labels and the pillar order; and the plain label of each enum code, by parameter."""
    from porchlamp.serve import report_pages as rp

    words = {
        "units": rp.copy_js_map("PARAM_UNIT_WORDS"), "reply": rp.copy_js_map("REPLY_TIME_WORDS"),
        "bands": rp.copy_js_map("COVERAGE_BAND_LABELS"), "pillars": list(rp.copy_js_map("PILLAR_NAMES")),
        "lenses": list(rp.copy_js_map("PERSONA_NAMES")),
    }
    return words, {pid: rp.copy_js_map(name) for pid, name in _ENUM_LABELS.items()}


def _share(fraction: float) -> str:
    """A fraction as a percent with one decimal, half up from its printed decimal (``0.3445`` -> ``34.5``)."""
    return _round_half_up(_printed(fraction) * 100, 1)


def _black_hole_hooks(detail: dict) -> dict[str, str]:
    """The black-hole card's values (``views/project.js`` ``blackHoleBlock``): the counts and shares, the window, each
    exclusion counted, and each counted pull request's number and date."""
    holed = detail.get("gate_reason") == "black_hole" or "black_hole" in (detail.get("suspicion") or [])
    ev = detail.get("black_hole_evidence") or {}
    counts = [ev.get(key) for key in ("unanswered", "population_n")]
    if not holed or not all(isinstance(n, int) and not isinstance(n, bool) for n in counts) or counts[1] <= 0:
        return {}
    hooks = {"bh.unanswered": str(counts[0]), "bh.population_n": str(counts[1])}
    if isinstance(ev.get("unanswered_share"), (int, float)):
        hooks["bh.unanswered_share"] = _share(ev["unanswered_share"])
    if isinstance(ev.get("ignored"), int) and isinstance(ev.get("ignored_share"), (int, float)):
        hooks |= {"bh.ignored": str(ev["ignored"]), "bh.ignored_share": _share(ev["ignored_share"])}
    if all(isinstance(ev.get(key), (int, float)) for key in ("wilson_lower_bound", "threshold_share", "min_n")):
        hooks |= {"bh.wilson_lower_bound": _share(ev["wilson_lower_bound"]), "bh.threshold_share": _share(ev["threshold_share"]),
                  "bh.min_n": str(ev["min_n"])}
    if ev.get("window_start") and ev.get("window_end"):
        hooks["bh.window"] = _date_range(ev["window_start"], ev["window_end"])
    excluded = ev.get("excluded") or {}
    # draft and self-closed counts exist only when the snapshot recorded them
    measured = ["draft", "self_closed"] if ev.get("rule_inputs_measured") is True else []
    for key in (*measured, "new_account", "bot", "internal", "under_14d"):
        if (excluded.get(key) or 0) > 0:
            hooks[f"bh.excluded.{key}"] = str(excluded[key])
    prs = [pr for pr in ev.get("counted_prs") or [] if isinstance(pr.get("number"), int) and pr["number"] > 0]
    for i, pr in enumerate(prs):
        hooks[f"bh.counted_prs.{i}.number"] = str(pr["number"])
        if pr.get("created_at"):
            hooks[f"bh.counted_prs.{i}.created_at"] = _long_date(pr["created_at"])
    return hooks


def _expected_project_hooks(
    detail: dict, evidence: Optional[list[dict]], standings: Optional[dict[str, tuple[Optional[int], int, Optional[str]]]],
    similar: Optional[list[dict]], answer: Optional[str], words: dict[str, Any], labels: dict[str, dict[str, str]],
    *, mode: str = "page",
) -> dict[str, str]:
    """Every value the project view (``views/project.js``) prints for a record (a ``data-v`` hook), as it formats it, from
    the API's own record and evidence and, for a ranked project, its lens standings (``(rank, total, reason)`` per
    lens), its similar projects and its answer (None where the record has none). ``mode`` is "page" or "panel"; only
    the page prints the snapshot date."""
    ranked = bool(detail["eligible"] and detail["rated"])
    hooks = {"detail.snapshot_id": detail["snapshot_id"]}
    if mode == "page":
        hooks["detail.snapshot_date"] = _long_date(detail["snapshot_id"])
    if answer is not None:
        hooks["answer"] = answer
    if detail["raw_hash"]:
        hooks["detail.raw_hash"] = detail["raw_hash"]
    if ranked:
        # the key facts and the rating card, and the cite line
        hooks["cite.year"] = detail["snapshot_id"][:4]
        if detail["rank"] is not None:
            hooks["detail.rank"] = f"{detail['rank']:,}"
            if standings:
                hooks["standings.balanced.total"] = f"{standings['balanced'][1]:,}"
        if detail["porchlamp"] is not None:
            hooks["detail.porchlamp"] = _js_fixed(detail["porchlamp"], 2)
        cov = detail["coverage"]
        band = "full" if cov >= 0.9 else "A" if cov >= 0.8 else "B" if cov >= 0.6 else "below"
        hooks["detail.coverage"] = f"{math.floor(cov * 100 + 0.5)}% · {words['bands'].get(band, 'Below floor')}"
        for key in ("leverage", "friction"):
            if detail[key] is not None:
                hooks[f"detail.{key}"] = _js_fixed(float(detail[key]), 1)
            if detail[f"{key}_coverage"] is not None:
                hooks[f"detail.{key}_coverage"] = f"{math.floor(detail[f'{key}_coverage'] * 100 + 0.5)}%"
        by_id = {row["param_id"]: row for row in evidence or []}
        reply, ghost = (by_id.get(pid) for pid in ("ttfhr_hours", "gr14_ghost_rate"))
        if reply and not reply["unmeasured_reason"] and reply["value"] is not None:
            hooks["facts.reply_time"] = _key_fact_reply(reply["value"], words["reply"])
        if ghost and not ghost["unmeasured_reason"] and ghost["value"] is not None:
            hooks["facts.no_reply_share"] = f"{_half_up(ghost['value'], 100)}%"
        if standings:
            first_rank, first_total, first_reason = standings["first_timer"]
            if first_rank is not None:
                hooks["standings.first_timer.rank"], hooks["standings.first_timer.total"] = f"{first_rank:,}", f"{first_total:,}"
            elif first_reason:
                hooks["standings.first_timer.reason"] = first_reason
    hooks |= _black_hole_hooks(detail)
    # the pillars
    if any(detail[f"p_{p}"] is not None for p in words["pillars"]):
        for p in words["pillars"]:
            if detail[f"p_{p}"] is not None:
                hooks[f"detail.p_{p}"] = _js_fixed(detail[f"p_{p}"], 1)
            hooks[f"detail.c_{p}"] = f"{math.floor((detail[f'c_{p}'] or 0) * 100 + 0.5)}%"
    # the parameters
    for row in evidence or []:
        if row["unmeasured_reason"]:
            continue
        ident = f"evidence.{row['param_id']}"
        if row["value"] is not None:
            hooks[f"{ident}.value"] = _param_text(row["param_id"], row["value"], words["units"], labels)
        if row["score"] is not None:
            hooks[f"{ident}.score"] = _js_fixed(row["score"], 1)
        if row["n"]:
            hooks[f"{ident}.n"] = f"{row['n']:,}"
        if row["window_start"] and row["window_end"]:
            hooks[f"{ident}.window"] = _date_range(row["window_start"], row["window_end"])
    # the lens table: a lens the project is not gated from, with a composite
    composites, gates = detail.get("persona_composites") or {}, detail.get("persona_gates") or {}
    for lens in words["lenses"]:
        if not detail["eligible"] or gates.get(lens) or not composites.get(lens):
            continue
        rank, total, _reason = (standings or {}).get(lens, (None, None, None))
        if rank is not None:
            hooks[f"standings.{lens}.rank"], hooks[f"standings.{lens}.total"] = f"{rank:,}", f"{total:,}"
        if composites[lens].get("porchlamp") is not None:
            hooks[f"detail.persona_composites.{lens}.porchlamp"] = _js_fixed(composites[lens]["porchlamp"], 2)
    for i, row in enumerate(similar or []):
        hooks[f"similar.{i}.rank"] = f"{row['rank']:,}"
    return hooks


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


def _expected_banner(detail: dict, floor: Any, gate_copy: dict[str, str], unrated_copy: dict[str, str]) -> str:
    """The sentence the project view's banner prints for a project that is not ranked (copy.js ``gateReasonText`` for a
    project disqualified before scoring, ``unratedReasonText`` for one unrated), from the API's record and the
    methodology's coverage floor (None where it states none)."""
    from porchlamp.serve import report_pages as rp

    gate, unrated = detail.get("gate_reason") or "", detail.get("unrated_reason") or ""
    if not detail["eligible"] and not gate.startswith("unrated:"):
        return gate_copy.get(gate) or unrated_copy.get(gate.removeprefix("unrated: ")) or gate
    reason = unrated or gate
    bare = reason.removeprefix("unrated: ")
    if bare != "insufficient_evidence":
        return unrated_copy.get(bare) or reason
    if not isinstance(floor, (int, float)) or isinstance(floor, bool):
        return rp.copy_js_map("NOT_RANKED_LIST_REASONS")["insufficient_evidence"]
    coverage = detail["coverage"]
    clause = rp.copy_js_string("UNRATED_NO_RESPONSIVENESS" if coverage >= floor else "UNRATED_COVERAGE_BELOW_FLOOR")
    whole = lambda share: f"{math.floor(share * 100 + 0.5)}%"  # noqa: E731
    return f"{unrated_copy[bare]} " + clause.replace("{coverage}", whole(coverage)).replace("{floor}", whole(floor))


def _expected_stub_description(who: str, sentence: str) -> str:
    """The head's description of a project that is not ranked: its name, then its reason's sentence with a lower-case first
    letter; at most 155 characters: whole, else cut at a word with an ellipsis, never into the name."""
    lead = f"{who} is not ranked: "
    whole = lead + sentence.rstrip(".")[:1].lower() + sentence.rstrip(".")[1:] + "."
    if len(whole) <= _DESCRIPTION_LIMIT:
        return whole
    cut = whole[: _DESCRIPTION_LIMIT - 1].rsplit(" ", 1)[0].rstrip(" ;,:.")
    return f"{cut}\u2026" if len(cut) > len(lead.rstrip()) else lead.rstrip(" :")


def _verify_stub(
    ledger: "_Ledger", site: Path, sid: str, detail: dict, evidence: list[dict], context: dict[str, Any],
) -> tuple[str, str, str]:
    """The page at a not-ranked project's address, which says it is not ranked and why: its head (title with the snapshot's
    date, self canonical, description and previews from the reason's sentence), the banner, no rank or answer, the black-hole
    card and every value the view prints (hooks), the outline and the links. Returns ``(title, canonical, description)``."""
    owner, name = detail["owner"], detail["name"]
    key, rel = f"{owner}/{name}", f"projects/{owner.lower()}/{name.lower()}/index.html"
    text = (site / rel).read_text(encoding="utf-8")
    main = _report_main(text)
    if main is None:
        ledger.errors.append(f"{rel}: the page has no <main> the project view drew into")
        return "", "", ""
    page = read_page(main)
    ledger.equal(f"{rel} is not a rated project's record", detail["rated"] or detail["rank"] is not None, False)
    canonical = re.search(r'<link rel="canonical" href="([^"]*)"', text).group(1)
    ledger.equal(f"{rel} canonical", canonical, f"{SITE_ORIGIN}/projects/{owner.lower()}/{name.lower()}/")
    title = html.unescape(re.search(r"<title>(.*?)</title>", text, re.S).group(1))
    ledger.equal(f"{rel} <title>", title, f"{key}: not ranked in the Porchlamp snapshot of {_long_date(sid)} | Porchlamp")
    code = (detail.get("gate_reason") or detail.get("unrated_reason") or "").removeprefix("unrated: ")
    gate_copy, unrated_copy = context["gate_copy"], context["unrated_copy"]
    sentence = gate_copy.get(code) or unrated_copy.get(code)
    if sentence is None:
        ledger.errors.append(f"{rel}: the reason {code!r} has no wording in copy.js")
        return title, canonical, ""
    description = html.unescape(re.search(r'<meta name="description" content="([^"]*)"', text).group(1))
    ledger.equal(f"{rel} meta description", description, _expected_stub_description(key, sentence))
    for tag in ('property="og:description"', 'name="twitter:description"'):
        found = re.search(rf'<meta {tag} content="([^"]*)"', text)
        ledger.equal(f"{rel} <meta {tag}>", html.unescape(found.group(1)) if found else None, description)
    ledger.equal(f"{rel} robots", 'content="index, follow' in text, True)

    banner = re.findall(r'<p class="gate-banner project-gate">(.*?)</p>', main, re.S)
    ledger.equal(f"{rel} reason banner", [html.unescape(b) for b in banner], [_expected_banner(detail, context["floor"], gate_copy, unrated_copy)])
    ledger.equal(f"{rel} says it is not ranked, with the snapshot's date", re.findall(r'<p class="project-lede">(.*?)</p>', main, re.S),
                 [f'{context["lede"]}'.replace("{date}", f'<span data-v="detail.snapshot_date">{_long_date(sid)}</span>')])
    ledger.equal(f"{rel} prints no rank, answer or rating", [m for m in ("project-answer", "key-facts-card", 'id="rating"', 'id="similar"') if m in main], [])
    before = ledger.checked
    _compare_hooks(ledger, rel, page.hooks, _expected_project_hooks(detail, evidence, None, None, None, context["words"], context["labels"]))
    found_bad = _BAD_TEXT.search(_plain(main).replace(key, "").replace(name, ""))
    if found_bad:
        ledger.errors.append(f"{rel}: the page prints {found_bad.group(0)!r}")
    ledger.equal(f"{rel} h1", [heading for tag, heading in page.headings if tag == "h1"], [key])
    levels = [int(tag[1]) for tag, _ in page.headings]
    ledger.equal(f"{rel} no heading level skipped", all(b <= a + 1 for a, b in zip(levels, levels[1:])), True)
    ledger.equal(f"{rel} no collapsed section", "<details" in main, False)
    for missing in sorted({"evidence", "correction"} - page.ids):
        ledger.errors.append(f"{rel}: no element with id {missing!r}")
    bucket = _export_static().bucket_of(owner, name)
    for link in (
        "/", f"https://github.com/{quote(owner, safe='')}/{quote(name, safe='')}", f"/compare/?cmp={quote(key, safe='')}",
        f"/data/{sid}/repos/{bucket}.json", "/methodology/", context["issue_base"] + quote(context["issue_prefix"] + key, safe="!*'()"),
    ):
        ledger.equal(f"{rel} links to {link}", link in page.links, True)
    ld = json.loads(re.search(r'<script type="application/ld\+json">\s*(.*?)\s*</script>', text, re.S).group(1))
    ledger.equal(f"{rel} breadcrumb", ld, {
        "@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Porchlamp", "item": f"{SITE_ORIGIN}/"},
            {"@type": "ListItem", "position": 2, "name": key, "item": canonical}]})
    context["hooks_checked"] += ledger.checked - before
    return title, canonical, description


def verify_projects(db: Path, site: Path, *, evidence_files: Any = None, previous_site: Optional[Path] = None, log=print) -> dict:
    """Recompute every value on every project page from the API (the leaderboards, ``/repos/{owner}/{name}`` and its
    ``/evidence``) and compare with what the page prints (its ``data-v`` hooks) and its head; check the page set, the
    sitemaps and the /check/ stub. A page at the address of a project that is not ranked must be that project's stub (the
    title, description, reason banner and values of :func:`_verify_stub`); with ``previous_site`` (the site as last
    published) every project it had a page for must still have one, ranked or stub, unless the snapshot no longer holds
    it. ``evidence_files`` are the files the export read (a black hole's counts come from them).
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
        client = TestClient(create_app(str(copy), web_dir=str(Path(tmp) / "__no_web__"), evidence_files=evidence_files))

        def api(path: str, **params: Any) -> Any:
            response = client.get(path, params=params)
            if response.status_code != 200:
                raise SystemExit(f"verify: GET {path} -> {response.status_code}")
            return response.json()

        snapshot = api("/v1/porchlamp/snapshot")
        methodology = api("/v1/porchlamp/methodology")
        found_dirs = {p.parent.relative_to(site).as_posix() for p in (site / "projects").rglob("index.html")} if (site / "projects").is_dir() else set()
        held: dict[str, list[tuple[str, str]]] = {}
        names = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        try:
            for owner, name in names.execute("SELECT owner, name FROM repositories WHERE snapshot_id = ?", (snapshot["snapshot_id"],)):
                held.setdefault(f"projects/{owner.lower()}/{name.lower()}", []).append((owner, name))
        finally:
            names.close()
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
        ranked_dirs = {f"projects/{r['owner'].lower()}/{r['name'].lower()}" for r in boards["balanced"][0]}
        stub_records = {}
        for directory in sorted(found_dirs - ranked_dirs):
            if len(held.get(directory, [])) == 1:
                owner, name = held[directory][0]
                path = f"/v1/porchlamp/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
                stub_records[(owner, name)] = (api(path), api(path + "/evidence"))

    sid, ranked = snapshot["snapshot_id"], boards["balanced"][0]
    as_of = snapshot["as_of"][:10]
    gate_copy = rp.copy_js_map("PERSONA_GATE_COPY")
    insufficient = rp.copy_js_string("PERSONA_INSUFFICIENT_EVIDENCE_TEXT")
    words, labels = _project_copy()
    issue_base, issue_prefix = rp.copy_js_string("ISSUE_BASE"), rp.copy_js_string("ISSUE_TITLE_PREFIX")
    boilerplate = html.unescape(re.search(r'<meta name="description" content="([^"]*)"', export_static.APP_SHELL.read_text(encoding="utf-8")).group(1))
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
    ledger.equal("project pages in the export", len(found_dirs & expected_dirs), len(expected_dirs))
    for missing in sorted(expected_dirs - found_dirs)[:5]:
        ledger.errors.append(f"project page {missing}/ is missing")
    for extra in sorted(found_dirs - expected_dirs):
        if len(held.get(extra, [])) != 1:
            ledger.errors.append(f"project page {extra}/ is not a ranked project and is not a project in the snapshot")
    if previous_site is not None:
        for owner, name in sorted(export_static.previous_project_pages(previous_site)):
            directory = f"projects/{owner}/{name}"
            if directory not in found_dirs and held.get(directory):
                ledger.errors.append(f"project page {directory}/ was published before and the snapshot holds the project, "
                                     "but the export has no page for it")

    titles, canonicals, descriptions, hooks_checked = [], [], [], 0
    for (owner, name), (detail, evidence) in records.items():
        rel = f"projects/{owner.lower()}/{name.lower()}/index.html"
        who, key = rel, f"{owner}/{name}"
        path = site / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        main = _report_main(text)
        if main is None:
            ledger.errors.append(f"{who}: the page has no <main> the project view drew into")
            continue
        page = read_page(main)

        # the head: title, canonical, the description (the project, its rank and its reply answer), previews
        canonical = re.search(r'<link rel="canonical" href="([^"]*)"', text).group(1)
        ledger.equal(f"{who} canonical", canonical, f"{SITE_ORIGIN}/projects/{owner.lower()}/{name.lower()}/")
        canonicals.append(canonical)
        title = html.unescape(re.search(r"<title>(.*?)</title>", text, re.S).group(1))
        titles.append(title)
        ledger.equal(f"{who} <title>", title, f"{key}: pull request response time and contributor experience | Porchlamp")
        bal, total = lens_rank["balanced"][(owner, name)], lens_total["balanced"]
        ledger.equal(f"{who} the record's rank is the balanced leaderboard's", detail["rank"], bal)
        first, rest, short = _expected_reply(detail, evidence, min_n)
        description = html.unescape(re.search(r'<meta name="description" content="([^"]*)"', text).group(1))
        ledger.equal(f"{who} meta description", description, _expected_description(key, bal, total, short))
        ledger.equal(
            f"{who} meta description names the project and its rank in at most {_DESCRIPTION_LIMIT} characters",
            len(description) <= _DESCRIPTION_LIMIT and description.startswith(f"{key} ranks {bal:,} of {total:,}.") and description != boilerplate,
            True,
        )
        descriptions.append(description)
        for tag in ('property="og:description"', 'name="twitter:description"'):
            found = re.search(rf'<meta {tag} content="([^"]*)"', text)
            ledger.equal(f"{who} <meta {tag}>", html.unescape(found.group(1)) if found else None, description)

        # every value the page prints, recomputed: the answer, the key facts, the rating, the pillars, the parameters,
        # the lens table, the similar projects and the record's identity
        standings = {}
        for lens in pp.LENS_ORDER:
            r = lens_rank[lens].get((owner, name))
            if r is not None:
                standings[lens] = (r, lens_total[lens], None)
            else:
                code = detail["persona_gates"][lens]
                standings[lens] = (None, lens_total[lens], insufficient if code.startswith("insufficient_evidence:") else gate_copy[code])
        first_rank, first_total, first_reason = standings["first_timer"]
        if first_rank is not None:
            timer = f"For first-time contributors it ranks {first_rank:,} of {first_total:,} ({_rank_band(first_rank, first_total)})."
        else:
            reason = (first_reason or "").rstrip(".")
            timer = f"It is not ranked for first-time contributors: {reason[:1].lower() + reason[1:]}."
        answer = " ".join(part for part in (first, rest, f"It ranks {bal:,} of {total:,} overall.", timer) if part)
        here = place_of[(owner, name)]
        near = sorted((q for q in by_eco[detail["ecosystem"]] if q != here), key=lambda q: (abs(q - here), q))[:5]
        similar = [ranked[q] for q in sorted(near)]
        before = ledger.checked
        _compare_hooks(ledger, who, page.hooks, _expected_project_hooks(detail, evidence, standings, similar, answer, words, labels))
        hooks_checked += ledger.checked - before
        found_bad = _BAD_TEXT.search(_plain(main).replace(key, "").replace(name, ""))  # a project may be called "null"
        if found_bad:
            ledger.errors.append(f"{who}: the page prints {found_bad.group(0)!r}")

        # the outline: one h1 (the project), no level skipped, nothing collapsed, the anchors links into the page use
        ledger.equal(f"{who} h1", [heading for tag, heading in page.headings if tag == "h1"], [key])
        levels = [int(tag[1]) for tag, _ in page.headings]
        ledger.equal(f"{who} no heading level skipped", all(b <= a + 1 for a, b in zip(levels, levels[1:])), True)
        ledger.equal(f"{who} no collapsed section", "<details" in main, False)
        anchors = {"answer", "key-facts", "rating", "pillars", "parameters", "lenses", "evidence", "correction", "similar"}
        anchors |= {f"parameters-{row['pillar']}" for row in evidence}
        for missing in sorted(anchors - page.ids):
            ledger.errors.append(f"{who}: no element with id {missing!r}")

        # the links: home, GitHub, Compare, the ecosystem's page, the record's file, the methodology, the issue form, and
        # the similar projects' pages under their names
        bucket = export_static.bucket_of(owner, name)
        for link in (
            "/", f"https://github.com/{quote(owner, safe='')}/{quote(name, safe='')}", f"/compare/?cmp={quote(key, safe='')}",
            f"/{ECOSYSTEM_PAGE_SLUGS[detail['ecosystem']]}/", f"/data/{sid}/repos/{bucket}.json", "/methodology/",
            issue_base + quote(issue_prefix + key, safe="!*'()"),
        ):
            ledger.equal(f"{who} links to {link}", link in page.links, True)
        similar_card = re.search(r'<section class="project-card" id="similar"[^>]*>(.*?)</section>', main, re.S)
        listed = re.findall(r'<li><a href="(/projects/[^"]+)">([^<]+)</a>', similar_card.group(1) if similar_card else "")
        ledger.equal(f"{who} similar projects", [(href, html.unescape(label)) for href, label in listed], [
            (f"/projects/{r['owner'].lower()}/{r['name'].lower()}/", f"{r['owner']}/{r['name']}") for r in similar])
        ld = json.loads(re.search(r'<script type="application/ld\+json">\s*(.*?)\s*</script>', text, re.S).group(1))
        ledger.equal(f"{who} breadcrumb", ld, {
            "@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Porchlamp", "item": f"{SITE_ORIGIN}/"},
                {"@type": "ListItem", "position": 2, "name": key, "item": canonical}]})
    context = {
        "gate_copy": rp.copy_js_map("GATE_REASON_COPY"), "unrated_copy": rp.copy_js_map("UNRATED_REASON_COPY"),
        "floor": ((methodology.get("missingness") or {}).get("coverage_thresholds") or {}).get("unrated_below"),
        "lede": rp.copy_js_string("PROJECT_LEDE_NOT_RANKED"), "words": words, "labels": labels,
        "issue_base": issue_base, "issue_prefix": issue_prefix, "hooks_checked": 0,
    }
    for (_owner, _name), (detail, evidence) in sorted(stub_records.items()):
        stub_title, stub_canonical, stub_description = _verify_stub(ledger, site, sid, detail, evidence, context)
        titles.append(stub_title)
        canonicals.append(stub_canonical)
        descriptions.append(stub_description)
    hooks_checked += context["hooks_checked"]
    ledger.equal("project page titles are all different", len(set(titles)), len(titles))
    ledger.equal("project page canonicals are all different", len(set(canonicals)), len(canonicals))

    # sitemaps
    projects_sitemap = site / "sitemap-projects.xml"
    if projects_sitemap.is_file():
        locs = re.findall(r"<loc>([^<]+)</loc>", projects_sitemap.read_text(encoding="utf-8"))
        ledger.equal("sitemap-projects.xml lists every ranked project by balanced rank", locs, [
            f"{SITE_ORIGIN}/projects/{r['owner'].lower()}/{r['name'].lower()}/" for r in ranked])
        lastmods = set(re.findall(r"<lastmod>([^<]+)</lastmod>", projects_sitemap.read_text(encoding="utf-8")))
        ledger.equal("sitemap-projects.xml lastmod is the snapshot's as_of date", lastmods, {as_of})
    else:
        ledger.errors.append("sitemap-projects.xml is missing")
    for problem in check_sitemap_files(site):
        ledger.errors.append(problem)

    # /check/: a stub that sends the address on to the rankings; the header's search finds a project
    stub = site / "check" / "index.html"
    ledger.equal("check stub is in the export", stub.is_file(), True)
    if stub.is_file():
        text = stub.read_text(encoding="utf-8")
        ledger.equal("check stub refreshes to /", re.findall(r'<meta http-equiv="refresh" content="([^"]*)">', text), ["0; url=/"])
        ledger.equal("check stub canonical", re.findall(r'<link rel="canonical" href="([^"]*)"', text), [f"{SITE_ORIGIN}/"])
        ledger.equal("check stub scripts", re.findall(r"<script\b[^>]*>", text, re.I), [])
        ledger.equal("check stub links /", read_page(text).links, ["/"])
    sitemap_pages = site / "sitemap-pages.xml"
    listed = sitemap_pages.read_text(encoding="utf-8") if sitemap_pages.is_file() else ""
    ledger.equal("check stub is out of sitemap-pages.xml", f"<loc>{SITE_ORIGIN}/check/</loc>" in listed, False)
    ledger.equal("check search files are gone", [rel for rel in ("check/projects.json", "check.js") if (site / rel).exists()], [])

    log(f"verify: project pages: checked {hooks_checked} printed values (data-v hooks) and {ledger.checked - hooks_checked} other "
        f"values on {len(found_dirs & expected_dirs)} of {len(expected_dirs)} ranked pages and {len(stub_records)} pages of projects no longer ranked")
    log(f"verify: project heads: {len(descriptions)} descriptions, each names the project (a ranked project's also its rank); the longest is "
        f"{max(map(len, descriptions), default=0)} characters (at most {_DESCRIPTION_LIMIT}); "
        f"{sum(d == boilerplate for d in descriptions)} are the site's boilerplate")
    _log_mismatches(log, "PROJECT", ledger.errors)
    return {"checked": ledger.checked, "pages": len(found_dirs & expected_dirs) + len(stub_records), "errors": ledger.errors}


# --------------------------------------------------------------------------- #
# The list views and the home page
# --------------------------------------------------------------------------- #
_NUMBER_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}


def _weight_percent(weight: float) -> str:
    from decimal import ROUND_HALF_UP, Decimal

    return f"{(Decimal(repr(weight)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)}%"


def _joined(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _section_after(page: str, ident: str) -> str:
    match = re.search(rf'<h2 id="{re.escape(ident)}">(.*?)(?=<footer)', page, re.S)
    return match.group(1) if match else ""


_PROJECT_ADDRESS = re.compile(r"https://porchlamp\.org/projects/([a-z0-9._-]+)/([a-z0-9._-]+)/")


#: The report values the app's finding lines print (copy.js ``FINDINGS_LINE`` and the methodology's Finding), each in a
#: hook of the report view's own id.
_FINDING_SLOTS = ("one_in", "deadline_days")


def keyed_items(fragment: str, tag: str, cls: Optional[str] = None) -> list[tuple[str, str]]:
    """``(owner, name)`` of each ``tag`` element in ``fragment`` that carries ``data-owner`` and ``data-name`` (and the
    class ``cls``, if given), in page order, whatever the order of its attributes."""
    found = []
    for match in re.finditer(rf"<{tag}\b[^>]*>", fragment):
        attrs = dict(re.findall(r'([a-z][a-z-]*)="([^"]*)"', match.group(0)))
        if "data-owner" in attrs and "data-name" in attrs and (cls is None or cls in attrs.get("class", "").split()):
            found.append((html.unescape(attrs["data-owner"]), html.unescape(attrs["data-name"])))
    return found


_SITE_ADDRESS = re.compile(r"https://porchlamp\.org/([^\s)\"<>#]*)")


def _site_addresses(text: str) -> list[str]:
    """The site paths ``text`` gives as addresses; a pattern such as ``/projects/<owner>/<name>/`` is not one."""
    return sorted({m.group(1) for m in _SITE_ADDRESS.finditer(text) if not re.match(r"<[a-z]", text[m.end():m.end() + 2])})


def _home_view(text: str) -> Optional[str]:
    found = re.search(r'<main class="app-main" id="app-main" tabindex="-1"[^>]*>(.*?)\n *</main>', text, re.S)
    return found.group(1) if found else None


def verify_home(db: Path, site: Path, *, report: Optional[dict], log=print) -> dict:
    """``/`` and the files generated beside it (``porchlamp/serve/home_pages.py``), from facts worked out here: the API's
    snapshot, methodology and first page of the balanced leaderboard, and ``report`` (the report's record, holding the
    values :func:`verify_report` worked out).

    - ``llms.txt`` and ``feed.xml`` are byte for byte what the generators write from those facts.
    - The home page is byte for byte what ``web/prerender.mjs`` draws into the generated head (its description and
      JSON-LD) from those facts: the header, nav and footer, and the ranked view.
    - Read without JavaScript (Python's HTML parser), the page holds the findings line with the report's values and the
      first page of rows, in rank order, each linking its project's page.
    - The JSON-LD has no SoftwareApplication, no Dataset and no search action; its page is about the report's Dataset;
      every ItemList address is a project page of this export.
    ``{"checked", "rows", "errors"}``."""
    from porchlamp.serve import home_pages as hp
    from porchlamp.serve import report_pages as rp

    ledger = _Ledger()
    home, report_page = site / "index.html", site / "black-holes" / "index.html"
    if report is None or not home.is_file() or not report_page.is_file():
        return {"checked": 0, "rows": 0, "errors": ["the report's record, index.html or black-holes/index.html is missing"]}
    text = home.read_text(encoding="utf-8")
    template = (REPO_ROOT / "web" / "app" / "index.html").read_text(encoding="utf-8")
    page_size = hp.TOP_ROWS
    snapshot, methodology, board = _api_answers(
        db, "/v1/porchlamp/snapshot", "/v1/porchlamp/methodology", f"/v1/porchlamp/leaderboard?persona=balanced&page=1&page_size={page_size}")
    export_static = _export_static()
    facts = hp.home_facts(snapshot, methodology, report, board["rows"], export_static.app_routes())
    sid = snapshot["snapshot_id"]

    # the generated files, re-generated here; every address they give is a file of this export
    for name, expected in (("llms.txt", hp.llms_txt(facts)), ("feed.xml", hp.feed_xml(facts))):
        path = site / name
        shipped = path.read_text(encoding="utf-8") if path.is_file() else None
        ledger.equal(f"{name} is what home_pages writes from the facts worked out here (bytes)", shipped, expected)
        for address in _site_addresses(shipped or ""):
            target = site / address / "index.html" if address == "" or address.endswith("/") else site / address
            ledger.equal(f"{name} links https://porchlamp.org/{address}, a file of the export", target.is_file(), True)

    # the page again: the generated head, then the prerender's chrome and ranked view, from those facts
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data = root / "data" / sid
        data.mkdir(parents=True)
        db_sha = hashlib.sha256(db.read_bytes()).hexdigest()
        # the leaderboard file and the sort allow-list api.js checks ``sort`` against: the request grid proves both, request
        # by request, equal to the API (verify)
        shipped = json.loads((site / "data" / sid / "manifest.json").read_text(encoding="utf-8"))
        manifest = {"source_db_sha256": db_sha, "sort_fields": shipped.get("sort_fields", [])}
        for name, payload in (("snapshot", snapshot), ("methodology", methodology), ("manifest", manifest), ("report", report)):
            (data / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
        shutil.copyfile(site / "data" / sid / "leaderboard-balanced.json", data / "leaderboard-balanced.json")
        (root / "api.js").write_bytes(export_static.STATIC_API.read_bytes())
        (root / "data-version.js").write_text(export_static.data_version_js(sid), encoding="utf-8")
        (root / "index.html").write_text(hp.home_page(template, facts), encoding="utf-8")
        (root / "job.json").write_text(json.dumps({"pages": [{"path": "index.html", "view": "ranked"}]}), encoding="utf-8")
        ran = subprocess.run([find_node(), str(REPO_ROOT / "web" / "prerender.mjs"), str(root), str(root / "job.json")],
                             capture_output=True, text=True, timeout=120, check=False)
        again = (root / "index.html").read_text(encoding="utf-8") if ran.returncode == 0 else f"prerender failed: {ran.stderr[-600:]}"
    ledger.equal("index.html is what the prerender draws into the generated head from the facts worked out here (bytes)",
                 again == text, True)

    # read without JavaScript: the findings line and the first page of rows
    view = _home_view(text) or ""
    page = read_page(view)
    findings = [(ident, value) for ident, value in page.hooks if ident in _FINDING_SLOTS]
    _compare_hooks(ledger, "home findings line", findings, {key: report["values"][key] for key in _FINDING_SLOTS})
    line = re.search(r'<span data-findings-text="true">(.*?)</span> <a\b', view, re.S)
    filled = hp.fill(rp.copy_js_string("FINDINGS_LINE"), {key: report["values"][key] for key in _FINDING_SLOTS})
    ledger.equal("home findings line", _plain(line.group(1)) if line else None, filled)
    table = re.search(r'<tbody data-leaderboard-tbody="true"[^>]*>(.*?)</tbody>', view, re.S)
    shown = keyed_items(table.group(1) if table else "", "tr")
    first = [(r["owner"], r["name"]) for r in board["rows"]]
    ledger.equal(f"home table rows are the first {page_size} of the balanced leaderboard, in rank order", shown, first)
    cards = keyed_items(view, "div", "leaderboard-card")
    ledger.equal("home cards are the same rows", cards, first)
    for owner, name in first:
        ledger.equal(f"home links {owner}/{name}'s page", f"/projects/{owner.lower()}/{name.lower()}/" in page.links, True)
    for slug in (*rp.ECOSYSTEM_SLUGS.values(), *rp.LENS_SLUGS.values()):
        ledger.equal(f"home links the landing page /{slug}/", f"/{slug}/" in page.links, True)

    # the structured data
    ld = json.loads(re.search(r'<script type="application/ld\+json">\s*(.*?)\s*</script>', text, re.S).group(1))
    graph = ld["@graph"]
    types = [n["@type"] for n in graph]
    for gone in ("SoftwareApplication", "Dataset"):
        ledger.equal(f"home JSON-LD has no {gone}", gone in types, False)
    website = next((n for n in graph if n["@type"] == "WebSite"), {})
    ledger.equal("home WebSite has no search action (/?q= needs JavaScript to answer)", "potentialAction" in website, False)
    report_ld = json.loads(re.search(r'<script type="application/ld\+json">\s*(.*?)\s*</script>', report_page.read_text(encoding="utf-8"), re.S).group(1))
    dataset_ids = [n["@id"] for n in report_ld["@graph"] if n["@type"] == "Dataset"]
    webpage = next((n for n in graph if n["@type"] == "WebPage"), {})
    ledger.equal("home WebPage about names the report's Dataset @id", webpage.get("about"), {"@id": dataset_ids[0]} if dataset_ids else None)
    for item in next((n for n in graph if n["@type"] == "ItemList"), {}).get("itemListElement", []):
        match = _PROJECT_ADDRESS.fullmatch(item.get("url", ""))
        ledger.equal(f"home ItemList address {item.get('url')} has a page in the export",
                     bool(match) and (site / "projects" / match.group(1) / match.group(2) / "index.html").is_file(), True)

    log(f"verify: home page: {len(shown)} rows and {len(cards)} cards, the findings line, the head and the JSON-LD; "
        f"re-drawn from the facts worked out here {'byte-identical' if again == text else 'DIFFERENT'}; llms.txt and "
        f"feed.xml re-generated; checked {ledger.checked} values")
    _log_mismatches(log, "HOME", ledger.errors)
    return {"checked": ledger.checked, "rows": len(shown), "errors": ledger.errors}


#: A list view's title and description, whole as a search result shows them (PLAN-search R11).
TITLE_MAX, DESCRIPTION_MAX = 70, 155


def _preset_request(route: dict, address: str) -> dict[str, Any]:
    """The leaderboard request a preset's address names: its fixed fields over its defaults over the app's own
    (balanced, every ecosystem, the ranking's order, page 1, 20 a page)."""
    fixed, defaults = route.get("fixed") or {}, route.get("defaults") or {}
    page = 1
    if "{page}" in route["path"]:
        found = re.fullmatch(re.escape(route["path"]).replace(r"\{page\}", r"(\d+)"), address)
        page = int(found.group(1))
    return {
        "persona": fixed.get("persona") or "balanced", "eco": fixed.get("eco"),
        "sort": None if fixed.get("sort") in (None, "rank") else fixed["sort"], "page": page,
        "page_size": fixed.get("page_size") or defaults.get("page_size") or 20,
    }


def _leaderboard_path(request: dict[str, Any]) -> str:
    query = {"persona": request["persona"], "page": request["page"], "page_size": request["page_size"]}
    if request["eco"]:
        query["eco"] = request["eco"]
    if request["sort"]:
        query["sort"] = request["sort"]
    return "/v1/porchlamp/leaderboard?" + "&".join(f"{k}={v}" for k, v in query.items())


def _listed_rows(route: dict, rows: list[dict], min_n: int) -> list[dict]:
    """The rows a list view shows: all of the page, or, on a top view, those before the first row whose median first
    reply is unmeasured or from fewer than the methodology's minimum sample."""
    if not route.get("top"):
        return rows
    shown = []
    for row in rows:
        if row["first_reply_hours"] is None or (row["first_reply_n"] or 0) < min_n:
            break
        shown.append(row)
    return shown


def _expected_list_hooks(route: dict, request: dict, total: int, shown: list[dict], record: Optional[dict],
                         methodology: dict, min_n: int) -> dict[str, str]:
    """The values a list view prints in hooks (``list.*``), worked out here from the API's answers, the methodology and
    the report's record."""
    from porchlamp.serve import home_pages as hp
    from porchlamp.serve import report_pages as rp

    fixed = route.get("fixed") or {}
    hooks: dict[str, str] = {}
    if "{page}" in route["path"]:
        first = (request["page"] - 1) * request["page_size"] + 1
        hooks.update({"list.first": rp.fmt_int(first), "list.last": rp.fmt_int(first + len(shown) - 1), "list.total": rp.fmt_int(total)})
    elif route.get("top"):
        hooks.update({"list.total": rp.fmt_int(len(shown)), "list.min_n": rp.fmt_int(min_n),
                      "list.parameters": rp.fmt_int(len(methodology["parameters"])),
                      "list.pillars": _NUMBER_WORDS[len(methodology["pillars"])]})
    else:
        hooks["list.total"] = rp.fmt_int(total)
        if "persona" in fixed:
            hooks["list.weights"] = hp.lens_weights_text(methodology, fixed["persona"])
        elif record is not None:
            eco = next(e for e in record["ecosystems"] if e["key"] == fixed["eco"])
            hooks.update({"list.judged": eco["judged_text"], "list.eco_share": eco["share_text"],
                          "list.deadline_days": str(record["values"]["deadline_days"]), "list.share": str(record["values"]["share"]),
                          "list.ecosystems_word": _NUMBER_WORDS[len(record["ecosystems"])]})
    return hooks


def _cell_texts(fragment: str, cell: str) -> list[str]:
    return [_plain(m) for m in re.findall(rf'<td data-cell="{cell}"[^>]*>(.*?)</td>', fragment, re.S)]


def _redraw(db: Path, site: Path, snapshot: dict, methodology: dict, report: Optional[dict], jobs: list[dict],
            pages: dict[str, str], ledger: "_Ledger") -> None:
    """Each job's page again, drawn by ``web/prerender.mjs`` through the site's api.js from the API's answers and the
    verifier's own digest and record (the leaderboard and not-ranked files are the ones the request grid proves equal
    to the API), and compared with the export's page byte for byte (``pages``, by address)."""
    export_static = _export_static()
    sid = snapshot["snapshot_id"]
    db_sha = hashlib.sha256(db.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data = root / "data" / sid
        data.mkdir(parents=True)
        shipped = json.loads((site / "data" / sid / "manifest.json").read_text(encoding="utf-8"))
        payloads = [("snapshot", snapshot), ("methodology", methodology),
                    ("manifest", {"source_db_sha256": db_sha, "sort_fields": shipped.get("sort_fields", [])})]
        if report is not None:
            payloads.append(("report", report))
        for name, payload in payloads:
            (data / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
        for pattern in ("leaderboard-*.json", "not-ranked-*.json"):
            for board in (site / "data" / sid).glob(pattern):
                shutil.copyfile(board, data / board.name)
        (root / "api.js").write_bytes(export_static.STATIC_API.read_bytes())
        (root / "data-version.js").write_text(export_static.data_version_js(sid), encoding="utf-8")
        for job in jobs:
            target = root / job["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(export_static.view_page({**job["route"], "path": job["address"]}), encoding="utf-8")
        (root / "job.json").write_text(json.dumps({"pages": [{k: v for k, v in j.items() if k != "route"} for j in jobs]}), encoding="utf-8")
        ran = subprocess.run([find_node(), str(REPO_ROOT / "web" / "prerender.mjs"), str(root), str(root / "job.json")],
                             capture_output=True, text=True, timeout=600, check=False)
        if ran.returncode != 0:
            ledger.errors.append(f"the prerender failed: {ran.stderr[-600:]}")
            return
        for job in jobs:
            again = (root / job["path"]).read_text(encoding="utf-8")
            ledger.equal(f"{job['address']} is what the prerender draws from the API's answers (bytes)", again == pages[job["address"]], True)


#: The "Not ranked" tab's count on a page: the length of the list the tab opens.
_NOT_RANKED_TAB_COUNT = re.compile(r'data-route-tab="notranked"[^>]*>[^<]*<span class="count">([^<]*)</span>')


def _universe(db: Path, sid: str) -> dict[Optional[str], int]:
    """Projects in the snapshot, by ecosystem, and all of them (``None``)."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        counts = dict(conn.execute("SELECT ecosystem, COUNT(*) FROM repositories WHERE snapshot_id = ? GROUP BY ecosystem", (sid,)))
    finally:
        conn.close()
    return {**counts, None: sum(counts.values())}


def verify_lists(db: Path, site: Path, *, report: Optional[dict], log=print) -> dict:
    """Every list view (the presets of ``web/app/routes.js``: the ranking's pages, the ecosystem and lens pages, the
    first-timer and the fastest-first-reply views), at every address the route table and the API's own counts give:

    - the page exists and the sitemap lists it, and no page is drawn as a list at an address the table does not give;
    - its rows, as a table and as cards, are the API's page for its state (a top view: cut at the methodology's minimum
      sample), and the reply cells print the API's values with Python's own formatters;
    - each value it prints in a hook (``list.*``) is the one worked out here;
    - its title and description fit a search result whole, and no two titles are the same;
    - a ranking page links every page of the ranking;
    - each page is byte for byte what ``web/prerender.mjs`` draws from the API's answers.
    Prints each view's hook count. ``{"pages", "hooks", "addresses", "errors"}``, with every list view's address."""
    from porchlamp.serve import project_pages as pp

    ledger = _Ledger()
    export_static = _export_static()
    snapshot, methodology = _api_answers(db, "/v1/porchlamp/snapshot", "/v1/porchlamp/methodology")
    sid = snapshot["snapshot_id"]
    min_n = next(q["min_n"] for q in methodology["parameters"] if q["input_id"] == "ttfhr_hours")
    universe = _universe(db, sid)
    sitemap = site / "sitemap-pages.xml"
    listed = set(re.findall(r"<loc>https://porchlamp\.org(/[^<]*)</loc>", sitemap.read_text(encoding="utf-8"))) if sitemap.is_file() else set()
    jobs, pages, titles, hook_counts = [], {}, {}, {}
    routes = [route for route in export_static.app_routes() if route.get("fixed") and route["shell"] and route.get("view") == "ranked"]
    given: set[str] = set()
    for route in routes:
        # the addresses: one, or one per page of what the route lists, counted by the API
        first = _preset_request(route, route["path"].replace("{page}", "1"))
        (probe,) = _api_answers(db, _leaderboard_path({**first, "page": 1, "page_size": 1}))
        addresses = [route["path"]] if "{page}" not in route["path"] else [
            route["path"].replace("{page}", str(n)) for n in range(1, max(1, -(-probe["total"] // first["page_size"])) + 1)]
        given.update(addresses)
        for address in addresses:
            path = site / address.strip("/") / "index.html"
            ledger.equal(f"{address} is a page of the export", path.is_file(), True)
            ledger.equal(f"{address} is in sitemap-pages.xml", address in listed, True)
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            pages[address] = text
            jobs.append({"path": path.relative_to(site).as_posix(), "view": route.get("view", route["id"]), "address": address,
                         "route": route})
            request = _preset_request(route, address)
            (body,) = _api_answers(db, _leaderboard_path(request))
            shown = _listed_rows(route, body["rows"], min_n)
            want = [(r["owner"], r["name"]) for r in shown]
            view = _home_view(text) or ""
            table = re.search(r'<tbody data-leaderboard-tbody="true"[^>]*>(.*?)</tbody>', view, re.S)
            ledger.equal(f"{address} table rows are the API's page", keyed_items(table.group(1) if table else "", "tr"), want)
            ledger.equal(f"{address} cards are the same rows", keyed_items(view, "div", "leaderboard-card"), want)
            replies = [pp.reply_time_text(r["first_reply_hours"]) if r["first_reply_hours"] is not None else "—" for r in shown]
            shares = [pp.percent_text(r["no_reply_14d_share"]) if r["no_reply_14d_share"] is not None else "—" for r in shown]
            got = _cell_texts(table.group(1) if table else "", "first_reply")
            ledger.equal(f"{address} first reply cells", [g[: len(w)] for g, w in zip(got, replies)] if len(got) == len(replies) else got, replies)
            if route.get("top"):
                ledger.equal(f"{address} first reply cells show the sample",
                             all(f"{r['first_reply_n']:,} pull requests" in g for g, r in zip(got, shown)), True)
            ledger.equal(f"{address} no reply cells", _cell_texts(table.group(1) if table else "", "no_reply_14d"), shares)
            # the values in hooks
            hooks = [(i, v) for i, v in read_page(view).hooks if i.startswith("list.")]
            hook_counts[route["id"]] = hook_counts.get(route["id"], 0) + len(hooks)
            _compare_hooks(ledger, address, hooks, _expected_list_hooks(route, request, body["total"], shown, report, methodology, min_n))
            # the Not ranked tab: the projects of this lens and ecosystem the ranking leaves out
            tab = _NOT_RANKED_TAB_COUNT.search(view)
            ledger.equal(f"{address} Not ranked tab", tab.group(1) if tab else None, str(universe[request["eco"]] - probe["total"]))
            # the head
            title = html.unescape(re.search(r"<title>([^<]*)</title>", text).group(1))
            description = html.unescape(re.search(r'<meta name="description" content="([^"]*)">', text).group(1))
            ledger.equal(f"{address} title fits ({TITLE_MAX})", len(title) <= TITLE_MAX, True)
            ledger.equal(f"{address} description fits ({DESCRIPTION_MAX})", len(description) <= DESCRIPTION_MAX, True)
            ledger.equal(f"{address} title is its own", titles.setdefault(title, address), address)
            if "{page}" in route["path"]:
                links = set(read_page(view).links)
                ledger.equal(f"{address} links every page of the ranking", [a for a in addresses if a not in links], [])
    # the other way: a page drawn as a list (the leaderboard prerendered), or at a list route's address pattern, where
    # the route table gives no address (a page past the ranking's last, a preset no longer in the table)
    drawn = {path for path in site.rglob("index.html")
             if not path.relative_to(site).as_posix().startswith("data/") and 'data-prerendered="ranked"' in path.read_text(encoding="utf-8")}
    for route in routes:
        drawn.update(site.glob(route["path"].strip("/").replace("{page}", "*") + "/index.html"))
    found = {"/" + "".join(f"{part}/" for part in path.parent.relative_to(site).parts) for path in drawn}
    ledger.equal("list pages at no address of the route table", sorted(found - given - {"/"}), [])
    _redraw(db, site, snapshot, methodology, report, jobs, pages, ledger)
    log(f"verify: list views: {len(pages)} pages; hooks per view {dict(sorted(hook_counts.items()))}; "
        f"re-drawn from the API's answers; checked {ledger.checked} values")
    _log_mismatches(log, "LIST", ledger.errors)
    return {"pages": len(pages), "hooks": hook_counts, "addresses": sorted(given), "errors": ledger.errors}


def not_ranked_projects(db: Path, sid: str) -> list[dict]:
    """Every project the balanced ranking leaves out, A to Z as a reader expects (sorted here: by lowercase owner, then
    lowercase name), each with its group and its reason as the route table names it, classified here from the stored
    columns: a stage-0 gate
    (``eligible = 0``) is gated under its ``gate_reason``, except the gate the engine spells
    ``"unrated: no_external_workflow"``, which is unrated; an eligible project that is not rated is unrated, as
    ``off_platform_routed`` when it is routed off-platform, else under its ``unrated_reason``. The ``"unrated: "``
    prefix is dropped."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        records = conn.execute(
            "SELECT owner, name, ecosystem, eligible, gate_reason, unrated_reason, routed, coverage FROM repositories "
            "WHERE snapshot_id = ? AND (eligible = 0 OR rated = 0)", (sid,)).fetchall()
    finally:
        conn.close()
    projects = []
    for owner, name, ecosystem, eligible, gate, unrated, routed, coverage in records:
        if not eligible:
            group, reason = ("unrated" if gate.startswith("unrated: ") else "gated"), gate.removeprefix("unrated: ")
        else:
            group, reason = "unrated", "off_platform_routed" if routed == "off_platform" else unrated.removeprefix("unrated: ")
        projects.append({"owner": owner, "name": name, "ecosystem": ecosystem, "group": group, "reason": reason, "coverage": coverage})
    return sorted(projects, key=lambda p: (p["owner"].lower(), p["name"].lower()))


def not_ranked_sentence(project: dict, *, report: Optional[dict], numbers: dict[tuple[str, str], tuple[int, int]],
                        floor: Optional[float]) -> str:
    """A project's sentence on its reason list, built here from copy.js's words and the values worked out here:
    ``owner/name (Ecosystem): <its reason in words>``, a black hole with its own two numbers and the report's deadline
    when both are known (DESIGN, PR 2, R5 a), a project short of evidence with its coverage and ``floor`` (the
    methodology's ``unrated_below``) when that is known -- or, when its coverage reached the floor, the responsiveness
    evidence D-EVID-1 also needs (RFC-0024). Nothing else stands beside the name."""
    from porchlamp.serve import report_pages as rp

    gate, unrated, row, black_hole_row, below_floor, no_responsiveness, list_reasons = _not_ranked_words()
    reason, key = project["reason"], (project["owner"], project["name"])

    def whole(share: float) -> str:
        return f"{math.floor(share * 100 + 0.5)}%"

    if reason == "insufficient_evidence":
        coverage = project["coverage"]
        clause = no_responsiveness if coverage is not None and floor is not None and coverage >= floor else below_floor
        sentence = list_reasons[reason] if floor is None else (
            f"{unrated[reason]} {_fill(clause, coverage='unknown' if coverage is None else whole(coverage), floor=whole(floor))}")
    elif reason == "black_hole" and report is not None and key in numbers:
        unanswered, counted = numbers[key]
        sentence = _fill(black_hole_row, unanswered=rp.fmt_int(unanswered),
                         counted=rp.fmt_int(counted), deadline_days=report["values"]["deadline_days"])
    else:
        sentence = gate.get(reason) or unrated.get(reason) or reason
    return _fill(row, repo=f"{project['owner']}/{project['name']}", ecosystem=rp.ECOSYSTEM_NAMES[project["ecosystem"]], sentence=sentence)


@functools.lru_cache(maxsize=1)
def _not_ranked_words() -> tuple[dict[str, str], dict[str, str], str, str, str, str, dict[str, str]]:
    """copy.js's reason sentences (gated, unrated), its row ("{repo} ({ecosystem}): {sentence}"), its sentence for a
    black hole's row with its numbers, its coverage clause with the floor, its clause for a project unrated with its
    coverage at the floor or above, and its lists' own reason sentences."""
    from porchlamp.serve import report_pages as rp

    return (rp.copy_js_map("GATE_REASON_COPY"), rp.copy_js_map("UNRATED_REASON_COPY"), rp.copy_js_string("NOT_RANKED_LIST_ROW"),
            rp.copy_js_string("NOT_RANKED_LIST_ROW_BLACK_HOLE"), rp.copy_js_string("UNRATED_COVERAGE_BELOW_FLOOR"),
            rp.copy_js_string("UNRATED_NO_RESPONSIVENESS"), rp.copy_js_map("NOT_RANKED_LIST_REASONS"))
_REASON_LIST = re.compile(r'<ol class="reason-list"[^>]*?start="(\d+)"[^>]*>(.*?)</ol>', re.S)
_REASON_ROW = re.compile(r"<li\b[^>]*>(.*?)</li>", re.S)


def _fill(template: str, **slots: str) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: slots[m.group(1)], template)


def verify_not_ranked_lists(db: Path, site: Path, *, report: Optional[dict], numbers: Optional[dict], stats: Optional[dict],
                            log=print) -> dict:
    """The not-ranked projects as lists by reason (``/not-ranked/<reason>/<n>/``, the route table's presets of the
    not-ranked view), against a classification worked out here from the database:

    - each list's pages exist, the sitemap lists them, and no other page sits under a list's address;
    - each page's rows are its reason's projects, alphabetical, 200 a page, each one sentence built here
      (:func:`not_ranked_sentence`; a black hole's numbers from ``numbers``, what ``/repos`` serves), with nothing
      beside the name;
    - every not-ranked project is in exactly one list, once; the counts per reason add up to the snapshot's gated and
      unrated counts and, with ``stats``, the black holes and the total to the stats file's;
    - the hooks, the head (title and description whole in a search result, no two titles alike), the links to every
      page of the list, the Not ranked tab;
    - ``/not-ranked/``, the whole list's page, links every list with projects and no list without;
    - each page, and ``/not-ranked/``, is byte for byte what ``web/prerender.mjs`` draws from the API's answers.
    ``{"pages", "rows", "addresses", "errors"}``, with every list page's address."""
    from porchlamp.serve import report_pages as rp

    ledger = _Ledger()
    export_static = _export_static()
    snapshot, methodology = _api_answers(db, "/v1/porchlamp/snapshot", "/v1/porchlamp/methodology")
    sid = snapshot["snapshot_id"]
    projects = not_ranked_projects(db, sid)
    numbers = numbers or {}
    floor = ((methodology.get("missingness") or {}).get("coverage_thresholds") or {}).get("unrated_below")
    sitemap = site / "sitemap-pages.xml"
    listed = set(re.findall(r"<loc>https://porchlamp\.org(/[^<]*)</loc>", sitemap.read_text(encoding="utf-8"))) if sitemap.is_file() else set()
    # the counts: the snapshot's groups and, with the stats file, its black holes and its total
    by_group = {group: sum(1 for p in projects if p["group"] == group) for group in ("gated", "unrated")}
    ledger.equal("not-ranked gated projects = the snapshot's gated count", by_group["gated"], snapshot["counts"]["gated"])
    ledger.equal("not-ranked unrated projects = the snapshot's unrated count", by_group["unrated"], snapshot["counts"]["unrated"])
    if stats is not None:
        ledger.equal("not-ranked projects = the stats file's repos - rated", len(projects), stats["repos"] - stats["rated"])
        ledger.equal("black-hole list = the stats file's black holes", sum(1 for p in projects if p["reason"] == "black_hole"),
                     stats["black_holes"])
    routes = [route for route in export_static.app_routes() if (route.get("fixed") or {}).get("reason") and route["shell"]]
    # each list's first page and its count: the "Lists by reason" line links the lists that have projects
    first_pages = {route["path"].replace("{page}", "1"): sum(1 for p in projects if p["reason"] == route["fixed"]["reason"])
                   for route in routes}
    seen: dict[tuple[str, str], int] = {}
    on_pages: dict[str, int] = {}  # rows found on each reason's pages
    jobs, pages, titles, given, per_reason = [], {}, {}, set(), {}
    for route in routes:
        reason, size = route["fixed"]["reason"], route["fixed"]["page_size"]
        members = [p for p in projects if p["reason"] == reason]
        per_reason[reason] = len(members)
        addresses = [route["path"].replace("{page}", str(n)) for n in range(1, -(-len(members) // size) + 1)]  # none when empty
        given.update(addresses)
        for number, address in enumerate(addresses, start=1):
            path = site / address.strip("/") / "index.html"
            ledger.equal(f"{address} is a page of the export", path.is_file(), True)
            ledger.equal(f"{address} is in sitemap-pages.xml", address in listed, True)
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            pages[address] = text
            jobs.append({"path": path.relative_to(site).as_posix(), "view": route["view"], "address": address, "route": route})
            view = _home_view(text) or ""
            shown = members[(number - 1) * size : number * size]
            found = _REASON_LIST.search(view)
            items = _REASON_ROW.findall(found.group(2)) if found else []
            ledger.equal(f"{address} list starts at", found.group(1) if found else None, str((number - 1) * size + 1))
            ledger.equal(f"{address} rows are its reason's projects, alphabetical", keyed_items(found.group(2) if found else "", "li"),
                         [(p["owner"], p["name"]) for p in shown])
            got = [_plain(item) for item in items]
            want = [not_ranked_sentence(p, report=report, numbers=numbers, floor=floor) for p in shown]
            for i, (g, w) in enumerate(zip(got, want)):
                ledger.equal(f"{address} row {i + 1}", g, w)
            ledger.equal(f"{address} row count", len(got), len(want))
            for key in keyed_items(found.group(2) if found else "", "li"):
                seen[key] = seen.get(key, 0) + 1
                on_pages[reason] = on_pages.get(reason, 0) + 1
            hooks = [(i, v) for i, v in read_page(view).hooks if i.startswith("list.")]
            expected = {"list.total": rp.fmt_int(len(members))}
            if reason == "black_hole" and report is not None:
                expected.update({k: report["values"][v] for k, v in (("list.deadline_days", "deadline_days"),
                                 ("list.min_n", "min_n"), ("list.ci_percent", "ci_percent"))})
            _compare_hooks(ledger, address, hooks, expected)
            links = set(read_page(view).links)
            ledger.equal(f"{address} links every page of its list", [a for a in addresses if a not in links], [])
            ledger.equal(f"{address} links every other list with projects, and no list without",
                         sorted(a for a in links if a in first_pages), sorted(a for a, n in first_pages.items() if n))
            tab = _NOT_RANKED_TAB_COUNT.search(view)
            ledger.equal(f"{address} Not ranked tab", tab.group(1) if tab else None, str(len(projects)))
            title = html.unescape(re.search(r"<title>([^<]*)</title>", text).group(1))
            description = html.unescape(re.search(r'<meta name="description" content="([^"]*)">', text).group(1))
            ledger.equal(f"{address} title fits ({TITLE_MAX})", len(title) <= TITLE_MAX, True)
            ledger.equal(f"{address} description fits ({DESCRIPTION_MAX})", len(description) <= DESCRIPTION_MAX, True)
            ledger.equal(f"{address} title is its own", titles.setdefault(title, address), address)
    # the whole list's page: its "Lists by reason" line links every list with projects, and it is re-drawn with the lists
    list_pages = len(pages)
    whole = next(route for route in export_static.app_routes() if route["id"] == "notranked")
    path = site / whole["path"].strip("/") / "index.html"
    ledger.equal(f"{whole['path']} is a page of the export", path.is_file(), True)
    if path.is_file():
        pages[whole["path"]] = path.read_text(encoding="utf-8")
        jobs.append({"path": path.relative_to(site).as_posix(), "view": "notranked", "address": whole["path"], "route": whole})
        links = set(read_page(_home_view(pages[whole["path"]]) or "").links)
        ledger.equal(f"{whole['path']} links every list with projects, and no list without",
                     sorted(a for a in links if a in first_pages), sorted(a for a, n in first_pages.items() if n))
    # the rows the gated lists and the unrated lists hold, against the snapshot's counts
    group_of = {p["reason"]: p["group"] for p in projects}
    by_list = {group: sum(n for reason, n in on_pages.items() if group_of.get(reason) == group) for group in ("gated", "unrated")}
    ledger.equal("rows on the gated lists = the snapshot's gated count", by_list["gated"], snapshot["counts"]["gated"])
    ledger.equal("rows on the unrated lists = the snapshot's unrated count", by_list["unrated"], snapshot["counts"]["unrated"])
    # every not-ranked project in exactly one list, once
    everyone = {(p["owner"], p["name"]) for p in projects}
    ledger.equal("not-ranked projects in no list", sorted(everyone - set(seen))[:20], [])
    ledger.equal("not-ranked projects listed more than once", sorted(k for k, n in seen.items() if n > 1)[:20], [])
    ledger.equal("listed projects that are ranked", sorted(set(seen) - everyone)[:20], [])
    # no page under a list's address that the route table does not give
    under = {path for pattern in ("not-ranked/*/index.html", "not-ranked/*/*/index.html") for path in site.glob(pattern)}
    found_addresses = {"/" + "".join(f"{part}/" for part in path.parent.relative_to(site).parts) for path in under}
    ledger.equal("not-ranked list pages at no address of the route table", sorted(found_addresses - given), [])
    _redraw(db, site, snapshot, methodology, report, jobs, pages, ledger)
    log(f"verify: not-ranked lists: {list_pages} pages and {whole['path']}; rows per reason {dict(sorted(per_reason.items()))}; rows listed "
        f"{sum(on_pages.values())} of {len(projects)} not ranked (gated lists {by_list['gated']}, unrated lists {by_list['unrated']}), "
        f"each once: {not ledger.errors}; re-drawn from the API's answers; checked {ledger.checked} values")
    _log_mismatches(log, "NOT-RANKED", ledger.errors)
    return {"pages": list_pages, "rows": sum(seen.values()), "addresses": sorted(given), "errors": ledger.errors}


#: Every ranked project's page is at most this many plain-link clicks from ``/`` (owner, 2026-10-17).
PROJECT_CLICKS_MAX = 2
#: The most plain-link clicks from ``/`` to each kind of list page: ``/`` links every list view; its Not ranked tab
#: links ``/not-ranked/``, whose "Lists by reason" line links each list's first page, whose pages line links the rest.
LIST_CLICKS_MAX = {"list views": 1, "not-ranked lists' first pages": 2, "not-ranked list pages": 3}
_A_HREF = re.compile(r"<a\b[^>]*\bhref=\"([^\"]*)\"", re.S)


def _page_of(site: Path, address: str) -> Path:
    return site / address.strip("/") / "index.html" if address.strip("/") else site / "index.html"


def crawl_depths(site: Path) -> dict[str, int]:
    """Breadth-first from ``/`` over the plain ``<a href>`` links between this export's pages, as a crawler without
    JavaScript follows them: each address reached (a directory with an ``index.html``) and its depth in clicks. A
    query or a fragment does not make another page (a static host serves the same file), and a link to anything but
    a page (a data file, another site) is not followed."""
    base = "https://porchlamp.org"
    depths = {"/": 0}
    queue = deque(["/"])
    while queue:
        address = queue.popleft()
        text = _page_of(site, address).read_text(encoding="utf-8")
        for href in _A_HREF.findall(text):
            url = urlsplit(urljoin(base + address, html.unescape(href)))
            if f"{url.scheme}://{url.netloc}" != base or not url.path.endswith("/"):
                continue
            if url.path not in depths and _page_of(site, url.path).is_file():
                depths[url.path] = depths[address] + 1
                queue.append(url.path)
    return depths


def verify_crawl(db: Path, site: Path, *, lists: list[str], not_ranked: list[str], log=print) -> dict:
    """Every ranked project's page is reached from ``/`` within :data:`PROJECT_CLICKS_MAX` plain-link clicks (the
    list views link them all: ``/`` links each ranking page, which links its projects), and every list's page within
    :data:`LIST_CLICKS_MAX`: ``lists``, the list views' addresses, and ``not_ranked``, the not-ranked lists' pages, as
    :func:`verify_lists` and :func:`verify_not_ranked_lists` work them out. The project pages are the API's own ranked
    set. ``{"depths", "lists", "errors"}``, ``lists`` each kind of list page by depth (``None``: not reached)."""
    ledger = _Ledger()
    (board,) = _api_answers(db, "/v1/porchlamp/leaderboard?persona=balanced&page=1&page_size=1")
    total = board["total"]
    depths = crawl_depths(site)
    projects = {address: depth for address, depth in depths.items() if _PROJECT_PATH.fullmatch(address)}
    by_depth: dict[int, int] = {}
    for depth in depths.values():
        by_depth[depth] = by_depth.get(depth, 0) + 1
    near = sum(1 for depth in projects.values() if depth <= PROJECT_CLICKS_MAX)
    ledger.equal(f"ranked project pages reached from / within {PROJECT_CLICKS_MAX} clicks", near, total)
    ledger.equal("project pages reached at all", len(projects), total)
    project_by_depth = {d: sum(1 for x in projects.values() if x == d) for d in sorted(set(projects.values()))}
    firsts = {route["path"].replace("{page}", "1") for route in _export_static().app_routes() if (route.get("fixed") or {}).get("reason")}
    kinds = {"list views": lists, "not-ranked lists' first pages": [a for a in not_ranked if a in firsts], "not-ranked list pages": not_ranked}
    list_depths, said = {}, []
    for kind, addresses in kinds.items():
        most = LIST_CLICKS_MAX[kind]
        clicks = f"{most} click" + ("" if most == 1 else "s")
        far = [address for address in addresses if depths.get(address, most + 1) > most]
        ledger.equal(f"{kind} more than {clicks} from / (or not reached)", far[:20], [])
        found = [depths.get(address) for address in addresses]
        list_depths[kind] = {d: found.count(d) for d in sorted(set(found), key=lambda d: (d is None, d))}
        said.append(f"{kind} {len(addresses) - len(far)} of {len(addresses)} within {clicks} (by depth {list_depths[kind]})")
    log(f"verify: crawl from /: pages by depth {dict(sorted(by_depth.items()))}; project pages by depth {project_by_depth}; "
        f"{near} of {total} ranked projects within {PROJECT_CLICKS_MAX} clicks; " + "; ".join(said))
    _log_mismatches(log, "CRAWL", ledger.errors)
    return {"depths": project_by_depth, "lists": list_depths, "errors": ledger.errors}


_PROJECT_PATH = re.compile(r"/projects/[a-z0-9._-]+/[a-z0-9._-]+/")


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
    parser.add_argument(
        "--previous-site", type=Path, default=None,
        help="the site as last published, as given to the export: every project it had a page for must still have one",
    )
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
    record, numbers, stats = None, None, None
    if args.stats is not None:
        result = verify_report(args.db.resolve(), args.stats.resolve(), site, evidence_files=evidence_files)
        ok, record, numbers = not result["errors"] and ok, result.get("record"), result.get("black_hole_numbers")
        stats = json.loads(args.stats.read_text(encoding="utf-8"))
    ok = not verify_methodology(args.db.resolve(), site, report=record)["errors"] and ok
    lists = verify_lists(args.db.resolve(), site, report=record)
    not_ranked = verify_not_ranked_lists(args.db.resolve(), site, report=record, numbers=numbers, stats=stats)
    ok = not lists["errors"] and not not_ranked["errors"] and ok
    if args.stats is not None:
        ok = not verify_projects(args.db.resolve(), site, evidence_files=evidence_files,
                                 previous_site=args.previous_site.resolve() if args.previous_site else None)["errors"] and ok
        ok = not verify_home(args.db.resolve(), site, report=record)["errors"] and ok
        ok = not verify_crawl(args.db.resolve(), site, lists=lists["addresses"], not_ranked=not_ranked["addresses"])["errors"] and ok
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
