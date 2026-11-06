"""Phase 2: Within-Ecosystem ECDF normalization (spec 2, Phase 2; 10.2).

Maps a raw metric value to an ecosystem percentile rank in [0, 100] using the
Empirical Cumulative Distribution Function. Neutralizes cross-ecosystem scale
distortions (e.g. npm's fine-grained packages vs monolithic Crates/Go libs).
"""

from __future__ import annotations

import math
from typing import Iterable


def compute_ecosystem_percentile(
    target_value: float, population: Iterable[float]
) -> float:
    """Return the ECDF percentile rank of ``target_value`` in [0, 100].

    ``R_pct(x) = (1/N) * sum(1[x_k <= x]) * 100`` (spec 10.2).

    An empty population returns the neutral median fallback of 50.0.
    """
    if not math.isfinite(float(target_value)):
        raise ValueError(f"target_value must be finite, got {target_value!r}")
    values = [float(x) for x in population]
    if any(not math.isfinite(x) for x in values):
        raise ValueError("population values must be finite")
    if not values:
        return 50.0
    count_below_or_equal = sum(1 for x in values if x <= target_value)
    exact = (count_below_or_equal / len(values)) * 100.0
    return round(exact, 2)
