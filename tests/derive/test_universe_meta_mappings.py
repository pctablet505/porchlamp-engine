"""W1-10 acceptance tests: ``cvi.derive.bundle``'s universe-entry
star-velocity pass-through (``UNIVERSE_META_MAPPINGS`` /
``resolve_universe_meta_facts``), and the full chain into
``cvi.verify.detectors``'s ``star_burst`` detector through the real
``run_detectors``.

``star_velocity_flag``/``stars_audited`` are not part of
``FeatureBundle.inputs`` (D-IN-5: stars never enter the composite;
``INPUT_MAPPINGS``'s own assert closes ``inputs`` at exactly the 18 v2
parameter ids) -- these tests exercise the separate pass-through table
directly, using the same v1-document fixture helpers
``tests/derive/test_input_mappings.py`` uses for the 18 real rows.
"""
from __future__ import annotations

from pathlib import Path

from _fixtures import base_document, expected_hash, prov, write_envelope

from cvi.core.models import BundleValidationError, GateResult, Measured, ParamScore, RepoIdentity, ScoreRecord, Unmeasured
from cvi.derive.bundle import UNIVERSE_META_MAPPINGS, resolve_universe_meta_facts
from cvi.verify.detectors import DetectorInputs, run_detectors

import pytest


def test_universe_meta_mappings_has_exactly_two_rows():
    assert set(UNIVERSE_META_MAPPINGS) == {"star_velocity_flag", "stars_audited"}


# --------------------------------------------------------------------------- #
# star_velocity_flag                                                          #
# --------------------------------------------------------------------------- #


def test_star_velocity_flag_measured_true(tmp_path: Path):
    data = {"measured": True, "star_velocity_flag": True, "stars_audited": 150,
            "star_velocity": {"recent_stars": 534, "window_days": 90}, "provenance": {}}
    write_envelope(tmp_path / "raw", "universe_entry", "acme__widget", data)
    doc = base_document(
        meta={"star_velocity_flag": True, "stars_audited": 150},
        provenance={"star_velocity_flag": prov("universe_entry"), "stars_audited": prov("universe_entry")},
        confidence={"star_velocity_flag": "verified", "stars_audited": "verified"},
    )
    result = resolve_universe_meta_facts(doc, raw_root=tmp_path / "raw")
    assert result["star_velocity_flag"] == Measured(
        value=1, n=0, window_start=None, window_end=None, method="primary",
        source="raw/universe_entry/acme__widget.json",
        source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
    )
    assert bool(result["star_velocity_flag"].value) is True


def test_star_velocity_flag_measured_false(tmp_path: Path):
    data = {"measured": True, "star_velocity_flag": False, "stars_audited": 42,
            "star_velocity": {"recent_stars": 42, "window_days": 90}, "provenance": {}}
    write_envelope(tmp_path / "raw", "universe_entry", "acme__widget", data)
    doc = base_document(
        meta={"star_velocity_flag": False, "stars_audited": 42},
        provenance={"star_velocity_flag": prov("universe_entry")},
        confidence={"star_velocity_flag": "verified"},
    )
    result = resolve_universe_meta_facts(doc, raw_root=tmp_path / "raw")
    assert result["star_velocity_flag"] == Measured(
        value=0, n=0, window_start=None, window_end=None, method="primary",
        source="raw/universe_entry/acme__widget.json",
        source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
    )
    assert bool(result["star_velocity_flag"].value) is False


def test_star_velocity_flag_unmeasured_when_confidence_missing(tmp_path: Path):
    """A failed star-history fetch (confidence 'missing') -> Unmeasured,
    never a silent False -- the value in ``meta`` (whatever it is) is
    never read once confidence says the check never ran."""
    doc = base_document(
        meta={"star_velocity_flag": False},  # placeholder derive.py writes; must be ignored
        confidence={"star_velocity_flag": "missing"},
    )
    result = resolve_universe_meta_facts(doc)
    assert result["star_velocity_flag"] == Unmeasured(
        reason="collector_failure",
        detail="universe entry never audited star velocity for this repo "
               "(star-history fetch failed, cvi.collect.universe)",
    )


def test_star_velocity_flag_absent_from_document_is_unmeasured():
    """No confidence entry at all (an old document predating this field,
    e.g. the snapshot_50 fixture) falls to the generic 'not recognized'
    Unmeasured path -- never Measured, never a crash."""
    doc = base_document()
    result = resolve_universe_meta_facts(doc)
    assert isinstance(result["star_velocity_flag"], Unmeasured)
    assert result["star_velocity_flag"].reason == "collector_failure"


def test_star_velocity_flag_non_bool_value_with_verified_confidence_is_unmeasured(tmp_path: Path):
    """A malformed document (confidence says verified but the value is not
    actually a bool) never becomes a fabricated Measured."""
    doc = base_document(
        meta={"star_velocity_flag": "yes"},
        provenance={"star_velocity_flag": prov("universe_entry")},
        confidence={"star_velocity_flag": "verified"},
    )
    result = resolve_universe_meta_facts(doc, raw_root=tmp_path / "raw")
    assert isinstance(result["star_velocity_flag"], Unmeasured)


# --------------------------------------------------------------------------- #
# stars_audited                                                               #
# --------------------------------------------------------------------------- #


def test_stars_audited_measured(tmp_path: Path):
    data = {"measured": True, "star_velocity_flag": False, "stars_audited": 271,
            "star_velocity": {"recent_stars": 271, "window_days": 90}, "provenance": {}}
    write_envelope(tmp_path / "raw", "universe_entry", "acme__widget", data)
    doc = base_document(
        meta={"stars_audited": 271},
        provenance={"stars_audited": prov("universe_entry")},
        confidence={"stars_audited": "verified"},
    )
    result = resolve_universe_meta_facts(doc, raw_root=tmp_path / "raw")
    assert result["stars_audited"] == Measured(
        value=271, n=0, window_start=None, window_end=None, method="primary",
        source="raw/universe_entry/acme__widget.json",
        source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
    )


def test_stars_audited_unmeasured_when_confidence_missing():
    doc = base_document(meta={"stars_audited": 0}, confidence={"stars_audited": "missing"})
    result = resolve_universe_meta_facts(doc)
    assert result["stars_audited"] == Unmeasured(
        reason="collector_failure",
        detail="universe entry never audited star velocity for this repo "
               "(star-history fetch failed, cvi.collect.universe)",
    )
    # Never a silent 0 masquerading as a real audited count:
    assert result["stars_audited"] != Measured(
        value=0, n=0, window_start=None, window_end=None, method="primary",
        source="x", source_hash="a" * 64, fetched_at="2026-09-01T00:00:00+00:00",
    )


def test_resolve_universe_meta_facts_rejects_a_malformed_document():
    with pytest.raises(BundleValidationError):
        resolve_universe_meta_facts({"repo": "acme/widget"})  # no meta/provenance/confidence


# --------------------------------------------------------------------------- #
# Full chain: universe entry -> bundle pass-through -> DetectorInputs ->     #
# the real run_detectors (star_burst, 03 section 6).                         #
# --------------------------------------------------------------------------- #

_HASH64 = "a" * 64
_REPO = RepoIdentity(owner="acme", name="widget", ecosystem="pypi", archived=False,
                      mirror_url=None, fork=False, license_spdx="MIT", default_branch="main")


def _record() -> ScoreRecord:
    return ScoreRecord(
        repo=_REPO, snapshot_id="s0", benchmark_version="2.0.0-dev", constants_hash=_HASH64,
        raw_hash=_HASH64, gate=GateResult(eligible=True, reason=None, routed=None, undecided=()),
        params={"ttfhr_hours": ParamScore(
            value=50.0, score=50.0, measured=True, reason=None, n=20, window_start=None,
            window_end=None, method="primary", source="test", fetched_at="2026-09-01T00:00:00+00:00",
        )},
        pillars={}, composite=None, coverage=0.0, tier=None, leverage=None, friction=None,
        quadrant=None, persona_gates={}, persona_composites={}, tie_key=(), tie_group=None,
        rank=None, suspicion=[],
    )


def _detector_inputs_from(document: dict, raw_root=None) -> DetectorInputs:
    """The wiring a future caller (W4-4) does: resolve the pass-through and
    thread its Measured/Unmeasured flag into DetectorInputs -- exactly the
    field name detect_star_burst reads."""
    resolved = resolve_universe_meta_facts(document, raw_root=raw_root)
    flag_value = resolved["star_velocity_flag"]
    penalty_applied = bool(flag_value.value) if isinstance(flag_value, Measured) else None
    return DetectorInputs(star_lockstep_penalty_applied=penalty_applied)


def test_flagged_fixture_fires_star_burst_through_real_run_detectors(tmp_path: Path):
    data = {"measured": True, "star_velocity_flag": True, "stars_audited": 100,
            "star_velocity": {"recent_stars": 534, "window_days": 90}, "provenance": {}}
    write_envelope(tmp_path / "raw", "universe_entry", "acme__widget", data)
    doc = base_document(
        meta={"star_velocity_flag": True, "stars_audited": 100},
        provenance={"star_velocity_flag": prov("universe_entry"), "stars_audited": prov("universe_entry")},
        confidence={"star_velocity_flag": "verified", "stars_audited": "verified"},
    )
    inputs = _detector_inputs_from(doc, tmp_path / "raw")
    result = run_detectors(_record(), None, inputs)
    assert "star_burst" in result


def test_normal_repo_does_not_fire_star_burst_through_real_run_detectors(tmp_path: Path):
    data = {"measured": True, "star_velocity_flag": False, "stars_audited": 100,
            "star_velocity": {"recent_stars": 100, "window_days": 90}, "provenance": {}}
    write_envelope(tmp_path / "raw", "universe_entry", "acme__widget", data)
    doc = base_document(
        meta={"star_velocity_flag": False},
        provenance={"star_velocity_flag": prov("universe_entry")},
        confidence={"star_velocity_flag": "verified"},
    )
    inputs = _detector_inputs_from(doc, tmp_path / "raw")
    result = run_detectors(_record(), None, inputs)
    assert "star_burst" not in result


def test_unmeasured_never_fires_star_burst_through_real_run_detectors():
    """Unmeasured-never-fires (cvi.verify.detectors' own rule): a failed
    star-history fetch must not manufacture a False->never-fires OR a
    True->false-accusation; it must simply not fire, same as 'no evidence'."""
    doc = base_document(confidence={"star_velocity_flag": "missing"})
    inputs = _detector_inputs_from(doc)
    assert inputs.star_lockstep_penalty_applied is None
    result = run_detectors(_record(), None, inputs)
    assert "star_burst" not in result
