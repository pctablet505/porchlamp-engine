"""Tests for the cvi/ingest.py deprecation shim (W4-1).

Every real piece of the old ``cvi/ingest.py`` moved elsewhere or was
deleted (see the module's own docstring); this file now only pins the
shim's two behaviours: it still warns, and it still re-exports the
rate-limiter names so a stray external import does not hard-crash.

Coverage that moved with the code:

* Rate limiter / ETag cache / circuit breaker -- ``tests/collect/
  test_ratelimit.py`` (against ``cvi.collect.ratelimit`` directly).
* ``apply_raw_metrics`` -- ``tests/test_lakehouse.py`` (against
  ``cvi.lakehouse`` directly).

Coverage that did not move (deleted, Tier A dead code, no successor
named in ``04-ARCHITECTURE.md``): the GHArchive bulk-stream stack
(``GHArchiveStreamer``, ``map_pull_request_event``, ``gharchive_hourly_url``,
``_parse_iso8601``) and ``ingest_and_score``.
"""

from __future__ import annotations

import importlib
import sys

import pytest


def test_importing_ingest_emits_deprecation_warning():
    """FAIL-BEFORE: before this shim existed (or if the ``warnings.warn``
    call were removed), importing ``cvi.ingest`` raised no warning at
    all -- this test would then fail on ``pytest.warns``' own "did not
    warn" assertion."""
    sys.modules.pop("cvi.ingest", None)
    with pytest.warns(DeprecationWarning, match="cvi.ingest is deprecated"):
        importlib.import_module("cvi.ingest")


def test_ingest_shim_still_reexports_ratelimit_names():
    """The one-release compatibility guarantee: a caller that has not yet
    updated ``from cvi.ingest import CachedHttpClient`` still gets a
    working class, identical to importing it from the real location."""
    sys.modules.pop("cvi.ingest", None)
    with pytest.warns(DeprecationWarning):
        import cvi.ingest as ingest_shim
    from cvi.collect import ratelimit

    for name in (
        "IngestError",
        "RateLimitedError",
        "TokenBucketRateLimiter",
        "ETagCache",
        "FetchResult",
        "CircuitBreaker",
        "CachedHttpClient",
    ):
        assert getattr(ingest_shim, name) is getattr(ratelimit, name)
