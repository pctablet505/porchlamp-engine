"""Deprecated. This module is a compatibility shim, kept only in case
something outside this repository still imports it by path.

W4-1 (`.agents/W4-1/inventory.md` sections 4/8): every real piece of
``cvi/ingest.py`` has moved or been deleted:

* The rate limiter / ETag cache / circuit breaker moved verbatim to
  :mod:`cvi.collect.ratelimit` (`04-ARCHITECTURE.md` section 2).
* ``apply_raw_metrics`` moved to :mod:`cvi.lakehouse` (its only argument
  type, ``OperationalStore``, already lives there -- one hop, not two).
* The GHArchive bulk-stream stack (``GHArchiveStreamer``,
  ``map_pull_request_event``, ``gharchive_hourly_url``, ``_parse_iso8601``)
  and ``ingest_and_score`` were Tier A dead code (zero production
  callers, confirmed by the inventory's ``grep`` sweep) with no
  relocation target named anywhere in ``04`` -- deleted outright, not
  moved.

Every in-repo caller was re-pointed at the new locations in the same
change that moved the code; as of this commit nothing in ``cvi/``,
``scripts/`` or ``tests/`` imports this module for anything but the
deprecation-warning test below. Importing it emits ``DeprecationWarning``
and re-exports the rate-limiter names so a stray external
``from cvi.ingest import CachedHttpClient`` still resolves for one
release, per the shim policy in ``04-ARCHITECTURE.md`` section 2
("scripts: thin shims ... kept one release for compatibility") applied
here to a module import path instead of a script entry point.
"""

from __future__ import annotations

import warnings

from .collect.ratelimit import (
    CachedHttpClient,
    CircuitBreaker,
    ETagCache,
    FetchResult,
    IngestError,
    RateLimitedError,
    TokenBucketRateLimiter,
)

__all__ = [
    "IngestError",
    "RateLimitedError",
    "TokenBucketRateLimiter",
    "ETagCache",
    "FetchResult",
    "CircuitBreaker",
    "CachedHttpClient",
]

warnings.warn(
    "cvi.ingest is deprecated and will be removed: the rate limiter, ETag "
    "cache and circuit breaker moved to cvi.collect.ratelimit; "
    "apply_raw_metrics moved to cvi.lakehouse; the GHArchive stack and "
    "ingest_and_score were deleted (dead code, W4-1).",
    DeprecationWarning,
    stacklevel=2,
)
