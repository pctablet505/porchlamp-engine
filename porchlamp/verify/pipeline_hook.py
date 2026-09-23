"""Composition point wiring :func:`porchlamp.verify.detectors.run_detectors` into
:func:`porchlamp.core.pipeline.score_snapshot`.

``porchlamp/core/*.py`` may only import ``porchlamp.core`` plus the standard library
(``tests/core/test_import_graph.py::test_core_imports_nothing_outside_core_and_stdlib``
and ``test_importing_core_loads_no_other_porchlamp_package`` both enforce this),
so a plain import of :mod:`porchlamp.verify.detectors` from ``porchlamp.core`` is not
available. ``porchlamp.core.pipeline.score_snapshot`` therefore accepts an
optional ``detectors: DetectorsCallable`` composition seam instead of
importing :mod:`porchlamp.verify.detectors` itself; this module -- outside
``porchlamp/core``, free to import both ``porchlamp.core`` and ``porchlamp.verify`` -- is the
one place that builds the real callable.

Two things :func:`porchlamp.verify.detectors.run_detectors` needs that a bare
``ScoreRecord`` does not carry:

* a :class:`~porchlamp.verify.detectors.DetectorInputs` per repo, built from that
  repo's *current* :class:`~porchlamp.core.models.FeatureBundle`
  (:func:`detector_inputs_from_bundle`) -- reusing
  :func:`porchlamp.verify.detectors.closed_unread_share_from_pr_window` rather
  than recomputing the closed-unread share/n a second way (the only other
  adapter in the codebase, ``porchlamp.audit.detectors_report._row_from_json``,
  builds ``DetectorInputs`` from an interim JSON row format, not from a
  real ``FeatureBundle`` -- there is no existing bundle-to-``DetectorInputs``
  adapter to reuse, so this is the one definition of it, not a second one).
* the previous snapshot's matching record, looked up by
  :func:`porchlamp.core.pipeline.repo_key` -- ``None`` when this repo was not in
  ``previous.records`` (a brand-new repo), which
  ``porchlamp.verify.detectors.run_detectors`` already treats correctly (only
  ``detect_param_jump`` needs a real previous record; every other detector
  ignores ``previous`` entirely).

All fields of :class:`~porchlamp.verify.detectors.DetectorInputs` except
``source_drift_held`` are sourced from ``bundle`` and wired into
:class:`~porchlamp.verify.detectors.DetectorInputs` by
:func:`detector_inputs_from_bundle`:

* ``newcomer_issue_count`` and ``elephant_factor`` from ``bundle.inputs``.
* ``closed_unread_share`` and ``closed_unread_n`` from ``bundle.pr_window``
  via :func:`porchlamp.verify.detectors.closed_unread_share_from_pr_window`.
* ``newcomer_accounts`` from ``bundle.cohorts`` via
  :func:`porchlamp.verify.detectors.newcomer_accounts_from_cohorts`.
* ``top_email_domain_share`` from ``bundle`` sidecar via
  :func:`porchlamp.derive.bundle.top_email_domain_share`.
* ``star_lockstep_penalty_applied`` from ``bundle`` sidecar via
  :func:`porchlamp.derive.bundle.star_velocity_flag`.

Only ``source_drift_held`` is left at its ``DetectorInputs`` default
(``None``) because it is the live-sample canary's own per-run output, not a
``FeatureBundle`` field.
"""
from __future__ import annotations

from typing import Mapping, Sequence

from porchlamp.core.models import FeatureBundle, Measured, ScoreRecord
from porchlamp.core.pipeline import DetectorsCallable, RepoKey, ScoredSnapshot, repo_key
from porchlamp.derive.bundle import (
    TopEmailDomainShareMeasured,
    star_velocity_flag as bundle_star_velocity_flag,
    top_email_domain_share as bundle_top_email_domain_share,
)
from porchlamp.verify.detectors import (
    DetectorInputs,
    closed_unread_share_from_pr_window,
    newcomer_accounts_from_cohorts,
    run_detectors,
)

#: ``bundle.inputs`` keys this adapter reads a raw ``Measured`` value from
#: (``porchlamp.core.constants.PARAM_INPUT_IDS`` names both; restated here only
#: because importing the whole 18-entry tuple to pick out two would be less
#: clear at the call site -- the same convention
#: ``porchlamp.verify.detectors``'s own ``INPUT_*`` constants already use).
_NEWCOMER_ISSUE_SUPPLY_INPUT_ID = "newcomer_issue_supply"
_ELEPHANT_FACTOR_INPUT_ID = "elephant_factor"


def _measured_bundle_value(bundle: FeatureBundle, input_id: str) -> float | None:
    """The raw ``Measured.value`` of ``input_id`` in ``bundle.inputs`` as a
    ``float``, or ``None`` when unmeasured or absent -- the same
    unmeasured-never-fires rule ``porchlamp.verify.detectors._measured_score``
    applies to a scored ``ParamScore``, applied here to a bundle input
    instead."""
    value = bundle.inputs.get(input_id)
    if not isinstance(value, Measured):
        return None
    return float(value.value)


def _top_email_domain_share(bundle: FeatureBundle) -> float | None:
    """``top_email_domain_share``'s raw share, or ``None`` when the
    bundle-level fact is ``Unmeasured`` (missing/failed commits envelope,
    or an insufficient non-bot commit sample -- both collapse to "no
    evidence", the same unmeasured-never-fires rule every other
    :class:`~porchlamp.verify.detectors.DetectorInputs` field already follows)."""
    fact = bundle_top_email_domain_share(bundle)
    return fact.value if isinstance(fact, TopEmailDomainShareMeasured) else None


def detector_inputs_from_bundle(
    bundle: FeatureBundle,
    source_drift_held: bool | None = None,
) -> DetectorInputs:
    """Build :class:`~porchlamp.verify.detectors.DetectorInputs` from a real
    ``FeatureBundle`` (module docstring): raw ``elephant_factor`` and
    ``newcomer_issue_supply`` values when measured, closed-unread share/n
    from ``bundle.pr_window``, ``newcomer_accounts`` from ``bundle.cohorts``,
    ``top_email_domain_share`` from the bundle sidecar fact,
    ``star_lockstep_penalty_applied`` from the bundle ``star_velocity_flag``
    sidecar fact, and optional ``source_drift_held``."""
    share, n = closed_unread_share_from_pr_window(bundle.pr_window)
    newcomer_accounts = newcomer_accounts_from_cohorts(bundle.cohorts)
    return DetectorInputs(
        newcomer_issue_count=_measured_bundle_value(bundle, _NEWCOMER_ISSUE_SUPPLY_INPUT_ID),
        elephant_factor=_measured_bundle_value(bundle, _ELEPHANT_FACTOR_INPUT_ID),
        top_email_domain_share=_top_email_domain_share(bundle),
        closed_unread_share=share,
        closed_unread_n=n,
        newcomer_accounts=newcomer_accounts,
        star_lockstep_penalty_applied=bundle_star_velocity_flag(bundle),
        source_drift_held=source_drift_held,
    )


def build_detectors(
    bundles: Sequence[FeatureBundle],
    source_drift_by_key: Mapping[RepoKey, bool] | None = None,
) -> DetectorsCallable:
    """The ``detectors`` callable :func:`porchlamp.core.pipeline.score_snapshot`
    accepts: ``(records, previous) -> {repo_key: [fired detector names]}``.

    Closes over ``bundles`` (the *current* snapshot's, the same sequence
    passed to ``score_snapshot``) to build each repo's
    :class:`DetectorInputs` once, up front, rather than re-deriving it per
    call. ``previous`` is matched by :func:`porchlamp.core.pipeline.repo_key`
    against ``previous.records``; a repo absent from ``previous`` (or
    ``previous is None``) scores with ``previous_record=None`` -- see
    :func:`porchlamp.verify.detectors.run_detectors` for which detectors that
    silently excludes (only ``detect_param_jump``).

    A repo in ``records`` with no matching bundle in ``bundles`` (should not
    happen when this callable is built from the same bundles that produced
    ``records`` -- :func:`porchlamp.core.pipeline.score_snapshot` builds ``records``
    from exactly ``bundles``) falls back to the all-``None`` empty
    ``DetectorInputs``, i.e. no detector needing a bundle fact can fire for
    it, rather than raising."""
    drift_map = source_drift_by_key or {}
    inputs_by_key: dict[RepoKey, DetectorInputs] = {
        repo_key(bundle): detector_inputs_from_bundle(
            bundle, source_drift_held=drift_map.get(repo_key(bundle))
        )
        for bundle in bundles
    }
    empty_inputs = DetectorInputs()

    def _run(
        records: Sequence[ScoreRecord], previous: "ScoredSnapshot | None"
    ) -> Mapping[RepoKey, list[str]]:
        previous_by_key: dict[RepoKey, ScoreRecord] = (
            {repo_key(record): record for record in previous.records} if previous is not None else {}
        )
        result: dict[RepoKey, list[str]] = {}
        for record in records:
            key = repo_key(record)
            previous_record = previous_by_key.get(key)
            inputs = inputs_by_key.get(key, empty_inputs)
            result[key] = run_detectors(record, previous_record, inputs)
        return result

    return _run
