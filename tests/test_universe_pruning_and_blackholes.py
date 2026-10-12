"""Unit tests for pre-collection universe/fingerprint pruning and black_hole ranking."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from porchlamp.collect.envelope import write_envelope
from porchlamp.collect.fingerprint import (
    PRUNED_REASONS,
    RepoFingerprint,
    classify_prune_reason,
    run_fingerprint_pass,
)
from porchlamp.core import constants
from porchlamp.core.engine import Snapshot, score
from porchlamp.core.gates import (
    REASON_BLACK_HOLE,
    GateEvidence,
    evaluate_gates,
    is_black_hole_bundle,
)
from porchlamp.core.models import (
    Cohorts,
    FeatureBundle,
    Measured,
    PRWindow,
    PRWindowEvent,
    RepoIdentity,
)
from porchlamp.core.pipeline import score_snapshot
from scripts import build_universe, gather

NOW = datetime(2026, 10, 12, 0, 0, 0, tzinfo=timezone.utc)


class _DummyPopulations:
    def population(self, ecosystem: str, param: str) -> object | None:
        return True

    def ecdf(self, ecosystem: str, param: str) -> Any:
        return lambda _v: 0.50


class _FakeTransport:
    def __init__(self, nodes_by_alias: Mapping[str, Any]) -> None:
        self._nodes = dict(nodes_by_alias)

    def execute(
        self, query: str, variables: Optional[Mapping[str, Any]] = None
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "rateLimit": {"cost": 1, "remaining": 4999, "resetAt": "2026-10-12T01:00:00Z"}
        }
        data.update(self._nodes)
        return {"data": data}


def _make_bundle(
    *,
    owner: str = "acme",
    name: str = "blackhole-lib",
    ghost_rate: float = 0.80,
    ghost_n: int = 12,
) -> FeatureBundle:
    inputs: dict[str, Any] = {}
    for input_id in constants.PARAM_INPUT_IDS:
        if input_id == "gr14_ghost_rate":
            inputs[input_id] = Measured(
                value=ghost_rate,
                n=ghost_n,
                window_start="2026-04-15T00:00:00Z",
                window_end="2026-10-12T00:00:00Z",
                method="primary",
                source=f"raw/pr_window/{owner}__{name}.json",
                source_hash="a" * 64,
                fetched_at="2026-10-12T00:00:00Z",
            )
        elif input_id == "legal_ip":
            inputs[input_id] = Measured(
                value="DCO",
                n=1,
                window_start=None,
                window_end=None,
                method="primary",
                source=f"raw/governance/{owner}__{name}.json",
                source_hash="a" * 64,
                fetched_at="2026-10-12T00:00:00Z",
            )
        elif input_id == "promotion_ladder":
            inputs[input_id] = Measured(
                value="DOCUMENTED",
                n=1,
                window_start=None,
                window_end=None,
                method="primary",
                source=f"raw/governance/{owner}__{name}.json",
                source_hash="a" * 64,
                fetched_at="2026-10-12T00:00:00Z",
            )
        elif input_id == "ai_posture":
            inputs[input_id] = Measured(
                value="REGULATED",
                n=1,
                window_start=None,
                window_end=None,
                method="primary",
                source=f"raw/governance/{owner}__{name}.json",
                source_hash="a" * 64,
                fetched_at="2026-10-12T00:00:00Z",
            )
        else:
            inputs[input_id] = Measured(
                value=0.75,
                n=35,
                window_start="2026-04-15T00:00:00Z",
                window_end="2026-10-12T00:00:00Z",
                method="primary",
                source=f"raw/github_meta/{owner}__{name}.json",
                source_hash="a" * 64,
                fetched_at="2026-10-12T00:00:00Z",
            )
    return FeatureBundle(
        schema_version=constants.SCHEMA_VERSION,
        repo=RepoIdentity(
            owner=owner,
            name=name,
            ecosystem="pypi",
            archived=False,
            mirror_url=None,
            fork=False,
            license_spdx="MIT",
            default_branch="main",
        ),
        inputs=inputs,
        pr_window=PRWindow(
            start="2026-04-15T00:00:00Z",
            end="2026-10-12T00:00:00Z",
            truncated=False,
            events=(
                PRWindowEvent(
                    pr_number=1,
                    created_at="2026-06-01T00:00:00Z",
                    closed_at="2026-06-03T00:00:00Z",
                    merged_at="2026-06-03T00:00:00Z",
                    author_login="ext-user",
                    author_association="NONE",
                    is_external=True,
                    first_human_review_at="2026-06-02T00:00:00Z",
                ),
            ),
        ),
        cohorts=Cohorts(decision_pr_numbers=(1,), newcomer_authors=()),
        provenance_hash="a" * 64,
    )


def _eligible_evidence() -> GateEvidence:
    return GateEvidence(
        manifest_found=True,
        is_curated_anchor=False,
        license_file_present=True,
        fork_parent_in_universe=False,
        vitality_commits_180d=25,
        off_platform_marker=False,
    )


def test_classify_prune_reason_from_fingerprint(tmp_path: Path) -> None:
    archived_fp = RepoFingerprint(
        owner="org",
        name="archived-repo",
        observed_at=NOW.isoformat(),
        pushed_at="2026-10-01T00:00:00Z",
        is_archived=True,
    )
    assert classify_prune_reason(archived_fp, owner="org", name="archived-repo", now=NOW) == "archived"

    disabled_fp = RepoFingerprint(
        owner="org",
        name="disabled-repo",
        observed_at=NOW.isoformat(),
        pushed_at="2026-10-01T00:00:00Z",
        is_disabled=True,
    )
    assert classify_prune_reason(disabled_fp, owner="org", name="disabled-repo", now=NOW) == "disabled"

    stale_fp = RepoFingerprint(
        owner="org",
        name="stale-repo",
        observed_at=NOW.isoformat(),
        pushed_at="2025-01-01T00:00:00Z",
    )
    assert classify_prune_reason(stale_fp, owner="org", name="stale-repo", now=NOW) == "stale"

    healthy_fp = RepoFingerprint(
        owner="org",
        name="healthy-repo",
        observed_at=NOW.isoformat(),
        pushed_at="2026-09-15T00:00:00Z",
    )
    assert classify_prune_reason(healthy_fp, owner="org", name="healthy-repo", now=NOW) is None


def test_classify_prune_reason_from_raw_envelopes(tmp_path: Path) -> None:
    # 1. non_software (awesome-list / docs only, no manifest at depth <= 2)
    write_envelope(
        tmp_path / "repo_object" / "org__awesome-list.json",
        source="test",
        data={
            "archived": False,
            "mirror_url": None,
            "fork": False,
            "license_spdx_id": "MIT",
            "pull_requests_total": 50,
            "tree_paths_le2": ["README.md", "CONTRIBUTING.md", "docs/index.md"],
            "tree_truncated": False,
        },
        fetched_at=NOW.isoformat(),
    )
    assert (
        classify_prune_reason(
            None, raw_dir=tmp_path, owner="org", name="awesome-list", now=NOW
        )
        == "non_software"
    )

    # 2. unlicensed (NOASSERTION and no LICENSE* file)
    write_envelope(
        tmp_path / "repo_object" / "org__unlicensed-repo.json",
        source="test",
        data={
            "archived": False,
            "mirror_url": None,
            "fork": False,
            "license_spdx_id": "NOASSERTION",
            "pull_requests_total": 10,
            "tree_paths_le2": ["pyproject.toml", "README.md"],
            "tree_truncated": False,
        },
        fetched_at=NOW.isoformat(),
    )
    assert (
        classify_prune_reason(
            None, raw_dir=tmp_path, owner="org", name="unlicensed-repo", now=NOW
        )
        == "unlicensed"
    )

    # 3. no_external_workflow (0 PRs ever and not off-platform)
    write_envelope(
        tmp_path / "repo_object" / "org__code-dump.json",
        source="test",
        data={
            "archived": False,
            "mirror_url": None,
            "fork": False,
            "license_spdx_id": "MIT",
            "pull_requests_total": 0,
            "tree_paths_le2": ["Cargo.toml", "LICENSE", "README.md"],
            "tree_truncated": False,
            "offplatform_paths": [],
            "maintainers_has_l_lines": False,
            "readme_off_platform_hits": [],
        },
        fetched_at=NOW.isoformat(),
    )
    assert (
        classify_prune_reason(
            None, raw_dir=tmp_path, owner="org", name="code-dump", now=NOW
        )
        == "no_external_workflow"
    )

    # 4. Off-platform repo (e.g. Gerrit / mailing list) with 0 PRs is NOT pruned as no_external_workflow
    write_envelope(
        tmp_path / "repo_object" / "org__mailing-list-kernel.json",
        source="test",
        data={
            "archived": False,
            "mirror_url": None,
            "fork": False,
            "license_spdx_id": "GPL-2.0-only",
            "pull_requests_total": 0,
            "tree_paths_le2": ["Makefile", "COPYING", "Documentation/process/submitting-patches.rst"],
            "tree_truncated": False,
            "offplatform_paths": ["Documentation/process/submitting-patches.rst"],
            "maintainers_has_l_lines": True,
            "readme_off_platform_hits": ["mailing_list"],
        },
        fetched_at=NOW.isoformat(),
    )
    assert (
        classify_prune_reason(
            None, raw_dir=tmp_path, owner="org", name="mailing-list-kernel", now=NOW
        )
        is None
    )

    # 5. Non-OSI SPDX expression (e.g. BUSL-1.1 / SSPL-1.0) is pruned as unlicensed
    write_envelope(
        tmp_path / "repo_object" / "org__busl-repo.json",
        source="test",
        data={
            "archived": False,
            "mirror_url": None,
            "fork": False,
            "license_spdx_id": "BUSL-1.1",
            "pull_requests_total": 20,
            "tree_paths_le2": ["pyproject.toml", "LICENSE", "README.md"],
            "tree_truncated": False,
        },
        fetched_at=NOW.isoformat(),
    )
    assert (
        classify_prune_reason(
            None, raw_dir=tmp_path, owner="org", name="busl-repo", now=NOW
        )
        == "unlicensed"
    )

    # 6. Mirror repository is pruned as mirror
    write_envelope(
        tmp_path / "repo_object" / "org__mirror-repo.json",
        source="test",
        data={
            "archived": False,
            "mirror_url": "https://gitlab.example.org/org/mirror-repo.git",
            "fork": False,
            "license_spdx_id": "MIT",
            "pull_requests_total": 20,
            "tree_paths_le2": ["pyproject.toml", "LICENSE", "README.md"],
            "tree_truncated": False,
        },
        fetched_at=NOW.isoformat(),
    )
    assert (
        classify_prune_reason(
            None, raw_dir=tmp_path, owner="org", name="mirror-repo", now=NOW
        )
        == "mirror"
    )

    # 7. Bot-only commits_180d is pruned as stale, unless 300-commit pagination cap is hit inside 180d
    write_envelope(
        tmp_path / "commits_180d" / "org__bot-only.json",
        source="test",
        data=[
            {
                "author_login": "dependabot[bot]",
                "author_type": "Bot",
                "date": "2026-10-01T00:00:00Z",
            }
        ],
        fetched_at=NOW.isoformat(),
    )
    assert (
        classify_prune_reason(
            None, raw_dir=tmp_path, owner="org", name="bot-only", now=NOW
        )
        == "stale"
    )

    write_envelope(
        tmp_path / "commits_180d" / "org__bot-burst-300.json",
        source="test",
        data=[
            {
                "author_login": "renovate[bot]",
                "author_type": "Bot",
                "date": "2026-10-01T00:00:00Z",
            }
            for _ in range(300)
        ],
        fetched_at=NOW.isoformat(),
    )
    assert (
        classify_prune_reason(
            None, raw_dir=tmp_path, owner="org", name="bot-burst-300", now=NOW
        )
        is None
    )

    # 8. Truncated pr_window with 0 external PRs does NOT falsely prune
    write_envelope(
        tmp_path / "pr_window" / "org__truncated-pr-window.json",
        source="test",
        data={
            "truncated": True,
            "events": [
                {
                    "pr_number": 1,
                    "author_association": "MEMBER",
                    "is_external": False,
                    "merged_at": "2026-09-01T00:00:00Z",
                }
            ],
        },
        fetched_at=NOW.isoformat(),
    )
    assert (
        classify_prune_reason(
            None, raw_dir=tmp_path, owner="org", name="truncated-pr-window", now=NOW
        )
        is None
    )

    # 9. Complete pr_events fallback (<30 PRs, all internal) prunes as no_external_workflow
    write_envelope(
        tmp_path / "repo_object" / "org__internal-only-prs.json",
        source="test",
        data={
            "archived": False,
            "mirror_url": None,
            "fork": False,
            "license_spdx_id": "MIT",
            "pull_requests_total": 1,
            "tree_paths_le2": ["pyproject.toml", "LICENSE", "README.md"],
            "tree_truncated": False,
            "offplatform_paths": [],
            "maintainers_has_l_lines": False,
            "readme_off_platform_hits": [],
        },
        fetched_at=NOW.isoformat(),
    )
    write_envelope(
        tmp_path / "pr_events" / "org__internal-only-prs.json",
        source="test",
        data={
            "pull_requests": [
                {
                    "number": 1,
                    "author_association": "OWNER",
                    "merged_at": "2026-09-01T00:00:00Z",
                }
            ]
        },
        fetched_at=NOW.isoformat(),
    )
    assert (
        classify_prune_reason(
            None, raw_dir=tmp_path, owner="org", name="internal-only-prs", now=NOW
        )
        == "no_external_workflow"
    )


def test_run_fingerprint_pass_prune_ineligible_skips_collectors(tmp_path: Path) -> None:
    transport = _FakeTransport(
        {
            "r0": {
                "pushedAt": "2026-10-05T00:00:00Z",
                "updatedAt": "2026-10-05T00:00:00Z",
                "isArchived": True,
                "isDisabled": False,
                "stargazerCount": 5000,
                "forkCount": 200,
                "pullRequests": {"nodes": []},
                "issues": {"nodes": []},
                "releases": {"nodes": []},
                "defaultBranchRef": {"target": {"oid": "abc123"}},
            },
            "r1": {
                "pushedAt": "2026-10-05T00:00:00Z",
                "updatedAt": "2026-10-05T00:00:00Z",
                "isArchived": False,
                "isDisabled": False,
                "stargazerCount": 5000,
                "forkCount": 200,
                "pullRequests": {"nodes": [{"updatedAt": "2026-10-05T00:00:00Z"}]},
                "issues": {"nodes": []},
                "releases": {"nodes": []},
                "defaultBranchRef": {"target": {"oid": "def456"}},
            },
        }
    )
    res = run_fingerprint_pass(
        [("org", "archived"), ("org", "active")],
        state_file=tmp_path / "fingerprints.jsonl",
        raw_dir=tmp_path,
        transport=transport,
        now=NOW,
        prune_ineligible=True,
    )
    assert res.pruned_by_repo == {"org__archived": "archived"}
    assert res.dirty_by_repo["org__archived"] == frozenset()
    assert res.invalidated_by_repo["org__archived"] == frozenset()
    assert len(res.dirty_by_repo["org__active"]) > 0


def test_build_universe_search_query_and_pruning(tmp_path: Path) -> None:
    q = build_universe.build_search_query(
        "language:Python", exclude_ineligible=True, min_pushed_date="2026-04-15"
    )
    assert q == "language:Python archived:false fork:false mirror:false pushed:>=2026-04-15"
    assert build_universe.build_search_query("language:Python", exclude_ineligible=False) == "language:Python"

    write_envelope(
        tmp_path / "repo_object" / "org__awesome-book.json",
        source="test",
        data={
            "archived": False,
            "mirror_url": None,
            "fork": False,
            "license_spdx_id": "MIT",
            "pull_requests_total": 5,
            "tree_paths_le2": ["README.md"],
            "tree_truncated": False,
        },
        fetched_at=NOW.isoformat(),
    )
    rows = [
        {"owner": "org", "name": "awesome-book", "ecosystem": "pypi"},
        {"owner": "org", "name": "active-pkg", "ecosystem": "pypi"},
    ]
    kept, pruned = build_universe.prune_ineligible_candidates(
        rows, raw_dir=tmp_path, now=NOW
    )
    assert [r["name"] for r in kept] == ["active-pkg"]
    assert pruned == {"org__awesome-book": "non_software"}
    assert all(r in PRUNED_REASONS for r in pruned.values())


def test_gather_should_skip_ineligible(tmp_path: Path) -> None:
    write_envelope(
        tmp_path / "github_meta" / "org__old-repo.json",
        source="test",
        data={
            "archived": False,
            "pushed_at": "2024-01-01T00:00:00Z",
            "license_spdx": "MIT",
        },
        fetched_at=NOW.isoformat(),
    )
    assert (
        gather.should_skip_ineligible(
            "org", "old-repo", raw_dir=tmp_path, now=NOW
        )
        == "stale"
    )


def test_black_hole_ranking_and_suspicion_flag() -> None:
    bh_bundle = _make_bundle(owner="acme", name="blackhole-lib", ghost_rate=0.80, ghost_n=12)
    healthy_bundle = _make_bundle(owner="acme", name="healthy-lib", ghost_rate=0.10, ghost_n=12)
    evidence = _eligible_evidence()

    assert is_black_hole_bundle(bh_bundle) is True
    assert is_black_hole_bundle(healthy_bundle) is False

    # Default mode (rank_black_holes=False): D-GATE-5 disqualifies black_hole
    gate_default = evaluate_gates(bh_bundle, evidence, NOW, rank_black_holes=False)
    assert gate_default.eligible is False
    assert gate_default.reason == REASON_BLACK_HOLE

    # Ranked mode (rank_black_holes=True): D-GATE-5 does not disqualify black_hole
    gate_ranked = evaluate_gates(bh_bundle, evidence, NOW, rank_black_holes=True)
    assert gate_ranked.eligible is True
    assert gate_ranked.reason is None

    # Engine score with rank_black_holes=True scores and flags the black hole
    rec = score(
        bh_bundle,
        _DummyPopulations(),
        Snapshot(
            snapshot_id="2026-10-12-test",
            as_of=NOW,
            gate_evidence=evidence,
            rank_black_holes=True,
        ),
    )
    assert rec.rated is True
    assert rec.composite is not None
    assert rec.params["gr14_ghost_rate"].score == 0.0
    assert rec.suspicion == [REASON_BLACK_HOLE]

    # Full snapshot pipeline preserves black_hole flag alongside any detector flags
    snap = score_snapshot(
        [bh_bundle, healthy_bundle],
        _DummyPopulations(),
        snapshot_id="2026-10-12-test",
        as_of=NOW,
        gate_evidence={
            "acme/blackhole-lib": evidence,
            "acme/healthy-lib": evidence,
        },
        raw_hashes={
            "acme/blackhole-lib": "a" * 64,
            "acme/healthy-lib": "b" * 64,
        },
        detectors=lambda _records, _prev: {"acme/blackhole-lib": ["closed_unread"]},
        rank_black_holes=True,
    )
    assert snap.counts.rated == 2
    assert snap.counts.gated == 0
    by_name = {r.repo.name: r for r in snap.records}
    assert by_name["healthy-lib"].rank == 1
    assert by_name["blackhole-lib"].rank == 2
    assert by_name["blackhole-lib"].suspicion == ["black_hole", "closed_unread"]
    assert snap.counts.suspicion_counts["black_hole"] == 1
