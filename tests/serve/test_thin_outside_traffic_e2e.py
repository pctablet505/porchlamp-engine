"""A project with fewer than three outside PRs in the window is unrated, through the real pipeline (RFC-0027).

Two projects merge every outside PR within a day. One has two outside PRs in the 180-day window, the other three.
Only the one with two is unrated for lack of outside traffic; both are otherwise the same project.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from porchlamp.serve.app import PinnedEvidenceFiles, create_app
from tests.serve.test_black_hole_evidence_store_built import (
    AS_OF, OWNER, _iso, _pr, _seed_repo, _store, derive,
)

THIN = "thin"
ENOUGH = "enough"
MANIFEST_KEY = "outside_prs_180d"


def _merged(count: int) -> list[dict[str, Any]]:
    prs = []
    for n in range(1, count + 1):
        pr = _pr(n, 40 + n, maintainer_reply=True)
        merged = _iso(AS_OF - timedelta(days=40 + n) + timedelta(days=1))
        pr.update(state="MERGED", mergedAt=merged, closedAt=merged, mergedBy={"login": "maint-secret"})
        prs.append(pr)
    return prs


@pytest.fixture
def built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(derive, "DATA_DIR", data_dir)
    monkeypatch.setenv("PORCHLAMP_DATA_ROOT", str(data_dir))
    window = dict(window_anchor=AS_OF, fetched_at=AS_OF - timedelta(days=1))
    _seed_repo(data_dir, THIN, _merged(2), **window)
    _seed_repo(data_dir, ENOUGH, _merged(3), **window)
    (data_dir / "derived").mkdir(parents=True)
    for name in (THIN, ENOUGH):
        bundle = derive.derive_repo(OWNER, name, derive.Ecosystem.PYPI, data_dir, fetch=False, now=AS_OF)
        (data_dir / "derived" / f"{OWNER}__{name}.json").write_text(json.dumps(bundle, indent=2, sort_keys=True), encoding="utf-8")
    snapshot_dir, db_path = _store(data_dir, tmp_path)
    client = TestClient(create_app(db_path, web_dir=tmp_path, evidence_files=PinnedEvidenceFiles(snapshot_dir, data_dir)))
    return {"client": client, "manifest": json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))}


def _detail(client: TestClient, name: str) -> dict[str, Any]:
    resp = client.get(f"/v1/porchlamp/repos/{OWNER}/{name}")
    assert resp.status_code == 200
    return resp.json()


def test_the_manifest_records_the_outside_pr_count_of_each_project(built):
    counts = {r["owner"] + "/" + r["name"]: r["gate_evidence"][MANIFEST_KEY] for r in built["manifest"]["repos"]}
    assert counts == {f"{OWNER}/{THIN}": 2, f"{OWNER}/{ENOUGH}": 3}


def test_the_project_with_two_outside_prs_is_unrated_for_want_of_a_workflow(built):
    body = _detail(built["client"], THIN)
    assert body["gate_reason"] == "unrated: no_external_workflow"
    assert body["rank"] is None


def test_the_project_with_three_outside_prs_is_not_stopped_by_the_floor(built):
    assert _detail(built["client"], ENOUGH)["gate_reason"] != "unrated: no_external_workflow"
