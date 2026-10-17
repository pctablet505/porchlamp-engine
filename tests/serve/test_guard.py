"""Acceptance tests for `porchlamp/serve/guard.py`.

The v1 guard's OWN behaviour (fixture-clock, content-hash, message format)
is already pinned by `tests/test_serve.py` through `scripts.serve`'s
re-exports -- this file adds only what moved/is new: the frozen fixture-
clock sentinel never drifting from
`tests.fixtures.synthetic_catalog.BENCHMARK_NOW`, and the v2 guard (Blocker B-4) itself.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from porchlamp.serve import guard
from porchlamp.store.writer import finalize_snapshot_hash

from serve.helpers import SNAPSHOT_ID, build_snapshot, fresh_db


def test_fixture_clock_sentinel_matches_synthetic_catalog():
    """The guard freezes
    its own literal rather than importing
    `tests.fixtures.synthetic_catalog.BENCHMARK_NOW` across the
    production/test boundary -- this test is the only thing standing
    between that literal and silent drift."""
    from tests.fixtures.synthetic_catalog import BENCHMARK_NOW

    assert guard._FIXTURE_CLOCK_SENTINEL == BENCHMARK_NOW
    assert guard._FIXTURE_MARKER == BENCHMARK_NOW.isoformat()


def _build_and_finalize(db_path: Path, *, n_rated: int = 5) -> sqlite3.Connection:
    conn = fresh_db(db_path)
    build_snapshot(conn, db_path, n_rated=n_rated)
    finalize_snapshot_hash(conn, db_path, SNAPSHOT_ID, promoted_at="2026-09-13T01:00:00Z")
    return conn


def test_v2_refusal_reasons_empty_for_a_clean_finalised_store(tmp_path):
    db_path = tmp_path / "db.sqlite"
    conn = _build_and_finalize(db_path)
    conn.close()
    assert guard.v2_refusal_reasons(str(db_path)) == []


def test_v2_refusal_reasons_refuses_a_tampered_finalised_snapshot(tmp_path):
    """Blocker B-4's own reproduction: a finalised v2 store edited AFTER
    `finalize_snapshot_hash` (any statement -- here, nudging one row's
    `porchlamp`) must be refused: the recomputed `snapshot_dump_hash` no longer
    matches the stored `canonical_dump_hash`."""
    db_path = tmp_path / "db.sqlite"
    conn = _build_and_finalize(db_path)
    conn.close()

    tamper = sqlite3.connect(db_path)
    tamper.execute("UPDATE repositories SET porchlamp = 100.0 WHERE snapshot_id = ?", (SNAPSHOT_ID,))
    tamper.commit()
    tamper.close()

    reasons = guard.v2_refusal_reasons(str(db_path))
    assert len(reasons) == 1
    assert SNAPSHOT_ID in reasons[0]
    assert "does not" in reasons[0] and "match" in reasons[0]


def test_v2_refusal_reasons_tolerates_an_unfinalised_snapshot(tmp_path):
    """A snapshot with no `canonical_dump_hash` yet is a NORMAL, temporary
    state (Warning W-6: `porchlamp.serve.app._require_snapshot` 503s it per
    request) -- the STARTUP guard has nothing to compare and must not
    refuse it."""
    db_path = tmp_path / "db.sqlite"
    conn = fresh_db(db_path)
    build_snapshot(conn, db_path, n_rated=5)
    conn.close()  # never finalized -- canonical_dump_hash stays NULL
    assert guard.v2_refusal_reasons(str(db_path)) == []


def test_v2_refusal_reasons_refuses_a_fixture_marked_legacy_table(tmp_path):
    """The fixture-clock check must also fire against `legacy_repositories`
    (the name `04` section 10's migration renames a v1 `repositories`
    table to) -- a v1 fixture catalog does not stop being fabricated
    telemetry just because it was migrated forward."""
    db_path = tmp_path / "db.sqlite"
    conn = _build_and_finalize(db_path)
    conn.execute(
        "CREATE TABLE legacy_repositories (id INTEGER PRIMARY KEY, owner TEXT, name TEXT, last_indexed_at TEXT)"
    )
    conn.execute(
        "INSERT INTO legacy_repositories (owner, name, last_indexed_at) VALUES (?, ?, ?)",
        ("fixture-org", "fixture-repo", guard._FIXTURE_MARKER),
    )
    conn.commit()
    conn.close()

    reasons = guard.v2_refusal_reasons(str(db_path))
    assert len(reasons) == 1
    assert "legacy_repositories" in reasons[0]
    assert "fixture-org/fixture-repo" in reasons[0]


def test_v2_refusal_reasons_a_v2_only_database_has_no_v1_fixture_columns(tmp_path):
    """A fresh v2 schema's `repositories` table has no `last_indexed_at`
    column at all -- the fixture-clock check must be a clean no-op, never
    an `OperationalError`."""
    db_path = tmp_path / "db.sqlite"
    conn = fresh_db(db_path)
    build_snapshot(conn, db_path, n_rated=5)
    conn.close()
    assert guard.v2_refusal_reasons(str(db_path)) == []


def test_served_snapshot_id_prefers_the_promoted_one(tmp_path):
    db_path = tmp_path / "db.sqlite"
    conn = fresh_db(db_path)
    build_snapshot(conn, db_path, n_rated=5)
    conn.close()
    raw = sqlite3.connect(db_path)
    assert guard.served_snapshot_id(raw) == SNAPSHOT_ID  # falls back to most-recently-created
    raw.close()

    conn = sqlite3.connect(db_path)
    finalize_snapshot_hash(conn, db_path, SNAPSHOT_ID, promoted_at="2026-09-13T01:00:00Z")
    conn.close()
    raw = sqlite3.connect(db_path)
    assert guard.served_snapshot_id(raw) == SNAPSHOT_ID
    raw.close()


def test_served_snapshot_id_none_when_no_snapshots_table(tmp_path):
    db_path = tmp_path / "db.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE unrelated (x INTEGER)")
    conn.commit()
    conn.close()
    raw = sqlite3.connect(db_path)
    assert guard.served_snapshot_id(raw) is None
    raw.close()


def test_run_guard_returns_none_for_a_v2_only_database(tmp_path):
    """No v1 shape at all -- nothing to refuse on that front, and
    definitely not a `SystemExit`."""
    db_path = tmp_path / "db.sqlite"
    conn = _build_and_finalize(db_path)
    conn.close()
    assert guard.run_guard(str(db_path)) is None


def test_run_guard_raises_systemexit_for_a_tampered_v2_database(tmp_path):
    db_path = tmp_path / "db.sqlite"
    conn = _build_and_finalize(db_path)
    conn.close()
    tamper = sqlite3.connect(db_path)
    tamper.execute("UPDATE repositories SET porchlamp = 100.0 WHERE snapshot_id = ?", (SNAPSHOT_ID,))
    tamper.commit()
    tamper.close()
    with pytest.raises(SystemExit) as excinfo:
        guard.run_guard(str(db_path))
    assert SNAPSHOT_ID in str(excinfo.value)


def test_run_guard_leaves_unpromoted_lakehouse_rows_to_the_promote_check(tmp_path):
    """The server serves no `porchlamp.lakehouse.OperationalStore` rows (the
    first-generation API that read them was removed), so a database whose
    lakehouse table was never promoted -- a real, non-fixture-marked,
    non-tampered row but NO `snapshot_meta` row -- is not refused at serve
    time. `refusal_reasons`, which `scripts/promote.py` runs before its swap,
    still names the missing row."""
    from porchlamp.lakehouse import apply_raw_metrics
    from porchlamp.lakehouse import OperationalStore
    from porchlamp.models import PorchlampCompositeResult, Ecosystem, PillarScores, Quadrant, Tier

    db_path = tmp_path / "porchlamp.db"
    store = OperationalStore(str(db_path))
    result = PorchlampCompositeResult(
        owner="numpy", name="numpy", ecosystem=Ecosystem.PYPI, overall_porchlamp=70.31,
        rank_tier=Tier.A, matrix_quadrant=Quadrant.SWEET_SPOT, leverage_score=72.0,
        friction_score=30.0, is_stage0_disqualified=False, disqualification_reason=None,
    )
    store.upsert_composite(result, pillar_scores=PillarScores(70, 80, 60, 65, 55))
    apply_raw_metrics(store, "numpy", "numpy", stars=32721, forks=12769)
    store.close()

    assert guard.run_guard(str(db_path)) is None
    store = OperationalStore(str(db_path))
    try:
        reasons = guard.refusal_reasons(store, str(db_path))
    finally:
        store.close()
    assert len(reasons) == 1 and "no snapshot_meta row" in reasons[0]
