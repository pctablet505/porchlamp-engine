"""Tests for bulk 25-repo/call GraphQL fingerprinting & dirty-only invalidation.

Subject: ``porchlamp.collect.fingerprint`` (``08-SUSTAINABLE-COLLECTION.md``
§2 / §3 / §6; ``D-COL-1``, ``D-COL-3``, ``D-COL-4``, ``W6-2``).

Verifies:
  * 25-repo aliased GraphQL query construction with parameterized variables
    (``$o0..$o24``, ``$n0..$n24``) and strict slug validation.
  * Partial-batch error salvage (a deleted/404 repo in a batch does not
    discard the other fingerprints).
  * Retry: 5xx with backoff (3 tries), null aliases (RESOURCE_LIMITS_EXCEEDED)
    in smaller batches; a repo still unresolved is dirty, never clean.
  * Exact cadence tier classification boundaries (hot <= 7d, warm <= 30d,
    cold <= 365d, dormant > 365d or archived/disabled).
  * Per-collector invalidation matrix (``08`` §6): push/HEAD changes, release
    changes, issue timestamp changes, PR timestamp changes, metadata-only
    changes.
  * ``dirty_collectors``: missing envelopes, the weekly clock, the slow clock
    (30 d AND the branch moved since that collector's last fetch), and the
    14-day window anchors of ``releases`` / ``issues_counts``.
  * A pass writes nothing; the fingerprint is persisted per repo, and only
    when the invalidated collectors succeeded.
  * Two consecutive passes over an unchanged 150-repo universe execute in 6
    GraphQL calls (6 points) per pass and trigger 0 collector fetches on pass 2.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

import pytest

from porchlamp.collect.envelope import write_envelope
from porchlamp.collect.fingerprint import (
    ALL_GATHER_COLLECTORS,
    COLD_DAYS,
    FINGERPRINT_BATCH_SIZE,
    HOT_DAYS,
    SLOW_BRANCH_COLLECTORS,
    SLOW_FACT_TTL_DAYS,
    WARM_DAYS,
    WEEKLY_COLLECTORS,
    WEEKLY_TTL_DAYS,
    WINDOW_ANCHOR_REFRESH_DAYS,
    RepoFingerprint,
    append_fingerprints,
    build_fingerprint_batch_query,
    classify_cadence_tier,
    classify_prune_reason,
    dirty_collectors,
    fetch_fingerprint_batch,
    fetch_fingerprints_resilient,
    fingerprint_to_persist,
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


def test_build_fingerprint_batch_query_25_repos_parameterized():
    # 100 per query returns 502/504 or RESOURCE_LIMITS_EXCEEDED on the live API.
    assert FINGERPRINT_BATCH_SIZE == 25
    q = build_fingerprint_batch_query(FINGERPRINT_BATCH_SIZE)
    assert "rateLimit { cost remaining resetAt }" in q
    assert "r0: repository(owner: $o0, name: $n0)" in q
    assert "r24: repository(owner: $o24, name: $n24)" in q
    assert "r25:" not in q

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
    assert invalidated_collectors(prev, curr) == frozenset()


def test_invalidated_collectors_push_leaves_slow_facts_to_their_own_clock():
    prev = _fp()
    curr = _fp(pushed_at="2026-09-12T10:00:00Z", default_branch_oid="b" * 40)
    inv = invalidated_collectors(prev, curr)
    assert inv == frozenset({"github_meta", "commits_180d", "commits_365d", "runs", "actions_runs"})
    assert not (inv & SLOW_BRANCH_COLLECTORS)


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




# --- Retry: 5xx with backoff, null aliases in smaller batches ----------------- #


class _FailFirst:
    """Raises (as ``GhCliTransport`` does on a 5xx) for the first ``n`` calls."""

    def __init__(self, inner: FakeBatchTransport, n: int) -> None:
        self.inner = inner
        self.n = n
        self.calls = 0

    def execute(self, query: str, variables: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        self.calls += 1
        if self.calls <= self.n:
            raise RuntimeError("gh api graphql rc=1: HTTP 502")
        return self.inner.execute(query, variables)


class _LimitedAliases:
    """Answers batches over ``max_aliases`` with null aliases and
    ``RESOURCE_LIMITS_EXCEEDED`` (the live shape of an oversize batch)."""

    def __init__(self, inner: FakeBatchTransport, max_aliases: int) -> None:
        self.inner = inner
        self.max_aliases = max_aliases
        self.sizes: list[int] = []

    def execute(self, query: str, variables: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        n = len(variables or {}) // 2
        self.sizes.append(n)
        if n <= self.max_aliases:
            return self.inner.execute(query, variables)
        return {
            "data": {"rateLimit": {"cost": 3, "remaining": 4000}, **{f"r{i}": None for i in range(n)}},
            "errors": [
                {"type": "RESOURCE_LIMITS_EXCEEDED", "path": [f"r{i}"], "message": "too big"}
                for i in range(n)
            ],
        }


def _universe(n: int) -> list[tuple[str, str]]:
    return [(f"org{i // 10}", f"repo{i}") for i in range(n)]


def test_5xx_is_retried_with_backoff_then_resolves():
    repos = _universe(3)
    inner = FakeBatchTransport({slug: _gql_node() for slug in repos})
    transport = _FailFirst(inner, 2)
    sleeps: list[float] = []
    resolved, unresolved, queries, cost, _rem = fetch_fingerprints_resilient(
        repos, transport, NOW, sleep=sleeps.append
    )
    assert len(resolved) == 3 and unresolved == []
    assert queries == 3 and transport.calls == 3
    assert sleeps == [2.0, 4.0]
    assert cost == 1


def test_5xx_three_failures_leave_the_batch_unresolved():
    repos = _universe(3)
    transport = _FailFirst(FakeBatchTransport({slug: _gql_node() for slug in repos}), 99)
    sleeps: list[float] = []
    resolved, unresolved, queries, _cost, _rem = fetch_fingerprints_resilient(
        repos, transport, NOW, sleep=sleeps.append
    )
    assert resolved == {}
    assert sorted(unresolved) == sorted(f"{o}__{n}" for o, n in repos)
    assert transport.calls == 3  # at most 3 tries
    assert queries == 3


def test_null_aliases_are_retried_in_smaller_batches():
    repos = _universe(25)
    inner = FakeBatchTransport({slug: _gql_node(stars=i) for i, slug in enumerate(repos)})
    transport = _LimitedAliases(inner, max_aliases=7)
    resolved, unresolved, queries, _cost, _rem = fetch_fingerprints_resilient(
        repos, transport, NOW, sleep=lambda s: None
    )
    assert unresolved == []
    assert len(resolved) == 25
    assert transport.sizes[0] == 25 and max(transport.sizes[1:]) < 25
    assert queries == len(transport.sizes)


def test_not_found_repo_is_final_and_not_retried_but_stays_unresolved():
    repos = [("acme", "alpha"), ("acme", "deleted")]
    inner = FakeBatchTransport({("acme", "alpha"): _gql_node(), ("acme", "deleted"): None})
    resolved, unresolved, queries, _cost, _rem = fetch_fingerprints_resilient(
        repos, inner, NOW, sleep=lambda s: None
    )
    assert set(resolved) == {"acme__alpha"}
    assert unresolved == ["acme__deleted"]
    assert queries == 1


def test_pass_over_batches_of_25_reports_unresolved_as_dirty(tmp_path: Path):
    """A repo the pass cannot resolve is dirty (fast collectors), never clean."""
    repos = _universe(30)
    inner = FakeBatchTransport({slug: _gql_node() for slug in repos if slug != ("org0", "repo3")})
    state_file = tmp_path / "state" / "fingerprints.jsonl"
    res = run_fingerprint_pass(
        repos, state_file=state_file, raw_dir=tmp_path / "raw", transport=inner, now=NOW,
        sleep=lambda s: None,
    )
    assert res.unresolved_keys == ("org0__repo3",)
    dirty = res.dirty_by_repo["org0__repo3"]
    assert dirty and {"pr_window", "commits_365d", "releases"} <= dirty
    # a resolved repo with no envelopes yet is dirty too (missing envelopes)
    assert res.dirty_by_repo["org0__repo0"] == ALL_GATHER_COLLECTORS


# --- dirty_collectors: missing envelopes, clocks, window anchors --------------- #


ECO = "pypi"


def _seed(raw_dir: Path, fp: RepoFingerprint, *, fetched_at: datetime,
          skip: tuple[str, ...] = (), anchors: Optional[Mapping[str, datetime]] = None,
          ages: Optional[Mapping[str, timedelta]] = None) -> None:
    for collector in ALL_GATHER_COLLECTORS - set(skip):
        when = fetched_at - (ages or {}).get(collector, timedelta(0))
        data: dict[str, Any] = {"x": 1}
        if anchors and collector in anchors:
            data["window_anchor"] = anchors[collector].isoformat()
        elif collector in ("releases", "issues_counts"):
            data["window_anchor"] = when.isoformat()
        key = f"{ECO}__{fp.name}" if collector == "downloads" else fp.key
        write_envelope(raw_dir / collector / f"{key}.json", source="t", data=data,
                       fetched_at=when.isoformat())


def _dirty(raw_dir: Path, previous: Optional[RepoFingerprint], current: Optional[RepoFingerprint],
           now: datetime = NOW) -> frozenset[str]:
    return dirty_collectors(previous, current, raw_dir=raw_dir, owner="acme", name="widget",
                            now=now, ecosystem=ECO)


def test_unchanged_repo_with_fresh_envelopes_is_clean(tmp_path: Path):
    fp = _fp(default_branch_oid="a" * 40)
    _seed(tmp_path, fp, fetched_at=NOW - timedelta(days=1))
    assert _dirty(tmp_path, fp, fp) == frozenset()


def test_missing_envelope_of_any_collector_is_dirty(tmp_path: Path):
    fp = _fp()
    _seed(tmp_path, fp, fetched_at=NOW - timedelta(days=1), skip=("pr_window", "governance", "scorecard"))
    assert _dirty(tmp_path, fp, fp) == frozenset({"pr_window", "governance", "scorecard"})


def test_unresolved_repo_is_dirty_for_every_fingerprint_driven_collector(tmp_path: Path):
    fp = _fp()
    _seed(tmp_path, fp, fetched_at=NOW - timedelta(days=1))
    dirty = _dirty(tmp_path, fp, None)
    assert dirty
    assert {"pr_window", "commits_365d", "releases", "issues_counts", "github_meta"} <= dirty
    assert not (dirty & SLOW_BRANCH_COLLECTORS - {"governance"} - {"contributors", "tree", "repo_object"})


def test_weekly_collectors_refetch_on_the_weekly_clock(tmp_path: Path):
    assert WEEKLY_TTL_DAYS == 7
    assert WEEKLY_COLLECTORS == {"depsdev", "scorecard", "downloads", "dependents"}
    fp = _fp()
    ages = {
        "depsdev": timedelta(days=6, hours=23),
        "scorecard": timedelta(days=7),
        "downloads": timedelta(days=30),
        "dependents": timedelta(days=8),
    }
    _seed(tmp_path, fp, fetched_at=NOW, ages=ages)
    assert _dirty(tmp_path, fp, fp) == frozenset({"scorecard", "downloads", "dependents"})


def test_slow_collector_needs_the_30_day_clock_and_a_branch_move_since_its_last_fetch(tmp_path: Path):
    assert SLOW_FACT_TTL_DAYS == 30
    moved = "b" * 40
    prev = RepoFingerprint(**{**_fp(default_branch_oid="a" * 40).to_dict(),
                              "slow_oids": {c: "a" * 40 for c in SLOW_BRANCH_COLLECTORS}})
    cur_moved = _fp(default_branch_oid=moved)
    cur_same = _fp(default_branch_oid="a" * 40)

    def run(days: float, current: RepoFingerprint) -> frozenset[str]:
        raw = tmp_path / f"d{days}-{current.default_branch_oid[0]}"
        _seed(raw, prev, fetched_at=NOW,
              ages={c: timedelta(days=days) for c in SLOW_BRANCH_COLLECTORS})
        return _dirty(raw, prev, current) & SLOW_BRANCH_COLLECTORS

    assert run(30, cur_moved) == SLOW_BRANCH_COLLECTORS          # exactly 30 d, moved -> due
    assert run(31, cur_moved) == SLOW_BRANCH_COLLECTORS
    assert run(29.9, cur_moved) == frozenset()                   # clock not due yet
    assert run(31, cur_same) == frozenset()                      # clock due, branch did not move
    assert run(400, cur_same) == frozenset()


def test_slow_collector_branch_move_is_remembered_across_passes(tmp_path: Path):
    """The branch moved on day 5 and the pass consumed that move (the fingerprint was
    persisted); at day 31 the slow collector is still due, because its baseline is the
    oid at ITS last fetch, not the previous pass's oid."""
    baseline = "a" * 40
    after_move = "b" * 40
    prev = RepoFingerprint(**{**_fp(default_branch_oid=after_move).to_dict(),
                              "slow_oids": {c: baseline for c in SLOW_BRANCH_COLLECTORS}})
    cur = _fp(default_branch_oid=after_move)  # unchanged vs previous: same pass sees no move
    _seed(tmp_path, prev, fetched_at=NOW,
          ages={c: timedelta(days=31) for c in SLOW_BRANCH_COLLECTORS})
    assert _dirty(tmp_path, prev, cur) == SLOW_BRANCH_COLLECTORS


def test_slow_collector_without_a_recorded_baseline_counts_as_moved(tmp_path: Path):
    prev = _fp()  # no slow_oids
    _seed(tmp_path, prev, fetched_at=NOW - timedelta(hours=1),
          ages={c: timedelta(days=31) for c in SLOW_BRANCH_COLLECTORS})
    assert _dirty(tmp_path, prev, prev) == SLOW_BRANCH_COLLECTORS


def test_missing_slow_envelope_is_fetched_at_once(tmp_path: Path):
    prev = RepoFingerprint(**{**_fp().to_dict(), "slow_oids": {c: "a" * 40 for c in SLOW_BRANCH_COLLECTORS}})
    _seed(tmp_path, prev, fetched_at=NOW - timedelta(hours=1), skip=("governance",))
    assert _dirty(tmp_path, prev, prev) == frozenset({"governance"})


def test_stale_window_anchor_refetches_releases_and_issues_counts(tmp_path: Path):
    """derive marks a window anchor older than 14 days Unmeasured (openssf_criticality):
    --dirty must refresh releases and issues_counts before that, though nothing moved."""
    assert WINDOW_ANCHOR_REFRESH_DAYS == 13
    fp = _fp()
    anchors = {
        "releases": NOW - timedelta(days=13),
        "issues_counts": NOW - timedelta(days=12, hours=23),
    }
    _seed(tmp_path, fp, fetched_at=NOW - timedelta(hours=1), anchors=anchors)
    assert _dirty(tmp_path, fp, fp) == frozenset({"releases"})
    anchors = {"releases": NOW - timedelta(days=20), "issues_counts": NOW - timedelta(days=15)}
    _seed(tmp_path, fp, fetched_at=NOW - timedelta(hours=1), anchors=anchors)
    assert _dirty(tmp_path, fp, fp) == frozenset({"releases", "issues_counts"})


# --- Persisting the fingerprint ---------------------------------------------- #


def test_fingerprint_is_not_persisted_when_an_invalidated_collector_failed():
    prev = _fp(pr_updated_at="2026-09-09T00:00:00Z")
    cur = _fp(pr_updated_at="2026-09-12T00:00:00Z")
    inv = invalidated_collectors(prev, cur)
    assert fingerprint_to_persist(prev, cur, inv, fetched_ok=inv - {"pr_window"},
                                  failed={"pr_window"}) is None
    rec = fingerprint_to_persist(prev, cur, inv, fetched_ok=inv, failed=set())
    assert rec is not None and rec.pr_updated_at == "2026-09-12T00:00:00Z"


def test_persisted_fingerprint_records_the_slow_baseline_of_fetched_collectors():
    prev = RepoFingerprint(**{**_fp(default_branch_oid="a" * 40).to_dict(),
                              "slow_oids": {"tree": "a" * 40, "governance": "a" * 40}})
    cur = _fp(default_branch_oid="b" * 40)
    inv = invalidated_collectors(prev, cur)
    rec = fingerprint_to_persist(prev, cur, inv, fetched_ok=inv | {"governance"}, failed=set())
    assert rec is not None
    assert rec.slow_oids == {"tree": "a" * 40, "governance": "b" * 40}


def test_nothing_persisted_for_an_unchanged_repo():
    fp = _fp()
    assert fingerprint_to_persist(fp, _fp(observed_at="2026-09-13T00:00:00+00:00"),
                                  frozenset(), set(), set()) is None


def test_first_observation_with_all_collectors_fetched_is_persisted_with_baselines():
    cur = _fp(default_branch_oid="c" * 40)
    rec = fingerprint_to_persist(None, cur, ALL_GATHER_COLLECTORS, ALL_GATHER_COLLECTORS, set())
    assert rec is not None
    assert rec.slow_oids == {c: "c" * 40 for c in SLOW_BRANCH_COLLECTORS}


def test_slow_oids_round_trip_through_the_state_file(tmp_path: Path):
    fp = RepoFingerprint(**{**_fp().to_dict(), "slow_oids": {"tree": "a" * 40, "governance": None}})
    state_file = tmp_path / "fingerprints.jsonl"
    append_fingerprints(state_file, [fp])
    assert load_latest_fingerprints(state_file)["acme__widget"].slow_oids == fp.slow_oids


# --- Persistence ------------------------------------------------------------- #


def test_append_and_load_latest_fingerprints_round_trip(tmp_path: Path):
    state_file = tmp_path / "state" / "fingerprints.jsonl"
    f1 = _fp(stargazer_count=10, observed_at="2026-09-10T00:00:00+00:00")
    f2 = _fp(stargazer_count=25, observed_at="2026-09-12T00:00:00+00:00")
    append_fingerprints(state_file, [f1])
    append_fingerprints(state_file, [f2])
    loaded = load_latest_fingerprints(state_file)
    assert loaded["acme__widget"].stargazer_count == 25
    assert loaded["acme__widget"].observed_at == "2026-09-12T00:00:00+00:00"


# --- End-to-end 2-pass sweep (W6-2 / D-COL-1) ------------------------------- #


def test_a_pass_writes_nothing_and_does_not_touch_envelopes(tmp_path: Path):
    """No watermark rewrite: unchanged envelopes are byte-identical after a pass
    (rewriting ~5,700 repos x 20 collectors each run would wear the SSD), and the
    pass itself does not append fingerprint state."""
    repos = _universe(3)
    transport = FakeBatchTransport({slug: _gql_node() for slug in repos})
    raw_dir = tmp_path / "raw"
    for owner, name in repos:
        _seed(raw_dir, _fp(owner=owner, name=name), fetched_at=NOW - timedelta(days=1))
    before = {p: p.read_bytes() for p in raw_dir.rglob("*.json")}
    assert before
    state_file = tmp_path / "state" / "fingerprints.jsonl"
    run_fingerprint_pass(repos, state_file=state_file, raw_dir=raw_dir, transport=transport, now=NOW)
    assert {p: p.read_bytes() for p in raw_dir.rglob("*.json")} == before
    assert not state_file.exists()
    assert not list(raw_dir.rglob("*.tmp"))


def test_two_fingerprint_passes_unchanged_universe_zero_dirty_on_pass_two(tmp_path: Path):
    """W6-2 verification: 150 repos take 6 GraphQL batch calls (6 points);
    after pass 1's fingerprints are persisted, pass 2 over an unchanged universe
    reports 0 dirty repos."""
    repos = _universe(150)
    nodes = {slug: _gql_node(stars=i) for i, slug in enumerate(repos)}
    transport = FakeBatchTransport(nodes, cost_per_call=1)
    state_file = tmp_path / "state" / "fingerprints.jsonl"
    raw_dir = tmp_path / "raw"
    ecosystems = {f"{o}__{n}": ECO for o, n in repos}

    pass1 = run_fingerprint_pass(repos, state_file=state_file, raw_dir=raw_dir,
                                 transport=transport, now=NOW - timedelta(hours=6),
                                 ecosystems=ecosystems)
    assert pass1.queries_executed == 6
    assert pass1.cost_points == 6
    assert len(pass1.fingerprints) == 150
    assert all(inv == ALL_GATHER_COLLECTORS for inv in pass1.invalidated_by_repo.values())

    # The caller gathers every collector, then persists each repo's fingerprint.
    for fp in pass1.fingerprints.values():
        _seed(raw_dir, fp, fetched_at=NOW - timedelta(hours=6))
        rec = fingerprint_to_persist(None, fp, ALL_GATHER_COLLECTORS, ALL_GATHER_COLLECTORS, set())
        assert rec is not None
        append_fingerprints(state_file, [rec])

    pass2 = run_fingerprint_pass(repos, state_file=state_file, raw_dir=raw_dir,
                                 transport=transport, now=NOW, ecosystems=ecosystems)
    assert pass2.queries_executed == 6
    assert pass2.cost_points == 6
    assert all(len(inv) == 0 for inv in pass2.dirty_by_repo.values())

    # Pass 3: mutate 1 repo's release timestamp -> only that repo & collector dirty
    transport.nodes_by_slug[("org0", "repo0")] = _gql_node(
        stars=0, release_created_at="2026-09-12T12:00:00Z"
    )
    pass3 = run_fingerprint_pass(repos, state_file=state_file, raw_dir=raw_dir,
                                 transport=transport, now=NOW + timedelta(hours=1),
                                 ecosystems=ecosystems)
    dirty_repos = {k: v for k, v in pass3.dirty_by_repo.items() if v}
    assert dirty_repos == {"org0__repo0": frozenset({"releases"})}


def _make_pr_window_envelope(raw_dir: Path, owner: str, name: str, prs: list[dict], fetched_at: str,
                             *, truncated: bool = False) -> None:
    """A ``pr_window`` envelope in the collector's shape: ``truncated`` says whether the
    collector stopped at its PR cap, and the window is the 180 days before ``fetched_at``."""
    from porchlamp.collect.envelope import write_envelope
    prw_dir = raw_dir / "pr_window"
    prw_dir.mkdir(parents=True, exist_ok=True)
    write_envelope(
        prw_dir / f"{owner}__{name}.json",
        source="test-pr-window",
        data={
            "window_anchor": fetched_at,
            "window_days": 180,
            "truncated": truncated,
            "prs": prs,
            "derived": [
                {
                    "number": pr["number"],
                    "is_external": True,
                    "is_bot_author": False,
                    "first_substantive_response_at": None,
                }
                for pr in prs
            ],
        },
        fetched_at=fetched_at,
    )


def test_is_confirmed_black_hole_in_raw_review_handoff_returns_false(tmp_path: Path):
    from porchlamp.collect.fingerprint import is_confirmed_black_hole_in_raw
    raw_dir = tmp_path / "raw"
    owner, name = "git", "git"
    # 10 PRs with review handoff notice comments
    prs = [
        {
            "number": i,
            "createdAt": (NOW - timedelta(days=30 + i)).isoformat(),
            "authorAssociation": "NONE",
            "author": {"login": f"user{i}", "__typename": "User"},
            "comments": [
                {
                    "author_login": "gitgitgadget",
                    "body": "welcome to gitgitgadget",
                }
            ],
            "reviews": {"nodes": []},
        }
        for i in range(1, 11)
    ]
    _make_pr_window_envelope(raw_dir, owner, name, prs, NOW.isoformat())
    assert is_confirmed_black_hole_in_raw(raw_dir, owner, name, now=NOW) is False


def test_is_confirmed_black_hole_in_raw_confirmed_black_hole_returns_true(tmp_path: Path):
    from porchlamp.collect.fingerprint import is_confirmed_black_hole_in_raw
    raw_dir = tmp_path / "raw"
    owner, name = "abandoned", "project"
    # 15 external PRs opened 30-45 days ago with zero response
    prs = [
        {
            "number": i,
            "createdAt": (NOW - timedelta(days=30 + i)).isoformat(),
            "authorAssociation": "NONE",
            "author": {"login": f"user{i}", "__typename": "User"},
            "comments": [],
            "reviews": {"nodes": []},
        }
        for i in range(1, 16)
    ]
    _make_pr_window_envelope(raw_dir, owner, name, prs, NOW.isoformat())
    assert is_confirmed_black_hole_in_raw(raw_dir, owner, name, now=NOW) is True


def _thin_repo(raw_dir: Path, name: str, *, outside: int, insiders: int = 3, off_platform: bool = False,
               truncated: bool = False) -> None:
    """A repository whose recorded window holds ``outside`` outside PRs (all merged, so D-GATE-6's decided-PR test
    passes) and ``insiders`` PRs by its own members."""
    write_envelope(
        raw_dir / "repo_object" / f"org__{name}.json", source="test", fetched_at=NOW.isoformat(),
        data={
            "archived": False, "mirror_url": None, "fork": False, "license_spdx_id": "MIT",
            "pull_requests_total": outside + insiders, "tree_paths_le2": ["pyproject.toml", "LICENSE", "README.md"],
            "tree_truncated": False,
            "offplatform_paths": ["Documentation/process/submitting-patches.rst"] if off_platform else [],
            "maintainers_has_l_lines": off_platform, "readme_off_platform_hits": ["mailing_list"] if off_platform else [],
        })
    prs = []
    for i in range(1, outside + insiders + 1):
        is_outside = i <= outside
        created = NOW - timedelta(days=30 + i)
        prs.append({
            "number": i, "createdAt": created.isoformat(), "mergedAt": (created + timedelta(days=2)).isoformat(),
            "authorAssociation": "NONE" if is_outside else "MEMBER",
            "author": {"login": f"user{i}", "__typename": "User"}, "comments": [], "reviews": {"nodes": []},
        })
    _make_pr_window_envelope(raw_dir, "org", name, prs, NOW.isoformat(), truncated=truncated)


def test_classify_prune_reason_prunes_a_project_with_fewer_than_three_outside_prs(tmp_path: Path):
    """RFC-0027: two outside PRs, both merged, are a decided workflow but too little outside traffic to collect on."""
    _thin_repo(tmp_path, "thin", outside=2)
    assert classify_prune_reason(None, raw_dir=tmp_path, owner="org", name="thin", now=NOW) == "no_external_workflow"


def test_classify_prune_reason_keeps_a_project_with_three_outside_prs(tmp_path: Path):
    _thin_repo(tmp_path, "enough", outside=3)
    assert classify_prune_reason(None, raw_dir=tmp_path, owner="org", name="enough", now=NOW) is None


def test_classify_prune_reason_keeps_a_thin_project_whose_record_stops_short_of_the_window(tmp_path: Path):
    _thin_repo(tmp_path, "cut", outside=1, truncated=True)
    assert classify_prune_reason(None, raw_dir=tmp_path, owner="org", name="cut", now=NOW) is None


def test_classify_prune_reason_keeps_a_thin_project_that_works_off_platform(tmp_path: Path):
    _thin_repo(tmp_path, "list", outside=1, off_platform=True)
    assert classify_prune_reason(None, raw_dir=tmp_path, owner="org", name="list", now=NOW) is None


def test_classify_prune_reason_keeps_a_project_with_no_pr_window_yet(tmp_path: Path):
    _thin_repo(tmp_path, "fresh", outside=0)
    (tmp_path / "pr_window" / "org__fresh.json").unlink()
    # no decided external PR recorded and no window: nothing to prune on but the older rule, which finds none
    assert classify_prune_reason(None, raw_dir=tmp_path, owner="org", name="fresh", now=NOW) is None


def test_is_confirmed_black_hole_in_raw_leaves_a_slow_project_alone(tmp_path: Path):
    """RFC-0026: the same 15 PRs unanswered for 14 days but merged by a maintainer 25 days after opening
    are a slow project, not a black hole; the collector must keep collecting it."""
    from porchlamp.collect.fingerprint import is_confirmed_black_hole_in_raw
    raw_dir = tmp_path / "raw"
    owner, name = "slow", "project"
    prs = []
    for i in range(1, 16):
        created = NOW - timedelta(days=40 + i)
        prs.append({
            "number": i,
            "createdAt": created.isoformat(),
            "mergedAt": (created + timedelta(days=25)).isoformat(),
            "authorAssociation": "NONE",
            "author": {"login": f"user{i}", "__typename": "User"},
            "comments": [],
            "reviews": {"nodes": []},
        })
    _make_pr_window_envelope(raw_dir, owner, name, prs, NOW.isoformat())
    assert is_confirmed_black_hole_in_raw(raw_dir, owner, name, now=NOW) is False


def test_is_confirmed_black_hole_in_raw_refuses_a_record_cut_at_the_pr_cap(tmp_path: Path):
    """The same silent PRs in a record the collector cut at its PR cap: D-GATE-5 is
    undecided on a record that does not reach back to the window's start (RFC-0011), so
    the collector does not confirm a black hole from it either."""
    from porchlamp.collect.fingerprint import is_confirmed_black_hole_in_raw
    raw_dir = tmp_path / "raw"
    owner, name = "busy", "project"
    prs = [
        {
            "number": i,
            "createdAt": (NOW - timedelta(days=30 + i)).isoformat(),
            "authorAssociation": "NONE",
            "author": {"login": f"user{i}", "__typename": "User"},
            "comments": [],
            "reviews": {"nodes": []},
        }
        for i in range(1, 16)
    ]
    _make_pr_window_envelope(raw_dir, owner, name, prs, NOW.isoformat(), truncated=True)
    assert is_confirmed_black_hole_in_raw(raw_dir, owner, name, now=NOW) is False


def test_run_fingerprint_pass_black_hole_strips_deep_collectors(tmp_path: Path):
    from porchlamp.collect.fingerprint import DEEP_COLLECTORS_SKIPPED_ON_BLACK_HOLE
    raw_dir = tmp_path / "raw"
    state_file = tmp_path / "state" / "fingerprints.jsonl"
    owner, name = "blackhole", "repo"
    key = f"{owner}__{name}"

    prs = [
        {
            "number": i,
            "createdAt": (NOW - timedelta(days=30 + i)).isoformat(),
            "authorAssociation": "NONE",
            "author": {"login": f"user{i}", "__typename": "User"},
            "comments": [],
            "reviews": {"nodes": []},
        }
        for i in range(1, 16)
    ]
    _make_pr_window_envelope(raw_dir, owner, name, prs, NOW.isoformat())

    transport = FakeBatchTransport({(owner, name): _gql_node(stars=10)})
    res = run_fingerprint_pass(
        [(owner, name)],
        state_file=state_file,
        raw_dir=raw_dir,
        transport=transport,
        now=NOW,
        prune_ineligible=True,
    )

    # Repo is not pruned by classify_prune_reason (black holes are not pruned in prune_ineligible)
    assert key not in res.pruned_by_repo

    # But deep collectors are stripped from dirty_by_repo and invalidated_by_repo
    assert key in res.dirty_by_repo
    dirty = res.dirty_by_repo[key]
    assert not (dirty & DEEP_COLLECTORS_SKIPPED_ON_BLACK_HOLE)
    # pr_window and github_meta remain in the dirty set because it's a new repo
    assert "pr_window" in dirty
    assert "github_meta" in dirty


def test_is_confirmed_black_hole_in_raw_graphql_comments_and_timeline_items(tmp_path: Path):
    from porchlamp.collect.fingerprint import is_confirmed_black_hole_in_raw
    raw_dir = tmp_path / "raw"

    # 1. GraphQL comments.nodes shape for review handoff -> returns False
    prs_handoff = [
        {
            "number": i,
            "createdAt": (NOW - timedelta(days=30 + i)).isoformat(),
            "authorAssociation": "NONE",
            "author": {"login": f"user{i}", "__typename": "User"},
            "comments": {
                "nodes": [
                    {
                        "author": {"login": "gitgitgadget", "__typename": "Bot"},
                        "bodyText": "welcome to gitgitgadget",
                    }
                ]
            },
            "reviews": {"nodes": []},
        }
        for i in range(1, 11)
    ]
    _make_pr_window_envelope(raw_dir, "git", "graphql-shape", prs_handoff, NOW.isoformat())
    assert is_confirmed_black_hole_in_raw(raw_dir, "git", "graphql-shape", now=NOW) is False

    # 2. PRs closed within 14d by maintainer via timelineItems ClosedEvent -> answered, not black hole
    prs_maint_closed = [
        {
            "number": i,
            "createdAt": (NOW - timedelta(days=30 + i)).isoformat(),
            "closedAt": (NOW - timedelta(days=28 + i)).isoformat(),
            "state": "CLOSED",
            "authorAssociation": "NONE",
            "author": {"login": f"user{i}", "__typename": "User"},
            "timelineItems": {
                "nodes": [
                    {
                        "__typename": "ClosedEvent",
                        "createdAt": (NOW - timedelta(days=28 + i)).isoformat(),
                        "actor": {"__typename": "User", "login": "maint-user"},
                    }
                ]
            },
            "comments": {"nodes": []},
            "reviews": {"nodes": []},
        }
        for i in range(1, 16)
    ]
    _make_pr_window_envelope(raw_dir, "active", "maint-closed", prs_maint_closed, NOW.isoformat())
    assert is_confirmed_black_hole_in_raw(raw_dir, "active", "maint-closed", now=NOW) is False




def _write_window_with_stale_derived(raw_dir: Path, owner: str, name: str, prs: list[dict]) -> None:
    """A ``pr_window`` envelope whose stored ``derived`` list, from an older run, calls
    every author a bot and no PR external: #57, no reader may trust it over the nodes."""
    prw_dir = raw_dir / "pr_window"
    prw_dir.mkdir(parents=True, exist_ok=True)
    write_envelope(
        prw_dir / f"{owner}__{name}.json", source="test-pr-window",
        data={"window_anchor": NOW.isoformat(), "window_days": 180, "truncated": False, "prs": prs,
              "derived": [{"number": pr["number"], "is_external": False, "is_bot_author": True} for pr in prs]},
        fetched_at=NOW.isoformat(),
    )


def test_review_handoff_reads_the_pr_nodes_not_the_stored_derived_list(tmp_path: Path):
    """Ten outside PRs (external, human authors), each carrying a GitGitGadget welcome:
    a hand-off judged from the nodes. A reader of the stale stored list finds no outside
    PR and answers False."""
    from porchlamp.collect.fingerprint import _review_handoff

    prs = [{"number": i, "createdAt": (NOW - timedelta(days=30 + i)).isoformat(), "authorAssociation": "NONE",
            "author": {"login": f"user{i}", "__typename": "User"},
            "comments": {"nodes": [{"author": {"login": "gitgitgadget", "__typename": "User"},
                                    "bodyText": "Welcome to GitGitGadget"}]},
            "reviews": {"nodes": []}} for i in range(1, 11)]
    _write_window_with_stale_derived(tmp_path / "raw", "git", "git", prs)
    assert _review_handoff(tmp_path / "raw", "git__git") is True


def test_decided_external_pr_reads_the_pr_nodes_not_the_stored_derived_list(tmp_path: Path):
    """One outside PR merged ten days ago: a decided external PR, judged from the node.
    A reader of the stale stored list skips it as a bot's and finds none."""
    from porchlamp.collect.fingerprint import _has_decided_external_pr_in_raw

    prs = [{"number": 1, "createdAt": (NOW - timedelta(days=20)).isoformat(),
            "mergedAt": (NOW - timedelta(days=10)).isoformat(), "closedAt": (NOW - timedelta(days=10)).isoformat(),
            "authorAssociation": "CONTRIBUTOR", "author": {"login": "alice", "__typename": "User"},
            "comments": {"nodes": []}, "reviews": {"nodes": []}}]
    _write_window_with_stale_derived(tmp_path / "raw", "acme", "widget", prs)
    assert _has_decided_external_pr_in_raw(tmp_path / "raw", "acme", "widget", NOW) is True
