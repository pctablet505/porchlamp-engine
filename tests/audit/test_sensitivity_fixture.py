"""W2-8b acceptance: D-AGG-3 sensitivity over the v2 fixture snapshot
(``cvi.audit.sensitivity.sensitivity_v2``, ``docs/v2/02-RANKING-DESIGN.md``
section 7).

Fail-before: before this packet ``sensitivity_v2`` does not exist, so every
test below fails on ``AttributeError``/``ImportError``.

Two things are proven, on two different snapshots, on purpose:

1. ``test_sensitivity_v2_arithmetic_on_a_rated_synthetic_snapshot`` --
   ``sensitivity_v2`` is *correct*: on a hand-built snapshot with 8 rated
   repos (the same synthetic fixture ``tests/core/test_pipeline.py`` uses,
   reproduced locally -- see that file's module docstring for the
   arithmetic), every perturbation clears tau-b >= 0.90, and the two
   pillars whose perturbation moves the composite (responsiveness is the
   only pillar these 8 repos vary on) show a real, non-1.0 tau-b rather
   than a suspiciously perfect score on every row.
2. ``test_sensitivity_v2_on_real_fixture_needs_at_least_two_rated_repos`` --
   the packet's own literal acceptance ("tau >= 0.90 for every perturbation
   on the fixture") **cannot be evaluated on `tests/fixtures/snapshot_50`**:
   the real sample-200 corpus, run through the currently-landed W2-9(f)
   v1-to-v2 loader, tops out at 25.5% composite coverage for its best-
   covered repo (measured, not estimated -- see the handoff) -- nowhere
   close to D-EVID-1's 60% rated threshold. Every one of this fixture's 50
   repos is therefore gated or unrated; zero are rated, so there is no
   ranking to compare a perturbation against. This is not this packet's
   own defect (``cvi/core/pipeline.py`` correctly computes zero rated
   repos from the correct inputs) and not tunable away (R-6, D-AGG-3): it
   is a genuine data-pipeline gap upstream of this packet (dr90/mr_first/
   gr14/openssf_criticality/pipeline_defenses/newcomer_conversion/
   rr90_return_rate/dx are still ``_unmapped`` in
   ``cvi/derive/bundle.py::INPUT_MAPPINGS``, and this specific sample's
   derived documents additionally carry no ``pr_window_count`` provenance
   at all, so ttfhr/ttm_p90/substantive_review_rate never resolve either),
   recorded here as a measured fact and in the handoff as a blocking
   finding for the lead -- not worked around by hand-picking a
   differently-selected 50 repos to chase a passing number.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from cvi.audit.sensitivity import DEFAULT_PERTURBATION_PP, TAU_RELEASE_THRESHOLD, sensitivity_v2
from cvi.core.pipeline import score_snapshot

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "snapshot_50"
SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "build_fixture_snapshot.py"
PIPELINE_TEST_PATH = Path(__file__).resolve().parents[1] / "core" / "test_pipeline.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# 1. Correctness: a rated synthetic snapshot (reuses tests/core/test_pipeline.py's
#    own 8-rated-repo _Fixture12, so the two files never drift on what
#    "a rated snapshot" looks like).
# --------------------------------------------------------------------------- #
def _synthetic_rated_snapshot():
    pipeline_tests = _load_module("test_pipeline_for_sensitivity", PIPELINE_TEST_PATH)
    return pipeline_tests._Fixture12.score()


def test_sensitivity_v2_arithmetic_on_a_rated_synthetic_snapshot() -> None:
    snapshot = _synthetic_rated_snapshot()
    report = sensitivity_v2(snapshot)

    assert report.rated == 8
    assert report.pp == DEFAULT_PERTURBATION_PP
    assert len(report.rows) == 10  # 5 pillars x 2 directions

    for row in report.rows:
        assert row.tau_b is not None, (row.pillar, row.delta_pp)
        assert row.tau_b >= TAU_RELEASE_THRESHOLD, (row.pillar, row.delta_pp, row.tau_b)
    assert report.all_pass is True

    # These 8 repos vary only on dr90 (a responsiveness-pillar input), so a
    # responsiveness-weight perturbation is the one most likely to actually
    # move a rank -- proving the function measures something real, not
    # returning a hard-coded 1.0 regardless of input (an easy mutant this
    # guards against: see the handoff's mutation table).
    by_pillar_sign = {(row.pillar, row.delta_pp): row.tau_b for row in report.rows}
    assert any(
        by_pillar_sign[("responsiveness", sign)] < 1.0 for sign in (DEFAULT_PERTURBATION_PP, -DEFAULT_PERTURBATION_PP)
    )


def test_sensitivity_v2_rejects_fewer_than_two_rated_repos() -> None:
    """Fail-before / oracle for the guard itself: a one-rated-repo snapshot
    cannot support a ranking comparison."""
    snapshot = _synthetic_rated_snapshot()
    one_rated = [record for record in snapshot.records if record.rank is not None][:1]
    gated_or_unrated = [record for record in snapshot.records if record.rank is None]
    from dataclasses import replace

    tiny = replace(snapshot, records=tuple(one_rated + gated_or_unrated))
    with pytest.raises(ValueError, match="need at least 2 rated repos"):
        sensitivity_v2(tiny)


# --------------------------------------------------------------------------- #
# 2. The real, measured finding on tests/fixtures/snapshot_50.
# --------------------------------------------------------------------------- #
def _real_fixture_snapshot():
    build_script = _load_module("build_fixture_snapshot", SCRIPT_PATH)
    frozen = build_script.load_frozen_fixture(FIXTURE_DIR)
    return score_snapshot(
        frozen.bundles, frozen.populations, snapshot_id=frozen.snapshot_id, as_of=frozen.as_of,
        gate_evidence=frozen.gate_evidence, raw_hashes=frozen.raw_hashes,
    )


def test_sensitivity_v2_on_real_fixture_needs_at_least_two_rated_repos() -> None:
    """The packet's literal acceptance criterion cannot be evaluated on
    this fixture -- see the module docstring's finding #2. Asserts the
    exact, measured blocking condition rather than skipping quietly."""
    snapshot = _real_fixture_snapshot()
    rated = [record for record in snapshot.records if record.rank is not None]
    assert rated == [], (
        f"expected 0 rated repos on tests/fixtures/snapshot_50 (measured finding); "
        f"got {len(rated)} -- if a loader/collector fix has landed, re-run "
        f"scripts/build_fixture_snapshot.py, update this assertion, and report "
        f"real tau numbers instead of this blocking-condition test."
    )
    with pytest.raises(ValueError, match="need at least 2 rated repos"):
        sensitivity_v2(snapshot)


# --------------------------------------------------------------------------- #
# Mutation-battery oracle: renormalisation over measured pillars only
# (D-MISS-2 generalised, see cvi.audit.sensitivity's module docstring).
# --------------------------------------------------------------------------- #
def test_weighted_composite_and_coverage_hand_computed() -> None:
    """Hand computation with one pillar unmeasured (accessibility), so
    renormalisation (dividing by the *measured* weight, not 1.0) is
    load-bearing -- the packet's own 8-rated synthetic fixture never
    exercises this because every one of its pillars is always fully
    measured. Mirrors ``tests/core/test_persona_v2.py::
    test_persona_composite_applies_binding_ceiling_not_just_tier_by_score``'s
    own hand-picked numbers (a perturbed pillar-weight vector is exactly
    the same kind of "arbitrary weights over ScoreRecord.pillars"
    reduction ``persona_composite`` already needs -- reusing its proven
    arithmetic here rather than inventing new numbers).

    weights (a stand-in for a perturbed vector): responsiveness .35,
    criticality .10, governance .15, rigor .10, accessibility .30 (sums to
    1.0); accessibility unmeasured.
    coverage = .35+.10+.15+.10 (unmeasured pillar contributes 0) = 0.70
    composite_raw = (.35*91 + .10*84 + .15*77 + .10*41) / 0.70
                  = 55.90 / 0.70 = 79.857142857...
    """
    from cvi.audit.sensitivity import _weighted_composite_raw, _weighted_coverage
    from cvi.core.models import PillarScore

    pillars = {
        "responsiveness": PillarScore(91.0, 1.0),
        "criticality": PillarScore(84.0, 1.0),
        "governance": PillarScore(77.0, 1.0),
        "rigor": PillarScore(41.0, 1.0),
        "accessibility": PillarScore(None, 0.0),
    }
    weights = {
        "responsiveness": 0.35, "criticality": 0.10, "governance": 0.15,
        "rigor": 0.10, "accessibility": 0.30,
    }
    assert _weighted_coverage(pillars, weights) == pytest.approx(0.70)
    assert _weighted_composite_raw(pillars, weights) == pytest.approx(79.857142857142857)


def test_weighted_composite_raw_is_none_when_every_pillar_unmeasured() -> None:
    from cvi.audit.sensitivity import _weighted_composite_raw
    from cvi.core.models import PillarScore

    pillars = {name: PillarScore(None, 0.0) for name in ("responsiveness", "criticality", "governance", "rigor", "accessibility")}
    weights = {"responsiveness": 0.35, "criticality": 0.10, "governance": 0.15, "rigor": 0.10, "accessibility": 0.30}
    assert _weighted_composite_raw(pillars, weights) is None
