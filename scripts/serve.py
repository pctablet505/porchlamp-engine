"""CVI production serving entrypoint: verified SQLite + the web front end.

Unlike ``scripts/demo_app.py`` (which seeds synthetic ``benchmark_data``
fixtures), this opens the ALREADY-PROMOTED operational store written by
``scripts/run_all.py`` / ``scripts/promote.py`` — the only rows are bundles
that passed ``scripts/verify.py`` — and serves the CVI REST API
(:func:`cvi.api.create_app`) plus the offline single-page UI in
``web/index.html`` mounted at ``/``. No seeding happens here: an empty DB
serves an empty (but honest) dashboard.

The guard of packet W0-4 (`03` §5, F1) runs before the server starts and
refuses, naming the row that broke it:

* a row whose telemetry equals a ``cvi/benchmark_data.py`` fixture row (the
  synthetic catalog is test fixture only, never a ranking source);
* a DB with no snapshot identity — the ``manifest.json`` that
  ``python -m cvi.snapshot freeze`` writes under ``<db dir>/snapshots/``.

Run:
    .venv/bin/python scripts/serve.py                        # data/cvi.db, port 8000
    .venv/bin/python scripts/serve.py --db /path/cvi.db
    PORT=8765 .venv/bin/python scripts/serve.py              # port override
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from cvi.api import create_app
from cvi.benchmark_data import get_benchmark_catalog
from cvi.lakehouse import OperationalStore
from cvi.snapshot.manifest import MANIFEST_FILENAME, SNAPSHOTS_DIRNAME

_REPO_ROOT: Path = Path(__file__).resolve().parent.parent
_WEB_DIR: Path = _REPO_ROOT / "web"
_DEFAULT_DB: str = str(_REPO_ROOT / "data" / "cvi.db")


def count_repos(store: OperationalStore) -> int:
    """Number of promoted repo rows currently in the store."""
    row = store.connection.execute("SELECT COUNT(*) FROM repositories").fetchone()
    return int(row[0])


def _fixture_metrics() -> dict[tuple[str, str], tuple[int, int]]:
    """``(owner, name) -> (stars, forks)`` for every ``benchmark_data`` fixture."""
    return {(row["owner"], row["name"]): (int(row["stars"]), int(row["forks"]))
            for row in get_benchmark_catalog()}


def _snapshots_dir(db_path: str) -> Path:
    """Snapshot directory of the data dir ``db_path`` lives in."""
    return Path(db_path).resolve().parent / SNAPSHOTS_DIRNAME


def snapshot_identity(db_path: str) -> Optional[str]:
    """Newest snapshot id frozen beside ``db_path``, or ``None``.

    Snapshot directories are named ``<as_of><merkle prefix>``, so the sorted
    last one is the most recent freeze. A database with no freeze has no
    identity: nothing says which envelopes and constants produced its rows.
    """
    manifests = sorted(_snapshots_dir(db_path).glob(f"*/{MANIFEST_FILENAME}"))
    if not manifests:
        return None
    return str(json.loads(manifests[-1].read_text(encoding="utf-8"))["snapshot_id"])


def refusal_reasons(store: OperationalStore, db_path: str) -> list[str]:
    """Why ``db_path`` must not be served, one sentence per broken rule; [] to serve.

    Every sentence names a row, so the operator can point at the data instead
    of guessing which of the 1,000 rows caused the refusal.

    A fixture is recognised by its counters, not by its name: real
    repositories share names with the prominent repositories the fixtures
    imitate (``numpy/numpy`` is one of the 199 sample repos), and a real row
    carrying a fixture name must still serve.
    """
    rows = store.connection.execute(
        "SELECT id, owner, name, stars, forks FROM repositories ORDER BY id"
    ).fetchall()
    fixtures = _fixture_metrics()
    fabricated = [row for row in rows if fixtures.get((row[1], row[2])) == (row[3], row[4])]
    reasons: list[str] = []
    if fabricated:
        _id, owner, name, stars, forks = fabricated[0]
        reasons.append(
            f"{len(fabricated)} of {len(rows)} rows are cvi/benchmark_data.py fixtures: "
            f"row {_id} {owner}/{name} (stars={stars}, forks={forks}) is fabricated "
            "telemetry, which is a test fixture and never a ranking source")
    if snapshot_identity(db_path) is None:
        subject = f"row {rows[0][0]} {rows[0][1]}/{rows[0][2]}" if rows else db_path
        reasons.append(
            f"{subject} carries no snapshot identity: no {MANIFEST_FILENAME} under "
            f"{_snapshots_dir(db_path)} — freeze the data dir first with "
            f"`python -m cvi.snapshot freeze --data-dir "
            f"{Path(db_path).resolve().parent} --as-of <ISO-8601>`")
    return reasons


def create_serve_app(store: OperationalStore) -> FastAPI:
    """CVI API over ``store`` + the offline web UI mounted at ``/``.

    API routes are registered first (inside :func:`cvi.api.create_app`), so
    they win over the static mount, mirroring ``scripts.demo_app``.
    """
    app = create_app(store=store)
    app.mount("/", StaticFiles(directory=_WEB_DIR, html=True), name="web")
    return app


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="CVI production serve: verified SQLite + web UI (no seeding)"
    )
    parser.add_argument(
        "--db",
        default=_DEFAULT_DB,
        help=f"SQLite path written by promote (default: {_DEFAULT_DB}).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", 8000)),
        help="Port to serve on (default: $PORT or 8000).",
    )
    args = parser.parse_args(argv)

    if not Path(args.db).exists() and args.db != ":memory:":
        raise SystemExit(
            f"No database at {args.db!r}. Run scripts/run_all.py first "
            "(gather -> derive -> verify -> promote)."
        )
    store = OperationalStore(args.db)
    reasons = refusal_reasons(store, args.db)
    if reasons:
        raise SystemExit(f"CVI serve refused for {args.db!r}:\n  "
                         + "\n  ".join(reasons))
    snapshot = snapshot_identity(args.db)
    n = count_repos(store)
    app = create_serve_app(store)
    print(f"CVI serve: {n} verified repos from {args.db!r} (snapshot {snapshot})")
    if n == 0:
        print("  WARNING: store is empty — dashboard will show no repos.")
    print(f"CVI serve: http://127.0.0.1:{args.port}/ (API under /v1, /healthz)")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
