"""Tests for the spec 12.2 repository deep-dive enrichment.

Covers three layers:

1. ``cvi.lakehouse`` — the additive optional ``deep_dive_json`` column:
   ensure-on-fresh-store, upsert round-trip, missing-repo semantics, and
   the guarantee that a fresh store keeps the exact spec 11.2 column set
   until a payload is stored.
2. ``cvi.deepdive.build_deep_dive`` — a small deterministic PR
   population with hand-computed TTFHR / MR_first / GR_14 / p90 values
   and a hand-computed Kaplan-Meier curve (incl. censored PRs, the
   risk-set convention at tied times, and deterministic thinning).
3. ``cvi.api`` — the deep-dive route surfaces the stored payload under
   ``deep_dive`` when present and omits the key when it is not; 404
   behavior unchanged.

Oracle style: exact expected values everywhere; a test that is easy to
pass is a defect.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Callable

from fastapi.testclient import TestClient

from cvi.api import create_app
from cvi.deepdive import build_deep_dive
from cvi.lakehouse import OperationalStore
from cvi.models import (
    AIPosture,
    AuthorAssociation,
    CVICompositeResult,
    Ecosystem,
    LegalIPModel,
    PillarScores,
    Quadrant,
    RawPREvent,
    RepoMeta,
    Tier,
)

NOW = datetime(2026, 9, 1, 12, 0)

# The exact spec 11.2 base column set (fresh stores must keep it).
SPEC_COLUMNS = [
    "id",
    "owner",
    "name",
    "ecosystem",
    "domain_category",
    "stars",
    "forks",
    "monthly_downloads",
    "dependents_count",
    "openssf_criticality",
    "elephant_factor",
    "legal_ip_model",
    "ai_posture",
    "score_responsiveness",
    "score_criticality",
    "score_governance",
    "score_rigor_ai",
    "score_accessibility",
    "leverage_score",
    "friction_score",
    "matrix_quadrant",
    "overall_cvi",
    "rank_tier",
    "is_stage0_disqualified",
    "disqualification_reason",
    "last_indexed_at",
]


def D(year, month, day, hour=0) -> datetime:
    return datetime(year, month, day, hour)


# --------------------------------------------------------------------------- #
# Deterministic PR population (hand-computed oracles below)
# --------------------------------------------------------------------------- #
def make_pr(
    pr_number: int,
    created_at: datetime,
    author_login: str = "alice",
    author_prior_merged_commits: int = 0,
    closed_at: datetime | None = None,
    merged_at: datetime | None = None,
    is_external: bool = True,
    response_latency_hours: float | None = None,
    maintainer_review_comments: int = 0,
) -> RawPREvent:
    return RawPREvent(
        repo_owner="acme",
        repo_name="widget",
        pr_number=pr_number,
        created_at=created_at,
        author_login=author_login,
        author_association=AuthorAssociation.NONE,
        author_prior_merged_commits=author_prior_merged_commits,
        closed_at=closed_at,
        merged_at=merged_at,
        is_external=is_external,
        response_latency_hours=response_latency_hours,
        maintainer_review_comments=maintainer_review_comments,
    )


def population() -> list[RawPREvent]:
    """Six PRs; NOW = 2026-09-01 12:00.

    P1 alice  first-timer, external, TTFHR 8h, merged+closed at 48h.
    P2 bob    returning (3 prior), external, closed unmerged at 48h,
              TTFHR 20h.
    P3 carol[bot] bot, external (prior 5), merged+closed at 24h,
              TTFHR 40h (bot -> excluded from TTFHR, kept for p90).
    P4 dave   first-timer, external, still open since 08-15 (17.5d at
              NOW), zero comments -> ghost; censored at 17.5d for KM.
    P5 erin   first-timer, external, closed unmerged at 24h.
    P6 intern internal (is_external=False), merged at 24h, TTFHR 2h
              (excluded everywhere by the external filter).
    """
    return [
        make_pr(1, D(2026, 8, 27), closed_at=D(2026, 8, 29),
                merged_at=D(2026, 8, 29), response_latency_hours=8.0,
                maintainer_review_comments=2),
        make_pr(2, D(2026, 8, 1), author_login="bob",
                author_prior_merged_commits=3, closed_at=D(2026, 8, 3),
                response_latency_hours=20.0, maintainer_review_comments=1),
        make_pr(3, D(2026, 8, 29), author_login="carol[bot]",
                author_prior_merged_commits=5, closed_at=D(2026, 8, 30),
                merged_at=D(2026, 8, 30), response_latency_hours=40.0),
        make_pr(4, D(2026, 8, 15), author_login="dave"),
        make_pr(5, D(2026, 8, 27), author_login="erin",
                closed_at=D(2026, 8, 28), maintainer_review_comments=1),
        make_pr(6, D(2026, 8, 20), author_login="intern",
                closed_at=D(2026, 8, 21), merged_at=D(2026, 8, 21),
                is_external=False, response_latency_hours=2.0),
    ]


def make_meta(**overrides) -> RepoMeta:
    base = dict(
        owner="acme",
        name="widget",
        ecosystem=Ecosystem.PYPI,
        elephant_factor=2,
        legal_ip_model=LegalIPModel.CORP_CLA_RECIPROCAL,
        ai_posture=AIPosture.REGULATED,
        active_unclaimed_gfis=7,
    )
    base.update(overrides)
    return RepoMeta(**base)


# --------------------------------------------------------------------------- #
# Lakehouse: deep_dive_json column (additive)
# --------------------------------------------------------------------------- #
class TestDeepDiveColumn:
    def test_fresh_store_keeps_exact_spec_column_set(self) -> None:
        store = OperationalStore()
        try:
            rows = store.connection.execute(
                "PRAGMA table_info(repositories)"
            ).fetchall()
            assert [r[1] for r in rows] == SPEC_COLUMNS
        finally:
            store.close()

    def test_ensure_column_idempotent(self) -> None:
        store = OperationalStore()
        try:
            store.ensure_deep_dive_column()
            store.ensure_deep_dive_column()  # no error, no duplicate
            cols = [
                r[1]
                for r in store.connection.execute(
                    "PRAGMA table_info(repositories)"
                ).fetchall()
            ]
            assert cols == SPEC_COLUMNS + ["deep_dive_json"]
        finally:
            store.close()

    def test_upsert_round_trip_and_overwrite(self) -> None:
        store = OperationalStore()
        try:
            self._seed(store)
            payload_a = {"raw_metrics": {"ttfhr_hours": 14.0}, "n": 1}
            assert store.upsert_deep_dive("acme", "widget", payload_a) == 1
            row = store.get_repo("acme", "widget")
            assert row is not None
            assert json.loads(row["deep_dive_json"]) == payload_a
            payload_b = {"survival_curve": [[0.0, 1.0]]}
            assert store.upsert_deep_dive("acme", "widget", payload_b) == 1
            assert json.loads(store.get_repo("acme", "widget")["deep_dive_json"]) == payload_b
        finally:
            store.close()

    def test_upsert_missing_repo_returns_zero_and_creates_no_row(self) -> None:
        store = OperationalStore()
        try:
            assert store.upsert_deep_dive("nobody", "nothing", {"x": 1}) == 0
            assert store.get_repo("nobody", "nothing") is None
            count = store.connection.execute(
                "SELECT COUNT(*) FROM repositories"
            ).fetchone()[0]
            assert count == 0
        finally:
            store.close()

    def test_get_repo_key_absent_before_column_materialized(self) -> None:
        """No payload stored -> column does not exist -> key absent (the
        fresh-store contract pinned by tests/test_lakehouse.py)."""
        store = OperationalStore()
        try:
            self._seed(store)
            row = store.get_repo("acme", "widget")
            assert "deep_dive_json" not in row
        finally:
            store.close()

    def test_get_repo_null_payload_after_other_repo_upsert(self) -> None:
        """Once materialized, the key is present and NULL-unset."""
        store = OperationalStore()
        try:
            self._seed(store)
            self._seed(store, owner="other", name="repo")
            assert store.upsert_deep_dive("other", "repo", {"x": 1}) == 1
            row = store.get_repo("acme", "widget")
            assert "deep_dive_json" in row and row["deep_dive_json"] is None
        finally:
            store.close()

    @staticmethod
    def _seed(store: OperationalStore, owner="acme", name="widget") -> None:
        result = CVICompositeResult(
            owner=owner,
            name=name,
            ecosystem=Ecosystem.PYPI,
            overall_cvi=50.0,
            rank_tier=Tier.B,
            matrix_quadrant=Quadrant.SWEET_SPOT,
            leverage_score=60.0,
            friction_score=40.0,
            is_stage0_disqualified=False,
        )
        store.upsert_composite(result)


# --------------------------------------------------------------------------- #
# build_deep_dive: raw metrics (hand-computed)
# --------------------------------------------------------------------------- #
class TestRawMetrics:
    def test_hand_computed_values(self) -> None:
        """TTFHR: bot-filtered median of [8.0, 20.0] -> 14.0.
        MR_first: decided external first-timers P1 (merged), P5 (closed)
            -> 1/2 = 0.5.
        GR_14: ghosts {P4} / all external {P1..P5} -> 1/5 = 0.2.
        p90 (nearest-rank) of external merged latencies [24h, 48h]:
            rank = ceil(0.9 * 2) = 2 -> 48h -> 2.0 days."""
        payload = build_deep_dive(make_meta(), population(), NOW)
        assert payload["raw_metrics"] == {
            "ttfhr_hours": 14.0,
            "first_timer_merge_rate": 0.5,
            "ghost_rate_14d": 0.2,
            "tail_merge_latency_days": 2.0,
        }

    def test_bot_and_internal_prs_excluded_from_ttfhr(self) -> None:
        """TTFHR over P1 only (bot P3, internal P6 dropped) -> 8.0;
        p90 over the single external merged PR P1 -> 48h -> 2.0 days."""
        events = [
            population()[0],  # P1: 8h latency, merged at 48h
            population()[2],  # P3: bot
            population()[5],  # P6: internal
        ]
        payload = build_deep_dive(make_meta(), events, NOW)
        assert payload["raw_metrics"]["ttfhr_hours"] == 8.0
        assert payload["raw_metrics"]["tail_merge_latency_days"] == 2.0
        # P3 is not a first-timer (5 prior commits), P6 is internal:
        assert payload["raw_metrics"]["first_timer_merge_rate"] == 1.0

    def test_empty_population_all_none(self) -> None:
        payload = build_deep_dive(make_meta(), [], NOW)
        assert payload["raw_metrics"] == {
            "ttfhr_hours": None,
            "first_timer_merge_rate": None,
            "ghost_rate_14d": None,
            "tail_merge_latency_days": None,
        }


# --------------------------------------------------------------------------- #
# build_deep_dive: Kaplan-Meier survival curve
# --------------------------------------------------------------------------- #
class TestSurvivalCurve:
    def test_hand_computed_curve_with_censored_pr(self) -> None:
        """External durations: P1 2.0d (fail), P2 2.0d (fail), P3 1.0d
        (fail), P4 censored at 17.5d, P5 1.0d (fail).

        t=1.0: n = 5 (all durations >= 1.0), d = 2 -> S = 1 * (1 - 2/5) = 0.6
        t=2.0: n = 3 (P1, P2, P4), d = 2 -> S = 0.6 * (1 - 2/3) = 0.2
        P4 stays in the risk set at both event times (censoring 17.5d)."""
        payload = build_deep_dive(make_meta(), population(), NOW)
        assert payload["survival_curve"] == [
            [0.0, 1.0],
            [1.0, 0.6],
            [2.0, 0.2],
        ]

    def test_shape_invariants(self) -> None:
        curve = build_deep_dive(make_meta(), population(), NOW)["survival_curve"]
        assert curve[0] == [0.0, 1.0]  # S(0) = 1.0
        times = [t for t, _ in curve]
        assert times == sorted(times)  # increasing, no duplicates
        survivals = [s for _, s in curve]
        assert all(a >= b for a, b in zip(survivals, survivals[1:]))
        assert survivals[-1] <= 1.0 and survivals[-1] >= 0.0

    def test_all_censored_stays_at_one(self) -> None:
        """Only still-open PRs -> no failure times -> flat S = 1."""
        events = [
            make_pr(1, D(2026, 8, 22)),
            make_pr(2, D(2026, 8, 27)),
        ]
        assert build_deep_dive(make_meta(), events, NOW)["survival_curve"] == [
            [0.0, 1.0]
        ]

    def test_no_external_prs(self) -> None:
        events = [
            make_pr(1, D(2026, 8, 20), merged_at=D(2026, 8, 21),
                    closed_at=D(2026, 8, 21), is_external=False),
        ]
        assert build_deep_dive(make_meta(), events, NOW)["survival_curve"] == [
            [0.0, 1.0]
        ]

    def test_censored_exactly_at_event_time_stays_in_risk_set(self) -> None:
        """P1 fails at 1.0d (created 08-26, closed 08-27); P2 is created
        exactly 1.0d before NOW (08-31 12:00) and still open, so it is
        censored exactly at 1.0d. Convention: the censored PR is still at
        risk at its censoring time -> n = 2, S(1.0) = 1 - 1/2 = 0.5."""
        events = [
            make_pr(1, D(2026, 8, 26), closed_at=D(2026, 8, 27),
                    merged_at=D(2026, 8, 27)),
            make_pr(2, datetime(2026, 8, 31, 12)),
        ]
        assert build_deep_dive(make_meta(), events, NOW)["survival_curve"] == [
            [0.0, 1.0],
            [1.0, 0.5],
        ]

    def test_thinning_capped_at_20_points(self) -> None:
        """30 distinct failure times (1..30 days) -> 31 points, thinned
        deterministically to 20: origin kept, last event kept, times
        strictly increasing, survival non-increasing. S follows
        (30 - t) / 30: S(29) = 1/30 > 0, S(30) = 0 (all PRs failed)."""
        events = [
            make_pr(i, D(2026, 1, 1), closed_at=D(2026, 1, 1 + i))
            for i in range(1, 31)
        ]
        curve = build_deep_dive(make_meta(), events, NOW)["survival_curve"]
        assert len(curve) == 20
        assert curve[0] == [0.0, 1.0]
        assert curve[-1][0] == 30.0  # last event always kept
        times = [t for t, _ in curve]
        assert all(a < b for a, b in zip(times, times[1:]))
        survivals = [s for _, s in curve]
        assert all(a >= b for a, b in zip(survivals, survivals[1:]))
        assert survivals[-1] == 0.0  # S(30): every PR has failed
        assert survivals[-2] > 0.0  # the thinned second-to-last point is pre-final

    def test_deterministic_across_calls(self) -> None:
        events = population()
        a = build_deep_dive(make_meta(), events, NOW)
        b = build_deep_dive(make_meta(), events, NOW)
        assert a == b
        # Stable serialization (the store serializes with sort_keys=True,
        # but the dict content itself must be identical).
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# --------------------------------------------------------------------------- #
# build_deep_dive: elephant / legal / GFI sections
# --------------------------------------------------------------------------- #
class TestFeedPendingAndMetaSections:
    def test_elephant_breakdown_placeholder(self) -> None:
        payload = build_deep_dive(make_meta(elephant_factor=4), [], NOW)
        breakdown = payload["elephant_breakdown"]
        assert breakdown["elephant_factor"] == 4
        assert breakdown["distribution"] is None
        assert breakdown["source"] == "pending_feed"
        assert "GraphQL" in breakdown["note"]

    def test_good_first_issues_placeholder(self) -> None:
        payload = build_deep_dive(make_meta(active_unclaimed_gfis=9), [], NOW)
        gfi = payload["good_first_issues"]
        assert gfi["count"] == 9
        assert gfi["items"] == []
        assert gfi["source"] == "pending_feed"
        assert "issues API feed" in gfi["note"]

    def test_legal_ip_safety_semantics(self) -> None:
        """Hazard mirrors the governance pillar's S_legal scores
        (DCO=100, FOUNDATION_CLA=75 -> low; CORP_CLA_RECIPROCAL=40 ->
        medium; CORP_CLA_UNILATERAL=10 -> high)."""
        cases = {
            LegalIPModel.DCO: ("DCO", "low", 100.0),
            LegalIPModel.FOUNDATION_CLA: ("FOUNDATION_CLA", "low", 75.0),
            LegalIPModel.CORP_CLA_RECIPROCAL: ("CORP_CLA_RECIPROCAL", "medium", 40.0),
            LegalIPModel.CORP_CLA_UNILATERAL: ("CORP_CLA_UNILATERAL", "high", 10.0),
        }
        for model, (value, hazard, s_legal) in cases.items():
            payload = build_deep_dive(
                make_meta(legal_ip_model=model, ai_posture=AIPosture.PROSCRIBED),
                [],
                NOW,
            )
            legal = payload["legal_ip"]
            assert legal["model"] == value
            assert legal["cla_hazard"] == hazard
            assert legal["s_legal_score"] == s_legal
            assert legal["ai_policy_posture"] == "PROSCRIBED"

    def test_payload_top_level_shape(self) -> None:
        payload = build_deep_dive(make_meta(), population(), NOW)
        assert set(payload) == {
            "raw_metrics",
            "survival_curve",
            "elephant_breakdown",
            "legal_ip",
            "good_first_issues",
        }


# --------------------------------------------------------------------------- #
# API: deep-dive route
# --------------------------------------------------------------------------- #
def make_result(owner: str, name: str) -> CVICompositeResult:
    return CVICompositeResult(
        owner=owner,
        name=name,
        ecosystem=Ecosystem.PYPI,
        overall_cvi=92.5,
        rank_tier=Tier.S_PLUS,
        matrix_quadrant=Quadrant.SWEET_SPOT,
        leverage_score=80.0,
        friction_score=20.0,
        is_stage0_disqualified=False,
    )


def make_pillars() -> PillarScores:
    return PillarScores(
        responsiveness=90.0,
        criticality=80.0,
        governance=70.0,
        rigor=60.0,
        accessibility=50.0,
    )


def seed_deep_dive_store() -> OperationalStore:
    """Two repos: alpha/repo carries a stored deep-dive payload,
    beta/repo does not."""
    store = OperationalStore()
    store.upsert_composite(make_result("alpha", "repo"), pillar_scores=make_pillars())
    store.upsert_composite(make_result("beta", "repo"), pillar_scores=make_pillars())
    payload = build_deep_dive(make_meta(), population(), NOW)
    assert store.upsert_deep_dive("alpha", "repo", payload) == 1
    return store


def open_client(seed: Callable[[], OperationalStore]) -> TestClient:
    """Enter a TestClient and rebuild the store on the ASGI portal thread.

    sqlite3 connections are thread-bound, and starlette's TestClient
    dispatches requests onto its blocking-portal thread, so a store
    created on the test thread cannot be touched by the app (same
    pattern as tests/test_api.py).
    """
    app = create_app()
    client = TestClient(app)
    client.__enter__()
    app.state.store = client.portal.call(seed)
    return client


class TestApiDeepDive:
    def test_seeded_repo_includes_deep_dive(self) -> None:
        client = open_client(seed_deep_dive_store)
        try:
            resp = client.get("/v1/cvi/repos/alpha/repo")
            assert resp.status_code == 200
            body = resp.json()
            expected_payload = build_deep_dive(make_meta(), population(), NOW)
            assert body["deep_dive"] == expected_payload
            # The raw serialized column still rides along in the row.
            assert json.loads(body["deep_dive_json"]) == expected_payload
            # ...and every other field is unchanged.
            assert body["owner"] == "alpha"
            assert body["name"] == "repo"
            assert body["overall_cvi"] == 92.5
            assert body["rank_tier"] == "Tier S+"
            assert body["matrix_quadrant"] == "Sweet Spot"
            assert body["pillar_scores"] == {
                "responsiveness": 90.0,
                "criticality": 80.0,
                "governance": 70.0,
                "rigor": 60.0,
                "accessibility": 50.0,
            }
        finally:
            client.__exit__(None, None, None)

    def test_unseeded_repo_omits_deep_dive_key(self) -> None:
        client = open_client(seed_deep_dive_store)
        try:
            resp = client.get("/v1/cvi/repos/beta/repo")
            assert resp.status_code == 200
            body = resp.json()
            # Column exists (alpha's upsert materialized it) but is NULL:
            # no deep_dive key, and all other fields unchanged.
            assert "deep_dive" not in body
            assert body["deep_dive_json"] is None
            assert body["owner"] == "beta"
            assert body["overall_cvi"] == 92.5
            assert body["pillar_scores"] == {
                "responsiveness": 90.0,
                "criticality": 80.0,
                "governance": 70.0,
                "rigor": 60.0,
                "accessibility": 50.0,
            }
            # beta's key set equals alpha's minus the parsed deep_dive.
            alpha_keys = set(
                client.get("/v1/cvi/repos/alpha/repo").json()
            ) - {"deep_dive"}
            assert set(body) == alpha_keys
        finally:
            client.__exit__(None, None, None)

    def test_missing_repo_404_unchanged(self) -> None:
        client = open_client(seed_deep_dive_store)
        try:
            resp = client.get("/v1/cvi/repos/nobody/nothing")
            assert resp.status_code == 404
            assert "detail" in resp.json()
        finally:
            client.__exit__(None, None, None)
