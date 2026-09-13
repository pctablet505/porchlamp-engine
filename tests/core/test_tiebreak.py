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

import os
import random
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from cvi.core.models import GateResult, PillarScore, PRWindow, PRWindowEvent, RepoIdentity, ScoreRecord
from cvi.core.tiebreak import (
    TieBreakInput,
    _commit_epoch,
    _measured_asc,
    _measured_desc,
    assign_ranks,
    tie_key,
    ttfhr_p75_from_pr_window,
)

_PILLARS = ("responsiveness", "criticality", "governance", "rigor", "accessibility")


def make_record(label: str, *, owner: str = "acme", name: str | None = None, **overrides: object) -> ScoreRecord:
    """A fully-measured, internally consistent ``ScoreRecord``. ``label`` is
    stashed in ``raw_hash`` purely so tests can recover row identity after
    :func:`~cvi.core.tiebreak.assign_ranks` rebuilds the record (see the
    module docstring); it plays no role in any tie-break comparison.
    ``owner``/``name`` (defaulting to ``"acme"``/``label``, matching
    :func:`make_entry`'s own defaults) build ``repo`` so a record built here
    always agrees with the ``TieBreakInput`` a caller wraps it in."""
    responsiveness_score = overrides.pop("responsiveness_score", 80.0)
    pillars = {name: PillarScore(score=80.0, coverage=1.0) for name in _PILLARS}
    pillars["responsiveness"] = PillarScore(score=responsiveness_score, coverage=1.0)  # type: ignore[arg-type]
    base = dict(
        repo=RepoIdentity(
            owner=owner,
            name=name if name is not None else label,
            ecosystem="pypi",
            archived=False,
            mirror_url=None,
            fork=False,
            license_spdx=None,
            default_branch=None,
        ),
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
    resolved_name = name if name is not None else label
    return TieBreakInput(
        record=make_record(label, owner=owner, name=resolved_name, **record_overrides),
        owner=owner,
        name=resolved_name,
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


def test_tie_key_composite_pinned_at_two_decimals() -> None:
    """W2 (round-2 review, M17/M18): D-TIE-1's "composite (2 decimals)"
    level must round to exactly 2 decimals inside :func:`tie_key`, not 3,
    and not skip rounding entirely. ``round(91.238, 2) == round(91.24, 2)
    == 91.24``, so a1 and a2 tie at level 1 and coverage (level 2) decides
    -- a2's 0.60 beats a1's 0.50. A 3-decimal or unrounded key treats
    ``91.24 > 91.238`` directly and never reaches coverage, inverting the
    order to ``['a1', 'a2']``."""
    a1 = make_entry("a1", composite=91.24, coverage=0.50)
    a2 = make_entry("a2", composite=91.238, coverage=0.60)
    ranked = assign_ranks([a1, a2])
    assert _labels_in_order(ranked) == ["a2", "a1"]


def test_tie_group_boundary_pinned_at_two_decimals() -> None:
    """W3 (round-2 review, M11/M19/M20): D-TIE-2's tie-group boundary must
    also round to exactly 2 decimals. ``88.004`` rounds to ``88.0`` (same
    group as an ``88.0`` row); ``88.006`` rounds to ``88.01`` (its own,
    different group). Asserted by comparing ``tie_group`` *values* for both
    pairs, never by a crash: the review found the 1-/0-decimal mutants were
    only "killed" via a ``KeyError`` raised by the module's separately
    duplicated rounding, which is not evidence this boundary is pinned."""
    base1 = make_entry("base1", composite=88.0)
    base2 = make_entry("base2", composite=88.004)
    other1 = make_entry("other1", composite=88.006)
    other2 = make_entry("other2", composite=88.006)
    singleton = make_entry("singleton", composite=70.5)

    ranked = assign_ranks([singleton, other1, base1, other2, base2])
    tie_group_by_label = {record.raw_hash: tg for record, _rank, tg in ranked}

    assert tie_group_by_label["base1"] == tie_group_by_label["base2"]
    assert tie_group_by_label["base1"] is not None
    assert tie_group_by_label["other1"] == tie_group_by_label["other2"]
    assert tie_group_by_label["other1"] is not None
    assert tie_group_by_label["base1"] != tie_group_by_label["other1"]
    assert tie_group_by_label["singleton"] is None


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
# B1 (round-2 review): ``_commit_epoch`` must never build a naive datetime.
#
# Fail-before, measured on the pre-fix code with ``TZ=Asia/Kolkata``:
#   _commit_epoch('2026-06-01')             -> 1780252200.0
#   _commit_epoch('2026-06-01T00:00:00Z')   -> 1780272000.0
# a 5.5-hour shift from building a naive datetime and letting
# ``.timestamp()`` read the host's local zone. The same two-row case built
# below (a-row date-only, b-row explicit ``Z``) then ordered
# ``['b-row', 'a-row']`` under ``TZ=Asia/Kolkata`` and ``['a-row', 'b-row']``
# under ``TZ=UTC`` on the pre-fix code -- confirmed by temporarily
# reintroducing the naive-datetime line and rerunning both subprocesses
# during this round's work.
# ---------------------------------------------------------------------------


def test_commit_epoch_treats_offsetless_iso_string_as_utc() -> None:
    """A date-only ISO string and the same instant written with an explicit
    ``Z`` offset must be the identical epoch: neither is naive, both are
    UTC. `04` section 3 types the neighbouring ``window_start`` field
    ``str | None  # ISO date``, so the date-only form is a first-class input
    this module must accept, not a caller error."""
    assert _commit_epoch("2026-06-01") == _commit_epoch("2026-06-01T00:00:00Z") == pytest.approx(
        1780272000.0
    )


def test_commit_epoch_rejects_a_non_iso_string_naming_the_value() -> None:
    with pytest.raises(ValueError, match="not-a-date"):
        _commit_epoch("not-a-date")


def test_commit_epoch_orders_identically_under_different_host_timezones(
    tmp_path: Path,
) -> None:
    """Runs a two-row ``assign_ranks`` case -- tied on every level except
    the commit date, where a-row's date is date-only and b-row's is the
    same instant with an explicit ``Z`` -- in two real subprocesses, one
    under ``TZ=Asia/Kolkata`` and one under ``TZ=UTC``, and asserts the
    resulting order is identical. Real subprocesses (not a monkeypatched
    ``TZ`` in this process) are required because glibc reads ``TZ`` once at
    process start; the pre-fix code gave ``['b-row', 'a-row']`` under
    Kolkata and ``['a-row', 'b-row']`` under UTC (see the module note
    above) -- the fixed code must give the same order both times."""
    script = tmp_path / "tz_case.py"
    out_template = str(tmp_path / "order_{tz}.txt")
    script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "from cvi.core.tiebreak import assign_ranks\n"
        "from tests.core.test_tiebreak import make_entry\n"
        "a = make_entry('a-row', last_non_bot_commit_at='2026-06-01')\n"
        "b = make_entry('b-row', last_non_bot_commit_at='2026-06-01T00:00:00Z')\n"
        "ranked = assign_ranks([b, a])\n"
        "order = ','.join(r.raw_hash for r, _rank, _tg in ranked)\n"
        "Path(sys.argv[1]).write_text(order)\n"
    )
    repo_root = str(Path(__file__).resolve().parents[2])
    orders: dict[str, str] = {}
    for tz in ("Asia/Kolkata", "UTC"):
        env = dict(os.environ)
        env["TZ"] = tz
        env["PYTHONPATH"] = repo_root
        out_path = out_template.format(tz=tz.replace("/", "-"))
        subprocess.run(
            [sys.executable, str(script), out_path],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        orders[tz] = Path(out_path).read_text()
    assert orders["Asia/Kolkata"] == orders["UTC"] == "a-row,b-row"


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


def test_unrated_ordering_distinguishes_forward_from_reversed_name() -> None:
    """C2 (round-2 review, M21): the pair above ('gated-a', 'unrated-z')
    happens to be invariant under string reversal -- forward and reversed
    comparison agree on which sorts first -- so a mutated ``_name_key``
    that reverses the name before comparing (``name.lower()[::-1]``)
    survives it. 'ax' < 'xa' forward ('a' < 'x' at position 0); reversed,
    'xa' < 'ax' (the reversed keys are 'xa' and 'ax' respectively, and
    'ax' < 'xa'), which puts the *other* row first -- a pair that flips
    order under reversal, unlike the first test's."""
    forward_first = make_entry(
        "forward-first", name="ax", composite=None,
        gate=GateResult(eligible=False, reason="unrated: insufficient_evidence", routed=None, undecided=()),
        coverage=0.0, tier=None, leverage=None, friction=None, quadrant=None,
    )
    reversed_would_win = make_entry(
        "reversed-would-win", name="xa", composite=None,
        gate=GateResult(eligible=False, reason="unrated: insufficient_evidence", routed=None, undecided=()),
        coverage=0.0, tier=None, leverage=None, friction=None, quadrant=None,
    )
    ranked = assign_ranks([reversed_would_win, forward_first])
    assert _labels_in_order(ranked) == ["forward-first", "reversed-would-win"]


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


def test_tiebreak_input_rejects_owner_name_mismatch_with_record_repo() -> None:
    """W2-10 (04 section 3): ``owner``/``name`` must agree with
    ``record.repo`` -- one source of identity, validated. The message must
    name both sides so a caller can see which one is wrong."""
    record = make_record("x", owner="real-owner", name="real-repo")
    with pytest.raises(ValueError) as excinfo:
        TieBreakInput(record=record, owner="wrong-owner", name="real-repo")
    assert "wrong-owner" in str(excinfo.value)
    assert "real-owner" in str(excinfo.value)

    with pytest.raises(ValueError) as excinfo:
        TieBreakInput(record=record, owner="real-owner", name="wrong-repo")
    assert "wrong-repo" in str(excinfo.value)
    assert "real-repo" in str(excinfo.value)


def test_tiebreak_input_accepts_a_matching_owner_name_pair() -> None:
    record = make_record("x", owner="real-owner", name="real-repo")
    entry = TieBreakInput(record=record, owner="real-owner", name="real-repo")
    assert entry.owner == "real-owner"
    assert entry.name == "real-repo"


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
# SQL parity (04 section 6's ORDER BY).
#
# W4 (round-2 review): 04 section 6 now mandates ``NULLS LAST`` on every
# tie-break column (ruling 2026-09-13, after this review) because SQLite
# sorts NULL *first* under ``ASC`` by default -- the inverse of D-TIE-1's
# "unmeasured sorts after measured regardless of direction" for an ASC
# column (``friction``, ``ttfhr_p75``). The DESC columns agree with the
# naive clause only by coincidence (SQLite already puts NULLs last under
# DESC), which is why an all-measured or partially-measured sample can pass
# a naive parity test and still ship an inverted leaderboard the moment an
# ASC column goes NULL. ``_CORRECTED_ORDER_BY`` below is transcribed
# verbatim from 04 section 6; ``_NAIVE_ORDER_BY`` is the pre-ruling clause,
# kept only as this test's negative control.
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE t (
    owner TEXT, name TEXT, composite REAL, coverage REAL,
    p_responsiveness REAL, friction REAL, leverage REAL,
    ttfhr_p75 REAL, last_human_commit_at TEXT
)
"""

_NAIVE_ORDER_BY = """
SELECT name FROM t ORDER BY
    round(composite, 2) DESC, coverage DESC, p_responsiveness DESC,
    friction ASC, leverage DESC, ttfhr_p75 ASC,
    last_human_commit_at DESC, lower(owner || '/' || name) ASC
"""

_CORRECTED_ORDER_BY = """
SELECT name,
    ROW_NUMBER() OVER (ORDER BY
        round(composite, 2) DESC NULLS LAST, coverage DESC NULLS LAST,
        p_responsiveness DESC NULLS LAST, friction ASC NULLS LAST,
        leverage DESC NULLS LAST, ttfhr_p75 ASC NULLS LAST,
        last_human_commit_at DESC NULLS LAST,
        lower(owner || '/' || name) ASC) AS rank_p,
    DENSE_RANK() OVER (ORDER BY round(composite, 2) DESC) AS tie_group
FROM t
ORDER BY rank_p
"""


def _build_sqlite_table(conn: sqlite3.Connection, entries: list[TieBreakInput]) -> None:
    conn.execute(_SCHEMA_SQL)
    for entry in entries:
        pillar = entry.record.pillars.get("responsiveness")
        conn.execute(
            "INSERT INTO t VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry.owner, entry.name, entry.record.composite, entry.record.coverage,
                pillar.score if pillar is not None else None,
                entry.record.friction, entry.record.leverage,
                entry.ttfhr_p75_hours, entry.last_non_bot_commit_at,
            ),
        )


def test_sql_order_by_agrees_with_tie_key_on_the_fully_measured_eight_row_case() -> None:
    """The original 8-row parity case: every field measured, so it cannot
    exercise NULL-ordering at all (see
    ``test_sql_order_by_with_nulls_needs_nulls_last_and_matches_tie_key``
    below for that). Renamed from
    ``test_sql_order_by_agrees_with_tie_key_on_the_eight_row_case`` (W4):
    the old name's ``agrees`` claimed general parity, not just parity on an
    all-measured sample."""
    conn = sqlite3.connect(":memory:")
    _build_sqlite_table(conn, _eight_rows())
    rows = conn.execute(_NAIVE_ORDER_BY).fetchall()
    conn.close()
    sql_order = [row[0] for row in rows]
    python_order = [e.name for e in sorted(_eight_rows(), key=tie_key)]
    assert sql_order == python_order == _EXPECTED_ORDER


def _nine_null_bearing_rows() -> list[TieBreakInput]:
    """9 rows, all composite 88.0 / coverage 0.70 (so levels 3-8 decide
    everything), with a NULL at four different ASC/DESC tie levels
    (responsiveness DESC, friction ASC, leverage DESC, ttfhr_p75 ASC) plus
    one NULL commit date (DESC) -- the multi-level NULL case a
    single-NULL-column sample cannot exercise. Baseline values are shared
    by every row except the one field each label calls out; each ``m-``
    row wins its level outright on a distinctly better measured value, each
    ``n-`` row is unmeasured at exactly one level and must fall after every
    measured row at that level regardless of direction."""
    baseline_responsiveness, baseline_friction = 60.0, 50.0
    baseline_leverage, baseline_ttfhr = 40.0, 100.0
    baseline_commit = "2020-01-01T00:00:00Z"

    def row(label: str, **overrides: object) -> TieBreakInput:
        fields: dict[str, object] = dict(
            composite=88.0, coverage=0.70,
            responsiveness_score=baseline_responsiveness, friction=baseline_friction,
            leverage=baseline_leverage, ttfhr_p75_hours=baseline_ttfhr,
            last_non_bot_commit_at=baseline_commit,
        )
        fields.update(overrides)
        return make_entry(label, name=label, **fields)  # type: ignore[arg-type]

    return [
        row("m-resp-hi", responsiveness_score=95.0),
        row("m-fric-lo", friction=5.0),
        row("m-lev-hi", leverage=90.0),
        row("m-ttfhr-lo", ttfhr_p75_hours=10.0),
        row("n-commit-null", last_non_bot_commit_at=None),
        row("n-ttfhr-null", ttfhr_p75_hours=None),
        row("n-lev-null", leverage=None),
        row("n-fric-null", friction=None),
        row("n-resp-null", responsiveness_score=None),
    ]


_NINE_NULL_BEARING_EXPECTED_ORDER = [
    "m-resp-hi", "m-fric-lo", "m-lev-hi", "m-ttfhr-lo",
    "n-commit-null", "n-ttfhr-null", "n-lev-null", "n-fric-null", "n-resp-null",
]


def test_sql_order_by_with_nulls_needs_nulls_last_and_matches_tie_key() -> None:
    """W4 (round-2 review): parity on the 9-row NULL-bearing case, plus a
    negative control proving the ``NULLS LAST`` clause is load-bearing, not
    decorative. ``_CORRECTED_ORDER_BY`` (04 section 6, current) must
    reproduce :func:`tie_key`'s order exactly, including ``rank_p`` as a
    strict ``1..9`` via ``ROW_NUMBER()``; ``_NAIVE_ORDER_BY`` (04 section 6,
    pre-ruling -- no ``NULLS LAST`` anywhere) must diverge from it, because
    SQLite's default NULL-first-under-ASC promotes ``n-fric-null`` and
    ``n-ttfhr-null`` above measured rows at their respective ASC levels."""
    entries = _nine_null_bearing_rows()
    python_order = [e.name for e in sorted(entries, key=tie_key)]
    assert python_order == _NINE_NULL_BEARING_EXPECTED_ORDER

    conn = sqlite3.connect(":memory:")
    _build_sqlite_table(conn, entries)
    corrected_rows = conn.execute(_CORRECTED_ORDER_BY).fetchall()
    naive_rows = conn.execute(_NAIVE_ORDER_BY).fetchall()
    conn.close()

    corrected_order = [row[0] for row in corrected_rows]
    corrected_ranks = [row[1] for row in corrected_rows]
    naive_order = [row[0] for row in naive_rows]

    assert corrected_order == python_order
    assert corrected_ranks == list(range(1, len(entries) + 1))
    # Negative control: without NULLS LAST, the SQL order does NOT match --
    # the defect the corrected clause exists to fix.
    assert naive_order != python_order


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


def test_ttfhr_p75_nearest_rank_at_n_equals_3_pins_ceil_not_floor() -> None:
    """W1 (round-2 review, M22): ``N=4`` and ``N=1`` are the only two sample
    sizes where ``ceil(0.75*N)`` and ``max(1, floor(0.75*N))`` coincide, so
    neither pins the nearest-rank method. At ``N=3``: ``ceil(0.75*3) =
    ceil(2.25) = 3`` -> 3rd smallest -> 3.0, while the floor mutant gives
    ``floor(2.25) = 2`` -> 2nd smallest -> 2.0. Three external, responded
    events with hours 1, 2, 3."""
    window = PRWindow(
        start="2026-01-01T00:00:00Z",
        end="2026-06-01T00:00:00Z",
        truncated=False,
        events=(
            _event(1, "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"),
            _event(2, "2026-01-02T00:00:00Z", "2026-01-02T02:00:00Z"),
            _event(3, "2026-01-03T00:00:00Z", "2026-01-03T03:00:00Z"),
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
