"""Tests for scripts/verify.py cross-source verification logic."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("verify", ROOT / "scripts" / "verify.py")
verify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify)


def bundle(stars=1000, forks=50, score=8.0, conf=None):
    return {"meta": {"stars": stars, "forks": forks, "scorecard_score": score},
            "confidence": conf or {"stars": "verified", "forks": "verified"}}


def test_clean_bundle():
    r = verify.verify_bundle(bundle(), {"stars": 995, "forks": 50,
                                        "scorecard_overall": 8.2})
    assert r["clean"] and r["flags"] == []


def test_stars_cache_lag_ok():
    # deps.dev below GitHub is expected cache staleness on a growing repo
    # (stars are monotonic), so it must NOT gate.
    r = verify.verify_bundle(bundle(stars=1000), {"stars": 700})
    assert r["clean"]


def test_stars_cache_above_live_flags():
    # The impossible direction: a cached count ABOVE the live count means the
    # sources genuinely disagree (wrong repo mapping / corrupt data).
    r = verify.verify_bundle(bundle(stars=1000), {"stars": 1400})
    assert not r["clean"] and "stars" in r["flags"]


def test_scorecard_mismatch_is_advisory_not_gating():
    # deps.dev's scorecard_overall is a cached copy of the same OpenSSF tool the
    # bundle already uses, so a disagreement is cache-refresh drift, not a
    # two-source contradiction. It must be recorded for visibility but must NOT
    # flip clean->False on its own.
    r = verify.verify_bundle(bundle(score=9.0), {"scorecard_overall": 6.0})
    assert r["clean"] and r["flags"] == []
    rec = [c for c in r["checks"] if c["field"] == "scorecard"]
    assert rec and rec[0]["ok"] is False  # recorded as a failing check...
    assert "scorecard" not in r["flags"]  # ...but excluded from the gate


def test_independent_source_mismatch_still_gates():
    # Contrast: a deps.dev fork count ABOVE the live GitHub count is impossible
    # for a monotonic counter, so it still flags even when stars agree.
    r = verify.verify_bundle(bundle(stars=1000, forks=50),
                             {"stars": 1000, "forks": 500, "scorecard_overall": 8.0})
    assert not r["clean"] and "forks" in r["flags"]


def test_missing_required_confidence_flags():
    r = verify.verify_bundle(bundle(conf={"stars": "derived", "forks": "verified"}), {})
    assert not r["clean"] and "required:stars" in r["flags"]


def test_single_source_depsdev_absent_ok():
    r = verify.verify_bundle(bundle(), {})
    assert r["clean"]


def test_license_cross_check():
    b = bundle()
    r = verify.verify_bundle(b, {"license": "MIT"}, gh_meta={"license_spdx": "Apache-2.0"})
    assert not r["clean"] and "license" in r["flags"]
    r2 = verify.verify_bundle(b, {"license": "Apache-2.0"},
                              gh_meta={"license_spdx": "Apache-2.0"})
    assert r2["clean"]
