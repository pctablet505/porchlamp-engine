"""CVI production serving entrypoint: verified SQLite + the web front end.

Unlike ``scripts/demo_app.py`` (which seeds synthetic ``benchmark_data``
fixtures), this opens the ALREADY-PROMOTED operational store written by
``scripts/run_all.py`` / ``scripts/promote.py`` — the only rows are bundles
that passed ``scripts/verify.py`` — and serves the v2 API + v2 front end
(``cvi.serve.app.create_app``; v1 stays reachable, unchanged, at
``/legacy`` for as long as a database has a v1 shape to guard — ``04``
section 10's strangler migration). No seeding happens here: an empty DB
serves an empty (but honest) dashboard.

The served-DB guard (``03`` section 5, F1) lives in
:mod:`cvi.serve.guard` — this module re-exports its v1 names
(``count_repos``, ``matching_snapshot_id``, ``refusal_reasons``,
``open_or_exit``) so every caller of ``scripts.serve.<name>`` resolves the
identical function, with the identical behaviour. See that module's own
docstring for the two-guard design (v1 fixture-clock + content-hash; v2
tamper + fixture-clock-on-a-migrated-table).

``main()`` calls :func:`cvi.serve.guard.run_guard` — the SAME function
:func:`cvi.serve.app.create_app` calls — which raises ``SystemExit`` on a
genuine refusal (v1- or v2-shaped) and otherwise returns the v1 store to
report on, or ``None`` when the database has no v1 shape at all (a normal,
non-error state: nothing to report, no ``/legacy`` to mount, the v2 API
still starts and serves whatever v2 data the database has, even if that is
none yet).

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
from cvi.lakehouse import OperationalStore
from cvi.serve.app import create_app as create_app_v2
from cvi.serve.guard import (
    count_repos,
    matching_snapshot_id,
    open_or_exit,
    refusal_reasons,
    run_guard,
)

__all__ = [
    "count_repos",
    "matching_snapshot_id",
    "refusal_reasons",
    "open_or_exit",
    "create_serve_app",
    "main",
]

_REPO_ROOT: Path = Path(__file__).resolve().parent.parent
_WEB_DIR: Path = _REPO_ROOT / "web"
#: `06` sections 6/6.1: the v2 front end. `cvi.serve.app.create_app` mounts
#: it with ``check_dir=False`` so a missing directory never crashes app
#: construction, only requests for a page it cannot find.
_WEB_V2_DIR: Path = _REPO_ROOT / "web" / "v2"
_DEFAULT_DB: str = str(_REPO_ROOT / "data" / "cvi.db")


def create_serve_app(store: OperationalStore) -> FastAPI:
    """CVI **v1** API over ``store`` + the offline v1 web UI mounted at
    ``/``. This is the v1-only smoke path ``tests/test_serve.py`` exercises
    directly for guard/mounting coverage; :func:`main` serves the v2 app
    instead.

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

    # run_guard() runs the v2 guard (always) and the v1 guard
    # (only if the database has a v1 shape at all) in one call, raising
    # SystemExit on a genuine refusal from either. `store` is `None` for a
    # v2-only database -- a normal state, not an error: no v1 banner to
    # print, no `/legacy` to mount.
    legacy_store = run_guard(args.db)
    if legacy_store is not None:
        snapshot = matching_snapshot_id(legacy_store, args.db)
        n = count_repos(legacy_store)
        v1_banner = f"{n} verified repos from {args.db!r} (snapshot {snapshot})"
    else:
        v1_banner = "no v1 data at this path (a v2-only database, or nothing promoted yet)"

    # create_app_v2 re-runs the exact same guard on `args.db` itself
    # (`cvi.serve.app`'s own call to `cvi.serve.guard.run_guard`) before it
    # ever touches the v2 store connection or mounts anything --
    # belt-and-suspenders: a database refused here never reaches this line
    # at all.
    app = create_app_v2(args.db, web_dir=_WEB_V2_DIR)
    print(f"CVI serve: {v1_banner}")
    print(f"CVI serve: http://127.0.0.1:{args.port}/ (API under /v1/cvi, legacy under /legacy)")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
