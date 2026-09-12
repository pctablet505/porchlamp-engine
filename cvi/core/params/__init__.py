"""CVI v2 parameter functions (packet W2-3, 02 sections 4-5).

One function per parameter (18 total), one module per pillar. Each function
takes the FeatureBundle input for its parameter (``bundle.inputs``) and
returns the calibrated ``ParamScore`` (score, measured, reason, n, method);
curves are the absolute calibrations of 02 section 4 and the minimum-n rule
of the same section. D-PARAM-MONO: every curve is monotone non-decreasing in
"better for the contributor"; :func:`monotonicity_violations` asserts it
(tests/core/test_params_monotonic.py).

The ``PARAMS`` registry maps each frozen input id (constants_v2
``PARAM_INPUT_IDS``) to its ``ParamDef``; W2-5 aggregation consumes it.
"""
from __future__ import annotations

from typing import Dict, List

from cvi.core import constants_v2
from cvi.core.params.accessibility import (
    DEFS as _ACCESSIBILITY_DEFS,
)
from cvi.core.params.accessibility import (
    dx,
    newcomer_conversion,
    newcomer_issue_supply,
    rr90_return_rate,
)
from cvi.core.params.criticality import DEFS as _CRITICALITY_DEFS
from cvi.core.params.criticality import dependents, downloads, openssf_criticality
from cvi.core.params.curves import (
    KIND_ECDF,
    KIND_ENUM,
    KIND_NUMERIC,
    ParamDef,
    monotonicity_violations,
    reference_ecdf,
)
from cvi.core.params.governance import DEFS as _GOVERNANCE_DEFS
from cvi.core.params.governance import elephant_factor, legal_ip, promotion_ladder
from cvi.core.params.rigor import DEFS as _RIGOR_DEFS
from cvi.core.params.rigor import ai_posture, pipeline_defenses, substantive_review_rate
from cvi.core.params.responsiveness import DEFS as _RESPONSIVENESS_DEFS
from cvi.core.params.responsiveness import dr90, gr14, mr_first, ttfhr, ttm_p90

#: The 18 registered parameter curves, keyed by frozen input id, pillar order.
PARAMS: Dict[str, ParamDef] = {
    defn.input_id: defn
    for pillar_defs in (
        _RESPONSIVENESS_DEFS,
        _CRITICALITY_DEFS,
        _GOVERNANCE_DEFS,
        _RIGOR_DEFS,
        _ACCESSIBILITY_DEFS,
    )
    for defn in pillar_defs
}


def _registry_problems() -> List[str]:
    """Structural invariants of the registry, checked at import time."""
    problems: List[str] = []
    expected = set(constants_v2.PARAM_INPUT_IDS)
    for missing in sorted(expected - set(PARAMS)):
        problems.append(f"input id {missing!r} has no registered curve")
    for extra in sorted(set(PARAMS) - expected):
        problems.append(f"input id {extra!r} is registered but not in PARAM_INPUT_IDS")
    pillar_totals: Dict[str, float] = {}
    for input_id, defn in sorted(PARAMS.items()):
        if defn.input_id != input_id:
            problems.append(f"PARAMS key {input_id!r} does not match defn.input_id {defn.input_id!r}")
        if defn.pillar not in constants_v2.PILLAR_NAMES:
            problems.append(f"{input_id}: pillar {defn.pillar!r} is not a frozen pillar name")
        if not 0.0 < defn.weight <= 1.0:
            problems.append(f"{input_id}: weight {defn.weight} outside (0, 1]")
        pillar_totals[defn.pillar] = pillar_totals.get(defn.pillar, 0.0) + defn.weight
        if defn.kind in (KIND_NUMERIC, KIND_ECDF):
            if not (
                isinstance(defn.domain, tuple)
                and len(defn.domain) == 2
                and defn.domain[0] < defn.domain[1]
                and isinstance(defn.higher_is_better, bool)
            ):
                problems.append(f"{input_id}: {defn.kind} curve needs a domain (lo < hi) and higher_is_better")
        elif defn.kind != KIND_ENUM:
            problems.append(f"{input_id}: unknown kind {defn.kind!r}")
    for pillar in constants_v2.PILLAR_NAMES:
        if pillar not in pillar_totals:
            problems.append(f"pillar {pillar!r} has no registered parameters")
    for pillar, total in sorted(pillar_totals.items()):
        if abs(total - 1.0) > 1e-9:
            problems.append(f"pillar {pillar!r} within-pillar weights sum to {total}, expected 1.0")
    return problems


_PROBLEMS = _registry_problems()
if _PROBLEMS:
    raise ValueError("cvi.core.params registry is inconsistent: " + "; ".join(_PROBLEMS))

__all__ = [
    "PARAMS",
    "ParamDef",
    "ai_posture",
    "dependents",
    "dx",
    "dr90",
    "downloads",
    "elephant_factor",
    "gr14",
    "legal_ip",
    "monotonicity_violations",
    "mr_first",
    "newcomer_conversion",
    "newcomer_issue_supply",
    "openssf_criticality",
    "pipeline_defenses",
    "promotion_ladder",
    "reference_ecdf",
    "rr90_return_rate",
    "substantive_review_rate",
    "ttfhr",
    "ttm_p90",
]
