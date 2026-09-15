"""Shared fixture builders for ``cvi.derive.bundle`` oracle tests.

Every helper here builds a hand-controlled v1 derived-file document (the
``scripts/derive.py`` output shape: ``repo``/``meta``/``provenance``/
``confidence``/``pr_events``) plus the raw envelope files its provenance
entries point at, so each test in this package can assert an exact
:class:`~cvi.core.models.Measured`/:class:`~cvi.core.models.Unmeasured`
value with no unpinned fields.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

KEY = "acme__widget"
REPO = "acme/widget"
ECOSYSTEM = "pypi"
#: Shared fetched_at used across a fixture's envelope and matching provenance
#: entry when a test does not need a distinct value for its own assertion.
FETCHED_AT = "2026-09-01T00:05:00+00:00"


def expected_hash(data: Any) -> str:
    """03 section 5 content hash, recomputed independently of
    ``cvi.core.models.canonical_json``/``sha256_hex`` (same documented
    algorithm: sha256 of sorted-key, no-whitespace JSON), so the oracle does
    not call the code under test to check its own output."""
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_envelope(
    raw_root: Path, collector: str, key: str, data: Any, fetched_at: str = FETCHED_AT
) -> Path:
    """Write a raw envelope file under ``raw_root/<collector>/<key>.json``.

    Only the ``data`` key is read by ``cvi.derive.bundle._envelope_data``;
    the rest of the real ``Envelope`` shape is not needed to exercise the
    loader (matches the existing convention in ``tests/test_derive.py``)."""
    path = raw_root / collector / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fetched_at": fetched_at, "source": "test", "data": data}),
                     encoding="utf-8")
    return path


def recorded_source(collector: str, key: str = KEY) -> str:
    """A v1 provenance ``source`` string with a ``raw/<collector>/<key>.json``
    tail, so ``cvi.derive.bundle._resolve_envelope``'s ``raw_root`` fallback
    resolves it regardless of where the original absolute path pointed."""
    return f"/original-data-dir/raw/{collector}/{key}.json"


def prov(collector: str, fetched_at: str = FETCHED_AT, key: str = KEY) -> dict[str, Any]:
    """One ``provenance[field]`` entry: ``{"source": ..., "fetched_at": ...}``."""
    return {"source": recorded_source(collector, key), "fetched_at": fetched_at}


def base_document(
    *,
    meta: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    confidence: Mapping[str, Any] | None = None,
    pr_events: list[Mapping[str, Any]] | None = None,
    repo: str = REPO,
    ecosystem: str = ECOSYSTEM,
) -> dict[str, Any]:
    """A minimal valid v1 derived document. Every v2 input id this test's
    ``meta``/``provenance``/``confidence`` do not mention falls to its
    resolver's generic "no value recorded" Unmeasured path (verified safe
    for every row in ``test_input_mappings.py``'s ``test_empty_document_*``
    tests) so a fixture only needs to supply the fields its own row reads."""
    return {
        "repo": repo,
        "meta": {"ecosystem": ecosystem, **(meta or {})},
        "provenance": {**(provenance or {})},
        "confidence": {**(confidence or {})},
        "pr_events": pr_events or [],
    }


def write_document(root: Path, document: Mapping[str, Any], key: str = KEY) -> Path:
    path = root / "derived" / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def load(root: Path, document: Mapping[str, Any], key: str = KEY):
    """Write ``document`` to ``root/derived/<key>.json`` and load it back
    through the public :func:`cvi.derive.bundle.load_bundle` entry point
    (not the private ``_load_v1``), with ``raw_root=root/"raw"`` -- the same
    two-step disk round trip ``tests/core/test_sample200_load.py`` uses."""
    from cvi.derive.bundle import load_bundle

    path = write_document(root, document, key)
    return load_bundle(path, raw_root=root / "raw")


def pr_window_data(
    *,
    window_anchor: str = "2026-09-01T00:00:00+00:00",
    window_days: int = 180,
    prs: list[dict[str, Any]] | None = None,
    derived: list[dict[str, Any]] | None = None,
    count: int = 0,
    truncated: bool = False,
    cost_points: int = 0,
) -> dict[str, Any]:
    """The raw ``pr_window`` envelope ``data`` shape
    (``cvi.collect.github_graphql.collect_pr_window``): ``window_anchor``/
    ``window_days`` (bounds), ``prs`` (raw GraphQL PR nodes: ``number``,
    ``createdAt``, ``mergedAt``), ``derived`` (per-PR classifications:
    ``number``, ``is_external``, ``merged``, ``decided``,
    ``substantive_review``)."""
    return {
        "window_anchor": window_anchor,
        "window_days": window_days,
        "count": count,
        "truncated": truncated,
        "cost_points": cost_points,
        "prs": prs or [],
        "derived": derived or [],
    }


def author_history_data(
    *,
    author_records: list[dict[str, Any]] | None = None,
    cost_points: int = 0,
) -> dict[str, Any]:
    """The raw ``author_history`` envelope ``data`` shape
    (``cvi.collect.github_graphql.collect_author_history``): only the
    ``author_records`` list (``login``, ``account_created_at``,
    ``public_event_count``, ``eligibility_unmeasured``,
    ``history_unmeasured``) is read by the DR_90 eligibility join
    (``cvi.derive.bundle._author_history_envelope`` /
    ``_dr90_author_eligible``); the rest of the real envelope shape
    (``sampled_authors``, rate-limit accounting) is not needed here."""
    return {
        "cost_points": cost_points,
        "author_records": author_records or [],
    }
