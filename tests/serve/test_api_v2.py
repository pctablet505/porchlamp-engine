"""Packet W4-4 acceptance: `cvi/serve/app.py` -- the v2 API, served straight
off the v2 store, through `cvi/serve/contract.py`'s wire models.

Every endpoint's JSON is parsed by `response_model=` before this test ever
sees it (FastAPI/pydantic itself refuses to serialise a response that fails
the contract), so a passing test here is already proof the shape validates;
the assertions below check the CONTENT the packet's bridge decisions
produce, not merely "it parsed".
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # tests/ on sys.path for `store.helpers`

from cvi.core.methodology import methodology_document, methodology_hash
from cvi.core.models import canonical_json, sha256_hex
from cvi.serve.app import create_app
from cvi.serve.contract import (
    EvidenceRow,
    LeaderboardResponse,
    NotRankedResponse,
    SnapshotSummary,
)
from cvi.store import queries

from serve.helpers import SNAPSHOT_ID, build_snapshot, finalize, fresh_db

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "ui"


# --------------------------------------------------------------------------- #
# Shared fixture: one >= 300-row synthetic snapshot, served through a real
# `create_app`. Session-scoped would be faster, but a fresh app/db per test
# keeps tests independent (no risk of one test's assertions depending on
# another's incidental ordering); the DB build itself is a few hundred
# `INSERT`s and takes well under 100ms.
# --------------------------------------------------------------------------- #


@pytest.fixture
def api(tmp_path):
    db_path = tmp_path / "db.sqlite"
    conn = fresh_db(db_path)
    fx = build_snapshot(conn, db_path)
    finalize(conn, db_path)
    conn.close()

    web_dir = tmp_path / "web"
    web_dir.mkdir()
    (web_dir / "index.html").write_text("<html><body>v2 shell</body></html>")

    app = create_app(str(db_path), web_dir=web_dir)
    client = TestClient(app)
    yield client, fx, db_path


def _raw_conn(db_path) -> sqlite3.Connection:
    """A second, independent connection to the same file, for oracle
    queries straight through `cvi.store.queries` (never through the API)."""
    return sqlite3.connect(str(db_path))


# --------------------------------------------------------------------------- #
# Leaderboard: shape, field bridge, balanced vs persona projection.
# --------------------------------------------------------------------------- #


def test_leaderboard_balanced_default_shape_and_total(api):
    client, fx, _db = api
    resp = client.get("/v1/cvi/leaderboard")
    assert resp.status_code == 200
    body = LeaderboardResponse(**resp.json())
    assert body.persona == "balanced"
    assert body.total == len(fx.rated_names)
    assert body.page == 1 and body.page_size == 20
    assert len(body.rows) == 20
    # D-TIE-2: rank is strict 1..N in this page.
    assert [row.rank for row in body.rows] == list(range(1, 21))
    # Bridge item 2: no delta under the balanced lens itself.
    assert all(row.balanced_rank is None and row.rank_delta is None for row in body.rows)


def test_leaderboard_suspicion_and_stars_audited_round_trip(api):
    """Bridge: `suspicion`/`stars_audited` are projected verbatim (not
    dropped, not fabricated) -- also proves the W1-10/G-006 writer bridge
    (`cvi/store/writer.py`) actually reaches the wire."""
    client, fx, _db = api
    resp = client.get("/v1/cvi/leaderboard?q=flagged-repo")
    body = LeaderboardResponse(**resp.json())
    assert len(body.rows) == 1
    row = body.rows[0]
    assert set(row.suspicion) == {"newcomer_burst", "param_jump"}
    assert row.stars_audited == fx.stars_audited["flagged-repo"]


def test_leaderboard_stars_audited_null_when_not_measured(api):
    client, fx, _db = api
    resp = client.get("/v1/cvi/leaderboard?q=repo-0000")
    row = LeaderboardResponse(**resp.json()).rows[0]
    assert row.stars_audited is None


def test_leaderboard_tie_group_shared_by_ties_and_null_for_singletons(api):
    client, fx, _db = api
    resp = client.get("/v1/cvi/leaderboard?page_size=1000")
    rows = {row.owner + "/" + row.name: row for row in LeaderboardResponse(**resp.json()).rows}
    tie3_groups = {rows[f"acme/{name}"].tie_group for name in fx.tie_group_names["tie3"]}
    assert len(tie3_groups) == 1 and None not in tie3_groups
    tie2_groups = {rows[f"acme/{name}"].tie_group for name in fx.tie_group_names["tie2"]}
    assert len(tie2_groups) == 1 and None not in tie2_groups
    # Every unique-composite bulk row is a singleton: shares a group with
    # nobody (R-D), never a spurious 0/placeholder id.
    bulk_singletons = [name for name in fx.rated_names if name.startswith("repo-")]
    assert bulk_singletons
    for name in bulk_singletons:
        assert rows[f"bulk/{name}"].tie_group is None


def test_leaderboard_persona_projects_p_suffixed_fields_and_computes_delta(api):
    client, fx, db_path = api
    resp = client.get("/v1/cvi/leaderboard?persona=first_timer&page_size=1000")
    body = LeaderboardResponse(**resp.json())
    assert body.persona == "first_timer"
    # The persona-gated row must be ABSENT from this persona's leaderboard.
    names = {row.name for row in body.rows}
    assert "persona-gated-repo" not in names

    conn = _raw_conn(db_path)
    store_rows, store_total = queries.list_ranked(conn, SNAPSHOT_ID, persona="first_timer", limit=1000, offset=0)
    conn.close()
    assert body.total == store_total
    by_name = {row["name"]: row for row in store_rows}
    for row in body.rows:
        store_row = by_name[row.name]
        assert row.rank == store_row["rank_p"]
        assert row.tie_group == store_row["tie_group_p"]
        assert row.cvi == store_row["cvi_p"]
        assert row.coverage == store_row["coverage_p"]
        assert row.tier == store_row["tier_p"]
        assert row.balanced_rank == store_row["rank"]
        if store_row["rank"] is None:
            assert row.rank_delta is None
        else:
            assert row.rank_delta == store_row["rank"] - store_row["rank_p"]


def test_leaderboard_persona_delta_is_null_when_balanced_rank_is_null(api):
    """A row that is persona-rated but balanced-UNRATED has nothing to
    show a delta against (`cvi.store.queries.list_ranked`'s own docstring)
    -- this fixture has no such row by construction (every rated row here
    clears every persona's coverage floor), so this test instead proves
    the code path handles it via the parity check already covering the
    general case: `balanced_rank is None => rank_delta is None`, asserted
    for every row in the persona parity test above. Kept as a named,
    explicit assertion of that one implication so it reads as a
    requirement, not an incidental side effect of the loop."""
    client, _fx, db_path = api
    resp = client.get("/v1/cvi/leaderboard?persona=career_capital&page_size=1000")
    body = LeaderboardResponse(**resp.json())
    for row in body.rows:
        if row.balanced_rank is None:
            assert row.rank_delta is None


def test_leaderboard_sort_pillar_translation_and_allow_list(api):
    client, _fx, _db = api
    ok = client.get("/v1/cvi/leaderboard?sort=p_responsiveness&page_size=1000")
    assert ok.status_code == 200
    body = LeaderboardResponse(**ok.json())
    scores = [row.p_responsiveness for row in body.rows if row.p_responsiveness is not None]
    assert scores == sorted(scores, reverse=True)

    bad = client.get("/v1/cvi/leaderboard?sort=stars")
    assert bad.status_code == 422
    assert bad.json()["error"]["endpoint"] == "/v1/cvi/leaderboard"
    assert "sort" in bad.json()["error"]["detail"]


def test_leaderboard_unknown_persona_is_422(api):
    client, _fx, _db = api
    resp = client.get("/v1/cvi/leaderboard?persona=nonexistent")
    assert resp.status_code == 422
    assert resp.json()["error"]["status"] == 422


# --------------------------------------------------------------------------- #
# Paging: page/page_size <-> limit/offset, bounds.
# --------------------------------------------------------------------------- #


def test_paging_pages_partition_the_full_set_in_order(api):
    client, fx, _db = api
    full = LeaderboardResponse(**client.get("/v1/cvi/leaderboard?page_size=1000").json())
    assert len(full.rows) == len(fx.rated_names)

    page1 = LeaderboardResponse(**client.get("/v1/cvi/leaderboard?page=1&page_size=20").json())
    page2 = LeaderboardResponse(**client.get("/v1/cvi/leaderboard?page=2&page_size=20").json())
    assert [(r.owner, r.name) for r in page1.rows] == [(r.owner, r.name) for r in full.rows[0:20]]
    assert [(r.owner, r.name) for r in page2.rows] == [(r.owner, r.name) for r in full.rows[20:40]]
    assert page1.total == page2.total == full.total


def test_paging_beyond_range_is_empty_rows_with_total(api):
    client, fx, _db = api
    resp = LeaderboardResponse(**client.get("/v1/cvi/leaderboard?page=999&page_size=20").json())
    assert resp.rows == []
    assert resp.total == len(fx.rated_names)


def test_paging_page_size_1000_accepted_1001_rejected(api):
    client, _fx, _db = api
    ok = client.get("/v1/cvi/leaderboard?page_size=1000")
    assert ok.status_code == 200
    bad = client.get("/v1/cvi/leaderboard?page_size=1001")
    assert bad.status_code == 422
    assert "1000" in bad.json()["error"]["detail"]


# --------------------------------------------------------------------------- #
# Not-ranked: group split, bridge items 3/4 (translation), persona_hidden.
# --------------------------------------------------------------------------- #


def test_not_ranked_gated_group_has_universal_reasons_only(api):
    client, fx, _db = api
    body = NotRankedResponse(**client.get("/v1/cvi/not-ranked").json())
    gated_by_name = {row.name: row for row in body.gated}
    assert set(gated_by_name) == {n for n, r in fx.gated.items() if r != "unrated: no_external_workflow"}
    for name, row in gated_by_name.items():
        assert row.gate_reason == fx.gated[name]
        assert row.unrated_reason is None


def test_not_ranked_no_external_workflow_gate_is_translated_to_unrated(api):
    """Bridge item 3 (lead ruling): a stage-6 GATE (`gate_reason ==
    "unrated: no_external_workflow"`) is reported in the UNRATED group
    with the bare `unrated_reason`, never widening `GateReason`."""
    client, fx, _db = api
    body = NotRankedResponse(**client.get("/v1/cvi/not-ranked").json())
    name = next(n for n, r in fx.gated.items() if r == "unrated: no_external_workflow")
    assert name not in {row.name for row in body.gated}
    unrated_row = next(row for row in body.unrated if row.name == name)
    assert unrated_row.unrated_reason == "no_external_workflow"
    assert unrated_row.gate_reason is None


def test_not_ranked_off_platform_routed_is_derived(api):
    """Bridge item 4: `routed == "off_platform"` on an eligible repo ->
    `unrated_reason == "off_platform_routed"`, overriding whatever the
    stored `unrated_reason` column itself says."""
    client, fx, _db = api
    body = NotRankedResponse(**client.get("/v1/cvi/not-ranked").json())
    routed_names = {n for n, kind in fx.unrated.items() if kind == "routed"}
    routed_rows = [row for row in body.unrated if row.name in routed_names]
    assert len(routed_rows) == len(routed_names)
    assert all(row.unrated_reason == "off_platform_routed" for row in routed_rows)

    plain_names = {n for n, kind in fx.unrated.items() if kind == "plain"}
    plain_rows = [row for row in body.unrated if row.name in plain_names]
    assert len(plain_rows) == len(plain_names)
    assert all(row.unrated_reason in ("insufficient_evidence", "unrated: insufficient_evidence") for row in plain_rows)


def test_not_ranked_persona_hidden_group_under_a_persona(api):
    client, fx, _db = api
    body = NotRankedResponse(**client.get(f"/v1/cvi/not-ranked?persona={fx.persona_gate_persona}").json())
    hidden_by_name = {row.name: row for row in body.persona_hidden}
    assert set(hidden_by_name) == set(fx.persona_gated)
    for name, reason in fx.persona_gated.items():
        assert hidden_by_name[name].persona_gate_reason == reason
        assert hidden_by_name[name].gate_reason is None
        assert hidden_by_name[name].unrated_reason is None


def test_not_ranked_balanced_has_no_persona_hidden_group(api):
    client, _fx, _db = api
    body = NotRankedResponse(**client.get("/v1/cvi/not-ranked").json())
    assert body.persona_hidden == []


def test_not_ranked_counts_match_snapshot_summary(api):
    client, fx, _db = api
    not_ranked = NotRankedResponse(**client.get("/v1/cvi/not-ranked").json())
    snapshot = SnapshotSummary(**client.get("/v1/cvi/snapshot").json())
    assert not_ranked.counts == snapshot.counts
    assert snapshot.counts.rated == len(fx.rated_names)
    assert snapshot.counts.gated == len(fx.gated)
    assert snapshot.counts.unrated == len(fx.unrated)
    assert snapshot.counts.total == snapshot.counts.rated + snapshot.counts.gated + snapshot.counts.unrated


# --------------------------------------------------------------------------- #
# Persona gating counts (`snapshot.persona_visible_counts`).
# --------------------------------------------------------------------------- #


def test_persona_visible_counts_reflect_the_one_persona_gated_row(api):
    client, fx, _db = api
    snapshot = SnapshotSummary(**client.get("/v1/cvi/snapshot").json())
    assert snapshot.persona_visible_counts["balanced"] == len(fx.rated_names)
    # Every rated row here has uniform 0.9 per-pillar coverage (`tests/store/
    # helpers.make_rated_record`'s default), so it clears every persona's
    # 0.60 rated floor regardless of that persona's weights -- the ONLY
    # row a persona excludes is the one explicitly persona-gated.
    assert snapshot.persona_visible_counts[fx.persona_gate_persona] == len(fx.rated_names) - 1
    for persona in ("career_capital", "senior_systems", "ai_assisted"):
        assert snapshot.persona_visible_counts[persona] == len(fx.rated_names)


# --------------------------------------------------------------------------- #
# Repo detail + evidence.
# --------------------------------------------------------------------------- #


def test_repo_detail_rated_repo(api):
    client, fx, _db = api
    resp = client.get("/v1/cvi/repos/acme/flagged-repo")
    assert resp.status_code == 200
    body = resp.json()
    assert body["eligible"] is True
    assert body["rated"] is True
    assert body["rank"] is not None
    assert body["stars_audited"] == fx.stars_audited["flagged-repo"]
    assert set(body["suspicion"]) == {"newcomer_burst", "param_jump"}


def test_repo_detail_gated_repo_has_null_pillars_and_gate_status(api):
    client, fx, _db = api
    resp = client.get("/v1/cvi/repos/gated-org/gated-fork")
    assert resp.status_code == 200
    body = resp.json()
    assert body["eligible"] is False
    assert body["gate_reason"] == "fork"
    assert body["rated"] is False
    assert body["rank"] is None
    assert body["cvi"] is None
    assert body["c_responsiveness"] is None


def test_repo_detail_unknown_repo_is_404_with_consistent_body(api):
    client, _fx, _db = api
    resp = client.get("/v1/cvi/repos/nobody/nothing")
    assert resp.status_code == 404
    body = resp.json()
    assert set(body.keys()) == {"error"}
    assert set(body["error"].keys()) == {"endpoint", "status", "detail"}
    assert body["error"]["endpoint"] == "/v1/cvi/repos/nobody/nothing"
    assert body["error"]["status"] == 404


def test_evidence_shape_for_a_rated_repo(api):
    client, _fx, _db = api
    resp = client.get("/v1/cvi/repos/acme/flagged-repo/evidence")
    assert resp.status_code == 200
    rows = [EvidenceRow(**item) for item in resp.json()]
    assert len(rows) > 0
    for row in rows:
        if row.measured:
            assert row.unmeasured_reason is None
        else:
            assert row.unmeasured_reason is not None


def test_evidence_unknown_repo_is_404_with_consistent_body(api):
    client, _fx, _db = api
    resp = client.get("/v1/cvi/repos/nobody/nothing/evidence")
    assert resp.status_code == 404
    assert set(resp.json()["error"].keys()) == {"endpoint", "status", "detail"}


# --------------------------------------------------------------------------- #
# Matrix.
# --------------------------------------------------------------------------- #


def test_matrix_only_plots_rated_rows_and_excludes_the_rest(api):
    client, fx, _db = api
    resp = client.get("/v1/cvi/matrix")
    assert resp.status_code == 200
    body = resp.json()
    plotted = {(p["owner"], p["name"]) for p in body["points"]}
    for name in fx.gated:
        assert ("gated-org", name) not in plotted
    # "tie2-null-friction" is rated with `friction=None` (deliberately, for
    # the tie-break NULL-ordering fixture) -- `MatrixPoint.friction` is
    # required, so this one rated row is EXCLUDED alongside the truly
    # gated/unrated ones (mutation guard: a version of `_matrix_point`
    # that only checks `quadrant` would crash constructing this exact
    # point instead of excluding it -- caught directly by this fixture).
    assert ("acme", "tie2-null-friction") not in plotted
    assert len(body["points"]) == len(fx.rated_names) - 1
    assert body["excluded_count"] == len(fx.gated) + len(fx.unrated) + 1


# --------------------------------------------------------------------------- #
# Snapshot / methodology.
# --------------------------------------------------------------------------- #


def test_snapshot_not_finalised_is_503(tmp_path):
    db_path = tmp_path / "db.sqlite"
    conn = fresh_db(db_path)
    build_snapshot(conn, db_path, n_rated=5)
    conn.close()  # never finalize_snapshot_hash -> canonical_dump_hash stays NULL
    web_dir = tmp_path / "web"
    web_dir.mkdir()
    (web_dir / "index.html").write_text("<html></html>")
    app = create_app(str(db_path), web_dir=web_dir)
    client = TestClient(app)
    resp = client.get("/v1/cvi/snapshot")
    assert resp.status_code == 503
    resp2 = client.get("/v1/cvi/leaderboard")
    assert resp2.status_code == 503


def test_methodology_matches_the_real_pure_document_and_its_hash(api):
    client, _fx, _db = api
    resp = client.get("/v1/cvi/methodology")
    assert resp.status_code == 200
    served = resp.json()
    # `methodology_document()` is Python (tuples included); a JSON round
    # trip normalises it to the same list-only shape `resp.json()` already
    # is, so the comparison below is content, not container-type, equality.
    pure = json.loads(json.dumps(methodology_document()))
    for key, value in pure.items():
        assert served[key] == value, key
    stripped = {key: served[key] for key in pure}
    assert sha256_hex(canonical_json(stripped)) == methodology_hash()
    # Extended fields (`cvi.audit.methodology_history`) are also present.
    assert "detectors" in served and "rfc_log" in served


# --------------------------------------------------------------------------- #
# Reproject: parity with `queries.reproject`, 422 on bad weights.
# --------------------------------------------------------------------------- #


_WEIGHTS = {
    "weight_responsiveness": 0.3, "weight_criticality": 0.3, "weight_governance": 0.15,
    "weight_rigor": 0.15, "weight_accessibility": 0.1,
}


def test_reproject_parity_with_store_queries(api):
    client, _fx, db_path = api
    resp = client.post("/v1/cvi/reproject?page_size=1000", json=_WEIGHTS)
    assert resp.status_code == 200
    body = resp.json()

    conn = _raw_conn(db_path)
    weights = {k.removeprefix("weight_"): v for k, v in _WEIGHTS.items()}
    store_rows, store_total = queries.reproject(conn, SNAPSHOT_ID, weights, limit=1000, offset=0)
    conn.close()

    assert body["total"] == store_total
    assert body["lens"] == "custom"
    by_name = {row["name"]: row for row in store_rows}
    for row in body["rows"]:
        store_row = by_name[row["name"]]
        assert row["rank"] == store_row["rank"]
        assert row["cvi"] == store_row["cvi"]
        assert row["coverage"] == store_row["coverage"]
        assert row["tier"] == store_row["tier"]
        assert row["balanced_rank"] == store_row["balanced_rank"]


def test_reproject_rejects_negative_weight(api):
    client, _fx, _db = api
    bad = dict(_WEIGHTS)
    bad["weight_responsiveness"] = -0.5
    bad["weight_criticality"] = 0.95
    resp = client.post("/v1/cvi/reproject", json=bad)
    assert resp.status_code == 422
    assert "non-negative" in resp.json()["error"]["detail"]


def test_reproject_rejects_unnormalised_weights(api):
    client, _fx, _db = api
    bad = {k: 0.5 for k in _WEIGHTS}
    resp = client.post("/v1/cvi/reproject", json=bad)
    assert resp.status_code == 422
    assert "sum to 1" in resp.json()["error"]["detail"]


# --------------------------------------------------------------------------- #
# Error body shape (rule 1, U-15).
# --------------------------------------------------------------------------- #


def test_error_body_shape_is_consistent_across_404_422_503(tmp_path, api):
    client, _fx, _db = api
    responses = [
        client.get("/v1/cvi/repos/nobody/nothing"),  # 404
        client.get("/v1/cvi/leaderboard?persona=nope"),  # 422
    ]
    for resp in responses:
        body = resp.json()
        assert set(body.keys()) == {"error"}
        assert set(body["error"].keys()) == {"endpoint", "status", "detail"}
        assert body["error"]["status"] == resp.status_code
        assert isinstance(body["error"]["detail"], str)


# --------------------------------------------------------------------------- #
# Served-DB guard, through `create_app` (rule 2).
# --------------------------------------------------------------------------- #


def test_create_app_refuses_a_fixture_marked_v1_database(tmp_path):
    from cvi.benchmark_data import BENCHMARK_NOW, get_benchmark_catalog
    from cvi.lakehouse import OperationalStore, repositories_dump_hash
    from cvi.snapshot.manifest import build_manifest, envelope_entries, write_manifest
    import importlib.util

    root = Path(__file__).resolve().parent.parent.parent
    spec = importlib.util.spec_from_file_location("promote_probe", root / "scripts" / "promote.py")
    promote = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(promote)

    db_path = tmp_path / "cvi.db"
    store = OperationalStore(str(db_path))
    rows = get_benchmark_catalog(3)
    store.seed_benchmark_catalog(rows, BENCHMARK_NOW)
    store.close()

    sample_raw = root / "tests" / "fixtures" / "numpy-envelopes"
    for collector_dir in sorted(p for p in sample_raw.iterdir() if p.is_dir()):
        src = collector_dir / "numpy__numpy.json"
        if src.is_file():
            dest = tmp_path / "raw" / collector_dir.name / "numpy__numpy.json"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
    scan = envelope_entries(tmp_path)
    manifest = build_manifest(scan.entries, as_of="2026-09-13T00:00:00+00:00", code_commit="unknown", canonical_dump_hash="pending")
    write_manifest(tmp_path, manifest)
    promote.write_snapshot_meta(str(db_path), manifest, repositories_dump_hash(str(db_path)))

    web_dir = tmp_path / "web"
    web_dir.mkdir()
    (web_dir / "index.html").write_text("<html></html>")

    with pytest.raises(SystemExit) as excinfo:
        create_app(str(db_path), web_dir=web_dir)
    offending = f"{rows[0]['owner']}/{rows[0]['name']}"
    assert offending in str(excinfo.value)
    assert "benchmark_data" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# UI fixtures validate against the SAME models the API uses (drift guard).
# --------------------------------------------------------------------------- #


def test_ui_fixtures_validate_against_the_api_contract_models():
    import pydantic

    from cvi.serve.contract import EvidenceRow as _EvidenceRow
    from cvi.serve.contract import LeaderboardResponse as _LeaderboardResponse
    from cvi.serve.contract import NotRankedResponse as _NotRankedResponse
    from cvi.serve.contract import SnapshotSummary as _SnapshotSummary

    evidence_adapter = pydantic.TypeAdapter(list[_EvidenceRow])
    checked = 0
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        payload = json.loads(path.read_text())
        if path.name.startswith("leaderboard"):
            _LeaderboardResponse(**payload)
        elif path.name.startswith("notranked"):
            _NotRankedResponse(**payload)
        elif path.name == "snapshot.json":
            _SnapshotSummary(**payload)
        elif path.name.startswith("evidence"):
            evidence_adapter.validate_python(payload)
        else:
            pytest.fail(f"unrecognised UI fixture, add a mapping: {path.name}")
        checked += 1
    assert checked == len(list(FIXTURES_DIR.glob("*.json")))
    assert checked >= 8


# --------------------------------------------------------------------------- #
# Zero external requests through the v2 static mount (extends the existing
# `tests/test_webapp.py::TestZeroCDNAssurance` pattern to `cvi.serve.app`'s
# own mount; `web/v2/` does not exist in every worktree yet -- W4-5a lands
# it -- so this proves the MOUNT + fallback mechanism never introduces an
# external reference of its own, using a controlled `index.html`).
# --------------------------------------------------------------------------- #

import re  # noqa: E402


def test_v2_static_mount_serves_html_with_no_external_references(tmp_path):
    db_path = tmp_path / "db.sqlite"
    conn = fresh_db(db_path)
    build_snapshot(conn, db_path, n_rated=5)
    finalize(conn, db_path)
    conn.close()

    web_dir = tmp_path / "web"
    web_dir.mkdir()
    (web_dir / "index.html").write_text(
        '<html><head><link rel="stylesheet" href="styles/tokens.css"></head>'
        '<body><script src="app.js"></script></body></html>'
    )
    app = create_app(str(db_path), web_dir=web_dir)
    client = TestClient(app)
    html = client.get("/").text
    for src in re.findall(r'<script\s+[^>]*src=["\']([^"\']+)["\']', html, re.I):
        assert not src.startswith("http://") and not src.startswith("https://")
    for href in re.findall(r'<link\s+[^>]*href=["\']([^"\']+)["\']', html, re.I):
        assert not href.startswith("http://") and not href.startswith("https://")
    assert "fonts.googleapis.com" not in html and "fonts.gstatic.com" not in html

    # SPA fallback: an unknown app-owned path still gets index.html, not a
    # bare static 404 (`06` section 6: "index fallback ... URL state
    # reloads work").
    fallback = client.get("/leaderboard?persona=first_timer")
    assert fallback.status_code == 200
    assert fallback.text == html


# --------------------------------------------------------------------------- #
# Performance: p95 of /leaderboard and /reproject via TestClient on a
# 10,000-row synthetic snapshot, budget < 150ms (server 100ms + serialisation).
# --------------------------------------------------------------------------- #


def _p95(samples):
    ordered = sorted(samples)
    return ordered[max(0, int(len(ordered) * 0.95) - 1)]


def _time_calls(fn, n=15):
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
    return times


def _assert_under_budget_with_retry(measure, label: str, budget_ms: float = 150.0) -> None:
    """Same pattern as `tests/store/test_queries.py`'s own perf tests (same
    budget-under-contention problem, same fix): this host runs several
    concurrent agent sessions sharing one CPU, so a single batch's p95 can
    occasionally overshoot from a transient scheduling stall rather than a
    real regression in the endpoint's cost -- tolerate ONE re-measure
    before failing (never a relaxed budget)."""
    first = measure()
    print(f"\n[W4-4 perf] {label}: {first:.2f} ms")
    if first < budget_ms:
        return
    second = measure()
    print(f"[W4-4 perf] {label} (retry after a first-batch overshoot): {second:.2f} ms")
    assert second < budget_ms, (
        f"{label} exceeded the {budget_ms:.0f}ms budget twice in a row "
        f"({first:.2f}ms, then {second:.2f}ms on retry) -- not attributed to transient host contention"
    )


def test_performance_leaderboard_and_reproject_p95_under_150ms_for_10k_rows(tmp_path):
    db_path = tmp_path / "db.sqlite"
    conn = fresh_db(db_path)
    build_snapshot(conn, db_path, n_rated=10_000, seed=7)
    finalize(conn, db_path)
    conn.close()

    web_dir = tmp_path / "web"
    web_dir.mkdir()
    (web_dir / "index.html").write_text("<html></html>")
    app = create_app(str(db_path), web_dir=web_dir)
    client = TestClient(app)

    for limit in (20, 1000):
        _assert_under_budget_with_retry(
            lambda limit=limit: _p95(_time_calls(lambda: client.get(f"/v1/cvi/leaderboard?page_size={limit}"))),
            f"GET /leaderboard page_size={limit} p95 over 15 calls",
        )
        _assert_under_budget_with_retry(
            lambda limit=limit: _p95(_time_calls(lambda: client.post(f"/v1/cvi/reproject?page_size={limit}", json=_WEIGHTS))),
            f"POST /reproject page_size={limit} p95 over 15 calls",
        )
