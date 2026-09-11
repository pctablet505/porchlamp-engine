"""CVI production serving entrypoint: verified SQLite + the web front end.

Unlike ``scripts/demo_app.py`` (which seeds synthetic ``benchmark_data``
fixtures), this opens the ALREADY-PROMOTED operational store written by
``scripts/run_all.py`` / ``scripts/promote.py`` — the only rows are bundles
that passed ``scripts/verify.py`` — and serves the CVI REST API
(:func:`cvi.api.create_app`) plus the offline single-page UI in
``web/index.html`` mounted at ``/``. No seeding happens here: an empty DB
serves an empty (but honest) dashboard.

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

_REPO_ROOT: Path = Path(__file__).resolve().parent.parent
_WEB_DIR: Path = _REPO_ROOT / "web"
_DEFAULT_DB: str = str(_REPO_ROOT / "data" / "cvi.db")


def count_repos(store: OperationalStore) -> int:
    """Number of promoted repo rows currently in the store."""
    row = store.connection.execute("SELECT COUNT(*) FROM repositories").fetchone()
    return int(row[0])


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
    n = count_repos(store)
    app = create_serve_app(store)
    print(f"CVI serve: {n} verified repos from {args.db!r}")
    if n == 0:
        print("  WARNING: store is empty — dashboard will show no repos.")
    print(f"CVI serve: http://127.0.0.1:{args.port}/ (API under /v1, /healthz)")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
