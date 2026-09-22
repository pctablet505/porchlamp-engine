"""`SnapshotSummary.persona_visible_counts` bound (regression, 2026-09-21).

A persona view is a *re-weighted projection*: `cvi.core.persona` states that
`persona_composite` is "a separate re-weighted projection" whose coverage is
computed from that persona's own pillar weights, and
`cvi.store.queries._persona_visible_where_sql` admits a row on
`persona_composites[<persona>].coverage >= 0.60` -- not on the balanced
`rated` flag. A persona that down-weights the pillars a sweep is still
missing therefore clears the floor for MORE repos than balanced does.

The first snapshot in this project's history with `rated > 0` showed exactly
that: rated=94 while ai_assisted=303 and senior_systems=98. The original
validator asserted `visible <= counts.rated` and rejected it, 500-ing
`GET /v1/cvi/snapshot`. The assertion was unfalsifiable until then, because
every snapshot until then had rated=0, which forces every persona count to 0
and makes the bound pass trivially.

`tests/serve/test_api.py::test_persona_visible_counts_reflect_the_one_persona_gated_row`
cannot catch this: its own comment records that every rated row there has
"uniform 0.9 per-pillar coverage ... so it clears every persona's 0.60 rated
floor regardless of that persona's weights" -- the fixture is built so
re-weighting can never change the visible set.
"""

import pytest
from pydantic import ValidationError

from cvi.serve.contract import SnapshotCounts, SnapshotSummary

_BASE = {
    "snapshot_id": "2026-09-21-L3gp",
    "as_of": "2026-09-21T02:45:16+00:00",
    "benchmark_version": "2.0.0-rc1",
    "constants_hash": "c" * 64,
    "manifest_root": "m" * 64,
    "code_commit": "e9963501e59809d39cc3c49c143f5d77629432be",
    "canonical_dump_hash": "d" * 64,
    "detector_summary": {},
    "reproduce_command": "python -m cvi.snapshot reproduce 2026-09-21-L3gp",
}


def _summary(persona_visible_counts, *, rated=94, gated=3498, unrated=2163):
    return SnapshotSummary(
        **_BASE,
        counts=SnapshotCounts(rated=rated, gated=gated, unrated=unrated, total=rated + gated + unrated),
        persona_visible_counts=persona_visible_counts,
    )


def test_a_persona_may_out_count_balanced_rated():
    """The measured 2026-09-21-L3gp numbers must validate."""
    s = _summary({"ai_assisted": 303, "balanced": 94, "career_capital": 0,
                  "first_timer": 0, "senior_systems": 98})
    assert s.persona_visible_counts["ai_assisted"] == 303
    assert s.counts.rated == 94


def test_persona_count_may_reach_every_non_gated_row():
    """rated + unrated is the bound: persona-visible requires eligible = 1,
    and every eligible row is counted in rated or unrated, never in gated."""
    assert _summary({"ai_assisted": 94 + 2163}).persona_visible_counts["ai_assisted"] == 2257


# --- over-correction guards: the bound must still REJECT these ------------- #

def test_persona_count_above_non_gated_total_is_still_rejected():
    """Relaxing the bound must not remove it: a count that exceeds every
    non-gated row cannot describe a subset of eligible rows."""
    with pytest.raises(ValidationError, match="persona_visible_counts"):
        _summary({"ai_assisted": 94 + 2163 + 1})


def test_negative_persona_count_is_still_rejected():
    with pytest.raises(ValidationError, match="persona_visible_counts"):
        _summary({"ai_assisted": -1})
