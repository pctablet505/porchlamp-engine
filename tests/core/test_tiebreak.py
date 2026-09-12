"""W2-6 acceptance: the D-TIE-1 tie-break key, D-TIE-2 strict ranks and tie
groups (`02` section 10; `04` section 3, section 6's SQL ``ORDER BY``).

Fail-before: before this packet ``cvi.core.tiebreak`` does not exist, so
every test below fails on ``ModuleNotFoundError`` (recorded verbatim in the
handoff). A second fail-before proof is
``test_wrong_key_order_is_caught_by_the_oracle``: it runs the main 8-row
oracle's own name-order assertion against a deliberately broken key
(descending/ascending directions flipped, no measured-before-unmeasured
encoding) and shows the assertion actually fails on it, rather than trusting
the oracle to be sensitive to a wrong implementation on faith.

Row identity trick: :class:`~cvi.core.models.ScoreRecord` carries no repo
identity (that is exactly this packet's ownership-request finding -- see
``cvi/core/tiebreak.py``'s module docstring), so a test that wants to read
"which row is this, after ``assign_ranks`` returned a new record via
``dataclasses.replace``" stashes the row's own label in ``raw_hash`` (never
read by any tie-break logic) and reads it back off the output records.
"""
from __future__ import annotations

import random
import sqlite3

import pytest

from cvi.core.models import GateResult, PillarScore, PRWindow, PRWindowEvent, ScoreRecord
from cvi.core.tiebreak import (
    TieBreakInput,
    _measured_asc,
    _measured_desc,
    assign_ranks,
    tie_key,
    ttfhr_p75_from_pr_window,
)

_PILLARS = ("responsiveness", "criticality", "governance", "rigor", "accessibility")


def make_record(label: str, **overrides: object) -> ScoreRecord:
    """A fully-measured, internally consistent ``ScoreRecord``. ``label`` is
    stashed in ``raw_hash`` purely so tests can recover row identity after
    :func:`~cvi.core.tiebreak.assign_ranks` rebuilds the record (see the
    module docstring); it plays no role in any tie-break comparison."""
    responsiveness_score = overrides.pop("responsiveness_score", 80.0)
    pillars = {name: PillarScore(score=80.0, coverage=1.0) for name in _PILLARS}
    pillars["responsiveness"] = PillarScore(score=responsiveness_score, coverage=1.0)  # type: ignore[arg-type]
    base = dict(
        snapshot_id="snap-1",
        benchmark_version="2.0.0-dev",
        constants_hash="c" * 64,
        raw_hash=label,
        gate=GateResult(eligible=True, reason=None, routed=None, undecided=()),
        params={},
        pillars=pillars,
        composite=75.0,
        coverage=0.90,
        tier="A",
        leverage=70.0,
        friction=30.0,
        quadrant="Sweet Spot",
        persona_gates={},
        persona_composites={},
        tie_key=(),
        tie_group=None,
        rank=None,
        suspicion=[],
    )
    base.update(overrides)
    return ScoreRecord(**base)  # type: ignore[arg-type]


def make_entry(
    label: str,
    owner: str = "acme",
    name: str | None = None,
    ttfhr_p75_hours: float | None = 10.0,
    last_non_bot_commit_at: str | None = "2026-06-01T00:00:00Z",
    stars: int | None = None,
    forks: int | None = None,
    downloads: int | None = None,
    **record_overrides: object,
) -> TieBreakInput:
    return TieBreakInput(
        record=make_record(label, **record_overrides),
        owner=owner,
        name=name if name is not None else label,
        ttfhr_p75_hours=ttfhr_p75_hours,
        last_non_bot_commit_at=last_non_bot_commit_at,
        stars=stars,
        forks=forks,
        downloads=downloads,
    )


def _labels_in_order(ranked: list[tuple[ScoreRecord, int | None, int | None]]) -> list[str]:
    return [record.raw_hash for record, _rank, _tie_group in ranked]


# ---------------------------------------------------------------------------
# The 8-row hand-built oracle: every D-TIE-1 tie-break level (2 through 8)
# discriminates exactly one adjacent pair. All 8 rows share the identical
# composite (75.00 -- 118 distinct composite values over 981 rows in the
# live sample, per the packet's own "Why", so an all-tied-on-composite case
# is the realistic one, not a contrived edge case). Level 1 (composite) is
# exercised by a separate, tiny test below.
# ---------------------------------------------------------------------------


def _eight_rows() -> list[TieBreakInput]:
    return [
        make_entry(
            "r1", coverage=0.95,
            responsiveness_score=70.0, friction=50.0, leverage=40.0,
            ttfhr_p75_hours=20.0, last_non_bot_commit_at="2026-01-01T00:00:00Z",
            stars=0, forks=0, downloads=0,
        ),
        make_entry(
            "r2", coverage=0.80,
            responsiveness_score=90.0, friction=50.0, leverage=40.0,
            ttfhr_p75_hours=20.0, last_non_bot_commit_at="2026-01-01T00:00:00Z",
        ),
        make_entry(
            "r3", coverage=0.80,
            responsiveness_score=70.0, friction=5.0, leverage=40.0,
            ttfhr_p75_hours=20.0, last_non_bot_commit_at="2026-01-01T00:00:00Z",
        ),
        make_entry(
            "r4", coverage=0.80,
            responsiveness_score=70.0, friction=50.0, leverage=90.0,
            ttfhr_p75_hours=20.0, last_non_bot_commit_at="2026-01-01T00:00:00Z",
        ),
        make_entry(
            "r5", coverage=0.80,
            responsiveness_score=70.0, friction=50.0, leverage=40.0,
            ttfhr_p75_hours=3.0, last_non_bot_commit_at="2026-01-01T00:00:00Z",
        ),
        make_entry(
            "r6", coverage=0.80,
            responsiveness_score=70.0, friction=50.0, leverage=40.0,
            ttfhr_p75_hours=20.0, last_non_bot_commit_at="2026-09-10T00:00:00Z",
        ),
        make_entry(
            "apple", owner="acme", name="apple", coverage=0.80,
            responsiveness_score=70.0, friction=50.0, leverage=40.0,
            ttfhr_p75_hours=20.0, last_non_bot_commit_at="2026-01-01T00:00:00Z",
        ),
        make_entry(
            "Zebra", owner="acme", name="Zebra", coverage=0.80,
            responsiveness_score=70.0, friction=50.0, leverage=40.0,
            ttfhr_p75_hours=20.0, last_non_bot_commit_at="2026-01-01T00:00:00Z",
            stars=999_999_999, forks=999_999, downloads=999_999_999,
        ),
    ]


_EXPECTED_ORDER = ["r1", "r2", "r3", "r4", "r5", "r6", "apple", "Zebra"]


def test_eight_row_oracle_matches_d_tie_1_exactly() -> None:
    """r1 wins on coverage (level 2, 0.95 > 0.80); within the coverage=0.80
    group, r2 wins on responsiveness (level 3, 90 > 70); then r3 on
    friction (level 4, ascending -- 5.0 beats 50.0); then r4 on leverage
    (level 5, descending -- 90 beats 40); then r5 on TTFHR p75 (level 6,
    ascending -- 3.0 beats 20.0); then r6 on last-commit date (level 7,
    descending -- 2026-09-10 beats 2026-01-01); the final pair (apple,
    Zebra) ties on every numeric level and is decided by lowercase name
    (level 8): 'apple' < 'zebra' alphabetically, even though raw byte
    comparison would put capital-Z 'Zebra' first (0x5A < 0x61) -- this
    proves the comparison folds case rather than comparing raw bytes.
    r1's adversarial zero stars/forks/downloads and Zebra's adversarial
    nine-hundred-million never move either row (D-TIE-1: vanity, gameable,
    never read by :func:`tie_key` at all)."""
    ranked = assign_ranks(_eight_rows())
    assert _labels_in_order(ranked) == _EXPECTED_ORDER
    assert [rank for _r, rank, _tg in ranked] == [1, 2, 3, 4, 5, 6, 7, 8]
    tie_groups = [tg for _r, _rank, tg in ranked]
    assert tie_groups == [tie_groups[0]] * 8
    assert tie_groups[0] is not None


def test_eight_row_oracle_result_records_carry_their_tie_key() -> None:
    """``assign_ranks`` fills ``ScoreRecord.tie_key`` in (04 section 3
    reserves the field for this packet); it is not left at the engine's
    placeholder ``()``."""
    ranked = assign_ranks(_eight_rows())
    for record, _rank, _tie_group in ranked:
        assert record.tie_key != ()
        assert record.tie_key == tie_key(
            next(e for e in _eight_rows() if e.record.raw_hash == record.raw_hash)
        )


def test_wrong_key_order_is_caught_by_the_oracle() -> None:
    """Fail-before proof for the oracle itself: a key that (a) does not
    negate the descending levels and (b) does not push unmeasured values
    after measured ones produces a *different* order than
    ``_EXPECTED_ORDER`` on these same 8 rows, so the assertion above is not
    vacuously true."""

    def broken_key(entry: TieBreakInput) -> tuple:
        # No negation on the "descending" levels, no measured-first
        # encoding -- both defects the real ``tie_key`` guards against.
        pillar = entry.record.pillars.get("responsiveness")
        responsiveness = pillar.score if pillar is not None else 0.0
        return (
            round(entry.record.composite, 2),
            entry.record.coverage,
            responsiveness,
            entry.record.friction,
            entry.record.leverage,
            entry.ttfhr_p75_hours,
            entry.owner.lower(),
            entry.name.lower(),
        )

    broken_order = [e.record.raw_hash for e in sorted(_eight_rows(), key=broken_key)]
    assert broken_order != _EXPECTED_ORDER


def test_composite_level_one_descending() -> None:
    """Level 1: a higher composite always wins, independent of every other
    field (r_low is given a "perfect" profile on every later level and
    still loses to r_high's higher composite alone)."""
    r_high = make_entry("high", composite=80.0, coverage=0.10, responsiveness_score=1.0,
                         friction=99.0, leverage=1.0, ttfhr_p75_hours=999.0,
                         last_non_bot_commit_at="1900-01-01T00:00:00Z")
    r_low = make_entry("low", composite=79.99, coverage=1.0, responsiveness_score=100.0,
                        friction=0.0, leverage=100.0, ttfhr_p75_hours=0.0,
                        last_non_bot_commit_at="2026-09-12T00:00:00Z")
    ranked = assign_ranks([r_low, r_high])
    assert _labels_in_order(ranked) == ["high", "low"]


# ---------------------------------------------------------------------------
# Unmeasured sorts after measured at every level 3-7, even when the measured
# competitor's own value is a "bad" one.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["responsiveness_score", "friction", "leverage", "ttfhr_p75_hours", "last_non_bot_commit_at"],
)
def test_unmeasured_sorts_after_measured_at_every_level(field: str) -> None:
    bad_value = {
        "responsiveness_score": 1.0,
        "friction": 99.0,
        "leverage": 1.0,
        "ttfhr_p75_hours": 999.0,
        "last_non_bot_commit_at": "1900-01-01T00:00:00Z",
    }[field]
    measured = make_entry("measured", **{field: bad_value})
    unmeasured = make_entry("unmeasured", **{field: None})
    ranked = assign_ranks([unmeasured, measured])
    assert _labels_in_order(ranked) == ["measured", "unmeasured"]


def test_measured_desc_and_asc_encode_unmeasured_last() -> None:
    """Direct unit check of the two small encoders ``tie_key`` is built
    from, independent of the end-to-end oracle above."""
    assert _measured_desc(None) > _measured_desc(100.0)
    assert _measured_desc(50.0) < _measured_desc(10.0)  # descending: higher value sorts first
    assert _measured_asc(None) > _measured_asc(100.0)
    assert _measured_asc(10.0) < _measured_asc(50.0)  # ascending: lower value sorts first


# ---------------------------------------------------------------------------
# Gated / unrated placement (D-TIE-2).
# ---------------------------------------------------------------------------


def test_gated_and_unrated_are_listed_after_rated_ordered_by_name() -> None:
    rated_a = make_entry("rated-a", name="rated-a", composite=90.0)
    rated_b = make_entry("rated-b", name="rated-b", composite=50.0)
    gated = make_entry(
        "gated",
        name="unrated-z",
        composite=None,
        gate=GateResult(eligible=False, reason="archived", routed=None, undecided=()),
        coverage=0.0,
        tier=None,
        leverage=None,
        friction=None,
        quadrant=None,
    )
    unrated = make_entry(
        "unrated",
        name="gated-a",
        composite=None,
        gate=GateResult(eligible=False, reason="unrated: insufficient_evidence", routed=None, undecided=()),
        coverage=0.3,
        tier=None,
        leverage=None,
        friction=None,
        quadrant=None,
    )
    ranked = assign_ranks([gated, rated_b, unrated, rated_a])
    assert _labels_in_order(ranked) == ["rated-a", "rated-b", "unrated", "gated"]
    ranks = [rank for _r, rank, _tg in ranked]
    assert ranks == [1, 2, None, None]
    tie_groups = [tg for _r, _rank, tg in ranked]
    assert tie_groups == [None, None, None, None]
    for record, _rank, _tg in ranked[2:]:
        assert record.tie_key == ()


# ---------------------------------------------------------------------------
# Rank strictness and tie_group assignment (D-TIE-2), isolated from the
# 8-row oracle's single tie group.
# ---------------------------------------------------------------------------


def test_rank_is_strict_and_tie_group_only_shared_within_a_composite() -> None:
    a = make_entry("a", composite=60.0, coverage=0.90)
    b = make_entry("b", composite=60.0, coverage=0.80)
    c = make_entry("c", composite=60.0, coverage=0.70)
    d = make_entry("d", composite=50.0, coverage=1.0)  # unique composite: no tie_group
    ranked = assign_ranks([d, c, b, a])
    assert _labels_in_order(ranked) == ["a", "b", "c", "d"]
    ranks = [rank for _r, rank, _tg in ranked]
    assert ranks == [1, 2, 3, 4]
    tie_groups = [tg for _r, _rank, tg in ranked]
    assert tie_groups[0] == tie_groups[1] == tie_groups[2]
    assert tie_groups[0] is not None
    assert tie_groups[3] is None


def test_tie_key_rejects_an_unrated_record() -> None:
    unrated = make_entry("x", composite=None)
    with pytest.raises(ValueError, match="rated"):
        tie_key(unrated)


def test_tiebreak_input_rejects_empty_owner_or_name() -> None:
    with pytest.raises(ValueError, match="owner"):
        TieBreakInput(record=make_record("x"), owner="", name="widget")
    with pytest.raises(ValueError, match="name"):
        TieBreakInput(record=make_record("x"), owner="acme", name="")


# ---------------------------------------------------------------------------
# Input-order independence.
# ---------------------------------------------------------------------------


def test_assign_ranks_is_independent_of_input_order() -> None:
    baseline = _eight_rows() + [
        make_entry(
            "gated", name="zzz-gated", composite=None,
            gate=GateResult(eligible=False, reason="archived", routed=None, undecided=()),
            coverage=0.0, tier=None, leverage=None, friction=None, quadrant=None,
        )
    ]
    canonical = assign_ranks(baseline)
    canonical_labels = _labels_in_order(canonical)
    canonical_ranks = [rank for _r, rank, _tg in canonical]
    canonical_groups = [tg for _r, _rank, tg in canonical]

    rng = random.Random(20260912)
    for _ in range(5):
        shuffled = list(baseline)
        rng.shuffle(shuffled)
        result = assign_ranks(shuffled)
        assert _labels_in_order(result) == canonical_labels
        assert [rank for _r, rank, _tg in result] == canonical_ranks
        assert [tg for _r, _rank, tg in result] == canonical_groups

    reversed_result = assign_ranks(list(reversed(baseline)))
    assert _labels_in_order(reversed_result) == canonical_labels


# ---------------------------------------------------------------------------
# SQL parity (04 section 6's ORDER BY) on the same 8 rows.
# ---------------------------------------------------------------------------


def test_sql_order_by_agrees_with_tie_key_on_the_eight_row_case() -> None:
    """Encodes 04 section 6's tie-break clause
    (``ROUND(cvi_p,2) DESC, coverage DESC, p_responsiveness DESC,
    friction ASC, leverage DESC, ttfhr_p75 ASC, last_human_commit_at DESC,
    lower(owner||'/'||name) ASC``) against an in-memory sqlite3 table built
    from the same 8 rows, and compares row order to :func:`tie_key`'s. All
    8 rows are fully measured, so this does not exercise SQLite's default
    NULL-ordering (NULLs first for ASC / last for DESC), which does not
    match D-TIE-1's "unmeasured sorts after measured regardless of
    direction" for an ASC column -- 04 section 6's snippet names no
    ``NULLS FIRST``/``NULLS LAST`` clause, so this is flagged as an
    ownership note (see the handoff) rather than asserted here."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE t (
            owner TEXT, name TEXT, composite REAL, coverage REAL,
            p_responsiveness REAL, friction REAL, leverage REAL,
            ttfhr_p75 REAL, last_human_commit_at TEXT
        )
        """
    )
    for entry in _eight_rows():
        pillar = entry.record.pillars["responsiveness"]
        conn.execute(
            "INSERT INTO t VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry.owner, entry.name, entry.record.composite, entry.record.coverage,
                pillar.score, entry.record.friction, entry.record.leverage,
                entry.ttfhr_p75_hours, entry.last_non_bot_commit_at,
            ),
        )
    rows = conn.execute(
        """
        SELECT name FROM t ORDER BY
            round(composite, 2) DESC, coverage DESC, p_responsiveness DESC,
            friction ASC, leverage DESC, ttfhr_p75 ASC,
            last_human_commit_at DESC, lower(owner || '/' || name) ASC
        """
    ).fetchall()
    conn.close()
    sql_order = [row[0] for row in rows]
    python_order = [e.name for e in sorted(_eight_rows(), key=tie_key)]
    assert sql_order == python_order == _EXPECTED_ORDER


# ---------------------------------------------------------------------------
# ttfhr_p75_from_pr_window.
# ---------------------------------------------------------------------------


def _event(number: int, created: str, response: str | None, *, external: bool = True) -> PRWindowEvent:
    return PRWindowEvent(
        pr_number=number,
        created_at=created,
        closed_at=None,
        merged_at=None,
        author_login="contributor",
        author_association="NONE",
        is_external=external,
        first_human_review_at=response,
    )


def test_ttfhr_p75_nearest_rank_over_external_responded_events() -> None:
    # Hours to first response: 1, 2, 3, 4 (four external, responded events).
    # nearest-rank p75 of N=4 -> ceil(0.75*4)=3rd smallest -> 3.0.
    window = PRWindow(
        start="2026-01-01T00:00:00Z",
        end="2026-06-01T00:00:00Z",
        truncated=False,
        events=(
            _event(1, "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"),
            _event(2, "2026-01-02T00:00:00Z", "2026-01-02T02:00:00Z"),
            _event(3, "2026-01-03T00:00:00Z", "2026-01-03T03:00:00Z"),
            _event(4, "2026-01-04T00:00:00Z", "2026-01-04T04:00:00Z"),
        ),
    )
    assert ttfhr_p75_from_pr_window(window) == pytest.approx(3.0)


def test_ttfhr_p75_ignores_internal_and_unresponded_events() -> None:
    window = PRWindow(
        start="2026-01-01T00:00:00Z",
        end="2026-06-01T00:00:00Z",
        truncated=False,
        events=(
            _event(1, "2026-01-01T00:00:00Z", "2026-01-01T05:00:00Z", external=False),  # internal: excluded
            _event(2, "2026-01-02T00:00:00Z", None),  # no response: excluded
            _event(3, "2026-01-03T00:00:00Z", "2026-01-03T10:00:00Z"),  # the only qualifying event
        ),
    )
    assert ttfhr_p75_from_pr_window(window) == pytest.approx(10.0)


def test_ttfhr_p75_is_none_with_no_qualifying_events() -> None:
    window = PRWindow(start=None, end=None, truncated=False, events=())
    assert ttfhr_p75_from_pr_window(window) is None
