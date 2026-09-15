"""Shared fixture builders for ``tests/store`` (not itself a test module).

``FeatureBundle`` has no ``__post_init__`` validation (only the separate
``.validate()`` method, never called here), so a bundle below can carry a
handful of ``inputs`` rather than the full 18 -- the store writer only
needs whatever a given test's ``record.params`` actually names.

Uses ``ScoreRecord.repo: RepoIdentity`` (required first field) and
``persona_composites: dict[str, PersonaComposite]``.
"""
from __future__ import annotations

from cvi.core.models import (
    Cohorts,
    FeatureBundle,
    GateResult,
    Measured,
    ParamScore,
    PersonaComposite,
    PillarScore,
    PRWindow,
    RepoIdentity,
    ScoreRecord,
)
from cvi.core.tiebreak import TieBreakInput
from cvi.store.writer import RepoWrite

PILLARS = ("responsiveness", "criticality", "governance", "rigor", "accessibility")

HEX64 = "a" * 64

_EMPTY_PR_WINDOW = PRWindow(start=None, end=None, truncated=False, events=())


def make_repo_identity(
    owner: str,
    name: str,
    ecosystem: str = "python",
    archived: bool = False,
    mirror_url: str | None = None,
    fork: bool = False,
    license_spdx: str | None = "MIT",
    default_branch: str | None = "main",
) -> RepoIdentity:
    return RepoIdentity(
        owner=owner, name=name, ecosystem=ecosystem, archived=archived,
        mirror_url=mirror_url, fork=fork, license_spdx=license_spdx, default_branch=default_branch,
    )


def make_bundle(
    owner: str,
    name: str,
    ecosystem: str = "python",
    inputs: dict | None = None,
    archived: bool = False,
    mirror_url: str | None = None,
    fork: bool = False,
    license_spdx: str | None = "MIT",
    pr_window: PRWindow | None = None,
    repo: RepoIdentity | None = None,
) -> FeatureBundle:
    return FeatureBundle(
        schema_version=2,
        repo=repo or make_repo_identity(
            owner, name, ecosystem=ecosystem, archived=archived,
            mirror_url=mirror_url, fork=fork, license_spdx=license_spdx,
        ),
        inputs=dict(inputs or {}),
        pr_window=pr_window or _EMPTY_PR_WINDOW,
        cohorts=Cohorts(decision_pr_numbers=(), newcomer_authors=()),
        provenance_hash=HEX64,
    )


def make_measured(
    value: float | int | str,
    n: int = 40,
    source: str = "data/raw/github_rest/acme__demo.json",
    source_hash: str = HEX64,
    fetched_at: str = "2026-09-01T00:00:00Z",
    window_start: str | None = "2026-03-05T00:00:00Z",
    window_end: str | None = "2026-09-01T00:00:00Z",
    method: str = "primary",
) -> Measured:
    return Measured(
        value=value,
        n=n,
        window_start=window_start,
        window_end=window_end,
        method=method,
        source=source,
        source_hash=source_hash,
        fetched_at=fetched_at,
    )


def make_rated_record(
    snapshot_id: str,
    owner: str = "acme",
    name: str = "demo",
    ecosystem: str = "python",
    repo: RepoIdentity | None = None,
    raw_hash: str = HEX64,
    composite: float = 82.5,
    coverage: float = 0.9,
    pillar_scores: dict[str, float] | None = None,
    params: dict[str, ParamScore] | None = None,
    tier: str | None = "B",
    tier_ceiling: str | None = None,
    leverage: float | None = 70.0,
    leverage_coverage: float = 1.0,
    friction: float | None = 20.0,
    friction_coverage: float = 1.0,
    quadrant: str | None = "Sweet Spot",
    persona_gates: dict[str, str] | None = None,
    persona_composites: dict[str, PersonaComposite] | None = None,
    suspicion: list[str] | None = None,
    gate_undecided: tuple[str, ...] = (),
) -> ScoreRecord:
    scores = pillar_scores or {n: 80.0 for n in PILLARS}
    pillars = {n: PillarScore(score=scores.get(n), coverage=0.9) for n in PILLARS}
    return ScoreRecord(
        repo=repo or make_repo_identity(owner, name, ecosystem=ecosystem),
        snapshot_id=snapshot_id,
        benchmark_version="2.0.0-dev",
        constants_hash="c" * 64,
        raw_hash=raw_hash,
        provenance_hash=HEX64,
        gate=GateResult(eligible=True, reason=None, routed=None, undecided=gate_undecided),
        params=dict(params or {}),
        pillars=pillars,
        composite=composite,
        coverage=coverage,
        tier=tier,
        leverage=leverage,
        friction=friction,
        quadrant=quadrant,
        rated=True,
        unrated_reason=None,
        tier_ceiling=tier_ceiling,
        leverage_coverage=leverage_coverage,
        friction_coverage=friction_coverage,
        persona_gates=dict(persona_gates or {}),
        persona_composites=dict(persona_composites or {}),
        tie_key=(),
        tie_group=None,
        rank=None,
        suspicion=list(suspicion or []),
    )


def make_gated_record(
    snapshot_id: str,
    owner: str = "acme",
    name: str = "old",
    ecosystem: str = "python",
    repo: RepoIdentity | None = None,
    raw_hash: str = HEX64,
    gate_reason: str = "archived",
) -> ScoreRecord:
    return ScoreRecord(
        repo=repo or make_repo_identity(owner, name, ecosystem=ecosystem),
        snapshot_id=snapshot_id,
        benchmark_version="2.0.0-dev",
        constants_hash="c" * 64,
        raw_hash=raw_hash,
        provenance_hash=HEX64,
        gate=GateResult(eligible=False, reason=gate_reason, routed=None, undecided=()),
        params={},
        pillars={},
        composite=None,
        coverage=0.0,
        tier=None,
        leverage=None,
        friction=None,
        quadrant=None,
        rated=False,
        unrated_reason=None,
        tier_ceiling=None,
        leverage_coverage=0.0,
        friction_coverage=0.0,
        persona_gates={},
        persona_composites={},
        tie_key=(),
        tie_group=None,
        rank=None,
        suspicion=[],
    )


def make_unrated_record(
    snapshot_id: str,
    owner: str = "acme",
    name: str = "tiny-lib",
    ecosystem: str = "python",
    repo: RepoIdentity | None = None,
    raw_hash: str = HEX64,
    coverage: float = 0.1,
    params: dict[str, ParamScore] | None = None,
    tier_ceiling: str | None = None,
) -> ScoreRecord:
    pillars = {n: PillarScore(score=None, coverage=0.1) for n in PILLARS}
    return ScoreRecord(
        repo=repo or make_repo_identity(owner, name, ecosystem=ecosystem),
        snapshot_id=snapshot_id,
        benchmark_version="2.0.0-dev",
        constants_hash="c" * 64,
        raw_hash=raw_hash,
        provenance_hash=HEX64,
        gate=GateResult(eligible=True, reason=None, routed=None, undecided=()),
        params=dict(params or {}),
        pillars=pillars,
        composite=None,
        coverage=coverage,
        tier=None,
        leverage=None,
        friction=None,
        quadrant=None,
        rated=False,
        unrated_reason="unrated: insufficient_evidence",
        tier_ceiling=tier_ceiling,
        leverage_coverage=0.0,
        friction_coverage=0.0,
        persona_gates={},
        persona_composites={},
        tie_key=(),
        tie_group=None,
        rank=None,
        suspicion=[],
    )


def make_tie(
    record: ScoreRecord,
    owner: str | None = None,
    name: str | None = None,
    ttfhr_p75_hours: float | None = 12.0,
    last_non_bot_commit_at: str | None = "2026-08-01T00:00:00Z",
    stars: int | None = 100,
    forks: int | None = 10,
    downloads: int | None = None,
) -> TieBreakInput:
    """Still needed upstream of the writer: :func:`cvi.core.tiebreak.
    assign_ranks` takes ``TieBreakInput`` entries to compute ``tie_key``/
    ``rank``/``tie_group`` on the record BEFORE it ever reaches
    :class:`~cvi.store.writer.RepoWrite`, which does not carry one.
    Defaults ``owner``/``name`` to the record's own identity."""
    return TieBreakInput(
        record=record,
        owner=owner if owner is not None else record.repo.owner,
        name=name if name is not None else record.repo.name,
        ttfhr_p75_hours=ttfhr_p75_hours,
        last_non_bot_commit_at=last_non_bot_commit_at,
        stars=stars,
        forks=forks,
        downloads=downloads,
    )


def make_repo_write(
    record: ScoreRecord,
    inputs: dict | None = None,
    pr_window: PRWindow | None = None,
) -> RepoWrite:
    """The bundle's ``repo`` is ``record.repo`` itself (not a separately
    constructed, merely-equal ``RepoIdentity``) so the two can never drift:
    ``RepoWrite.__post_init__`` requires them equal (identity comes from
    ``record.repo``)."""
    bundle = FeatureBundle(
        schema_version=2,
        repo=record.repo,
        inputs=dict(inputs or {}),
        pr_window=pr_window or _EMPTY_PR_WINDOW,
        cohorts=Cohorts(decision_pr_numbers=(), newcomer_authors=()),
        provenance_hash=HEX64,
    )
    return RepoWrite(record=record, bundle=bundle)
