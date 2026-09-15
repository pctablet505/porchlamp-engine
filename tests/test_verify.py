"""Tests for scripts/verify.py cross-source verification logic.

Provenance is part of verification (packet, `03` §5): a field that claims
``verified`` must name an envelope under ``raw/<collector>/<owner>__<name>.json``
whose hash matches the sha256 recorded in the bundle's own provenance at
derive time (round 2, ) -- and that envelope must belong to THIS
bundle's own key and, where the field's collector is known, that collector
too (round 2, ). ``data_dir`` is an argument of every check here
rather than an ambient global.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Optional

import pytest

from cvi.collect.envelope import envelope_hash, write_envelope

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("verify", ROOT / "scripts" / "verify.py")
verify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify)

OWNER, NAME, ECOSYSTEM = "rust-lang", "serde", "crates"
KEY = f"{OWNER}__{NAME}"
FETCHED_AT = "2026-09-11T19:49:51.804283+00:00"
ENVELOPE_DATA = {"stars": 1000, "forks": 50}
#: The flag `03` §5 names for provenance that cannot be re-checked.
UNVERIFIABLE = "unverifiable_provenance"


def envelope_path(data_dir: Path, collector: str = "github_meta", key: str = KEY) -> Path:
    """Path of the envelope the fixture bundle's provenance points at."""
    return data_dir / "raw" / collector / f"{key}.json"


def envelope_provenance(data_dir: Path) -> dict[str, Any]:
    """stars/forks provenance exactly as ``scripts/derive.py`` records it now:
    the envelope path plus the envelope's own hash (round 2, )."""
    digest = envelope_hash(ENVELOPE_DATA)
    entry = {"source": str(envelope_path(data_dir)), "fetched_at": FETCHED_AT, "sha256": digest}
    return {"stars": dict(entry), "forks": dict(entry)}


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Data dir holding one envelope whose collector recorded its hash."""
    write_envelope(envelope_path(tmp_path), "github:repository", ENVELOPE_DATA, FETCHED_AT)
    return tmp_path


def bundle(prov: Optional[dict] = None, conf: Optional[dict] = None,
           stars=1000, forks=50, score=8.0,
           owner: Any = OWNER, name: Any = NAME, ecosystem: Any = ECOSYSTEM):
    return {"meta": {"owner": owner, "name": name, "ecosystem": ecosystem,
                     "stars": stars, "forks": forks, "scorecard_score": score},
            "confidence": conf or {"stars": "verified", "forks": "verified"},
            "provenance": prov or {}}


def test_clean_bundle(data_dir):
    r = verify.verify_bundle(bundle(envelope_provenance(data_dir)),
                             {"stars": 995, "forks": 50, "scorecard_overall": 8.2},
                             data_dir=data_dir)
    assert r["clean"] and r["flags"] == []


def test_stars_cache_lag_ok(data_dir):
    # deps.dev below GitHub is expected cache staleness on a growing repo
    # (stars are monotonic), so it must NOT gate.
    r = verify.verify_bundle(bundle(envelope_provenance(data_dir), stars=1000),
                             {"stars": 700}, data_dir=data_dir)
    assert r["clean"]


def test_stars_cache_above_live_flags(data_dir):
    # The impossible direction: a cached count ABOVE the live count means the
    # sources genuinely disagree (wrong repo mapping / corrupt data).
    r = verify.verify_bundle(bundle(envelope_provenance(data_dir), stars=1000),
                             {"stars": 1400}, data_dir=data_dir)
    assert not r["clean"] and "stars" in r["flags"]


def test_scorecard_mismatch_is_advisory_not_gating(data_dir):
    # deps.dev's scorecard_overall is a cached copy of the same OpenSSF tool the
    # bundle already uses, so a disagreement is cache-refresh drift, not a
    # two-source contradiction. It must be recorded for visibility but must NOT
    # flip clean->False on its own.
    r = verify.verify_bundle(bundle(envelope_provenance(data_dir), score=9.0),
                             {"scorecard_overall": 6.0}, data_dir=data_dir)
    assert r["clean"] and r["flags"] == []
    rec = [c for c in r["checks"] if c["field"] == "scorecard"]
    assert rec and rec[0]["ok"] is False  # recorded as a failing check...
    assert "scorecard" not in r["flags"]  # ...but excluded from the gate


def test_independent_source_mismatch_still_gates(data_dir):
    # Contrast: a deps.dev fork count ABOVE the live GitHub count is impossible
    # for a monotonic counter, so it still flags even when stars agree.
    r = verify.verify_bundle(bundle(envelope_provenance(data_dir), stars=1000, forks=50),
                             {"stars": 1000, "forks": 500, "scorecard_overall": 8.0},
                             data_dir=data_dir)
    assert not r["clean"] and "forks" in r["flags"]


def test_missing_required_confidence_flags(data_dir):
    r = verify.verify_bundle(bundle(envelope_provenance(data_dir),
                                    conf={"stars": "derived", "forks": "verified"}),
                             {}, data_dir=data_dir)
    assert not r["clean"] and "required:stars" in r["flags"]


def test_single_source_depsdev_absent_ok(data_dir):
    r = verify.verify_bundle(bundle(envelope_provenance(data_dir)), {}, data_dir=data_dir)
    assert r["clean"]


def test_license_cross_check(data_dir):
    b = bundle(envelope_provenance(data_dir))
    r = verify.verify_bundle(b, {"license": "MIT"}, gh_meta={"license_spdx": "Apache-2.0"},
                             data_dir=data_dir)
    assert not r["clean"] and "license" in r["flags"]
    r2 = verify.verify_bundle(b, {"license": "Apache-2.0"},
                              gh_meta={"license_spdx": "Apache-2.0"}, data_dir=data_dir)
    assert r2["clean"]


# --------------------------------------------------------------------------- #
# Unforgeable provenance (`03` §5, R-4): a field that claims `verified` must
# name an envelope path -- belonging to THIS bundle's key and collector --
# whose hash matches the sha256 the provenance itself records.
# --------------------------------------------------------------------------- #


def test_vocabulary_provenance_flags(data_dir):
    # The deleted LLM-curation path labelled invented telemetry
    # "ecosystem-intelligence"; a word is not an envelope path.
    word = {"source": "ecosystem-intelligence", "fetched_at": FETCHED_AT}
    r = verify.verify_bundle(bundle({"stars": dict(word), "forks": dict(word)}),
                             {"stars": 1000, "forks": 50}, data_dir=data_dir)
    assert r["clean"] is False
    assert r["flags"] == ["unverifiable_provenance"]
    offenders = [c for c in r["checks"]
                 if c["field"] == UNVERIFIABLE and not c["ok"]]
    assert len(offenders) == 2
    assert "ecosystem-intelligence" in offenders[0]["detail"]


def test_verified_field_without_provenance_flags(data_dir):
    r = verify.verify_bundle(bundle(), {}, data_dir=data_dir)
    assert r["clean"] is False and r["flags"] == [UNVERIFIABLE]


def test_url_provenance_for_verified_field_flags(data_dir):
    #  provenance is an envelope path
    # only. A bare URL is not a path under raw/, hash or no hash.
    prov = envelope_provenance(data_dir)
    prov["stars"] = {"source": "https://api.github.com/repos/rust-lang/serde",
                     "fetched_at": FETCHED_AT}
    r = verify.verify_bundle(bundle(prov), {}, data_dir=data_dir)
    assert r["clean"] is False and r["flags"] == [UNVERIFIABLE]


def test_url_provenance_with_recorded_hash_still_flags(data_dir):
    # accepted an http(s) URL carrying a recorded sha256 without ever
    # recomputing it (: any 64-hex string passed). deletes
    # that branch entirely -- this guards against it being reintroduced.
    prov = envelope_provenance(data_dir)
    prov["stars"] = {"source": "https://api.github.com/repos/rust-lang/serde",
                     "fetched_at": FETCHED_AT, "sha256": "a" * 64}
    r = verify.verify_bundle(bundle(prov), {}, data_dir=data_dir)
    assert r["clean"] is False and r["flags"] == [UNVERIFIABLE]


def test_missing_envelope_flags(data_dir):
    prov = envelope_provenance(data_dir)
    prov["stars"] = {"source": str(data_dir / "raw" / "github_meta" / "absent.json"),
                     "fetched_at": FETCHED_AT}
    r = verify.verify_bundle(bundle(prov), {}, data_dir=data_dir)
    assert r["clean"] is False and r["flags"] == [UNVERIFIABLE]


def test_provenance_outside_raw_flags(data_dir):
    # A file that exists but is not an envelope: anything outside raw/ can be
    # rewritten without leaving a trace.
    elsewhere = data_dir / "derived" / f"{KEY}.json"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_text(json.dumps(ENVELOPE_DATA), encoding="utf-8")
    prov = envelope_provenance(data_dir)
    prov["stars"] = {"source": str(elsewhere), "fetched_at": FETCHED_AT}
    r = verify.verify_bundle(bundle(prov), {}, data_dir=data_dir)
    assert r["clean"] is False and r["flags"] == [UNVERIFIABLE]


def test_hash_recorded_in_provenance_must_match(data_dir):
    prov = envelope_provenance(data_dir)
    prov["stars"]["sha256"] = "0" * 64
    r = verify.verify_bundle(bundle(prov), {}, data_dir=data_dir)
    assert r["clean"] is False and r["flags"] == [UNVERIFIABLE]
    offender = next(c for c in r["checks"]
                    if c["field"] == UNVERIFIABLE and not c["ok"])
    assert offender["detail"].startswith("field=stars")


def test_provenance_without_recorded_hash_flags(data_dir):
    #  a bundle derived BEFORE this change recorded no
    # hash at all -- round 1's bug treated "nothing to compare" as passing,
    # which verified file existence, not content. It must flag instead, and
    # stay flagged until the bundle is re-derived.
    prov = envelope_provenance(data_dir)
    del prov["stars"]["sha256"]
    r = verify.verify_bundle(bundle(prov), {}, data_dir=data_dir)
    assert r["clean"] is False and r["flags"] == [UNVERIFIABLE]
    offender = next(c for c in r["checks"]
                    if c["field"] == UNVERIFIABLE and not c["ok"])
    assert offender["detail"].startswith("field=stars")
    assert "no sha256" in offender["detail"]


def test_envelope_hash_not_matching_its_data_flags(data_dir):
    # Tamper: the envelope still asserts the hash its collector wrote while its
    # data field has changed underneath it.
    path = envelope_path(data_dir)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["data"]["stars"] = 9999999
    path.write_text(json.dumps(doc), encoding="utf-8")
    r = verify.verify_bundle(bundle(envelope_provenance(data_dir)), {}, data_dir=data_dir)
    assert r["clean"] is False and r["flags"] == [UNVERIFIABLE]


def test_backfilled_envelope_hash_is_verifiable(data_dir):
    # Envelopes written before packet recorded no hash: the reader hashes
    # their data field (the  read-time rule), so the provenance is verifiable.
    path = envelope_path(data_dir)
    doc = json.loads(path.read_text(encoding="utf-8"))
    del doc["sha256"]
    path.write_text(json.dumps(doc), encoding="utf-8")
    r = verify.verify_bundle(bundle(envelope_provenance(data_dir)), {}, data_dir=data_dir)
    assert r["clean"] is True and r["flags"] == []


def test_path_and_hash_form_is_accepted(data_dir):
    # The canonical provenance string of packet: "<path>#<sha256>".
    prov = envelope_provenance(data_dir)
    prov["stars"] = {"source": f"{envelope_path(data_dir)}#{envelope_hash(ENVELOPE_DATA)}",
                     "fetched_at": FETCHED_AT}
    r = verify.verify_bundle(bundle(prov), {}, data_dir=data_dir)
    assert r["clean"] is True and r["flags"] == []


def test_copied_data_dir_resolves_by_raw_tail(tmp_path):
    # A copied sample keeps the absolute paths it was collected with; the tail
    # from raw/ onward resolves inside the copy (the same reason a manifest
    # stores paths relative to its data dir).
    collected_under = Path("/elsewhere/original/data")
    write_envelope(envelope_path(tmp_path), "github:repository", ENVELOPE_DATA, FETCHED_AT)
    prov = envelope_provenance(tmp_path)
    prov["stars"] = {"source": str(collected_under / "raw/github_meta" / f"{KEY}.json"),
                     "fetched_at": FETCHED_AT, "sha256": envelope_hash(ENVELOPE_DATA)}
    r = verify.verify_bundle(bundle(prov), {}, data_dir=tmp_path)
    assert r["clean"] is True and r["flags"] == []


def test_provenance_checked_only_for_verified_fields(data_dir):
    # Over-correction guard: `derived`/`missing` fields make no verification
    # claim, so a URL, a bare word or no source at all must not flag.
    prov = {**envelope_provenance(data_dir),
            "ai_posture": {"source": "https://raw.githubusercontent.com/rust-lang/serde/HEAD/README.md",
                           "fetched_at": None},
            "ecosystem": {"source": "cli", "fetched_at": None},
            "dependents_count": {"source": None, "fetched_at": None}}
    conf = {"stars": "verified", "forks": "verified", "ai_posture": "derived",
            "ecosystem": "derived", "dependents_count": "missing"}
    r = verify.verify_bundle(bundle(prov, conf=conf), {}, data_dir=data_dir)
    assert r["clean"] is True and r["flags"] == []


def test_verify_repo_records_unverifiable_provenance(tmp_path):
    # End-to-end through the promotion gate: the report written into verified/
    # is what scripts/promote.py filters on, so it must say not-clean.
    word = {"source": "ecosystem-intelligence", "fetched_at": FETCHED_AT}
    (tmp_path / "derived").mkdir(parents=True)
    (tmp_path / "derived" / f"{KEY}.json").write_text(
        json.dumps(bundle({"stars": dict(word), "forks": dict(word)})), encoding="utf-8")
    report = verify.verify_repo(OWNER, NAME, tmp_path)
    assert report["clean"] is False
    assert report["flags"] == [UNVERIFIABLE]
    written = json.loads((tmp_path / "verified" / f"{KEY}.json").read_text(encoding="utf-8"))
    assert written["report"]["clean"] is False


# --------------------------------------------------------------------------- #
# Bound provenance (round 2, ): an envelope must belong to THIS
# bundle's own repository key and, where known, its field's own collector.
# --------------------------------------------------------------------------- #


def test_envelope_for_a_different_repository_flags(data_dir):
    # numpy's stars provenance pointing at PythonRobotics' github_meta
    # envelope: same collector, wrong key.
    foreign_data = {"stars": 1, "forks": 1}
    foreign = data_dir / "raw" / "github_meta" / "AtsushiSakai__PythonRobotics.json"
    write_envelope(foreign, "github:repository", foreign_data, FETCHED_AT)
    prov = envelope_provenance(data_dir)
    prov["stars"] = {"source": str(foreign), "fetched_at": FETCHED_AT,
                     "sha256": envelope_hash(foreign_data)}
    r = verify.verify_bundle(bundle(prov), {}, data_dir=data_dir)
    assert r["clean"] is False and r["flags"] == [UNVERIFIABLE]
    offender = next(c for c in r["checks"] if c["field"] == UNVERIFIABLE and not c["ok"])
    assert offender["detail"].startswith("field=stars")
    assert "does not match this bundle's own" in offender["detail"]


def test_envelope_from_wrong_collector_flags(data_dir):
    # numpy's stars provenance pointing at numpy's OWN gfi envelope: same
    # key, wrong collector -- the key check alone would miss this.
    gfi_data = {"count": 3}
    gfi_path = data_dir / "raw" / "gfi" / f"{KEY}.json"
    write_envelope(gfi_path, "gfi:list", gfi_data, FETCHED_AT)
    prov = envelope_provenance(data_dir)
    prov["stars"] = {"source": str(gfi_path), "fetched_at": FETCHED_AT,
                     "sha256": envelope_hash(gfi_data)}
    r = verify.verify_bundle(bundle(prov), {}, data_dir=data_dir)
    assert r["clean"] is False and r["flags"] == [UNVERIFIABLE]
    offender = next(c for c in r["checks"] if c["field"] == UNVERIFIABLE and not c["ok"])
    assert offender["detail"].startswith("field=stars")
    assert "is not one of" in offender["detail"]


def test_downloads_field_keys_on_ecosystem_not_owner(tmp_path):
    # 's one explicit exception: scripts/derive.py's downloads
    # collector keys on <ecosystem>__<name>, not <owner>__<name>.
    write_envelope(envelope_path(tmp_path), "github:repository", ENVELOPE_DATA, FETCHED_AT)
    dl_data = {"monthly": 500}
    dl_path = tmp_path / "raw" / "downloads" / f"{ECOSYSTEM}__{NAME}.json"
    write_envelope(dl_path, "downloads:pkg", dl_data, FETCHED_AT)
    prov = envelope_provenance(tmp_path)
    prov["monthly_downloads"] = {"source": str(dl_path), "fetched_at": FETCHED_AT,
                                 "sha256": envelope_hash(dl_data)}
    conf = {"stars": "verified", "forks": "verified", "monthly_downloads": "verified"}
    r = verify.verify_bundle(bundle(prov, conf=conf), {}, data_dir=tmp_path)
    assert r["clean"] is True and r["flags"] == []

    # Over-correction guard: keying it on OWNER instead (the bug 
    # would reintroduce for this one field) must flag.
    wrong_key_path = tmp_path / "raw" / "downloads" / f"{OWNER}__{NAME}.json"
    write_envelope(wrong_key_path, "downloads:pkg", dl_data, FETCHED_AT)
    prov2 = dict(prov)
    prov2["monthly_downloads"] = {"source": str(wrong_key_path), "fetched_at": FETCHED_AT,
                                  "sha256": envelope_hash(dl_data)}
    r2 = verify.verify_bundle(bundle(prov2, conf=conf), {}, data_dir=tmp_path)
    assert r2["clean"] is False and r2["flags"] == [UNVERIFIABLE]


def test_list_valued_provenance_checked_elementwise(data_dir):
    # : a list-valued source (pr_events/pr_reviews-shaped, e.g.
    # mean_inline_comments) is checked element by element, not accepted on
    # the strength of its first item alone. "mean_inline_comments" carries
    # no FIELD_COLLECTORS entry, so only the key (not the collector) binds.
    field = "mean_inline_comments"
    second_data = {"n": 1}
    second = data_dir / "raw" / "pr_reviews" / f"{KEY}.json"
    write_envelope(second, "reviews:list", second_data, FETCHED_AT)
    good = str(envelope_path(data_dir))
    prov = envelope_provenance(data_dir)
    prov[field] = {
        "source": [good, str(second)],
        "fetched_at": [FETCHED_AT, FETCHED_AT],
        "sha256": [envelope_hash(ENVELOPE_DATA), envelope_hash(second_data)],
    }
    conf = {"stars": "verified", "forks": "verified", field: "verified"}
    r = verify.verify_bundle(bundle(prov, conf=conf), {}, data_dir=data_dir)
    assert r["clean"] is True and r["flags"] == []

    # Swap the SECOND element for a foreign repository's envelope: the first
    # element alone must not carry the whole field clean.
    foreign = data_dir / "raw" / "pr_reviews" / "torvalds__linux.json"
    write_envelope(foreign, "reviews:list", second_data, FETCHED_AT)
    prov[field]["source"] = [good, str(foreign)]
    r2 = verify.verify_bundle(bundle(prov, conf=conf), {}, data_dir=data_dir)
    assert r2["clean"] is False and r2["flags"] == [UNVERIFIABLE]
    offender = next(c for c in r2["checks"] if c["field"] == UNVERIFIABLE and not c["ok"])
    assert offender["detail"].startswith(f"field={field}")
