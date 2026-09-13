"""Composition point wiring :func:`cvi.verify.detectors.run_detectors` into
:func:`cvi.core.pipeline.score_snapshot` (packet W3-2f).

``cvi/core/*.py`` may only import ``cvi.core`` plus the standard library
(``tests/core/test_import_graph.py::test_core_imports_nothing_outside_core_and_stdlib``
and ``test_importing_core_loads_no_other_cvi_package`` both enforce this,
checked directly against this base commit before writing this module -- the
lead's preferred "plain import" option named in the packet dispatch is not
available). ``cvi.core.pipeline.score_snapshot`` therefore accepts an
optional ``detectors: DetectorsCallable`` composition seam instead of
importing :mod:`cvi.verify.detectors` itself; this module -- outside
``cvi/core``, free to import both ``cvi.core`` and ``cvi.verify`` -- is the
one place that builds the real callable.

Two things :func:`cvi.verify.detectors.run_detectors` needs that a bare
``ScoreRecord`` does not carry:

* a :class:`~cvi.verify.detectors.DetectorInputs` per repo, built from that
  repo's *current* :class:`~cvi.core.models.FeatureBundle`
  (:func:`detector_inputs_from_bundle`) -- reusing
  :func:`cvi.verify.detectors.closed_unread_share_from_pr_window` rather
  than recomputing the closed-unread share/n a second way (the only other
  adapter in the codebase, ``cvi.audit.detectors_report._row_from_json``,
  builds ``DetectorInputs`` from an interim JSON row format, not from a
  real ``FeatureBundle`` -- there is no existing bundle-to-``DetectorInputs``
  adapter to reuse, so this is the one definition of it, not a second one).
* the previous snapshot's matching record, looked up by
  :func:`cvi.core.pipeline.repo_key` -- ``None`` when this repo was not in
  ``previous.records`` (a brand-new repo), which
  ``cvi.verify.detectors.run_detectors`` already treats correctly (only
  ``detect_param_jump`` needs a real previous record; every other detector
  ignores ``previous`` entirely).

Three of :class:`~cvi.verify.detectors.DetectorInputs`' seven fields have no
landed collector yet (``top_email_domain_share``, ``newcomer_accounts``,
``star_lockstep_penalty_applied`` -- see that module's own module docstring,
"Named inputs this packet cannot source yet") and a fourth
(``source_drift_held``) is the W3-3 live-sample canary's own per-run output,
not a bundle field. :func:`detector_inputs_from_bundle` leaves all four at
their ``DetectorInputs`` default (``None``/``()``\\ ) -- "not collected for
this repo", the same unmeasured-never-fires rule
:mod:`cvi.verify.detectors` already documents for every other field, rather
than inventing a value this packet has no evidence for.
"""
from __future__ import annotations

from typing import Mapping, Sequence

from cvi.core.models import FeatureBundle, Measured, ScoreRecord
from cvi.core.pipeline import DetectorsCallable, RepoKey, ScoredSnapshot, repo_key
from cvi.verify.detectors import DetectorInputs, closed_unread_share_from_pr_window, run_detectors

#: ``bundle.inputs`` keys this adapter reads a raw ``Measured`` value from
#: (``cvi.core.constants_v2.PARAM_INPUT_IDS`` names both; restated here only
#: because importing the whole 18-entry tuple to pick out two would be less
#: clear at the call site -- the same convention
#: ``cvi.verify.detectors``'s own ``INPUT_*`` constants already use).
_NEWCOMER_ISSUE_SUPPLY_INPUT_ID = "newcomer_issue_supply"
_ELEPHANT_FACTOR_INPUT_ID = "elephant_factor"


def _measured_bundle_value(bundle: FeatureBundle, input_id: str) -> float | None:
    """The raw ``Measured.value`` of ``input_id`` in ``bundle.inputs`` as a
    ``float``, or ``None`` when unmeasured or absent -- the same
    unmeasured-never-fires rule ``cvi.verify.detectors._measured_score``
    applies to a scored ``ParamScore``, applied here to a bundle input
    instead."""
    value = bundle.inputs.get(input_id)
    if not isinstance(value, Measured):
        return None
    return float(value.value)


def detector_inputs_from_bundle(bundle: FeatureBundle) -> DetectorInputs:
    """Build the one :class:`~cvi.verify.detectors.DetectorInputs` a real
    ``FeatureBundle`` can source today (module docstring): raw
    ``elephant_factor`` and ``newcomer_issue_supply`` values when measured,
    plus the closed-unread share/n computed from ``bundle.pr_window`` --
    every other field stays at its documented "not collected" default."""
    share, n = closed_unread_share_from_pr_window(bundle.pr_window)
    return DetectorInputs(
        newcomer_issue_count=_measured_bundle_value(bundle, _NEWCOMER_ISSUE_SUPPLY_INPUT_ID),
        elephant_factor=_measured_bundle_value(bundle, _ELEPHANT_FACTOR_INPUT_ID),
        closed_unread_share=share,
        closed_unread_n=n,
    )


def build_detectors(bundles: Sequence[FeatureBundle]) -> DetectorsCallable:
    """The ``detectors`` callable :func:`cvi.core.pipeline.score_snapshot`
    accepts: ``(records, previous) -> {repo_key: [fired detector names]}``.

    Closes over ``bundles`` (the *current* snapshot's, the same sequence
    passed to ``score_snapshot``) to build each repo's
    :class:`DetectorInputs` once, up front, rather than re-deriving it per
    call. ``previous`` is matched by :func:`cvi.core.pipeline.repo_key`
    against ``previous.records``; a repo absent from ``previous`` (or
    ``previous is None``) scores with ``previous_record=None`` -- see
    :func:`cvi.verify.detectors.run_detectors` for which detectors that
    silently excludes (only ``detect_param_jump``).

    A repo in ``records`` with no matching bundle in ``bundles`` (should not
    happen when this callable is built from the same bundles that produced
    ``records`` -- :func:`cvi.core.pipeline.score_snapshot` builds ``records``
    from exactly ``bundles``) falls back to the all-``None`` empty
    ``DetectorInputs``, i.e. no detector needing a bundle fact can fire for
    it, rather than raising."""
    inputs_by_key: dict[RepoKey, DetectorInputs] = {
        repo_key(bundle): detector_inputs_from_bundle(bundle) for bundle in bundles
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
