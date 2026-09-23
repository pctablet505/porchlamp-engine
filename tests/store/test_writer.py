"""Tests for ``porchlamp.store.writer``: writing snapshots, repositories, parameters, and populations."""
from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from porchlamp.core.models import ParamScore, PersonaComposite, PRWindow, PRWindowEvent
from porchlamp.snapshot.populations import Population, PopulationSet
from porchlamp.store import schema
from porchlamp.store.migrations import apply_migrations
from porchlamp.store.writer import (
    RepoWrite,
    _normalize_utc_z,
    finalize_snapshot_hash,
    snapshot_dump_hash,
    write_records,
    write_snapshot,
)

from .helpers import (
    make_bundle,
    make_gated_record,
    make_measured,
    make_rated_record,
    make_repo_write,
    make_unrated_record,
)


def _fresh_db(path: Path) -> sqlite3.Connection:
    conn = schema.connect(path)  # PRAGMA foreign_keys=ON
    apply_migrations(conn, applied_at="2026-09-13T00:00:00Z")
    return conn


def _write_one_snapshot(conn, snapshot_id: str = "snap-1") -> None:
    write_snapshot(
        conn, id=snapshot_id, as_of="2026-09-13", benchmark_version="2.0.0-dev",
        constants_hash="c" * 64, code_commit="deadbeef", manifest_root="root" * 8,
        created_at="2026-09-13T00:00:00Z",
    )


# --------------------------------------------------------------------- #
# write_snapshot: conflict detection
# --------------------------------------------------------------------- #

def test_write_snapshot_is_idempotent_on_identical_definition(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    _write_one_snapshot(conn)  # identical definition -> silent no-op
    count = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    assert count == 1


def test_write_snapshot_raises_on_a_conflicting_definition(tmp_path) -> None:
    """A second call for the same id with a DIFFERENT identity field
    must raise, not silently keep the first definition."""
    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    with pytest.raises(ValueError, match="different definition"):
        write_snapshot(
            conn, id="snap-1", as_of="2099-01-01", benchmark_version="9.9.9",
            constants_hash="f" * 64, code_commit="deadbeef", manifest_root="OTHERROOT",
            created_at="2026-09-13T00:00:00Z",
        )
    row = conn.execute(
        "SELECT as_of, benchmark_version, constants_hash, manifest_root FROM snapshots WHERE id='snap-1'"
    ).fetchone()
    assert row == ("2026-09-13", "2.0.0-dev", "c" * 64, "root" * 8)  # untouched


def test_write_snapshot_never_clobbers_a_finalized_hash(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    finalize_snapshot_hash(conn, tmp_path / "db.sqlite", "snap-1", promoted_at="2026-09-13T01:00:00Z")
    _write_one_snapshot(conn)  # same identity fields -> no-op, hash/promoted_at untouched
    row = conn.execute("SELECT canonical_dump_hash, promoted_at FROM snapshots WHERE id='snap-1'").fetchone()
    assert row[0] is not None
    assert row[1] == "2026-09-13T01:00:00Z"


# --------------------------------------------------------------------- #
# write_records: round trip, gating
# --------------------------------------------------------------------- #

def test_write_records_round_trips_repo_params_and_populations(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)

    param_scores = {
        "ttfhr_hours": ParamScore(value=88.0, score=88.0, measured=True, reason=None, n=42, window_start=None, window_end=None, method="primary", source="test", fetched_at="2026-09-01T00:00:00+00:00"),
        "dependents_deduped": ParamScore(value=None, score=None, measured=False, reason="insufficient_n", n=3, window_start=None, window_end=None, method=None, source=None, fetched_at=None),
    }
    record = make_rated_record("snap-1", params=param_scores)
    item = make_repo_write(
        record,
        inputs={
            "ttfhr_hours": make_measured(4.5, n=42),
            "dependents_deduped": make_measured(3, n=3),
        },
    )
    counts = write_records(conn, "snap-1", [item], populations=None)
    assert counts == (1, 2, 0)

    repo_row = conn.execute(
        "SELECT owner, name, ecosystem, porchlamp, coverage, rated FROM repositories WHERE snapshot_id='snap-1'"
    ).fetchone()
    assert repo_row == ("acme", "demo", "python", 82.5, 0.9, 1)

    param_rows = conn.execute(
        "SELECT param_id, score, measured, n FROM params ORDER BY param_id"
    ).fetchall()
    assert param_rows == [
        ("dependents_deduped", None, 0, 3),
        ("ttfhr_hours", 88.0, 1, 42),
    ]


def test_write_records_nulls_source_for_a_measured_input_with_no_population(tmp_path) -> None:
    """When ``reason="no_population"``, ``ParamScore.measured`` is False.
    The writer ensures provenance fields (source, source_hash, fetched_at, method)
    are stored as NULL to satisfy the EvidenceRow contract."""
    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)

    param_scores = {
        "ttfhr_hours": ParamScore(
            value=None, score=None, measured=False, reason="no_population", n=0,
            window_start=None, window_end=None, method=None, source=None, fetched_at=None,
        ),
    }
    record = make_rated_record("snap-1", params=param_scores)
    item = make_repo_write(record, inputs={"ttfhr_hours": make_measured(4.5, n=42)})
    write_records(conn, "snap-1", [item], populations=None)

    row = conn.execute(
        "SELECT measured, reason, raw_value, source, source_hash, fetched_at, method "
        "FROM params WHERE param_id = 'ttfhr_hours'"
    ).fetchone()
    measured, reason, raw_value, source, source_hash, fetched_at, method = row
    assert measured == 0
    assert reason == "no_population"
    # The input-stage value survives as informational context (the contract
    # does not restrict it), but every measured-only provenance field is
    # NULL, matching what the row's own `measured=False` promises.
    assert raw_value == "4.5"
    assert source is None
    assert source_hash is None
    assert fetched_at is None
    assert method is None


def test_param_row_rejects_an_absolute_source(tmp_path) -> None:
    """The store refuses to write absolute source paths, ensuring only relative
    paths are persisted as evidence."""
    from porchlamp.store.writer import _param_row

    param_scores = {
        "ttfhr_hours": ParamScore(
            value=4.5, score=88.0, measured=True, reason=None, n=42,
            window_start=None, window_end=None, method="primary",
            source="raw/pr_window/acme__demo.json", fetched_at="2026-09-01T00:00:00Z",
        ),
    }
    record = make_rated_record("snap-1", params=param_scores)
    corrupted = make_measured(4.5, n=42)
    object.__setattr__(corrupted, "source", "/home/someone/porchlamp-data/raw/pr_window/acme__demo.json")
    item = make_repo_write(record, inputs={"ttfhr_hours": corrupted})
    with pytest.raises(ValueError, match="relative to the data root"):
        _param_row(1, "ttfhr_hours", item)


def test_write_records_writes_no_params_for_a_gated_repo(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    record = make_gated_record("snap-1", gate_reason="archived")
    item = make_repo_write(record)
    counts = write_records(conn, "snap-1", [item])
    assert counts == (1, 0, 0)
    eligible, rated = conn.execute(
        "SELECT eligible, rated FROM repositories WHERE owner='acme' AND name='old'"
    ).fetchone()
    assert (eligible, rated) == (0, 0)


def test_write_records_rejects_a_snapshot_id_mismatch(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    record = make_rated_record("snap-OTHER")
    item = make_repo_write(record)
    with pytest.raises(ValueError, match="snapshot_id"):
        write_records(conn, "snap-1", [item])


def test_write_records_writes_populations(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    popset = PopulationSet(
        snapshot_id="snap-1",
        benchmark_version="2.0.0-dev",
        constants_hash="c" * 64,
        populations=(
            Population(ecosystem="python", param="ttfhr_hours", n=2, fallback=False, values={"a/b": 1.0, "c/d": 2.0}),
        ),
    )
    counts = write_records(conn, "snap-1", [], populations=popset)
    assert counts == (0, 0, 1)
    row = conn.execute("SELECT ecosystem, param_id, n, fallback FROM populations").fetchone()
    assert row == ("python", "ttfhr_hours", 2, 0)


# --------------------------------------------------------------------- #
# A snapshot id is immutable
# --------------------------------------------------------------------- #

def test_write_records_rejects_a_second_write_to_the_same_snapshot(tmp_path) -> None:
    """Five repos, then a second write of 2 for the SAME snapshot ->
    raises; the database still shows exactly the original 5 rows and the
    snapshot's identity hash is unchanged."""
    db_path = tmp_path / "db.sqlite"
    conn = _fresh_db(db_path)
    _write_one_snapshot(conn)
    five = [make_repo_write(make_rated_record("snap-1", owner="acme", name=f"r{i}", composite=float(i))) for i in range(5)]
    write_records(conn, "snap-1", five)
    conn.commit()
    hash_before = snapshot_dump_hash(db_path, "snap-1")

    two = [make_repo_write(make_rated_record("snap-1", owner="other", name=f"x{i}", composite=float(i))) for i in range(2)]
    with pytest.raises(ValueError, match="immutable"):
        write_records(conn, "snap-1", two)

    count = conn.execute("SELECT COUNT(*) FROM repositories WHERE snapshot_id='snap-1'").fetchone()[0]
    assert count == 5
    names = {row[0] for row in conn.execute("SELECT name FROM repositories WHERE snapshot_id='snap-1'").fetchall()}
    assert names == {"r0", "r1", "r2", "r3", "r4"}
    hash_after = snapshot_dump_hash(db_path, "snap-1")
    assert hash_after == hash_before


def test_write_records_rejects_rewrite_even_when_shrinking(tmp_path) -> None:
    """Over-correction guard: the immutability check fires even when the
    caller is trying to write FEWER rows than before (the exact shape a
    delete-then-insert design would have silently allowed to leave ghost
    members)."""
    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    write_records(conn, "snap-1", [make_repo_write(make_rated_record("snap-1", owner="acme", name="only"))])
    with pytest.raises(ValueError, match="immutable"):
        write_records(conn, "snap-1", [])
    assert conn.execute("SELECT COUNT(*) FROM repositories").fetchone()[0] == 1


def test_write_records_retry_after_a_rolled_back_failure_succeeds(tmp_path) -> None:
    """A snapshot id whose only prior attempt FAILED (and so rolled back to
    zero rows) is not "already written" -- retrying with the same id must
    succeed, proving rollback and immutability checks
    compose correctly rather than permanently locking out a legitimate
    retry."""
    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    bad_record = make_rated_record("snap-1", owner="acme", name="bad")
    bad_record = replace(bad_record, persona_composites={"x": object()})  # type: ignore[dict-item]
    with pytest.raises(TypeError):
        write_records(conn, "snap-1", [make_repo_write(bad_record)])
    assert conn.execute("SELECT COUNT(*) FROM repositories").fetchone()[0] == 0

    good_record = make_rated_record("snap-1", owner="acme", name="good")
    counts = write_records(conn, "snap-1", [make_repo_write(good_record)])
    assert counts == (1, 0, 0)


# --------------------------------------------------------------------- #
# One snapshot = one transaction, rollback on failure
# --------------------------------------------------------------------- #

def test_write_records_rolls_back_on_failure_leaving_zero_rows_after_a_later_commit(tmp_path) -> None:
    """A raise after 2 of 3 rows leaves the transaction rolled back; a
    SUBSEQUENT, unrelated ``commit()`` on the same connection must not make
    any of the partial batch durable."""
    db_path = tmp_path / "db.sqlite"
    conn = _fresh_db(db_path)
    _write_one_snapshot(conn)
    r0 = make_repo_write(make_rated_record("snap-1", owner="acme", name="r0"))
    r1 = make_repo_write(make_rated_record("snap-1", owner="acme", name="r1"))
    bad = make_repo_write(replace(make_rated_record("snap-1", owner="acme", name="r2"), persona_composites={"x": object()}))  # type: ignore[dict-item]

    with pytest.raises(TypeError):
        write_records(conn, "snap-1", [r0, r1, bad])

    # A later, unrelated commit on the SAME connection must not resurrect
    # the partial batch.
    conn.execute("SELECT 1")
    conn.commit()

    count = conn.execute("SELECT COUNT(*) FROM repositories WHERE snapshot_id='snap-1'").fetchone()[0]
    assert count == 0

    second_conn = sqlite3.connect(db_path)
    assert second_conn.execute("SELECT COUNT(*) FROM repositories").fetchone()[0] == 0
    second_conn.close()


# --------------------------------------------------------------------- #
# Transaction boundary atomicity
# --------------------------------------------------------------------- #

def test_write_records_params_loop_failure_leaves_zero_repositories_rows(tmp_path, monkeypatch) -> None:
    """A failure in the params loop rolls back preceding repository inserts
    within the same call, leaving zero repository rows committed."""
    import porchlamp.store.writer as writer_module

    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    record = make_rated_record(
        "snap-1", owner="acme", name="demo",
        params={"ttfhr_hours": ParamScore(value=88.0, score=88.0, measured=True, reason=None, n=10, window_start=None, window_end=None, method="primary", source="test", fetched_at="2026-09-01T00:00:00+00:00")},
    )
    item = make_repo_write(record, inputs={"ttfhr_hours": make_measured(3.0, n=10)})

    with monkeypatch.context() as m:
        m.setattr(writer_module, "_PARAM_INSERT_SQL", "INSERT INTO params (repo_id) VALUES (?)")
        with pytest.raises(sqlite3.ProgrammingError):
            write_records(conn, "snap-1", [item])

    conn.execute("SELECT 1")
    conn.commit()  # a later, unrelated commit must not resurrect anything
    assert conn.execute("SELECT COUNT(*) FROM repositories WHERE snapshot_id='snap-1'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM params").fetchone()[0] == 0

    # And the retry (same id, now that nothing committed) succeeds.
    counts = write_records(conn, "snap-1", [item])
    assert counts == (1, 1, 0)


def test_write_snapshot_and_write_records_compose_into_one_transaction(tmp_path, monkeypatch) -> None:
    """Transaction composition guarantee: a caller wraps :func:`write_snapshot`
    and :func:`write_records` in ONE outer transaction (enabled by the
    join-if-open guard); a failure while writing params leaves NOTHING for
    the id -- not even the ``snapshots`` row -- and a retry with the SAME
    id succeeds."""
    import porchlamp.store.writer as writer_module

    db_path = tmp_path / "db.sqlite"
    conn = _fresh_db(db_path)
    record = make_rated_record(
        "snap-1", owner="acme", name="demo",
        params={"ttfhr_hours": ParamScore(value=88.0, score=88.0, measured=True, reason=None, n=10, window_start=None, window_end=None, method="primary", source="test", fetched_at="2026-09-01T00:00:00+00:00")},
    )
    item = make_repo_write(record, inputs={"ttfhr_hours": make_measured(3.0, n=10)})

    with monkeypatch.context() as m:
        m.setattr(writer_module, "_PARAM_INSERT_SQL", "INSERT INTO params (repo_id) VALUES (?)")
        conn.execute("BEGIN")
        try:
            write_snapshot(
                conn, id="snap-1", as_of="2026-09-13", benchmark_version="2.0.0-dev",
                constants_hash="c" * 64, code_commit="deadbeef", manifest_root="root",
                created_at="2026-09-13T00:00:00Z",
            )
            write_records(conn, "snap-1", [item])
            conn.commit()
            pytest.fail("expected the params write to fail")
        except sqlite3.ProgrammingError:
            conn.rollback()

    assert conn.execute("SELECT COUNT(*) FROM snapshots WHERE id='snap-1'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM repositories WHERE snapshot_id='snap-1'").fetchone()[0] == 0

    _write_one_snapshot(conn)
    counts = write_records(conn, "snap-1", [item])
    assert counts == (1, 1, 0)


# --------------------------------------------------------------------- #
# PRAGMA foreign_keys = ON
# --------------------------------------------------------------------- #

def test_orphan_params_row_is_rejected(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO params (repo_id, param_id, measured) VALUES (999999, 'ttfhr_hours', 0)")


def test_orphan_repositories_row_is_rejected(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO repositories (snapshot_id, owner, name, ecosystem, raw_hash, constants_hash, "
            "eligible, rated, coverage) VALUES ('snap-GHOST', 'acme', 'ghost', 'python', ?, ?, 1, 1, 0.9)",
            ("h" * 64, "c" * 64),
        )


def test_foreign_key_check_is_empty_after_a_full_write(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    record = make_rated_record(
        "snap-1", params={"ttfhr_hours": ParamScore(value=88.0, score=88.0, measured=True, reason=None, n=10, window_start=None, window_end=None, method="primary", source="test", fetched_at="2026-09-01T00:00:00+00:00")},
    )
    write_records(conn, "snap-1", [make_repo_write(record, inputs={"ttfhr_hours": make_measured(3.0, n=10)})])
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


# --------------------------------------------------------------------- #
# Join an already-open transaction
# --------------------------------------------------------------------- #

def test_write_snapshot_joins_an_already_open_transaction(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    conn.execute("BEGIN")
    write_snapshot(  # must NOT raise "cannot start a transaction within a transaction"
        conn, id="snap-1", as_of="2026-09-13", benchmark_version="2.0.0-dev",
        constants_hash="c" * 64, code_commit="deadbeef", manifest_root="root",
        created_at="2026-09-13T00:00:00Z",
    )
    assert conn.in_transaction  # write_snapshot must not have committed the OUTER transaction
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1


def test_write_snapshot_joined_transaction_still_rolls_back(tmp_path) -> None:
    """Rollback semantics still hold when write_snapshot only joins an
    ambient transaction: an outer rollback undoes its insert too."""
    conn = _fresh_db(tmp_path / "db.sqlite")
    conn.execute("BEGIN")
    write_snapshot(
        conn, id="snap-1", as_of="2026-09-13", benchmark_version="2.0.0-dev",
        constants_hash="c" * 64, code_commit="deadbeef", manifest_root="root",
        created_at="2026-09-13T00:00:00Z",
    )
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 0


def test_write_records_joins_an_already_open_transaction(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    conn.execute("BEGIN")
    counts = write_records(conn, "snap-1", [make_repo_write(make_rated_record("snap-1"))])
    assert conn.in_transaction
    conn.commit()
    assert counts == (1, 0, 0)
    assert conn.execute("SELECT COUNT(*) FROM repositories").fetchone()[0] == 1


# --------------------------------------------------------------------- #
# Mutation-battery survivors: dedicated kill tests
# --------------------------------------------------------------------- #

def test_rank_is_null_whenever_not_rated(tmp_path) -> None:
    """Kills M3 (a not-ranked row given ``rank = 1``): `06` section 4 --
    gated and unrated rows carry no rank."""
    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    gated = make_repo_write(make_gated_record("snap-1", owner="acme", name="gated-one"))
    unrated = make_repo_write(make_unrated_record("snap-1", owner="acme", name="unrated-one"))
    write_records(conn, "snap-1", [gated, unrated])
    ranks = conn.execute(
        "SELECT name, rank FROM repositories WHERE snapshot_id='snap-1' ORDER BY name"
    ).fetchall()
    assert ranks == [("gated-one", None), ("unrated-one", None)]


def test_two_dict_orderings_of_the_same_record_write_byte_identical_json(tmp_path) -> None:
    """Kills M4 (non-canonical JSON): two records whose ``persona_gates``
    dict was built with keys inserted in a different order, but with equal
    content, must produce byte-identical stored JSON."""
    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)

    forward = {"alpha": "a", "mid": "m", "zebra": "z"}
    backward = {"zebra": "z", "mid": "m", "alpha": "a"}
    assert forward == backward  # same content, different insertion order

    r1 = make_repo_write(make_rated_record("snap-1", owner="acme", name="one", persona_gates=forward))
    r2 = make_repo_write(make_rated_record("snap-1", owner="acme", name="two", persona_gates=backward))
    write_records(conn, "snap-1", [r1, r2])

    json_one, json_two = (
        row[0]
        for row in conn.execute(
            "SELECT persona_gates FROM repositories WHERE snapshot_id='snap-1' ORDER BY name"
        ).fetchall()
    )
    assert json_one == json_two


def test_migrating_a_create_schema_built_v2_db_renames_nothing(tmp_path) -> None:
    """Kills M10 (unconditional legacy rename): a database built directly
    via ``schema.create_schema`` (v2-shaped, no ``schema_migrations`` rows)
    must survive ``apply_migrations`` with its ``repositories`` rows
    intact and no ``legacy_repositories`` table created."""
    db_path = tmp_path / "v2_from_scratch.db"
    conn = sqlite3.connect(db_path)
    schema.create_schema(conn)
    _write_one_snapshot(conn)
    conn.execute(
        "INSERT INTO repositories (snapshot_id, owner, name, ecosystem, raw_hash, constants_hash, "
        "eligible, rated, coverage) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("snap-1", "acme", "pre-existing", "python", "h" * 64, "c" * 64, 1, 1, 0.9),
    )
    conn.commit()

    applied = apply_migrations(conn, applied_at="2026-09-13T00:00:00Z")
    assert applied == [1, 2]

    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    assert "legacy_repositories" not in tables
    row = conn.execute("SELECT owner, name FROM repositories WHERE snapshot_id='snap-1'").fetchone()
    assert row == ("acme", "pre-existing")


# --------------------------------------------------------------------- #
# Allow-list dump hash
# --------------------------------------------------------------------- #

def test_snapshot_dump_hash_ignores_other_snapshots(tmp_path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = _fresh_db(db_path)
    write_snapshot(
        conn, id="snap-A", as_of="2026-09-13", benchmark_version="2.0.0-dev",
        constants_hash="c" * 64, code_commit="deadbeef", manifest_root="r",
        created_at="2026-09-13T00:00:00Z",
    )
    write_snapshot(
        conn, id="snap-B", as_of="2026-09-13", benchmark_version="2.0.0-dev",
        constants_hash="c" * 64, code_commit="deadbeef", manifest_root="r",
        created_at="2026-09-13T00:00:00Z",
    )
    write_records(conn, "snap-A", [make_repo_write(make_rated_record("snap-A", owner="acme", name="a-repo", composite=50.0))])
    hash_before = snapshot_dump_hash(db_path, "snap-A")

    write_records(conn, "snap-B", [make_repo_write(make_rated_record("snap-B", owner="acme", name="b-repo", composite=99.0))])
    hash_after = snapshot_dump_hash(db_path, "snap-A")
    assert hash_before == hash_after  # snap-B's write must not change snap-A's hash

    # Sensitivity control: a genuinely different snapshot's hash differs.
    write_snapshot(
        conn, id="snap-C", as_of="2026-09-13", benchmark_version="2.0.0-dev",
        constants_hash="c" * 64, code_commit="deadbeef", manifest_root="r",
        created_at="2026-09-13T00:00:00Z",
    )
    write_records(conn, "snap-C", [make_repo_write(make_rated_record("snap-C", owner="acme", name="a-repo", composite=51.0))])
    hash_c = snapshot_dump_hash(db_path, "snap-C")
    assert hash_c != hash_before


def test_snapshot_dump_hash_ignores_legacy_repositories(tmp_path) -> None:
    """A fixed exclude-list would hash a migrated v1 DB's
    ``legacy_repositories`` (1,000 rows) into every v2 snapshot's identity.
    The allow-list (keep ``repositories`` only) must be immune to it."""
    db_path = tmp_path / "db.sqlite"
    conn = _fresh_db(db_path)
    _write_one_snapshot(conn)
    write_records(conn, "snap-1", [make_repo_write(make_rated_record("snap-1", owner="acme", name="demo"))])
    conn.commit()
    hash_before = snapshot_dump_hash(db_path, "snap-1")

    conn.execute(
        "CREATE TABLE legacy_repositories (id INTEGER PRIMARY KEY, owner TEXT, name TEXT, overall_porchlamp REAL)"
    )
    conn.execute("INSERT INTO legacy_repositories (owner, name, overall_porchlamp) VALUES ('v1co','repo',71.5)")
    conn.commit()
    hash_with_legacy = snapshot_dump_hash(db_path, "snap-1")
    assert hash_with_legacy == hash_before  # legacy content must not move the v2 hash

    conn.execute("UPDATE legacy_repositories SET overall_porchlamp = 99.9 WHERE owner='v1co'")
    conn.commit()
    hash_after_legacy_edit = snapshot_dump_hash(db_path, "snap-1")
    assert hash_after_legacy_edit == hash_before  # editing legacy content still must not move it

    # Sensitivity control: editing the ACTUAL v2 row DOES move the hash.
    conn.execute("UPDATE repositories SET porchlamp = 12.0 WHERE snapshot_id='snap-1' AND name='demo'")
    conn.commit()
    hash_after_v2_edit = snapshot_dump_hash(db_path, "snap-1")
    assert hash_after_v2_edit != hash_before


def test_snapshot_dump_hash_covers_params_and_populations_rows(tmp_path) -> None:
    """The identity hash must cover the snapshot's
    OWN ``params`` and ``populations`` rows, not just ``repositories`` --
    rewriting a param's score/raw_value/source_hash, or a population's
    values, must move the hash; another snapshot's rows must not."""
    db_path = tmp_path / "db.sqlite"
    conn = _fresh_db(db_path)
    write_snapshot(
        conn, id="snap-A", as_of="2026-09-13", benchmark_version="2.0.0-dev",
        constants_hash="c" * 64, code_commit="deadbeef", manifest_root="r",
        created_at="2026-09-13T00:00:00Z",
    )
    write_snapshot(
        conn, id="snap-B", as_of="2026-09-13", benchmark_version="2.0.0-dev",
        constants_hash="c" * 64, code_commit="deadbeef", manifest_root="r",
        created_at="2026-09-13T00:00:00Z",
    )
    record_a = make_rated_record(
        "snap-A", owner="acme", name="a-repo", composite=50.0,
        params={"ttfhr_hours": ParamScore(value=88.0, score=88.0, measured=True, reason=None, n=10, window_start=None, window_end=None, method="primary", source="test", fetched_at="2026-09-01T00:00:00+00:00")},
    )
    write_records(
        conn, "snap-A", [make_repo_write(record_a, inputs={"ttfhr_hours": make_measured(4.0, n=10)})],
        populations=PopulationSet(
            snapshot_id="snap-A", benchmark_version="2.0.0-dev", constants_hash="c" * 64,
            populations=(Population(ecosystem="python", param="ttfhr_hours", n=2, fallback=False,
                                     values={"a/b": 1.0, "c/d": 2.0}),),
        ),
    )
    record_b = make_rated_record(
        "snap-B", owner="acme", name="b-repo", composite=60.0,
        params={"ttfhr_hours": ParamScore(value=70.0, score=70.0, measured=True, reason=None, n=8, window_start=None, window_end=None, method="primary", source="test", fetched_at="2026-09-01T00:00:00+00:00")},
    )
    write_records(
        conn, "snap-B", [make_repo_write(record_b, inputs={"ttfhr_hours": make_measured(3.0, n=8)})],
        populations=PopulationSet(
            snapshot_id="snap-B", benchmark_version="2.0.0-dev", constants_hash="c" * 64,
            populations=(Population(ecosystem="python", param="ttfhr_hours", n=2, fallback=False,
                                     values={"e/f": 5.0, "g/h": 6.0}),),
        ),
    )
    conn.commit()

    baseline_a = snapshot_dump_hash(db_path, "snap-A")
    baseline_b = snapshot_dump_hash(db_path, "snap-B")

    # (1) rewriting snap-A's params row (score, raw_value, source_hash) moves
    # snap-A's hash and leaves snap-B's alone.
    conn.execute(
        "UPDATE params SET score=1.0, raw_value='999', source_hash=? "
        "WHERE repo_id = (SELECT id FROM repositories WHERE snapshot_id='snap-A' AND name='a-repo') "
        "AND param_id='ttfhr_hours'",
        ("d" * 64,),
    )
    conn.commit()
    assert snapshot_dump_hash(db_path, "snap-A") != baseline_a
    assert snapshot_dump_hash(db_path, "snap-B") == baseline_b

    # (2) rewriting snap-B's populations row moves snap-B's hash and leaves
    # a THIRD, fresh baseline of snap-A's (still-mutated) hash alone.
    baseline_a_after_params_edit = snapshot_dump_hash(db_path, "snap-A")
    conn.execute(
        "UPDATE populations SET n=999, values_json='[]', values_hash=? WHERE snapshot_id='snap-B'",
        ("f" * 64,),
    )
    conn.commit()
    assert snapshot_dump_hash(db_path, "snap-B") != baseline_b
    assert snapshot_dump_hash(db_path, "snap-A") == baseline_a_after_params_edit


def test_finalize_snapshot_hash_stamps_hash_and_promoted_at(tmp_path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = _fresh_db(db_path)
    _write_one_snapshot(conn)
    record = make_rated_record("snap-1")
    write_records(conn, "snap-1", [make_repo_write(record)])

    expected_hash = snapshot_dump_hash(db_path, "snap-1")
    returned_hash = finalize_snapshot_hash(conn, db_path, "snap-1", promoted_at="2026-09-13T02:00:00Z")
    assert returned_hash == expected_hash

    row = conn.execute("SELECT canonical_dump_hash, promoted_at FROM snapshots WHERE id='snap-1'").fetchone()
    assert row == (expected_hash, "2026-09-13T02:00:00Z")


# --------------------------------------------------------------------- #
# RepoWrite identity, PersonaComposite serialisation
# --------------------------------------------------------------------- #

def test_repo_write_rejects_mismatched_identity() -> None:
    record = make_rated_record("snap-1", owner="acme", name="demo")
    bundle = make_bundle("acme", "other-name")
    with pytest.raises(ValueError, match="does not match"):
        RepoWrite(record=record, bundle=bundle)


def test_persona_composites_round_trip_via_asdict(tmp_path) -> None:
    """``PersonaComposite`` dataclasses serialize to dicts for JSON storage
    and reconstruct identically on read."""
    from porchlamp.store import reader

    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    composites = {
        "balanced": PersonaComposite(porchlamp=82.5, coverage=0.9, tier="B", rated=True),
        "senior_systems": PersonaComposite(porchlamp=71.5, coverage=0.6, tier="C", rated=True),
        "first_timer": PersonaComposite(porchlamp=None, coverage=0.2, tier=None, rated=False),
    }
    record = make_rated_record("snap-1", persona_composites=composites)
    write_records(conn, "snap-1", [make_repo_write(record)])

    row = reader.get_row(conn, "snap-1", "acme", "demo")
    stored = row["persona_composites"]
    for persona, original in composites.items():
        assert PersonaComposite(**stored[persona]) == original


def test_writer_raises_a_clear_error_on_the_stale_dict_float_shape() -> None:
    """A record built with bare float persona composites fails loudly at
    write time rather than storing invalid data."""
    record = make_rated_record("snap-1", owner="acme", name="demo")
    record = replace(record, persona_composites={"balanced": 82.5})  # type: ignore[dict-item]
    item = make_repo_write(record)
    with pytest.raises(TypeError):
        from porchlamp.store.writer import _repo_row
        _repo_row("snap-1", item)


# --------------------------------------------------------------------- #
# gate_undecided
# --------------------------------------------------------------------- #

def test_gate_undecided_round_trips(tmp_path) -> None:
    from porchlamp.store import reader

    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    record = make_rated_record("snap-1", gate_undecided=("D-GATE-3", "D-GATE-6"))
    write_records(conn, "snap-1", [make_repo_write(record)])
    row = reader.get_row(conn, "snap-1", "acme", "demo")
    assert row["gate_undecided"] == ["D-GATE-3", "D-GATE-6"]


# --------------------------------------------------------------------- #
# UTC normalisation
# --------------------------------------------------------------------- #

def test_normalize_utc_z_passes_none_through() -> None:
    assert _normalize_utc_z(None) is None


def test_normalize_utc_z_converts_a_non_utc_offset() -> None:
    # 20:00 at UTC-05:00 is the same instant as 01:00Z the next day.
    assert _normalize_utc_z("2026-01-01T20:00:00-05:00") == "2026-01-02T01:00:00Z"


def test_normalize_utc_z_treats_an_offset_less_value_as_already_utc() -> None:
    assert _normalize_utc_z("2026-06-01T00:00:00") == "2026-06-01T00:00:00Z"


def test_normalize_utc_z_is_idempotent_on_an_already_z_value() -> None:
    assert _normalize_utc_z("2026-06-01T00:00:00Z") == "2026-06-01T00:00:00Z"


# --------------------------------------------------------------------- #
# ttfhr_p75 computed from the bundle (RepoWrite no longer carries it)
# --------------------------------------------------------------------- #

def test_ttfhr_p75_is_computed_from_bundle_pr_window(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    record = make_rated_record("snap-1")
    pr_window = PRWindow(
        start="2026-01-01T00:00:00Z", end="2026-06-01T00:00:00Z", truncated=False,
        events=(
            PRWindowEvent(
                pr_number=1, created_at="2026-01-01T00:00:00Z", closed_at=None, merged_at=None,
                author_login="alice", author_association="NONE", is_external=True,
                first_human_review_at="2026-01-01T05:00:00Z",  # 5 hours later
            ),
        ),
    )
    item = make_repo_write(record, pr_window=pr_window)
    write_records(conn, "snap-1", [item])
    ttfhr = conn.execute("SELECT ttfhr_p75 FROM repositories WHERE snapshot_id='snap-1'").fetchone()[0]
    assert ttfhr == 5.0


def test_ttfhr_p75_is_none_with_no_qualifying_event(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    record = make_rated_record("snap-1")
    item = make_repo_write(record)  # default empty pr_window
    write_records(conn, "snap-1", [item])
    ttfhr = conn.execute("SELECT ttfhr_p75 FROM repositories WHERE snapshot_id='snap-1'").fetchone()[0]
    assert ttfhr is None


def test_stars_forks_are_null_no_producer(tmp_path) -> None:
    """When a bundle has no stars/forks/last_commit facts set, the corresponding
    columns in repositories remain NULL."""
    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    write_records(conn, "snap-1", [make_repo_write(make_rated_record("snap-1"))])
    row = conn.execute(
        "SELECT stars_audited, forks, last_human_commit_at FROM repositories WHERE snapshot_id='snap-1'"
    ).fetchone()
    assert row == (None, None, None)


def test_stars_audited_and_forks_written_from_bundle_sidecars(tmp_path) -> None:
    """When a bundle carries stars_audited and forks sidecar facts, write_records
    persists non-null values into repositories."""
    from porchlamp.derive.bundle import set_forks_count, set_stars_audited

    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    item = make_repo_write(make_rated_record("snap-1"))
    set_stars_audited(item.bundle, 456)
    set_forks_count(item.bundle, 78)
    write_records(conn, "snap-1", [item])
    row = conn.execute(
        "SELECT stars_audited, forks FROM repositories WHERE snapshot_id='snap-1'"
    ).fetchone()
    assert row == (456, 78)


def test_stars_audited_legacy_inputs_fallback_still_works(tmp_path) -> None:
    """When stars_audited sidecar is not set but bundle.inputs carries a Measured
    'stars_audited' entry, write_records falls back to it."""
    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    item = make_repo_write(make_rated_record("snap-1"))
    item.bundle.inputs["stars_audited"] = make_measured(999, n=0)
    write_records(conn, "snap-1", [item])
    row = conn.execute(
        "SELECT stars_audited, forks FROM repositories WHERE snapshot_id='snap-1'"
    ).fetchone()
    assert row == (999, None)



# --------------------------------------------------------------------- #
# last_human_commit_at produced from the bundle sidecar fact             #
# --------------------------------------------------------------------- #

def test_last_human_commit_at_is_produced_from_the_bundle_sidecar_fact(tmp_path) -> None:
    from porchlamp.core.commit_domains import LastNonBotCommitMeasured, set_last_non_bot_commit_at

    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    record = make_rated_record("snap-1")
    item = make_repo_write(record)
    set_last_non_bot_commit_at(item.bundle, LastNonBotCommitMeasured(
        value="2026-06-01T00:00:00Z", method="measured:commits_365d",
        source="raw/commits_365d/acme__demo.json", source_hash="a" * 64,
        fetched_at="2026-09-01T00:00:00Z",
    ))
    write_records(conn, "snap-1", [item])
    stored = conn.execute(
        "SELECT last_human_commit_at FROM repositories WHERE snapshot_id='snap-1'"
    ).fetchone()[0]
    assert stored == "2026-06-01T00:00:00Z"


def test_last_human_commit_at_normalizes_a_non_utc_offset_through_the_real_writer(tmp_path) -> None:
    """The sidecar fact is already ``Z``-formatted by ``porchlamp.derive.bundle``
    in practice, but the writer's own ``_normalize_utc_z`` pass must not be
    a no-op that merely trusts the input -- a non-UTC-offset value must
    still normalize correctly when it flows through the real writer path,
    not just the ``_write_and_rank`` UPDATE-stamp fixtures."""
    from porchlamp.core.commit_domains import LastNonBotCommitMeasured, set_last_non_bot_commit_at

    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    record = make_rated_record("snap-1")
    item = make_repo_write(record)
    set_last_non_bot_commit_at(item.bundle, LastNonBotCommitMeasured(
        value="2026-06-01T20:00:00-05:00", method="measured:commits_365d",
        source="s", source_hash="a" * 64, fetched_at="2026-09-01T00:00:00Z",
    ))
    write_records(conn, "snap-1", [item])
    stored = conn.execute(
        "SELECT last_human_commit_at FROM repositories WHERE snapshot_id='snap-1'"
    ).fetchone()[0]
    assert stored == "2026-06-02T01:00:00Z"


def test_last_human_commit_at_is_null_for_a_measured_absence(tmp_path) -> None:
    """A ``LastNonBotCommitMeasured(value=None, ...)`` measured absence
    (D-MISS-1 F2 ruling) must store ``NULL``, not the sentinel string --
    same as an unset/``Unmeasured`` fact, distinguishing "the column has
    no evidence" from "we measured zero non-bot commits" only at the
    ``params``/detail level, never in this ordering column."""
    from porchlamp.core.commit_domains import LastNonBotCommitMeasured, set_last_non_bot_commit_at

    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    record = make_rated_record("snap-1")
    item = make_repo_write(record)
    set_last_non_bot_commit_at(item.bundle, LastNonBotCommitMeasured(
        value=None, method="measured:no_non_bot_commit_365d",
        source="s", source_hash="a" * 64, fetched_at="2026-09-01T00:00:00Z",
    ))
    write_records(conn, "snap-1", [item])
    stored = conn.execute(
        "SELECT last_human_commit_at FROM repositories WHERE snapshot_id='snap-1'"
    ).fetchone()[0]
    assert stored is None


def test_sql_rank_order_agrees_with_python_when_writer_produces_the_commit_date(tmp_path) -> None:
    """Two repos tied through
    D-TIE-1 level 6, differing only by ``last_non_bot_commit_at`` --
    ``assign_ranks`` (Python) and ``reader.ranked_order_sql`` (SQL) must
    agree once the WRITER produces the column for real, with no UPDATE
    stamp anywhere in this test."""
    from porchlamp.core.commit_domains import LastNonBotCommitMeasured, set_last_non_bot_commit_at
    from porchlamp.core.tiebreak import TieBreakInput, assign_ranks
    from porchlamp.store import reader

    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)

    older = make_rated_record("snap-1", owner="acme", name="aaa-old", raw_hash="h-old", composite=70.0, coverage=1.0)
    newer = make_rated_record("snap-1", owner="acme", name="zzz-new", raw_hash="h-new", composite=70.0, coverage=1.0)
    commit_dates = {"aaa-old": "2026-01-01T00:00:00Z", "zzz-new": "2026-06-01T00:00:00Z"}
    entries = [
        TieBreakInput(older, "acme", "aaa-old", last_non_bot_commit_at=commit_dates["aaa-old"]),
        TieBreakInput(newer, "acme", "zzz-new", last_non_bot_commit_at=commit_dates["zzz-new"]),
    ]
    ranked = assign_ranks(entries)
    python_order = [(record.repo.owner, record.repo.name) for record, _rank, _tie_group in ranked]
    # The later commit (zzz-new) outranks the earlier one (aaa-old) even
    # though 'aaa-old' sorts first alphabetically -- proves level 7, not
    # the level-8 name fallback, decided this order.
    assert python_order == [("acme", "zzz-new"), ("acme", "aaa-old")]

    items = []
    for record, _rank, _tie_group in ranked:
        item = make_repo_write(record)
        set_last_non_bot_commit_at(item.bundle, LastNonBotCommitMeasured(
            value=commit_dates[record.repo.name], method="measured:commits_365d",
            source="s", source_hash="a" * 64, fetched_at="2026-09-01T00:00:00Z",
        ))
        items.append(item)
    write_records(conn, "snap-1", items)

    sql_order = [(row[0], row[1]) for row in reader.ranked_order_sql(conn, "snap-1")]
    assert sql_order == python_order
