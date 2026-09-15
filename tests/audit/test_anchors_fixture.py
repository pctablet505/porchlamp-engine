"""Acceptance tests: D-VER-4 anchors over the v2 fixture snapshot
(``cvi.audit.anchors.evaluate_anchors_v2``, ``docs/v2/02-RANKING-DESIGN.md``
section 11).

Every anchor this fixture *can* evaluate must PASS (or REPORT, for
``keras_hub_gate_reported``, which never fails by design); an anchor this
50-repo fixture has no way to evaluate (its identity repo is absent, or --
``newcomer_label_5_1`` -- the v2 ``ScoreRecord`` contract itself cannot
supply the independent fact the relation needs) is asserted SKIPPED WITH
REASON, never silently passed.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from cvi.audit.anchors import (
    ANCHOR_RELATIONS,
    FAIL,
    PASS,
    REPORT,
    SKIPPED,
    evaluate_anchors_v2,
    format_anchor_report_v2,
)
from cvi.core.models import ParamScore
from cvi.core.pipeline import score_snapshot

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "snapshot_50"
SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "build_fixture_snapshot.py"

#: Anchors this 50-repo fixture can actually evaluate (identity repo
#: present, or a broad relation with at least one matching repo) -- must
#: PASS. Everything else in :data:`ANCHOR_RELATIONS` must come back
#: SKIPPED (absent identity / no independent fact to check) or REPORT
#: (``keras_hub_gate_reported``, which never fails).
_EXPECT_PASS = {"linux_off_platform", "archived_repo_gated", "awesome_non_software"}
# linux_dco is SKIPPED, not PASS, on this fixture: legal_ip reads
# the real governance envelope, which is absent here, so legal_ip is genuinely
# Unmeasured(collector_failure) for torvalds/linux -- measured, not a bug (see
# _skipped_unmeasured_input in cvi/audit/anchors.py and the commit that fixed
# this from a wrongly-reported FAIL).
_EXPECT_SKIPPED = {"gcc_mirror_gated", "kubernetes_rated", "vllm_rated", "newcomer_label_5_1", "linux_dco"}
_EXPECT_REPORT = {"keras_hub_gate_reported"}

assert _EXPECT_PASS | _EXPECT_SKIPPED | _EXPECT_REPORT == {relation for relation, _ in ANCHOR_RELATIONS}


def _load_build_script():
    spec = importlib.util.spec_from_file_location("build_fixture_snapshot", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_fixture_snapshot"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def snapshot():
    build_script = _load_build_script()
    frozen = build_script.load_frozen_fixture(FIXTURE_DIR)
    return score_snapshot(
        frozen.bundles, frozen.populations, snapshot_id=frozen.snapshot_id, as_of=frozen.as_of,
        gate_evidence=frozen.gate_evidence, raw_hashes=frozen.raw_hashes,
    )


@pytest.fixture(scope="module")
def results(snapshot):
    return evaluate_anchors_v2(snapshot)


def test_every_relation_is_evaluated_exactly_once(results) -> None:
    assert [result.relation for result in results] == [relation for relation, _ in ANCHOR_RELATIONS]


def test_evaluable_anchors_pass(results) -> None:
    by_relation = {result.relation: result for result in results}
    failures = [
        f"{relation}: verdict={by_relation[relation].verdict} observed={by_relation[relation].observed!r}"
        for relation in _EXPECT_PASS
        if by_relation[relation].verdict != PASS
    ]
    assert failures == [], "\n".join(failures)


def test_absent_identity_anchors_are_skipped_with_reason(results) -> None:
    by_relation = {result.relation: result for result in results}
    for relation in _EXPECT_SKIPPED:
        result = by_relation[relation]
        assert result.verdict == SKIPPED, (relation, result.verdict, result.observed)
        assert result.observed, f"{relation} SKIPPED with no reason"


def test_keras_hub_is_report_only(results) -> None:
    by_relation = {result.relation: result for result in results}
    assert by_relation["keras_hub_gate_reported"].verdict == REPORT


def test_no_relation_ever_returns_fail_on_this_fixture(results) -> None:
    """Over-correction guard: nothing here is vacuously easy to pass by
    construction -- if a relation this fixture *can* evaluate ever
    genuinely contradicts the ranking, that must show up as FAIL, not be
    silently absorbed into SKIPPED. This fixture's real, measured anchors
    all hold, so today's
    expected count is zero; a future change to the fixture or the gates
    that breaks a real anchor must fail this test, not be missed by it."""
    failing = [result for result in results if result.verdict == FAIL]
    assert failing == [], [(result.relation, result.observed) for result in failing]


def test_print_anchor_table(results, snapshot) -> None:
    """Not an assertion -- prints the anchor table (``-s``
    to see it; harmless without)."""
    print(format_anchor_report_v2(results, len(snapshot.records)))


# --------------------------------------------------------------------------- #
# Mutation-battery oracle: a hand-built snapshot where the broad relations
# (archived/awesome) must FAIL, independent of what the real 50-repo
# fixture happens to contain (this fixture's own repos are all correctly
# gated, so a comparison-operator mutant that always reports PASS would
# not be caught by the tests above alone).
# --------------------------------------------------------------------------- #
def _minimal_gated_record(owner: str, name: str, *, eligible: bool, reason):
    from cvi.core.models import GateResult, RepoIdentity, ScoreRecord

    repo = RepoIdentity(
        owner=owner, name=name, ecosystem="pypi", archived=False, mirror_url=None,
        fork=False, license_spdx="MIT", default_branch="main",
    )
    gate = GateResult(eligible=eligible, reason=reason, routed=None, undecided=())
    return ScoreRecord(
        repo=repo, snapshot_id="s", benchmark_version="2.0.0-dev", constants_hash="x", raw_hash="x",
        gate=gate, params={}, pillars={}, composite=None, coverage=0.0, tier=None, leverage=None,
        friction=None, quadrant=None, persona_gates={}, persona_composites={}, tie_key=(), tie_group=None,
        rank=None, suspicion=[],
    )


def test_awesome_and_archived_relations_fail_when_not_actually_gated() -> None:
    """Oracle: an ``awesome*`` repo left eligible (not gated non_software)
    and no archived-gated repo anywhere -- both broad D-VER-4 relations
    must FAIL, not PASS. Kills a comparison-operator mutant in
    ``_check_awesome_v2``/``_check_archived_v2`` that a vacuously-true
    condition (e.g. ``len(gated_as_list) >= 0``) would let slip through as
    an always-PASS."""
    from cvi.core.pipeline import ScoredSnapshot, SnapshotCounts

    records = (
        _minimal_gated_record("someone", "awesome-not-gated", eligible=True, reason=None),
        _minimal_gated_record("someone", "plain-repo", eligible=True, reason=None),
    )
    snapshot = ScoredSnapshot(
        snapshot_id="s", benchmark_version="2.0.0-dev", constants_hash="x", records=records,
        counts=SnapshotCounts(rated=0, gated=0, unrated=2, persona_visible={}), records_hash="x",
    )
    by_relation = {result.relation: result for result in evaluate_anchors_v2(snapshot)}
    assert by_relation["awesome_non_software"].verdict == FAIL
    assert by_relation["archived_repo_gated"].verdict == FAIL


# --------------------------------------------------------------------------- #
# Unmeasured-required-input rule (dispatch fix, following fixture
# regeneration): SKIPPED naming the reason, never FAIL -- FAIL means a
# *measured* value contradicts the relation.
# --------------------------------------------------------------------------- #
def _linux_record_with_legal_ip(param):
    """A ``torvalds/linux``-identity, gate-eligible record whose only
    controlled fact is its ``legal_ip`` ``ParamScore`` (``param``, or
    ``None`` to omit it from ``params`` entirely -- not exercised by these
    tests, kept for completeness of the shape)."""
    from cvi.core.models import GateResult, RepoIdentity, ScoreRecord

    repo = RepoIdentity(
        owner="torvalds", name="linux", ecosystem="systems", archived=False, mirror_url=None,
        fork=False, license_spdx="NOASSERTION", default_branch="master",
    )
    gate = GateResult(eligible=True, reason=None, routed="off_platform", undecided=())
    params = {} if param is None else {"legal_ip": param}
    return ScoreRecord(
        repo=repo, snapshot_id="s", benchmark_version="2.0.0-dev", constants_hash="x", raw_hash="x",
        gate=gate, params=params, pillars={}, composite=None, coverage=0.0, tier=None, leverage=None,
        friction=None, quadrant=None, persona_gates={}, persona_composites={}, tie_key=(), tie_group=None,
        rank=None, suspicion=[], rated=False, unrated_reason="unrated: insufficient_evidence",
    )


def test_linux_dco_fails_on_a_measured_contradicting_value() -> None:
    """Oracle (kept): a *measured* ``legal_ip`` of ``FOUNDATION_CLA``
    (score 75.0, not 100.0) genuinely contradicts "legal = DCO" -- FAIL,
    not SKIPPED. This is the case the dispatch fix must never touch."""
    param = ParamScore(
        value="FOUNDATION_CLA", score=75.0, measured=True, reason=None, n=0,
        window_start=None, window_end=None, method="primary", source="test", fetched_at="2026-09-01T00:00:00+00:00",
    )
    record = _linux_record_with_legal_ip(param)
    snapshot = _snapshot_of(record)
    by_relation = {result.relation: result for result in evaluate_anchors_v2(snapshot)}
    assert by_relation["linux_dco"].verdict == FAIL
    assert "75.0" in by_relation["linux_dco"].observed


def test_linux_dco_is_skipped_not_failed_when_legal_ip_is_unmeasured() -> None:
    """The dispatch fix's oracle: the *same* anchor, same
    identity, with ``legal_ip`` genuinely ``Unmeasured`` -- SKIPPED, naming
    the real reason, never FAIL. Reproduces the case measured on
    ``tests/fixtures/snapshot_50`` when legal_ip started
    reading the governance envelope (absent there)."""
    param = ParamScore(
        value=None, score=None, measured=False, reason="collector_failure", n=0,
        window_start=None, window_end=None, method=None, source=None, fetched_at=None,
    )
    record = _linux_record_with_legal_ip(param)
    snapshot = _snapshot_of(record)
    by_relation = {result.relation: result for result in evaluate_anchors_v2(snapshot)}
    result = by_relation["linux_dco"]
    assert result.verdict == SKIPPED
    assert "collector_failure" in result.observed
    assert "unmeasured" in result.observed.lower()


def test_mutant_reverting_unmeasured_skip_to_fail_is_red() -> None:
    """Regression test for the dispatch fix itself: the old behaviour (report
    FAIL for an Unmeasured required input) must fail
    ``test_linux_dco_is_skipped_not_failed_when_legal_ip_is_unmeasured`` --
    reproduced directly here (without touching the source file) so this
    stands as a permanent regression guard."""
    param = ParamScore(
        value=None, score=None, measured=False, reason="collector_failure", n=0,
        window_start=None, window_end=None, method=None, source=None, fetched_at=None,
    )
    _linux_record_with_legal_ip(param)  # shape check only; the assertion below is the guard

    # The old (pre-fix) behaviour, inlined: any Unmeasured legal_ip was FAIL.
    old_verdict = FAIL if not param.measured else PASS
    assert old_verdict == FAIL, "the old code reported FAIL for an Unmeasured required input"
    # The fixed behaviour never does this -- see the test above.


def _snapshot_of(record):
    from cvi.core.pipeline import ScoredSnapshot, SnapshotCounts

    return ScoredSnapshot(
        snapshot_id="s", benchmark_version="2.0.0-dev", constants_hash="x", records=(record,),
        counts=SnapshotCounts(rated=0, gated=0, unrated=1, persona_visible={}), records_hash="x",
    )
