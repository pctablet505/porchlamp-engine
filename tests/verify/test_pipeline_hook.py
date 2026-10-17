"""Acceptance tests for :mod:`porchlamp.verify.pipeline_hook`.

The real end-to-end proof that ``porchlamp.core.pipeline.score_snapshot``'s
``previous``/``detectors`` seam and ``porchlamp.verify.detectors.run_detectors``
compose correctly over genuine ``FeatureBundle``\\ s -- ``tests/core/
test_pipeline.py`` covers the seam itself (``apply_suspicion``'s contract)
with hand-written stub callables; this file covers only the real
composition ``porchlamp/verify/pipeline_hook.py`` builds.

Six repos, engineered so exactly three named detectors fire in the baseline
fixture (each on sourceable inputs -- only ``source_drift`` needs a
``DetectorInputs`` field from the live-sample canary rather than the bundle):

* ``param_jump`` on ``jump/repo``: ``dr90_decision_rate`` moves from 0.0
  (score 0.0) to 1.0 (score 100.0), n=20 both sides.
* ``closed_unread`` on ``closed/repo``: ``CLOSED_UNREAD_MIN_N`` external PRs,
  closed unmerged, no recorded human review (share 1.0). This repo is *absent* from the
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

The same six-repo ``current_bundles`` are also scored with ``previous=None``
outright (:func:`test_previous_none_still_fires_single_snapshot_detectors`):
``previous=None`` suppresses only cross-snapshot detectors (``param_jump`` here),
never single-snapshot ones. With no previous snapshot at all, ``jump/repo``
therefore fires nothing (there is no previous ``dr90`` to jump from), while
``closed/repo`` and ``spam/repo`` fire exactly as they do with a real
``previous`` -- the presence or absence of a specific previous record
(whole snapshot missing, or just this one repo new to it) is the only thing
that changes ``param_jump``'s own answer.
"""
from __future__ import annotations

import copy
import datetime

from porchlamp.core import constants
from porchlamp.core.gates import GateEvidence
from porchlamp.core.models import (
    Cohorts,
    FeatureBundle,
    Measured,
    NewcomerAuthor,
    PRRef,
    PRWindow,
    PRWindowEvent,
    RepoIdentity,
    Unmeasured,
)
from porchlamp.core.pipeline import ScoredSnapshot, repo_key, score_snapshot
from porchlamp.snapshot.populations import PopulationSet
from porchlamp.verify.detectors import CLOSED_UNREAD_MIN_N, DETECTOR_NAMES
from porchlamp.verify.pipeline_hook import build_detectors, detector_inputs_from_bundle

NOW = datetime.datetime(2026, 9, 12, 0, 0, tzinfo=datetime.timezone.utc)
_HASH64 = "e" * 64
_SNAPSHOT_PREVIOUS = "snap-previous"
_SNAPSHOT_CURRENT = "snap-current"

KEY_PARAM_JUMP = "jump/repo"
KEY_CLOSED_UNREAD = "closed/repo"
KEY_LABEL_SPAM = "spam/repo"
KEY_HEALTHY = ("healthy/one", "healthy/two", "healthy/three")

_HEALTHY_EVIDENCE = GateEvidence(
    manifest_found=True, is_curated_anchor=False, license_file_present=None,
    fork_parent_in_universe=None, vitality_commits_180d=5, off_platform_marker=None,
)
_EMPTY_POPULATIONS = PopulationSet(
    snapshot_id="pop", benchmark_version=constants.BENCHMARK_VERSION,
    constants_hash=constants.CONSTANTS_HASH, populations=(),
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
    inputs = {key: _u() for key in constants.PARAM_INPUT_IDS}
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


def _bundle(repo: RepoIdentity, inputs: dict, *, events: tuple = (), cohorts: Cohorts | None = None) -> FeatureBundle:
    bundle = FeatureBundle(
        schema_version=constants.SCHEMA_VERSION, repo=repo, inputs=inputs,
        pr_window=PRWindow(
            start=_iso(NOW - datetime.timedelta(days=180)) if events else None,
            end=_iso(NOW) if events else None, truncated=False, events=events,
        ),
        cohorts=cohorts if cohorts is not None else Cohorts(decision_pr_numbers=(), newcomer_authors=()),
        provenance_hash=_HASH64,
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
        events=tuple(_closed_unmerged_unread_event(i) for i in range(1, CLOSED_UNREAD_MIN_N + 1)),
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
    # top_email_domain_share is None here because this hand-built bundle
    # never had the top_email_domain_share sidecar fact set -- not because
    # it is an unsourceable field.
    assert inputs.top_email_domain_share is None
    assert inputs.newcomer_accounts == ()
    assert inputs.star_lockstep_penalty_applied is None
    assert inputs.source_drift_held is None


def test_detector_inputs_from_bundle_reads_top_email_domain_share_sidecar() -> None:
    """When a bundle carries the ``top_email_domain_share`` sidecar fact,
    the adapter reads the real share through."""
    from porchlamp.derive.bundle import TopEmailDomainShareMeasured, _set_top_email_domain_share

    bundle = _bundle(_repo("org", "alpha"), _full_inputs({"elephant_factor": _m(4.0, n=30)}))
    _set_top_email_domain_share(bundle, TopEmailDomainShareMeasured(
        value=0.75, top_domain="corp.com", n=30, method="measured:commits_365d",
        source="s", source_hash="a" * 64, fetched_at=_iso(NOW),
    ))
    inputs = detector_inputs_from_bundle(bundle)
    assert inputs.top_email_domain_share == 0.75


def test_detector_inputs_from_bundle_top_email_domain_share_unmeasured_is_none() -> None:
    from porchlamp.core.models import Unmeasured as _Unmeasured
    from porchlamp.derive.bundle import _set_top_email_domain_share

    bundle = _bundle(_repo("org", "alpha"), _full_inputs())
    _set_top_email_domain_share(bundle, _Unmeasured(reason="insufficient_n"))
    inputs = detector_inputs_from_bundle(bundle)
    assert inputs.top_email_domain_share is None


def test_ef_domain_mismatch_fires_through_the_real_composition() -> None:
    """End-to-end: ``ef_domain_mismatch`` (top_email_domain_share >= 0.5 and
    elephant_factor >= 4) fires through the real ``build_detectors`` ->
    ``run_detectors`` composition when a bundle carries both the scored
    ``elephant_factor`` input and the ``top_email_domain_share``
    sidecar fact."""
    from porchlamp.derive.bundle import TopEmailDomainShareMeasured, _set_top_email_domain_share

    owner, name = "mismatch", "repo"
    bundle = _bundle(_repo(owner, name), _full_inputs({"elephant_factor": _m(6.0, n=30)}))
    _set_top_email_domain_share(bundle, TopEmailDomainShareMeasured(
        value=0.9, top_domain="corp.com", n=30, method="measured:commits_365d",
        source="s", source_hash="a" * 64, fetched_at=_iso(NOW),
    ))
    detectors = build_detectors([bundle])
    snapshot = _score([bundle], "snap-ef-domain", previous=None, detectors=detectors)
    record = _by_key(snapshot)[repo_key(bundle)]
    assert "ef_domain_mismatch" in record.suspicion


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


def test_previous_none_still_fires_single_snapshot_detectors() -> None:
    """``previous=None`` -- no baseline snapshot at all, not just one repo
    missing from it -- still fires every single-snapshot detector normally
    through the real ``build_detectors`` composition. Only ``param_jump``
    (the one detector that reads ``previous``) yields nothing, on every repo,
    since there is no previous record anywhere to compare against."""
    current_bundles = _current_bundles()
    detectors = build_detectors(current_bundles)
    snapshot = _score(current_bundles, _SNAPSHOT_CURRENT, previous=None, detectors=detectors)
    by_key = _by_key(snapshot)

    assert by_key[KEY_CLOSED_UNREAD].suspicion == ["closed_unread"]
    assert by_key[KEY_LABEL_SPAM].suspicion == ["label_spam"]
    # jump/repo's only measured fact is the dr90 jump itself -- with no
    # previous record anywhere, param_jump cannot fire and nothing else does.
    assert by_key[KEY_PARAM_JUMP].suspicion == []
    for key in KEY_HEALTHY:
        assert by_key[key].suspicion == [], key

    fired = {name for record in snapshot.records for name in record.suspicion}
    assert fired == {"closed_unread", "label_spam"}
    assert "param_jump" not in fired


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


def test_newcomer_burst_fires_through_build_detectors_pipeline_hook() -> None:
    """Regression test: verify newcomer_accounts_from_cohorts is wired into
    detector_inputs_from_bundle, allowing detect_newcomer_burst to fire
    during live snapshot scoring when 5 newcomer accounts merge within 7 days."""
    authors = tuple(
        NewcomerAuthor(
            login=f"burst_newcomer_{i}",
            first_pr=PRRef(number=200 + i, created_at=_iso(NOW - datetime.timedelta(days=2, hours=i)),
                           merged_at=_iso(NOW - datetime.timedelta(days=2, hours=i - 1))),
            followups=(),
            account_created_at=_iso(NOW - datetime.timedelta(days=3, hours=i)),
        )
        for i in range(5)
    )
    cohorts = Cohorts(decision_pr_numbers=(), newcomer_authors=authors)
    repo = _repo("burst", "repo")
    bundle = _bundle(repo, _full_inputs(), cohorts=cohorts)
    detectors = build_detectors([bundle])
    snapshot = _score([bundle], "snap-burst-test", previous=None, detectors=detectors)
    assert len(snapshot.records) == 1
    assert "newcomer_burst" in snapshot.records[0].suspicion


def test_detector_inputs_from_bundle_reads_star_velocity_flag_sidecar() -> None:
    """When a bundle carries the star_velocity_flag sidecar fact (True/False or
    Measured 1/0), detector_inputs_from_bundle populates star_lockstep_penalty_applied."""
    from porchlamp.derive.bundle import set_star_velocity_flag

    bundle_true = _bundle(_repo("org", "true-flag"), _full_inputs())
    set_star_velocity_flag(bundle_true, True)
    assert detector_inputs_from_bundle(bundle_true).star_lockstep_penalty_applied is True

    bundle_false = _bundle(_repo("org", "false-flag"), _full_inputs())
    set_star_velocity_flag(bundle_false, False)
    assert detector_inputs_from_bundle(bundle_false).star_lockstep_penalty_applied is False

    bundle_measured_true = _bundle(_repo("org", "measured-true"), _full_inputs())
    set_star_velocity_flag(bundle_measured_true, _m(1))
    assert detector_inputs_from_bundle(bundle_measured_true).star_lockstep_penalty_applied is True

    bundle_measured_false = _bundle(_repo("org", "measured-false"), _full_inputs())
    set_star_velocity_flag(bundle_measured_false, _m(0))
    assert detector_inputs_from_bundle(bundle_measured_false).star_lockstep_penalty_applied is False


def test_star_burst_fires_through_build_detectors_pipeline_hook() -> None:
    """End-to-end: star_burst fires during snapshot scoring when star_velocity_flag
    is True on the bundle, and does not fire when False."""
    from porchlamp.derive.bundle import set_star_velocity_flag

    flagged = _bundle(_repo("star", "flagged"), _full_inputs())
    set_star_velocity_flag(flagged, True)
    normal = _bundle(_repo("star", "normal"), _full_inputs())
    set_star_velocity_flag(normal, False)

    detectors = build_detectors([flagged, normal])
    snapshot = _score([flagged, normal], "snap-star-burst", previous=None, detectors=detectors)
    by_key = _by_key(snapshot)
    assert "star_burst" in by_key["star/flagged"].suspicion
    assert "star_burst" not in by_key["star/normal"].suspicion

