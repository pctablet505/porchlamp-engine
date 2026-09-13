"""Snapshot manifests and reproduction (packet W0-3, `04` §2 ``snapshot/``).

``freeze`` freezes the envelope hashes of a data dir into
``snapshots/<id>/manifest.json`` with a Merkle root; ``reproduce`` rebuilds the
database from those envelopes and compares a canonical dump hash. Envelope
hashing itself lives in :mod:`cvi.collect.envelope` (one definition per fact).

W4-6f: ``cvi.snapshot.store_cmd``'s five public names (below) are exposed
LAZILY, via module ``__getattr__`` (PEP 562), never imported at package
load time. Eagerly importing them here created a real circular import on
main (found by the W4-4 reviewer, reproduced as
``python -c "import cvi.store.writer"``): ``cvi.store.writer`` imports
``cvi.snapshot.populations``, which -- as a submodule import -- forces
Python to first run this very ``__init__.py`` if ``cvi.snapshot`` is not
already in ``sys.modules``; that run used to eagerly import
``cvi.snapshot.store_cmd``, which imports names FROM ``cvi.store.writer``
-- the exact module still mid-execution one frame up, so those names do
not exist on it yet (``ImportError: cannot import name 'RepoWrite' from
partially initialized module``). The cycle only shows up when
``cvi.store.writer`` (or anything that imports it) is the FIRST ``cvi``
import of the process -- which nothing in this suite ever does, since
some other module always imports ``cvi.snapshot`` (harmlessly, at that
point) first. See ``tests/snapshot/test_import_order.py`` for a subprocess
test that pins the actual import order this bug needs to reproduce.

``__getattr__`` defers the ``cvi.store.writer`` import until one of these
five names is actually READ off this package (``cvi.snapshot.
score_snapshot_dir``, say) -- by then the process's imports have long
settled, so the cycle never gets a chance to form. ``__all__`` still lists
them (a plain list of strings, no import needed to build it) so
``tests/snapshot/test_cli.py``'s
``test_every_name_cli_imports_from_the_snapshot_package_is_exported`` --
which checks membership in ``__all__``, never that the name is already
bound as a module attribute -- keeps passing unchanged."""

from __future__ import annotations

from typing import Any

from cvi.snapshot.manifest import (
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
from cvi.snapshot.reproduce import (
    SnapshotError,
    canonical_dump,
    canonical_dump_hash,
    rebuild,
    reproduce,
)

#: Names ``cvi.snapshot.store_cmd`` exposes, resolved lazily by
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
    """PEP 562 lazy attribute lookup: ``cvi.snapshot.<name>`` for one of
    :data:`_LAZY_STORE_CMD_NAMES` imports ``cvi.snapshot.store_cmd`` on
    FIRST access only (Python caches the result on this module after
    the first successful lookup -- ``__getattr__`` is not called again
    for a name already set as a real module attribute), so the
    ``cvi.store.writer`` import this triggers never happens during this
    package's own import, only whenever a caller actually asks for one
    of these five names."""
    if name in _LAZY_STORE_CMD_NAMES:
        from cvi.snapshot import store_cmd

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
    "VerificationError",
    "build_manifest",
    "canonical_dump",
    "canonical_dump_hash",
    "code_commit",
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
