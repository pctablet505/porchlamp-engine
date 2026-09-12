"""Collectors: network fetches that each return one envelope (04 section 2).

Only :mod:`cvi.collect.envelope` lives here at packet W0-3: the contract, the
write/read pair and the single hashing definition. The collector modules that
fill it in land in W1.
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
