"""Regression tests for non-UI codebase audit flaw fixes (#119)."""
from __future__ import annotations

import io
import json
import math
import sqlite3
import tarfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from porchlamp.audit import anchors, coverage
from porchlamp.collect import ghcli, github_graphql, governance
from porchlamp.collect.ratelimit import CachedHttpClient, CircuitBreaker, ETagCache, TokenBucketRateLimiter
from porchlamp.collect.user_cache import UserCache
from porchlamp.core import gates
from porchlamp.core.models import (
    FeatureBundle,
    Measured,
    PRWindow,
    PRWindowEvent,
    RepoIdentity,
    Unmeasured,
)
from porchlamp.core.openssf import calculate_pike_criticality
from porchlamp.core.params.curves import clamped_identity, cohort_rate_score, fill_rate_score, reference_ecdf
from porchlamp.core.tiebreak import ttfhr_p75_from_pr_window
from porchlamp.deepdive import _raw_metrics, _survival_curve
from porchlamp.derive.bundle import _resolve_commit_facts, _V1Ctx
from porchlamp.derive.gate_evidence import _ghost_rate_prs_from_nodes
from porchlamp.models import AuthorAssociation, RawPREvent
from porchlamp.normalize import compute_ecosystem_percentile
from porchlamp.searchops.credentials import Redactor
from porchlamp.serve.app import EvidencePinError, PinnedEvidenceFiles
from porchlamp.snapshot import stats
from porchlamp.store.schema import create_schema


def test_nan_and_inf_rejected_in_curves_and_criticality() -> None:
    for fn in (fill_rate_score, cohort_rate_score, clamped_identity, reference_ecdf):
        with pytest.raises(ValueError, match="finite"):
            fn(float("nan"))
        with pytest.raises(ValueError, match="finite"):
            fn(float("inf"))

    with pytest.raises(ValueError, match="NaN"):
        compute_ecosystem_percentile(float("nan"), [1.0, 2.0, 3.0])

    with pytest.raises(ValueError, match="NaN"):
        compute_ecosystem_percentile(1.0, [1.0, float("nan"), 3.0])

    # an infinity keeps its place in the order (tests/test_adversarial_tier5_hardening.py's extremes)
    assert compute_ecosystem_percentile(float("inf"), [1.0, 2.0, 3.0]) == 100.0
    assert compute_ecosystem_percentile(float("-inf"), [1.0, 2.0, 3.0]) == 0.0

    signals = {
        "created_since_months": 12.0,
        "updated_since_months": 1.0,
        "contributor_count": 10,
        "org_count": 2,
        "commit_frequency_weekly": float("nan"),
        "recent_releases_count": 4,
        "closed_issues_count_90d": 10,
        "updated_issues_count_90d": 20,
        "comment_frequency": 2.0,
        "dependents_count": 100,
    }
    with pytest.raises(ValueError, match="finite"):
        calculate_pike_criticality(signals)


def test_wilson_lower_bound_handles_overshoot_and_nan() -> None:
    assert gates.wilson_lower_bound(15, 10) == gates.wilson_lower_bound(10, 10)
    assert gates.wilson_lower_bound_from_share(float("nan"), 10) == 0.0
    assert gates.wilson_lower_bound_from_share(0.5, 10, z=float("nan")) == 0.0


def test_maintainers_preserves_automated_across_set_ops() -> None:
    m1 = gates.Maintainers(["alice", "bob"])
    m1.automated = frozenset({("alice", "thanks for the pr"), ("bob", "welcome contributor")})

    m2 = gates.Maintainers(["bob", "carol"])
    m2.automated = frozenset({("bob", "bot-like template"), ("carol", "auto review message")})

    union = m1 | m2
    assert isinstance(union, gates.Maintainers)
    assert union.automated == m1.automated | m2.automated

    diff = m1 - {"bob"}
    assert isinstance(diff, gates.Maintainers)
    assert diff.automated == frozenset({("alice", "thanks for the pr")})

    rdiff = frozenset({"alice", "dave"}) - m2
    assert isinstance(rdiff, gates.Maintainers)
    assert rdiff == frozenset({"alice", "dave"})

    inter = m1 & m2
    assert isinstance(inter, gates.Maintainers)
    assert inter.automated == frozenset({("bob", "welcome contributor"), ("bob", "bot-like template")})

    rinter = frozenset({"bob"}) & m1
    assert isinstance(rinter, gates.Maintainers)
    assert rinter.automated == frozenset({("bob", "welcome contributor")})

    xor = m1 ^ m2
    assert isinstance(xor, gates.Maintainers)
    assert xor.automated == frozenset({("alice", "thanks for the pr"), ("carol", "auto review message")})


def test_ttfhr_p75_clamps_clock_skewed_negative_latency() -> None:
    ev = PRWindowEvent(
        pr_number=1,
        created_at="2026-06-01T12:00:00Z",
        closed_at=None,
        merged_at=None,
        author_login="ext",
        author_association="NONE",
        is_external=True,
        first_human_review_at="2026-06-01T11:55:00Z",
    )
    win = PRWindow(
        start="2026-01-01T00:00:00Z",
        end="2026-06-30T00:00:00Z",
        truncated=False,
        events=(ev,),
    )
    res = ttfhr_p75_from_pr_window(win)
    assert res == 0.0
    assert not math.isnan(res)


def test_governance_observed_promotions_reads_timeline_items_closed_events() -> None:
    prs = [
        {
            "number": 1,
            "createdAt": "2026-01-10T00:00:00Z",
            "mergedAt": "2026-01-11T00:00:00Z",
            "author": {"login": "newbie", "createdAt": "2020-01-01T00:00:00Z"},
            "mergedBy": {"login": "maint1"},
        },
        {
            "number": 2,
            "createdAt": "2026-02-10T00:00:00Z",
            "closedAt": "2026-02-12T00:00:00Z",
            "author": {"login": "other_user", "createdAt": "2020-01-01T00:00:00Z"},
            "timelineItems": {
                "nodes": [
                    {
                        "__typename": "ClosedEvent",
                        "createdAt": "2026-02-12T00:00:00Z",
                        "actor": {"login": "newbie"},
                    }
                ]
            },
        },
    ]
    res = governance.observed_promotions(prs)
    assert len(res) == 1
    assert res[0]["login"] == "newbie"
    assert res[0]["first_maintainer_action"] == "close"


def test_user_cache_file_root_fallback_and_monotonic_put(tmp_path: Path) -> None:
    db_file = tmp_path / "users.sqlite"
    cache = UserCache(db_file, backend="sqlite")
    assert isinstance(cache._db_lock, type(threading.RLock()))
    t1 = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

    assert cache.put("alice", {"account_created_at": "2020-01-01T00:00:00Z", "public_event_count": 50}, t2)
    # Older observation must not overwrite newer observation
    assert cache.put("alice", {"account_created_at": "2020-01-01T00:00:00Z", "public_event_count": 10}, t1)
    got = cache.get("alice", t2)
    assert got is not None
    assert got["public_event_count"] == 50

    # Naive datetime is normalized to UTC and readable
    naive_now = datetime(2026, 6, 2, 12, 0)
    assert cache.put("bob", {"account_created_at": "2021-01-01T00:00:00Z", "public_event_count": 25}, naive_now)
    got_bob = cache.get("bob", datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc))
    assert got_bob is not None
    assert got_bob["public_event_count"] == 25
    assert cache.count_rows() == 2

    # JSON fallback path when root is a .sqlite file uses sibling users/ directory
    json_path = cache._path("alice")
    assert json_path == tmp_path / "users" / "alice.json"
    cache.close()
    cache._use_sqlite = False
    assert cache.put("carol", {"account_created_at": "2021-01-01T00:00:00Z", "public_event_count": 30}, t2)
    assert cache.count_rows() == 1


def test_reopened_pr_not_marked_closed_from_historical_timeline_event() -> None:
    node = {
        "number": 10,
        "state": "OPEN",
        "isDraft": False,
        "createdAt": "2026-05-01T00:00:00Z",
        "closedAt": None,
        "mergedAt": None,
        "author": {"login": "contributor", "createdAt": "2020-01-01T00:00:00Z"},
        "authorAssociation": "NONE",
        "reviews": {"nodes": [], "pageInfo": {"hasNextPage": False}},
        "comments": {"nodes": [], "pageInfo": {"hasNextPage": False}},
        "timelineItems": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [
                {
                    "__typename": "ClosedEvent",
                    "createdAt": "2026-05-02T00:00:00Z",
                    "actor": {"login": "contributor"},
                }
            ],
        },
    }
    prs = _ghost_rate_prs_from_nodes([node])
    assert len(prs) == 1
    assert prs[0].closed_by_author is False
    assert prs[0].closed_by_maintainer_at is None

    derived = github_graphql.derive_pr_window([node])
    assert len(derived) == 1
    assert derived[0]["closed_by_author"] is False
    assert derived[0]["closed_by_maintainer_at"] is None

    assert gates.is_excluded_from_review_denominator(node, frozenset()) is False


def test_a_maintainers_close_answers_a_pull_request_reopened_later() -> None:
    # Closed by a maintainer a day after it was opened, then reopened: the project answered within 14 days.
    node = {
        "number": 11,
        "state": "OPEN",
        "isDraft": False,
        "createdAt": "2026-05-01T00:00:00Z",
        "closedAt": None,
        "mergedAt": None,
        "author": {"login": "contributor", "createdAt": "2020-01-01T00:00:00Z"},
        "authorAssociation": "NONE",
        "reviews": {"nodes": [], "pageInfo": {"hasNextPage": False}},
        "comments": {"nodes": [], "pageInfo": {"hasNextPage": False}},
        "timelineItems": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [
                {"__typename": "ClosedEvent", "createdAt": "2026-05-02T00:00:00Z", "actor": {"login": "maya"}},
            ],
        },
    }
    prs = _ghost_rate_prs_from_nodes([node])
    assert prs[0].closed_by_author is False
    assert prs[0].closed_by_maintainer_at == "2026-05-02T00:00:00Z"
    window = gates.evaluate_gate5_window(prs, datetime(2026, 7, 1, tzinfo=timezone.utc))
    assert (window.population_n, window.unanswered, window.ignored) == (1, 0, 0)

    derived = github_graphql.derive_pr_window([node])
    assert derived[0]["closed_by_maintainer_at"] == "2026-05-02T00:00:00Z"


def test_redactor_ignores_short_tokens() -> None:
    r = Redactor()
    r.add("a")
    r.add("ab")
    r.add("secret_token_12345")
    assert r("status: active with secret_token_12345") == "status: active with [redacted]"


def test_ratelimit_primitives_have_thread_locks() -> None:
    limiter = TokenBucketRateLimiter(rate=1000.0, capacity=20.0)
    cache = ETagCache()
    breaker = CircuitBreaker(jitter_fn=lambda: 0.0)
    client = CachedHttpClient(rate_limiter=limiter, etag_cache=cache, circuit_breaker=breaker)

    assert hasattr(limiter, "_lock")
    assert hasattr(cache, "_lock")
    assert hasattr(breaker, "_lock")
    assert hasattr(client, "_quota_lock")

    errors: list[Exception] = []

    def worker(idx: int) -> None:
        try:
            limiter.acquire()
            cache.put(f"https://example.com/{idx}", f'"e{idx}"', b"ok")
            assert cache.get(f"https://example.com/{idx}") == (f'"e{idx}"', b"ok")
            breaker.trip()
            assert isinstance(breaker.is_open, bool)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors


def test_bundle_resolve_commit_facts_cut_empty_window_unmeasured(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    c365_dir = raw_root / "commits_365d"
    c365_dir.mkdir(parents=True)
    # 1000 bot commits -> window is cut (truncated at pre-RFC-0021 cap of 1000) and has 0 human commits
    bot_rows = [
        {
            "sha": f"s{i}",
            "date": "2026-06-01T00:00:00Z",
            "author_login": "dependabot[bot]",
            "author_type": "Bot",
            "author_email": "bot@github.com",
        }
        for i in range(1000)
    ]
    (c365_dir / "acme__lib.json").write_text(
        json.dumps(
            {
                "fetched_at": "2026-07-01T00:00:00Z",
                "source": "github-rest:commits",
                "data": {"rows": bot_rows, "truncated": True},
            }
        ),
        encoding="utf-8",
    )
    ctx = _V1Ctx(
        meta={},
        provenance={},
        confidence={},
        raw_root=raw_root,
        problems=[],
        referenced={},
    )
    last_fact, domain_fact = _resolve_commit_facts(ctx, "acme__lib")
    assert isinstance(last_fact, Unmeasured)
    assert last_fact.reason == "collector_failure"
    assert isinstance(domain_fact, Unmeasured)
    assert domain_fact.reason == "collector_failure"


def test_ghcli_ignores_zero_ratelimit_limit_and_updates_pooled_timeout() -> None:
    headers = {
        "x-ratelimit-limit": "0",
        "x-ratelimit-remaining": "0",
        "x-ratelimit-reset": "9999999999",
    }
    assert ghcli._limited(headers) is False

    class DummySock:
        def __init__(self) -> None:
            self.timeout: float | None = None

        def settimeout(self, val: float) -> None:
            self.timeout = val

    class DummyConn:
        def __init__(self) -> None:
            self.timeout = 5.0
            self.sock = DummySock()

        def request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None) -> None:
            pass

        def getresponse(self) -> object:
            class Resp:
                status = 200
                reason = "OK"
                headers: dict[str, str] = {}

                def read(self) -> bytes:
                    return b"{}"

            return Resp()

    conn = DummyConn()
    with ghcli._POOL_LOCK:
        ghcli._POOL.clear()
        ghcli._POOL_IDLE.clear()
        ghcli._POOL[("https", "api.github.com", 443)] = conn  # type: ignore[assignment]
    try:
        status, _hdrs, _body = ghcli._send_pooled(
            "GET", "https://api.github.com/rate_limit", None, {}, 42.0, ["api", "/rate_limit"]
        )
        assert status == 200
        assert conn.timeout == 42.0
        assert conn.sock.timeout == 42.0
    finally:
        ghcli.close_pool()


def test_reproject_coverage_rounding_preserves_exact_floor() -> None:
    from porchlamp.store import queries

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    conn.execute(
        "INSERT INTO snapshots (id, as_of, benchmark_version, constants_hash, code_commit, manifest_root, created_at)"
        " VALUES ('s1', '2026-07-01T00:00:00Z', '2.0.0', 'h', 'c', '/tmp', '2026-07-01T00:00:00Z')"
    )
    conn.execute(
        """
        INSERT INTO repositories (
            id, snapshot_id, owner, name, ecosystem, raw_hash, constants_hash,
            eligible, gate_reason, gate_undecided,
            routed, rated, unrated_reason, porchlamp, rank, tier, coverage,
            p_responsiveness, c_responsiveness,
            p_criticality, c_criticality,
            p_governance, c_governance,
            p_rigor, c_rigor,
            p_accessibility, c_accessibility,
            friction, leverage, quadrant, suspicion
        ) VALUES (
            1, 's1', 'acme', 'lib', 'pypi', 'rh', 'ch',
            1, NULL, '[]',
            NULL, 1, NULL, 75.0, 1, 'A', 0.6,
            80.0, 0.6,
            70.0, 0.6,
            0.0, 0.0,
            90.0, 1.0,
            85.0, 1.0,
            20.0, 75.0, 'Sweet Spot', '[]'
        )
        """
    )
    conn.commit()
    # Balanced weights: 0.25*0.6 + 0.25*0.6 + 0.20*0.0 + 0.15*1.0 + 0.15*1.0 == 0.59999999999999998 in IEEE-754 float
    rows, total = queries.reproject(
        conn,
        "s1",
        {"responsiveness": 0.25, "criticality": 0.25, "governance": 0.20, "rigor": 0.15, "accessibility": 0.15},
    )
    assert total == 1
    assert rows[0]["coverage"] == 0.6


def test_pinned_evidence_files_rejects_empty_and_traversing_bundle_file(tmp_path: Path) -> None:
    snap_dir = tmp_path / "snap"
    snap_dir.mkdir()

    for bad_rel in ("", ".", "../outside.json"):
        (snap_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "snapshot_id": "s1",
                    "repos": [{"owner": "acme", "name": "lib", "bundle_file": bad_rel}],
                }
            ),
            encoding="utf-8",
        )
        pins = PinnedEvidenceFiles(snap_dir, tmp_path / "raw")
        with pytest.raises(EvidencePinError, match="bundle_file"):
            pins.pr_window_bytes("s1", "acme", "lib", "hash")


def test_snapshot_stats_handles_empty_repositories_and_non_repo_meta_files(tmp_path: Path) -> None:
    meta_dir = tmp_path / "raw" / "github_meta"
    meta_dir.mkdir(parents=True)
    (meta_dir / "stray.json").write_text('{"data": {"stars": 100}}', encoding="utf-8")
    (meta_dir / "acme__lib.json").write_text('{"data": {"stars": 50}}', encoding="utf-8")
    stars_map = stats._read_stars(meta_dir)
    assert stars_map == {("acme", "lib"): 50}

    db_path = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db_path))
    create_schema(conn)
    conn.execute(
        "INSERT INTO snapshots (id, as_of, benchmark_version, constants_hash, code_commit, manifest_root, created_at)"
        " VALUES ('s-empty', '2026-07-01T00:00:00Z', '2.0.0', 'h', 'c', '/tmp', '2026-07-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    out = stats.compute_stats(db_path, data_dir=tmp_path)
    assert out["snapshot_id"] == "s-empty"
    assert out["repos"] == 0
    assert out["black_hole_share"] == 0.0
    assert out["top100_stars_min"] == -1

    # no snapshot row and no as_of: there is no reference time, and none is made up
    bare = tmp_path / "bare.db"
    conn = sqlite3.connect(str(bare))
    create_schema(conn)
    conn.close()
    with pytest.raises(ValueError, match="no snapshot row"):
        stats.compute_stats(bare, data_dir=tmp_path)


def test_audit_anchors_and_coverage_fixes(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    (raw_dir / "pr_window").mkdir(parents=True)
    (raw_dir / "pr_window" / "acme__lib.json").write_text(
        json.dumps(
            {
                "data": {
                    "prs": [
                        {
                            "number": 1,
                            "author": {"login": "ext", "__typename": "User"},
                            "authorAssociation": "NONE",
                            "mergedAt": "2026-06-01T00:00:00Z",
                            "closedAt": "2026-06-01T00:00:00Z",
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    ev = coverage.RepoEvidence("acme/lib", tmp_path)
    assert len(ev.pr_records()) == 1
    row = coverage._repo_row(ev)
    assert row.prs == 1

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    conn.execute(
        "INSERT INTO snapshots (id, as_of, benchmark_version, constants_hash, code_commit, manifest_root, created_at)"
        " VALUES ('s1', '2026-07-01T00:00:00Z', '2.0.0', 'h', 'c', '/tmp', '2026-07-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO repositories (id, snapshot_id, owner, name, ecosystem, raw_hash, constants_hash, eligible, gate_undecided, rated, coverage, suspicion)"
        " VALUES (1, 's1', 'acme', 'lib', 'pypi', 'rh', 'ch', 1, '[]', 1, 0.8, '[]')"
    )
    conn.execute(
        "INSERT INTO params (repo_id, param_id, raw_value, score, n, measured, reason)"
        " VALUES (1, 'newcomer_issue_supply', '5', 80.0, 5, 1, NULL)"
    )
    conn.commit()
    res = anchors._check_newcomer_db_v2(conn, "s1")
    assert res.verdict == anchors.PASS


def test_phone_and_pull_scripts_validation_and_bounds(tmp_path: Path) -> None:
    from phone import serve as phone_serve
    from scripts import gather, pull_from_phone

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "lan_token").write_text("short\n", encoding="utf-8")
    with pytest.raises(ValueError, match="shorter than 24 characters"):
        phone_serve.load_token(tmp_path)

    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        info = tarfile.TarInfo("raw/github_meta/acme__lib.json")
        data = b"{}"
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
        tf.addfile(info, io.BytesIO(data))  # duplicate member refused
    with pytest.raises(pull_from_phone.PullError):
        pull_from_phone._check_members(archive, "L0")

    # A real pr_window envelope runs past 64 MiB (the largest on record is 129 MiB): the layer is still taken.
    big = tmp_path / "big.tar.gz"
    size = 65 * 1024 * 1024
    with tarfile.open(big, "w:gz", compresslevel=1) as tf:
        info = tarfile.TarInfo("raw/pr_window/acme__lib.json")
        info.size = size
        tf.addfile(info, io.BytesIO(bytes(size)))
    assert [m.size for m in pull_from_phone._check_members(big, "L1")] == [size]

    assert gather.main(["--repo", "acme", "lib", "--ecosystem", "../bad"]) == 2


def test_legacy_v1_deepdive_and_gate5_ignored_n() -> None:
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    skewed_pr = RawPREvent(
        repo_owner="acme",
        repo_name="lib",
        pr_number=1,
        created_at=now,
        author_login="ext",
        author_association=AuthorAssociation.NONE,
        author_prior_merged_commits=0,
        closed_at=now - timedelta(hours=2),
        merged_at=now - timedelta(hours=2),
        is_external=True,
        response_latency_hours=1.0,
        maintainer_review_comments=1,
    )
    metrics = _raw_metrics([skewed_pr], now)
    assert metrics["tail_merge_latency_days"] == 0.0
    curve = _survival_curve([skewed_pr], now)
    assert all(pt[0] >= 0.0 for pt in curve)

    bundle = FeatureBundle(
        schema_version=2,
        repo=RepoIdentity(
            owner="acme",
            name="lib",
            ecosystem="pypi",
            archived=False,
            mirror_url=None,
            fork=False,
            license_spdx="MIT",
            default_branch="main",
        ),
        inputs={
            "gr14_ghost_rate": Measured(
                value=0.9,
                n=20,
                window_start=None,
                window_end=None,
                method="primary",
                source="pr_window",
                source_hash="0" * 64,
                fetched_at="2026-07-01T00:00:00Z",
            ),
            "vitality_commits_180d": Measured(
                value=10,
                n=10,
                window_start=None,
                window_end=None,
                method="primary",
                source="commits_180d",
                source_hash="0" * 64,
                fetched_at="2026-07-01T00:00:00Z",
            ),
        },
        pr_window=PRWindow(start=None, end=None, truncated=False, events=()),
        cohorts=None,
        provenance_hash="0" * 64,
    )
    ev = gates.GateEvidence(
        manifest_found=True,
        license_file_present=True,
        vitality_commits_180d=10,
        decided_external_pr=True,
        pr_window_complete=True,
        pr_rule_inputs_recorded=True,
        outside_prs_180d=15,
        pr_ignored=4,
        pr_ignored_n=5,  # < GATE5_MIN_N (10) -> must be UNDECIDED, not eligible
    )
    gate_res = gates.evaluate_gates(bundle, ev, now)
    assert "D-GATE-5" in gate_res.undecided
