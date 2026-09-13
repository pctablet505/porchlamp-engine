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
replaced it with identity bound to the database's own content
(:func:`cvi.lakehouse.repositories_dump_hash`) but DELETED the fixture check
outright rather than keeping it as a second, independent guard -- so seeding
the synthetic catalog and then promoting a real sample ON TOP (``promote.py``
upserted but never deleted) produced a bound, self-consistent database that
served 995 fabricated rows alongside the real ones (round 3, Blocker-1). Two
independent checks now run, because they catch different things:

* :func:`_fixture_marker_rows` -- rename-proof, keyed on
  ``last_indexed_at == cvi.benchmark_data.BENCHMARK_NOW``, a stamp only
  ``cvi/benchmark_data.py``'s seeded catalog ever writes and
  ``scripts/promote.py`` never does (it always writes the scoring
  ``as_of``). This is what "what was ever written to this table" the
  content-hash check cannot see: it catches a fixture row regardless of
  when it arrived relative to a legitimate promotion, and survives renaming
  every owner (round 1's exact bypass) because it does not read ``owner``
  at all. ``scripts/promote.py`` (round 4) also promotes into a fresh temp
  file and only ``os.replace``s it onto the served path once every check
  passes (:func:`scripts.promote.promote_atomically`), so this ordering
  accident no longer occurs by construction either -- the two fixes are
  independent and both required.
* the content-hash identity (round 2, unchanged): ``scripts/promote.py``
  writes one ``snapshot_meta`` row per promotion carrying the frozen
  manifest's identity plus :func:`cvi.lakehouse.repositories_dump_hash`
  computed over the just-written ``repositories`` table. This refuses
  unless RECOMPUTING that hash over the database's CURRENT content still
  matches a stored row:

  * no ``snapshot_meta`` row at all (never promoted against a frozen
    snapshot);
  * the recomputed hash matches no stored row (the database was edited
    after promotion, by any statement).

Scope, stated plainly (`03` §5): both guards defend against ACCIDENTAL
substitution and unrecomputed edits -- the wrong database, a stale fixture,
an ordering mistake. An attacker with SQLite write access and this same code
can always recompute :func:`cvi.lakehouse.repositories_dump_hash` and
overwrite the stored value, or delete the fixture rows outright; that is not
this guard's threat model (see ``docs/v2/03-ANTI-MANIPULATION.md`` §5).

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
from cvi.benchmark_data import BENCHMARK_NOW
from cvi.lakehouse import OperationalStore, repositories_dump_hash
from cvi.serve.app import create_app as create_app_v2

_REPO_ROOT: Path = Path(__file__).resolve().parent.parent
_WEB_DIR: Path = _REPO_ROOT / "web"
#: `06` sections 6/6.1: the v2 front end (packet W4-5); does not exist in
#: every worktree yet (it lands with W4-5a) -- `cvi.serve.app.create_app`
#: mounts it with ``check_dir=False`` so a not-yet-landed directory never
#: crashes app construction, only requests for a page it cannot find.
_WEB_V2_DIR: Path = _REPO_ROOT / "web" / "v2"
_DEFAULT_DB: str = str(_REPO_ROOT / "data" / "cvi.db")


def count_repos(store: OperationalStore) -> int:
    """Number of promoted repo rows currently in the store."""
    row = store.connection.execute("SELECT COUNT(*) FROM repositories").fetchone()
    return int(row[0])


#: The ``last_indexed_at`` stamp only ``cvi/benchmark_data.py``'s seeded
#: catalog ever writes (``seed_benchmark_store``'s default ``now``);
#: ``scripts/promote.py`` always writes the scoring ``as_of`` instead, never
#: this literal value (round 3, Blocker-1).
_FIXTURE_MARKER = BENCHMARK_NOW.isoformat()


def _fixture_marker_rows(store: OperationalStore) -> list[tuple[int, str, str]]:
    """``(id, owner, name)`` for every row carrying the fixture clock.

    Rename-proof by construction: it reads ``last_indexed_at``, never
    ``owner``/``name``/``stars``/``forks``, so ``UPDATE repositories SET
    owner = owner || '-inc'`` (round 1's exact bypass of the old
    name/telemetry rule) does not change which rows this returns.
    """
    return store.connection.execute(
        "SELECT id, owner, name FROM repositories WHERE last_indexed_at = ? ORDER BY id",
        (_FIXTURE_MARKER,),
    ).fetchall()


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

    Two independent checks (round 3, Blocker-1, `03` §5): a fixture-clock
    row and a content-hash mismatch are different failures with different
    causes, so both are reported when both are true, instead of the second
    check masking the first.
    """
    reasons: list[str] = []
    fixtures = _fixture_marker_rows(store)
    if fixtures:
        total = count_repos(store)
        _id, owner, name = fixtures[0]
        reasons.append(
            f"{len(fixtures)} of {total} rows carry the cvi/benchmark_data.py "
            f"fixture clock (last_indexed_at={_FIXTURE_MARKER}): row {_id} "
            f"{owner}/{name} is fabricated telemetry, which is a test fixture "
            "and never a ranking source")
    rows = _snapshot_meta_rows(store)
    if not rows:
        reasons.append(
            f"{db_path} has no snapshot_meta row: promote against a frozen "
            "snapshot first (`python -m cvi.snapshot freeze --data-dir <dir> "
            "--as-of <ISO-8601>` then `scripts/promote.py --snapshot-id <id> "
            "--db <db> --data-dir <dir>`)")
    elif matching_snapshot_id(store, db_path) is None:
        reasons.append(
            f"{db_path}'s current content matches none of its {len(rows)} "
            "recorded snapshot_meta row(s) -- the database was modified "
            "after promotion, or copied beside a foreign snapshot")
    return reasons


def open_or_exit(db_path: str) -> OperationalStore:
    """Open ``db_path`` and enforce the served-DB guard (`03` §5,
    INT-W0-4 round 3): ``SystemExit`` with every :func:`refusal_reasons`
    sentence, one per line, if any apply.

    Factored out of :func:`main` (packet W4-4) so a second entrypoint --
    ``cvi.serve.app.create_app``, the v2 API -- refuses the exact same
    databases the exact same way instead of a second, drifting message
    format; this extraction changes no observable behaviour of
    :func:`main` itself (same store, same guard, same message, same
    control flow), so it needs no new test of its own here.
    """
    store = OperationalStore(db_path)
    reasons = refusal_reasons(store, db_path)
    if reasons:
        raise SystemExit(f"CVI serve refused for {db_path!r}:\n  " + "\n  ".join(reasons))
    return store


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
    store = open_or_exit(args.db)
    snapshot = matching_snapshot_id(store, args.db)
    n = count_repos(store)
    # Packet W4-4: serve the v2 API + v2 front end (v1 stays reachable,
    # unchanged, at /legacy -- `04` section 10's strangler migration).
    # `create_app_v2` re-runs the exact same guard above on `args.db`
    # itself (`cvi.serve.app._run_served_db_guard`) before it ever touches
    # the v2 store connection or mounts anything -- belt-and-suspenders,
    # not a behaviour change: a database refused here never reaches this
    # line at all.
    app = create_app_v2(args.db, web_dir=_WEB_V2_DIR)
    print(f"CVI serve: {n} verified repos from {args.db!r} (snapshot {snapshot})")
    if n == 0:
        print("  WARNING: store is empty — dashboard will show no repos.")
    print(f"CVI serve: http://127.0.0.1:{args.port}/ (API under /v1/cvi, legacy under /legacy, /healthz)")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
