""" oracle tests: one test per ``porchlamp.derive.bundle.INPUT_MAPPINGS`` row.

Each test builds a hand-controlled v1 derived-file document (see
``_fixtures.py``), loads it through the public ``load_bundle`` entry point,
and asserts the exact ``Measured``/``Unmeasured`` value produced for that one
v2 input id -- every field pinned, nothing left to shape-only assertions.

Sixteen rows have a real resolver with both a positive (Measured) and at
least one negative (Unmeasured) path: ``ttfhr_hours``, ``dr90_decision_rate``
(;  adds the D-IN-3 per-author cap and eligibility-gate join),
``mr_first_rate``, ``newcomer_conversion``, ``rr90_return_rate`` (,
read-through of the ``author_history``-derived D-IN-3-adjusted cohort
rates), ``ttm_p90_hours``, ``dependents_deduped``, ``openssf_criticality``
(, ), ``downloads_monthly``, ``legal_ip``, ``elephant_factor``,
``promotion_ladder``, ``pipeline_defenses`` (, ), ``ai_posture``,
``substantive_review_rate``, ``newcomer_issue_supply``. ``gr14_ghost_rate``
is covered in ``test_ghost_rate.py``. One row has no landed producer yet
and is a constant ``Unmeasured(collector_failure, ...)`` regardless of
document content: ``dx``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from _fixtures import (
    author_history_data,
    base_document,
    expected_hash,
    load,
    pr_window_data,
    prov,
    write_envelope,
)

from porchlamp.core import constants
from porchlamp.core.models import BundleValidationError, Measured, Unmeasured, feature_provenance_hash
from porchlamp.core.params import dr90
from porchlamp.derive.bundle import INPUT_MAPPINGS

#: Copied verbatim from porchlamp/derive/bundle.py's ``_PR_WINDOW_UNRESOLVABLE_DETAIL``
#: -- pinned here as a literal so a change to the message is a deliberate,
#: reviewed edit to this test, not a silent drift.
PR_WINDOW_UNRESOLVABLE_DETAIL = (
    "pr_window envelope not resolvable (W1-3 hook did not run for this repo, "
    "or raw_root cannot resolve the recorded source to a file)"
)


def test_input_mappings_cover_every_v2_input_id() -> None:
    assert set(INPUT_MAPPINGS) == set(constants.PARAM_INPUT_IDS)
    assert len(INPUT_MAPPINGS) == 18


# --------------------------------------------------------------------------- #
# 1.1 ttfhr_hours                                                             #
# --------------------------------------------------------------------------- #

def test_ttfhr_hours(tmp_path: Path) -> None:
    data = pr_window_data(window_anchor="2026-09-01T00:00:00+00:00", window_days=180)
    write_envelope(tmp_path / "raw", "pr_window", "acme__widget", data, "2026-09-01T00:05:00+00:00")
    doc = base_document(
        meta={
            "pr_window_ttfhr_median_hours": 6.5,
            "pr_window_external_measured_latency_count": 12,
        },
        provenance={"pr_window_count": prov("pr_window")},
        confidence={"pr_window_count": "verified"},
    )
    bundle = load(tmp_path, doc)
    value = bundle.inputs["ttfhr_hours"]
    assert value == Measured(
        value=6.5, n=12, window_start="2026-03-05T00:00:00+00:00",
        window_end="2026-09-01T00:00:00+00:00", method="primary",
        source="raw/pr_window/acme__widget.json",
        source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
    )

    # Negative 1: n below the min-n floor (02 section 4: min n 10).
    low_n = base_document(
        meta={"pr_window_ttfhr_median_hours": 6.5, "pr_window_external_measured_latency_count": 5},
        provenance={"pr_window_count": prov("pr_window")},
        confidence={"pr_window_count": "verified"},
    )
    low_n_bundle = load(tmp_path, low_n, key="acme__low-n")
    assert low_n_bundle.inputs["ttfhr_hours"] == Unmeasured(
        reason="insufficient_n", detail="5 external PRs with a measured TTFHR, need >= 10",
    )

    # Boundary: n == 10 (the min-n floor itself) is Measured; n == 9 is not
    # (kills an off-by-one "n <= 10" mutant of the "n < 10" floor check).
    write_envelope(tmp_path / "raw", "pr_window", "acme__boundary-hit", data, "2026-09-01T00:05:00+00:00")
    at_floor = base_document(
        meta={"pr_window_ttfhr_median_hours": 6.5, "pr_window_external_measured_latency_count": 10},
        provenance={"pr_window_count": prov("pr_window", key="acme__boundary-hit")},
    )
    at_floor_bundle = load(tmp_path, at_floor, key="acme__boundary-hit")
    assert isinstance(at_floor_bundle.inputs["ttfhr_hours"], Measured)
    assert at_floor_bundle.inputs["ttfhr_hours"].n == 10

    write_envelope(tmp_path / "raw", "pr_window", "acme__boundary-miss", data, "2026-09-01T00:05:00+00:00")
    below_floor = base_document(
        meta={"pr_window_ttfhr_median_hours": 6.5, "pr_window_external_measured_latency_count": 9},
        provenance={"pr_window_count": prov("pr_window", key="acme__boundary-miss")},
    )
    below_floor_bundle = load(tmp_path, below_floor, key="acme__boundary-miss")
    assert below_floor_bundle.inputs["ttfhr_hours"] == Unmeasured(
        reason="insufficient_n", detail="9 external PRs with a measured TTFHR, need >= 10",
    )

    # Negative 2: no pr_window envelope at all ( hook never ran here).
    no_envelope = base_document()
    no_envelope_bundle = load(tmp_path, no_envelope, key="acme__no-window")
    assert no_envelope_bundle.inputs["ttfhr_hours"] == Unmeasured(
        reason="collector_failure", detail=PR_WINDOW_UNRESOLVABLE_DETAIL,
    )


# --------------------------------------------------------------------------- #
# 1.5 ttm_p90_hours                                                           #
# --------------------------------------------------------------------------- #

_TTM_PRS = [
    {"number": 1, "createdAt": "2026-08-01T00:00:00+00:00", "mergedAt": "2026-08-01T10:00:00+00:00"},
    {"number": 2, "createdAt": "2026-08-02T00:00:00+00:00", "mergedAt": "2026-08-02T20:00:00+00:00"},
    {"number": 3, "createdAt": "2026-08-03T00:00:00+00:00", "mergedAt": "2026-08-04T06:00:00+00:00"},
    {"number": 4, "createdAt": "2026-08-04T00:00:00+00:00", "mergedAt": "2026-08-05T16:00:00+00:00"},
    {"number": 5, "createdAt": "2026-08-05T00:00:00+00:00", "mergedAt": "2026-08-07T02:00:00+00:00"},
]
_TTM_DERIVED = [{"number": n, "is_external": True, "merged": True} for n in (1, 2, 3, 4, 5)]


def test_ttm_p90_hours(tmp_path: Path) -> None:
    # Latencies 10/20/30/40/50h; nearest-rank p90 of 5 values (ceil(0.9*5)=5th
    # smallest, 1-indexed) is the max: 50.0.
    data = pr_window_data(prs=_TTM_PRS, derived=_TTM_DERIVED)
    write_envelope(tmp_path / "raw", "pr_window", "acme__widget", data, "2026-09-01T00:05:00+00:00")
    doc = base_document(provenance={"pr_window_count": prov("pr_window")})
    bundle = load(tmp_path, doc)
    assert bundle.inputs["ttm_p90_hours"] == Measured(
        value=50.0, n=5, window_start="2026-03-05T00:00:00+00:00",
        window_end="2026-09-01T00:00:00+00:00", method="primary",
        source="raw/pr_window/acme__widget.json",
        source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
    )

    # Negative 1: only 4 merged-external latencies, below min n 5.
    low_data = pr_window_data(prs=_TTM_PRS[:4], derived=_TTM_DERIVED[:4])
    write_envelope(tmp_path / "raw", "pr_window", "acme__low-n", low_data, "2026-09-01T00:05:00+00:00")
    low_doc = base_document(provenance={"pr_window_count": prov("pr_window", key="acme__low-n")})
    low_bundle = load(tmp_path, low_doc, key="acme__low-n")
    assert low_bundle.inputs["ttm_p90_hours"] == Unmeasured(
        reason="insufficient_n", detail="4 merged external PRs with recorded latency, need >= 5",
    )

    # Negative 2: no pr_window envelope at all.
    no_envelope_bundle = load(tmp_path, base_document(), key="acme__no-window")
    assert no_envelope_bundle.inputs["ttm_p90_hours"] == Unmeasured(
        reason="collector_failure", detail=PR_WINDOW_UNRESOLVABLE_DETAIL,
    )


_TTM_MIXED_PRS = [
    # 201: internal (is_external False), merged, a very long latency -- must
    # be excluded by the is_external filter, not counted toward n or p90.
    {"number": 201, "createdAt": "2026-07-01T00:00:00+00:00", "mergedAt": "2026-08-10T00:00:00+00:00"},
    # 202: external, derived.merged False, but its raw record still carries
    # a parseable mergedAt (isolates the "merged" flag guard from the
    # separate "raw mergedAt is null" guard the resolver also has -- a null
    # mergedAt would be excluded either way and could not kill a mutant that
    # only drops the "merged" flag check). Its ~524h latency would corrupt
    # the p90 below if wrongly counted.
    {"number": 202, "createdAt": "2026-08-06T00:00:00+00:00", "mergedAt": "2026-08-27T20:00:00+00:00"},
    # 203: external, merged, but mergedAt < createdAt (clock skew / bad
    # data) -- excluded by the merged-before-created guard, not the two
    # flags; included here so the fixture exercises all three exclusions
    # at once.
    {"number": 203, "createdAt": "2026-08-10T00:00:00+00:00", "mergedAt": "2026-08-09T00:00:00+00:00"},
    # 204-208: external, merged, valid latencies 6/12/18/24/30h -- the only
    # PRs that should contribute to n and the p90.
    {"number": 204, "createdAt": "2026-08-01T00:00:00+00:00", "mergedAt": "2026-08-01T06:00:00+00:00"},
    {"number": 205, "createdAt": "2026-08-02T00:00:00+00:00", "mergedAt": "2026-08-02T12:00:00+00:00"},
    {"number": 206, "createdAt": "2026-08-03T00:00:00+00:00", "mergedAt": "2026-08-03T18:00:00+00:00"},
    {"number": 207, "createdAt": "2026-08-04T00:00:00+00:00", "mergedAt": "2026-08-05T00:00:00+00:00"},
    {"number": 208, "createdAt": "2026-08-05T00:00:00+00:00", "mergedAt": "2026-08-06T06:00:00+00:00"},
]
_TTM_MIXED_DERIVED = [
    {"number": 201, "is_external": False, "merged": True},
    {"number": 202, "is_external": True, "merged": False},
    {"number": 203, "is_external": True, "merged": True},
    {"number": 204, "is_external": True, "merged": True},
    {"number": 205, "is_external": True, "merged": True},
    {"number": 206, "is_external": True, "merged": True},
    {"number": 207, "is_external": True, "merged": True},
    {"number": 208, "is_external": True, "merged": True},
]


def test_ttm_p90_hours_excludes_internal_unmerged_and_backwards_time_prs(tmp_path: Path) -> None:
    """: 1.5 TTM_p90 must count only PRs that are external AND merged
    AND have mergedAt >= createdAt. The fixture carries one PR failing each
    of the three guards (201 internal, 202 unmerged, 203 backwards-time)
    alongside 5 clean external-merged PRs (204-208, latencies 6/12/18/24/30h)
    -- proving n and the p90 come from the 5 clean PRs alone, not 8."""
    data = pr_window_data(prs=_TTM_MIXED_PRS, derived=_TTM_MIXED_DERIVED)
    write_envelope(tmp_path / "raw", "pr_window", "acme__mixed-ttm", data, "2026-09-01T00:05:00+00:00")
    doc = base_document(provenance={"pr_window_count": prov("pr_window", key="acme__mixed-ttm")})
    bundle = load(tmp_path, doc, key="acme__mixed-ttm")
    # Nearest-rank p90 of [6, 12, 18, 24, 30] (ceil(0.9*5)=5th smallest) is
    # the max, 30.0 -- the same value the excluded PR 201's ~960h latency
    # would grossly distort if it were wrongly counted.
    assert bundle.inputs["ttm_p90_hours"] == Measured(
        value=30.0, n=5, window_start="2026-03-05T00:00:00+00:00",
        window_end="2026-09-01T00:00:00+00:00", method="primary",
        source="raw/pr_window/acme__mixed-ttm.json",
        source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
    )


# --------------------------------------------------------------------------- #
# 4.3 substantive_review_rate                                                 #
# --------------------------------------------------------------------------- #

def test_substantive_review_rate(tmp_path: Path) -> None:
    decided_external = [{"is_external": True, "decided": True} for _ in range(10)]
    data = pr_window_data(derived=decided_external)
    write_envelope(tmp_path / "raw", "pr_window", "acme__widget", data, "2026-09-01T00:05:00+00:00")
    doc = base_document(
        meta={"pr_window_substantive_review_rate": 0.4},
        provenance={"pr_window_count": prov("pr_window")},
    )
    bundle = load(tmp_path, doc)
    assert bundle.inputs["substantive_review_rate"] == Measured(
        value=0.4, n=10, window_start="2026-03-05T00:00:00+00:00",
        window_end="2026-09-01T00:00:00+00:00", method="primary",
        source="raw/pr_window/acme__widget.json",
        source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
    )

    # Negative 1: only 9 decided-external PRs, below min n 10.
    low_data = pr_window_data(derived=decided_external[:9])
    write_envelope(tmp_path / "raw", "pr_window", "acme__low-n", low_data, "2026-09-01T00:05:00+00:00")
    low_doc = base_document(
        meta={"pr_window_substantive_review_rate": 0.4},
        provenance={"pr_window_count": prov("pr_window", key="acme__low-n")},
    )
    low_bundle = load(tmp_path, low_doc, key="acme__low-n")
    assert low_bundle.inputs["substantive_review_rate"] == Unmeasured(
        reason="insufficient_n", detail="9 decided external PRs, need >= 10",
    )

    # Negative 2: no pr_window envelope at all.
    no_envelope_bundle = load(tmp_path, base_document(), key="acme__no-window")
    assert no_envelope_bundle.inputs["substantive_review_rate"] == Unmeasured(
        reason="collector_failure", detail=PR_WINDOW_UNRESOLVABLE_DETAIL,
    )


def test_substantive_review_rate_excludes_internal_reviews_from_n(tmp_path: Path) -> None:
    """,  (Opus review mutant M12): ``n`` counts decided PRs that are
    BOTH external AND decided, not decided alone. 12 internal (is_external
    False) decided PRs plus 4 external decided PRs is 16 decided PRs total
    but only 4 decided-EXTERNAL PRs -- below min n 10, so Unmeasured
    (insufficient_n). Dropping the ``is_external`` half of the filter (the
    review's mutant) would count all 16 and wrongly yield
    Measured(value=0.5, n=12) on a related 6/6 fixture; this one pins the
    correct n so that mutant cannot survive."""
    internal_decided = [{"is_external": False, "decided": True} for _ in range(12)]
    external_decided = [{"is_external": True, "decided": True} for _ in range(4)]
    data = pr_window_data(derived=internal_decided + external_decided)
    write_envelope(tmp_path / "raw", "pr_window", "acme__mixed", data, "2026-09-01T00:05:00+00:00")
    doc = base_document(
        meta={"pr_window_substantive_review_rate": 0.5},
        provenance={"pr_window_count": prov("pr_window", key="acme__mixed")},
    )
    bundle = load(tmp_path, doc, key="acme__mixed")
    assert bundle.inputs["substantive_review_rate"] == Unmeasured(
        reason="insufficient_n", detail="4 decided external PRs, need >= 10",
    )


# --------------------------------------------------------------------------- #
# 2.1 dependents_deduped                                                      #
# --------------------------------------------------------------------------- #

def test_dependents_deduped(tmp_path: Path) -> None:
    data = {"deduped_count": 42, "root_orgs": 7}
    write_envelope(tmp_path / "raw", "dependents", "acme__widget", data, "2026-09-01T00:05:00+00:00")
    doc = base_document(
        meta={"dependents_count": 42},
        provenance={"dependents_count": prov("dependents")},
        confidence={"dependents_count": "derived"},
    )
    bundle = load(tmp_path, doc)
    assert bundle.inputs["dependents_deduped"] == Measured(
        value=42, n=0, window_start=None, window_end=None, method="primary",
        source="raw/dependents/acme__widget.json",
        source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
    )

    # Negative 1: confidence "verified" is the raw (non-deduped) deps.dev
    # usedByCount under the same v1 key, not v2 2.1's dependents_deduped.
    raw_doc = base_document(
        meta={"dependents_count": 999},
        provenance={"dependents_count": prov("depsdev")},
        confidence={"dependents_count": "verified"},
    )
    raw_bundle = load(tmp_path, raw_doc, key="acme__raw-count")
    assert raw_bundle.inputs["dependents_deduped"] == Unmeasured(
        reason="collector_failure",
        detail="dependents_count is the raw deps.dev usedByCount, not deduplicated by root "
               "organisation (v2 2.1 needs the W1-2 'dependents' collector)",
    )

    # Negative 2 (, ): v1 deriver recorded nothing at all -- 2.1 is
    # class TP (02 section 4 row 2.1), so a deps.dev/ecosyste.ms absence is
    # third_party_gap, exactly like 2.3 downloads_monthly's equivalent
    # branch, not collector_failure (that stays reserved for a local-side
    # fault, e.g. Negative 1 above's "wrong collector ran" case).
    missing_doc = base_document(confidence={"dependents_count": "missing"})
    missing_bundle = load(tmp_path, missing_doc, key="acme__missing-deps")
    assert missing_bundle.inputs["dependents_deduped"] == Unmeasured(
        reason="third_party_gap", detail="deps.dev/ecosyste.ms returned no dependents data",
    )


def test_dependents_deduped_systems_is_not_applicable(tmp_path: Path) -> None:
    """, D-MISS-5: ecosystem == systems always yields
    Unmeasured(not_applicable), read straight off the bundle's own
    ecosystem fact -- ahead of, and regardless of, confidence. Checked
    against every confidence value a real v1 document could carry,
    including "derived" (a hypothetical collector bug must not be able to
    smuggle a Measured value past the D-MISS-5 guard)."""
    for confidence_value in ("missing", "verified", "derived", None):
        confidence = {} if confidence_value is None else {"dependents_count": confidence_value}
        meta = {"dependents_count": 7} if confidence_value == "derived" else {}
        doc = base_document(ecosystem="systems", meta=meta, confidence=confidence)
        bundle = load(tmp_path, doc, key=f"acme__systems-{confidence_value}")
        assert bundle.inputs["dependents_deduped"] == Unmeasured(
            reason="not_applicable",
            detail="D-MISS-5: ecosystem == systems has no package registry (2.1 is undefined)",
        )


def test_dependents_deduped_maven_missing_names_the_pom_gap(tmp_path: Path) -> None:
    """: maven's third_party_gap detail names the real current gap
    (pom.xml was never fetched), not the generic deps.dev-absence text."""
    doc = base_document(ecosystem="maven", confidence={"dependents_count": "missing"})
    bundle = load(tmp_path, doc, key="acme__maven-missing")
    assert bundle.inputs["dependents_deduped"] == Unmeasured(
        reason="third_party_gap",
        detail="root pom.xml contents not collected (tree envelope carries paths only; "
               "a later fetch must add one raw-content call per maven repo)",
    )


def test_dependents_deduped_maven_measured_when_derived(tmp_path: Path) -> None:
    """Over-correction guard: a maven repo whose dependents WERE measured
    (confidence "derived", e.g. once a later packet adds the pom.xml fetch)
    is unaffected by the maven-specific missing-branch detail -- it takes
    the same Measured path as any other ecosystem."""
    data = {"deduped_count": 3, "package": "org.apache.thrift:libthrift"}
    write_envelope(tmp_path / "raw", "dependents", "acme__widget", data, "2026-09-01T00:05:00+00:00")
    doc = base_document(
        ecosystem="maven",
        meta={"dependents_count": 3},
        provenance={"dependents_count": prov("dependents")},
        confidence={"dependents_count": "derived"},
    )
    bundle = load(tmp_path, doc)
    assert bundle.inputs["dependents_deduped"] == Measured(
        value=3, n=0, window_start=None, window_end=None, method="primary",
        source="raw/dependents/acme__widget.json",
        source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
    )


def test_dependents_deduped_go_measured_when_derived(tmp_path: Path) -> None:
    """Over-correction guard: go's Measured path is unchanged --  only
    touches the missing/not_applicable branches, not the "derived" one."""
    data = {"deduped_count": 5, "package": "github.com/a-h/templ"}
    write_envelope(tmp_path / "raw", "dependents", "acme__widget", data, "2026-09-01T00:05:00+00:00")
    doc = base_document(
        ecosystem="go",
        meta={"dependents_count": 5},
        provenance={"dependents_count": prov("dependents")},
        confidence={"dependents_count": "derived"},
    )
    bundle = load(tmp_path, doc)
    assert bundle.inputs["dependents_deduped"] == Measured(
        value=5, n=0, window_start=None, window_end=None, method="primary",
        source="raw/dependents/acme__widget.json",
        source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
    )


def test_dependents_deduped_go_missing_is_generic_third_party_gap(tmp_path: Path) -> None:
    """ requirement 4 (unchanged branch): a mapped ecosystem (go) with
    no measured count stays the generic third_party_gap detail, exactly
    like pypi/npm/crates -- not the maven-specific wording."""
    doc = base_document(ecosystem="go", confidence={"dependents_count": "missing"})
    bundle = load(tmp_path, doc, key="acme__go-missing")
    assert bundle.inputs["dependents_deduped"] == Unmeasured(
        reason="third_party_gap", detail="deps.dev/ecosyste.ms returned no dependents data",
    )


def test_dependents_deduped_unmapped_ecosystem_names_it_not_not_applicable(tmp_path: Path) -> None:
    """D-MISS-5's guard: "the rule list is closed" -- an ecosystem this row
    has never seen must still fall to third_party_gap, never
    not_applicable, and its detail names the ecosystem."""
    doc = base_document(ecosystem="conda", confidence={"dependents_count": "missing"})
    bundle = load(tmp_path, doc, key="acme__conda-missing")
    assert bundle.inputs["dependents_deduped"] == Unmeasured(
        reason="third_party_gap", detail="no dependents mapping for ecosystem 'conda'",
    )


# --------------------------------------------------------------------------- #
# 2.3 downloads_monthly                                                       #
# --------------------------------------------------------------------------- #

def test_downloads_monthly(tmp_path: Path) -> None:
    data = {"monthly": 15000}
    write_envelope(tmp_path / "raw", "downloads", "acme__widget", data, "2026-09-01T00:05:00+00:00")
    doc = base_document(
        meta={"monthly_downloads": 15000},
        provenance={"monthly_downloads": prov("downloads")},
        confidence={"monthly_downloads": "verified"},
    )
    bundle = load(tmp_path, doc)
    assert bundle.inputs["downloads_monthly"] == Measured(
        value=15000, n=0, window_start=None, window_end=None, method="primary",
        source="raw/downloads/acme__widget.json",
        source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
    )

    # Negative 1: W1-8 downloads_status=pending (retryable ecosystem).
    pending = base_document(meta={"downloads_status": "pending"}, confidence={"monthly_downloads": "missing"})
    pending_bundle = load(tmp_path, pending, key="acme__pending")
    assert pending_bundle.inputs["downloads_monthly"] == Unmeasured(
        reason="third_party_gap",
        detail="W1-8 downloads_status=pending: ecosystem is retryable, not yet backfilled",
    )

    # Negative 2: W1-8 downloads_status=unmeasured (no registry for this ecosystem).
    unmeasured = base_document(meta={"downloads_status": "unmeasured"}, confidence={"monthly_downloads": "missing"})
    unmeasured_bundle = load(tmp_path, unmeasured, key="acme__unmeasured")
    assert unmeasured_bundle.inputs["downloads_monthly"] == Unmeasured(
        reason="third_party_gap",
        detail="W1-8 downloads_status=unmeasured: ecosystem has no downloads registry",
    )

    # Negative 2b: downloads_status=unattributed (deps.dev links no package of the
    # registry to the repo, or the count on file is another package's).
    unattributed = base_document(meta={"downloads_status": "unattributed"},
                                 confidence={"monthly_downloads": "missing"})
    unattributed_bundle = load(tmp_path, unattributed, key="acme__unattributed")
    assert unattributed_bundle.inputs["downloads_monthly"] == Unmeasured(
        reason="third_party_gap",
        detail="downloads_status=unattributed: deps.dev links no package of this "
               "registry to the repo, or the count on file is another package's",
    )

    # Negative 3: old-format document, no downloads_status hook at all.
    old_format = base_document(confidence={"monthly_downloads": "missing"})
    old_bundle = load(tmp_path, old_format, key="acme__old-format")
    assert old_bundle.inputs["downloads_monthly"] == Unmeasured(
        reason="third_party_gap", detail="registry returned no downloads data",
    )


# --------------------------------------------------------------------------- #
# 3.1 legal_ip                                                                #
# --------------------------------------------------------------------------- #

def test_legal_ip(tmp_path: Path) -> None:
    """ BB3: this row now reads the packet's own bare "legal_ip" field
    (a "governance" envelope) as an identity vocab over
    constants.LEGAL_IP_VALUES -- never "legal_ip_model" (the v1 regex
    heuristic  was built to replace; deleted from this row entirely, so
    a document that only ever wrote the old field is indistinguishable from
    one that never ran the collector at all -- both are Unmeasured, Negative
    1 below). Mirrors test_ai_posture's shape exactly (same envelope,
    same identity-vocab pattern, already correct before this round)."""
    data = {"legal_ip": "CORP_CLA"}
    write_envelope(tmp_path / "raw", "governance", "acme__widget", data, "2026-09-01T00:05:00+00:00")
    doc = base_document(
        meta={"legal_ip": "CORP_CLA"},
        provenance={"legal_ip": prov("governance")},
        confidence={"legal_ip": "derived"},
    )
    bundle = load(tmp_path, doc)
    assert bundle.inputs["legal_ip"] == Measured(
        value="CORP_CLA", n=0, window_start=None, window_end=None, method="primary",
        source="raw/governance/acme__widget.json",
        source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
    )

    # Negative 1: v1 deriver recorded nothing (no governance envelope, or
    # the collector failed --  B2's "missing" confidence).
    missing = base_document(confidence={"legal_ip": "missing"})
    missing_bundle = load(tmp_path, missing, key="acme__missing-legal")
    assert missing_bundle.inputs["legal_ip"] == Unmeasured(
        reason="collector_failure", detail="v1 deriver recorded no value",
    )

    # Negative 2 (, ): an unrecognized value is THIS ROW's own gap,
    # not a whole-bundle rejection -- even when the envelope is otherwise
    # perfectly resolvable (pre-: this raised BundleValidationError and
    # lost the whole repo's 17 other, perfectly good inputs over one bad
    # value in a row that was going to be Unmeasured regardless). 's own
    # classify_legal_ip never emits an out-of-vocabulary value, so this
    # scenario is a defensive guard against a malformed/hand-edited document,
    # not a reachable collector output.
    bogus_data = {"legal_ip": "BOGUS_REGIME"}
    write_envelope(tmp_path / "raw", "governance", "acme__bogus-legal", bogus_data, "2026-09-01T00:05:00+00:00")
    bogus_doc = base_document(
        meta={"legal_ip": "BOGUS_REGIME"},
        provenance={"legal_ip": prov("governance", key="acme__bogus-legal")},
        confidence={"legal_ip": "derived"},
    )
    bogus_bundle = load(tmp_path, bogus_doc, key="acme__bogus-legal")  # must not raise
    assert bogus_bundle.inputs["legal_ip"] == Unmeasured(
        reason="collector_failure",
        detail="v1 value 'BOGUS_REGIME' for 'legal_ip' has no v2 vocabulary mapping",
    )


def test_legal_ip_unmappable_enum_over_correction_guard(tmp_path: Path) -> None:
    """Over-correction guard for : the row-scoped fix does not disable
    whole-bundle rejection generally -- a genuinely malformed bundle (no
    parseable ``owner/name`` repo identity) is still rejected, even when it
    also carries the same unmappable enum used above."""
    doc = base_document(repo="not-a-valid-repo-string", meta={"legal_ip_model": "BOGUS_REGIME"},
                        confidence={"legal_ip_model": "verified"})
    with pytest.raises(BundleValidationError, match="owner/name"):
        load(tmp_path, doc, key="acme__malformed-repo")


# --------------------------------------------------------------------------- #
# 3.2 elephant_factor                                                         #
# --------------------------------------------------------------------------- #

def test_elephant_factor(tmp_path: Path) -> None:
    data = {"total": 5}
    write_envelope(tmp_path / "raw", "contributors", "acme__widget", data, "2026-09-01T00:05:00+00:00")
    doc = base_document(
        meta={"elephant_factor": 3},
        provenance={"elephant_factor": prov("contributors")},
        confidence={"elephant_factor": "derived"},
    )
    bundle = load(tmp_path, doc)
    assert bundle.inputs["elephant_factor"] == Measured(
        value=3, n=0, window_start=None, window_end=None, method="primary",
        source="raw/contributors/acme__widget.json",
        source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
    )

    missing = base_document(confidence={"elephant_factor": "missing"})
    missing_bundle = load(tmp_path, missing, key="acme__missing-ef")
    assert missing_bundle.inputs["elephant_factor"] == Unmeasured(
        reason="collector_failure", detail="v1 deriver recorded no value",
    )


# --------------------------------------------------------------------------- #
# 3.3 promotion_ladder                                                        #
# --------------------------------------------------------------------------- #

def test_promotion_ladder(tmp_path: Path) -> None:
    data = {"promotion_ladder": "VERIFIED_PROMOTION"}
    write_envelope(tmp_path / "raw", "tree", "acme__widget", data, "2026-09-01T00:05:00+00:00")
    doc = base_document(
        meta={"promotion_ladder": "VERIFIED_PROMOTION"},
        provenance={"promotion_ladder": prov("tree")},
        confidence={"promotion_ladder": "verified"},
    )
    bundle = load(tmp_path, doc)
    assert bundle.inputs["promotion_ladder"] == Measured(
        value="VERIFIED", n=0, window_start=None, window_end=None, method="primary",
        source="raw/tree/acme__widget.json",
        source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
    )

    missing = base_document(confidence={"promotion_ladder": "missing"})
    missing_bundle = load(tmp_path, missing, key="acme__missing-ladder")
    assert missing_bundle.inputs["promotion_ladder"] == Unmeasured(
        reason="collector_failure", detail="v1 deriver recorded no value",
    )

    # , : same row-scoped fix as legal_ip -- an unmappable enum never
    # rejects the whole bundle.
    bogus_data = {"promotion_ladder": "ROGUE_RUNG"}
    write_envelope(tmp_path / "raw", "tree", "acme__bogus-ladder", bogus_data, "2026-09-01T00:05:00+00:00")
    bogus_doc = base_document(
        meta={"promotion_ladder": "ROGUE_RUNG"},
        provenance={"promotion_ladder": prov("tree", key="acme__bogus-ladder")},
        confidence={"promotion_ladder": "verified"},
    )
    bogus_bundle = load(tmp_path, bogus_doc, key="acme__bogus-ladder")  # must not raise
    assert bogus_bundle.inputs["promotion_ladder"] == Unmeasured(
        reason="collector_failure",
        detail="v1 value 'ROGUE_RUNG' for 'promotion_ladder' has no v2 vocabulary mapping",
    )


def test_promotion_ladder_accepts_both_v2_identity_and_legacy_v1_spellings(tmp_path: Path) -> None:
    """ BB3 pin (round-3 review CW1 / round-3 review mutant M8):
    INPUT_MAPPINGS["promotion_ladder"] merges the v2 identity vocabulary
    (VERIFIED/DOCUMENTED/AD_HOC/CLOSED -- what 's own
    classify_promotion_ladder writes into this same bare field) with the
    legacy V1_LADDER_VALUES fallback (VERIFIED_PROMOTION/DOCUMENTED_PATHWAY/
    AD_HOC/CLOSED_SINGLE_FIRM). M8 (revert the row to the v1-only vocabulary
    -- the exact round-2 Blocker state) makes every v2-spelling case below
    go red while every legacy-spelling case keeps passing; this is what
    catches that revert (test_promotion_ladder above does not: it only ever
    feeds legacy v1 spellings in)."""
    pairs = (
        ("VERIFIED", "VERIFIED"),
        ("VERIFIED_PROMOTION", "VERIFIED"),
        ("DOCUMENTED", "DOCUMENTED"),
        ("DOCUMENTED_PATHWAY", "DOCUMENTED"),
        ("AD_HOC", "AD_HOC"),
        ("CLOSED", "CLOSED"),
        ("CLOSED_SINGLE_FIRM", "CLOSED"),
    )
    for i, (written, expected) in enumerate(pairs):
        key = f"acme__ladder-pair-{i}"
        data = {"promotion_ladder": written}
        write_envelope(tmp_path / "raw", "governance", key, data, "2026-09-01T00:05:00+00:00")
        doc = base_document(
            meta={"promotion_ladder": written},
            provenance={"promotion_ladder": prov("governance", key=key)},
            confidence={"promotion_ladder": "derived"},
        )
        bundle = load(tmp_path, doc, key=key)
        assert bundle.inputs["promotion_ladder"] == Measured(
            value=expected, n=0, window_start=None, window_end=None, method="primary",
            source=f"raw/governance/{key}.json",
            source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
        ), f"{written!r} -> expected Measured({expected!r})"


# --------------------------------------------------------------------------- #
# 4.2 ai_posture                                                              #
# --------------------------------------------------------------------------- #

def test_ai_posture(tmp_path: Path) -> None:
    data = {"ai_posture": "REGULATED"}
    write_envelope(tmp_path / "raw", "governance", "acme__widget", data, "2026-09-01T00:05:00+00:00")
    doc = base_document(
        meta={"ai_posture": "REGULATED"},
        provenance={"ai_posture": prov("governance")},
        confidence={"ai_posture": "flag"},
    )
    bundle = load(tmp_path, doc)
    assert bundle.inputs["ai_posture"] == Measured(
        value="REGULATED", n=0, window_start=None, window_end=None, method="primary",
        source="raw/governance/acme__widget.json",
        source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
    )

    unrecognized = base_document(
        meta={"ai_posture": "REGULATED"}, confidence={"ai_posture": "weird_value"},
    )
    unrecognized_bundle = load(tmp_path, unrecognized, key="acme__weird-conf")
    assert unrecognized_bundle.inputs["ai_posture"] == Unmeasured(
        reason="collector_failure",
        detail="v1 confidence 'weird_value' for 'ai_posture' is not recognized",
    )


def test_ai_posture_rejects_an_out_of_set_value(tmp_path: Path) -> None:
    """, : ai_posture validates against constants.AI_POSTURE_VALUES
    -- an out-of-set value is Unmeasured(collector_failure) with the bad
    value in detail, never Measured, even with an otherwise perfectly
    resolvable envelope (pre-: the non-validating scalar resolver
    passed any string through as a Measured value)."""
    data = {"ai_posture": "NOT_A_POSTURE"}
    write_envelope(tmp_path / "raw", "governance", "acme__bad-posture", data, "2026-09-01T00:05:00+00:00")
    doc = base_document(
        meta={"ai_posture": "NOT_A_POSTURE"},
        provenance={"ai_posture": prov("governance", key="acme__bad-posture")},
        confidence={"ai_posture": "derived"},
    )
    bundle = load(tmp_path, doc, key="acme__bad-posture")
    assert bundle.inputs["ai_posture"] == Unmeasured(
        reason="collector_failure",
        detail="v1 value 'NOT_A_POSTURE' for 'ai_posture' has no v2 vocabulary mapping",
    )
    assert isinstance(bundle.inputs["ai_posture"], Unmeasured)  # never Measured on a bad value


# --------------------------------------------------------------------------- #
# 5.1 newcomer_issue_supply                                                   #
# --------------------------------------------------------------------------- #

def test_newcomer_issue_supply(tmp_path: Path) -> None:
    data = {"count": 7}
    write_envelope(tmp_path / "raw", "gfi", "acme__widget", data, "2026-09-01T00:05:00+00:00")
    doc = base_document(
        meta={"active_unclaimed_gfis": 7},
        provenance={"active_unclaimed_gfis": prov("gfi")},
        confidence={"active_unclaimed_gfis": "verified"},
    )
    bundle = load(tmp_path, doc)
    assert bundle.inputs["newcomer_issue_supply"] == Measured(
        value=7, n=0, window_start=None, window_end=None, method="primary",
        source="raw/gfi/acme__widget.json",
        source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
    )

    missing = base_document(confidence={"active_unclaimed_gfis": "missing"})
    missing_bundle = load(tmp_path, missing, key="acme__missing-gfi")
    assert missing_bundle.inputs["newcomer_issue_supply"] == Unmeasured(
        reason="collector_failure", detail="v1 deriver recorded no value",
    )


# --------------------------------------------------------------------------- #
# 2.2 openssf_criticality (, )                                       #
# --------------------------------------------------------------------------- #

#: The ten Pike signals (porchlamp.models.OpenSSFSignals field names), a plausible
#: mid-sized repo's values -- matches the module docstring's signal table.
_OPENSSF_SIGNALS = {
    "created_since_months": 48.0,
    "updated_since_months": 0.5,
    "contributor_count": 12,
    "org_count": 3,
    "commit_frequency_weekly": 8.0,
    "recent_releases_count": 6,
    "closed_issues_count_90d": 40,
    "updated_issues_count_90d": 55,
    "comment_frequency": 2.5,
    "dependents_count": 120,
}
_OPENSSF_PROV_COLLECTORS = ("github_meta", "commits_365d", "releases", "issues_counts", "dependents")


def _write_openssf_envelopes(tmp_path: Path, key: str) -> None:
    for collector in _OPENSSF_PROV_COLLECTORS:
        write_envelope(tmp_path / "raw", collector, key, {"placeholder": collector}, "2026-09-01T00:05:00+00:00")


def test_openssf_criticality(tmp_path: Path) -> None:
    from porchlamp.models import OpenSSFSignals
    from porchlamp.openssf import calculate_pike_criticality

    key = "acme__pike"
    _write_openssf_envelopes(tmp_path, key)
    doc = base_document(
        meta={"openssf_signals": dict(_OPENSSF_SIGNALS)},
        provenance={"openssf_signals": {
            "source": [f"/original-data-dir/raw/{c}/{key}.json" for c in _OPENSSF_PROV_COLLECTORS],
            "fetched_at": "2026-09-01T00:05:00+00:00",
        }},
        confidence={"openssf_signals": "derived"},
    )
    bundle = load(tmp_path, doc, key=key)
    value = bundle.inputs["openssf_criticality"]
    assert isinstance(value, Measured)
    # Oracle: porchlamp.openssf.calculate_pike_criticality itself (independent of
    # porchlamp.derive.bundle's mirrored, porchlamp.core-only reimplementation), not the
    # code under test.
    expected = round(calculate_pike_criticality(OpenSSFSignals(**_OPENSSF_SIGNALS)), 4)
    assert value == Measured(
        value=expected, n=0, window_start=None, window_end=None, method="primary",
        source=f"raw/github_meta/{key}.json",
        source_hash=expected_hash({"placeholder": "github_meta"}),
        fetched_at="2026-09-01T00:05:00+00:00",
    )
    assert 0.0 <= value.value <= 1.0


#: Three signal vectors spanning the formula's interesting regions: every
#: field at zero, a plausible mid-sized repo, and every field pinned exactly
#: at its OPENSSF_THRESHOLDS saturation point (porchlamp.core.openssf.
#: OPENSSF_THRESHOLDS values) -- the boundary where the log-ratio's
#: denominator equals its numerator's cap.
_OPENSSF_ALL_ZERO_SIGNALS = {
    "created_since_months": 0.0, "updated_since_months": 0.0, "contributor_count": 0,
    "org_count": 0, "commit_frequency_weekly": 0.0, "recent_releases_count": 0,
    "closed_issues_count_90d": 0, "updated_issues_count_90d": 0,
    "comment_frequency": 0.0, "dependents_count": 0,
}
_OPENSSF_AT_THRESHOLD_SIGNALS = {
    "created_since_months": 120.0, "updated_since_months": 120.0, "contributor_count": 5000,
    "org_count": 10, "commit_frequency_weekly": 1000.0, "recent_releases_count": 26,
    "closed_issues_count_90d": 5000, "updated_issues_count_90d": 5000,
    "comment_frequency": 15.0, "dependents_count": 500000,
}


@pytest.mark.parametrize(
    "signals",
    [_OPENSSF_ALL_ZERO_SIGNALS, _OPENSSF_SIGNALS, _OPENSSF_AT_THRESHOLD_SIGNALS],
    ids=["all-zero", "mid-vector", "at-threshold"],
)
def test_openssf_criticality_matches_core_openssf_directly(tmp_path: Path, signals: dict) -> None:
    """ wiring proof: the bundle-derived value equals
    porchlamp.core.openssf.calculate_pike_criticality(signals) called directly --
    not porchlamp.derive.bundle's own reimplementation (there is none any more)
    and not porchlamp.openssf's OpenSSFSignals-typed wrapper (a second hop this
    test does not take), across three regions of the formula: every signal
    at zero, a mid-sized repo, and every signal exactly at its threshold."""
    from porchlamp.core.openssf import calculate_pike_criticality

    key = f"acme__pike-parity-{hash(frozenset(signals.items())) & 0xffff:x}"
    _write_openssf_envelopes(tmp_path, key)
    doc = base_document(
        meta={"openssf_signals": dict(signals)},
        provenance={"openssf_signals": {
            "source": [f"/original-data-dir/raw/{c}/{key}.json" for c in _OPENSSF_PROV_COLLECTORS],
            "fetched_at": "2026-09-01T00:05:00+00:00",
        }},
        confidence={"openssf_signals": "derived"},
    )
    bundle = load(tmp_path, doc, key=key)
    value = bundle.inputs["openssf_criticality"]
    assert isinstance(value, Measured)
    assert value.value == round(calculate_pike_criticality(signals), 4)


def test_openssf_criticality_at_threshold_matches_hand_computed_score(tmp_path: Path) -> None:
    """Independent oracle (): unlike the parity test above (which
    compares two calls to the SAME porchlamp.core.openssf.calculate_pike_
    criticality and so cannot detect a wrong default weight or threshold --
    both sides would drift together), this asserts against a value derived
    by hand, calling no Porchlamp code at all. At every signal exactly at its
    threshold, saturation_denom == value for every term, so every
    normalized S_i* is exactly log(1+T)/log(1+T) = 1.0 regardless of T's
    magnitude; the score collapses to sum(alpha_i) / sum(|alpha_i|) = 8.5 /
    10.5 (the module docstrings' own cited constants) = 0.809523... ->
    0.8095. A single mutated default weight changes this ratio."""
    key = "acme__pike-hand-computed"
    _write_openssf_envelopes(tmp_path, key)
    doc = base_document(
        meta={"openssf_signals": dict(_OPENSSF_AT_THRESHOLD_SIGNALS)},
        provenance={"openssf_signals": {
            "source": [f"/original-data-dir/raw/{c}/{key}.json" for c in _OPENSSF_PROV_COLLECTORS],
            "fetched_at": "2026-09-01T00:05:00+00:00",
        }},
        confidence={"openssf_signals": "derived"},
    )
    bundle = load(tmp_path, doc, key=key)
    value = bundle.inputs["openssf_criticality"]
    assert isinstance(value, Measured)
    assert value.value == round(8.5 / 10.5, 4) == 0.8095


def test_openssf_criticality_missing_signal_names_it(tmp_path: Path) -> None:
    """, : a partial openssf_signals dict (defensive -- production's
    derive_pike_signals never emits one) is the whole-2.2-Unmeasured rule,
    with the missing signal named in detail."""
    key = "acme__pike-partial"
    _write_openssf_envelopes(tmp_path, key)
    partial_signals = dict(_OPENSSF_SIGNALS)
    del partial_signals["org_count"]
    doc = base_document(
        meta={"openssf_signals": partial_signals},
        provenance={"openssf_signals": {
            "source": [f"/original-data-dir/raw/{c}/{key}.json" for c in _OPENSSF_PROV_COLLECTORS],
            "fetched_at": "2026-09-01T00:05:00+00:00",
        }},
        confidence={"openssf_signals": "derived"},
    )
    bundle = load(tmp_path, doc, key=key)
    assert bundle.inputs["openssf_criticality"] == Unmeasured(
        reason="third_party_gap",
        detail="openssf_signals missing signal 'org_count' (02 section 4 2.2: never a partial score)",
    )


def test_openssf_criticality_absence_is_third_party_gap(tmp_path: Path) -> None:
    """02 section 4 row 2.2: class TP "only if a signal is missing" -- the
    real-world case (scripts/derive.py's ``b.missing("openssf_signals",
    None)`` placeholder) is confidence "missing", not an absent key."""
    doc = base_document(confidence={"openssf_signals": "missing"})
    bundle = load(tmp_path, doc, key="acme__pike-missing")
    assert bundle.inputs["openssf_criticality"] == Unmeasured(
        reason="third_party_gap",
        detail="one or more of the ten Pike signal sources is absent (02 section 4 2.2: never a partial score)",
    )


def test_openssf_criticality_collector_failure_carries_the_companion_detail(tmp_path: Path) -> None:
    """ items 1 and 3: scripts/derive.py records confidence
    "collector_failure" (a pike_signals crash, or a stale window_anchor)
    with the specific reason in the companion "openssf_signals_detail" meta
    key -- this row must surface that exact detail, not the generic
    "confidence not recognized" fallback message a bare unrecognized string
    gets."""
    doc = base_document(
        meta={"openssf_signals_detail": "pike_signals.derive_w17_fields raised RuntimeError: boom"},
        confidence={"openssf_signals": "collector_failure"},
    )
    bundle = load(tmp_path, doc, key="acme__pike-crashed")
    assert bundle.inputs["openssf_criticality"] == Unmeasured(
        reason="collector_failure",
        detail="pike_signals.derive_w17_fields raised RuntimeError: boom",
    )


def test_openssf_criticality_collector_failure_without_detail_still_names_the_reason(
    tmp_path: Path,
) -> None:
    """Over-correction guard: a missing/blank companion detail must not
    crash the resolver or silently swallow the reason -- it falls back to a
    short, still-truthful message."""
    doc = base_document(confidence={"openssf_signals": "collector_failure"})
    bundle = load(tmp_path, doc, key="acme__pike-crashed-no-detail")
    assert bundle.inputs["openssf_criticality"] == Unmeasured(
        reason="collector_failure", detail="pike_signals collector failed",
    )


# --------------------------------------------------------------------------- #
# 2.2 openssf_criticality proxy: proxy:no_dependents_signal (,           #
# D-MISS-5 consequence for 2.2)                                              #
# --------------------------------------------------------------------------- #

_OPENSSF_PARTIAL_PROV_COLLECTORS = ("github_meta", "commits_365d", "releases", "issues_counts")


def _write_openssf_partial_envelopes(tmp_path: Path, key: str) -> None:
    for collector in _OPENSSF_PARTIAL_PROV_COLLECTORS:
        write_envelope(tmp_path / "raw", collector, key, {"placeholder": collector}, "2026-09-01T00:05:00+00:00")


def _openssf_partial_provenance(key: str) -> dict:
    return {
        "source": [f"/original-data-dir/raw/{c}/{key}.json" for c in _OPENSSF_PARTIAL_PROV_COLLECTORS],
        "fetched_at": "2026-09-01T00:05:00+00:00",
    }


def test_openssf_criticality_proxy_when_dependents_not_applicable(tmp_path: Path) -> None:
    """D-MISS-5: a systems repo (2.1 not_applicable) with the other nine Pike
    signals present gets the renormalised proxy, method
    proxy:no_dependents_signal, provenance derived (D-MISS-4)."""
    key = "acme__pike-proxy-systems"
    _write_openssf_partial_envelopes(tmp_path, key)
    partial_signals = dict(_OPENSSF_SIGNALS)
    del partial_signals["dependents_count"]
    doc = base_document(
        ecosystem="systems",
        meta={
            "openssf_signals_partial": partial_signals,
            "openssf_signals_partial_missing": ["dependents_count"],
        },
        provenance={"openssf_signals_partial": _openssf_partial_provenance(key)},
        confidence={"openssf_signals_partial": "derived"},
    )
    bundle = load(tmp_path, doc, key=key)
    value = bundle.inputs["openssf_criticality"]
    assert isinstance(value, Measured)
    assert value.method == "proxy:no_dependents_signal"
    from porchlamp.core.openssf import calculate_pike_criticality as core_calculate

    expected = round(core_calculate(partial_signals, missing=frozenset({"dependents_count"})), 4)
    assert value.value == expected
    assert 0.0 <= value.value <= 1.0
    assert value.source == f"raw/github_meta/{key}.json"


def test_openssf_criticality_proxy_when_dependents_third_party_gap(tmp_path: Path) -> None:
    """D-MISS-5: a pypi repo whose 2.1 is a deps.dev third_party_gap (not
    not_applicable) still qualifies for the same proxy."""
    key = "acme__pike-proxy-gap"
    _write_openssf_partial_envelopes(tmp_path, key)
    partial_signals = dict(_OPENSSF_SIGNALS)
    del partial_signals["dependents_count"]
    doc = base_document(
        ecosystem="pypi",
        meta={
            "openssf_signals_partial": partial_signals,
            "openssf_signals_partial_missing": ["dependents_count"],
        },
        provenance={"openssf_signals_partial": _openssf_partial_provenance(key)},
        confidence={
            "dependents_count": "missing",
            "openssf_signals_partial": "derived",
        },
    )
    bundle = load(tmp_path, doc, key=key)
    value = bundle.inputs["openssf_criticality"]
    assert isinstance(value, Measured)
    assert value.method == "proxy:no_dependents_signal"


def test_openssf_criticality_proxy_hand_computed_oracle(tmp_path: Path) -> None:
    """Independent oracle (mirrors test_openssf_criticality_at_threshold_
    matches_hand_computed_score's arithmetic): every present signal exactly
    at its threshold collapses each S_i* to 1.0, so the renormalised score
    is sum(alpha_i excl. dependents) / sum(|alpha_i| excl. dependents) =
    6.5 / 8.5 = 0.7647058... -> 0.7647. A mutated default weight or a
    renormalisation that forgets to shrink the denominator changes this."""
    key = "acme__pike-proxy-hand-computed"
    _write_openssf_partial_envelopes(tmp_path, key)
    partial_signals = dict(_OPENSSF_AT_THRESHOLD_SIGNALS)
    del partial_signals["dependents_count"]
    doc = base_document(
        ecosystem="systems",
        meta={
            "openssf_signals_partial": partial_signals,
            "openssf_signals_partial_missing": ["dependents_count"],
        },
        provenance={"openssf_signals_partial": _openssf_partial_provenance(key)},
        confidence={"openssf_signals_partial": "derived"},
    )
    bundle = load(tmp_path, doc, key=key)
    value = bundle.inputs["openssf_criticality"]
    assert isinstance(value, Measured)
    assert value.value == round(6.5 / 8.5, 4) == 0.7647


def test_openssf_criticality_proxy_dependents_collector_failure_stays_unmeasured(tmp_path: Path) -> None:
    """D-MISS-5 consequence, negative case: a dependents collector_failure
    (a retry can still measure it) never gets the proxy, unlike
    not_applicable/third_party_gap."""
    key = "acme__pike-proxy-collector-failure"
    _write_openssf_partial_envelopes(tmp_path, key)
    partial_signals = dict(_OPENSSF_SIGNALS)
    del partial_signals["dependents_count"]
    doc = base_document(
        ecosystem="pypi",
        meta={
            "openssf_signals_partial": partial_signals,
            "openssf_signals_partial_missing": ["dependents_count"],
        },
        provenance={"openssf_signals_partial": _openssf_partial_provenance(key)},
        confidence={
            # "verified" (raw usedByCount) resolves 2.1 to collector_failure.
            "dependents_count": "verified",
            "openssf_signals_partial": "derived",
        },
    )
    bundle = load(tmp_path, doc, key=key)
    assert bundle.inputs["openssf_criticality"] == Unmeasured(
        reason="collector_failure",
        detail="dependents signal is collector_failure; a retry can still measure openssf_criticality",
    )


def test_openssf_criticality_proxy_insufficient_signals(tmp_path: Path) -> None:
    """Fewer than OPENSSF_PROXY_MIN_SIGNALS (8) present -> insufficient_n,
    naming the count -- a hand-built shape (production's derive_pike_signals
    only ever omits dependents_count alone) proving the general guard, not
    just the one-missing-field case."""
    key = "acme__pike-proxy-insufficient"
    _write_openssf_partial_envelopes(tmp_path, key)
    missing_fields = ["dependents_count", "org_count", "comment_frequency"]
    partial_signals = {k: v for k, v in _OPENSSF_SIGNALS.items() if k not in missing_fields}
    doc = base_document(
        ecosystem="systems",
        meta={
            "openssf_signals_partial": partial_signals,
            "openssf_signals_partial_missing": missing_fields,
        },
        provenance={"openssf_signals_partial": _openssf_partial_provenance(key)},
        confidence={"openssf_signals_partial": "derived"},
    )
    bundle = load(tmp_path, doc, key=key)
    assert bundle.inputs["openssf_criticality"] == Unmeasured(
        reason="insufficient_n", detail="7 of 10 signals",
    )


def test_openssf_criticality_proxy_not_reached_when_dependents_measured(tmp_path: Path) -> None:
    """Over-correction guard: a hand-built/corrupted document carrying both
    a genuinely measured dependents_count AND an (inconsistent)
    openssf_signals_partial companion must not be short-circuited into the
    proxy -- falls through to the ordinary confidence-based branches."""
    key = "acme__pike-proxy-not-reached"
    _write_openssf_partial_envelopes(tmp_path, key)
    write_envelope(tmp_path / "raw", "dependents", key, {"deduped_count": 42}, "2026-09-01T00:05:00+00:00")
    partial_signals = dict(_OPENSSF_SIGNALS)
    del partial_signals["dependents_count"]
    doc = base_document(
        ecosystem="pypi",
        meta={
            "dependents_count": 42,
            "openssf_signals_partial": partial_signals,
            "openssf_signals_partial_missing": ["dependents_count"],
        },
        provenance={
            "dependents_count": prov("dependents", key=key),
            "openssf_signals_partial": _openssf_partial_provenance(key),
        },
        confidence={
            "dependents_count": "derived",
            "openssf_signals_partial": "derived",
        },
    )
    bundle = load(tmp_path, doc, key=key)
    # dependents_deduped is Measured; the proxy helper returns None and the
    # generic "confidence not recognized" path applies (no "openssf_signals"
    # confidence was ever set in this hand-built doc).
    assert bundle.inputs["openssf_criticality"] == Unmeasured(
        reason="collector_failure",
        detail="v1 confidence None for 'openssf_signals' is not recognized",
    )


def test_openssf_criticality_pike_signals_and_openssf_containers_agree(tmp_path: Path) -> None:
    """"Both containers agree" ( packet text): porchlamp.collect.pike_signals'
    own derive_pike_signals output, fed through porchlamp.core.openssf.
    calculate_pike_criticality's missing kwarg, matches the bundle-derived
    proxy value end to end -- not two independent reimplementations that
    could silently drift apart."""
    from datetime import datetime, timezone

    from porchlamp.collect import pike_signals as pike
    from porchlamp.core.openssf import calculate_pike_criticality as core_calculate

    now = datetime(2026, 9, 12, 0, 0, 0, tzinfo=timezone.utc)
    gh = {"created_at": "2016-09-14T00:00:00+00:00"}
    c365 = [{"date": "2025-09-12T00:00:00Z", "author_email": "dev@a.com",
             "author_login": "", "author_type": "User"}] * 12
    rel = {"source": "releases", "releases": []}
    iss = {"closed_90d": 42, "updated_90d": 120, "comments_per_issue": 4.0}
    result = pike.derive_pike_signals(gh, c365, rel, iss, None, now)
    assert result is not None
    signals, missing = result
    assert missing == frozenset({"dependents_count"})
    pike_side = core_calculate(signals, missing=missing)

    key = "acme__pike-parity-containers"
    _write_openssf_partial_envelopes(tmp_path, key)
    doc = base_document(
        ecosystem="systems",
        meta={"openssf_signals_partial": signals, "openssf_signals_partial_missing": sorted(missing)},
        provenance={"openssf_signals_partial": _openssf_partial_provenance(key)},
        confidence={"openssf_signals_partial": "derived"},
    )
    bundle = load(tmp_path, doc, key=key)
    value = bundle.inputs["openssf_criticality"]
    assert isinstance(value, Measured)
    assert value.value == round(pike_side, 4)


# --------------------------------------------------------------------------- #
# 4.1 pipeline_defenses (, )                                         #
# --------------------------------------------------------------------------- #

def test_pipeline_defenses_primary_method(tmp_path: Path) -> None:
    """Scorecard subset mean: method "primary", n=0 (a scalar)."""
    key = "acme__scorecard"
    write_envelope(tmp_path / "raw", "scorecard", key, {"score": 5, "checks": {}}, "2026-09-01T00:05:00+00:00")
    doc = base_document(
        meta={"pipeline_defenses": 32.5, "pipeline_defenses_method": "primary"},
        provenance={"pipeline_defenses": {
            "source": [f"/original-data-dir/raw/scorecard/{key}.json"],
            "fetched_at": "2026-09-01T00:05:00+00:00",
        }},
        confidence={"pipeline_defenses": "derived"},
    )
    bundle = load(tmp_path, doc, key=key)
    assert bundle.inputs["pipeline_defenses"] == Measured(
        value=32.5, n=0, window_start=None, window_end=None, method="primary",
        source=f"raw/scorecard/{key}.json",
        source_hash=expected_hash({"score": 5, "checks": {}}), fetched_at="2026-09-01T00:05:00+00:00",
    )


def test_pipeline_defenses_proxy_method_counts_merged_prs(tmp_path: Path) -> None:
    """proxy:review_ci: n is the merged-PR count (the code_review_rate
    denominator), read from the pr_window envelope's own prs list -- 3
    merged of 5 recorded PRs here, none of which is the min-n gate's
    concern (that lives in porchlamp.core.params.rigor, not this loader)."""
    key = "acme__proxy-pd"
    prs = [
        {"number": 1, "mergedAt": "2026-08-01T00:00:00+00:00"},
        {"number": 2, "mergedAt": "2026-08-02T00:00:00+00:00"},
        {"number": 3, "mergedAt": None},
        {"number": 4, "mergedAt": "2026-08-04T00:00:00+00:00"},
        {"number": 5, "mergedAt": None},
    ]
    pr_window_env = pr_window_data(prs=prs)
    write_envelope(tmp_path / "raw", "pr_window", key, pr_window_env, "2026-09-01T00:05:00+00:00")
    write_envelope(tmp_path / "raw", "runs", key, {"runs": []}, "2026-09-01T00:03:00+00:00")
    doc = base_document(
        meta={"pipeline_defenses": 18.0, "pipeline_defenses_method": "proxy:review_ci"},
        provenance={"pipeline_defenses": {
            "source": [f"/original-data-dir/raw/pr_window/{key}.json", f"/original-data-dir/raw/runs/{key}.json"],
            "fetched_at": "2026-09-01T00:05:00+00:00",
        }},
        confidence={"pipeline_defenses": "derived"},
    )
    bundle = load(tmp_path, doc, key=key)
    assert bundle.inputs["pipeline_defenses"] == Measured(
        value=18.0, n=3, window_start=None, window_end=None, method="proxy:review_ci",
        source=f"raw/pr_window/{key}.json",
        source_hash=expected_hash(pr_window_env), fetched_at="2026-09-01T00:05:00+00:00",
    )


def test_pipeline_defenses_absence_is_third_party_gap(tmp_path: Path) -> None:
    """, : scripts/derive.py never calls Bundle.missing for this
    field -- when neither method is measurable its confidence key is simply
    absent (base_document()'s empty confidence dict), which must be treated
    the same as an explicit "missing" (02 section 4 row 4.1: "TP with
    proxy")."""
    doc = base_document()
    bundle = load(tmp_path, doc, key="acme__pd-absent")
    assert bundle.inputs["pipeline_defenses"] == Unmeasured(
        reason="third_party_gap",
        detail="no scorecard envelope with a usable check subset and no pr_window/runs envelopes to "
               "derive the review/CI proxy (porchlamp.collect.pike_signals.pipeline_defenses)",
    )


def test_pipeline_defenses_collector_failure_carries_the_companion_detail(tmp_path: Path) -> None:
    """ item 1: the same collector_failure sentinel as
    openssf_criticality, keyed off "pipeline_defenses_detail" instead."""
    doc = base_document(
        meta={"pipeline_defenses_detail": "pike_signals.derive_w17_fields raised RuntimeError: boom"},
        confidence={"pipeline_defenses": "collector_failure"},
    )
    bundle = load(tmp_path, doc, key="acme__pd-crashed")
    assert bundle.inputs["pipeline_defenses"] == Unmeasured(
        reason="collector_failure",
        detail="pike_signals.derive_w17_fields raised RuntimeError: boom",
    )


def test_pipeline_defenses_n_feeds_the_rigor_min_n_gate(tmp_path: Path) -> None:
    """ item 5 (02 section 4.1, F7): the proxy method's recorded n
    (merged-PR count) must not just exist but actually reach
    porchlamp.core.params.rigor's registered 10-merged-PR floor. n genuinely
    changes with the fixture (9 vs 10 merged PRs, one extra open PR that
    never counts either way) and 9 falls below the floor while 10 clears
    it -- proving the wiring from bundle.py's loader through to the curve
    module that reads ``value.n``, not merely that a number is stored."""
    from porchlamp.core.params.rigor import pipeline_defenses as rigor_pipeline_defenses

    def make(n_merged: int, key: str) -> Any:
        prs = [{"number": i, "mergedAt": "2026-08-01T00:00:00+00:00"} for i in range(n_merged)]
        prs.append({"number": 999, "mergedAt": None})  # one open PR: never counted either way
        pr_window_env = pr_window_data(prs=prs)
        write_envelope(tmp_path / "raw", "pr_window", key, pr_window_env, "2026-09-01T00:05:00+00:00")
        write_envelope(tmp_path / "raw", "runs", key, {"runs": []}, "2026-09-01T00:03:00+00:00")
        doc = base_document(
            meta={"pipeline_defenses": 18.0, "pipeline_defenses_method": "proxy:review_ci"},
            provenance={"pipeline_defenses": {
                "source": [f"/original-data-dir/raw/pr_window/{key}.json",
                           f"/original-data-dir/raw/runs/{key}.json"],
                "fetched_at": "2026-09-01T00:05:00+00:00",
            }},
            confidence={"pipeline_defenses": "derived"},
        )
        return load(tmp_path, doc, key=key).inputs["pipeline_defenses"]

    below = make(9, "acme__pd-n-below-floor")
    at_floor = make(10, "acme__pd-n-at-floor")

    assert below.n == 9 and at_floor.n == 10  # n changes with the fixture

    below_score = rigor_pipeline_defenses(below)
    at_floor_score = rigor_pipeline_defenses(at_floor)
    assert below_score.measured is False and below_score.reason == "insufficient_n"
    assert below_score.n == 9
    assert at_floor_score.measured is True and at_floor_score.reason is None
    assert at_floor_score.score == 18.0


# --------------------------------------------------------------------------- #
# The five rows with no landed producer: a constant Unmeasured regardless of #
# document content. Two very different documents (empty, and one carrying    #
# unrelated data for every other row) land on the exact same value, proving  #
# these rows do not branch on anything in the fixture.                       #
# --------------------------------------------------------------------------- #

_UNMAPPED_ROWS = {
    "dx": "v2 5.4 blends CI runtime with setup signals; v1 stores a CI runtime mean only",
}


@pytest.mark.parametrize("input_id, detail", sorted(_UNMAPPED_ROWS.items()))
def test_unmapped_row_is_constant(tmp_path: Path, input_id: str, detail: str) -> None:
    empty_bundle = load(tmp_path, base_document(), key="acme__empty")
    assert empty_bundle.inputs[input_id] == Unmeasured(reason="collector_failure", detail=detail)

    # A document loaded with unrelated data present for every OTHER row
    # (elephant_factor, ai_posture, etc.) still yields the exact same
    # constant for this row: it does not branch on document content at all.
    busy_doc = base_document(
        meta={"elephant_factor": 9, "ai_posture": "REGULATED", "monthly_downloads": 100},
        confidence={"elephant_factor": "derived", "ai_posture": "verified", "monthly_downloads": "verified"},
        provenance={
            "elephant_factor": prov("contributors", key="acme__busy"),
            "ai_posture": prov("governance", key="acme__busy"),
            "monthly_downloads": prov("downloads", key="acme__busy"),
        },
    )
    for collector, data in (
        ("contributors", {"total": 1}), ("governance", {"ai_posture": "REGULATED"}),
        ("downloads", {"monthly": 100}),
    ):
        write_envelope(tmp_path / "raw", collector, "acme__busy", data, "2026-09-01T00:05:00+00:00")
    busy_bundle = load(tmp_path, busy_doc, key="acme__busy")
    assert busy_bundle.inputs[input_id] == Unmeasured(reason="collector_failure", detail=detail)


# --------------------------------------------------------------------------- #
# 1.3 mr_first_rate / 5.2 newcomer_conversion / 5.3 rr90_return_rate (,  #
# review W-4//C1e) -- read-through of author_history's own D-IN-3-      #
# adjusted cohort rates via _author_history_rate_resolver. Full path         #
# coverage lives on mr_first_rate (the shared factory code); the other two   #
# rows only need proving their own field names are wired correctly.         #
# --------------------------------------------------------------------------- #

def test_mr_first_rate(tmp_path: Path) -> None:
    ah_data = author_history_data()
    write_envelope(tmp_path / "raw", "author_history", "acme__mrfirst", ah_data, "2026-09-01T00:06:00+00:00")
    doc = base_document(
        meta={"mr_first_rate": 0.6, "mr_first_pr_count": 5},
        provenance={"mr_first_rate": prov("author_history", fetched_at="2026-09-01T00:06:00+00:00",
                                          key="acme__mrfirst")},
        confidence={"mr_first_rate": "derived"},
    )
    bundle = load(tmp_path, doc, key="acme__mrfirst")
    assert bundle.inputs["mr_first_rate"] == Measured(
        value=0.6, n=5, window_start=None, window_end=None, method="primary",
        source="raw/author_history/acme__mrfirst.json",
        source_hash=expected_hash(ah_data), fetched_at="2026-09-01T00:06:00+00:00",
    )
    # X11 ( review round 2): this is the ONE measured field in this
    # minimal document, so provenance_hash must fold exactly this one
    # author_history envelope -- proving the resolver's ctx.referenced[...]
    # write actually reaches FeatureBundle.provenance_hash, not just the
    # resolver's own return value.
    assert bundle.provenance_hash == feature_provenance_hash({
        "raw/author_history/acme__mrfirst.json": expected_hash(ah_data),
    })


def test_mr_first_rate_insufficient_n(tmp_path: Path) -> None:
    """A real, "derived" value with a count below PARAM_MIN_N (5, per
    constants.PARAM_MIN_N['mr_first_rate']) is insufficient_n, not
    Measured on too little evidence and not collector_failure (the
    collector did its job; there just were not enough authors)."""
    ah_data = author_history_data()
    write_envelope(tmp_path / "raw", "author_history", "acme__mrfirst-low-n", ah_data, "2026-09-01T00:06:00+00:00")
    doc = base_document(
        meta={"mr_first_rate": 1.0, "mr_first_pr_count": 3},
        provenance={"mr_first_rate": prov("author_history", fetched_at="2026-09-01T00:06:00+00:00",
                                          key="acme__mrfirst-low-n")},
        confidence={"mr_first_rate": "derived"},
    )
    bundle = load(tmp_path, doc, key="acme__mrfirst-low-n")
    assert bundle.inputs["mr_first_rate"] == Unmeasured(
        reason="insufficient_n", detail="3 PRs in the D-IN-3-adjusted cohort, need >= 5",
    )


def test_mr_first_rate_zero_cohort_is_insufficient_n_not_collector_failure(tmp_path: Path) -> None:
    """derive_cohort_fields' own ``_ratio`` returns ``None`` for a
    zero-denominator cohort even under a "derived" (gate-not-fired)
    confidence -- this must read as insufficient_n (a real, checked, empty
    cohort), not collector_failure (which would wrongly suggest the
    collector itself malfunctioned)."""
    doc = base_document(
        meta={"mr_first_rate": None, "mr_first_pr_count": 0},
        provenance={"mr_first_rate": prov("author_history", key="acme__mrfirst-zero")},
        confidence={"mr_first_rate": "derived"},
    )
    bundle = load(tmp_path, doc, key="acme__mrfirst-zero")
    assert bundle.inputs["mr_first_rate"] == Unmeasured(
        reason="insufficient_n", detail="0 PRs in the D-IN-3-adjusted cohort, need >= 5",
    )


def test_mr_first_rate_collector_failure_gate_fired(tmp_path: Path) -> None:
    """confidence "missing" (derive_cohort_fields' AUTHOR_UNMEASURED_MAX_FRACTION
    gate fired): Unmeasured(collector_failure) with the REAL, dynamic detail
    from meta.cohort_unmeasured_detail -- never the static unset_detail,
    which would hide that a collector FAILURE (not an absent collector) is
    why this row has no value (review C1e)."""
    doc = base_document(
        meta={"mr_first_rate": None, "mr_first_pr_count": None,
              "cohort_unmeasured_detail": "12 of 20 authors unmeasured"},
        provenance={"mr_first_rate": prov("author_history", key="acme__mrfirst-gated")},
        confidence={"mr_first_rate": "missing"},
    )
    bundle = load(tmp_path, doc, key="acme__mrfirst-gated")
    assert bundle.inputs["mr_first_rate"] == Unmeasured(
        reason="collector_failure", detail="12 of 20 authors unmeasured",
    )


def test_mr_first_rate_unset_when_no_author_history_collected(tmp_path: Path) -> None:
    """No confidence entry at all (derive_cohort_fields returned {} because
    no author_history envelope was ever collected for this repo, or a v1
    run predates ): the STATIC unset_detail, matching the old
    _unmapped(...) behavior byte for byte (an over-correction guard: this
    row must not start reporting collector_failure with an EMPTY detail,
    or crash, just because the field is genuinely absent)."""
    bundle = load(tmp_path, base_document(), key="acme__mrfirst-unset")
    assert bundle.inputs["mr_first_rate"] == Unmeasured(
        reason="collector_failure",
        detail="needs W1-4 author history; the v1 pipeline collected none",
    )


def test_mr_first_rate_unrecognized_confidence(tmp_path: Path) -> None:
    doc = base_document(
        meta={"mr_first_rate": 0.5, "mr_first_pr_count": 5},
        confidence={"mr_first_rate": "verified"},
    )
    bundle = load(tmp_path, doc, key="acme__mrfirst-bad-conf")
    assert bundle.inputs["mr_first_rate"] == Unmeasured(
        reason="collector_failure",
        detail="v1 confidence 'verified' for 'mr_first_rate' is not recognized",
    )


def test_newcomer_conversion(tmp_path: Path) -> None:
    ah_data = author_history_data()
    write_envelope(tmp_path / "raw", "author_history", "acme__newconv", ah_data, "2026-09-01T00:06:00+00:00")
    doc = base_document(
        meta={"newcomer_conversion": 0.25, "newcomer_conversion_author_count": 8},
        provenance={"newcomer_conversion": prov("author_history", fetched_at="2026-09-01T00:06:00+00:00",
                                                key="acme__newconv")},
        confidence={"newcomer_conversion": "derived"},
    )
    bundle = load(tmp_path, doc, key="acme__newconv")
    assert bundle.inputs["newcomer_conversion"] == Measured(
        value=0.25, n=8, window_start=None, window_end=None, method="primary",
        source="raw/author_history/acme__newconv.json",
        source_hash=expected_hash(ah_data), fetched_at="2026-09-01T00:06:00+00:00",
    )
    assert bundle.provenance_hash == feature_provenance_hash({
        "raw/author_history/acme__newconv.json": expected_hash(ah_data),
    })

    # Same "missing"-gate wiring, its OWN detail field (newcomer_unmeasured_detail).
    gated_doc = base_document(
        meta={"newcomer_conversion": None, "newcomer_conversion_author_count": None,
              "newcomer_unmeasured_detail": "9 of 15 newcomer authors unmeasured"},
        confidence={"newcomer_conversion": "missing"},
    )
    gated_bundle = load(tmp_path, gated_doc, key="acme__newconv-gated")
    assert gated_bundle.inputs["newcomer_conversion"] == Unmeasured(
        reason="collector_failure", detail="9 of 15 newcomer authors unmeasured",
    )

    unset_bundle = load(tmp_path, base_document(), key="acme__newconv-unset")
    assert unset_bundle.inputs["newcomer_conversion"] == Unmeasured(
        reason="collector_failure",
        detail="needs the W1-4 newcomer cohort; the v1 pipeline collected none",
    )


def test_rr90_return_rate(tmp_path: Path) -> None:
    ah_data = author_history_data()
    write_envelope(tmp_path / "raw", "author_history", "acme__rr90", ah_data, "2026-09-01T00:06:00+00:00")
    doc = base_document(
        meta={"rr90_return_rate": 0.75, "rr90_return_author_count": 12},
        provenance={"rr90_return_rate": prov("author_history", fetched_at="2026-09-01T00:06:00+00:00",
                                             key="acme__rr90")},
        confidence={"rr90_return_rate": "derived"},
    )
    bundle = load(tmp_path, doc, key="acme__rr90")
    assert bundle.inputs["rr90_return_rate"] == Measured(
        value=0.75, n=12, window_start=None, window_end=None, method="primary",
        source="raw/author_history/acme__rr90.json",
        source_hash=expected_hash(ah_data), fetched_at="2026-09-01T00:06:00+00:00",
    )
    assert bundle.provenance_hash == feature_provenance_hash({
        "raw/author_history/acme__rr90.json": expected_hash(ah_data),
    })

    # rr90_return_rate shares newcomer_conversion's own "missing"-gate detail
    # field (both rows are gated on the SAME sampled-newcomer-roster fraction
    # in derive_cohort_fields, so this is the correct field, not a typo).
    gated_doc = base_document(
        meta={"rr90_return_rate": None, "rr90_return_author_count": None,
              "newcomer_unmeasured_detail": "9 of 15 newcomer authors unmeasured"},
        confidence={"rr90_return_rate": "missing"},
    )
    gated_bundle = load(tmp_path, gated_doc, key="acme__rr90-gated")
    assert gated_bundle.inputs["rr90_return_rate"] == Unmeasured(
        reason="collector_failure", detail="9 of 15 newcomer authors unmeasured",
    )

    unset_bundle = load(tmp_path, base_document(), key="acme__rr90-unset")
    assert unset_bundle.inputs["rr90_return_rate"] == Unmeasured(
        reason="collector_failure",
        detail="v2 5.3 is cohort-based (W1-4); v1 rr_90 approximates author gaps",
    )


# --------------------------------------------------------------------------- #
# 1.2 dr90_decision_rate (, D-IN-4 amendment 2026-09-13)                 #
#                                                                              #
# All fixtures share the same pr_window envelope anchor as the ttfhr/ttm_p90  #
# fixtures above ("2026-09-01T00:00:00+00:00", ``pr_window_data``'s default), #
# so the cohort bounds are fixed throughout: cohort_start = anchor-180d =     #
# "2026-03-05T00:00:00+00:00" (the same window_start ttfhr pins above) and    #
# cohort_end = anchor-90d = "2026-06-03T00:00:00+00:00".                      #
# --------------------------------------------------------------------------- #

_DR90_WINDOW_START = "2026-03-05T00:00:00+00:00"
_DR90_WINDOW_END = "2026-06-03T00:00:00+00:00"
#: "proxy:din3_unapplied" when no author_history envelope was collected
#: for the repo (D-IN-3's eligibility gate needs the per-author event
#: count pr_window alone does not carry,  review W-1/W-2); "primary"
#: once an author_history envelope makes the gate applicable too. See
#: porchlamp/derive/bundle.py's _DR90_METHOD_PRIMARY/_DR90_METHOD_GATE_UNAPPLIED.
_DR90_METHOD = "proxy:din3_unapplied"
_DR90_METHOD_GATED = "primary"

#: Ten external PRs opened 100-109 d before the anchor (comfortably inside
#: the cohort, away from every boundary tested separately below). Decision
#: outcomes are chosen to exercise every path _dr90_decision_cohort branches
#: on: #100 decided by mergedAt (20 d, well inside 90), #101 decided by
#: closedAt ALONE (mergedAt absent -- kills an "ignores closedAt" mutant),
#: #102 decided at exactly 90 d (the decision-window's own inclusive
#: boundary), #103 NOT decided at 91 d (one day over), #104 decided at 30 d,
#: #105-107 still open (no mergedAt/closedAt at all), #108 eventually merged
#: but 200 d later (long past the 90 d decision window -- a late merge is
#: not a fast decision), #109 still open. Decided: 100, 101, 102, 104 = 4 of
#: 10, r = 0.4.
_DR90_PRS = [
    {"number": 100, "createdAt": "2026-05-24T00:00:00+00:00", "mergedAt": "2026-06-13T00:00:00+00:00"},
    {"number": 101, "createdAt": "2026-05-23T00:00:00+00:00", "closedAt": "2026-06-07T00:00:00+00:00"},
    {"number": 102, "createdAt": "2026-05-22T00:00:00+00:00", "mergedAt": "2026-08-20T00:00:00+00:00"},
    {"number": 103, "createdAt": "2026-05-21T00:00:00+00:00", "mergedAt": "2026-08-20T00:00:00+00:00"},
    {"number": 104, "createdAt": "2026-05-20T00:00:00+00:00", "mergedAt": "2026-06-19T00:00:00+00:00"},
    {"number": 105, "createdAt": "2026-05-19T00:00:00+00:00"},
    {"number": 106, "createdAt": "2026-05-18T00:00:00+00:00"},
    {"number": 107, "createdAt": "2026-05-17T00:00:00+00:00"},
    {"number": 108, "createdAt": "2026-05-16T00:00:00+00:00", "mergedAt": "2026-12-02T00:00:00+00:00"},
    {"number": 109, "createdAt": "2026-05-15T00:00:00+00:00"},
]
_DR90_DERIVED = [{"number": n, "is_external": True} for n in range(100, 110)]


def test_dr90_decision_rate(tmp_path: Path) -> None:
    data = pr_window_data(prs=_DR90_PRS, derived=_DR90_DERIVED)
    write_envelope(tmp_path / "raw", "pr_window", "acme__widget", data, "2026-09-01T00:05:00+00:00")
    doc = base_document(provenance={"pr_window_count": prov("pr_window")})
    bundle = load(tmp_path, doc)
    assert bundle.inputs["dr90_decision_rate"] == Measured(
        value=0.4, n=10, window_start=_DR90_WINDOW_START, window_end=_DR90_WINDOW_END,
        method=_DR90_METHOD, source="raw/pr_window/acme__widget.json",
        source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
    )

    # Negative: no pr_window envelope at all ( hook never ran here).
    no_envelope_bundle = load(tmp_path, base_document(), key="acme__no-window")
    assert no_envelope_bundle.inputs["dr90_decision_rate"] == Unmeasured(
        reason="collector_failure", detail=PR_WINDOW_UNRESOLVABLE_DETAIL,
    )


#: The cohort-membership boundary fixture: PRs opened 89/90/91/179/180/181 d
#: before the anchor, all external, plus an internal PR opened 150 d before
#: the anchor (inside the date range but must be excluded by is_external)
#: and an external PR opened 250 d before the anchor (well outside either
#: edge). Only 91/179/180 fall in the half-open cohort window
#: [anchor-180d, anchor-90d): 89 and 90 are too recent (not yet owed a full
#: 90 d to be decided), 181 is older than the collected 180 d window, 150's
#: internal author excludes it regardless of its in-range date, and 250 is
#: far outside either edge. Decision outcomes are irrelevant here (left
#: open) since n=3 is below PARAM_MIN_N and never surfaces r.
_DR90_BOUNDARY_PRS = [
    {"number": 89, "createdAt": "2026-06-04T00:00:00+00:00"},
    {"number": 90, "createdAt": "2026-06-03T00:00:00+00:00"},
    {"number": 91, "createdAt": "2026-06-02T00:00:00+00:00"},
    {"number": 179, "createdAt": "2026-03-06T00:00:00+00:00"},
    {"number": 180, "createdAt": "2026-03-05T00:00:00+00:00"},
    {"number": 181, "createdAt": "2026-03-04T00:00:00+00:00"},
    {"number": 150, "createdAt": "2026-04-04T00:00:00+00:00"},  # internal, in-range date
    {"number": 250, "createdAt": "2025-12-25T00:00:00+00:00"},  # external, far outside
]
_DR90_BOUNDARY_DERIVED = [
    {"number": 89, "is_external": True},
    {"number": 90, "is_external": True},
    {"number": 91, "is_external": True},
    {"number": 179, "is_external": True},
    {"number": 180, "is_external": True},
    {"number": 181, "is_external": True},
    {"number": 150, "is_external": False},
    {"number": 250, "is_external": True},
]


def test_dr90_decision_rate_cohort_window_boundaries(tmp_path: Path) -> None:
    """Pins the half-open cohort interval's exact edges and the is_external
    filter in one fixture: only PR numbers 91, 179 and 180 (opened 91/179/
    180 d before the anchor) belong to the cohort, so n=3, below
    PARAM_MIN_N=10 -- an off-by-one on either edge, or a dropped
    is_external check (which would additionally admit the internal #150),
    changes the exact count named in this Unmeasured detail."""
    data = pr_window_data(prs=_DR90_BOUNDARY_PRS, derived=_DR90_BOUNDARY_DERIVED)
    write_envelope(tmp_path / "raw", "pr_window", "acme__boundary", data, "2026-09-01T00:05:00+00:00")
    doc = base_document(provenance={"pr_window_count": prov("pr_window", key="acme__boundary")})
    bundle = load(tmp_path, doc, key="acme__boundary")
    assert bundle.inputs["dr90_decision_rate"] == Unmeasured(
        reason="insufficient_n",
        detail="3 external PRs in the decision cohort (3 before the D-IN-3 per-author cap), need >= 10",
    )


#: Eight valid, comfortably in-cohort external PRs plus one external PR with
#: no createdAt at all and one with a createdAt that does not parse as
#: ISO-8601 -- both must be dropped from the cohort (n=8, not 10) and both
#: counted in the insufficient_n detail, not silently absorbed into a wrong
#: n.
_DR90_MALFORMED_PRS = [
    {"number": 100, "createdAt": "2026-05-24T00:00:00+00:00"},
    {"number": 101, "createdAt": "2026-05-23T00:00:00+00:00"},
    {"number": 102, "createdAt": "2026-05-22T00:00:00+00:00"},
    {"number": 103, "createdAt": "2026-05-21T00:00:00+00:00"},
    {"number": 104, "createdAt": "2026-05-20T00:00:00+00:00"},
    {"number": 105, "createdAt": "2026-05-19T00:00:00+00:00"},
    {"number": 106, "createdAt": "2026-05-18T00:00:00+00:00"},
    {"number": 107, "createdAt": "2026-05-17T00:00:00+00:00"},
    {"number": 900, "createdAt": None},
    {"number": 901, "createdAt": "not-a-timestamp"},
]
_DR90_MALFORMED_DERIVED = [{"number": n, "is_external": True} for n in (100, 101, 102, 103, 104, 105, 106, 107, 900, 901)]


def test_dr90_decision_rate_malformed_created_at_excluded_and_named(tmp_path: Path) -> None:
    data = pr_window_data(prs=_DR90_MALFORMED_PRS, derived=_DR90_MALFORMED_DERIVED)
    write_envelope(tmp_path / "raw", "pr_window", "acme__malformed", data, "2026-09-01T00:05:00+00:00")
    doc = base_document(provenance={"pr_window_count": prov("pr_window", key="acme__malformed")})
    bundle = load(tmp_path, doc, key="acme__malformed")
    assert bundle.inputs["dr90_decision_rate"] == Unmeasured(
        reason="insufficient_n",
        detail="8 external PRs in the decision cohort (8 before the D-IN-3 per-author cap), need >= 10 "
               "(2 excluded: malformed or missing createdAt)",
    )


# --------------------------------------------------------------------------- #
# 1.2 dr90_decision_rate -- D-IN-3 per-author cap ( review W-1)          #
# --------------------------------------------------------------------------- #

#: The reviewer's own sock-puppet oracle (-review.md C2b), resized
#: ( review round 2, B-1) so the CAPPED population clears
#: PARAM_MIN_N=10: a single login files 12 of a 20-PR cohort, all 12
#: fast-merged (self-approved); the other 8 honest single-PR authors are
#: all still open. Uncapped this reads r=0.6 -- a throwaway account can
#: buy an arbitrarily high DR_90 by flooding its own trivially-fast-merged
#: PRs. The D-IN-3 cap (``AUTHOR_CAP_FRACTION=0.20``) keeps only the
#: sock's 4 OLDEST of 12 PRs (``floor(0.20 * 20) == 4``); all 4 are still
#: decided, the 8 honest ones are not, so the capped population is 12
#: (4 sock + 8 honest) with 4 decided: r = 4/12 = 0.3333, not 0.6. This is
#: also the test that pins PARAM_MIN_N being checked against the CAPPED
#: 12-PR population (review B-1: the SAME number ``Measured.n`` reports,
#: and therefore the same number ``porchlamp.core.params.curves._apply``
#: re-checks when the record is scored) -- not the raw 20, and not zero
#: (the pre-round-2 bug would have checked min_n against the raw cohort
#: while reporting the capped n, so the record here and the param-layer
#: re-check would have used different numbers).
_DR90_SOCK_PRS = [
    # The sock puppet's 12 PRs, oldest first; only the oldest 4 survive
    # the cap. All 12 are fast-merged (5 d) so an off-by-one that kept a
    # 5th would also change the decided count, not just n.
    {"number": 500, "createdAt": "2026-03-06T00:00:00+00:00", "mergedAt": "2026-03-11T00:00:00+00:00",
     "author": {"login": "sock"}},
    {"number": 501, "createdAt": "2026-03-08T00:00:00+00:00", "mergedAt": "2026-03-13T00:00:00+00:00",
     "author": {"login": "sock"}},
    {"number": 502, "createdAt": "2026-03-10T00:00:00+00:00", "mergedAt": "2026-03-15T00:00:00+00:00",
     "author": {"login": "sock"}},
    {"number": 503, "createdAt": "2026-03-12T00:00:00+00:00", "mergedAt": "2026-03-17T00:00:00+00:00",
     "author": {"login": "sock"}},
    {"number": 504, "createdAt": "2026-03-14T00:00:00+00:00", "mergedAt": "2026-03-19T00:00:00+00:00",
     "author": {"login": "sock"}},
    {"number": 505, "createdAt": "2026-03-16T00:00:00+00:00", "mergedAt": "2026-03-21T00:00:00+00:00",
     "author": {"login": "sock"}},
    {"number": 506, "createdAt": "2026-03-18T00:00:00+00:00", "mergedAt": "2026-03-23T00:00:00+00:00",
     "author": {"login": "sock"}},
    {"number": 507, "createdAt": "2026-03-20T00:00:00+00:00", "mergedAt": "2026-03-25T00:00:00+00:00",
     "author": {"login": "sock"}},
    {"number": 508, "createdAt": "2026-03-22T00:00:00+00:00", "mergedAt": "2026-03-27T00:00:00+00:00",
     "author": {"login": "sock"}},
    {"number": 509, "createdAt": "2026-03-24T00:00:00+00:00", "mergedAt": "2026-03-29T00:00:00+00:00",
     "author": {"login": "sock"}},
    {"number": 510, "createdAt": "2026-03-26T00:00:00+00:00", "mergedAt": "2026-03-31T00:00:00+00:00",
     "author": {"login": "sock"}},
    {"number": 511, "createdAt": "2026-03-28T00:00:00+00:00", "mergedAt": "2026-04-02T00:00:00+00:00",
     "author": {"login": "sock"}},
    # Eight honest, distinct single-PR authors, all still open (not decided).
    {"number": 512, "createdAt": "2026-04-01T00:00:00+00:00", "author": {"login": "honest-a"}},
    {"number": 513, "createdAt": "2026-04-05T00:00:00+00:00", "author": {"login": "honest-b"}},
    {"number": 514, "createdAt": "2026-04-09T00:00:00+00:00", "author": {"login": "honest-c"}},
    {"number": 515, "createdAt": "2026-04-13T00:00:00+00:00", "author": {"login": "honest-d"}},
    {"number": 516, "createdAt": "2026-04-17T00:00:00+00:00", "author": {"login": "honest-e"}},
    {"number": 517, "createdAt": "2026-04-21T00:00:00+00:00", "author": {"login": "honest-f"}},
    {"number": 518, "createdAt": "2026-04-25T00:00:00+00:00", "author": {"login": "honest-g"}},
    {"number": 519, "createdAt": "2026-04-29T00:00:00+00:00", "author": {"login": "honest-h"}},
]
_DR90_SOCK_DERIVED = [{"number": n, "is_external": True} for n in range(500, 520)]


def test_dr90_decision_rate_author_cap_sock_puppet(tmp_path: Path) -> None:
    data = pr_window_data(prs=_DR90_SOCK_PRS, derived=_DR90_SOCK_DERIVED)
    write_envelope(tmp_path / "raw", "pr_window", "acme__sock", data, "2026-09-01T00:05:00+00:00")
    doc = base_document(provenance={"pr_window_count": prov("pr_window", key="acme__sock")})
    bundle = load(tmp_path, doc, key="acme__sock")
    assert bundle.inputs["dr90_decision_rate"] == Measured(
        value=0.3333, n=12, window_start=_DR90_WINDOW_START, window_end=_DR90_WINDOW_END,
        method=_DR90_METHOD, source="raw/pr_window/acme__sock.json",
        source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
    )
    # The engine's own scoring path, not just this bundle-layer resolver
    # (review B-1: "the tests that prove W-1 must assert the ParamScore,
    # not the resolver's return value" -- this is what let the original
    # bug ship). fill_rate_score(0.3333) = min(100, 125*0.3333) = 41.6625.
    scored = dr90(bundle.inputs["dr90_decision_rate"])
    assert scored.measured is True
    assert scored.reason is None
    assert scored.n == 12
    assert scored.score == round(min(100.0, 125.0 * 0.3333), 4)


#: Same 20-PR shape as above but the dominant login holds exactly
#: ``allowed + 1`` (5, one over the ``floor(0.20 * 20) == 4`` cap) instead
#: of 12: proves the cap drops the SINGLE excess PR (most recent first),
#: not zero and not all five, and that it is evaluated over the exact `n`
#: of THIS cohort (an off-by-one on the floor -- e.g. rounding instead of
#: truncating -- would keep all 5 and produce a different rate).
_DR90_CAP_BOUNDARY_PRS = [
    {"number": 520, "createdAt": "2026-03-06T00:00:00+00:00", "mergedAt": "2026-03-11T00:00:00+00:00",
     "author": {"login": "over-cap"}},          # oldest of the 5 -- kept, decided
    {"number": 521, "createdAt": "2026-03-08T00:00:00+00:00",
     "author": {"login": "over-cap"}},          # kept, NOT decided
    {"number": 522, "createdAt": "2026-03-10T00:00:00+00:00",
     "author": {"login": "over-cap"}},          # kept, NOT decided
    {"number": 523, "createdAt": "2026-03-12T00:00:00+00:00",
     "author": {"login": "over-cap"}},          # kept, NOT decided
    {"number": 524, "createdAt": "2026-03-14T00:00:00+00:00", "mergedAt": "2026-03-19T00:00:00+00:00",
     "author": {"login": "over-cap"}},          # most recent -- dropped by the cap, decided
    {"number": 525, "createdAt": "2026-04-01T00:00:00+00:00", "author": {"login": "honest-1"}},
    {"number": 526, "createdAt": "2026-04-03T00:00:00+00:00", "author": {"login": "honest-2"}},
    {"number": 527, "createdAt": "2026-04-05T00:00:00+00:00", "author": {"login": "honest-3"}},
    {"number": 528, "createdAt": "2026-04-07T00:00:00+00:00", "author": {"login": "honest-4"}},
    {"number": 529, "createdAt": "2026-04-09T00:00:00+00:00", "author": {"login": "honest-5"}},
    {"number": 530, "createdAt": "2026-04-11T00:00:00+00:00", "author": {"login": "honest-6"}},
    {"number": 531, "createdAt": "2026-04-13T00:00:00+00:00", "author": {"login": "honest-7"}},
    {"number": 532, "createdAt": "2026-04-15T00:00:00+00:00", "author": {"login": "honest-8"}},
    {"number": 533, "createdAt": "2026-04-17T00:00:00+00:00", "author": {"login": "honest-9"}},
    {"number": 534, "createdAt": "2026-04-19T00:00:00+00:00", "author": {"login": "honest-10"}},
    {"number": 535, "createdAt": "2026-04-21T00:00:00+00:00", "author": {"login": "honest-11"}},
    {"number": 536, "createdAt": "2026-04-23T00:00:00+00:00", "author": {"login": "honest-12"}},
    {"number": 537, "createdAt": "2026-04-25T00:00:00+00:00", "author": {"login": "honest-13"}},
    {"number": 538, "createdAt": "2026-04-27T00:00:00+00:00", "author": {"login": "honest-14"}},
    {"number": 539, "createdAt": "2026-04-29T00:00:00+00:00", "author": {"login": "honest-15"}},
]
_DR90_CAP_BOUNDARY_DERIVED = [{"number": n, "is_external": True} for n in range(520, 540)]


def test_dr90_decision_rate_author_cap_boundary(tmp_path: Path) -> None:
    """20 raw PRs, ``allowed = floor(0.20 * 20) == 4``: the dominant
    author's 5th (most recent) PR is dropped, its oldest 4 survive, so the
    capped cohort is 19 (4 + 15 honest), not 20 and not 15. Only 1 of the
    kept PRs is decided (the dominant author's oldest): r = 1/19 = 0.0526.
    A cap that kept all 5 would report n=20, r=2/20=0.1 (the dropped PR
    #524 is decided); a cap that dropped all 5 would report n=15, r=0/15=0.0."""
    data = pr_window_data(prs=_DR90_CAP_BOUNDARY_PRS, derived=_DR90_CAP_BOUNDARY_DERIVED)
    write_envelope(tmp_path / "raw", "pr_window", "acme__cap-boundary", data, "2026-09-01T00:05:00+00:00")
    doc = base_document(provenance={"pr_window_count": prov("pr_window", key="acme__cap-boundary")})
    bundle = load(tmp_path, doc, key="acme__cap-boundary")
    assert bundle.inputs["dr90_decision_rate"] == Measured(
        value=0.0526, n=19, window_start=_DR90_WINDOW_START, window_end=_DR90_WINDOW_END,
        method=_DR90_METHOD, source="raw/pr_window/acme__cap-boundary.json",
        source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
    )
    scored = dr90(bundle.inputs["dr90_decision_rate"])
    assert scored.measured is True
    assert scored.reason is None
    assert scored.n == 19


#: A REAL sock puppet large enough to dominate even after the cap floor
#: (review B-1: "insufficient_n keeps a detail naming raw and capped
#: counts"): 12 raw PRs, one login holds 9 of them. ``floor(0.20*12)==2``,
#: so the cap keeps 2 (that login) + 3 honest = 5, still below
#: PARAM_MIN_N=10 -- a genuinely insufficient capped population, not the
#: degenerate "nobody dominates" case ``apply_author_cap`` no longer
#: zeroes. The detail names BOTH the capped 5 and the raw 12, so a human
#: reading the bundle can tell a real cap bind from a merely small sample.
_DR90_CAP_INSUFFICIENT_PRS = [
    *[{"number": 540 + i, "createdAt": f"2026-03-{6 + i:02d}T00:00:00+00:00",
       "author": {"login": "mega-sock"}} for i in range(9)],
    {"number": 549, "createdAt": "2026-04-01T00:00:00+00:00", "author": {"login": "honest-1"}},
    {"number": 550, "createdAt": "2026-04-05T00:00:00+00:00", "author": {"login": "honest-2"}},
    {"number": 551, "createdAt": "2026-04-09T00:00:00+00:00", "author": {"login": "honest-3"}},
]
_DR90_CAP_INSUFFICIENT_DERIVED = [{"number": n, "is_external": True} for n in range(540, 552)]


def test_dr90_decision_rate_cap_reduces_below_min_n(tmp_path: Path) -> None:
    data = pr_window_data(prs=_DR90_CAP_INSUFFICIENT_PRS, derived=_DR90_CAP_INSUFFICIENT_DERIVED)
    write_envelope(tmp_path / "raw", "pr_window", "acme__cap-insufficient", data, "2026-09-01T00:05:00+00:00")
    doc = base_document(provenance={"pr_window_count": prov("pr_window", key="acme__cap-insufficient")})
    bundle = load(tmp_path, doc, key="acme__cap-insufficient")
    assert bundle.inputs["dr90_decision_rate"] == Unmeasured(
        reason="insufficient_n",
        detail="5 external PRs in the decision cohort (12 before the D-IN-3 per-author cap), need >= 10",
    )
    scored = dr90(bundle.inputs["dr90_decision_rate"])
    assert scored.measured is False
    assert scored.reason == "insufficient_n"


# --------------------------------------------------------------------------- #
# 1.2 dr90_decision_rate -- D-IN-3 eligibility gate join against            #
# author_history ( review W-1/W-2): present vs. absent for the same    #
# 14-PR cohort, pinning both the value AND the "method" tag each case gets. #
# --------------------------------------------------------------------------- #

#: 14 distinct single-PR authors (the cap is a no-op here -- nobody has
#: more than 1 PR -- so this fixture isolates the GATE's effect from the
#: cap's). 10 are genuinely D-IN-3-eligible (account >= 90 d old at PR
#: creation, >= 5 public events); 4 are not (2 too-new accounts, 2 with
#: too few events), and ALL FOUR of those ineligible authors' PRs are
#: fast-merged -- a throwaway-account flood that only the eligibility gate
#: (not the cap) can catch, since no single one of them holds more than
#: its own 1 PR. Of the 10 eligible authors, 4 are decided and 6 are not.
_DR90_GATE_PRS = [
    {"number": 300, "createdAt": "2026-04-01T00:00:00+00:00", "mergedAt": "2026-04-06T00:00:00+00:00",
     "author": {"login": "eligible-1"}},
    {"number": 301, "createdAt": "2026-04-01T00:00:00+00:00", "mergedAt": "2026-04-06T00:00:00+00:00",
     "author": {"login": "eligible-2"}},
    {"number": 302, "createdAt": "2026-04-01T00:00:00+00:00", "mergedAt": "2026-04-06T00:00:00+00:00",
     "author": {"login": "eligible-3"}},
    {"number": 303, "createdAt": "2026-04-01T00:00:00+00:00", "mergedAt": "2026-04-06T00:00:00+00:00",
     "author": {"login": "eligible-4"}},
    {"number": 304, "createdAt": "2026-04-01T00:00:00+00:00", "author": {"login": "eligible-5"}},
    {"number": 305, "createdAt": "2026-04-01T00:00:00+00:00", "author": {"login": "eligible-6"}},
    {"number": 306, "createdAt": "2026-04-01T00:00:00+00:00", "author": {"login": "eligible-7"}},
    {"number": 307, "createdAt": "2026-04-01T00:00:00+00:00", "author": {"login": "eligible-8"}},
    {"number": 308, "createdAt": "2026-04-01T00:00:00+00:00", "author": {"login": "eligible-9"}},
    {"number": 309, "createdAt": "2026-04-01T00:00:00+00:00", "author": {"login": "eligible-10"}},
    {"number": 310, "createdAt": "2026-04-01T00:00:00+00:00", "mergedAt": "2026-04-06T00:00:00+00:00",
     "author": {"login": "too-new-1"}},
    {"number": 311, "createdAt": "2026-04-01T00:00:00+00:00", "mergedAt": "2026-04-06T00:00:00+00:00",
     "author": {"login": "too-new-2"}},
    {"number": 312, "createdAt": "2026-04-01T00:00:00+00:00", "mergedAt": "2026-04-06T00:00:00+00:00",
     "author": {"login": "few-events-1"}},
    {"number": 313, "createdAt": "2026-04-01T00:00:00+00:00", "mergedAt": "2026-04-06T00:00:00+00:00",
     "author": {"login": "few-events-2"}},
]
_DR90_GATE_DERIVED = [{"number": n, "is_external": True} for n in range(300, 314)]

_DR90_GATE_AUTHOR_RECORDS = [
    *[{"login": f"eligible-{i}", "account_created_at": "2025-12-01T00:00:00+00:00",
       "public_event_count": 10, "eligibility_unmeasured": False} for i in range(1, 11)],
    {"login": "too-new-1", "account_created_at": "2026-03-25T00:00:00+00:00",
     "public_event_count": 10, "eligibility_unmeasured": False},
    {"login": "too-new-2", "account_created_at": "2026-03-25T00:00:00+00:00",
     "public_event_count": 10, "eligibility_unmeasured": False},
    {"login": "few-events-1", "account_created_at": "2025-01-01T00:00:00+00:00",
     "public_event_count": 2, "eligibility_unmeasured": False},
    {"login": "few-events-2", "account_created_at": "2025-01-01T00:00:00+00:00",
     "public_event_count": 2, "eligibility_unmeasured": False},
]


def test_dr90_decision_rate_eligibility_gate_applied_with_author_history(tmp_path: Path) -> None:
    """author_history IS collected for this repo: the gate keeps only the
    10 D-IN-3-eligible authors (the 4 too-new/too-few-events ones are
    dropped even though each holds only 1 PR, below the cap), raw=capped=10
    (the cap is a no-op -- one PR per surviving author). 4 of the 10 kept
    are decided: r = 4/10 = 0.4. ``method`` is "primary": both the cap AND
    the gate applied."""
    data = pr_window_data(prs=_DR90_GATE_PRS, derived=_DR90_GATE_DERIVED)
    write_envelope(tmp_path / "raw", "pr_window", "acme__gated", data, "2026-09-01T00:05:00+00:00")
    ah_data = author_history_data(author_records=_DR90_GATE_AUTHOR_RECORDS)
    write_envelope(tmp_path / "raw", "author_history", "acme__gated", ah_data, "2026-09-01T00:06:00+00:00")
    doc = base_document(
        provenance={
            "pr_window_count": prov("pr_window", key="acme__gated"),
            "author_history_cost_points": prov("author_history", fetched_at="2026-09-01T00:06:00+00:00",
                                               key="acme__gated"),
        },
    )
    bundle = load(tmp_path, doc, key="acme__gated")
    assert bundle.inputs["dr90_decision_rate"] == Measured(
        value=0.4, n=10, window_start=_DR90_WINDOW_START, window_end=_DR90_WINDOW_END,
        method=_DR90_METHOD_GATED, source="raw/pr_window/acme__gated.json",
        source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
    )


def test_dr90_decision_rate_eligibility_gate_unapplied_without_author_history(tmp_path: Path) -> None:
    """Identical 14-PR ``pr_window`` data as the previous test, but NO
    ``author_history`` envelope was collected for this repo (the  hook
    never ran here): the eligibility gate cannot be applied (``pr_window``
    alone carries no public-event count), so all 14 PRs count. The cap is
    still a no-op (one PR per author). All 4 "throwaway" authors' PRs are
    decided in addition to the 4 real ones: r = 8/14 = 0.5714, n=14 --
    both DIFFERENT from the gated test above, proving the gate (not just
    the cap) changes the reported rate. ``method`` is
    "proxy:din3_unapplied"."""
    data = pr_window_data(prs=_DR90_GATE_PRS, derived=_DR90_GATE_DERIVED)
    write_envelope(tmp_path / "raw", "pr_window", "acme__ungated", data, "2026-09-01T00:05:00+00:00")
    doc = base_document(provenance={"pr_window_count": prov("pr_window", key="acme__ungated")})
    bundle = load(tmp_path, doc, key="acme__ungated")
    assert bundle.inputs["dr90_decision_rate"] == Measured(
        value=0.5714, n=14, window_start=_DR90_WINDOW_START, window_end=_DR90_WINDOW_END,
        method=_DR90_METHOD, source="raw/pr_window/acme__ungated.json",
        source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
    )


#: One eleventh cohort author's ``author_history`` record has a failed
#: fetch (``eligibility_unmeasured``); D-IN-3 tri-state discipline (B-3/
#: F-3) says this must be excluded from BOTH numerator and denominator,
#: never read as either eligible or confirmed-ineligible. Ten OTHER
#: authors are cleanly eligible, so the gated (post-exclusion) cohort is
#: exactly 10 -- still >= PARAM_MIN_N -- proving the unmeasured exclusion
#: is folded into the count :func:`_resolve_dr90_decision_rate` checks
#: ``min_n`` against, not silently ignored (a bug here would either count
#: 11 raw PRs, or read the unmeasured author as ineligible and still
#: report 10 by coincidence but with the wrong decided count if that
#: author's own PR were merged, which it is here).
def _clean_author_pr(i: int) -> dict[str, Any]:
    pr: dict[str, Any] = {"number": 400 + i, "createdAt": "2026-04-01T00:00:00+00:00",
                          "author": {"login": f"clean-{i}"}}
    if i < 5:
        pr["mergedAt"] = "2026-04-06T00:00:00+00:00"
    return pr


_DR90_UNMEASURED_PRS = [
    *[_clean_author_pr(i) for i in range(10)],
    {"number": 410, "createdAt": "2026-04-01T00:00:00+00:00", "mergedAt": "2026-04-06T00:00:00+00:00",
     "author": {"login": "flaky-fetch"}},
]
_DR90_UNMEASURED_DERIVED = [{"number": n, "is_external": True} for n in range(400, 411)]

_DR90_UNMEASURED_AUTHOR_RECORDS = [
    *[{"login": f"clean-{i}", "account_created_at": "2025-12-01T00:00:00+00:00",
       "public_event_count": 10, "eligibility_unmeasured": False} for i in range(10)],
    {"login": "flaky-fetch", "account_created_at": "2025-12-01T00:00:00+00:00",
     "public_event_count": 10, "eligibility_unmeasured": True},
]


def test_dr90_decision_rate_eligibility_unmeasured_author_excluded(tmp_path: Path) -> None:
    """11 raw PRs; the gate drops the 1 ``eligibility_unmeasured`` author
    (fast-merged, so a bug that kept it would also change the decided
    count, not just n), leaving exactly 10 clean authors -- still
    Measured, not Unmeasured(insufficient_n), pinning that the exclusion
    is counted BEFORE the min_n check, not after. 5 of the 10 kept are
    decided: r = 5/10 = 0.5."""
    data = pr_window_data(prs=_DR90_UNMEASURED_PRS, derived=_DR90_UNMEASURED_DERIVED)
    write_envelope(tmp_path / "raw", "pr_window", "acme__unmeasured-author", data, "2026-09-01T00:05:00+00:00")
    ah_data = author_history_data(author_records=_DR90_UNMEASURED_AUTHOR_RECORDS)
    write_envelope(tmp_path / "raw", "author_history", "acme__unmeasured-author", ah_data,
                    "2026-09-01T00:06:00+00:00")
    doc = base_document(
        provenance={
            "pr_window_count": prov("pr_window", key="acme__unmeasured-author"),
            "author_history_cost_points": prov("author_history", fetched_at="2026-09-01T00:06:00+00:00",
                                               key="acme__unmeasured-author"),
        },
    )
    bundle = load(tmp_path, doc, key="acme__unmeasured-author")
    assert bundle.inputs["dr90_decision_rate"] == Measured(
        value=0.5, n=10, window_start=_DR90_WINDOW_START, window_end=_DR90_WINDOW_END,
        method=_DR90_METHOD_GATED, source="raw/pr_window/acme__unmeasured-author.json",
        source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
    )

#: X8 ( review round 2): 10 clean eligible authors (each 1 PR), one
#: of whom ALSO has ``history_unmeasured=True`` -- their MR_first-only
#: prior-merge search failed, but their OWN account age/event count are
#: perfectly good. DR_90's gate reads ONLY ``eligibility_unmeasured``, so
#: this author is NOT excluded: capped n stays 10, not 9. Decoupled
#: (docstring decision) from ``porchlamp.collect.github_graphql.
#: _account_eligibility``'s OR of both flags, which is correct for ITS
#: OWN shared 1.2+1.3 call site but wrong here, where only 1.2 is scored.
_DR90_HISTORY_ONLY_PRS = [
    *[{"number": 600 + i, "createdAt": "2026-04-01T00:00:00+00:00",
       "mergedAt": "2026-04-06T00:00:00+00:00" if i < 4 else None,
       "author": {"login": f"clean-{i}"}} for i in range(10)],
]
_DR90_HISTORY_ONLY_DERIVED = [{"number": n, "is_external": True} for n in range(600, 610)]

_DR90_HISTORY_ONLY_AUTHOR_RECORDS = [
    *[{"login": f"clean-{i}", "account_created_at": "2025-12-01T00:00:00+00:00",
       "public_event_count": 10, "eligibility_unmeasured": False,
       # Only "clean-3" has a failed prior-merge SEARCH; its own
       # eligibility fetch (age/events, above) succeeded and is genuinely
       # good data.
       "history_unmeasured": (i == 3)} for i in range(10)],
]


def test_dr90_decision_rate_ignores_history_unmeasured_when_eligibility_is_measured(tmp_path: Path) -> None:
    data = pr_window_data(prs=_DR90_HISTORY_ONLY_PRS, derived=_DR90_HISTORY_ONLY_DERIVED)
    write_envelope(tmp_path / "raw", "pr_window", "acme__history-only", data, "2026-09-01T00:05:00+00:00")
    ah_data = author_history_data(author_records=_DR90_HISTORY_ONLY_AUTHOR_RECORDS)
    write_envelope(tmp_path / "raw", "author_history", "acme__history-only", ah_data, "2026-09-01T00:06:00+00:00")
    doc = base_document(
        provenance={
            "pr_window_count": prov("pr_window", key="acme__history-only"),
            "author_history_cost_points": prov("author_history", fetched_at="2026-09-01T00:06:00+00:00",
                                               key="acme__history-only"),
        },
    )
    bundle = load(tmp_path, doc, key="acme__history-only")
    # n=10 (not 9): clean-3 is kept despite history_unmeasured=True. 4 of
    # the 10 are decided (i < 4): r = 4/10 = 0.4, same as if
    # history_unmeasured were absent entirely.
    assert bundle.inputs["dr90_decision_rate"] == Measured(
        value=0.4, n=10, window_start=_DR90_WINDOW_START, window_end=_DR90_WINDOW_END,
        method=_DR90_METHOD_GATED, source="raw/pr_window/acme__history-only.json",
        source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
    )


@pytest.mark.parametrize("v1_legal_ip", ["CORP_CLA_RECIPROCAL", "CORP_CLA_UNILATERAL"])
def test_v1_legal_ip_values_map_to_corp_cla(tmp_path: Path, v1_legal_ip: str) -> None:
    """Regression: V1_LEGAL_IP_VALUES (CORP_CLA_RECIPROCAL, CORP_CLA_UNILATERAL)
    map cleanly to CORP_CLA in the legal_ip input resolver."""
    gov_data = {"legal_ip": v1_legal_ip}
    write_envelope(tmp_path / "raw", "governance", "acme__widget", gov_data)
    doc = base_document(
        meta={"legal_ip": v1_legal_ip},
        provenance={"legal_ip": prov("governance")},
        confidence={"legal_ip": "verified"},
    )
    bundle = load(tmp_path, doc)
    assert bundle.inputs["legal_ip"] == Measured(
        value="CORP_CLA",
        n=0,
        window_start=None,
        window_end=None,
        method="primary",
        source="raw/governance/acme__widget.json",
        source_hash=expected_hash(gov_data),
        fetched_at="2026-09-01T00:05:00+00:00",
    )


@pytest.mark.parametrize("eco", ["go", "maven", "systems"])
def test_downloads_monthly_not_applicable_ecosystems(tmp_path: Path, eco: str) -> None:
    """D-MISS-5 / RFC-0006: ecosystems with no public per-package downloads
    registry (go, maven, systems) resolve downloads_monthly to
    Unmeasured(reason="not_applicable") rather than third_party_gap."""
    doc = base_document(
        meta={"ecosystem": eco, "downloads_status": "unmeasured"},
        confidence={"monthly_downloads": "missing"},
    )
    bundle = load(tmp_path, doc, key=f"acme__{eco}-repo")
    assert bundle.inputs["downloads_monthly"] == Unmeasured(
        reason="not_applicable",
        detail=f"D-MISS-5: ecosystem == {eco} has no public downloads registry (2.3 is undefined)",
    )


def test_dx_resolver_measured_insufficient_n_and_missing(tmp_path: Path) -> None:
    """5.4 DX resolver: Measured when n >= 5, insufficient_n when n < 5, and
    collector_failure when actions_runs was never collected."""
    runs_data = [{"run_duration_seconds": 600} for _ in range(5)]
    write_envelope(tmp_path / "raw", "actions_runs", "acme__dx-ok", runs_data, "2026-09-01T00:05:00+00:00")
    doc_ok = base_document(
        meta={"dx": 72.5, "dx_run_count": 5},
        provenance={"dx": prov("actions_runs", key="acme__dx-ok")},
        confidence={"dx": "derived", "dx_run_count": "derived"},
    )
    bundle_ok = load(tmp_path, doc_ok, key="acme__dx-ok")
    assert bundle_ok.inputs["dx"] == Measured(
        value=72.5,
        n=5,
        window_start=None,
        window_end=None,
        method="primary",
        source="raw/actions_runs/acme__dx-ok.json",
        source_hash=expected_hash(runs_data),
        fetched_at="2026-09-01T00:05:00+00:00",
    )

    doc_thin = base_document(
        meta={"dx": 72.5, "dx_run_count": 4},
        provenance={"dx": prov("actions_runs", key="acme__dx-ok")},
        confidence={"dx": "derived", "dx_run_count": "derived"},
    )
    bundle_thin = load(tmp_path, doc_thin, key="acme__dx-thin")
    assert bundle_thin.inputs["dx"] == Unmeasured(
        reason="insufficient_n",
        detail="4 pull-request commits with a finished CI run, need >= 5",
    )

    doc_zero = base_document(
        meta={"dx": None, "dx_run_count": 0},
        confidence={"dx": "missing", "dx_run_count": "derived"},
    )
    bundle_zero = load(tmp_path, doc_zero, key="acme__dx-zero")
    assert bundle_zero.inputs["dx"] == Unmeasured(
        reason="insufficient_n",
        detail="0 pull-request commits with a finished CI run, need >= 5",
    )

    doc_missing = base_document(
        meta={"dx": None, "dx_run_count": 0},
        confidence={"dx": "missing", "dx_run_count": "missing"},
    )
    bundle_missing = load(tmp_path, doc_missing, key="acme__dx-missing")
    assert bundle_missing.inputs["dx"] == Unmeasured(
        reason="collector_failure",
        detail="v1 deriver recorded no dx value (no actions_runs envelope)",
    )
