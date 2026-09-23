"""Export one Porchlamp snapshot database as a static site (GitHub Pages).

The UI in ``web/v2`` reads the v2 API (``porchlamp/serve/app.py``). A static host
has no API, so this writes, under ``<out>/data/<snapshot_id>/``, what that
API answers for this database -- every response is read THROUGH the API
(Starlette's ``TestClient`` on :func:`porchlamp.serve.app.create_app`), never
recomputed here -- plus the row lists and orderings ``web/static/api.js``
needs to answer the parameterised requests (search, ecosystem, sort,
paging) in the browser. The one computation the browser does itself is
``POST /reproject`` (custom weights), over the stored pillar columns
exported in ``reproject.json``.

``<out>`` is the whole site: ``web/v2`` with ``api.js`` replaced by
``web/static/api.js``, a generated ``data-version.js`` naming the data
directory, and ``.nojekyll``. ``scripts/verify_static_export.py`` checks the
exported site against the API, request by request.

The primary checkout cannot import ``porchlamp.serve.app`` while it carries the
owner's uncommitted rollback (``porchlamp.lakehouse.repositories_dump_hash``), so
run this from a clean tree::

    PYTHONPATH=<clean worktree> .venv/bin/python <clean worktree>/scripts/export_static.py \\
        --db ../cvi-scratch/bfs-run/cvi-L4a2.db --out ../cvi-scratch/static-site
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_V2 = REPO_ROOT / "web" / "v2"
STATIC_API = REPO_ROOT / "web" / "static" / "api.js"

#: ``web/v2`` entries the page never loads: its own test suite and the Node
#: package marker that suite needs.
_WEB_SKIP = frozenset({"tests", "package.json"})

#: Repo records (detail + evidence) are sharded by :func:`bucket_of` so a
#: drawer opening loads one small file, not 5,000+ records.
BUCKETS = 256

#: Every page ``/leaderboard`` accepts is at most this many rows.
PAGE_MAX = 1000

#: Things that must never be published, checked over every file written.
_FORBIDDEN = (
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b"), "a GitHub token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"), "a GitHub token"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]*[A-Za-z]"), "an email address"),
    (re.compile(r"/home/[A-Za-z0-9_.-]+/"), "a local home path"),
)


def bucket_of(owner: str, name: str) -> str:
    """FNV-1a (32-bit) of ``"owner/name"``'s UTF-8 bytes through murmur3's
    ``fmix32`` finalizer, mod :data:`BUCKETS`, as two hex digits.
    ``web/static/api.js`` computes the same function to find a repo's file.
    The finalizer is not decoration: FNV-1a's low bits barely mix (its low
    bit is the XOR of the input bytes' low bits), so without it
    ``owner7/repo7``-style names all land in odd buckets."""
    h = 0x811C9DC5
    for byte in f"{owner}/{name}".encode("utf-8"):
        h ^= byte
        h = (h * 0x01000193) & 0xFFFFFFFF
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & 0xFFFFFFFF
    h ^= h >> 16
    return f"{h % BUCKETS:02x}"


def sort_keys(pillars: tuple[str, ...]) -> tuple[str, ...]:
    """Every ``sort`` value ``/leaderboard`` accepts (``porchlamp.serve.app.
    _translate_sort``), both directions."""
    fields = ("rank", "porchlamp", "coverage", "tier") + tuple(f"p_{p}" for p in pillars)
    return tuple(s for f in fields for s in (f, f"-{f}"))


def _dump(path: Path, payload: Any) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


class _Api:
    """The v2 API over one database, called in-process."""

    def __init__(self, db_path: Path) -> None:
        from fastapi.testclient import TestClient

        from porchlamp.serve.app import create_app

        with tempfile.TemporaryDirectory() as empty_web:
            self.client = TestClient(create_app(db_path, web_dir=empty_web))

    def get(self, path: str, **params: Any) -> Any:
        response = self.client.get(path, params={k: v for k, v in params.items() if v is not None})
        if response.status_code != 200:
            raise SystemExit(f"export: GET {response.url} -> {response.status_code}: {response.text[:400]}")
        return response.json()


def _key(row: dict) -> tuple[str, str]:
    return row["owner"], row["name"]


def _leaderboard_pages(api: _Api, persona: str, sort: Optional[str]) -> tuple[list[dict], int]:
    rows: list[dict] = []
    page = 1
    while True:
        body = api.get("/v1/porchlamp/leaderboard", persona=persona, sort=sort, page=page, page_size=PAGE_MAX)
        rows.extend(body["rows"])
        if len(rows) >= body["total"] or not body["rows"]:
            return rows, body["total"]
        page += 1


def _export_leaderboard(api: _Api, persona: str, sorts: tuple[str, ...]) -> dict:
    """The persona's whole ranked list in the API's default order, plus, per
    ``sort``, the order the API returns it in (as indices into ``rows``;
    omitted where it IS the default order). Rows never depend on ``sort``
    (only their order does) -- asserted, since the browser reuses one row
    list for every order."""
    rows, total = _leaderboard_pages(api, persona, None)
    if len(rows) != total:
        raise SystemExit(f"export: {persona} leaderboard paged {len(rows)} rows of total={total}")
    index = {_key(row): i for i, row in enumerate(rows)}
    if len(index) != len(rows):
        raise SystemExit(f"export: {persona} leaderboard repeats an owner/name")
    orders: dict[str, list[int]] = {}
    for sort in sorts:
        sorted_rows, sorted_total = _leaderboard_pages(api, persona, sort)
        if sorted_total != total or len(sorted_rows) != total:
            raise SystemExit(f"export: {persona} sort={sort} returned {len(sorted_rows)}/{sorted_total} rows, not {total}")
        order = []
        for row in sorted_rows:
            i = index.get(_key(row))
            if i is None or rows[i] != row:
                raise SystemExit(f"export: {persona} sort={sort} row {_key(row)} differs from the default-order row")
            order.append(i)
        if order != list(range(total)):
            orders[sort] = order
    return {"persona": persona, "total": total, "max_page_size": PAGE_MAX, "rows": rows, "orders": orders}


def _export_not_ranked(api: _Api, conn: sqlite3.Connection, snapshot_id: str, persona: str) -> dict:
    """Every not-ranked row for ``persona`` in the API's order, each with the
    group the API files it under. ``/not-ranked`` itself returns only the
    first ``queries.MAX_LIMIT`` of a filtered list, so the full list comes
    from the same store query and the same classification the endpoint
    runs (``porchlamp.serve.app._classify_not_ranked``/``_not_ranked_row``), then
    is checked against the endpoint's own unfiltered answer."""
    from porchlamp.serve import app as serve_app
    from porchlamp.store import queries

    records: list[dict] = []
    while True:
        page, _counts, total = queries.list_not_ranked(
            conn, snapshot_id, persona=persona, limit=queries.MAX_LIMIT, offset=len(records)
        )
        records.extend(page)
        if len(records) >= total or not page:
            break
    rows = []
    for record in records:
        group, reason = serve_app._classify_not_ranked(record, persona=persona)
        row = serve_app._not_ranked_row(record, group=group, reason=reason).model_dump(mode="json")
        rows.append({"group": group, "row": row})

    served = api.get("/v1/porchlamp/not-ranked", persona=persona)
    first = rows[: queries.MAX_LIMIT]
    rebuilt = {
        group: [r["row"] for r in first if r["group"] == group] for group in ("gated", "unrated", "persona_hidden")
    }
    rebuilt.update(counts=served["counts"], truncated=len(rows) > len(first))
    if rebuilt != served:
        raise SystemExit(f"export: {persona} not-ranked list does not reproduce the endpoint's own response")
    return {"persona": persona, "max_rows": queries.MAX_LIMIT, "counts": served["counts"], "rows": rows}


def _repo_records(api: _Api, conn: sqlite3.Connection, snapshot_id: str) -> Iterator[tuple[str, str, dict]]:
    names = conn.execute(
        "SELECT owner, name FROM repositories WHERE snapshot_id = ? ORDER BY lower(owner || '/' || name)",
        (snapshot_id,),
    ).fetchall()
    for owner, name in names:
        path = f"/v1/porchlamp/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
        yield owner, name, {"detail": api.get(path), "evidence": api.get(f"{path}/evidence")}


#: ``ReprojectRow`` fields copied from a repo's stored record unchanged
#: (``porchlamp.serve.app._reproject_row`` -> ``_project_leaderboard_row``); the
#: browser computes rank/tie_group/porchlamp/coverage/tier/tier_ceiling/rank_delta.
_REPROJECT_STORED = (
    "owner", "name", "ecosystem", "quadrant", "leverage", "friction", "leverage_coverage",
    "friction_coverage", "persona_gates", "persona_composites", "suspicion", "stars_audited",
    "snapshot_id", "benchmark_version", "raw_hash",
)


def _export_reproject(conn: sqlite3.Connection, snapshot_id: str, details: dict[tuple[str, str], dict]) -> dict:
    """What ``queries.reproject`` reads, for every stage-0 eligible repo: the
    stored pillar columns and D-TIE-1 tail columns it orders by, and the
    stored record fields a ``ReprojectRow`` carries (from the API's own
    ``/repos/{owner}/{name}`` answer), plus the constants its SQL embeds."""
    from porchlamp.core import constants
    from porchlamp.core.aggregate import RATIO_DECIMALS
    from porchlamp.serve.contract import DEFAULT_PAGE_SIZE
    from porchlamp.store import queries

    pillars = constants.PILLAR_NAMES
    tail = conn.execute(
        "SELECT owner, name, ttfhr_p75, last_human_commit_at FROM repositories "
        "WHERE snapshot_id = ? AND eligible = 1 ORDER BY lower(owner || '/' || name)",
        (snapshot_id,),
    ).fetchall()
    lowered = [f"{o}/{n}".lower() for o, n, _t, _l in tail]
    if len(set(lowered)) != len(lowered):
        raise SystemExit("export: two eligible repos share lower(owner/name); D-TIE-1's last level is not total")
    rows = []
    for owner, name, ttfhr_p75, last_commit in tail:
        if not (ttfhr_p75 is None or isinstance(ttfhr_p75, (int, float))):
            raise SystemExit(f"export: {owner}/{name} ttfhr_p75 is {type(ttfhr_p75).__name__}, not a number")
        if not (last_commit is None or isinstance(last_commit, str)):
            raise SystemExit(f"export: {owner}/{name} last_human_commit_at is not text")
        detail = details[(owner, name)]
        if detail["eligible"] is not True:
            raise SystemExit(f"export: {owner}/{name} is eligible in SQL but not in its API record")
        row = {field: detail[field] for field in _REPROJECT_STORED}
        for pillar in pillars:
            row[f"p_{pillar}"] = detail[f"p_{pillar}"]
            row[f"c_{pillar}"] = detail[f"c_{pillar}"]
        row.update(
            balanced_rank=detail["rank"], balanced_tie_group=detail["tie_group"], balanced_porchlamp=detail["porchlamp"],
            balanced_coverage=detail["coverage"], balanced_tier=detail["tier"],
            ttfhr_p75=ttfhr_p75, last_human_commit_at=last_commit,
        )
        rows.append(row)
    return {
        "pillars": list(pillars),
        "tier_order": list(constants.TIER_ORDER),
        "tier_cutoffs": [[cutoff, label] for cutoff, label in constants.TIER_CUTOFFS],
        "rated_min_coverage": constants.EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE,
        "ceiling_a_min_coverage": constants.CEILING_A_MIN_COVERAGE,
        "no_ceiling_min_coverage": constants.NO_CEILING_MIN_COVERAGE,
        "ratio_decimals": RATIO_DECIMALS,
        "weight_sum_tolerance": constants.WEIGHT_SUM_TOLERANCE,
        "default_page_size": DEFAULT_PAGE_SIZE,
        "max_limit": queries.MAX_LIMIT,
        "rows": rows,
    }


def _copy_web(out: Path) -> None:
    for entry in WEB_V2.iterdir():
        if entry.name in _WEB_SKIP:
            continue
        target = out / entry.name
        if entry.is_dir():
            shutil.copytree(entry, target)
        else:
            shutil.copy2(entry, target)
    shutil.copy2(STATIC_API, out / "api.js")


def _scan_for_forbidden(out: Path) -> None:
    for path in sorted(out.rglob("*")):
        if not path.is_file() or path.suffix not in {".json", ".js", ".html", ".css"}:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern, what in _FORBIDDEN:
            match = pattern.search(text)
            if match:
                raise SystemExit(f"export: {path.relative_to(out)} contains {what} ({match.group(0)[:40]!r}); not publishing")


def export(db: Path, out: Path) -> dict:
    from porchlamp.core import constants
    from porchlamp.serve.app import _current_snapshot_id, _open_v2_connection

    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"export: {out} is not empty")
    out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        # A private, consistent copy: the served database is never opened for writing.
        copy = Path(tmp) / "snapshot.db"
        source = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        target = sqlite3.connect(copy)
        source.backup(target)
        source.close()
        target.close()

        api = _Api(copy)
        conn = _open_v2_connection(str(copy))
        snapshot_id = _current_snapshot_id(conn)
        snapshot = api.get("/v1/porchlamp/snapshot")
        if snapshot["snapshot_id"] != snapshot_id:
            raise SystemExit("export: the API serves a different snapshot than the database's current one")
        data = out / "data" / snapshot_id
        sizes: dict[str, int] = {}
        sizes["snapshot.json"] = _dump(data / "snapshot.json", snapshot)
        sizes["methodology.json"] = _dump(data / "methodology.json", api.get("/v1/porchlamp/methodology"))
        sizes["matrix.json"] = _dump(data / "matrix.json", api.get("/v1/porchlamp/matrix"))

        personas = sorted(constants.PERSONA_PRESETS)
        sorts = sort_keys(constants.PILLAR_NAMES)
        for persona in personas:
            name = f"leaderboard-{persona}.json"
            sizes[name] = _dump(data / name, _export_leaderboard(api, persona, sorts))
            name = f"not-ranked-{persona}.json"
            sizes[name] = _dump(data / name, _export_not_ranked(api, conn, snapshot_id, persona))

        # Every bucket is written, empty ones too: a repo absent from the snapshot
        # must read as a 404 for that repo, never as a missing data file.
        buckets: dict[str, dict[str, dict]] = {f"{b:02x}": {} for b in range(BUCKETS)}
        details: dict[tuple[str, str], dict] = {}
        for owner, name, record in _repo_records(api, conn, snapshot_id):
            buckets.setdefault(bucket_of(owner, name), {})[f"{owner}/{name}"] = record
            details[(owner, name)] = record["detail"]
        repo_bytes = sum(_dump(data / "repos" / f"{b}.json", records) for b, records in buckets.items())
        sizes[f"repos/ ({len(buckets)} files)"] = repo_bytes
        sizes["reproject.json"] = _dump(data / "reproject.json", _export_reproject(conn, snapshot_id, details))
        conn.close()

    manifest = {
        "snapshot_id": snapshot_id,
        "as_of": snapshot["as_of"],
        "counts": snapshot["counts"],
        "repos": len(details),
        "exported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source_db_sha256": hashlib.sha256(db.read_bytes()).hexdigest(),
        "buckets": BUCKETS,
        # the allow-list /leaderboard checks ``sort`` against (porchlamp.serve.app._translate_sort):
        "sort_fields": [s for s in sorts if not s.startswith("-")],
        "bytes": sizes,
    }
    _dump(data / "manifest.json", manifest)
    _copy_web(out)
    (out / "data-version.js").write_text(
        f"// Generated by scripts/export_static.py: the data directory this build reads.\n"
        f"export const DATA_VERSION = {json.dumps(snapshot_id)};\n",
        encoding="utf-8",
    )
    (out / ".nojekyll").write_text("", encoding="utf-8")
    _scan_for_forbidden(out)
    return manifest


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", type=Path, required=True, help="a promoted v2 snapshot database")
    parser.add_argument("--out", type=Path, required=True, help="an empty or absent directory for the site")
    args = parser.parse_args(argv)
    manifest = export(args.db.resolve(), args.out.resolve())
    print(json.dumps({k: manifest[k] for k in ("snapshot_id", "counts", "repos", "bytes")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
