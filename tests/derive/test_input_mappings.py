"""W2-9 oracle tests: one test per ``cvi.derive.bundle.INPUT_MAPPINGS`` row.

Each test builds a hand-controlled v1 derived-file document (see
``_fixtures.py``), loads it through the public ``load_bundle`` entry point,
and asserts the exact ``Measured``/``Unmeasured`` value produced for that one
v2 input id -- every field pinned, nothing left to shape-only assertions.

Twelve rows have a real resolver with both a positive (Measured) and at
least one negative (Unmeasured) path: ``ttfhr_hours``, ``ttm_p90_hours``,
``dependents_deduped``, ``openssf_criticality`` (W1-7, W2-9f),
``downloads_monthly``, ``legal_ip``, ``elephant_factor``,
``promotion_ladder``, ``pipeline_defenses`` (W1-7, W2-9f), ``ai_posture``,
``substantive_review_rate``, ``newcomer_issue_supply``. Six rows have no
landed producer yet and are a constant ``Unmeasured(collector_failure, ...)``
regardless of document content: ``dr90_decision_rate``, ``mr_first_rate``,
``gr14_ghost_rate``, ``newcomer_conversion``, ``rr90_return_rate``, ``dx``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from _fixtures import base_document, expected_hash, load, pr_window_data, prov, write_envelope

from cvi.core import constants_v2
from cvi.core.models import BundleValidationError, Measured, Unmeasured
from cvi.derive.bundle import INPUT_MAPPINGS

#: Copied verbatim from cvi/derive/bundle.py's ``_PR_WINDOW_UNRESOLVABLE_DETAIL``
#: -- pinned here as a literal so a change to the message is a deliberate,
#: reviewed edit to this test, not a silent drift.
PR_WINDOW_UNRESOLVABLE_DETAIL = (
    "pr_window envelope not resolvable (W1-3 hook did not run for this repo, "
    "or raw_root cannot resolve the recorded source to a file)"
)


def test_input_mappings_cover_every_v2_input_id() -> None:
    assert set(INPUT_MAPPINGS) == set(constants_v2.PARAM_INPUT_IDS)
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
        source=str(tmp_path / "raw" / "pr_window" / "acme__widget.json"),
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

    # Negative 2: no pr_window envelope at all (W1-3 hook never ran here).
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
        source=str(tmp_path / "raw" / "pr_window" / "acme__widget.json"),
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
    """W2-9g: 1.5 TTM_p90 must count only PRs that are external AND merged
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
        source=str(tmp_path / "raw" / "pr_window" / "acme__mixed-ttm.json"),
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
        source=str(tmp_path / "raw" / "pr_window" / "acme__widget.json"),
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
    """W2-9f, W2 (Opus review mutant M12): ``n`` counts decided PRs that are
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
        source=str(tmp_path / "raw" / "dependents" / "acme__widget.json"),
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

    # Negative 2 (W2-9f, W1): v1 deriver recorded nothing at all -- 2.1 is
    # class TP (02 section 4 row 2.1), so a deps.dev/ecosyste.ms absence is
    # third_party_gap, exactly like 2.3 downloads_monthly's equivalent
    # branch, not collector_failure (that stays reserved for a local-side
    # fault, e.g. Negative 1 above's "wrong collector ran" case).
    missing_doc = base_document(confidence={"dependents_count": "missing"})
    missing_bundle = load(tmp_path, missing_doc, key="acme__missing-deps")
    assert missing_bundle.inputs["dependents_deduped"] == Unmeasured(
        reason="third_party_gap", detail="deps.dev/ecosyste.ms returned no dependents data",
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
        source=str(tmp_path / "raw" / "downloads" / "acme__widget.json"),
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
    data = {"legal_ip_model": "CORP_CLA_RECIPROCAL"}
    write_envelope(tmp_path / "raw", "tree", "acme__widget", data, "2026-09-01T00:05:00+00:00")
    doc = base_document(
        meta={"legal_ip_model": "CORP_CLA_RECIPROCAL"},
        provenance={"legal_ip_model": prov("tree")},
        confidence={"legal_ip_model": "verified"},
    )
    bundle = load(tmp_path, doc)
    assert bundle.inputs["legal_ip"] == Measured(
        value="CORP_CLA", n=0, window_start=None, window_end=None, method="primary",
        source=str(tmp_path / "raw" / "tree" / "acme__widget.json"),
        source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
    )

    # Negative 1: v1 deriver recorded nothing.
    missing = base_document(confidence={"legal_ip_model": "missing"})
    missing_bundle = load(tmp_path, missing, key="acme__missing-legal")
    assert missing_bundle.inputs["legal_ip"] == Unmeasured(
        reason="collector_failure", detail="v1 deriver recorded no value",
    )

    # Negative 2 (W2-9f, W5): an unrecognized v1 enum spelling is THIS ROW's
    # own gap, not a whole-bundle rejection -- even when the envelope is
    # otherwise perfectly resolvable (pre-W2-9f: this raised
    # BundleValidationError and lost the whole repo's 17 other, perfectly
    # good inputs over one bad enum in a row that was going to be Unmeasured
    # regardless).
    bogus_data = {"legal_ip_model": "BOGUS_REGIME"}
    write_envelope(tmp_path / "raw", "tree", "acme__bogus-legal", bogus_data, "2026-09-01T00:05:00+00:00")
    bogus_doc = base_document(
        meta={"legal_ip_model": "BOGUS_REGIME"},
        provenance={"legal_ip_model": prov("tree", key="acme__bogus-legal")},
        confidence={"legal_ip_model": "verified"},
    )
    bogus_bundle = load(tmp_path, bogus_doc, key="acme__bogus-legal")  # must not raise
    assert bogus_bundle.inputs["legal_ip"] == Unmeasured(
        reason="collector_failure",
        detail="v1 value 'BOGUS_REGIME' for 'legal_ip_model' has no v2 vocabulary mapping",
    )

    # Negative 3 (W2-9f, W5, the review's exact scenario): the same bogus
    # enum PLUS an unresolvable (URL) source -- the real shape of 199 of 199
    # sample-200 documents' legal_ip_model/promotion_ladder provenance. The
    # envelope-resolution failure wins the detail message (the enum was
    # never reachable either way); the bundle still loads.
    url_bogus_doc = base_document(
        meta={"legal_ip_model": "BOGUS_REGIME"},
        provenance={"legal_ip_model": {
            "source": "https://raw.githubusercontent.com/acme/widget/HEAD/CONTRIBUTING.md",
            "fetched_at": "2026-09-01T00:05:00+00:00",
        }},
        confidence={"legal_ip_model": "verified"},
    )
    url_bogus_bundle = load(tmp_path, url_bogus_doc, key="acme__url-bogus-legal")  # must not raise
    assert url_bogus_bundle.inputs["legal_ip"] == Unmeasured(
        reason="collector_failure",
        detail="v1 source is not an envelope: "
               "'https://raw.githubusercontent.com/acme/widget/HEAD/CONTRIBUTING.md'",
    )


def test_legal_ip_unmappable_enum_over_correction_guard(tmp_path: Path) -> None:
    """Over-correction guard for W5: the row-scoped fix does not disable
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
        source=str(tmp_path / "raw" / "contributors" / "acme__widget.json"),
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
        source=str(tmp_path / "raw" / "tree" / "acme__widget.json"),
        source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
    )

    missing = base_document(confidence={"promotion_ladder": "missing"})
    missing_bundle = load(tmp_path, missing, key="acme__missing-ladder")
    assert missing_bundle.inputs["promotion_ladder"] == Unmeasured(
        reason="collector_failure", detail="v1 deriver recorded no value",
    )

    # W2-9f, W5: same row-scoped fix as legal_ip -- an unmappable enum never
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
        source=str(tmp_path / "raw" / "governance" / "acme__widget.json"),
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
    """W2-9f, W4: ai_posture validates against constants_v2.AI_POSTURE_VALUES
    -- an out-of-set value is Unmeasured(collector_failure) with the bad
    value in detail, never Measured, even with an otherwise perfectly
    resolvable envelope (pre-W2-9f: the non-validating scalar resolver
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
        source=str(tmp_path / "raw" / "gfi" / "acme__widget.json"),
        source_hash=expected_hash(data), fetched_at="2026-09-01T00:05:00+00:00",
    )

    missing = base_document(confidence={"active_unclaimed_gfis": "missing"})
    missing_bundle = load(tmp_path, missing, key="acme__missing-gfi")
    assert missing_bundle.inputs["newcomer_issue_supply"] == Unmeasured(
        reason="collector_failure", detail="v1 deriver recorded no value",
    )


# --------------------------------------------------------------------------- #
# 2.2 openssf_criticality (W1-7, W2-9f)                                       #
# --------------------------------------------------------------------------- #

#: The ten Pike signals (cvi.models.OpenSSFSignals field names), a plausible
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
    from cvi.models import OpenSSFSignals
    from cvi.openssf import calculate_pike_criticality

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
    # Oracle: cvi.openssf.calculate_pike_criticality itself (independent of
    # cvi.derive.bundle's mirrored, cvi.core-only reimplementation), not the
    # code under test.
    expected = round(calculate_pike_criticality(OpenSSFSignals(**_OPENSSF_SIGNALS)), 4)
    assert value == Measured(
        value=expected, n=0, window_start=None, window_end=None, method="primary",
        source=str(tmp_path / "raw" / "github_meta" / f"{key}.json"),
        source_hash=expected_hash({"placeholder": "github_meta"}),
        fetched_at="2026-09-01T00:05:00+00:00",
    )
    assert 0.0 <= value.value <= 1.0


#: Three signal vectors spanning the formula's interesting regions: every
#: field at zero, a plausible mid-sized repo, and every field pinned exactly
#: at its OPENSSF_THRESHOLDS saturation point (cvi.core.openssf.
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
    """W2-9g wiring proof: the bundle-derived value equals
    cvi.core.openssf.calculate_pike_criticality(signals) called directly --
    not cvi.derive.bundle's own reimplementation (there is none any more)
    and not cvi.openssf's OpenSSFSignals-typed wrapper (a second hop this
    test does not take), across three regions of the formula: every signal
    at zero, a mid-sized repo, and every signal exactly at its threshold."""
    from cvi.core.openssf import calculate_pike_criticality

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
    """Independent oracle (W2-9g): unlike the parity test above (which
    compares two calls to the SAME cvi.core.openssf.calculate_pike_
    criticality and so cannot detect a wrong default weight or threshold --
    both sides would drift together), this asserts against a value derived
    by hand, calling no CVI code at all. At every signal exactly at its
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
    """W2-9f, W3: a partial openssf_signals dict (defensive -- production's
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


# --------------------------------------------------------------------------- #
# 4.1 pipeline_defenses (W1-7, W2-9f)                                         #
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
        source=str(tmp_path / "raw" / "scorecard" / f"{key}.json"),
        source_hash=expected_hash({"score": 5, "checks": {}}), fetched_at="2026-09-01T00:05:00+00:00",
    )


def test_pipeline_defenses_proxy_method_counts_merged_prs(tmp_path: Path) -> None:
    """proxy:review_ci: n is the merged-PR count (the code_review_rate
    denominator), read from the pr_window envelope's own prs list -- 3
    merged of 5 recorded PRs here, none of which is the min-n gate's
    concern (that lives in cvi.core.params.rigor, not this loader)."""
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
        source=str(tmp_path / "raw" / "pr_window" / f"{key}.json"),
        source_hash=expected_hash(pr_window_env), fetched_at="2026-09-01T00:05:00+00:00",
    )


def test_pipeline_defenses_absence_is_third_party_gap(tmp_path: Path) -> None:
    """W2-9f, W3: scripts/derive.py never calls Bundle.missing for this
    field -- when neither method is measurable its confidence key is simply
    absent (base_document()'s empty confidence dict), which must be treated
    the same as an explicit "missing" (02 section 4 row 4.1: "TP with
    proxy")."""
    doc = base_document()
    bundle = load(tmp_path, doc, key="acme__pd-absent")
    assert bundle.inputs["pipeline_defenses"] == Unmeasured(
        reason="third_party_gap",
        detail="no scorecard envelope with a usable check subset and no pr_window/runs envelopes to "
               "derive the review/CI proxy (cvi.collect.pike_signals.pipeline_defenses)",
    )


# --------------------------------------------------------------------------- #
# The six rows with no landed producer: a constant Unmeasured regardless of  #
# document content. Two very different documents (empty, and one carrying    #
# unrelated data for every other row) land on the exact same value, proving  #
# these rows do not branch on anything in the fixture.                       #
# --------------------------------------------------------------------------- #

_UNMAPPED_ROWS = {
    "dr90_decision_rate": (
        "v2 parameter; D-IN-4's decision-rate cohort is PRs opened in [now-270d, now-90d], "
        "outside the landed W1-3 collector's trailing-180d window"
    ),
    "mr_first_rate": "needs W1-4 author history; the v1 pipeline collected none",
    "gr14_ghost_rate": (
        "1.4 needs a snapshot-relative 'now' to classify PR age; the v1-to-v2 loader receives no "
        "as_of and must not borrow the pr_window collector's own fetch-time clock as one (04 section 8) "
        "-- computing this belongs at derive time (scripts/derive.py), out of this packet's ownership"
    ),
    "newcomer_conversion": "needs the W1-4 newcomer cohort; the v1 pipeline collected none",
    "rr90_return_rate": "v2 5.3 is cohort-based (W1-4); v1 rr_90 approximates author gaps",
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
