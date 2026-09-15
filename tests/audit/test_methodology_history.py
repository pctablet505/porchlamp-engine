"""Acceptance tests for the extended methodology document
(``cvi.audit.methodology_history``) -- the caller that crosses the
``cvi.core`` import boundary to add detectors, process constants and the
RFC changelog to ``cvi.core.methodology.methodology_document``.
"""

from __future__ import annotations

from pathlib import Path

from cvi.audit import anchors, sensitivity
from cvi.audit.methodology_history import (
    methodology_with_history,
    parse_changelog,
)
from cvi.core.methodology import methodology_document
from cvi.verify import detectors

REPO_ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = REPO_ROOT / "docs" / "v2" / "rfcs" / "CHANGELOG.md"


def test_extended_document_adds_exactly_three_keys_to_the_pure_document() -> None:
    pure = methodology_document()
    extended = methodology_with_history(CHANGELOG)
    assert set(extended.keys()) - set(pure.keys()) == {
        "detectors",
        "not_hashed_process_constants",
        "rfc_log",
    }
    for key in pure:
        assert extended[key] == pure[key]


def test_detectors_cover_every_name_in_detector_names_order() -> None:
    extended = methodology_with_history(CHANGELOG)
    names = [entry["name"] for entry in extended["detectors"]]
    assert names == list(detectors.DETECTOR_NAMES)
    by_name = {entry["name"]: entry for entry in extended["detectors"]}
    assert by_name["param_jump"]["thresholds"] == {
        "threshold_points": detectors.PARAM_JUMP_THRESHOLD_POINTS,
        "min_n": detectors.PARAM_JUMP_MIN_N,
    }
    assert (
        by_name["newcomer_burst"]["thresholds"]["window_days"]
        == detectors.NEWCOMER_BURST_WINDOW.days
    )
    assert by_name["star_burst"]["thresholds"] == {}
    assert by_name["source_drift"]["thresholds"] == {}
    for entry in extended["detectors"]:
        assert entry["direction"] in {"magnitude", "above", "below", "compound", "boolean"}


def test_not_hashed_process_constants_reads_the_three_named_values() -> None:
    extended = methodology_with_history(CHANGELOG)
    by_name = {row["name"]: row for row in extended["not_hashed_process_constants"]}
    assert set(by_name) == {"MIN_COVERAGE", "TAU_RELEASE_THRESHOLD", "DEFAULT_PERTURBATION_PP"}
    assert by_name["MIN_COVERAGE"]["value"] == anchors.MIN_COVERAGE
    assert by_name["MIN_COVERAGE"]["module"] == "cvi.audit.anchors"
    assert by_name["TAU_RELEASE_THRESHOLD"]["value"] == sensitivity.TAU_RELEASE_THRESHOLD
    assert by_name["DEFAULT_PERTURBATION_PP"]["value"] == sensitivity.DEFAULT_PERTURBATION_PP
    for row in extended["not_hashed_process_constants"]:
        assert isinstance(row["gates"], str) and row["gates"]


def test_not_hashed_process_constants_are_absent_from_constants_dict() -> None:
    """These three are process constants precisely because they are NOT
    scoring inputs (`docs/v2/rfcs/README.md` "Process constants"): confirm
    none of their names collides with a real ``constants_dict()`` key,
    which would mean this packet mis-scoped one of them."""
    from cvi.core import constants

    names = {
        row["name"] for row in methodology_with_history(CHANGELOG)["not_hashed_process_constants"]
    }
    assert names.isdisjoint(constants.constants_dict().keys())


# --------------------------------------------------------------------------- #
# Changelog parsing                                                            #
# --------------------------------------------------------------------------- #


def test_parse_changelog_reads_the_real_file_oldest_first() -> None:
    rows = parse_changelog(CHANGELOG)
    assert len(rows) >= 3
    dates = [row["date"] for row in rows]
    assert dates == sorted(dates)
    rfc_ids = [row["rfc"] for row in rows]
    assert "RFC-0001" in rfc_ids
    assert "RFC-0002" in rfc_ids
    assert "RFC-0003" in rfc_ids
    # RFC-0001 must come before RFC-0002 before RFC-0003 (oldest first)
    assert rfc_ids.index("RFC-0001") < rfc_ids.index("RFC-0002") < rfc_ids.index("RFC-0003")


def test_parse_changelog_includes_the_genesis_row_first() -> None:
    rows = parse_changelog(CHANGELOG)
    assert rows[0]["rfc"] == "genesis"
    assert rows[0]["hash_before"] is None
    assert rows[0]["hash_after"] is not None
    assert len(rows[0]["hash_after"]) == 64  # a real sha256 hex digest
    # the genesis row's "after" hash must chain into RFC-0001's "before" hash
    rfc_0001 = next(row for row in rows if row["rfc"] == "RFC-0001")
    assert rows[0]["hash_after"] == rfc_0001["hash_before"]


def test_parse_changelog_hash_chain_is_unbroken() -> None:
    """Every row's ``hash_after`` equals the next row's ``hash_before`` --
    proves the changelog records one continuous lineage with no gaps."""
    rows = parse_changelog(CHANGELOG)
    for earlier, later in zip(rows, rows[1:]):
        assert earlier["hash_after"] == later["hash_before"]


def test_parse_changelog_skips_header_and_separator_rows(tmp_path: Path) -> None:
    sample = tmp_path / "CHANGELOG.md"
    sample.write_text(
        "# Constants-change log\n\n"
        "Some prose that is not a table row.\n\n"
        "| Date | RFC | Summary | Hash before -> after |\n"
        "|---|---|---|---|\n"
        "| 2026-01-01 | [RFC-0001](RFC-0001-x.md) | did a thing | `aaaa` -> `bbbb` |\n",
        encoding="utf-8",
    )
    rows = parse_changelog(sample)
    assert len(rows) == 1
    assert rows[0] == {
        "date": "2026-01-01",
        "rfc": "RFC-0001",
        "summary": "did a thing",
        "hash_before": "aaaa",
        "hash_after": "bbbb",
    }


def test_parse_changelog_genesis_row_shape(tmp_path: Path) -> None:
    sample = tmp_path / "CHANGELOG.md"
    sample.write_text(
        "| Date | RFC | Summary | Hash before -> after |\n"
        "|---|---|---|---|\n"
        "| 2026-01-01 | [RFC-0001](RFC-0001-x.md) | did a thing | `aaaa` -> `bbbb` |\n"
        "| 2025-12-31 | genesis | the initial freeze | *(none)* -> `9999` |\n",
        encoding="utf-8",
    )
    rows = parse_changelog(sample)
    assert rows[0] == {
        "date": "2025-12-31",
        "rfc": "genesis",
        "summary": "the initial freeze",
        "hash_before": None,
        "hash_after": "9999",
    }
