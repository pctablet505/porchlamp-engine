"""Shared synthetic-snapshot builder for ``tests/serve`` (not itself a test
module). Reuses ``tests/store/helpers.py``'s record/bundle builders and
``tests/store/test_queries.py``'s own rank-assignment pattern (build
``ScoreRecord``s, wrap in ``TieBreakInput``, ``assign_ranks``, THEN write --
a real pipeline's order) rather than inventing a second one, so a rated row
this module writes always carries a real, non-null ``rank`` the way
production does (`porchlamp.store.writer`'s own docstring: rank/tie_group are
"already baked into" a record by the time it reaches the writer).
"""
from __future__ import annotations

import random
import sqlite3
from dataclasses import replace as dc_replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from porchlamp.core import constants
from porchlamp.core.gates import REASON_FORK, ROUTED_OFF_PLATFORM, GateEvidence
from porchlamp.core.models import ParamScore, PillarScore, ScoreRecord, Unmeasured
from porchlamp.core.persona import PERSONA_PRESETS, persona_composite
from porchlamp.core.pipeline import score_bundle_stage_8_persona, score_bundle_stages_0_7
from porchlamp.core.tiebreak import TieBreakInput, assign_ranks
from porchlamp.snapshot.populations import Population, PopulationSet
from porchlamp.store import schema
from porchlamp.store.migrations import apply_migrations
from porchlamp.store.writer import write_records, write_snapshot, finalize_snapshot_hash

from store.helpers import (
    make_bundle,
    make_gated_record,
    make_measured,
    make_rated_record,
    make_repo_write,
    make_unrated_record,
)

PILLARS = ("responsiveness", "criticality", "governance", "rigor", "accessibility")

SNAPSHOT_ID = "snap-api-v2"
AS_OF = "2026-09-13"
BENCHMARK_VERSION = "2.0.0-dev"
CONSTANTS_HASH = "c" * 64
CODE_COMMIT = "deadbeef" * 5
MANIFEST_ROOT = "r" * 8


def fresh_db(path: Path, *, snapshot_id: str = SNAPSHOT_ID) -> sqlite3.Connection:
    conn = schema.connect(path)
    apply_migrations(conn, applied_at="2026-09-13T00:00:00Z")
    write_snapshot(
        conn,
        id=snapshot_id,
        as_of=AS_OF,
        benchmark_version=BENCHMARK_VERSION,
        constants_hash=CONSTANTS_HASH,
        code_commit=CODE_COMMIT,
        manifest_root=MANIFEST_ROOT,
        created_at="2026-09-13T00:00:00Z",
    )
    return conn


def _with_persona_composites(record):
    composites = {persona: persona_composite(record, persona) for persona in PERSONA_PRESETS}
    return dc_replace(record, persona_composites=composites)


def finalize(conn: sqlite3.Connection, db_path: Path, *, snapshot_id: str = SNAPSHOT_ID) -> str:
    """Stamp ``canonical_dump_hash``/``promoted_at`` so ``GET /snapshot``
    (which refuses an un-finalised snapshot, `porchlamp/serve/app.py`'s
    ``_require_snapshot_summary``) has one to serve."""
    return finalize_snapshot_hash(conn, db_path, snapshot_id, promoted_at="2026-09-13T01:00:00Z")


#: The owner of the repositories :func:`routed_pair_records` builds.
ROUTED_PAIR_OWNER = "lens-org"

#: Measured inputs of a routed repository and its twin. Responsiveness is
#: left unmeasured (a routed repository's review is invisible to GitHub);
#: criticality, governance and rigor are fully measured and accessibility
#: only 0.35. Balanced coverage is then 0.6525 (>= 0.60), and per persona:
#: first_timer 0.455 (below the floor, so gated by ``insufficient_evidence:coverage``),
#: career_capital 0.67, senior_systems 0.8175, ai_assisted 0.735 (all clear it).
_PAIR_MEASURED = {
    "dependents_deduped": 999999.0, "openssf_criticality": 1.0, "downloads_monthly": 999999999.0,
    "legal_ip": "DCO", "elephant_factor": 5.0, "promotion_ladder": "VERIFIED",
    "pipeline_defenses": 100.0, "ai_posture": "AD_HOC", "substantive_review_rate": 0.8,
    "newcomer_issue_supply": 10.0,
}


def _pair_populations() -> PopulationSet:
    """Every ``python`` ECDF population sits below the pair's values, so all
    three ECDF curves score 100.0."""
    return PopulationSet(
        snapshot_id=SNAPSHOT_ID, benchmark_version=constants.BENCHMARK_VERSION,
        constants_hash=constants.CONSTANTS_HASH,
        populations=(
            Population("python", "ttm_p90_hours", 4, False, {"a": 5.0, "b": 10.0, "c": 20.0, "d": 30.0}),
            Population("python", "dependents_deduped", 4, False, {"a": 0.0, "b": 1.0, "c": 2.0, "d": 3.0}),
            Population("python", "downloads_monthly", 3, False, {"a": 0.0, "b": 1.0, "c": 2.0}),
        ),
    )


def routed_pair_records(
    snapshot_id: str,
    *,
    routed_name: str = "routed-repo",
    twin_name: str = "twin-repo",
) -> Tuple[ScoreRecord, ScoreRecord]:
    """A repository D-GATE-6 routes off-platform and its unrouted twin, each
    scored through the real code path: ``GateEvidence`` -> ``evaluate_gates``
    -> ``engine.score`` (:func:`score_bundle_stages_0_7`) -> stage 8, never a
    hand-written row. Both carry the same inputs, so their pillars are
    identical; only ``GateEvidence.review_handoff`` differs (the routed one
    hands review off). Returns ``(routed, twin)``, both eligible; the twin is
    rated, and ``rank`` is still unset on both."""
    inputs = {key: Unmeasured(reason="not_applicable") for key in constants.PARAM_INPUT_IDS}
    inputs.update({key: make_measured(value, n=30) for key, value in _PAIR_MEASURED.items()})
    records = []
    for name, review_handoff, raw_hash in ((routed_name, True, "f" * 64), (twin_name, None, "9" * 64)):
        bundle = make_bundle(ROUTED_PAIR_OWNER, name, inputs=inputs)
        bundle.validate()
        evidence = GateEvidence(manifest_found=True, vitality_commits_180d=5, review_handoff=review_handoff)
        record = score_bundle_stages_0_7(
            bundle, _pair_populations(), snapshot_id=snapshot_id,
            as_of=datetime(2026, 9, 13, tzinfo=timezone.utc), gate_evidence=evidence, raw_hash=raw_hash,
        )
        records.append(score_bundle_stage_8_persona(record, bundle))
    routed, twin = records
    # What the helper promises, checked where it is built: a silent change to
    # the engine must fail here, not make the tests that use it vacuous.
    assert routed.gate.eligible and routed.gate.routed == ROUTED_OFF_PLATFORM and routed.rated is False
    assert twin.gate.eligible and twin.gate.routed is None and twin.rated is True
    assert {n: p.score for n, p in routed.pillars.items()} == {n: p.score for n, p in twin.pillars.items()}
    return routed, twin



class SnapshotFixture:
    """Everything a test needs to assert against, alongside the written
    rows themselves: which owner/name pairs landed in which bucket, and
    with which reason."""

    def __init__(self) -> None:
        self.rated_names: List[str] = []
        self.tie_group_names: Dict[str, List[str]] = {}
        self.singleton_names: List[str] = []
        self.stars_audited: Dict[str, int] = {}
        self.suspicious_names: List[str] = []
        self.persona_gated: Dict[str, str] = {}  # name -> (persona, gate_code) folded to one entry per name
        self.persona_gate_persona: str = "first_timer"
        self.gated: Dict[str, str] = {}  # name -> gate_reason (raw store value)
        self.unrated: Dict[str, str] = {}  # name -> reason category: "plain" | "routed"
        self.evidence_inputs: Dict[str, dict] = {}  # name -> extra bundle.inputs (Measured values)
        self.routed_name: Optional[str] = None  # set by ``with_routed_pair``: owner is ``ROUTED_PAIR_OWNER``
        self.twin_name: Optional[str] = None
        self.full_coverage_name: Optional[str] = None  # set by ``with_full_coverage_repo``: owner is ``acme``


def build_snapshot(
    conn: sqlite3.Connection,
    db_path: Path,
    *,
    snapshot_id: str = SNAPSHOT_ID,
    n_rated: int = 285,
    seed: int = 20260913,
    with_routed_pair: bool = False,
    with_full_coverage_repo: bool = False,
) -> SnapshotFixture:
    """A snapshot with >= 300 total rows: ``n_rated`` rated rows (a mix of
    unique composites, one explicit 3-way tie, one explicit 2-way tie with
    a NULL at the friction tie-break level, and a persona-gated row),
    ~25 gated rows (one per ``porchlamp.core.gates`` reason INCLUDING ``fork``
    and the ``"unrated: no_external_workflow"`` stage-6 gate the API
    translates), ~15 unrated rows (plain insufficient-evidence and
    ``routed="off_platform"``). Returns a :class:`SnapshotFixture` naming
    exactly what landed where, for assertions.

    ``with_routed_pair=True`` adds, through the real code path
    (:func:`routed_pair_records`), a repository routed off-platform whose
    pillars clear the 0.60 coverage floor under balanced weights, and its
    unrouted twin (a rated row ranked with the rest); their names are ``fixture.routed_name`` / ``fixture.twin_name``
    under ``ROUTED_PAIR_OWNER``. Off by default: the UI fixtures and the other
    tests pin this builder's default rows.

    ``with_full_coverage_repo=True`` adds one rated row whose five pillars are all measured in full
    (pillar coverage exactly 1.0), named ``fixture.full_coverage_name`` under ``acme``. The default rows
    stop at 0.9, so no custom weight vector could ever sum their coverage past 1.0; this one can.
    """
    rng = random.Random(seed)
    fixture = SnapshotFixture()
    items = []
    plain_records = []

    # -- Explicit 3-way tie (identical pillars -> identical composite under
    # every persona weight vector, so the tie is persona-invariant). -- #
    tie_pillars = {"responsiveness": 72.4, "criticality": 65.0, "governance": 80.0, "rigor": 55.0, "accessibility": 90.0}
    for i in range(3):
        rec = make_rated_record(
            snapshot_id, owner="acme", name=f"tie3-{i}", raw_hash=f"{i:064x}",
            composite=73.42, coverage=0.95, pillar_scores=dict(tie_pillars),
            friction=30.0, leverage=60.0,
        )
        plain_records.append((rec, dict(ttfhr_p75_hours=12.0, last_non_bot_commit_at="2026-08-01T00:00:00Z")))
    fixture.tie_group_names["tie3"] = [f"tie3-{i}" for i in range(3)]

    # -- Explicit 2-way tie with a NULL at the friction tie level. -- #
    tie2_pillars = {"responsiveness": 40.5, "criticality": 40.5, "governance": 40.5, "rigor": 40.5, "accessibility": 40.5}
    rec_a = make_rated_record(
        snapshot_id, owner="acme", name="tie2-measured", raw_hash="a1" * 32,
        composite=40.5, coverage=0.95, pillar_scores=dict(tie2_pillars), friction=15.0, leverage=60.0,
    )
    rec_b = make_rated_record(
        snapshot_id, owner="acme", name="tie2-null-friction", raw_hash="a2" * 32,
        composite=40.5, coverage=0.95, pillar_scores=dict(tie2_pillars), friction=None, leverage=60.0,
    )
    plain_records.append((rec_a, dict(ttfhr_p75_hours=20.0, last_non_bot_commit_at="2026-07-01T00:00:00Z")))
    plain_records.append((rec_b, dict(ttfhr_p75_hours=20.0, last_non_bot_commit_at="2026-07-01T00:00:00Z")))
    fixture.tie_group_names["tie2"] = ["tie2-measured", "tie2-null-friction"]

    # -- A row with suspicion flags, audited stars (writer bridge) and real
    # evidence (one measured, one unmeasured param -- `GET .../evidence`). -- #
    flagged_params = {
        "ttfhr_hours": ParamScore(
            value=11.2, score=92.0, measured=True, reason=None, n=48,
            window_start="2026-03-01", window_end="2026-08-28", method="primary",
            source="data/raw/github_graphql/acme__flagged-repo.json", fetched_at="2026-08-28T00:00:00Z",
        ),
        "dependents_deduped": ParamScore(
            value=None, score=None, measured=False, reason="third_party_gap", n=0,
            window_start=None, window_end=None, method=None, source=None, fetched_at=None,
        ),
    }
    flagged = make_rated_record(
        snapshot_id, owner="acme", name="flagged-repo", raw_hash="b1" * 32,
        composite=91.0, coverage=0.97, suspicion=["newcomer_burst", "param_jump"],
        params=flagged_params,
    )
    plain_records.append((flagged, dict(ttfhr_p75_hours=8.0, last_non_bot_commit_at="2026-08-10T00:00:00Z")))
    fixture.suspicious_names.append("flagged-repo")
    fixture.stars_audited["flagged-repo"] = 4242
    fixture.evidence_inputs["flagged-repo"] = {
        "ttfhr_hours": make_measured(
            11.2, n=48, source="data/raw/github_graphql/acme__flagged-repo.json",
            fetched_at="2026-08-28T00:00:00Z", window_start="2026-03-01", window_end="2026-08-28",
        ),
    }

    # -- A row gated OUT of one specific persona (still balanced-rated). -- #
    persona_gated = make_rated_record(
        snapshot_id, owner="acme", name="persona-gated-repo", raw_hash="b2" * 32,
        composite=88.0, coverage=0.96,
        persona_gates={fixture.persona_gate_persona: "high_barrier_to_entry"},
    )
    plain_records.append((persona_gated, dict(ttfhr_p75_hours=9.0, last_non_bot_commit_at="2026-08-11T00:00:00Z")))
    fixture.persona_gated["persona-gated-repo"] = "high_barrier_to_entry"

    # -- A repository routed off-platform and its unrouted twin (opt-in). -- #
    routed_pair_item = None
    if with_routed_pair:
        routed_record, twin_record = routed_pair_records(snapshot_id)
        plain_records.append((twin_record, dict(ttfhr_p75_hours=14.0, last_non_bot_commit_at="2026-08-12T00:00:00Z")))
        routed_pair_item = make_repo_write(routed_record)
        fixture.routed_name = routed_record.repo.name
        fixture.twin_name = twin_record.repo.name

    if with_full_coverage_repo:
        full = make_rated_record(
            snapshot_id, owner="acme", name="full-coverage", raw_hash="e" * 64,
            composite=77.77, coverage=1.0, pillar_scores={n: 80.0 for n in PILLARS},
        )
        full = dc_replace(full, pillars={n: PillarScore(score=80.0, coverage=1.0) for n in PILLARS})
        plain_records.append((full, dict(ttfhr_p75_hours=9.0, last_non_bot_commit_at="2026-08-10T00:00:00Z")))
        fixture.full_coverage_name = "full-coverage"

    # -- The remaining unique-composite rated rows. -- #
    used_composites = {73.42, 40.5, 91.0, 88.0}
    n_remaining = max(0, n_rated - len(plain_records))
    for i in range(n_remaining):
        # Bounded retries, not an unconditional `while True`: at 2 decimal
        # places over [40, 99] there are only ~5,900 distinct values, so a
        # LARGE `n_rated` (the performance test's 10,000-row snapshot)
        # would retry forever chasing a guarantee this module's own
        # tie/singleton tests do not need at that scale -- 50 attempts
        # makes a collision astronomically unlikely for the functional
        # tests' ~285 rows (birthday bound comfortably cleared) while
        # never hanging for the performance builder, which tolerates an
        # occasional incidental tie (it asserts timings, not tie shape).
        composite = round(rng.uniform(40, 99), 2)
        for _ in range(50):
            if composite not in used_composites:
                break
            composite = round(rng.uniform(40, 99), 2)
        used_composites.add(composite)
        coverage = round(rng.uniform(0.6, 1.0), 3)
        scores = {p: round(rng.uniform(20, 99), 1) for p in PILLARS}
        rec = make_rated_record(
            snapshot_id, owner="bulk", name=f"repo-{i:04d}", raw_hash=f"{i:064x}",
            composite=composite, coverage=coverage, pillar_scores=scores,
            friction=round(rng.uniform(0, 100), 1), leverage=round(rng.uniform(0, 100), 1),
        )
        plain_records.append((rec, dict(
            ttfhr_p75_hours=round(rng.uniform(1, 300), 1),
            last_non_bot_commit_at=f"2026-{1 + i % 9:02d}-01T00:00:00Z",
        )))

    records_with_meta = [(_with_persona_composites(rec), meta) for rec, meta in plain_records]
    tie_entries = [
        TieBreakInput(rec, rec.repo.owner, rec.repo.name, **meta) for rec, meta in records_with_meta
    ]
    ranked = assign_ranks(tie_entries)
    ranked_by_name = {r.repo.name: r for r, _rank, _tg in ranked}
    for rec, _meta in records_with_meta:
        final_record = ranked_by_name[rec.repo.name]
        inputs = dict(fixture.evidence_inputs.get(rec.repo.name, {}))
        if rec.repo.name in fixture.stars_audited:
            inputs["stars_audited"] = make_measured(fixture.stars_audited[rec.repo.name])
        items.append(make_repo_write(final_record, inputs=inputs or None))
        fixture.rated_names.append(rec.repo.name)
    fixture.singleton_names = [
        name for name in fixture.rated_names
        if name not in fixture.tie_group_names["tie3"] and name not in fixture.tie_group_names["tie2"]
    ]

    # -- Gated rows: one per universal gate reason. -- #
    gate_reasons = [
        "mirror", "archived", "unlicensed", "stale", "black_hole", "non_software",
        REASON_FORK, "unrated: no_external_workflow",
    ]
    for i, reason in enumerate(gate_reasons):
        name = f"gated-{reason.replace(': ', '-').replace('_', '-')}"
        rec = make_gated_record(snapshot_id, owner="gated-org", name=name, raw_hash=f"c{i}" * 16, gate_reason=reason)
        items.append(make_repo_write(rec))
        fixture.gated[name] = reason

    # -- Unrated rows: plain insufficient evidence, and off-platform routed. -- #
    for i in range(15):
        name = f"unrated-plain-{i}"
        rec = make_unrated_record(snapshot_id, owner="unrated-org", name=name, raw_hash=f"d{i}" * 16)
        items.append(make_repo_write(rec))
        fixture.unrated[name] = "plain"

    for i in range(3):
        name = f"unrated-routed-{i}"
        base = make_unrated_record(snapshot_id, owner="unrated-org", name=name, raw_hash=f"e{i}" * 16)
        routed = dc_replace(base, gate=dc_replace(base.gate, routed="off_platform"))
        items.append(make_repo_write(routed))
        fixture.unrated[name] = "routed"

    if routed_pair_item is not None:
        items.append(routed_pair_item)
        fixture.unrated[fixture.routed_name] = "routed"

    write_records(conn, snapshot_id, items)
    conn.commit()

    return fixture
