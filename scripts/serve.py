"""Porchlamp production serving entrypoint: verified SQLite + the web front end.

This opens the ALREADY-PROMOTED operational store written by
``scripts/run_all.py`` / ``scripts/promote.py`` — the only rows are bundles
that passed ``scripts/verify.py`` — and serves the v2 API + v2 front end
(``porchlamp.serve.app.create_app``). No seeding happens here: an empty DB
serves an empty (but honest) dashboard.

The served-DB guard (``03`` section 5, F1) lives in
:mod:`porchlamp.serve.guard` — this module re-exports two of its v1 names
(``count_repos``, ``refusal_reasons``) because ``scripts/promote.py`` and
``tests/test_serve.py`` import them from here, so every caller of
``scripts.serve.<name>`` resolves the identical function, with the identical
behaviour. See that module's own docstring for the two-guard design.

``main()`` calls :func:`porchlamp.serve.guard.run_guard` — the SAME function
:func:`porchlamp.serve.app.create_app` calls — which raises ``SystemExit`` on a
genuine refusal and otherwise returns ``None``: the v2 API starts and serves
whatever v2 data the database has, even if that is none yet.

Run:
    .venv/bin/python scripts/serve.py                        # data/cvi.db, port 8000
    .venv/bin/python scripts/serve.py --db /path/porchlamp.db
    PORT=8765 .venv/bin/python scripts/serve.py              # port override

``--site <dir>`` instead previews an exported static site (``scripts/export_static.py --out``) the way GitHub
Pages serves it (:class:`StaticSiteHandler`), so every view's address and ``404.html`` behave as they will live:
    .venv/bin/python scripts/serve.py --site /tmp/site --port 8796
"""

from __future__ import annotations

import argparse
import functools
import http.server
import os
from http import HTTPStatus
from pathlib import Path
from typing import Optional

import uvicorn

from porchlamp.serve.app import create_app
from porchlamp.serve.guard import count_repos, refusal_reasons, run_guard

__all__ = [
    "count_repos",
    "refusal_reasons",
    "StaticSiteHandler",
    "main",
]

_REPO_ROOT: Path = Path(__file__).resolve().parent.parent
#: `06` sections 6/6.1: the v2 front end. `porchlamp.serve.app.create_app` mounts
#: it with ``check_dir=False`` so a missing directory never crashes app
#: construction, only requests for a page it cannot find.
_WEB_APP_DIR: Path = _REPO_ROOT / "web" / "app"
_DEFAULT_DB: str = str(_REPO_ROOT / "data" / "porchlamp.db")


class StaticSiteHandler(http.server.SimpleHTTPRequestHandler):
    """An exported site, answered the way GitHub Pages answers it: a file at its path, a directory's ``index.html``
    at the directory's address (a path without its closing slash is redirected there), and ``404.html`` with status
    404 for an address with no file, where the app shows a project's side panel or "page not found". A directory
    without ``index.html`` is an address with no file: Pages lists no directory. Nothing is cached, so a preview
    always shows the latest export."""

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format, *args) -> None:  # the base class's signature; a preview's requests are not worth logging
        pass

    def list_directory(self, path):
        self.send_error(HTTPStatus.NOT_FOUND)
        return None

    def send_error(self, code, message=None, explain=None) -> None:
        page = Path(self.directory) / "404.html"
        if code != HTTPStatus.NOT_FOUND or not page.is_file():
            super().send_error(code, message, explain)
            return
        body = page.read_bytes()
        self.send_response(HTTPStatus.NOT_FOUND)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


def serve_site(site: Path, port: int) -> None:
    """Preview an exported site on loopback (:class:`StaticSiteHandler`) until interrupted."""
    if not (site / "index.html").is_file():
        raise SystemExit(f"No exported site at {site!s} (it has no index.html). Run scripts/export_static.py first.")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(StaticSiteHandler, directory=str(site)))
    print(f"Porchlamp site preview: http://127.0.0.1:{port}/ ({site})")
    server.serve_forever()


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Porchlamp production serve: verified SQLite + web UI (no seeding)"
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
    parser.add_argument(
        "--site",
        default=None,
        help="An exported static site (scripts/export_static.py --out) to preview the way GitHub Pages serves it, "
        "instead of the API and the database.",
    )
    args = parser.parse_args(argv)

    if args.site is not None:
        serve_site(Path(args.site), args.port)
        return

    if not Path(args.db).exists() and args.db != ":memory:":
        raise SystemExit(
            f"No database at {args.db!r}. Run scripts/run_all.py first "
            "(gather -> derive -> verify -> promote)."
        )

    # run_guard() raises SystemExit on a genuine refusal.
    run_guard(args.db)

    # create_app re-runs the exact same guard on `args.db` itself
    # (`porchlamp.serve.app`'s own call to `porchlamp.serve.guard.run_guard`) before it
    # ever touches the v2 store connection or mounts anything --
    # belt-and-suspenders: a database refused here never reaches this line
    # at all.
    app = create_app(args.db, web_dir=_WEB_APP_DIR)
    print(f"Porchlamp serve: http://127.0.0.1:{args.port}/ (API under /v1/porchlamp)")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
