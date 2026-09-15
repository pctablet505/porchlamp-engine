"""Collectors: network fetches that each return one envelope (04 section 2).

:mod:`cvi.collect.envelope` defines the envelope contract, the write/read pair
and the single hashing definition re-exported here.
"""

from __future__ import annotations

from cvi.collect.envelope import (
    COLLECTOR_VERSION,
    Envelope,
    canonical_json,
    envelope_hash,
    read_envelope,
    sha256_hex,
    write_envelope,
)

__all__ = [
    "COLLECTOR_VERSION",
    "Envelope",
    "canonical_json",
    "envelope_hash",
    "read_envelope",
    "sha256_hex",
    "write_envelope",
]
