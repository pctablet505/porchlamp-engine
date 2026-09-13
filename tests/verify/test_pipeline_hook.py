"""Acceptance tests for :mod:`cvi.verify.pipeline_hook` (packet W3-2f).

Fail-before: before this packet ``cvi.verify.pipeline_hook`` does not exist,
so every test below fails on ``ModuleNotFoundError``.

The real end-to-end proof that ``cvi.core.pipeline.score_snapshot``'s
``previous``/``detectors`` seam and ``cvi.verify.detectors.run_detectors``
compose correctly over genuine ``FeatureBundle``\\ s -- ``tests/core/
test_pipeline.py`` covers the seam itself (``apply_suspicion``'s contract)
with hand-written stub callables; this file covers only the real
composition ``cvi/verify/pipeline_hook.py`` builds.

Six repos, engineered so exactly three named detectors fire (each on
sourceable inputs -- ``star_burst``, ``ef_domain_mismatch``,
``newcomer_burst`` and ``source_drift`` need a ``DetectorInputs`` field this
adapter never populates, per its own module docstring, so none of those
four can ever fire through this composition regardless of bundle content):

* ``param_jump`` on ``jump/repo``: ``dr90_decision_rate`` moves from 0.0
  (score 0.0) to 1.0 (score 100.0), n=20 both sides.
* ``closed_unread`` on ``closed/repo``: two external PRs, closed unmerged,
  no recorded human review (share 1.0). This repo is *absent* from the
  previous snapshot entirely (the other deliverable case: "a repo absent
  from previous can still fire the single-snapshot detectors") -- it is
  also given the same dr90 jump as ``jump/repo`` to prove that specific
  absence suppresses ``param_jump`` for it (no previous record to compare
  against) while ``closed_unread`` still fires.
* ``label_spam`` on ``spam/repo``: raw ``newcomer_issue_supply`` 60 (>= 50)
  and ``newcomer_conversion`` 0.02 (score 4.0 <= 10, n=5).
* ``healthy/one``, ``healthy/two``, ``healthy/three``: nothing measured (or,
  for one of them, healthy values only) -- the over-correction guard that
  nothing fires on unremarkable data.
"""
from __future__ import annotations

import copy
import datetime

from cvi.core import constants_v2
from cvi.core.gates import GateEvidence
from cvi.core.models import (
    Cohorts,
    FeatureBundle,
    Measured,
    PRWindow,
    PRWindowEvent,
    RepoIdentity,
    Unmeasured,
)
from cvi.core.pipeline import ScoredSnapshot, repo_key, score_snapshot
from cvi.snapshot.populations import PopulationSet
from cvi.verify.detectors import DETECTOR_NAMES
from cvi.verify.pipeline_hook import build_detectors, detector_inputs_from_bundle

NOW = datetime.datetime(2026, 9, 12, 0, 0, tzinfo=datetime.timezone.utc)
_HASH64 = "e" * 64
_SNAPSHOT_PREVIOUS = "snap-w3-2f-previous"
_SNAPSHOT_CURRENT = "snap-w3-2f-current"

KEY_PARAM_JUMP = "jump/repo"
KEY_CLOSED_UNREAD = "closed/repo"
KEY_LABEL_SPAM = "spam/repo"
KEY_HEALTHY = ("healthy/one", "healthy/two", "healthy/three")

_HEALTHY_EVIDENCE = GateEvidence(
    manifest_found=True, is_curated_anchor=False, license_file_present=None,
    fork_parent_in_universe=None, vitality_commits_180d=5, off_platform_marker=None,
)
_EMPTY_POPULATIONS = PopulationSet(
    snapshot_id="pop", benchmark_version=constants_v2.BENCHMARK_VERSION,
    constants_hash=constants_v2.CONSTANTS_HASH, populations=(),
)


def _iso(value: datetime.datetime) -> str:
    return value.isoformat()


def _m(value, *, n: int = 0) -> Measured:
    return Measured(
        value=value, n=n, window_start=None, window_end=None, method="primary",
        source="test://fixture", source_hash=_HASH64, fetched_at=_iso(NOW),
    )


def _u(reason: str = "not_applicable") -> Unmeasured:
    return Unmeasured(reason=reason)


def _repo(owner: str, name: str) -> RepoIdentity:
    return RepoIdentity(
        owner=owner, name=name, ecosystem="pypi", archived=False, mirror_url=None,
        fork=False, license_spdx="MIT", default_branch="main",
    )


def _full_inputs(measured=None) -> dict:
    inputs = {key: _u() for key in constants_v2.PARAM_INPUT_IDS}
    inputs.update(measured or {})
    return inputs


def _closed_unmerged_unread_event(pr_number: int) -> PRWindowEvent:
    created = NOW - datetime.timedelta(days=10)
    return PRWindowEvent(
        pr_number=pr_number, created_at=_iso(created),
        closed_at=_iso(created + datetime.timedelta(days=1)), merged_at=None,
        author_login="drive-by", author_association="NONE", is_external=True,
        first_human_review_at=None,
    )


def _bundle(repo: RepoIdentity, inputs: dict, *, events: tuple = ()) -> FeatureBundle:
    bundle = FeatureBundle(
        schema_version=constants_v2.SCHEMA_VERSION, repo=repo, inputs=inputs,
        pr_window=PRWindow(
            start=_iso(NOW - datetime.timedelta(days=180)) if events else None,
            end=_iso(NOW) if events else None, truncated=False, events=events,
        ),
        cohorts=Cohorts(decision_pr_numbers=(), newcomer_authors=()), provenance_hash=_HASH64,
    )
    bundle.validate()
    return bundle


def _owner_name(key: str) -> tuple[str, str]:
    owner, name = key.split("/")
    return owner, name


def _current_bundles() -> list[FeatureBundle]:
    owner, name = _owner_name(KEY_PARAM_JUMP)
    param_jump_bundle = _bundle(_repo(owner, name), _full_inputs({"dr90_decision_rate": _m(1.0, n=20)}))

    owner, name = _owner_name(KEY_CLOSED_UNREAD)
    closed_unread_bundle = _bundle(
        _repo(owner, name), _full_inputs({"dr90_decision_rate": _m(1.0, n=20)}),
        events=(_closed_unmerged_unread_event(1), _closed_unmerged_unread_event(2)),
    )

    owner, name = _owner_name(KEY_LABEL_SPAM)
    label_spam_bundle = _bundle(
        _repo(owner, name),
        _full_inputs({"newcomer_issue_supply": _m(60.0, n=0), "newcomer_conversion": _m(0.02, n=5)}),
    )

    healthy_bundles = [_bundle(_repo(*_owner_name(key)), _full_inputs()) for key in KEY_HEALTHY]

    return [param_jump_bundle, closed_unread_bundle, label_spam_bundle, *healthy_bundles]


def _previous_bundles() -> list[FeatureBundle]:
    """Same six repos except: ``jump/repo``'s ``dr90`` is 0.0 (score 0.0,
    n=20) instead of 1.0 -- the 100-point jump ``param_jump`` needs -- and
    ``closed/repo`` is entirely absent (the "repo absent from previous"
    case, module docstring)."""
    owner, name = _owner_name(KEY_PARAM_JUMP)
    param_jump_bundle = _bundle(_repo(owner, name), _full_inputs({"dr90_decision_rate": _m(0.0, n=20)}))

    owner, name = _owner_name(KEY_LABEL_SPAM)
    label_spam_bundle = _bundle(
        _repo(owner, name),
        _full_inputs({"newcomer_issue_supply": _m(60.0, n=0), "newcomer_conversion": _m(0.02, n=5)}),
    )

    healthy_bundles = [_bundle(_repo(*_owner_name(key)), _full_inputs()) for key in KEY_HEALTHY]

    return [param_jump_bundle, label_spam_bundle, *healthy_bundles]


def _score(bundles: list[FeatureBundle], snapshot_id: str, *, previous=None, detectors=None) -> ScoredSnapshot:
    gate_evidence = {repo_key(b): _HEALTHY_EVIDENCE for b in bundles}
    raw_hashes = {repo_key(b): _HASH64 for b in bundles}
    return score_snapshot(
        bundles, _EMPTY_POPULATIONS, snapshot_id=snapshot_id, as_of=NOW,
        gate_evidence=gate_evidence, raw_hashes=raw_hashes,
        previous=previous, detectors=detectors,
    )


def _by_key(snapshot: ScoredSnapshot) -> dict:
    return {repo_key(r): r for r in snapshot.records}


def _score_current_with_detection() -> tuple[ScoredSnapshot, ScoredSnapshot]:
    current_bundles = _current_bundles()
    previous_bundles = _previous_bundles()
    previous_snapshot = _score(previous_bundles, _SNAPSHOT_PREVIOUS)
    detectors = build_detectors(current_bundles)
    current_snapshot = _score(
        current_bundles, _SNAPSHOT_CURRENT, previous=previous_snapshot, detectors=detectors,
    )
    return current_snapshot, previous_snapshot


# --------------------------------------------------------------------------- #
# detector_inputs_from_bundle: the adapter alone.
# --------------------------------------------------------------------------- #
def test_detector_inputs_from_bundle_reads_measured_bundle_facts() -> None:
    bundle = _bundle(
        _repo("org", "alpha"),
        _full_inputs({"newcomer_issue_supply": _m(12.0, n=0), "elephant_factor": _m(3.0, n=30)}),
        events=(_closed_unmerged_unread_event(1),),
    )
    inputs = detector_inputs_from_bundle(bundle)
    assert inputs.newcomer_issue_count == 12.0
    assert inputs.elephant_factor == 3.0
    assert inputs.closed_unread_share == 1.0
    assert inputs.closed_unread_n == 1
    # the three ownership-request fields and the canary field: never sourced
    # from a bundle (module docstring) -- always the "not collected" default.
    assert inputs.top_email_domain_share is None
    assert inputs.newcomer_accounts == ()
    assert inputs.star_lockstep_penalty_applied is None
    assert inputs.source_drift_held is None


def test_detector_inputs_from_bundle_unmeasured_never_fires() -> None:
    bundle = _bundle(_repo("org", "alpha"), _full_inputs())  # everything Unmeasured, no PR events
    inputs = detector_inputs_from_bundle(bundle)
    assert inputs.newcomer_issue_count is None
    assert inputs.elephant_factor is None
    assert inputs.closed_unread_share is None
    assert inputs.closed_unread_n == 0


# --------------------------------------------------------------------------- #
# The 6-repo fixture: exactly three detectors fire, on the named repos.
# --------------------------------------------------------------------------- #
def test_exactly_three_detectors_fire_on_the_named_repos() -> None:
    current_snapshot, previous_snapshot = _score_current_with_detection()
    by_key = _by_key(current_snapshot)

    assert by_key[KEY_PARAM_JUMP].suspicion == ["param_jump"]
    assert by_key[KEY_CLOSED_UNREAD].suspicion == ["closed_unread"]
    assert by_key[KEY_LABEL_SPAM].suspicion == ["label_spam"]
    for key in KEY_HEALTHY:
        assert by_key[key].suspicion == [], key

    fired = {name for record in current_snapshot.records for name in record.suspicion}
    assert fired == {"param_jump", "closed_unread", "label_spam"}
    assert fired <= set(DETECTOR_NAMES)

    assert current_snapshot.counts.suspicion_counts == {
        "param_jump": 1, "closed_unread": 1, "label_spam": 1,
    }

    # the deliverable's other explicit case: a repo absent from `previous`
    # can still fire a single-snapshot detector, but never `param_jump`.
    assert KEY_CLOSED_UNREAD not in _by_key(previous_snapshot)


def test_repo_absent_from_previous_suppresses_only_param_jump() -> None:
    """``closed/repo`` carries the exact same dr90 jump as ``jump/repo``
    (0.0 -> 1.0, n=20 both sides) -- if it had a previous record,
    ``param_jump`` would fire on it too. It does not have one (absent from
    ``_previous_bundles``), so ``param_jump`` must not fire, while
    ``closed_unread`` (single-snapshot) still does."""
    current_snapshot, _ = _score_current_with_detection()
    record = _by_key(current_snapshot)[KEY_CLOSED_UNREAD]
    assert "param_jump" not in record.suspicion
    assert "closed_unread" in record.suspicion


# --------------------------------------------------------------------------- #
# Purity: build_detectors/score_snapshot never mutate the bundles/snapshot
# they read from.
# --------------------------------------------------------------------------- #
def test_composition_does_not_mutate_bundles_or_previous_snapshot() -> None:
    current_bundles = _current_bundles()
    previous_bundles = _previous_bundles()
    previous_snapshot = _score(previous_bundles, _SNAPSHOT_PREVIOUS)

    current_before = copy.deepcopy(current_bundles)
    previous_before = copy.deepcopy(previous_snapshot)

    detectors = build_detectors(current_bundles)
    _score(current_bundles, _SNAPSHOT_CURRENT, previous=previous_snapshot, detectors=detectors)

    assert current_bundles == current_before
    assert previous_snapshot == previous_before
