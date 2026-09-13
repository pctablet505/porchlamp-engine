"""Serve `web/v2/` plus the v2 API surface from static JSON fixtures.

Packet W4-4 (the real store-backed API) has not landed yet. This is a
stdlib-only (`http.server`, no FastAPI, no third-party deps) stand-in so
`web/v2/` can be built and exercised against the wire contract in
`cvi/serve/contract.py` before W4-4 exists. Filtering, sorting and paging
happen here in Python over the fixture files under `tests/fixtures/ui/` — the
UI's query parameters are genuinely exercised, not just echoed.

Endpoints (all under `/v1/cvi/`, matching `docs/v2/04-ARCHITECTURE.md` §7 and
`docs/v2/06-UX-DESIGN.md` §4):

    GET /v1/cvi/leaderboard?persona=&eco=&q=&sort=&page=&page_size=
    GET /v1/cvi/not-ranked
    GET /v1/cvi/repos/{owner}/{name}/evidence
    GET /v1/cvi/snapshot
    GET /healthz

Everything else is served as a static file from `web/v2/` (default `/` ->
`index.html`).

Query parameter semantics (fixture-server contract; W4-4 owns the binding
real one):

- `persona`: one of `cvi.serve.contract.PERSONAS`, default `balanced`.
  `tests/fixtures/ui/leaderboard-first_timer.json` is a genuinely
  re-projected fixture (different ranks) and is served verbatim for
  `persona=first_timer`. The other three non-balanced personas have no
  dedicated re-projected fixture yet, so they fall back to the balanced
  ordering with only that persona's *gated* rows removed — a real
  re-projection is a W4-4 concern (SQL `reproject`, `04` §6), not this
  fixture stub's.
- `eco`: exact match against `ecosystem` (case-insensitive), unfiltered when
  omitted or empty.
- `q`: case-insensitive substring match against `"owner/name"`.
- `sort`: one of `rank, cvi, coverage, tier, p_responsiveness,
  p_criticality, p_governance, p_rigor, p_accessibility` (the columns Screen
  A allows sorting on — U-3 vanity columns are never sortable, and this
  server rejects any other value with 400). A leading `-` reverses the
  field's natural "best first" direction. Default `rank` (ascending — rank 1
  first). `tier` sorts by tier ordinal S > A > B > C > D > F. This encoding
  (lead-mandated 2026-09-13) is mirrored EXACTLY by
  `web/v2/components/table.js`'s `sortDirectionLabel` (the header's
  `aria-sort` label) and `web/v2/app.js`'s click-to-toggle handler — all
  three must agree on which fields default to descending vs ascending.
- `page`, `page_size`: 1-based page, `page_size` one of
  `cvi.serve.contract.LEADERBOARD_PAGE_SIZES` (20, 50, 100, 500, 1000;
  owner instruction 2026-09-13, default 20). `total` and `counts` always
  reflect the full filtered set / the whole snapshot respectively —
  `counts` (rated/gated/unrated) never changes with filtering (Screen A
  requirement: the counts beside the Ranked/Not-ranked sub-tabs never
  disappear on filter).

Run:
    PYTHONPATH=$PWD .venv/bin/python scripts/serve_ui_fixtures.py --port 8899
    PYTHONPATH=$PWD .venv/bin/python scripts/serve_ui_fixtures.py --port 0   # OS-assigned free port; printed on start
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from cvi.serve.contract import DEFAULT_PAGE_SIZE, LEADERBOARD_PAGE_SIZES

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WEB_DIR = REPO_ROOT / "web" / "v2"
DEFAULT_FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "ui"

#: Screen A: sort only on rank, CVI, coverage, tier, pillars (U-3: vanity
#: columns — stars, forks, downloads — carry no sort affordance at all).
SORTABLE_FIELDS = {
    "rank", "cvi", "coverage", "tier",
    "p_responsiveness", "p_criticality", "p_governance", "p_rigor", "p_accessibility",
}
#: Owner instruction 2026-09-13: 20/50/100/500/1000, default 20 — defined
#: once in cvi/serve/contract.py, imported here so the fixture server and
#: the real W4-4 API can never drift apart on the allowed set.
ALLOWED_PAGE_SIZES = LEADERBOARD_PAGE_SIZES
TIER_ORDER = {"S": 0, "A": 1, "B": 2, "C": 3, "D": 4, "F": 5}


class FixtureStore:
    """Loads `tests/fixtures/ui/*.json` once and answers the v2 endpoints."""

    def __init__(self, fixtures_dir: Path) -> None:
        self.dir = fixtures_dir
        self.leaderboard: dict[str, Any] = self._load("leaderboard.json")
        self.leaderboard_first_timer: dict[str, Any] = self._load("leaderboard-first_timer.json")
        self.notranked: dict[str, Any] = self._load("notranked.json")
        self.notranked_by_persona: dict[str, dict[str, Any]] = {"balanced": self.notranked}
        first_timer_notranked = self.dir / "notranked-first_timer.json"
        if first_timer_notranked.exists():
            self.notranked_by_persona["first_timer"] = json.loads(first_timer_notranked.read_text())
        self.snapshot: dict[str, Any] = self._load("snapshot.json")
        self.evidence: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for path in sorted(self.dir.glob("evidence-*.json")):
            owner, name = self._parse_evidence_filename(path.name)
            self.evidence[(owner, name)] = json.loads(path.read_text())

    def _load(self, name: str) -> dict[str, Any]:
        path = self.dir / name
        if not path.exists():
            raise SystemExit(f"missing fixture: {path}")
        return json.loads(path.read_text())

    @staticmethod
    def _parse_evidence_filename(filename: str) -> tuple[str, str]:
        # "evidence-<owner>__<name>.json" — double underscore, unambiguous
        # because neither a GitHub owner nor a repo name may contain one.
        #
        # Review Blocker B-7 (2026-09-13): the previous encoding was
        # "evidence-<owner>-<name>.json" split by "guess the midpoint word",
        # which silently mis-parsed any owner/name pair not evenly
        # word-balanced (ivywood/template-cli-71, 1 owner word vs. 3 name
        # words, split into "ivywood-template" / "cli-71") — 404ing a real
        # row's evidence forever. `TestEvidenceEndpoint::
        # test_every_evidence_fixture_file_is_reachable_at_200` in
        # tests/test_serve_ui_fixtures.py is the regression test this
        # encoding change exists to make pass (the module docstring
        # previously claimed that test existed; it did not).
        stem = filename[len("evidence-"):-len(".json")]
        owner, sep, name = stem.partition("__")
        if not sep:
            raise ValueError(
                f"evidence fixture filename {filename!r} has no '__' owner/name separator"
            )
        return owner, name

    def rows_for_persona(self, persona: str) -> list[dict[str, Any]]:
        if persona == "first_timer":
            return list(self.leaderboard_first_timer["rows"])
        rows = [dict(r) for r in self.leaderboard["rows"]]
        if persona != "balanced":
            rows = [r for r in rows if persona not in r.get("persona_gates", {})]
        return rows

    def notranked_for_persona(self, persona: str) -> dict[str, Any]:
        # Only "balanced" and "first_timer" have a dedicated fixture (the
        # other three non-balanced personas fall back to "balanced" with an
        # empty persona_hidden group — same fixture-stub limitation as
        # rows_for_persona above).
        return self.notranked_by_persona.get(persona, self.notranked)

    def leaderboard_page(
        self, *, persona: str, eco: str | None, q: str | None, sort: str, page: int, page_size: int
    ) -> dict[str, Any]:
        rows = self.rows_for_persona(persona)
        if eco:
            eco_lower = eco.lower()
            rows = [r for r in rows if r["ecosystem"].lower() == eco_lower]
        if q:
            q_lower = q.lower()
            rows = [r for r in rows if q_lower in f"{r['owner']}/{r['name']}".lower()]

        field = sort[1:] if sort.startswith("-") else sort
        reverse_flag = sort.startswith("-")
        if field not in SORTABLE_FIELDS:
            raise ValueError(f"unsortable field {field!r}; allowed: {sorted(SORTABLE_FIELDS)}")

        def sort_key(row: dict[str, Any]):
            if field == "tier":
                value = TIER_ORDER.get(row.get("tier") or "F", 5)
            else:
                value = row.get(field)
                if value is None:
                    value = float("inf") if field == "rank" else float("-inf")
            return value

        # "Best first" defaults: rank/tier ascending (rank 1, tier S first),
        # every score/coverage field descending (higher is better).
        best_first_ascending = field in ("rank", "tier")
        rows.sort(key=sort_key, reverse=(not best_first_ascending) != reverse_flag)

        total = len(rows)
        start = (page - 1) * page_size
        page_rows = rows[start:start + page_size]
        return {
            "rows": page_rows,
            "total": total,
            "counts": self.leaderboard["counts"],
            "page": page,
            "page_size": page_size,
            "persona": persona,
            "ecosystem": eco or None,
        }


def _query_params(path: str) -> tuple[str, dict[str, str]]:
    parsed = urllib.parse.urlsplit(path)
    params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
    return parsed.path, params


def make_handler(
    store: FixtureStore, web_dir: Path, fail_endpoints: frozenset[str] = frozenset()
) -> type[BaseHTTPRequestHandler]:
    """`fail_endpoints`: endpoint names (currently only "snapshot" is wired)
    that unconditionally return 500 — lets a test or a manual UI check
    exercise U-15's error-state requirement for the ONE fetch
    (`GET /v1/cvi/snapshot`) that, before this fix, had no error rendering
    at all (review 2026-09-13, NB-2/W-4: the footer/snapshot chip rested on
    a literal "Loading..." forever on any snapshot failure)."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "CVIUIFixtureServer/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:  # quieter test runs
            pass

        def _send_json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_error_json(self, status: int, message: str) -> None:
            self._send_json(status, {"error": message})

        def _serve_static(self, path: str) -> None:
            rel = path.lstrip("/") or "index.html"
            candidate = (web_dir / rel).resolve()
            try:
                candidate.relative_to(web_dir.resolve())
            except ValueError:
                self._send_error_json(403, "forbidden")
                return
            if candidate.is_dir():
                candidate = candidate / "index.html"
            if not candidate.is_file():
                self._send_error_json(404, f"not found: {path}")
                return
            content_type, _ = mimetypes.guess_type(str(candidate))
            if candidate.suffix == ".mjs":
                content_type = "text/javascript"
            body = candidate.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path, params = _query_params(self.path)

            if path == "/healthz":
                self._send_json(200, {"ok": True})
                return

            if path == "/v1/cvi/leaderboard":
                try:
                    page = int(params.get("page", "1"))
                    page_size = int(params.get("page_size", str(DEFAULT_PAGE_SIZE)))
                except ValueError:
                    self._send_error_json(400, "page and page_size must be integers")
                    return
                if page < 1:
                    self._send_error_json(400, "page must be >= 1")
                    return
                if page_size not in ALLOWED_PAGE_SIZES:
                    self._send_error_json(
                        400, f"page_size must be one of {ALLOWED_PAGE_SIZES}, got {page_size}"
                    )
                    return
                persona = params.get("persona", "balanced")
                try:
                    result = store.leaderboard_page(
                        persona=persona,
                        eco=params.get("eco"),
                        q=params.get("q"),
                        sort=params.get("sort", "rank"),
                        page=page,
                        page_size=page_size,
                    )
                except ValueError as exc:
                    self._send_error_json(400, str(exc))
                    return
                self._send_json(200, result)
                return

            if path == "/v1/cvi/not-ranked":
                persona = params.get("persona", "balanced")
                self._send_json(200, store.notranked_for_persona(persona))
                return

            if path == "/v1/cvi/snapshot":
                if "snapshot" in fail_endpoints:
                    self._send_error_json(500, "simulated snapshot failure (--fail snapshot)")
                    return
                self._send_json(200, store.snapshot)
                return

            if path.startswith("/v1/cvi/repos/") and path.endswith("/evidence"):
                remainder = path[len("/v1/cvi/repos/"):-len("/evidence")]
                parts = remainder.split("/")
                if len(parts) != 2:
                    self._send_error_json(404, f"malformed repo path: {path}")
                    return
                owner, name = parts
                rows = store.evidence.get((owner, name))
                if rows is None:
                    self._send_error_json(404, f"no evidence fixture for {owner}/{name}")
                    return
                self._send_json(200, rows)
                return

            if path.startswith("/v1/"):
                self._send_error_json(404, f"no such v2 fixture endpoint: {path}")
                return

            self._serve_static(path)

    return Handler


def serve(
    *, host: str, port: int, web_dir: Path, fixtures_dir: Path, fail_endpoints: frozenset[str] = frozenset()
) -> ThreadingHTTPServer:
    store = FixtureStore(fixtures_dir)
    handler = make_handler(store, web_dir, fail_endpoints)
    httpd = ThreadingHTTPServer((host, port), handler)
    return httpd


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8899, help="0 for an OS-assigned free port")
    parser.add_argument("--web-dir", type=Path, default=DEFAULT_WEB_DIR)
    parser.add_argument("--fixtures-dir", type=Path, default=DEFAULT_FIXTURES_DIR)
    parser.add_argument(
        "--fail",
        action="append",
        default=[],
        metavar="ENDPOINT",
        help="force ENDPOINT to return 500 (repeatable); currently only 'snapshot' is wired — "
        "for manually checking the U-15 error-state UI without editing a fixture",
    )
    args = parser.parse_args(argv)

    httpd = serve(
        host=args.host, port=args.port, web_dir=args.web_dir, fixtures_dir=args.fixtures_dir,
        fail_endpoints=frozenset(args.fail),
    )
    actual_port = httpd.server_address[1]
    print(f"CVI UI fixture server: http://{args.host}:{actual_port}/ "
          f"(web={args.web_dir}, fixtures={args.fixtures_dir})")
    sys.stdout.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
