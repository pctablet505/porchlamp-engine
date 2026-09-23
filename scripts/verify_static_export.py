"""Check an exported static site against the API it was exported from.

For a grid of requests -- /leaderboard for every persona x every sort (both
directions, and none) x ecosystem/search filters x pages; /not-ranked for
every persona x ecosystem x search; /repos/{owner}/{name} and its /evidence
for every repo (or a sample, --quick) plus unknown ones; /snapshot,
/matrix, /methodology; /reproject for the persona presets, one-hot and
seeded random weight vectors over EVERY page of the full custom ranking;
and the error cases the UI can reach -- this asks the API itself
(Starlette's ``TestClient`` on ``cvi.serve.app.create_app`` over the same
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
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[1]
PARITY_JS = REPO_ROOT / "scripts" / "static_parity.mjs"

#: (name, text in api.js, replacement). Each breaks one behaviour the grid
#: is meant to pin; each must be caught.
MUTANTS: tuple[tuple[str, str, str], ...] = (
    ("search ignores case",
     "sqlLower(row.owner).includes(needle) || sqlLower(row.name).includes(needle)",
     "row.owner.includes(needle) || row.name.includes(needle)"),
    ("ecosystem filter ignored",
     "(!present(eco) || row.ecosystem === eco)", "true"),
    ("sort order ignored",
     "const order = sort === null ? null : board.orders[sort];", "const order = null;"),
    ("not-ranked never truncated",
     "matching.slice(0, list.max_rows)", "matching.slice(0)"),
    ("repo looked up in the wrong bucket",
     "h = Math.imul(h, 0x85ebca6b) >>> 0;", "h = Math.imul(h, 0x85ebca6d) >>> 0;"),
    ("custom coverage divided by the measured weight",
     "const coverage = cov / totalWeight;", "const coverage = cov / (den || totalWeight);"),
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
     "members.get(v.cviDisplay) === 1 ? null : denseRank.get(v.cviDisplay)", "denseRank.get(v.cviDisplay)"),
)

_NODE_GLOBS = (".local/opt/node-*/bin/node", "tools/node-*/bin/node", ".nvm/versions/node/*/bin/node")


def _export_static():
    """``scripts/export_static.py``, loaded by path: putting ``scripts/`` on
    ``sys.path`` would let ``scripts/serve.py`` shadow ``tests/serve`` for
    every later import in the same process."""
    spec = importlib.util.spec_from_file_location("export_static", REPO_ROOT / "scripts" / "export_static.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def find_node() -> Optional[str]:
    """``PATH``, then ``CVI_NODE_BIN``, then the usual per-user installs
    (``tests/test_ui_node_suite.py``'s ``find_node``)."""
    on_path = shutil.which("node")
    if on_path:
        return on_path
    override = os.environ.get("CVI_NODE_BIN")
    if override and Path(override).is_file():
        return override
    for pattern in _NODE_GLOBS:
        for candidate in sorted(Path.home().glob(pattern), reverse=True):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
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

        from cvi.serve.app import create_app

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

    def post_reproject(self, weights: dict, page: Optional[int], page_size: Optional[int]) -> dict:
        params = {k: v for k, v in (("page", page), ("page_size", page_size)) if v is not None}
        answer = self._answer(self.client.post("/v1/cvi/reproject", params=params, json=weights))
        options = {k: v for k, v in (("page", page), ("page_size", page_size)) if v is not None}
        self.requests.append({"kind": "reproject", "fn": "postReproject", "args": [weights, options] if options else [weights]})
        self.expected.append(answer)
        return answer


def _leaderboard(grid: Grid, params: dict) -> None:
    grid.get("leaderboard", "fetchLeaderboard", [params], "/v1/cvi/leaderboard", params)


def _not_ranked(grid: Grid, params: dict) -> None:
    grid.get("not-ranked", "fetchNotRanked", [params], "/v1/cvi/not-ranked", params)


def _repo(grid: Grid, owner: str, name: str) -> None:
    path = f"/v1/cvi/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
    grid.get("repo", "fetchRepoDetail", [owner, name], path)
    grid.get("evidence", "fetchEvidence", [owner, name], f"{path}/evidence")


def _weights(pillars: tuple[str, ...], values: list[float]) -> dict:
    return {f"weight_{p}": v for p, v in zip(pillars, values)}


def build_grid(db: Path, *, quick: bool, seed: int = 20260923) -> Grid:
    from cvi.core import constants

    sort_keys = _export_static().sort_keys

    grid = Grid(db)
    pillars = constants.PILLAR_NAMES
    snapshot = grid.get("snapshot", "fetchSnapshot", [], "/v1/cvi/snapshot")["data"]
    grid.get("matrix", "fetchMatrix", [], "/v1/cvi/matrix")
    grid.get("methodology", "fetchMethodology", [], "/v1/cvi/methodology")
    personas = sorted(snapshot["persona_visible_counts"])

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    sid = snapshot["snapshot_id"]
    ecosystems = [e for (e,) in conn.execute(
        "SELECT DISTINCT ecosystem FROM repositories WHERE snapshot_id = ? ORDER BY ecosystem", (sid,))]
    repos = conn.execute(
        "SELECT owner, name FROM repositories WHERE snapshot_id = ? ORDER BY lower(owner || '/' || name)", (sid,)
    ).fetchall()
    conn.close()
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
                _not_ranked(grid, {k: v for k, v in (("persona", persona), ("eco", eco), ("q", q)) if v is not None})
    _not_ranked(grid, {"persona": "nope"})

    sample = repos if not quick else rng.sample(repos, min(len(repos), 150))
    for owner, name in sample:
        _repo(grid, owner, name)
    unknown = [("no-such-owner", "no-such-repo")]
    if repos:
        owner, name = repos[0]
        unknown += [(owner.upper() if owner.upper() != owner else owner + "X", name), (owner, name + "-missing")]
    for owner, name in unknown:
        _repo(grid, owner, name)

    presets = [list(constants.PERSONA_PRESETS[p][q] for q in pillars) for p in sorted(constants.PERSONA_PRESETS)]
    one_hot = [[1.0 if i == j else 0.0 for i in range(len(pillars))] for j in range(len(pillars))]
    vectors = presets + one_hot + [[0.2] * len(pillars)]
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
        raise SystemExit("verify: no Node.js interpreter (PATH, CVI_NODE_BIN, ~/.local/opt/node-*)")
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


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--quick", action="store_true", help="a smaller grid (a repo sample, fewer filters)")
    parser.add_argument("--no-mutants", action="store_true")
    args = parser.parse_args(argv)
    ok = passed(verify(args.db.resolve(), args.site.resolve(), quick=args.quick, mutants=not args.no_mutants))
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
