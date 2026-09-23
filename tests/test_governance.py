"""Tests for Pillar 3 — Governance, Safety & Path-to-Power.

Owner test bar: exact-oracle assertions on the calibrated formulas from
``docs/ARCHITECTURE.md`` sections 3.1-3.3. Every asserted value is the
hand-computed expected value, not a property a no-op implementation also
satisfies.

Oracle summary:
  3.1 Legal IP:   exact LEGAL_IP_SCORES lookup by enum value
                  (DCO=100, FOUNDATION_CLA=75, CORP_CLA_RECIPROCAL=40,
                  CORP_CLA_UNILATERAL=10).
  3.2 EF:         min(100, ef * 25); ef <= 0 guarded to 0.
  3.3 Ladder:     pass-through of PromotionLadder.score
                  (100 / 75 / 40 / 10).
  Pillar:         0.35*legal + 0.35*ef + 0.30*ladder, clamped to [0, 100].

Weight-isolation tests (``TestScoreGovernance``) pin each parameter
weight individually: e.g. with only the legal-IP parameter at 100 and
the other two at their worst-case values (EF=1 -> 25, closed -> 10),
the pillar score is 0.35*100 + 0.35*25 + 0.30*10 = 46.75 — a different
weight assignment yields a different value.
"""

from porchlamp.constants import LEGAL_IP_SCORES
from porchlamp.models import Ecosystem, LegalIPModel, PromotionLadder, RepoMeta
from porchlamp.pillars.governance import (
    score_decentralization,
    score_governance,
    score_legal_ip,
    score_promotion_ladder,
)


def make_meta(**overrides) -> RepoMeta:
    """RepoMeta with pillar-3 fields set; defaults are the raw zero signals."""
    kwargs = dict(owner="acme", name="widget", ecosystem=Ecosystem.PYPI)
    kwargs.update(overrides)
    return RepoMeta(**kwargs)


# --------------------------------------------------------------------------- #
# 3.1 Legal IP Regime & Relicensing Safety
# --------------------------------------------------------------------------- #
class TestScoreLegalIp:
    def test_dco_full(self):
        assert score_legal_ip(LegalIPModel.DCO) == 100.0

    def test_foundation_cla(self):
        assert score_legal_ip(LegalIPModel.FOUNDATION_CLA) == 75.0

    def test_corp_cla_reciprocal(self):
        assert score_legal_ip(LegalIPModel.CORP_CLA_RECIPROCAL) == 40.0

    def test_corp_cla_unilateral(self):
        assert score_legal_ip(LegalIPModel.CORP_CLA_UNILATERAL) == 10.0

    def test_every_enum_value_matches_constant_exactly(self):
        # Exact-oracle sweep: each enum member must map to the exact
        # LEGAL_IP_SCORES entry, and the constant table must cover the
        # whole enum (no missing key, no extra key).
        for model in LegalIPModel:
            assert score_legal_ip(model) == LEGAL_IP_SCORES[model.value]
        assert set(LEGAL_IP_SCORES) == {m.value for m in LegalIPModel}

    def test_scores_are_strictly_ordered(self):
        # DCO > foundation > reciprocal > unilateral; a lookup that
        # swaps any two adjacent entries breaks at least one of these.
        assert (
            score_legal_ip(LegalIPModel.DCO)
            > score_legal_ip(LegalIPModel.FOUNDATION_CLA)
            > score_legal_ip(LegalIPModel.CORP_CLA_RECIPROCAL)
            > score_legal_ip(LegalIPModel.CORP_CLA_UNILATERAL)
        )


# --------------------------------------------------------------------------- #
# 3.2 CHAOSS Elephant Factor / Decentralization
# --------------------------------------------------------------------------- #
class TestScoreDecentralization:
    def test_ef1_single_firm(self):
        assert score_decentralization(1) == 25.0

    def test_ef2(self):
        assert score_decentralization(2) == 50.0

    def test_ef3(self):
        assert score_decentralization(3) == 75.0

    def test_ef4_boundary_exactly_100(self):
        # The cap boundary: EF = 4 scores exactly 100, not > 100.
        assert score_decentralization(4) == 100.0

    def test_ef5_capped(self):
        assert score_decentralization(5) == 100.0

    def test_ef10_capped(self):
        assert score_decentralization(10) == 100.0

    def test_ef_zero_guarded(self):
        # Out-of-domain (missing/corrupt telemetry): guarded to 0, not
        # a negative score.
        assert score_decentralization(0) == 0.0

    def test_ef_negative_guarded(self):
        assert score_decentralization(-3) == 0.0


# --------------------------------------------------------------------------- #
# 3.3 Contributor Promotion Ladder
# --------------------------------------------------------------------------- #
class TestScorePromotionLadder:
    def test_verified_promotion(self):
        assert score_promotion_ladder(PromotionLadder.VERIFIED_PROMOTION) == 100.0

    def test_documented_pathway(self):
        assert score_promotion_ladder(PromotionLadder.DOCUMENTED_PATHWAY) == 75.0

    def test_ad_hoc(self):
        assert score_promotion_ladder(PromotionLadder.AD_HOC) == 40.0

    def test_closed_single_firm(self):
        assert score_promotion_ladder(PromotionLadder.CLOSED_SINGLE_FIRM) == 10.0

    def test_every_ladder_value_matches_enum_score(self):
        # Exact-oracle sweep: scorer must equal the enum's own .score
        # for every member (catches a hand-rolled lookup that drifts
        # from the enum values).
        for ladder in PromotionLadder:
            assert score_promotion_ladder(ladder) == ladder.score


# --------------------------------------------------------------------------- #
# Calibrated pillar combination
# --------------------------------------------------------------------------- #
class TestScoreGovernance:
    def test_perfect_repo(self):
        # DCO + EF=4 + verified: 0.35*100 + 0.35*100 + 0.30*100 = 100
        meta = make_meta(
            legal_ip_model=LegalIPModel.DCO,
            elephant_factor=4,
            promotion_ladder=PromotionLadder.VERIFIED_PROMOTION,
        )
        assert score_governance(meta) == 100.0

    def test_worst_repo(self):
        # UNILATERAL + EF=1 + closed: 0.35*10 + 0.35*25 + 0.30*10 = 15.25
        meta = make_meta(
            legal_ip_model=LegalIPModel.CORP_CLA_UNILATERAL,
            elephant_factor=1,
            promotion_ladder=PromotionLadder.CLOSED_SINGLE_FIRM,
        )
        assert score_governance(meta) == 15.25

    def test_foundation_ef2_documented(self):
        # 0.35*75 + 0.35*50 + 0.30*75 = 26.25 + 17.5 + 22.5 = 66.25
        meta = make_meta(
            legal_ip_model=LegalIPModel.FOUNDATION_CLA,
            elephant_factor=2,
            promotion_ladder=PromotionLadder.DOCUMENTED_PATHWAY,
        )
        assert score_governance(meta) == 66.25

    def test_reciprocal_ef3_ad_hoc(self):
        # 0.35*40 + 0.35*75 + 0.30*40 = 14.0 + 26.25 + 12.0 = 52.25
        meta = make_meta(
            legal_ip_model=LegalIPModel.CORP_CLA_RECIPROCAL,
            elephant_factor=3,
            promotion_ladder=PromotionLadder.AD_HOC,
        )
        assert score_governance(meta) == 52.25

    def test_dco_ef1_closed(self):
        # 0.35*100 + 0.35*25 + 0.30*10 = 35.0 + 8.75 + 3.0 = 46.75
        meta = make_meta(
            legal_ip_model=LegalIPModel.DCO,
            elephant_factor=1,
            promotion_ladder=PromotionLadder.CLOSED_SINGLE_FIRM,
        )
        assert score_governance(meta) == 46.75

    def test_unilateral_ef10_verified(self):
        # 0.35*10 + 0.35*100 + 0.30*100 = 3.5 + 35.0 + 30.0 = 68.5
        meta = make_meta(
            legal_ip_model=LegalIPModel.CORP_CLA_UNILATERAL,
            elephant_factor=10,
            promotion_ladder=PromotionLadder.VERIFIED_PROMOTION,
        )
        assert score_governance(meta) == 68.5

    def test_repo_meta_defaults(self):
        # Defaults: DCO + EF=1 + AD_HOC: 0.35*100 + 0.35*25 + 0.30*40
        # = 35.0 + 8.75 + 12.0 = 55.75
        assert score_governance(make_meta()) == 55.75

    def test_legal_ip_weight_isolation_dco_vs_foundation(self):
        # EF=1 + closed held constant; only the legal-IP parameter moves.
        # DCO:     0.35*100 + 0.35*25 + 0.30*10 = 46.75
        # FOUND:   0.35*75  + 0.35*25 + 0.30*10 = 38.0
        base = dict(elephant_factor=1, promotion_ladder=PromotionLadder.CLOSED_SINGLE_FIRM)
        assert (
            score_governance(make_meta(legal_ip_model=LegalIPModel.DCO, **base))
            == 46.75
        )
        assert (
            score_governance(
                make_meta(legal_ip_model=LegalIPModel.FOUNDATION_CLA, **base)
            )
            == 38.0
        )

    def test_decentralization_weight_isolation(self):
        # DCO + closed held constant; only EF moves.
        # EF=1: 0.35*100 + 0.35*25 + 0.30*10 = 46.75
        # EF=2: 0.35*100 + 0.35*50 + 0.30*10 = 55.5
        base = dict(legal_ip_model=LegalIPModel.DCO, promotion_ladder=PromotionLadder.CLOSED_SINGLE_FIRM)
        assert score_governance(make_meta(elephant_factor=1, **base)) == 46.75
        assert score_governance(make_meta(elephant_factor=2, **base)) == 55.5

    def test_promotion_ladder_weight_isolation(self):
        # DCO + EF=1 held constant; only the ladder moves.
        # verified: 0.35*100 + 0.35*25 + 0.30*100 = 73.75
        # closed:   0.35*100 + 0.35*25 + 0.30*10  = 46.75
        base = dict(legal_ip_model=LegalIPModel.DCO, elephant_factor=1)
        assert (
            score_governance(
                make_meta(promotion_ladder=PromotionLadder.VERIFIED_PROMOTION, **base)
            )
            == 73.75
        )
        assert (
            score_governance(
                make_meta(promotion_ladder=PromotionLadder.CLOSED_SINGLE_FIRM, **base)
            )
            == 46.75
        )

    def test_mixed_signals_all_parameters_unequal(self):
        # FOUNDATION + EF=3 + AD_HOC: 0.35*75 + 0.35*75 + 0.30*40
        # = 26.25 + 26.25 + 12.0 = 64.5
        # Parameter values deliberately unequal (75/75/40): an
        # equal-value mix would be invariant to any weight assignment
        # summing to 1.0.
        meta = make_meta(
            legal_ip_model=LegalIPModel.FOUNDATION_CLA,
            elephant_factor=3,
            promotion_ladder=PromotionLadder.AD_HOC,
        )
        assert score_governance(meta) == 64.5

    def test_result_is_float_in_range(self):
        # Type + range contract: every enum/EF combination must land in
        # [0, 100] as a float.
        for model in LegalIPModel:
            for ladder in PromotionLadder:
                for ef in (0, 1, 2, 3, 4, 5, 10):
                    result = score_governance(
                        make_meta(
                            legal_ip_model=model,
                            elephant_factor=ef,
                            promotion_ladder=ladder,
                        )
                    )
                    assert isinstance(result, float)
                    assert 0.0 <= result <= 100.0
