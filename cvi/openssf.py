"""Canonical OpenSSF (Rob Pike) criticality engine, ``OpenSSFSignals``-typed
wrapper (spec 2.2, 10.1).

The formula, its default weights/thresholds, and the ten-signal field
mapping live in :mod:`cvi.core.openssf` (a ``cvi.derive``-importable,
stdlib-only module), so this module and ``cvi.derive.bundle`` share one
definition instead of each carrying a full copy. This module re-exports
those constants and exposes the ``OpenSSFSignals``-typed public signature
its v1 callers (``cvi.pillars.criticality``, ``tests/test_openssf.py``)
use, adapting via ``dataclasses.asdict`` before delegating.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Optional

from cvi.core.openssf import OPENSSF_THRESHOLDS, OPENSSF_WEIGHTS
from cvi.core.openssf import calculate_pike_criticality as _calculate_pike_criticality
from .models import OpenSSFSignals

__all__ = ["OPENSSF_THRESHOLDS", "OPENSSF_WEIGHTS", "calculate_pike_criticality"]


def calculate_pike_criticality(
    signals: OpenSSFSignals,
    weights: Optional[dict[str, float]] = None,
    thresholds: Optional[dict[str, float]] = None,
) -> float:
    """Return the Rob Pike criticality score in [0.0, 1.0] (spec 10.1).

    Thin adapter: ``dataclasses.asdict(signals)`` is exactly the mapping
    shape :func:`cvi.core.openssf.calculate_pike_criticality` expects."""
    return _calculate_pike_criticality(asdict(signals), weights=weights, thresholds=thresholds)
