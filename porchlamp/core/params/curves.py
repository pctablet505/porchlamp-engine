"""Porchlamp v2 parameter-curve machinery (02 sections 4-5).

The registry record (:class:`ParamDef`), the shared input-to-ParamScore path
(``_apply``: Unmeasured pass-through, minimum-n rule, curve application,
[0, 100] clamp), the shared cross-pillar rate calibrations of 02 section 4,
the reference ECDF that probes the ECDF-normalised curves, and the
D-PARAM-MONO property check (:func:`monotonicity_violations`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Optional, Tuple

from porchlamp.core import constants
from porchlamp.core.models import ParamScore, ParamValue, Unmeasured

#: Curve kinds a ParamDef may declare: score(value) / score(value, ecdf) /
#: score(regime[, persona]).
KIND_NUMERIC: str = "numeric"
KIND_ECDF: str = "ecdf"
KIND_ENUM: str = "enum"

#: Float tolerance for the D-PARAM-MONO check, re-bound from
#: ``porchlamp.core.constants`` under the original name.
MONO_TOLERANCE: float = constants.MONO_TOLERANCE

#: Fixed probe sample for :func:`reference_ecdf`: 0.0 through 32.0. This is
#: the D-PARAM-MONO property-test probe population, not a per-snapshot
#: frozen population or a scoring input -- see the docstring of
#: :func:`reference_ecdf` below.
_REFERENCE_SAMPLE: Tuple[float, ...] = tuple(float(i) for i in range(33))

#: Shared 02 section 4 rate calibration (1.2 DR_90, 1.3 MR_first, 4.3
#: substantive review rate): full marks at an 80% rate, linear below.
RATE_SLOPE: float = constants.RATE_SLOPE

#: Shared 02 section 4 cohort-rate calibration (5.2 conversion, 5.3 RR_90):
#: full marks at a 50% cohort rate, linear below.
COHORT_RATE_SLOPE: float = constants.COHORT_RATE_SLOPE


@dataclass(frozen=True)
class ParamDef:
    """One registered parameter curve (02 section 4).

    ``score`` is the curve:
      - ``numeric``: ``score(value: float) -> float``
      - ``ecdf``: ``score(value: float, ecdf: Callable[[float], float]) -> float``,
        where ``ecdf`` is a D-NORM-1 mid-rank ECDF on the 0-100 scale
      - ``enum``: ``score(regime: str) -> float``, or
        ``score(regime: str, persona: str) -> float`` when ``persona_orders``
        is set (4.2)
    ``domain`` plus ``higher_is_better`` define the D-PARAM-MONO probe grid
    for numeric/ecdf curves; ``better_order`` (or ``persona_orders``) is the
    worst-to-best value sequence for enum curves. ``min_n`` is the minimum
    sample below which the parameter is ``insufficient_n``; ``proxy_min_n``
    overrides it when the measured method is a proxy (4.1).
    """

    input_id: str
    pillar: str
    weight: float
    kind: str
    score: Callable[..., float]
    min_n: Optional[int] = None
    proxy_min_n: Optional[int] = None
    domain: Optional[Tuple[float, float]] = None
    higher_is_better: Optional[bool] = None
    better_order: Optional[Tuple[str, ...]] = None
    persona_orders: Optional[Dict[str, Tuple[str, ...]]] = None


def fill_rate_score(rate: float) -> float:
    """The shared 02 section 4 rate curve: ``min(100, 125 * rate)``."""
    return min(100.0, RATE_SLOPE * rate)


def cohort_rate_score(rate: float) -> float:
    """The shared 02 section 4 cohort-rate curve: ``min(100, 200 * rate)``."""
    return min(100.0, COHORT_RATE_SLOPE * rate)


def clamped_identity(value: float) -> float:
    """Identity clamped to [0, 100] (02 section 4: 4.1 and 5.4 composites)."""
    return max(0.0, min(100.0, value))


def reference_ecdf(value: float) -> float:
    """Mid-rank ECDF (D-NORM-1) of the fixed reference sample 0.0..32.0, on the 0-100 scale.

    Probe population for the D-PARAM-MONO check of the ECDF-normalised
    curves (1.5, 2.1, 2.3). Not a frozen v2 population: those are
    per-snapshot and per-ecosystem (D-NORM-2)."""
    below = sum(1 for s in _REFERENCE_SAMPLE if s < value)
    tied = sum(1 for s in _REFERENCE_SAMPLE if s == value)
    return 100.0 * (below + 0.5 * tied) / len(_REFERENCE_SAMPLE)


def _required_min_n(defn: ParamDef, method: str) -> Optional[int]:
    """The minimum sample a measurement must meet: ``proxy_min_n`` for proxy methods, else ``min_n``."""
    if method.startswith(constants.PROXY_METHOD_PREFIX) and defn.proxy_min_n is not None:
        return defn.proxy_min_n
    return defn.min_n


def _apply(
    defn: ParamDef,
    value: ParamValue,
    ecdf: Optional[Callable[[float], float]] = None,
    persona: str = "human",
) -> ParamScore:
    """The shared bundle-input -> ParamScore path.

    ``Unmeasured`` inputs pass their D-MISS-1 reason through with
    ``measured=False``; a ``Measured`` input below the minimum sample is
    ``insufficient_n`` (the actual ``n`` is reported); otherwise the curve
    is applied and clamped to [0, 100]. ``ParamScore.value`` and the other
    evidence fields (``window_start``/``window_end``/``source``/
    ``fetched_at``) are copied from the ``Measured`` input whenever one
    exists -- including ``insufficient_n``, where a real measurement was
    taken but did not clear the sample-size bar -- and are ``None`` only for
    an ``Unmeasured`` input, which has no measurement to report."""
    if isinstance(value, Unmeasured):
        return ParamScore(
            value=None,
            score=None,
            measured=False,
            reason=value.reason,
            n=0,
            window_start=None,
            window_end=None,
            method=None,
            source=None,
            fetched_at=None,
        )
    required = _required_min_n(defn, value.method)
    if required is not None and value.n < required:
        return ParamScore(
            value=value.value,
            score=None,
            measured=False,
            reason="insufficient_n",
            n=value.n,
            window_start=value.window_start,
            window_end=value.window_end,
            method=None,
            source=value.source,
            fetched_at=value.fetched_at,
        )
    raw = value.value
    if defn.kind in (KIND_NUMERIC, KIND_ECDF):
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"{defn.input_id}: numeric value required, got {type(raw).__name__}")
        x = float(raw)
        # A non-finite input is not a measurement, and it does not fail loudly
        # further down: every clamp in this module is ``min(100.0, ...)``, and
        # ``min(100.0, nan)`` is 100.0 in Python (the comparison is False, so
        # the first operand wins), so NaN would be scored as full marks.
        # ``porchlamp.core.models.measured_problems`` already rejects these at
        # bundle validation; this is the second line of defence, at the only
        # place a raw value becomes a score.
        if not math.isfinite(x):
            raise ValueError(f"{defn.input_id}: numeric value must be finite, got {x!r}")
        if defn.kind == KIND_ECDF:
            if ecdf is None:
                raise ValueError(f"{defn.input_id}: ECDF-normalised curve requires the frozen-population ECDF")
            score = defn.score(x, ecdf)
        else:
            score = defn.score(x)
    else:
        if not isinstance(raw, str):
            raise ValueError(f"{defn.input_id}: enum value required, got {type(raw).__name__}")
        if defn.persona_orders is not None:
            if persona not in defn.persona_orders:
                raise ValueError(
                    f"{defn.input_id}: unknown persona {persona!r}; expected one of {sorted(defn.persona_orders)}"
                )
            score = defn.score(raw, persona)
        else:
            score = defn.score(raw)
    score = float(score)
    # Same reasoning for the curve's OUTPUT: the clamp below cannot be trusted
    # to bound a NaN, so a curve that produced one must raise rather than
    # silently report 100.0.
    if not math.isfinite(score):
        raise ValueError(f"{defn.input_id}: curve produced a non-finite score {score!r}")
    return ParamScore(
        value=value.value,
        score=max(0.0, min(100.0, score)),
        measured=True,
        reason=None,
        n=value.n,
        window_start=value.window_start,
        window_end=value.window_end,
        method=value.method,
        source=value.source,
        fetched_at=value.fetched_at,
    )


def _grid(domain: Tuple[float, float], points: int) -> List[float]:
    lo, hi = domain
    return [lo + (hi - lo) * i / (points - 1) for i in range(points)]


def _first_violation(pairs: List[Tuple[object, float]]) -> Optional[str]:
    """The first D-PARAM-MONO violation in a worst-to-best (value, score) sequence."""
    for value, score in pairs:
        if score < -MONO_TOLERANCE or score > 100.0 + MONO_TOLERANCE:
            return f"score {score} outside [0, 100] at value {value}"
    for index in range(len(pairs) - 1):
        left_value, left_score = pairs[index]
        right_value, right_score = pairs[index + 1]
        if right_score < left_score - MONO_TOLERANCE:
            return (
                f"score drops from {left_score} to {right_score} moving from {left_value} to "
                f"{right_value} in the better-for-contributor direction"
            )
    return None


def monotonicity_violations(defs: Mapping[str, ParamDef], grid_points: int = 33) -> Dict[str, str]:
    """D-PARAM-MONO property check over a set of registered curves.

    Probes each numeric and ecdf curve at ``grid_points`` values spanning its
    registered ``domain`` and each enum curve over its better-ordering
    (every persona when ``persona_orders`` is set); probe pairs are ordered
    worst-to-best, so a curve admitted by D-PARAM-MONO must score them
    non-decreasing and stay within [0, 100]. Returns a mapping from input id
    to the first violation found; empty means every curve is monotone
    non-decreasing in "better for the contributor"."""
    if isinstance(grid_points, bool) or not isinstance(grid_points, int) or grid_points < 2:
        raise ValueError(f"grid_points must be an int >= 2, got {grid_points!r}")
    violations: Dict[str, str] = {}
    for input_id, defn in defs.items():
        problem: Optional[str] = None
        if defn.kind in (KIND_NUMERIC, KIND_ECDF):
            if not (
                isinstance(defn.domain, tuple)
                and len(defn.domain) == 2
                and defn.domain[0] < defn.domain[1]
                and isinstance(defn.higher_is_better, bool)
            ):
                raise ValueError(f"{defn.input_id}: {defn.kind} curve needs a domain and higher_is_better")
            grid = _grid(defn.domain, grid_points)
            ordered = grid if defn.higher_is_better else list(reversed(grid))
            if defn.kind == KIND_NUMERIC:
                pairs: List[Tuple[object, float]] = [(v, defn.score(v)) for v in ordered]
            else:
                pairs = [(v, defn.score(v, reference_ecdf)) for v in ordered]
            problem = _first_violation(pairs)
        elif defn.kind == KIND_ENUM:
            if defn.persona_orders is not None:
                for persona in sorted(defn.persona_orders):
                    pairs = [
                        (f"{persona}:{regime}", defn.score(regime, persona))
                        for regime in defn.persona_orders[persona]
                    ]
                    problem = _first_violation(pairs)
                    if problem is not None:
                        break
            else:
                if not defn.better_order:
                    raise ValueError(f"{defn.input_id}: enum curve needs a better_order")
                pairs = [(regime, defn.score(regime)) for regime in defn.better_order]
                problem = _first_violation(pairs)
        else:
            raise ValueError(f"{defn.input_id}: unknown kind {defn.kind!r}")
        if problem is not None:
            violations[input_id] = problem
    return violations
