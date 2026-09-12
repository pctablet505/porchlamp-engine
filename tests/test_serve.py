"""Tests for the production serving entrypoint (scripts/serve.py).

The load-bearing property: serve.py reads an ALREADY-PROMOTED store and does
NOT seed synthetic benchmark_data. An empty DB must serve an empty dashboard
(not the ~1,000 fabricated Gemini fixtures). This locks the "only verified
data reaches the API" rule at the serving boundary.

sqlite3 connections are thread-bound and starlette's TestClient dispatches on
its portal thread, so the store is built inside ``client.portal.call`` (the
same pattern as ``tests/test_api.py::open_client``).

The second half of the file is the served-DB guard of packet W0-4: a store
holding ``cvi/benchmark_data.py`` fixtures, or without a snapshot identity, is
refused before the server starts. ``uvicorn.run`` is the external boundary
those tests stub.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cvi.api import create_app
from cvi.benchmark_data import BENCHMARK_NOW, get_benchmark_catalog
from cvi.ingest import apply_raw_metrics
from cvi.lakehouse import OperationalStore
from cvi.models import CVICompositeResult, Ecosystem, PillarScores, Quadrant, Tier
from cvi.snapshot.manifest import build_manifest, write_manifest
from scripts import serve
from scripts.serve import count_repos, create_serve_app

AS_OF = "2026-09-12T00:00:00+00:00"


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
# The served-DB guard (`03` §5, F1): fabricated fixtures and snapshot-less
# databases never reach a server.
# --------------------------------------------------------------------------- #


def _freeze(data_dir: Path) -> None:
    """Freeze ``data_dir`` with cvi.snapshot's own writer."""
    manifest = build_manifest([], as_of=AS_OF, code_commit="unknown",
                              canonical_dump_hash="pending")
    write_manifest(data_dir, manifest)


def _fixture_rows(store: OperationalStore, count: int) -> list[dict]:
    """Seed ``count`` synthetic benchmark rows, exactly as ``demo_app`` does."""
    rows = get_benchmark_catalog(count)
    store.seed_benchmark_catalog(rows, BENCHMARK_NOW)
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
    db = tmp_path / "cvi.db"
    store = _store_at(db)
    rows = _fixture_rows(store, 3)
    store.close()
    _freeze(tmp_path)  # snapshot identity present: only the fixture rule can fire

    reasons = serve.refusal_reasons(OperationalStore(str(db)), str(db))
    assert len(reasons) == 1
    offending = f"{rows[0]['owner']}/{rows[0]['name']}"
    assert offending in reasons[0]
    assert "benchmark_data" in reasons[0]

    with pytest.raises(SystemExit) as excinfo:
        serve.main(["--db", str(db)])
    assert offending in str(excinfo.value)
    assert served.ports == []  # the server never started


def test_database_without_snapshot_identity_is_refused(tmp_path, served):
    db = tmp_path / "cvi.db"
    store = _store_at(db)
    _promoted_row(store, "numpy", "numpy", stars=32721, forks=12769)
    store.close()

    reasons = serve.refusal_reasons(OperationalStore(str(db)), str(db))
    assert len(reasons) == 1
    assert "numpy/numpy" in reasons[0]
    assert "snapshot" in reasons[0]

    with pytest.raises(SystemExit):
        serve.main(["--db", str(db)])
    assert served.ports == []


def test_promoted_database_with_snapshot_identity_serves(tmp_path, served):
    db = tmp_path / "cvi.db"
    store = _store_at(db)
    _promoted_row(store, "numpy", "numpy", stars=32721, forks=12769)
    _promoted_row(store, "tokio-rs", "tokio", stars=33123, forks=3244)
    store.close()
    _freeze(tmp_path)

    assert serve.refusal_reasons(OperationalStore(str(db)), str(db)) == []
    serve.main(["--db", str(db), "--port", "8123"])
    assert served.ports == [8123]


def test_real_repo_sharing_a_fixture_name_still_serves(tmp_path, served):
    # Over-correction guard: six of the 199 sample repos share a name with a
    # benchmark fixture. A real row differs in its telemetry, so it must serve.
    db = tmp_path / "cvi.db"
    store = _store_at(db)
    _promoted_row(store, "torvalds", "linux", stars=248274, forks=64487)
    store.close()
    _freeze(tmp_path)

    fixture = next(row for row in get_benchmark_catalog(1000)
                   if (row["owner"], row["name"]) == ("torvalds", "linux"))
    assert (fixture["stars"], fixture["forks"]) != (248274, 64487)
    assert serve.refusal_reasons(OperationalStore(str(db)), str(db)) == []


def test_empty_promoted_database_serves(tmp_path, served):
    # An empty store is honest: no rows to be fabricated, and the snapshot
    # manifest still names what the database was promoted against.
    db = tmp_path / "cvi.db"
    OperationalStore(str(db)).close()
    _freeze(tmp_path)

    assert serve.refusal_reasons(OperationalStore(str(db)), str(db)) == []
    serve.main(["--db", str(db), "--port", "8124"])
    assert served.ports == [8124]
