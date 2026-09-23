"""Acceptance tests: strict FeatureBundle validation.

Fail-before (R-1): on the base commit there is no ``porchlamp.core`` and no
``porchlamp.derive``; the v1 deriver (``scripts/derive.py``) filled missing inputs
with silent defaults (``Bundle.missing(field, default)``), so a bundle with a
missing input key loaded fine and the default value scored. This module
proves the v2 loader rejects it instead.
"""
from __future__ import annotations

import pytest

from porchlamp.core import constants
from porchlamp.core.models import (
    BundleValidationError,
    Cohorts,
    FeatureBundle,
    Measured,
    PRWindow,
    PRWindowEvent,
    RepoIdentity,
    Unmeasured,
)
from porchlamp.derive.bundle import dump_bundle, validate_bundle

_HASH = "ab" * 32
_FETCHED_AT = "2026-09-01T00:00:00+00:00"


def make_measured(**overrides) -> Measured:
    fields = dict(
        value=4.0,
        n=0,
        window_start=None,
        window_end=None,
        method="primary",
        source="raw/pr_events/org__alpha.json",
        source_hash=_HASH,
        fetched_at=_FETCHED_AT,
    )
    fields.update(overrides)
    return Measured(**fields)


def make_inputs(unmeasured_ids=()) -> dict:
    return {
        input_id: (
            Unmeasured(reason="collector_failure", detail="no v1 collector")
            if input_id in unmeasured_ids
            else make_measured()
        )
        for input_id in constants.PARAM_INPUT_IDS
    }


def make_repo() -> RepoIdentity:
    return RepoIdentity(
        owner="org",
        name="alpha",
        ecosystem="pypi",
        archived=False,
        mirror_url=None,
        fork=False,
        license_spdx="MIT",
        default_branch="main",
    )


def make_window() -> PRWindow:
    return PRWindow(
        start="2026-03-04T00:00:00+00:00",
        end="2026-09-01T00:00:00+00:00",
        truncated=False,
        events=(
            PRWindowEvent(
                pr_number=1,
                created_at="2026-03-10T00:00:00+00:00",
                closed_at=None,
                merged_at=None,
                author_login="alice",
                author_association="NONE",
                is_external=True,
                first_human_review_at=None,
            ),
        ),
    )


def make_doc(**overrides) -> dict:
    doc = {
        "schema_version": 2,
        "repo": {
            "owner": "org",
            "name": "alpha",
            "ecosystem": "pypi",
            "archived": False,
            "mirror_url": None,
            "fork": False,
            "license_spdx": "MIT",
            "default_branch": "main",
        },
        "inputs": {
            input_id: {
                "kind": "measured",
                "value": 4.0,
                "n": 0,
                "window_start": None,
                "window_end": None,
                "method": "primary",
                "source": "raw/pr_events/org__alpha.json",
                "source_hash": _HASH,
                "fetched_at": _FETCHED_AT,
            }
            for input_id in constants.PARAM_INPUT_IDS
        },
        "pr_window": {
            "start": "2026-03-04T00:00:00+00:00",
            "end": "2026-09-01T00:00:00+00:00",
            "truncated": False,
            "events": [
                {
                    "pr_number": 1,
                    "created_at": "2026-03-10T00:00:00+00:00",
                    "closed_at": None,
                    "merged_at": None,
                    "author_login": "alice",
                    "author_association": "NONE",
                    "is_external": True,
                    "first_human_review_at": None,
                }
            ],
        },
        "cohorts": {"decision_pr_numbers": [], "newcomer_authors": []},
        "provenance_hash": _HASH,
    }
    doc.update(overrides)
    return doc


@pytest.mark.parametrize("missing_id", list(constants.PARAM_INPUT_IDS))
def test_rejects_bundle_with_missing_input_key(missing_id: str) -> None:
    """The acceptance fail-before case: v1 silently defaulted, v2 rejects."""
    doc = make_doc()
    del doc["inputs"][missing_id]
    with pytest.raises(BundleValidationError) as excinfo:
        validate_bundle(doc)
    assert missing_id in str(excinfo.value)


def test_rejects_bundle_with_extra_input_key() -> None:
    doc = make_doc()
    doc["inputs"]["zz_invented_input"] = {"kind": "unmeasured", "reason": "third_party_gap"}
    with pytest.raises(BundleValidationError) as excinfo:
        validate_bundle(doc)
    assert "zz_invented_input" in str(excinfo.value)


def test_rejects_unmeasured_with_unknown_reason() -> None:
    doc = make_doc()
    doc["inputs"]["ttfhr_hours"] = {"kind": "unmeasured", "reason": "nope", "detail": ""}
    with pytest.raises(BundleValidationError) as excinfo:
        validate_bundle(doc)
    assert "nope" in str(excinfo.value)


def test_rejects_measured_with_bad_source_hash() -> None:
    doc = make_doc()
    doc["inputs"]["ttfhr_hours"]["source_hash"] = "not-a-hash"
    with pytest.raises(BundleValidationError):
        validate_bundle(doc)


@pytest.mark.parametrize(
    "bad_source",
    [
        "/home/pctablet505/Projects/cvi-scratch/sample-200/raw/gfi/torvalds__linux.json",
        "/raw/gfi/torvalds__linux.json",
        "C:\\data\\raw\\gfi\\torvalds__linux.json",
        "raw/gfi/../../etc/passwd",
        "",
    ],
)
def test_rejects_measured_with_absolute_or_unsafe_source(bad_source: str) -> None:
    """Provenance is an envelope path plus its hash, and that path
    must be relative to the data root (docs/v2/00-README.md non-negotiables)
    -- an absolute path of the machine that derived a bundle, a Windows
    drive-letter path, or a ``..`` escape must never enter a bundle
    silently. Fail-before: this is the exact shape the real fixture store
    served (an absolute ``sample-200`` path) before the fix."""
    doc = make_doc()
    doc["inputs"]["ttfhr_hours"]["source"] = bad_source
    with pytest.raises(BundleValidationError) as excinfo:
        validate_bundle(doc)
    assert "relative to the data root" in str(excinfo.value) or "non-empty envelope path" in str(excinfo.value)


def test_accepts_measured_with_relative_source() -> None:
    doc = make_doc()
    doc["inputs"]["ttfhr_hours"]["source"] = "raw/pr_window/org__alpha.json"
    bundle = validate_bundle(doc)
    assert bundle.inputs["ttfhr_hours"].source == "raw/pr_window/org__alpha.json"


def test_rejects_measured_with_negative_n() -> None:
    doc = make_doc()
    doc["inputs"]["ttfhr_hours"]["n"] = -1
    with pytest.raises(BundleValidationError):
        validate_bundle(doc)


def test_rejects_measured_with_bool_value() -> None:
    doc = make_doc()
    doc["inputs"]["ttfhr_hours"]["value"] = True
    with pytest.raises(BundleValidationError):
        validate_bundle(doc)


@pytest.mark.parametrize("method", ["fast", "proxy:", "Primary"])
def test_rejects_measured_with_bad_method(method: str) -> None:
    doc = make_doc()
    doc["inputs"]["ttfhr_hours"]["method"] = method
    with pytest.raises(BundleValidationError):
        validate_bundle(doc)


def test_accepts_measured_with_proxy_method() -> None:
    doc = make_doc()
    doc["inputs"]["pipeline_defenses"]["method"] = "proxy:review_ci_blend"
    bundle = validate_bundle(doc)
    assert bundle.inputs["pipeline_defenses"].method == "proxy:review_ci_blend"


def test_rejects_wrong_schema_version() -> None:
    with pytest.raises(BundleValidationError):
        validate_bundle(make_doc(schema_version=1))


def test_rejects_unexpected_top_level_key() -> None:
    doc = make_doc()
    doc["confidence"] = {}
    with pytest.raises(BundleValidationError) as excinfo:
        validate_bundle(doc)
    assert "confidence" in str(excinfo.value)


def test_valid_document_round_trips() -> None:
    """Over-correction guard: a fully valid v2 document loads, dumps and
    reloads to the same value."""
    bundle = validate_bundle(make_doc())
    assert isinstance(bundle, FeatureBundle)
    assert bundle.schema_version == 2
    assert bundle.repo == make_repo()
    assert bundle.inputs["ttfhr_hours"] == make_measured()
    assert bundle.inputs == make_inputs()
    assert bundle.pr_window == make_window()
    assert bundle.cohorts == Cohorts(decision_pr_numbers=(), newcomer_authors=())
    assert bundle.provenance_hash == _HASH

    dumped = dump_bundle(bundle)
    assert dumped["schema_version"] == 2
    assert dumped["inputs"]["ttfhr_hours"]["value"] == 4.0
    assert dumped["inputs"]["ttfhr_hours"]["kind"] == "measured"
    reloaded = validate_bundle(dumped)
    assert reloaded == bundle
    assert set(dumped["inputs"]) == set(constants.PARAM_INPUT_IDS)


def test_validate_method_on_valid_bundle_passes() -> None:
    bundle = validate_bundle(make_doc())
    bundle.validate()  # must not raise
