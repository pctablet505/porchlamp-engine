"""Export one Porchlamp snapshot database as a static site (GitHub Pages).

The UI in ``web/app`` reads the v2 API (``porchlamp/serve/app.py``). A static host
has no API, so this writes, under ``<out>/data/<snapshot_id>/``, what that
API answers for this database -- every response is read THROUGH the API
(Starlette's ``TestClient`` on :func:`porchlamp.serve.app.create_app`), never
recomputed here -- plus the row lists and orderings ``web/static/api.js``
needs to answer the parameterised requests (search, ecosystem, sort,
paging) in the browser. The one computation the browser does itself is
``POST /reproject`` (custom weights), over the stored pillar columns
exported in ``reproject.json``.

``<out>`` is the whole site: ``web/app`` with ``api.js`` replaced by
``web/static/api.js``, the app's page at each of its views' addresses (``web/app/404.html`` with that view's title
and canonical address, at each path ``web/app/routes.js`` marks ``shell``: ``not-ranked/``, ``matrix/``, ...), a
generated ``data-version.js`` naming the data directory, and ``.nojekyll``. GitHub Pages answers any other missing
address with ``404.html``, where the app shows a project's panel or "page not found". Every page is built from
``web/app/404.html``; once all are written, ``web/prerender.mjs`` (Node) draws the one header, nav and footer into
each (``web/app/components/chrome.js``), and the methodology and report views into their pages (:func:`_prerender`).
``scripts/verify_static_export.py`` checks the exported site against the API, request by request.

The primary checkout cannot import ``porchlamp.serve.app`` while it carries the
owner's uncommitted rollback (``porchlamp.lakehouse.repositories_dump_hash``), so
run this from a clean tree::

    PYTHONPATH=<clean worktree> .venv/bin/python <clean worktree>/scripts/export_static.py \\
        --db ../cvi-scratch/bfs-run/cvi-L4a2.db --out ../cvi-scratch/static-site

With ``--stats <stats-YYYY-MM-DD.json>`` (from ``surprise_stats.py``) the export also writes the report
(``porchlamp/serve/report_pages.py``): its data, ``data/<snapshot_id>/report.json``, and its two pages, which the
prerender draws the report view into: ``black-holes/index.html``, the app's page, and the frozen copy
``black-holes/<snapshot_id>/index.html``, with its own ``styles.css`` and the CSV of the ranked projects beside it.
Older frozen copies are not written here: a publish carries them from the published tree. Then one page per ranked
project (``projects/<owner>/<name>/index.html``, lower case), with its head (``porchlamp/serve/project_pages.py``), which
the prerender draws the project view into from the project's record, ``sitemap-projects.xml`` (every project page,
in balanced-rank order), and ``check/index.html``, a stub that sends the address ``/check/`` on to the rankings
(:func:`check_stub`; the header's search finds a project). The black-hole
evidence the report sums is recomputed from the raw ``pr_window``
envelopes the stored snapshot pinned: pass ``--snapshot-dir`` (the directory ``porchlamp.snapshot store`` read)
and ``--data-dir`` (the data root holding ``raw/``); an envelope that changed since the store stops the export.
The export refuses when the stats file is for another snapshot or disagrees with the database, or when a guarded
sentence would be false. It also refuses any black-hole verdict whose evidence says the exclusion rules' inputs
were not recorded (``rule_inputs_measured`` is not true), and, when evidence files are given, any black hole the
pinned envelope does not explain; with ``--stats`` and no evidence files the report refuses, since it sums that
evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import os
import pwd
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_APP = REPO_ROOT / "web" / "app"
STATIC_API = REPO_ROOT / "web" / "static" / "api.js"
#: The app's route table (its JSON lines sit between these two marker comments) and the app's page with no text of
#: its own, which the export writes at each app view's address.
ROUTES_JS = WEB_APP / "routes.js"
APP_SHELL = WEB_APP / "404.html"
_ROUTES_BEGIN, _ROUTES_END = "// routes:begin", "// routes:end"
#: Draws the chrome into every page, and the views it can into theirs. Outside web/app, so it is never published.
PRERENDER = REPO_ROOT / "web" / "prerender.mjs"

#: Where a per-user Node.js install sits, under a home directory.
_NODE_GLOBS = (
    ".local/opt/node-*/bin/node",
    "tools/node-*/bin/node",
    ".nvm/versions/node/*/bin/node",
    "oss-contributions/node-*/node-*/bin/node",
    "node-*/bin/node",
)

#: ``web/app`` entries the page never loads: its own test suite and the Node
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
    (re.compile(r"/Users/[A-Za-z0-9_.-]+/"), "a local home path"),
)


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
    fields = ("rank", "porchlamp", "coverage", "tier") + tuple(f"p_{p}" for p in pillars) + ("first_reply",)
    return tuple(s for f in fields for s in (f, f"-{f}"))


def _dump(path: Path, payload: Any) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


class _Api:
    """The v2 API over one database, called in-process."""

    def __init__(self, db_path: Path, evidence_files: Any = None) -> None:
        from fastapi.testclient import TestClient

        from porchlamp.serve.app import create_app

        with tempfile.TemporaryDirectory() as empty_web:
            self.client = TestClient(create_app(db_path, web_dir=empty_web, evidence_files=evidence_files))

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


def _export_not_ranked(api: _Api, conn: sqlite3.Connection, snapshot_id: str, persona: str, evidence_files: Any) -> dict:
    """Every not-ranked row for ``persona`` in the API's order, each with the
    group the API files it under. ``/not-ranked`` itself returns only the
    first ``queries.MAX_LIMIT`` of a filtered list, so the full list comes
    from the same store query and the same classification the endpoint
    runs (``porchlamp.serve.app._classify_not_ranked``/``_not_ranked_row``, with
    a black hole's numbers from ``evidence_files``), then is checked against
    the endpoint's own unfiltered answer."""
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
        counts = serve_app._black_hole_counts(conn, snapshot_id, record, evidence_files)
        row = serve_app._not_ranked_row(record, group=group, reason=reason, counts=counts).model_dump(mode="json")
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
#: The reply measures every leaderboard and reprojected row carries (``porchlamp.store.queries.with_reply``).
_REPLY_FIELDS = ("first_reply_hours", "first_reply_n", "no_reply_14d_share", "no_reply_14d_n")


def _export_reproject(conn: sqlite3.Connection, snapshot_id: str, details: dict[tuple[str, str], dict]) -> dict:
    """What ``queries.reproject`` reads, for every repo it may rank
    (``queries.REPROJECT_CANDIDATE_SQL``: stage-0 eligible and not routed
    off-platform, the one definition the API and this export share): the
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
        f"WHERE snapshot_id = ? AND {queries.REPROJECT_CANDIDATE_SQL} ORDER BY lower(owner || '/' || name)",
        (snapshot_id,),
    ).fetchall()
    lowered = [f"{o}/{n}".lower() for o, n, _t, _l in tail]
    if len(set(lowered)) != len(lowered):
        raise SystemExit("export: two reprojection candidates share lower(owner/name); D-TIE-1's last level is not total")
    # the reply measures a row carries, read and decoded as the store reads them for the API's rows
    cursor = conn.execute(
        f"SELECT r.owner AS owner, r.name AS name, {queries.REPLY_SELECT_SQL} FROM repositories r{queries.REPLY_JOIN_SQL} "
        f"WHERE r.snapshot_id = ? AND {queries.REPROJECT_CANDIDATE_SQL}",
        (snapshot_id,),
    )
    columns = [d[0] for d in cursor.description]
    replies = {(rec["owner"], rec["name"]): rec for rec in (queries.with_reply(dict(zip(columns, raw))) for raw in cursor.fetchall())}
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
        row.update({field: replies[(owner, name)][field] for field in _REPLY_FIELDS})
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
    for entry in WEB_APP.iterdir():
        if entry.name in _WEB_SKIP:
            continue
        target = out / entry.name
        if entry.is_dir():
            shutil.copytree(entry, target)
        else:
            shutil.copy2(entry, target)
    shutil.copy2(STATIC_API, out / "api.js")


def app_routes() -> list[dict]:
    """The app's route table, ``ROUTES`` of ``web/app/routes.js``: the JSON lines between its marker comments."""
    lines = ROUTES_JS.read_text(encoding="utf-8").splitlines()
    stripped = [line.strip() for line in lines]
    try:
        body = lines[stripped.index(_ROUTES_BEGIN) + 1 : stripped.index(_ROUTES_END)]
        routes = json.loads("[" + "\n".join(body) + "]")
    except ValueError as error:
        raise SystemExit(f"export: {ROUTES_JS.name} has no JSON route table between {_ROUTES_BEGIN!r} and {_ROUTES_END!r}: {error}") from error
    return routes


def view_page(route: dict, template: Path = APP_SHELL) -> str:
    """``404.html`` with one view's title and canonical address: the app's page at that view's path."""
    from porchlamp.serve import report_pages

    try:
        return report_pages.app_page(
            title=route["title"],
            head=f'\n  <link rel="canonical" href="{html.escape(report_pages.SITE + route["path"])}">',
            template=template,
        )
    except report_pages.ReportError as error:
        raise SystemExit(f"export: {error}") from error


#: Views whose page another writer makes, with a head of its own: the report (:func:`_write_report_pages`, which
#: needs ``--stats``; without it ``/black-holes/`` is 404.html, where the app says the report did not load).
VIEWS_WRITTEN_ELSEWHERE = frozenset({"report"})


def not_ranked_reason(row: dict) -> str:
    """A not-ranked row's reason as the route table names it (a list's ``fixed.reason``): its gate or unrated reason,
    without the engine's ``"unrated: "`` prefix (copy.js ``bareUnratedReason``)."""
    return (row["gate_reason"] or row["unrated_reason"]).removeprefix("unrated: ")


def route_instances(route: dict, boards: dict[str, dict], not_ranked: dict[str, dict]) -> list[str]:
    """The addresses a route answers at: its path, or, for a path with ``{page}``, one per page of the list its fixed
    state names, ``fixed.page_size`` rows a page: a ranking (its lens, all ecosystems or one), counted in ``boards``
    (each lens's leaderboard: ``{"rows", ...}``, the API's own rows), or the not-ranked projects of one reason,
    counted in ``not_ranked`` (each lens's not-ranked list: ``{"rows": [{"group", "row"}, ...]}``). A ranking always
    has its first page; a reason no project has gets no page."""
    path = route["path"]
    if "{page}" not in path:
        return [path]
    fixed = route.get("fixed") or {}
    persona, eco = fixed.get("persona") or "balanced", fixed.get("eco")
    if "reason" in fixed:
        listed = sum(1 for entry in not_ranked[persona]["rows"] if not_ranked_reason(entry["row"]) == fixed["reason"])
        pages = -(-listed // fixed["page_size"])
    else:
        listed = sum(1 for row in boards[persona]["rows"] if eco is None or row["ecosystem"] == eco)
        pages = max(1, -(-listed // fixed["page_size"]))
    return [path.replace("{page}", str(n)) for n in range(1, pages + 1)]


def _write_view_pages(out: Path, boards: dict[str, dict], not_ranked: dict[str, dict]) -> list[dict]:
    """The app's page at each address of each view it draws in place (``shell`` in the route table; every page of a
    route with ``{page}``, :func:`route_instances`), so a static host serves every view at its own path; ``/`` is
    ``index.html`` itself. Written after every other page: a path two pages claim stops the export. Returns each page
    for :func:`_prerender`, with the address it answers at."""
    written = []
    for route in app_routes():
        if not route["shell"] or route["path"] == "/" or route["id"] in VIEWS_WRITTEN_ELSEWHERE:
            continue
        for address in route_instances(route, boards, not_ranked):
            if not re.fullmatch(r"(/[a-z0-9-]+)+/", address):
                raise SystemExit(f"export: the {route['id']} view's address {address!r} is not a plain directory path")
            target = out / address.strip("/") / "index.html"
            if target.exists():
                raise SystemExit(f"export: {target.relative_to(out)} is both the {route['id']} view and another page")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(view_page({**route, "path": address}), encoding="utf-8")
            written.append({"path": target.relative_to(out).as_posix(), "view": route.get("view", route["id"]), "address": address})
    return written


def listed_addresses(boards: dict[str, dict], not_ranked: dict[str, dict]) -> list[str]:
    """The list views' addresses the sitemap names: every address of every preset route (one with ``fixed``), in the
    route table's order."""
    return [address for route in app_routes() if route.get("fixed") and route["shell"]
            for address in route_instances(route, boards, not_ranked)]


def data_version_js(snapshot_id: str) -> str:
    """``data-version.js``: the data directory the site's api.js (``web/static/api.js``) reads."""
    return (
        "// Generated by scripts/export_static.py: the data directory this build reads.\n"
        f"export const DATA_VERSION = {json.dumps(snapshot_id)};\n"
    )


def _prerender(out: Path, pages: list[dict]) -> None:
    """Draws every page's header, nav and footer, and the methodology, report and project views into their pages
    (``web/prerender.mjs``), from the data this export wrote, loaded through the site's own api.js. ``pages``:
    ``{"path", "view"}`` for a page of the app (``"frozen": true`` for the report's frozen copy), ``{"path", "nav"}``
    (its nav entry, or None) for any other, with ``"project": "owner/name"`` for a project's page. The page list
    lives outside the site."""
    node = find_node()
    if node is None:
        raise SystemExit("export: no Node.js interpreter for web/prerender.mjs (PATH, PORCHLAMP_NODE_BIN or ~/.local/opt/node-*)")
    with tempfile.TemporaryDirectory() as tmp:
        job = Path(tmp) / "pages.json"
        job.write_text(json.dumps({"pages": pages}), encoding="utf-8")
        ran = subprocess.run(
            [node, str(PRERENDER), str(out), str(job)], capture_output=True, text=True, timeout=900, check=False,
        )
    if ran.returncode != 0:
        raise SystemExit(f"export: web/prerender.mjs failed ({ran.returncode}): {ran.stderr[-3000:]}")


def _scan_for_forbidden(out: Path) -> None:
    for path in sorted(out.rglob("*")):
        if not path.is_file() or path.suffix not in {".json", ".js", ".html", ".css", ".csv"}:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern, what in _FORBIDDEN:
            match = pattern.search(text)
            if match:
                raise SystemExit(f"export: {path.relative_to(out)} contains {what} ({match.group(0)[:40]!r}); not publishing")


def _load_stats(path: Path) -> dict:
    try:
        stats = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SystemExit(f"export: cannot read the stats file {path}: {error}") from error
    if not isinstance(stats, dict):
        raise SystemExit(f"export: the stats file {path} is not a JSON object")
    return stats


def _refuse_black_holes_without_rule_inputs(details: dict[tuple[str, str], dict], *, evidence_read: bool) -> None:
    """Stop the export when a black-hole verdict would be published without the inputs its rules need: its
    evidence says the draft and close-actor fields were not recorded, or (when evidence was read) there is no
    evidence for it at all. Without evidence files the verdicts carry no evidence; the report (``--stats``) then
    refuses, since it sums that evidence."""
    black_holes = [d for d in details.values() if d["gate_reason"] == "black_hole"]
    unmeasured = sum(
        1 for d in black_holes
        if d["black_hole_evidence"] is not None and d["black_hole_evidence"].get("rule_inputs_measured") is not True
    )
    unexplained = sum(1 for d in black_holes if d["black_hole_evidence"] is None) if evidence_read else 0
    if unmeasured:
        raise SystemExit(
            f"export: {unmeasured} of {len(black_holes)} black-hole verdicts lack the exclusion rules' inputs "
            "(rule_inputs_measured is not true); not publishing"
        )
    if unexplained:
        raise SystemExit(
            f"export: {unexplained} of {len(black_holes)} black-hole verdicts have no evidence from their pinned "
            "pr_window envelope; not publishing"
        )


def _db_rows_for_report(conn: sqlite3.Connection, snapshot_id: str) -> tuple[list[tuple], int]:
    rows = conn.execute(
        "SELECT ecosystem, gate_reason, gate_undecided FROM repositories WHERE snapshot_id = ?", (snapshot_id,)
    ).fetchall()
    rated = conn.execute(
        "SELECT COUNT(*) FROM repositories WHERE snapshot_id = ? AND rated = 1", (snapshot_id,)
    ).fetchone()[0]
    return rows, rated


def _report_facts(
    conn: sqlite3.Connection, snapshot: dict, methodology: dict, stats: dict, boards: dict[str, dict],
    details: dict[tuple[str, str], dict],
) -> Any:
    """Every number the report prints, from its source (``porchlamp.serve.report_pages.build_facts``);
    a stats file that disagrees with the database, or a guarded sentence that would be false, stops the export."""
    from porchlamp.serve import report_pages

    snapshot_id = snapshot["snapshot_id"]
    db_rows, db_rated = _db_rows_for_report(conn, snapshot_id)
    black_holes = [d for d in details.values() if d["gate_reason"] == "black_hole"]
    try:
        return report_pages.build_facts(
            snapshot=snapshot, methodology=methodology, stats=stats, db_rows=db_rows, db_rated=db_rated,
            api_ranked=boards["balanced"]["total"], api_black_holes=len(black_holes),
            evidence=[d["black_hole_evidence"] for d in black_holes if d["black_hole_evidence"]],
        )
    except report_pages.ReportError as error:
        raise SystemExit(f"export: {error}") from error


def _report_json(facts: Any) -> dict:
    """``report.json``: the report's numbers and its guarded sentences (``report_pages.report_json``)."""
    from porchlamp.serve import report_pages

    try:
        return report_pages.report_json(facts)
    except report_pages.ReportError as error:
        raise SystemExit(f"export: {error}") from error


def _write_report_pages(out: Path, facts: Any, boards: dict[str, dict], manifest: dict) -> list[dict]:
    """The report's two pages, for the prerender to draw the report view into: ``black-holes/index.html``, the app's
    page, and the frozen copy beside its own ``styles.css`` and the CSV. Returns them for :func:`_prerender`."""
    from porchlamp.core import constants
    from porchlamp.serve import report_pages

    exported_on = date.fromisoformat(manifest["exported_at"][:10])
    lenses = sorted(constants.PERSONA_PRESETS)
    frozen = out / "black-holes" / facts.snapshot_id
    frozen.mkdir(parents=True, exist_ok=True)
    try:
        (out / "black-holes" / "index.html").write_text(
            report_pages.report_page(facts, frozen=False, exported_on=exported_on), encoding="utf-8"
        )
        (frozen / "index.html").write_text(report_pages.report_page(facts, frozen=True, exported_on=exported_on), encoding="utf-8")
        (frozen / "styles.css").write_text(report_pages.frozen_stylesheet(), encoding="utf-8")
    except report_pages.ReportError as error:
        raise SystemExit(f"export: {error}") from error
    (frozen / facts.csv_name).write_text(report_pages.ranked_csv(boards, lenses), encoding="utf-8")
    return [
        {"path": "black-holes/index.html", "view": "report"},
        {"path": f"black-holes/{facts.snapshot_id}/index.html", "view": "report", "frozen": True},
    ]


def _add_project_extras(buckets: dict[str, dict[str, dict]], boards: dict[str, dict], methodology: dict) -> None:
    """Writes into each ranked project's record what its page and side panel print beside the API's detail and
    evidence: the answer sentences, its standing in each lens and its similar projects
    (``project_pages.record_extras``). With ``--stats`` only, as the project pages are."""
    from porchlamp.serve import project_pages as pp
    from porchlamp.serve import report_pages

    try:
        index = pp.ProjectIndex(boards, pp.Copy.load())
        min_n = {p["input_id"]: p["min_n"] for p in methodology["parameters"]}
        for records in buckets.values():
            for record in records.values():
                extras = pp.record_extras(index, record["detail"], record["evidence"], min_n)
                if extras is not None:
                    record.update(extras)
    except report_pages.ReportError as error:
        raise SystemExit(f"export: {error}") from error


def _write_project_pages(
    out: Path, facts: Any, methodology: dict, boards: dict[str, dict], buckets: dict[str, dict[str, dict]], snapshot: dict
) -> list[dict]:
    """One page per ranked project (``projects/<owner>/<name>/``) with its head, and ``sitemap-projects.xml``.
    :func:`_prerender` draws the project view into each page from the project's record. Returns the pages for it: none
    of them is a nav entry's."""
    from porchlamp.serve import project_pages as pp
    from porchlamp.serve import report_pages

    try:
        index = pp.ProjectIndex(boards, pp.Copy.load())
        missing = sorted({p["input_id"] for p in methodology["parameters"]} - set(report_pages.copy_js_map("PARAM_MEANINGS")))
        if missing:
            raise report_pages.ReportError(f"copy.js has no one-line meaning for the parameters {missing}")
        min_n = {p["input_id"]: p["min_n"] for p in methodology["parameters"]}
        as_of = date.fromisoformat(snapshot["as_of"][:10])
        pages = []
        for owner, name in index.keys:
            record = buckets[bucket_of(owner, name)][f"{owner}/{name}"]
            reply = pp.answer_reply(record["detail"], record["evidence"], min_n)
            description = pp.head_description(f"{owner}/{name}", index.standing(pp.BALANCED, record["detail"]), reply)
            target = out / pp.project_path(owner, name)
            target.mkdir(parents=True, exist_ok=True)
            (target / "index.html").write_text(pp.project_page(owner, name, description), encoding="utf-8")
            pages.append({"path": f"{pp.project_path(owner, name)}index.html", "nav": None, "project": f"{owner}/{name}"})
        (out / "sitemap-projects.xml").write_text(pp.sitemap_projects(index, as_of), encoding="utf-8")
    except report_pages.ReportError as error:
        raise SystemExit(f"export: {error}") from error
    return pages


def check_stub() -> str:
    """``check/index.html``: the address ``/check/`` as a stub that sends a reader on to the rankings, at once (a meta
    refresh) and for a search engine (the canonical address), with the 404 page's link to them for a browser that does
    not follow the refresh. No script, no stylesheet, and no place in the sitemap: the header's search finds a project."""
    from porchlamp.serve import report_pages

    home = next(route for route in app_routes() if route["id"] == "ranked")
    path, link = html.escape(home["path"]), html.escape(report_pages.copy_js_string("NOT_FOUND_HOME_LINK"))
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"  <title>{html.escape(home['title'])}</title>\n"
        f'  <meta http-equiv="refresh" content="0; url={path}">\n'
        f'  <link rel="canonical" href="{html.escape(report_pages.SITE)}{path}">\n'
        f'</head>\n<body>\n  <p><a href="{path}">{link}</a></p>\n</body>\n</html>\n'
    )


def _write_home(out: Path, data: Path) -> None:
    """The home page's description and structured data, ``llms.txt`` and ``feed.xml``
    (``porchlamp/serve/home_pages.py``), from this export's own data files: the snapshot, the methodology, the report
    and the balanced leaderboard. ``web/prerender.mjs`` draws the ranked view into the home page afterwards."""
    from porchlamp.serve import home_pages, report_pages

    def load(name: str) -> Any:
        return json.loads((data / name).read_text(encoding="utf-8"))

    try:
        f = home_pages.home_facts(load("snapshot.json"), load("methodology.json"), load("report.json"),
                                  load("leaderboard-balanced.json")["rows"], app_routes())
        (out / "index.html").write_text(home_pages.home_page((WEB_APP / "index.html").read_text(encoding="utf-8"), f), encoding="utf-8")
        (out / "llms.txt").write_text(home_pages.llms_txt(f), encoding="utf-8")
        (out / "feed.xml").write_text(home_pages.feed_xml(f), encoding="utf-8")
    except report_pages.ReportError as error:
        raise SystemExit(f"export: {error}") from error


def _sitemap_pages(out: Path, snapshot_id: str, as_of: str, listed: list[str]) -> str:
    """``sitemap-pages.xml``: the site's own pages that this export wrote (the root, the report and its frozen copy,
    the methodology, then the list views at ``listed``, :func:`listed_addresses`), in that order, each dated with the
    snapshot. A page the export did not write is not listed, nor the ``/check/`` stub."""
    from porchlamp.serve import report_pages

    paths = ["", "black-holes/", f"black-holes/{snapshot_id}/", "methodology/", *(a.lstrip("/") for a in listed)]
    stamp = as_of[:10]
    urls = "".join(
        f"  <url>\n    <loc>{report_pages.SITE}/{p}</loc>\n    <lastmod>{stamp}</lastmod>\n  </url>\n"
        for p in paths if (out / p / "index.html").is_file()
    )
    return '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + urls + "</urlset>\n"


def export(
    db: Path, out: Path, stats_path: Optional[Path] = None, *,
    snapshot_dir: Optional[Path] = None, data_dir: Optional[Path] = None,
) -> dict:
    from porchlamp.core import constants
    from porchlamp.serve.app import PinnedEvidenceFiles, _current_snapshot_id, _open_v2_connection

    if (snapshot_dir is None) != (data_dir is None):
        raise SystemExit("export: --snapshot-dir and --data-dir go together (the black-hole evidence needs both)")
    evidence_files = PinnedEvidenceFiles(snapshot_dir, data_dir) if snapshot_dir is not None else None

    stats = _load_stats(stats_path) if stats_path is not None else None

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

        api = _Api(copy, evidence_files)
        conn = _open_v2_connection(str(copy))
        snapshot_id = _current_snapshot_id(conn)
        snapshot = api.get("/v1/porchlamp/snapshot")
        if snapshot["snapshot_id"] != snapshot_id:
            raise SystemExit("export: the API serves a different snapshot than the database's current one")
        if stats is not None:  # refuse a stats file that is not this snapshot's before the long part
            from porchlamp.serve import report_pages

            try:
                report_pages.check_stats_against_db(stats, snapshot_id, *_db_rows_for_report(conn, snapshot_id))
            except report_pages.ReportError as error:
                raise SystemExit(f"export: {error}") from error
        data = out / "data" / snapshot_id
        sizes: dict[str, int] = {}
        sizes["snapshot.json"] = _dump(data / "snapshot.json", snapshot)
        methodology = api.get("/v1/porchlamp/methodology")
        sizes["methodology.json"] = _dump(data / "methodology.json", methodology)
        sizes["matrix.json"] = _dump(data / "matrix.json", api.get("/v1/porchlamp/matrix"))

        personas = sorted(constants.PERSONA_PRESETS)
        sorts = sort_keys(constants.PILLAR_NAMES)
        boards: dict[str, dict] = {}
        not_ranked: dict[str, dict] = {}
        for persona in personas:
            name = f"leaderboard-{persona}.json"
            boards[persona] = _export_leaderboard(api, persona, sorts)
            sizes[name] = _dump(data / name, boards[persona])
            name = f"not-ranked-{persona}.json"
            not_ranked[persona] = _export_not_ranked(api, conn, snapshot_id, persona, evidence_files)
            sizes[name] = _dump(data / name, not_ranked[persona])

        # Every bucket is written, empty ones too: a repo absent from the snapshot
        # must read as a 404 for that repo, never as a missing data file.
        buckets: dict[str, dict[str, dict]] = {f"{b:02x}": {} for b in range(BUCKETS)}
        details: dict[tuple[str, str], dict] = {}
        for owner, name, record in _repo_records(api, conn, snapshot_id):
            buckets.setdefault(bucket_of(owner, name), {})[f"{owner}/{name}"] = record
            details[(owner, name)] = record["detail"]
        _refuse_black_holes_without_rule_inputs(details, evidence_read=evidence_files is not None)
        facts = None
        if stats is not None:
            _add_project_extras(buckets, boards, methodology)
            facts = _report_facts(conn, snapshot, methodology, stats, boards, details)
            sizes["report.json"] = _dump(data / "report.json", _report_json(facts))
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
    pages = [{"path": "index.html", "view": "ranked"}, {"path": "404.html", "view": "notfound"}]
    if facts is not None:
        pages += _write_report_pages(out, facts, boards, manifest)
        pages += _write_project_pages(out, facts, methodology, boards, buckets, snapshot)
        (out / "check").mkdir()
        (out / "check" / "index.html").write_text(check_stub(), encoding="utf-8")
        _write_home(out, out / "data" / snapshot_id)
    pages += _write_view_pages(out, boards, not_ranked)
    (out / "data-version.js").write_text(data_version_js(snapshot_id), encoding="utf-8")
    _prerender(out, pages)
    (out / "sitemap-pages.xml").write_text(_sitemap_pages(out, snapshot_id, snapshot["as_of"], listed_addresses(boards, not_ranked)), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    _scan_for_forbidden(out)
    return manifest


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", type=Path, required=True, help="a promoted v2 snapshot database")
    parser.add_argument("--out", type=Path, required=True, help="an empty or absent directory for the site")
    parser.add_argument(
        "--stats", type=Path, default=None,
        help="surprise_stats.py's stats-<snapshot>.json: also write the report and methodology pages",
    )
    parser.add_argument(
        "--snapshot-dir", type=Path, default=None,
        help="the snapshot directory the database was stored from (pins each black hole's pr_window envelope)",
    )
    parser.add_argument("--data-dir", type=Path, default=None, help="the data root holding raw/pr_window/")
    args = parser.parse_args(argv)
    manifest = export(
        args.db.resolve(), args.out.resolve(), args.stats.resolve() if args.stats else None,
        snapshot_dir=args.snapshot_dir.resolve() if args.snapshot_dir else None,
        data_dir=args.data_dir.resolve() if args.data_dir else None,
    )
    print(json.dumps({k: manifest[k] for k in ("snapshot_id", "counts", "repos", "bytes")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
