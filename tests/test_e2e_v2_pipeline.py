"""End-to-end test for the v2 snapshot pipeline: raw -> derive -> snapshot build -> score -> store -> serve."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from porchlamp.collect.envelope import write_envelope
from porchlamp.serve.app import create_app
from porchlamp.snapshot.build_cmd import build_snapshot_dir
from porchlamp.snapshot.store_cmd import score_snapshot_dir, write_scored_snapshot
from porchlamp.store.migrations import apply_migrations
from porchlamp.store.schema import connect as connect_store

ROOT = Path(__file__).resolve().parent.parent


def _load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


derive_mod = _load_script("derive", "derive.py")
verify_mod = _load_script("verify", "verify.py")
run_all_mod = _load_script("run_all", "run_all.py")

NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)
OWNER, NAME = "acme", "widget"


def _seed_raw_envelopes(data_dir: Path) -> None:
    """Write minimal valid raw collector envelopes for acme/widget."""
    key = f"{OWNER}__{NAME}"
    at = "2026-09-11T00:00:00Z"

    def write(collector: str, target_key: str, data: object) -> None:
        p = data_dir / "raw" / collector / f"{target_key}.json"
        write_envelope(p, f"test:{collector}", data, at)

    write(
        "github_meta",
        key,
        {
            "stars": 1000,
            "forks": 50,
            "pushed_at": "2026-09-01T00:00:00Z",
            "archived": False,
            "license_spdx": "Apache-2.0",
            "default_branch": "main",
        },
    )
    write(
        "pr_events",
        key,
        [
            {
                "number": 1,
                "created_at": "2026-06-01T00:00:00Z",
                "closed_at": "2026-06-02T00:00:00Z",
                "merged_at": "2026-06-02T00:00:00Z",
                "author_login": "alice",
                "author_association": "NONE",
                "comments_total": 4,
                "state": "closed",
            },
            {
                "number": 2,
                "created_at": "2026-06-03T00:00:00Z",
                "closed_at": "2026-06-04T00:00:00Z",
                "merged_at": None,
                "author_login": "bob",
                "author_association": "MEMBER",
                "comments_total": 2,
                "state": "closed",
            },
        ],
    )
    write("pr_reviews", key, {"1": "2026-06-01T12:00:00Z", "2": None})
    write(
        "contributors",
        key,
        [
            {"login": "alice", "contributions": 40},
            {"login": "bob", "contributions": 40},
            {"login": "carol", "contributions": 20},
        ],
    )
    write(
        "commits_180d",
        key,
        [
            {"author_login": "alice", "date": "2026-08-01T00:00:00Z"},
            {"author_login": "alice", "date": "2026-08-02T00:00:00Z"},
            {"author_login": "bob", "date": "2026-08-03T00:00:00Z"},
        ],
    )
    write(
        "gfi",
        key,
        [
            {
                "number": 10,
                "assignee": None,
                "updated_at": "2026-09-07T00:00:00Z",
                "labels": ["good-first-issue"],
            }
        ],
    )
    write(
        "actions_runs",
        key,
        [
            {"head_sha": "c2", "status": "completed", "conclusion": "success", "run_duration_seconds": 1200, "created_at": "2026-09-02T00:00:00Z"},
            {"head_sha": "c1", "status": "completed", "conclusion": "success", "run_duration_seconds": 600, "created_at": "2026-09-01T00:00:00Z"},
        ],
    )
    write(
        "depsdev",
        key,
        {
            "stars": 1000,
            "forks": 50,
            "open_issues": 5,
            "license": "Apache-2.0",
            "usedby_count": 777,
            "scorecard_overall": 8.8,
        },
    )
    write("scorecard", key, {"score": 9.0, "checks": {"Branch-Protection": 10}})
    write("tree", key, {"paths": ["pyproject.toml", "LICENSE", "CONTRIBUTING.md", "README.md"]})
    write("downloads", f"pypi__{NAME}", {"monthly": 5000})
    write("dependents", key, {"source": "deps.dev", "deduped": 777})


def test_e2e_v2_pipeline_build_score_store_serve(tmp_path: Path, monkeypatch) -> None:
    """Full v2 pipeline: derive -> build_snapshot_dir -> score -> write_scored_snapshot -> FastAPI TestClient."""
    monkeypatch.setattr(derive_mod, "DATA_DIR", tmp_path)
    _seed_raw_envelopes(tmp_path)

    # Step 1: Derive repository metrics
    derived = derive_mod.derive_repo(
        OWNER,
        NAME,
        derive_mod.Ecosystem.PYPI,
        tmp_path,
        fetch=False,
        now=NOW,
    )
    derived_dir = tmp_path / "derived"
    derived_dir.mkdir(parents=True, exist_ok=True)
    derived_path = derived_dir / f"{OWNER}__{NAME}.json"
    derived_path.write_text(json.dumps(derived, indent=2, sort_keys=True), encoding="utf-8")

    # Assert relative provenance paths are recorded when data_dir == DATA_DIR
    prov_sources = [p["source"] for p in derived["provenance"].values() if p.get("source")]
    assert "raw/github_meta/acme__widget.json" in prov_sources
    for src_path in prov_sources:
        if isinstance(src_path, str) and src_path.startswith("raw/"):
            assert not Path(src_path).is_absolute(), f"Expected relative path, got {src_path}"

    # Step 2: Build v2 snapshot directory
    snapshot_dir = tmp_path / "snapshots_v2" / "e2e-v2"
    frozen = build_snapshot_dir(
        tmp_path,
        snapshot_dir,
        snapshot_id="e2e-v2",
        as_of=NOW,
        code_commit_sha="0123456",
    )
    assert frozen.snapshot_id == "e2e-v2"
    assert len(frozen.bundles) == 1
    assert frozen.bundles[0].repo.owner == OWNER
    assert frozen.bundles[0].repo.name == NAME
    ev = frozen.gate_evidence[f"{OWNER}/{NAME}"]
    assert ev.manifest_found is True
    assert ev.license_file_present is True
    assert ev.vitality_commits_180d == 3

    # Step 3: Score snapshot directory and write to SQLite
    scored, frozen_loaded = score_snapshot_dir(snapshot_dir)
    db_path = tmp_path / "porchlamp.db"
    conn = connect_store(db_path)
    try:
        apply_migrations(conn, applied_at=NOW.isoformat())
        write_scored_snapshot(
            conn,
            db_path,
            scored,
            frozen_loaded,
            code_commit="0123456",
        )
    finally:
        conn.close()

    # Step 4: Serve via FastAPI and verify all 5 v1/porchlamp endpoints return HTTP 200
    app = create_app(db_path, web_dir=tmp_path)
    client = TestClient(app)

    res_snap = client.get("/v1/porchlamp/snapshot")
    assert res_snap.status_code == 200
    assert res_snap.json()["snapshot_id"] == "e2e-v2"

    res_lead = client.get("/v1/porchlamp/leaderboard")
    assert res_lead.status_code == 200
    assert "rows" in res_lead.json()

    res_not_ranked = client.get("/v1/porchlamp/not-ranked")
    assert res_not_ranked.status_code == 200
    assert "unrated" in res_not_ranked.json()
    assert "gated" in res_not_ranked.json()

    res_matrix = client.get("/v1/porchlamp/matrix")
    assert res_matrix.status_code == 200
    assert "points" in res_matrix.json()

    res_repo = client.get("/v1/porchlamp/repos/acme/widget")
    assert res_repo.status_code == 200
    assert res_repo.json()["owner"] == "acme"
    assert res_repo.json()["name"] == "widget"
    assert res_repo.json()["name"] == "widget"


def test_e2e_run_all_freeze_and_promote_serves_v2(tmp_path: Path, monkeypatch) -> None:
    """run_all.freeze_and_promote promotes both v1 and v2 tables cleanly with zero guard refusals."""
    monkeypatch.setattr(derive_mod, "DATA_DIR", tmp_path)
    _seed_raw_envelopes(tmp_path)

    candidates = tmp_path / "raw" / "candidates.jsonl"
    candidates.parent.mkdir(parents=True, exist_ok=True)
    candidates.write_text(
        json.dumps({"owner": OWNER, "name": NAME, "ecosystem": "pypi"}) + "\n",
        encoding="utf-8",
    )

    derived = derive_mod.derive_repo(
        OWNER,
        NAME,
        derive_mod.Ecosystem.PYPI,
        tmp_path,
        fetch=False,
        now=NOW,
    )
    derived_dir = tmp_path / "derived"
    derived_dir.mkdir(parents=True, exist_ok=True)
    (derived_dir / f"{OWNER}__{NAME}.json").write_text(
        json.dumps(derived, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    # Run verify_repo so verified/acme__widget.json is created for v1 promotion
    report = verify_mod.verify_repo(OWNER, NAME, data_dir=tmp_path)
    assert report["clean"] is True

    db_path = tmp_path / "porchlamp_promoted.db"
    rc = run_all_mod.freeze_and_promote(
        str(db_path),
        tmp_path,
        NOW,
    )
    assert rc == 0
    assert db_path.exists()

    app = create_app(db_path, web_dir=tmp_path)
    client = TestClient(app)

    for endpoint in [
        "/v1/porchlamp/snapshot",
        "/v1/porchlamp/leaderboard",
        "/v1/porchlamp/not-ranked",
        "/v1/porchlamp/matrix",
        "/v1/porchlamp/repos/acme/widget",
    ]:
        resp = client.get(endpoint)
        assert resp.status_code == 200, f"Endpoint {endpoint} failed with {resp.status_code}: {resp.text}"
