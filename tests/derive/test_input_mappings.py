"""W2-9 oracle tests: one test per ``cvi.derive.bundle.INPUT_MAPPINGS`` row.

Each test builds a hand-controlled v1 derived-file document (see
``_fixtures.py``), loads it through the public ``load_bundle`` entry point,
and asserts the exact ``Measured``/``Unmeasured`` value produced for that one
v2 input id -- every field pinned, nothing left to shape-only assertions.

Ten rows have a real resolver with both a positive (Measured) and at least
one negative (Unmeasured) path: ``ttfhr_hours``, ``ttm_p90_hours``,
``dependents_deduped``, ``downloads_monthly``, ``legal_ip``,
``elephant_factor``, ``promotion_ladder``, ``ai_posture``,
``substantive_review_rate``, ``newcomer_issue_supply``. Eight rows have no
landed producer yet and are a constant ``Unmeasured(collector_failure, ...)``
regardless of document content: ``dr90_decision_rate``, ``mr_first_rate``,
``gr14_ghost_rate``, ``openssf_criticality``, ``pipeline_defenses``,
``newcomer_conversion``, ``rr90_return_rate``, ``dx``.
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

    # Negative 2: v1 deriver recorded nothing at all.
    missing_doc = base_document(confidence={"dependents_count": "missing"})
    missing_bundle = load(tmp_path, missing_doc, key="acme__missing-deps")
    assert missing_bundle.inputs["dependents_deduped"] == Unmeasured(
        reason="collector_failure", detail="v1 deriver recorded no value",
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

    # Negative 2: an unrecognized v1 enum spelling is a data-corruption signal
    # that rejects the WHOLE bundle (pre-W2-9 behaviour, unchanged) -- not a
    # soft per-field Unmeasured.
    bogus_data = {"legal_ip_model": "BOGUS_REGIME"}
    write_envelope(tmp_path / "raw", "tree", "acme__bogus-legal", bogus_data, "2026-09-01T00:05:00+00:00")
    bogus_doc = base_document(
        meta={"legal_ip_model": "BOGUS_REGIME"},
        provenance={"legal_ip_model": prov("tree", key="acme__bogus-legal")},
        confidence={"legal_ip_model": "verified"},
    )
    with pytest.raises(BundleValidationError, match="BOGUS_REGIME"):
        load(tmp_path, bogus_doc, key="acme__bogus-legal")


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

    bogus_data = {"promotion_ladder": "ROGUE_RUNG"}
    write_envelope(tmp_path / "raw", "tree", "acme__bogus-ladder", bogus_data, "2026-09-01T00:05:00+00:00")
    bogus_doc = base_document(
        meta={"promotion_ladder": "ROGUE_RUNG"},
        provenance={"promotion_ladder": prov("tree", key="acme__bogus-ladder")},
        confidence={"promotion_ladder": "verified"},
    )
    with pytest.raises(BundleValidationError, match="ROGUE_RUNG"):
        load(tmp_path, bogus_doc, key="acme__bogus-ladder")


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
# The eight rows with no landed producer: a constant Unmeasured regardless of #
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
    "openssf_criticality": (
        "v1 leaves the ten OpenSSF signals uncollected; v2 2.2 needs all ten (W1-7 not yet landed)"
    ),
    "pipeline_defenses": (
        "v2 4.1 scores Scorecard checks or a proxy; v1 stores only the composite score (W1-7 not yet landed)"
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
