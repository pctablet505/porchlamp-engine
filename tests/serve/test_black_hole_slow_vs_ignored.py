"""A project that answers late is slow, not a black hole (RFC-0026), through the real pipeline.

Two projects have the same 12 outside PRs, none answered inside 14 days. In one a maintainer replies on day 20; in
the other nobody ever does. Only the second is a black hole. Both have the same 1.4 GR_14 (12 of 12 unanswered): the
ranking scores the slow project's late replies, and the gate removes only the one that ignores outside PRs.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from porchlamp.core import gates
from porchlamp.serve.app import PinnedEvidenceFiles, create_app
from tests.serve.test_black_hole_evidence_store_built import (
    AS_OF, LONG_REPLY, OWNER, _iso, _pr, _seed_repo, _store, derive,
)

SLOW = "slow"
IGNORED = "ignored"
#: 14 PRs nobody touched and 4 a maintainer answered on day 20: 18 unanswered in 14 days, 14 ignored.
MIXED = "mixed"


def _late_replies(reply_after_days: float, first: int = 1, last: int = 12) -> list[dict[str, Any]]:
    prs = []
    for n in range(first, last + 1):
        pr = _pr(n, 60 + n)
        pr["comments"]["nodes"].append({
            "author": {"__typename": "User", "login": "maint-secret"}, "authorAssociation": "MEMBER",
            "bodyText": LONG_REPLY, "createdAt": _iso(AS_OF - timedelta(days=60 + n) + timedelta(days=reply_after_days))})
        prs.append(pr)
    return prs


@pytest.fixture
def built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(derive, "DATA_DIR", data_dir)
    monkeypatch.setenv("PORCHLAMP_DATA_ROOT", str(data_dir))
    window = dict(window_anchor=AS_OF, fetched_at=AS_OF - timedelta(days=1))
    _seed_repo(data_dir, SLOW, _late_replies(20), **window)
    _seed_repo(data_dir, IGNORED, [_pr(n, 60 + n) for n in range(1, 13)], **window)
    _seed_repo(data_dir, MIXED, [_pr(n, 60 + n) for n in range(1, 15)] + _late_replies(20, 15, 18), **window)
    (data_dir / "derived").mkdir(parents=True)
    gr14: dict[str, dict[str, Any]] = {}
    for name in (SLOW, IGNORED, MIXED):
        bundle = derive.derive_repo(OWNER, name, derive.Ecosystem.PYPI, data_dir, fetch=False, now=AS_OF)
        path = data_dir / "derived" / f"{OWNER}__{name}.json"
        path.write_text(json.dumps(bundle, indent=2, sort_keys=True), encoding="utf-8")
        meta = json.loads(path.read_text(encoding="utf-8"))["meta"]
        gr14[name] = {k: v for k, v in meta.items() if k.startswith("gr14_")}
    snapshot_dir, db_path = _store(data_dir, tmp_path)
    client = TestClient(create_app(db_path, web_dir=tmp_path, evidence_files=PinnedEvidenceFiles(snapshot_dir, data_dir)))
    return {"client": client, "gr14": gr14}


def _detail(client: TestClient, name: str) -> dict[str, Any]:
    resp = client.get(f"/v1/porchlamp/repos/{OWNER}/{name}")
    assert resp.status_code == 200
    return resp.json()


def test_both_projects_have_the_same_fourteen_day_ghost_rate(built):
    for name in (SLOW, IGNORED):
        gr = built["gr14"][name]
        assert (round(gr["gr14_ghost_rate"] * gr["gr14_population_n"]), gr["gr14_population_n"]) == (12, 12), name


def test_the_project_that_answers_late_is_not_a_black_hole(built):
    body = _detail(built["client"], SLOW)
    assert body["gate_reason"] != "black_hole"
    assert body["black_hole_evidence"] is None


def test_the_project_nobody_answers_is_a_black_hole_and_its_panel_shows_the_ignored_count(built):
    body = _detail(built["client"], IGNORED)
    assert body["gate_reason"] == "black_hole"
    ev = body["black_hole_evidence"]
    assert (ev["unanswered"], ev["ignored"], ev["population_n"]) == (12, 12, 12)
    assert ev["ignored_share"] == 1.0
    assert ev["wilson_lower_bound"] == round(gates.wilson_lower_bound(12, 12), 4)
    assert [pr["touched_ever"] for pr in ev["counted_prs"]] == [False] * 12


def test_the_panel_never_names_a_contributor(built):
    text = json.dumps(_detail(built["client"], IGNORED)["black_hole_evidence"])
    assert "secret" not in text and "outsider" not in text


def test_the_panel_of_a_mixed_project_gives_the_bound_of_the_share_the_gate_judged(built):
    # 18 unanswered in 14 days (bound near 0.82) but 14 ignored (bound near 0.57): the panel's bound is the ignored one
    body = _detail(built["client"], MIXED)
    assert body["gate_reason"] == "black_hole"
    ev = body["black_hole_evidence"]
    assert (ev["unanswered"], ev["ignored"], ev["population_n"]) == (18, 14, 18)
    assert ev["wilson_lower_bound"] == round(gates.wilson_lower_bound(14, 18), 4)
    assert ev["wilson_lower_bound"] < round(gates.wilson_lower_bound(18, 18), 4)
    assert sum(pr["touched_ever"] for pr in ev["counted_prs"]) == 4
