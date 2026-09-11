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


def test_stars_mismatch_flags():
    r = verify.verify_bundle(bundle(stars=1000), {"stars": 700})
    assert not r["clean"] and "stars" in r["flags"]


def test_scorecard_mismatch_flags():
    r = verify.verify_bundle(bundle(score=9.0), {"scorecard_overall": 6.0})
    assert not r["clean"] and "scorecard" in r["flags"]


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
