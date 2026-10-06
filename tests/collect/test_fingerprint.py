"""Tests for bulk 100-repo/call GraphQL fingerprinting & dirty-only invalidation.

Subject: ``porchlamp.collect.fingerprint`` (``08-SUSTAINABLE-COLLECTION.md``
§2 / §3 / §6; ``D-COL-1``, ``D-COL-3``, ``D-COL-4``, ``W6-2``).

Verifies:
  * 100-repo aliased GraphQL query construction with parameterized variables
    (``$o0..$o99``, ``$n0..$n99``) and strict slug validation.
  * Partial-batch error salvage (a deleted/404 repo in a 100-repo batch does
    not discard the other 99 fingerprints).
  * Exact cadence tier classification boundaries (hot <= 7d, warm <= 30d,
    cold <= 365d, dormant > 365d or archived/disabled).
  * Per-collector invalidation matrix (``08`` §6): push/HEAD changes, release
    changes, issue timestamp changes, PR timestamp changes, metadata-only
    changes, and slow-clock (30d) gating for ``SLOW_BRANCH_COLLECTORS``.
  * Watermark advancement on clean envelopes without mutating payload/sha256.
  * Two consecutive passes over an unchanged 150-repo universe execute in 2
    GraphQL calls (2 points) per pass and trigger 0 collector fetches on pass 2.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

import pytest

from porchlamp.collect.envelope import read_envelope, write_envelope
from porchlamp.collect.fingerprint import (
    ALL_GATHER_COLLECTORS,
    COLD_DAYS,
    FINGERPRINT_BATCH_SIZE,
    HOT_DAYS,
    SLOW_BRANCH_COLLECTORS,
    SLOW_FACT_TTL_DAYS,
    WARM_DAYS,
    RepoFingerprint,
    advance_envelope_watermarks,
    append_fingerprints,
    build_fingerprint_batch_query,
    classify_cadence_tier,
    fetch_fingerprint_batch,
    invalidated_collectors,
    load_latest_fingerprints,
    run_fingerprint_pass,
)

NOW = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)


def _fp(
    owner: str = "acme",
    name: str = "widget",
    *,
    observed_at: str = "2026-09-12T12:00:00+00:00",
    pushed_at: Optional[str] = "2026-09-10T00:00:00Z",
    updated_at: Optional[str] = "2026-09-10T00:00:00Z",
    is_archived: bool = False,
    is_disabled: bool = False,
    stargazer_count: int = 100,
    fork_count: int = 20,
    pr_updated_at: Optional[str] = "2026-09-09T00:00:00Z",
    issue_updated_at: Optional[str] = "2026-09-08T00:00:00Z",
    release_created_at: Optional[str] = "2026-08-01T00:00:00Z",
    default_branch_oid: Optional[str] = "a" * 40,
) -> RepoFingerprint:
    return RepoFingerprint(
        owner=owner,
        name=name,
        observed_at=observed_at,
        pushed_at=pushed_at,
        updated_at=updated_at,
        is_archived=is_archived,
        is_disabled=is_disabled,
        stargazer_count=stargazer_count,
        fork_count=fork_count,
        pr_updated_at=pr_updated_at,
        issue_updated_at=issue_updated_at,
        release_created_at=release_created_at,
        default_branch_oid=default_branch_oid,
    )


def _gql_node(
    *,
    pushed_at: str = "2026-09-10T00:00:00Z",
    updated_at: str = "2026-09-10T00:00:00Z",
    oid: str = "a" * 40,
    stars: int = 100,
    forks: int = 20,
    archived: bool = False,
    disabled: bool = False,
    pr_updated_at: Optional[str] = "2026-09-09T00:00:00Z",
    issue_updated_at: Optional[str] = "2026-09-08T00:00:00Z",
    release_created_at: Optional[str] = "2026-08-01T00:00:00Z",
) -> dict[str, Any]:
    return {
        "pushedAt": pushed_at,
        "updatedAt": updated_at,
        "isArchived": archived,
        "isDisabled": disabled,
        "stargazerCount": stars,
        "forkCount": forks,
        "defaultBranchRef": {"target": {"oid": oid}},
        "pullRequests": {"nodes": [{"updatedAt": pr_updated_at}] if pr_updated_at else []},
        "issues": {"nodes": [{"updatedAt": issue_updated_at}] if issue_updated_at else []},
        "releases": {"nodes": [{"createdAt": release_created_at}] if release_created_at else []},
    }


class FakeBatchTransport:
    """Fake GraphQL transport that serves aliased ``r0..rN`` repository nodes."""

    def __init__(
        self,
        nodes_by_slug: Mapping[tuple[str, str], Optional[dict[str, Any]]],
        cost_per_call: int = 1,
    ) -> None:
        self.nodes_by_slug = dict(nodes_by_slug)
        self.cost_per_call = cost_per_call
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(
        self, query: str, variables: Optional[Mapping[str, Any]] = None
    ) -> dict[str, Any]:
        vars_dict = dict(variables or {})
        self.calls.append((query, vars_dict))
        data: dict[str, Any] = {
            "rateLimit": {"cost": self.cost_per_call, "remaining": 4999}
        }
        errors: list[dict[str, Any]] = []
        idx = 0
        while f"o{idx}" in vars_dict and f"n{idx}" in vars_dict:
            alias = f"r{idx}"
            owner = vars_dict[f"o{idx}"]
            name = vars_dict[f"n{idx}"]
            node = self.nodes_by_slug.get((owner, name))
            data[alias] = node
            if node is None:
                errors.append(
                    {
                        "type": "NOT_FOUND",
                        "path": [alias],
                        "message": f"Could not resolve to a Repository {owner}/{name}",
                    }
                )
            idx += 1
        out: dict[str, Any] = {"data": data}
        if errors:
            out["errors"] = errors
        return out


# --- Query construction & validation ----------------------------------------- #


def test_build_fingerprint_batch_query_100_repos_parameterized():
    assert FINGERPRINT_BATCH_SIZE == 100
    q = build_fingerprint_batch_query(FINGERPRINT_BATCH_SIZE)
    assert "rateLimit { cost remaining resetAt }" in q
    assert "r0: repository(owner: $o0, name: $n0)" in q
    assert "r99: repository(owner: $o99, name: $n99)" in q

    with pytest.raises(ValueError, match="count must be >= 1"):
        build_fingerprint_batch_query(0)


@pytest.mark.parametrize(
    "bad_slug",
    [
        ('acme" } }', "widget"),
        ("acme", 'widget" { id }'),
        ("acme/sub", "widget"),
        ("", "widget"),
        ("acme", "widget name"),
        ("..", "widget"),
    ],
)
def test_fetch_fingerprint_batch_rejects_malicious_slugs(bad_slug):
    transport = FakeBatchTransport({})
    with pytest.raises(ValueError, match="invalid repository"):
        fetch_fingerprint_batch([bad_slug], transport, NOW)


# --- Cadence tier classification --------------------------------------------- #


def test_classify_cadence_tier_exact_boundaries():
    assert HOT_DAYS == 7
    assert WARM_DAYS == 30
    assert COLD_DAYS == 365

    # 0 days and exactly 7 days -> hot
    assert classify_cadence_tier(_fp(pushed_at=NOW.isoformat()), NOW) == "hot"
    seven_days = (NOW - timedelta(days=7)).isoformat()
    assert classify_cadence_tier(_fp(pushed_at=seven_days, pr_updated_at=None, issue_updated_at=None, release_created_at=None), NOW) == "hot"

    # 7 days + 1 second and exactly 30 days -> warm
    just_over_7d = (NOW - timedelta(days=7, seconds=1)).isoformat()
    assert classify_cadence_tier(_fp(pushed_at=just_over_7d, pr_updated_at=None, issue_updated_at=None, release_created_at=None), NOW) == "warm"
    thirty_days = (NOW - timedelta(days=30)).isoformat()
    assert classify_cadence_tier(_fp(pushed_at=thirty_days, pr_updated_at=None, issue_updated_at=None, release_created_at=None), NOW) == "warm"

    # 30 days + 1 second and 365 days -> cold
    just_over_30d = (NOW - timedelta(days=30, seconds=1)).isoformat()
    assert classify_cadence_tier(_fp(pushed_at=just_over_30d, pr_updated_at=None, issue_updated_at=None, release_created_at=None), NOW) == "cold"
    one_year = (NOW - timedelta(days=365)).isoformat()
    assert classify_cadence_tier(_fp(pushed_at=one_year, pr_updated_at=None, issue_updated_at=None, release_created_at=None), NOW) == "cold"

    # > 365 days -> dormant
    over_year = (NOW - timedelta(days=365, seconds=1)).isoformat()
    assert classify_cadence_tier(_fp(pushed_at=over_year, pr_updated_at=None, issue_updated_at=None, release_created_at=None), NOW) == "dormant"

    # Archived or disabled repo -> always dormant even if pushed today
    assert classify_cadence_tier(_fp(pushed_at=NOW.isoformat(), is_archived=True), NOW) == "dormant"
    assert classify_cadence_tier(_fp(pushed_at=NOW.isoformat(), is_disabled=True), NOW) == "dormant"


# --- Batch fetch & partial-error salvage ------------------------------------- #


def test_fetch_fingerprint_batch_salvages_valid_nodes_when_one_repo_404s():
    transport = FakeBatchTransport(
        {
            ("acme", "alpha"): _gql_node(stars=111),
            ("acme", "deleted"): None,
            ("acme", "beta"): _gql_node(
                stars=222,
                release_created_at=None,
                pushed_at=(NOW - timedelta(days=400)).isoformat(),
                pr_updated_at=None,
                issue_updated_at=None,
            ),
        },
        cost_per_call=1,
    )
    resolved, unresolved, cost, rem = fetch_fingerprint_batch(
        [("acme", "alpha"), ("acme", "deleted"), ("acme", "beta")],
        transport,
        NOW,
    )
    assert cost == 1
    assert rem == 4999
    assert unresolved == ["acme__deleted"]
    assert set(resolved.keys()) == {"acme__alpha", "acme__beta"}
    assert resolved["acme__alpha"].stargazer_count == 111
    assert classify_cadence_tier(resolved["acme__alpha"], NOW) == "hot"
    assert resolved["acme__beta"].stargazer_count == 222
    assert resolved["acme__beta"].release_created_at is None
    assert classify_cadence_tier(resolved["acme__beta"], NOW) == "dormant"


# --- Invalidation matrix (08 §6) --------------------------------------------- #


def test_invalidated_collectors_first_observation_invalidates_all():
    curr = _fp()
    assert invalidated_collectors(None, curr) == ALL_GATHER_COLLECTORS


def test_invalidated_collectors_unchanged_fingerprint_returns_empty():
    prev = _fp(observed_at=(NOW - timedelta(days=1)).isoformat())
    curr = _fp(observed_at=NOW.isoformat())
    assert invalidated_collectors(prev, curr, slow_clock_due=True) == frozenset()


def test_invalidated_collectors_push_and_slow_clock_gating():
    assert SLOW_FACT_TTL_DAYS == 30
    prev = _fp()
    curr = _fp(pushed_at="2026-09-12T10:00:00Z", default_branch_oid="b" * 40)

    # When slow_clock_due=False, SLOW_BRANCH_COLLECTORS are NOT invalidated:
    inv_fast = invalidated_collectors(prev, curr, slow_clock_due=False)
    assert inv_fast == frozenset({"github_meta", "commits_180d", "commits_365d", "runs", "actions_runs"})
    assert not (inv_fast & SLOW_BRANCH_COLLECTORS)

    # When slow_clock_due=True and OID moved, SLOW_BRANCH_COLLECTORS ARE invalidated:
    inv_slow = invalidated_collectors(prev, curr, slow_clock_due=True)
    assert inv_slow == inv_fast | SLOW_BRANCH_COLLECTORS


def test_invalidated_collectors_release_change_invalidates_only_releases():
    prev = _fp(release_created_at="2026-08-01T00:00:00Z")
    curr = _fp(release_created_at="2026-09-12T00:00:00Z")
    assert invalidated_collectors(prev, curr) == frozenset({"releases"})


def test_invalidated_collectors_issue_change_invalidates_issues_and_gfi():
    prev = _fp(issue_updated_at="2026-09-08T00:00:00Z")
    curr = _fp(issue_updated_at="2026-09-12T00:00:00Z")
    assert invalidated_collectors(prev, curr) == frozenset({"issues_counts", "gfi"})


def test_invalidated_collectors_pr_change_invalidates_pr_dependents():
    prev = _fp(pr_updated_at="2026-09-09T00:00:00Z")
    curr = _fp(pr_updated_at="2026-09-12T00:00:00Z")
    assert invalidated_collectors(prev, curr) == frozenset(
        {"github_meta", "pr_events", "pr_reviews", "pr_window", "author_history"}
    )


def test_invalidated_collectors_stars_change_invalidates_github_meta_only():
    prev = _fp(stargazer_count=100)
    curr = _fp(stargazer_count=105)
    assert invalidated_collectors(prev, curr) == frozenset({"github_meta"})


# --- Persistence & watermark advancement ------------------------------------- #


def test_append_and_load_latest_fingerprints_round_trip(tmp_path: Path):
    state_file = tmp_path / "state" / "fingerprints.jsonl"
    f1 = _fp(stargazer_count=10, observed_at="2026-09-10T00:00:00+00:00")
    f2 = _fp(stargazer_count=25, observed_at="2026-09-12T00:00:00+00:00")
    append_fingerprints(state_file, [f1])
    append_fingerprints(state_file, [f2])
    loaded = load_latest_fingerprints(state_file)
    assert loaded["acme__widget"].stargazer_count == 25
    assert loaded["acme__widget"].observed_at == "2026-09-12T00:00:00+00:00"


def test_advance_envelope_watermarks_preserves_sha256_and_records_verified_unchanged_at(
    tmp_path: Path,
):
    raw_dir = tmp_path / "raw"
    old_at = "2026-09-01T00:00:00+00:00"
    write_envelope(
        raw_dir / "releases" / "acme__widget.json",
        source="test",
        data={"recent_count": 3},
        fetched_at=old_at,
    )
    orig_rel_sha = read_envelope(raw_dir / "releases" / "acme__widget.json").recorded_sha256

    updated = advance_envelope_watermarks(
        raw_dir,
        "acme__widget",
        unchanged_collectors=frozenset({"releases"}),
        observed_at=NOW,
    )
    assert updated == 1
    rel_env = read_envelope(raw_dir / "releases" / "acme__widget.json")
    assert rel_env.recorded_sha256 == orig_rel_sha
    assert rel_env.hash_matches_data
    doc = json.loads((raw_dir / "releases" / "acme__widget.json").read_text(encoding="utf-8"))
    assert doc["verified_unchanged_at"] == NOW.isoformat()


# --- End-to-end 2-pass sweep (W6-2 / D-COL-1) ------------------------------- #


def test_two_fingerprint_passes_unchanged_universe_zero_dirty_on_pass_two(tmp_path: Path):
    """W6-2 verification: 150 repos take 2 GraphQL batch calls (2 points);
    pass 2 over an unchanged universe reports 0 dirty repos and advances
    watermarks on existing envelopes."""
    repos = [(f"org{i // 10}", f"repo{i}") for i in range(150)]
    nodes = {slug: _gql_node(stars=i) for i, slug in enumerate(repos)}
    transport = FakeBatchTransport(nodes, cost_per_call=1)
    state_file = tmp_path / "state" / "fingerprints.jsonl"
    raw_dir = tmp_path / "raw"

    for owner, name in repos:
        write_envelope(
            raw_dir / "github_meta" / f"{owner}__{name}.json",
            source="github-graphql",
            data={"stars": 1},
            fetched_at="2026-09-11T00:00:00+00:00",
        )

    pass1 = run_fingerprint_pass(
        repos,
        state_file=state_file,
        raw_dir=raw_dir,
        transport=transport,
        now=NOW - timedelta(hours=6),
    )
    assert pass1.queries_executed == 2
    assert pass1.cost_points == 2
    assert len(pass1.fingerprints) == 150
    assert all(inv == ALL_GATHER_COLLECTORS for inv in pass1.invalidated_by_repo.values())

    # Pass 2: 6 hours later, universe unchanged -> 0 invalidated collectors!
    pass2 = run_fingerprint_pass(
        repos,
        state_file=state_file,
        raw_dir=raw_dir,
        transport=transport,
        now=NOW,
    )
    assert pass2.queries_executed == 2
    assert pass2.cost_points == 2
    assert all(len(inv) == 0 for inv in pass2.invalidated_by_repo.values())
    doc = json.loads((raw_dir / "github_meta" / "org0__repo0.json").read_text(encoding="utf-8"))
    assert doc["verified_unchanged_at"] == NOW.isoformat()

    # Pass 3: mutate 1 repo's release timestamp -> only that repo & collector dirty
    transport.nodes_by_slug[("org0", "repo0")] = _gql_node(
        stars=0, release_created_at="2026-09-12T12:00:00Z"
    )
    pass3 = run_fingerprint_pass(
        repos,
        state_file=state_file,
        raw_dir=raw_dir,
        transport=transport,
        now=NOW + timedelta(hours=1),
    )
    dirty_repos = {k: v for k, v in pass3.invalidated_by_repo.items() if v}
    assert dirty_repos == {"org0__repo0": frozenset({"releases"})}
