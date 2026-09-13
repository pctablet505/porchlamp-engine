"""The served-DB guard (`03-ANTI-MANIPULATION.md` §5), for both the v1
shape (`cvi.lakehouse.OperationalStore`) and the v2 shape (`cvi.store.*`).

W4-4 REWORK (Blocker B-4; Warning W-5): previously `cvi/serve/app.py`
reached BACKWARDS into `scripts.serve` for the v1 guard (`from
scripts.serve import open_or_exit`) -- a package importing a script, via a
`scripts` namespace package with no `__init__.py`, invisible to
`tests/core/test_import_graph.py` because that test had no rule for
`cvi/serve` at all (it broke only a non-editable install, where the repo
root is off `sys.path`: `ModuleNotFoundError: No module named 'scripts'`).
Both the v1 guard's reusable logic AND the new v2 guard now live HERE;
`scripts/serve.py` imports FROM this module (the correct direction --
`scripts/` may import `cvi.*`, `cvi/serve` must never import `scripts.*`,
enforced by the `cvi/serve` edge rule added to `tests/core/
test_import_graph.py` in the same change) and re-exports the v1 names so
`tests/test_serve.py` (`from scripts import serve; serve.refusal_reasons`)
keeps resolving them unchanged.

Two independent guards live here, because a database can fail either, both
or neither:

* **The v1 guard** (`refusal_reasons`/`matching_snapshot_id`/
  `count_repos`/`open_or_exit`, moved verbatim -- same logic, same
  messages, same two independent checks its own docstrings already
  explain): a fixture-clock row (`last_indexed_at` matching
  `_FIXTURE_CLOCK_SENTINEL`) or a content-hash mismatch against
  `snapshot_meta`, over `cvi.lakehouse.OperationalStore`.

  `_FIXTURE_CLOCK_SENTINEL` is a LITERAL, frozen here rather than imported
  from `tests.fixtures.synthetic_catalog.BENCHMARK_NOW` (W4-1 moved
  `cvi/benchmark_data.py` there per the `.agents/W4-1/inventory.md`
  recommendation, §5: "never importable from `cvi/`", since this guard's
  whole security property is a stamp only the FIXTURE seeder ever writes
  -- a production module importing the fixture module, in either
  direction, is exactly backwards). `tests/serve/
  test_guard.py::test_fixture_clock_sentinel_matches_synthetic_catalog`
  asserts the two values stay byte-identical so they can never silently
  drift apart.

* **The v2 guard** (`v2_refusal_reasons`/`served_snapshot_id`, new,
  Blocker B-4): over the v2 `snapshots`/`repositories` tables `cvi.store.
  queries`/`cvi.store.reader` actually read. Recomputes `cvi.store.writer.
  snapshot_dump_hash` for the ONE snapshot `cvi.serve.app` would actually
  serve (`served_snapshot_id`: most recently PROMOTED, else most recently
  created) and refuses if it no longer matches the stored
  `canonical_dump_hash` -- `03` §5's identity check, generalised from v1's
  single-table `repositories_dump_hash` to v2's per-snapshot,
  multi-table (`repositories`/`params`/`populations`) hash. Only a
  FINALISED snapshot (`canonical_dump_hash IS NOT NULL`) is checked this
  way -- an unfinalised one is a normal, temporary state `cvi.serve.app.
  _require_snapshot` already 503s on per request (Warning W-6), not a
  startup refusal. Also checks the SAME fixture-clock stamp against
  whichever table currently carries the v1 shape: `repositories` on an
  unmigrated v1-only database opened directly, or `legacy_repositories`
  on one `04-ARCHITECTURE.md` §10's migration has already renamed it to
  (the migration comment in `cvi/store/migrations/0001_init_v2.sql`
  documents the rename) -- a v1 fixture catalog does not stop being
  fabricated telemetry just because it was migrated forward.

Both guards report ALL of their own reasons before refusing (never masks
one behind another, matching the v1 guard's own existing convention).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from cvi.lakehouse import OperationalStore, repositories_dump_hash
from cvi.store.writer import snapshot_dump_hash

__all__ = [
    "count_repos",
    "matching_snapshot_id",
    "refusal_reasons",
    "open_or_exit",
    "served_snapshot_id",
    "v2_refusal_reasons",
    "run_guard",
]

#: `tests.fixtures.synthetic_catalog.BENCHMARK_NOW`'s value, frozen as a literal here --
#: see the module docstring for why this is not an import.
_FIXTURE_CLOCK_SENTINEL: datetime = datetime(2026, 9, 11, 12, 0, 0)
_FIXTURE_MARKER: str = _FIXTURE_CLOCK_SENTINEL.isoformat()


def _refusal_message(db_path: str, reasons: List[str]) -> str:
    """The one message format both guards raise -- `main()`'s and
    `create_app`'s callers see the identical text regardless of which
    guard (or both) produced the reasons."""
    return f"CVI serve refused for {db_path!r}:\n  " + "\n  ".join(reasons)


# --------------------------------------------------------------------------- #
# v1 guard (moved verbatim from `scripts/serve.py`; INT-W0-4 round 3,
# `03` §5) -- unchanged behaviour; `tests/test_serve.py` still imports
# these same names back out of `scripts.serve`, which now re-exports them.
# --------------------------------------------------------------------------- #


def count_repos(store: OperationalStore) -> int:
    """Number of promoted repo rows currently in the store."""
    row = store.connection.execute("SELECT COUNT(*) FROM repositories").fetchone()
    return int(row[0])


def _fixture_marker_rows(store: OperationalStore) -> list:
    """``(id, owner, name)`` for every row carrying the fixture clock.

    Rename-proof by construction: it reads ``last_indexed_at``, never
    ``owner``/``name``/``stars``/``forks``, so ``UPDATE repositories SET
    owner = owner || '-inc'`` does not change which rows this returns.
    """
    return store.connection.execute(
        "SELECT id, owner, name FROM repositories WHERE last_indexed_at = ? ORDER BY id",
        (_FIXTURE_MARKER,),
    ).fetchall()


def _snapshot_meta_rows(store: OperationalStore) -> list:
    """``(snapshot_id, canonical_dump_hash)`` for every promotion on record."""
    return store.connection.execute(
        "SELECT snapshot_id, canonical_dump_hash FROM snapshot_meta"
    ).fetchall()


def matching_snapshot_id(store: OperationalStore, db_path: str) -> Optional[str]:
    """The ``snapshot_meta`` row whose hash still matches ``db_path``, or ``None``.

    Recomputes :func:`cvi.lakehouse.repositories_dump_hash` over
    ``db_path``'s CURRENT content and looks for a stored row that still
    agrees. A database edited after promotion recomputes to a different
    hash, so it matches nothing.
    """
    current = repositories_dump_hash(db_path)
    for snapshot_id, stored_hash in _snapshot_meta_rows(store):
        if stored_hash == current:
            return str(snapshot_id)
    return None


def refusal_reasons(store: OperationalStore, db_path: str) -> List[str]:
    """Why ``db_path`` must not be served (v1 shape), one sentence per
    broken rule; ``[]`` to serve. Two independent checks: a fixture-clock
    row and a content-hash mismatch are different failures with different
    causes, so both are reported when both are true.
    """
    reasons: List[str] = []
    fixtures = _fixture_marker_rows(store)
    if fixtures:
        total = count_repos(store)
        _id, owner, name = fixtures[0]
        reasons.append(
            f"{len(fixtures)} of {total} rows carry the tests/fixtures/synthetic_catalog.py "
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
    """Open ``db_path`` as a v1 :class:`OperationalStore` and enforce the
    guard: ``SystemExit`` with every :func:`refusal_reasons` sentence, one
    per line, if any apply."""
    store = OperationalStore(db_path)
    reasons = refusal_reasons(store, db_path)
    if reasons:
        raise SystemExit(_refusal_message(db_path, reasons))
    return store


# --------------------------------------------------------------------------- #
# v2 guard (new, Blocker B-4). Uses a PLAIN ``sqlite3.connect`` (not
# ``cvi.store.schema.connect``, and never ``OperationalStore``, which
# cannot open a v2-shaped database at all -- see `cvi/serve/app.py`'s
# `_run_served_db_guard` docstring): this guard must work on exactly the
# database `cvi.serve.app` reads, whatever shape it happens to be in.
# --------------------------------------------------------------------------- #


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall())


def served_snapshot_id(conn: sqlite3.Connection) -> Optional[str]:
    """The snapshot `cvi.serve.app` would serve: the most recently
    PROMOTED one (`promoted_at` set by `cvi.store.writer.
    finalize_snapshot_hash`), falling back to the most recently created.
    `None` if `snapshots` does not exist or is empty (a v1-only database,
    or a v2 store nothing has ever written a snapshot into yet)."""
    if not _table_exists(conn, "snapshots"):
        return None
    row = conn.execute(
        "SELECT id FROM snapshots WHERE promoted_at IS NOT NULL ORDER BY promoted_at DESC, id DESC LIMIT 1"
    ).fetchone()
    if row is not None:
        return row[0]
    row = conn.execute("SELECT id FROM snapshots ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
    return row[0] if row is not None else None


def _v1_fixture_reasons(conn: sqlite3.Connection) -> List[str]:
    """The fixture-clock check against whichever table currently carries
    the v1 shape: `repositories` (an unmigrated v1-only database opened
    directly) or `legacy_repositories` (one `04` §10's migration has
    already renamed a v1 `repositories` table to, preserving its
    columns -- `cvi/store/migrations/0001_init_v2.sql`'s own comment). A
    v2-only database has neither a `last_indexed_at` column on
    `repositories` (v2's own schema has no such column at all) nor a
    `legacy_repositories` table, so this is a no-op for one."""
    reasons: List[str] = []
    for table in ("repositories", "legacy_repositories"):
        if not _table_exists(conn, table) or not _column_exists(conn, table, "last_indexed_at"):
            continue
        rows = conn.execute(
            f'SELECT id, owner, name FROM "{table}" WHERE last_indexed_at = ? ORDER BY id', (_FIXTURE_MARKER,)
        ).fetchall()
        if not rows:
            continue
        total = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        row_id, owner, name = rows[0]
        reasons.append(
            f"{len(rows)} of {total} rows in {table!r} carry the tests/fixtures/synthetic_catalog.py "
            f"fixture clock (last_indexed_at={_FIXTURE_MARKER}): row {row_id} "
            f"{owner}/{name} is fabricated telemetry, which is a test fixture "
            "and never a ranking source")
    return reasons


def _v1_hash_tamper_reasons(conn: sqlite3.Connection, db_path: str) -> List[str]:
    """`repositories_dump_hash` tamper check against `snapshot_meta`, for
    a v1-only database opened directly by `cvi.serve.app` (never mounted
    at `/legacy` in that case -- see `_run_served_db_guard` -- but the v2
    API's own guard must still refuse a tampered legacy row set rather
    than silently ignore it because `/legacy` happens not to be mounted).
    A no-op when `snapshot_meta` does not exist (a v2-only database, or
    one already renamed to `legacy_repositories` -- W4-1's inventory notes
    `snapshot_meta` is v1-specific and is not part of the rename)."""
    if not _table_exists(conn, "snapshot_meta"):
        return []
    rows = conn.execute("SELECT snapshot_id, canonical_dump_hash FROM snapshot_meta").fetchall()
    if not rows:
        return []
    current = repositories_dump_hash(db_path)
    if any(stored_hash == current for _sid, stored_hash in rows):
        return []
    return [
        f"{db_path}'s current content matches none of its {len(rows)} "
        "recorded snapshot_meta row(s) -- the database was modified "
        "after promotion, or copied beside a foreign snapshot"
    ]


def v2_refusal_reasons(db_path: str) -> List[str]:
    """Why the v2 API must not serve ``db_path``, one sentence per broken
    rule; ``[]`` to serve. Runs against a fresh, short-lived connection
    (never the long-lived one `cvi.serve.app` opens for request serving)
    so this check can run standalone, from `scripts/serve.py`'s `main`,
    before `create_app` ever builds anything."""
    reasons: List[str] = []
    conn = sqlite3.connect(db_path)
    try:
        reasons.extend(_v1_fixture_reasons(conn))
        reasons.extend(_v1_hash_tamper_reasons(conn, db_path))

        snapshot_id = served_snapshot_id(conn)
        if snapshot_id is not None:
            stored_hash = conn.execute(
                "SELECT canonical_dump_hash FROM snapshots WHERE id = ?", (snapshot_id,)
            ).fetchone()[0]
            if stored_hash is not None:
                recomputed = snapshot_dump_hash(Path(db_path), snapshot_id)
                if recomputed != stored_hash:
                    reasons.append(
                        f"snapshot {snapshot_id!r}'s current content (hash {recomputed}) does not "
                        f"match its recorded canonical_dump_hash ({stored_hash}) -- the database "
                        "was modified after promotion"
                    )
    finally:
        conn.close()
    return reasons


def run_guard(db_path: str) -> Optional[OperationalStore]:
    """The one function both `scripts/serve.py`'s `main` and `cvi.serve.
    app.create_app` call (Blocker B-1/B-4): runs the v2 guard first
    (raises `SystemExit` on any v2-guard reason -- a tampered or
    fixture-marked database is refused regardless of whether a v1 shape
    is even present to mount at `/legacy`), then the v1 guard ONLY IF the
    database has a v1 shape at all (`OperationalStore.__init__` succeeds;
    a `sqlite3.OperationalError` means no v1 shape -- nothing to refuse
    on that front, nothing to mount either, `None` is the correct,
    non-error return). A genuine v1-shaped refusal still raises exactly
    the message :func:`open_or_exit` always produced.
    """
    v2_reasons = v2_refusal_reasons(db_path)
    if v2_reasons:
        raise SystemExit(_refusal_message(db_path, v2_reasons))
    try:
        return open_or_exit(db_path)
    except sqlite3.OperationalError:
        return None
