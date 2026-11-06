"""Canonical OpenSSF (Rob Pike) criticality engine, core-only.

Computes the multi-signal criticality score C in [0.0, 1.0] (spec 2.2, 10.1):

    C = sum(alpha_i * S_i*) / sum(|alpha_i|)
    S_i* = log(1 + max(0, S_i)) / log(1 + max(S_i, T_i))

A negative weight (``updated_since``) yields an inverted penalty for
abandoned codebases. The result is clamped to [0, 1].

``porchlamp.core`` may import nothing outside ``porchlamp.core`` and the standard
library (docs/v2/04-ARCHITECTURE.md section 3;
``tests/core/test_import_graph.py`` enforces it), so this module operates on
a plain ``Mapping[str, float]`` keyed by the ten ``porchlamp.models.OpenSSFSignals``
field names rather than importing that dataclass -- exactly the shape
``dataclasses.asdict(OpenSSFSignals(...))`` produces, and exactly the shape
a v1/v2 derived document's ``meta.openssf_signals`` already carries on disk
(``scripts/derive.py`` writes it via that same ``asdict`` call).

This is the single definition of the formula and its default
weights/thresholds: both ``porchlamp.openssf.calculate_pike_criticality`` (v1
pillar scoring) and ``porchlamp.derive.bundle._resolve_openssf_criticality`` (v2
loader) call this one function. See ``porchlamp/openssf.py`` for the
``OpenSSFSignals``-typed public wrapper v1 code uses.

The weights, thresholds and field mapping themselves live in
``porchlamp.core.constants`` and are re-bound below under their original names, so
every caller resolves ``porchlamp.core.openssf.<NAME>``.
"""
from __future__ import annotations

import math
from typing import Mapping, Optional

from porchlamp.core import constants

#: ``porchlamp.models.OpenSSFSignals`` field name -> the alpha_i / T_i key (spec
#: 2.2, 10.1). Iteration order matches the module docstring's signal table
#: in ``porchlamp/collect/pike_signals.py``.
SIGNAL_FIELDS: dict[str, str] = constants.SIGNAL_FIELDS

#: Default signal weights (alpha_i) and thresholds (T_i) — spec 2.2, 10.1.
OPENSSF_WEIGHTS: dict[str, float] = constants.OPENSSF_WEIGHTS
OPENSSF_THRESHOLDS: dict[str, float] = constants.OPENSSF_THRESHOLDS


def calculate_pike_criticality(
    signals: Mapping[str, float],
    weights: Optional[dict[str, float]] = None,
    thresholds: Optional[dict[str, float]] = None,
    *,
    missing: Optional[frozenset[str]] = None,
) -> float:
    """Return the Rob Pike criticality score in [0.0, 1.0] (spec 10.1).

    ``signals`` is a mapping keyed by the ten ``OpenSSFSignals`` field names
    (see :data:`SIGNAL_FIELDS`) — ``dataclasses.asdict(OpenSSFSignals(...))``
    or an equivalent plain dict (e.g. a derived document's
    ``meta.openssf_signals``); every field not named in ``missing`` must be
    present and numeric, which the caller is responsible for having already
    verified — a missing field raises ``KeyError``.

    ``missing`` (a D-MISS-5 consequence for 2.2) names alpha_i/T_i keys
    (:data:`SIGNAL_FIELDS` values, e.g. ``"dependents_count"``) to exclude
    from both the numerator and the denominator -- the score renormalises
    over the *present* signals' weights instead of treating an absent one as
    0.0 against the full denominator (which would bias toward 0 instead of
    neutral). With ``None``/empty (the default) every signal in ``weights``
    contributes.
    """
    weights = weights if weights is not None else OPENSSF_WEIGHTS
    thresholds = thresholds if thresholds is not None else OPENSSF_THRESHOLDS
    missing = missing if missing is not None else frozenset()
    raw = {short: signals[field] for field, short in SIGNAL_FIELDS.items() if short not in missing}

    denominator = sum(abs(alpha) for param, alpha in weights.items() if param not in missing)
    if denominator == 0:
        return 0.0

    numerator = 0.0
    for param, alpha in weights.items():
        if param in missing:
            continue
        if not math.isfinite(float(alpha)):
            raise ValueError(f"weight for {param!r} must be finite, got {alpha!r}")
        value = float(raw.get(param, 0.0))
        threshold = float(thresholds.get(param, 0.0))
        if not math.isfinite(value):
            raise ValueError(f"signal {param!r} must be finite, got {value!r}")
        if not math.isfinite(threshold):
            raise ValueError(f"threshold for {param!r} must be finite, got {threshold!r}")
        non_negative = max(0.0, value)
        saturation_denom = max(value, threshold)
        if saturation_denom <= 0:
            normalized = 0.0
        else:
            normalized = math.log(1.0 + non_negative) / math.log(1.0 + saturation_denom)
        numerator += alpha * normalized

    unbounded = numerator / denominator
    return max(0.0, min(1.0, unbounded))
