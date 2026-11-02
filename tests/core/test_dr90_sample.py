"""Subject: which projects 1.2 DR_90 samples, and what it draws (RFC-0028).

Everything comes from the stored ``pr_window``, so collect and derive must answer alike. The rule samples only above a
trigger and only where the sample cannot change the score; a band whose rate rests on a few prolific authors must not
pass as high just because the rate is high.
"""
from __future__ import annotations

import pytest

from porchlamp.core import constants, gates
from porchlamp.derive import bundle
from tests.collect.test_pr_window import NOW, w4_days


def _band(n: int, rate: float = 1.0) -> list[dict]:
    """``n`` authors, one PR each; the first ``rate`` of them decided in 5 days, the others after 200."""
    decided = round(n * rate)
    return [w4_days(2000 + i, 120, f"u{i:04d}", closed_after=5 if i < decided else 200) for i in range(n)]


def test_at_the_trigger_every_author_is_read_and_one_above_it_the_sample_is():
    assert len(gates.dr90_band_logins(_band(200), NOW)) == 200
    assert len(gates.dr90_band_logins(_band(201), NOW)) == constants.DR90_SAMPLE_AUTHORS == 150


def test_a_mid_rate_band_is_counted_in_full_whatever_its_size():
    assert len(gates.dr90_band_logins(_band(600, 0.5), NOW)) == 600


@pytest.mark.parametrize("rate, sampled", [(0.91, True), (0.90, False)])
def test_the_margin_and_the_confidence_decide_near_the_line(rate, sampled):
    """1,000 single-PR authors. The line moves by a whole point if either constant stops being read."""
    assert len(gates.dr90_band_logins(_band(1000, rate), NOW)) == (150 if sampled else 1000)


def test_the_line_is_the_score_curve_s_own():
    """Above ``100 / RATE_SLOPE`` the score is 100. A slope of 100 puts the line at rate 1.0, so nothing can pass."""
    authors = gates._dr90_band_authors(_band(1000), NOW)
    assert gates.dr90_sample_applies(authors)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(constants, "RATE_SLOPE", 100.0)
        assert not gates.dr90_sample_applies(authors)


def test_a_high_rate_that_rests_on_prolific_authors_is_counted_in_full():
    """The orca shape: the rate looks saturated, but a draw that misses the few heavy authors reads far below it.
    A binomial on the rate alone (authors = PRs) would sample this; the authors' own counts must not."""
    prs = [w4_days(10000 + 100 * a + i, 120, f"heavy{a}", closed_after=5) for a in range(8) for i in range(90)]
    prs += [w4_days(2000 + i, 120, f"u{i:04d}", closed_after=5 if i % 4 else 200) for i in range(392)]
    authors = gates._dr90_band_authors(prs, NOW)
    total = sum(m for m, _d in authors.values())
    rate = sum(d for _m, d in authors.values()) / total
    binomial = constants.DR90_SAMPLE_Z * (rate * (1 - rate) * (1 - 150 / 400) / 150) ** 0.5
    assert rate - binomial - constants.DR90_SAMPLE_GATE_MARGIN >= 0.80      # or this test proves nothing
    assert not gates.dr90_sample_applies(authors)
    assert len(gates.dr90_band_logins(prs, NOW)) == 400


def test_the_draw_is_pinned_so_collect_and_derive_cannot_drift():
    drawn = gates.dr90_band_logins(_band(600), NOW)
    assert gates.dr90_band_digest(drawn) == "83149bdc6856a5021abf9468a85a956122ecad56944f7aae68020ca6a472cc0f"


def test_the_draw_does_not_depend_on_the_order_of_the_prs():
    prs = _band(600)
    assert gates.dr90_band_logins(prs, NOW) == gates.dr90_band_logins(list(reversed(prs)), NOW)


def test_the_rule_counts_decisions_as_derive_does():
    """Its inputs are the same PRs and the same 90 days, inclusive: merged or closed, either one."""
    prs = [w4_days(1, 120, "a", closed_after=90), w4_days(2, 120, "b", closed_after=91),
           w4_days(3, 120, "c", merged_after=90), w4_days(4, 120, "d", merged_after=91),
           w4_days(5, 120, "e"), w4_days(6, 180, "f", closed_after=5), w4_days(7, 89, "g", closed_after=5),
           w4_days(8, 181, "h", closed_after=5)]
    authors = gates._dr90_band_authors(prs, NOW)
    decided, kept, *_ = bundle._dr90_decision_cohort({"prs": prs}, NOW)
    assert sum(m for m, _d in authors.values()) == kept
    assert sum(d for _m, d in authors.values()) == decided == 3


def test_a_draw_filters_the_cohort_and_no_draw_reads_all_of_it():
    prs = _band(600)
    records = {f"u{i:04d}": {"login": f"u{i:04d}", "account_created_at": "2015-01-01T00:00:00Z", "public_event_count": 50}
               for i in range(600)}
    drawn = gates.dr90_band_logins(prs, NOW)
    assert bundle._dr90_decision_cohort({"prs": prs}, NOW, records)[1] == 600
    assert bundle._dr90_decision_cohort({"prs": prs}, NOW, records, drawn=drawn)[1] == 150
