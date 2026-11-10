"""Snapshot manifests and reproduction (`04` §2 ``snapshot/``).

``freeze`` freezes the envelope hashes of a data dir into
``snapshots/<id>/manifest.json`` with a Merkle root; ``reproduce`` rebuilds the
database from those envelopes and compares a canonical dump hash. Envelope
hashing itself lives in :mod:`porchlamp.collect.envelope` (one definition per fact).

``porchlamp.snapshot.store_cmd``'s five public names (below) are exposed LAZILY,
via module ``__getattr__`` (PEP 562), never imported at package load time.
Importing them eagerly here creates a circular import: ``porchlamp.store.writer``
imports ``porchlamp.snapshot.populations``, which -- as a submodule import --
forces Python to first run this very ``__init__.py`` if ``porchlamp.snapshot`` is
not already in ``sys.modules``; an eager import of ``porchlamp.snapshot.store_cmd``
in that run would import names FROM ``porchlamp.store.writer``, the exact module
still mid-execution one frame up, so those names do not exist on it yet
(``ImportError: cannot import name 'RepoWrite' from partially initialized
module``). The cycle only shows up when ``porchlamp.store.writer`` (or anything
that imports it) is the FIRST ``porchlamp`` import of the process. See
``tests/snapshot/test_import_order.py`` for a subprocess test that pins that
import order.

``__getattr__`` defers the ``porchlamp.store.writer`` import until one of these
five names is actually READ off this package (``porchlamp.snapshot.
score_snapshot_dir``, say) -- by then the process's imports have long
settled, so the cycle never gets a chance to form. ``__all__`` still lists
them (a plain list of strings, no import needed to build it), so a caller
checking membership in ``__all__`` sees them without forcing the import."""

from __future__ import annotations

from typing import Any

from porchlamp.snapshot.first_pull_requests import compute_first_pull_requests
from porchlamp.snapshot.manifest import (
    PENDING_DUMP_HASH,
    REASON_INVALID_JSON,
    REASON_INVALID_JSON_FROZEN,
    REASON_STALE_HASH,
    SNAPSHOTS_DIRNAME,
    EnvelopeScan,
    build_manifest,
    code_commit,
    constants_hash,
    envelope_entries,
    load_manifest,
    merkle_root,
    snapshot_dir,
    snapshot_id,
    write_manifest,
)
from porchlamp.snapshot.reproduce import (
    SnapshotError,
    canonical_dump,
    canonical_dump_hash,
    rebuild,
    reproduce,
)
from porchlamp.snapshot.stats import StatsError, compute_stats

#: Names ``porchlamp.snapshot.store_cmd`` exposes, resolved lazily by
#: :func:`__getattr__` below -- never imported at this package's own
#: load time (module docstring: the circular-import fix).
_LAZY_STORE_CMD_NAMES = frozenset({
    "VerificationError",
    "score_snapshot_dir",
    "score_snapshot_with_detectors",
    "verify_expected",
    "write_scored_snapshot",
})


def __getattr__(name: str) -> Any:
    """PEP 562 lazy attribute lookup: ``porchlamp.snapshot.<name>`` for one of
    :data:`_LAZY_STORE_CMD_NAMES` imports ``porchlamp.snapshot.store_cmd`` on
    FIRST access only (Python caches the result on this module after
    the first successful lookup -- ``__getattr__`` is not called again
    for a name already set as a real module attribute), so the
    ``porchlamp.store.writer`` import this triggers never happens during this
    package's own import, only whenever a caller actually asks for one
    of these five names."""
    if name in _LAZY_STORE_CMD_NAMES:
        from porchlamp.snapshot import store_cmd

        value = getattr(store_cmd, name)
        globals()[name] = value  # cache: subsequent access skips __getattr__ entirely
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "EnvelopeScan",
    "PENDING_DUMP_HASH",
    "REASON_INVALID_JSON",
    "REASON_INVALID_JSON_FROZEN",
    "REASON_STALE_HASH",
    "SNAPSHOTS_DIRNAME",
    "SnapshotError",
    "StatsError",
    "VerificationError",
    "build_manifest",
    "canonical_dump",
    "canonical_dump_hash",
    "code_commit",
    "compute_first_pull_requests",
    "compute_stats",
    "constants_hash",
    "envelope_entries",
    "load_manifest",
    "merkle_root",
    "rebuild",
    "reproduce",
    "score_snapshot_dir",
    "score_snapshot_with_detectors",
    "snapshot_dir",
    "snapshot_id",
    "verify_expected",
    "write_manifest",
    "write_scored_snapshot",
]
