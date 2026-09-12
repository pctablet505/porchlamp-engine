"""CVI production serving entrypoint: verified SQLite + the web front end.

Unlike ``scripts/demo_app.py`` (which seeds synthetic ``benchmark_data``
fixtures), this opens the ALREADY-PROMOTED operational store written by
``scripts/run_all.py`` / ``scripts/promote.py`` — the only rows are bundles
that passed ``scripts/verify.py`` — and serves the CVI REST API
(:func:`cvi.api.create_app`) plus the offline single-page UI in
``web/index.html`` mounted at ``/``. No seeding happens here: an empty DB
serves an empty (but honest) dashboard.

The guard of packet W0-4 (`03` §5, F1) runs before the server starts. Round 1
keyed it on ``(owner, name) == (stars, forks)`` equality against the
``cvi/benchmark_data.py`` fixture catalog, which a single ``UPDATE
repositories SET owner = owner || '-inc'`` defeated (Blocker-3, round 2): the
whole synthetic catalog then had no matching name and served anyway. Round 2
binds identity to the database's own content instead: ``scripts/promote.py``
writes one ``snapshot_meta`` row per promotion carrying the frozen manifest's
identity plus a canonical dump hash
(:func:`cvi.lakehouse.repositories_dump_hash`) computed over the
just-written ``repositories`` table. This refuses unless RECOMPUTING that hash
over the database's CURRENT content still matches a stored row:

* no ``snapshot_meta`` row at all (never promoted against a frozen snapshot —
  this is what refuses the synthetic ``cvi/benchmark_data.py`` catalog: it was
  never promoted, so the table is empty);
* the recomputed hash matches no stored row (the database was edited after
  promotion, by any statement, not only ones a name-based check happens to
  catch).

Run:
    .venv/bin/python scripts/serve.py                        # data/cvi.db, port 8000
    .venv/bin/python scripts/serve.py --db /path/cvi.db
    PORT=8765 .venv/bin/python scripts/serve.py              # port override
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from cvi.api import create_app
from cvi.lakehouse import OperationalStore, repositories_dump_hash

_REPO_ROOT: Path = Path(__file__).resolve().parent.parent
_WEB_DIR: Path = _REPO_ROOT / "web"
_DEFAULT_DB: str = str(_REPO_ROOT / "data" / "cvi.db")


def count_repos(store: OperationalStore) -> int:
    """Number of promoted repo rows currently in the store."""
    row = store.connection.execute("SELECT COUNT(*) FROM repositories").fetchone()
    return int(row[0])


def _snapshot_meta_rows(store: OperationalStore) -> list[tuple[str, str]]:
    """``(snapshot_id, canonical_dump_hash)`` for every promotion on record."""
    return store.connection.execute(
        "SELECT snapshot_id, canonical_dump_hash FROM snapshot_meta"
    ).fetchall()


def matching_snapshot_id(store: OperationalStore, db_path: str) -> Optional[str]:
    """The ``snapshot_meta`` row whose hash still matches ``db_path``, or ``None``.

    Recomputes :func:`cvi.lakehouse.repositories_dump_hash` over
    ``db_path``'s CURRENT content — the same function ``scripts/promote.py``
    uses to write the stored value — and looks for a stored row that still
    agrees. A database edited after promotion (any statement: rename an
    owner, nudge a counter, seed a wholly synthetic catalog with no
    promotion at all) recomputes to a different hash, so it matches nothing.
    """
    current = repositories_dump_hash(db_path)
    for snapshot_id, stored_hash in _snapshot_meta_rows(store):
        if stored_hash == current:
            return str(snapshot_id)
    return None


def refusal_reasons(store: OperationalStore, db_path: str) -> list[str]:
    """Why ``db_path`` must not be served, one sentence per broken rule; [] to serve.

    Round 2 (Blocker-3, `03` §5): identity is bound to the database's own
    content via :func:`matching_snapshot_id`, not to a per-row heuristic
    (round 1's owner/telemetry equality) that one ``UPDATE`` statement
    defeated — renaming every owner, or nudging every star count by one, left
    the round-1 guard with zero reasons while all 1,000 fabricated rows still
    served.
    """
    rows = _snapshot_meta_rows(store)
    if not rows:
        return [f"{db_path} has no snapshot_meta row: promote against a frozen "
                "snapshot first (`python -m cvi.snapshot freeze --data-dir <dir> "
                "--as-of <ISO-8601>` then `scripts/promote.py --snapshot-id <id> "
                "--db <db> --data-dir <dir>`)"]
    if matching_snapshot_id(store, db_path) is None:
        return [f"{db_path}'s current content matches none of its {len(rows)} "
                "recorded snapshot_meta row(s) -- the database was modified "
                "after promotion, or copied beside a foreign snapshot"]
    return []


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
    snapshot = matching_snapshot_id(store, args.db)
    n = count_repos(store)
    app = create_serve_app(store)
    print(f"CVI serve: {n} verified repos from {args.db!r} (snapshot {snapshot})")
    if n == 0:
        print("  WARNING: store is empty — dashboard will show no repos.")
    print(f"CVI serve: http://127.0.0.1:{args.port}/ (API under /v1, /healthz)")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
