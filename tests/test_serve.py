"""Tests for the production serving entrypoint (scripts/serve.py).

The load-bearing property: serve.py reads an ALREADY-PROMOTED store and does
NOT seed synthetic benchmark_data. An empty DB must serve an empty dashboard
(not the ~1,000 fabricated Gemini fixtures). This locks the "only verified
data reaches the API" rule at the serving boundary.

sqlite3 connections are thread-bound and starlette's TestClient dispatches on
its portal thread, so the store is built inside ``client.portal.call`` (the
same pattern as ``tests/test_api.py::open_client``).

The second half of the file is the served-DB guard of packet W0-4 (`03` §5,
F1). Round 3 restores an independent rename-proof fixture check (Blocker-1:
round 2 deleted it while adding the content-hash identity, so seeding the
synthetic catalog and then promoting a real sample on top served fabricated
rows alongside real ones) alongside identity bound to the database's OWN
content, not a per-row name/telemetry heuristic (round 1's ``(owner, name)
== (stars, forks)`` equality, which one ``UPDATE`` statement defeated) or a
manifest.json that happens to sit beside the db file (round 1's Warning-3).
``scripts/promote.py`` writes a ``snapshot_meta`` row carrying a content hash
of the ``repositories`` table; ``scripts/serve.py`` recomputes that hash at
startup and refuses unless it still matches a stored row. ``uvicorn.run`` is
the external boundary those tests stub.
"""

from __future__ import annotations

import importlib.util
import shutil
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cvi.api import create_app
from cvi.benchmark_data import BENCHMARK_NOW, get_benchmark_catalog, seed_benchmark_catalog
from cvi.lakehouse import OperationalStore, apply_raw_metrics, repositories_dump_hash
from cvi.models import CVICompositeResult, Ecosystem, PillarScores, Quadrant, Tier
from cvi.snapshot.manifest import build_manifest, envelope_entries, write_manifest
from scripts import serve
from scripts.serve import count_repos, create_serve_app

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("promote", ROOT / "scripts" / "promote.py")
promote = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(promote)

AS_OF = "2026-09-12T00:00:00+00:00"

#: A real repository's envelopes, used to build genuine, non-placeholder
#: manifests (round 3, Warning-3): ``build_manifest([], ...)`` covered zero
#: envelopes, the exact shape that hid Blocker-2 from every test that used
#: it. serve.py's own checks never consult manifest envelope content, so
#: this repo need not relate to whatever rows a given test writes into
#: ``repositories`` -- only :func:`_promote_snapshot`'s content hash does
#: that. Round 4 (Blocker-2): committed under ``tests/fixtures/`` rather
#: than read from ``~/Projects/cvi-scratch/sample-200``, absent on any
#: machine but this one, CI included.
_SAMPLE_RAW = Path(__file__).resolve().parent / "fixtures" / "numpy-envelopes"
_SAMPLE_KEY = "numpy__numpy"


def _store_at(db_path: Path) -> OperationalStore:
    """Open the store at ``db_path`` (a file on disk, not ``:memory:``)."""
    return OperationalStore(str(db_path))


def _result(name: str) -> CVICompositeResult:
    return CVICompositeResult(
        owner="tokio-rs",
        name=name,
        ecosystem=Ecosystem.CRATES,
        overall_cvi=70.31,
        rank_tier=Tier.A,
        matrix_quadrant=Quadrant.SWEET_SPOT,
        leverage_score=72.0,
        friction_score=30.0,
        is_stage0_disqualified=False,
        disqualification_reason=None,
    )


def open_serve_client(seed: Callable[[], OperationalStore]) -> TestClient:
    """Enter a TestClient whose app store is built on the portal thread."""
    app = create_serve_app(OperationalStore())
    client = TestClient(app)
    client.__enter__()
    app.state.store = client.portal.call(seed)
    return client


def _empty() -> OperationalStore:
    return OperationalStore()


def _promoted() -> OperationalStore:
    store = OperationalStore()
    store.upsert_composite(
        _result("tokio"),
        pillar_scores=PillarScores(70, 80, 60, 65, 55),
    )
    return store


def test_count_repos_empty_is_zero():
    # count_repos runs on the test thread (direct SQL, no portal needed).
    assert count_repos(OperationalStore()) == 0


def test_empty_store_serves_no_repos():
    # FAIL-BEFORE: if serve.py seeded benchmark_data, an empty store would
    # report ~1000 repos here. It must report zero — no fabrication.
    client = open_serve_client(_empty)
    try:
        lb = client.get("/v1/cvi/leaderboard?limit=1000").json()
        assert lb["items"] == []
        assert lb["total"] == 0
    finally:
        client.__exit__(None, None, None)


def test_promoted_row_is_served():
    client = open_serve_client(_promoted)
    try:
        lb = client.get("/v1/cvi/leaderboard?limit=1000").json()
        assert lb["total"] == 1
        assert lb["items"][0]["owner"] == "tokio-rs"
        assert lb["items"][0]["name"] == "tokio"
        assert lb["items"][0]["cvi"] == 70.31
    finally:
        client.__exit__(None, None, None)


def test_healthz_ok():
    client = TestClient(create_app(store=OperationalStore()))
    assert client.get("/healthz").json() == {"status": "ok"}


# --------------------------------------------------------------------------- #
# The served-DB guard (`03` §5, F1): fabricated fixtures and unbound
# databases never reach a server.
# --------------------------------------------------------------------------- #


def _copy_sample_envelopes(data_dir: Path) -> None:
    """Copy one real repo's envelopes from the read-only sample into
    ``data_dir/raw/`` (round 3, Warning-3)."""
    for collector_dir in sorted(p for p in _SAMPLE_RAW.iterdir() if p.is_dir()):
        src = collector_dir / f"{_SAMPLE_KEY}.json"
        if src.is_file():
            dest = data_dir / "raw" / collector_dir.name / f"{_SAMPLE_KEY}.json"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)


def _freeze_real(data_dir: Path, as_of: str = AS_OF) -> dict:
    """A real frozen manifest over ``data_dir``'s own ``raw/`` envelopes."""
    _copy_sample_envelopes(data_dir)
    scan = envelope_entries(data_dir)
    manifest = build_manifest(scan.entries, as_of=as_of, code_commit="unknown",
                              canonical_dump_hash="pending")
    write_manifest(data_dir, manifest)
    return manifest


def _promote_snapshot(data_dir: Path, db: Path) -> dict:
    """Freeze ``data_dir`` (real envelopes) and bind ``db``'s current
    ``repositories`` content to it, exactly as ``scripts/promote.py`` does at
    the end of a real promotion (Blocker-3): a ``snapshot_meta`` row carrying
    :func:`cvi.lakehouse.repositories_dump_hash`, not a filesystem manifest
    lookup or a per-row name/telemetry match.
    """
    manifest = _freeze_real(data_dir)
    promote.write_snapshot_meta(str(db), manifest, repositories_dump_hash(str(db)))
    return manifest


def _fixture_rows(store: OperationalStore, count: int) -> list[dict]:
    """Seed ``count`` synthetic benchmark rows, exactly as ``demo_app`` does."""
    rows = get_benchmark_catalog(count)
    seed_benchmark_catalog(store, rows, BENCHMARK_NOW)
    return rows


def _promoted_row(store: OperationalStore, owner: str, name: str,
                  stars: int, forks: int) -> None:
    """Write one row the way scripts/promote.py does: composite + raw metrics."""
    result = CVICompositeResult(
        owner=owner, name=name, ecosystem=Ecosystem.PYPI, overall_cvi=70.31,
        rank_tier=Tier.A, matrix_quadrant=Quadrant.SWEET_SPOT, leverage_score=72.0,
        friction_score=30.0, is_stage0_disqualified=False, disqualification_reason=None,
    )
    store.upsert_composite(result, pillar_scores=PillarScores(70, 80, 60, 65, 55))
    apply_raw_metrics(store, owner, name, stars=stars, forks=forks)


class _RunRecorder:
    """Stand-in for ``uvicorn.run``: records that serving started, without blocking."""

    def __init__(self) -> None:
        self.ports: list[int] = []

    def __call__(self, app, **kwargs) -> None:
        self.ports.append(int(kwargs["port"]))


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> _RunRecorder:
    """Replace ``uvicorn.run`` so ``main`` returns instead of blocking."""
    recorder = _RunRecorder()
    monkeypatch.setattr(serve.uvicorn, "run", recorder)
    return recorder


def test_fixture_database_is_refused_naming_the_row(tmp_path, served):
    # Round 3 (Blocker-1): restored. Round 2 deleted this rule outright
    # while adding the content-hash identity below, rather than keeping it
    # as a second, independent guard -- the two catch different things (see
    # the module docstring). A snapshot is bound here too, so the fixture
    # rule is isolated: only IT can fire.
    db = tmp_path / "cvi.db"
    store = _store_at(db)
    rows = _fixture_rows(store, 3)
    store.close()
    _promote_snapshot(tmp_path, db)

    reasons = serve.refusal_reasons(OperationalStore(str(db)), str(db))
    assert len(reasons) == 1
    offending = f"{rows[0]['owner']}/{rows[0]['name']}"
    assert offending in reasons[0]
    assert "benchmark_data" in reasons[0]

    with pytest.raises(SystemExit) as excinfo:
        serve.main(["--db", str(db)])
    assert offending in str(excinfo.value)
    assert served.ports == []  # the server never started


def test_renaming_fixture_owners_does_not_evade_the_marker(tmp_path, served):
    # The fixture guard is keyed on last_indexed_at, never owner/name, so
    # round 1's exact bypass of the OLD (owner, name) == (stars, forks) rule
    # does nothing to it.
    db = tmp_path / "cvi.db"
    store = _store_at(db)
    _fixture_rows(store, 5)
    store.close()
    _promote_snapshot(tmp_path, db)

    conn = sqlite3.connect(db)
    conn.execute("UPDATE repositories SET owner = owner || '-inc'")
    conn.commit()
    conn.close()

    reasons = serve.refusal_reasons(OperationalStore(str(db)), str(db))
    assert any("benchmark_data" in r for r in reasons)
    with pytest.raises(SystemExit):
        serve.main(["--db", str(db)])
    assert served.ports == []


def test_database_without_snapshot_meta_is_refused(tmp_path, served):
    db = tmp_path / "cvi.db"
    store = _store_at(db)
    _promoted_row(store, "numpy", "numpy", stars=32721, forks=12769)
    store.close()

    reasons = serve.refusal_reasons(OperationalStore(str(db)), str(db))
    assert len(reasons) == 1
    assert "no snapshot_meta row" in reasons[0]

    with pytest.raises(SystemExit):
        serve.main(["--db", str(db)])
    assert served.ports == []


def test_promoted_database_with_snapshot_identity_serves(tmp_path, served):
    db = tmp_path / "cvi.db"
    store = _store_at(db)
    _promoted_row(store, "numpy", "numpy", stars=32721, forks=12769)
    _promoted_row(store, "tokio-rs", "tokio", stars=33123, forks=3244)
    store.close()
    _promote_snapshot(tmp_path, db)

    assert serve.refusal_reasons(OperationalStore(str(db)), str(db)) == []
    serve.main(["--db", str(db), "--port", "8123"])
    assert served.ports == [8123]


def test_real_repo_sharing_a_fixture_name_still_serves(tmp_path, served):
    # Over-correction guard: six of the 199 sample repos share a name with a
    # benchmark fixture. A real, promoted-and-bound row must still serve.
    db = tmp_path / "cvi.db"
    store = _store_at(db)
    _promoted_row(store, "torvalds", "linux", stars=248274, forks=64487)
    store.close()
    _promote_snapshot(tmp_path, db)

    fixture = next(row for row in get_benchmark_catalog(1000)
                   if (row["owner"], row["name"]) == ("torvalds", "linux"))
    assert (fixture["stars"], fixture["forks"]) != (248274, 64487)
    assert serve.refusal_reasons(OperationalStore(str(db)), str(db)) == []


def test_empty_promoted_database_serves(tmp_path, served):
    # An empty store is honest: no rows to be fabricated, and the snapshot
    # binding still names what the (empty) database was promoted against.
    db = tmp_path / "cvi.db"
    OperationalStore(str(db)).close()
    _promote_snapshot(tmp_path, db)

    assert serve.refusal_reasons(OperationalStore(str(db)), str(db)) == []
    serve.main(["--db", str(db), "--port", "8124"])
    assert served.ports == [8124]


# --------------------------------------------------------------------------- #
# Round 2 (Blocker-3): the guard survives edits a name/telemetry heuristic
# could not catch, because identity is bound to the table's own content.
# --------------------------------------------------------------------------- #


def test_renaming_every_owner_after_promotion_is_refused(tmp_path, served):
    # The lead's explicit bypass: round 1 keyed the guard on (owner, name) ==
    # (stars, forks); this single UPDATE gave it zero reasons while every
    # fabricated row still served. Round 2 binds identity to a content hash
    # of the whole table, so renaming every owner invalidates it too.
    db = tmp_path / "cvi.db"
    store = _store_at(db)
    _promoted_row(store, "numpy", "numpy", stars=32721, forks=12769)
    store.close()
    _promote_snapshot(tmp_path, db)
    assert serve.refusal_reasons(OperationalStore(str(db)), str(db)) == []

    conn = sqlite3.connect(db)
    conn.execute("UPDATE repositories SET owner = owner || '-inc'")
    conn.commit()
    conn.close()

    reasons = serve.refusal_reasons(OperationalStore(str(db)), str(db))
    assert len(reasons) == 1
    assert "matches none of its" in reasons[0]
    with pytest.raises(SystemExit):
        serve.main(["--db", str(db)])
    assert served.ports == []


def test_nudging_telemetry_after_promotion_is_refused(tmp_path, served):
    # The other half of round 1's bypass: nudging every counter by one leaves
    # names intact but still invalidates the content hash.
    db = tmp_path / "cvi.db"
    store = _store_at(db)
    _promoted_row(store, "numpy", "numpy", stars=32721, forks=12769)
    store.close()
    _promote_snapshot(tmp_path, db)

    conn = sqlite3.connect(db)
    conn.execute("UPDATE repositories SET stars = stars + 1")
    conn.commit()
    conn.close()

    reasons = serve.refusal_reasons(OperationalStore(str(db)), str(db))
    assert len(reasons) == 1
    assert "matches none of its" in reasons[0]


def test_a_stored_hash_sharing_every_digit_but_the_last_is_still_refused(tmp_path, served):
    # Round 4, W-003 (mutants M6/M12): the comparison must be full-hash
    # equality, not a truncated prefix check. M12 mutated `stored_hash ==
    # current` to `stored_hash[:1] == current[:1]` (M6: `[:8]`) and the
    # suite stayed green while a tampered database served. Construct the
    # strongest near-miss possible: a stored value identical to the real
    # current hash in every hex digit except the last -- any comparison
    # truncated to fewer than 64 characters accepts it.
    db = tmp_path / "cvi.db"
    store = _store_at(db)
    _promoted_row(store, "numpy", "numpy", stars=32721, forks=12769)
    store.close()
    _promote_snapshot(tmp_path, db)

    current = repositories_dump_hash(str(db))
    near_miss = current[:-1] + ("0" if current[-1] != "0" else "1")
    assert near_miss != current
    assert near_miss[:-1] == current[:-1]

    conn = sqlite3.connect(db)
    conn.execute("UPDATE snapshot_meta SET canonical_dump_hash = ?", (near_miss,))
    conn.commit()
    conn.close()

    reasons = serve.refusal_reasons(OperationalStore(str(db)), str(db))
    assert len(reasons) == 1
    assert "matches none of its" in reasons[0]
    with pytest.raises(SystemExit):
        serve.main(["--db", str(db)])
    assert served.ports == []


def test_manifest_copied_beside_a_foreign_database_is_refused(tmp_path, served):
    # Warning-3 (round 1): snapshot identity accepted ANY manifest.json
    # sitting beside the db file, with no check that it described that
    # database's content -- a sample-data manifest copied beside a
    # thousand-row synthetic database was accepted. Round 2's identity lives
    # inside the db's own snapshot_meta table, never a beside-the-file
    # manifest, so copying one buys nothing.
    src_dir = tmp_path / "source"
    src_dir.mkdir()
    db = src_dir / "cvi.db"
    store = _store_at(db)
    _promoted_row(store, "numpy", "numpy", stars=32721, forks=12769)
    store.close()
    _promote_snapshot(src_dir, db)
    assert serve.refusal_reasons(OperationalStore(str(db)), str(db)) == []

    foreign_dir = tmp_path / "foreign"
    foreign_dir.mkdir()
    foreign_db = foreign_dir / "cvi.db"
    OperationalStore(str(foreign_db)).close()  # never promoted at all
    shutil.copytree(src_dir / "snapshots", foreign_dir / "snapshots")

    reasons = serve.refusal_reasons(OperationalStore(str(foreign_db)), str(foreign_db))
    assert len(reasons) == 1
    assert "no snapshot_meta row" in reasons[0]


# --------------------------------------------------------------------------- #
# Round 3, Blocker-1 (`03` §5): the review's full seed-then-promote sequence
# -- two independent fixes, both required.
# --------------------------------------------------------------------------- #


def test_seed_then_promote_serves_only_the_real_rows(tmp_path, served):
    # Seed the synthetic catalog, THEN promote a real bundle on top (round
    # 2's exact ordering accident: promote upserted but never deleted, so
    # 995 fabricated rows served alongside the real ones). Fix #1 (round 4:
    # promote_atomically builds a fresh temp file and os.replace()s it onto
    # the served path -- there is no old content left to coexist with,
    # because the old file is never opened at all) means only the promoted
    # row survives.
    db = tmp_path / "cvi.db"
    store = _store_at(db)
    _fixture_rows(store, 1000)
    store.close()
    assert count_repos(OperationalStore(str(db))) == 1000

    bundle = {
        "meta": {
            "owner": "acme", "name": "widget", "ecosystem": "pypi",
            "stars": 100, "forks": 10, "monthly_downloads": 0,
            "dependents_count": 0, "elephant_factor": 3,
            "legal_ip_model": "DCO", "ai_posture": "AD_HOC",
            "scorecard_score": 5.0, "active_nonbot_commits_180d": 10,
            "off_platform_reviews": False, "hard_ai_ban": False,
            "promotion_ladder": "AD_HOC", "mean_inline_comments": 0.0,
            "active_unclaimed_gfis": 0, "rr_90": 0.0, "dx_ci_minutes": 0.0,
            "openssf_signals": None,
        },
        "pr_events": [],
    }
    as_of = datetime(2026, 9, 13, tzinfo=timezone.utc)
    manifest = _freeze_real(tmp_path, as_of=as_of.isoformat())
    promote.promote_atomically([bundle], str(db), as_of, tmp_path, manifest)

    store2 = OperationalStore(str(db))
    assert count_repos(store2) == 1  # not 1001 -- the seed did not survive
    assert serve.refusal_reasons(store2, str(db)) == []
    serve.main(["--db", str(db), "--port", "8125"])
    assert served.ports == [8125]

    # Fix #2, proven independently: reintroduce a fixture row some OTHER way
    # (a bug that bypassed the atomic swap, a manual INSERT). The marker
    # guard does not depend on fix #1 having run -- it is a second,
    # independent control, and it must still catch this.
    conn = sqlite3.connect(db)
    row = get_benchmark_catalog(1)[0]
    conn.execute(
        "INSERT INTO repositories (owner, name, ecosystem, domain_category, "
        "stars, forks, openssf_criticality, elephant_factor, legal_ip_model, "
        "ai_posture, score_responsiveness, score_criticality, score_governance, "
        "score_rigor_ai, score_accessibility, leverage_score, friction_score, "
        "matrix_quadrant, overall_cvi, rank_tier, last_indexed_at) VALUES "
        "(?, ?, 'pypi', '', ?, ?, 0, 1, 'DCO', 'AD_HOC', 0, 0, 0, 0, 0, 0, 0, "
        "'SWEET_SPOT', 0, 'A', ?)",
        (row["owner"], row["name"], row["stars"], row["forks"], BENCHMARK_NOW.isoformat()))
    conn.commit()
    conn.close()

    reasons = serve.refusal_reasons(OperationalStore(str(db)), str(db))
    assert any("benchmark_data" in r for r in reasons)
    offending = f"{row['owner']}/{row['name']}"
    assert any(offending in r for r in reasons)
    with pytest.raises(SystemExit):
        serve.main(["--db", str(db)])
    assert served.ports == [8125]  # unchanged: this second serve.main never started
