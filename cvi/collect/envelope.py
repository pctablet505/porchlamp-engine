"""The envelope contract: write, read and hash one raw fetch (`03` §5).

An envelope is the unit of provenance in CVI. A derived value points at the
path of the envelope it came from and repeats that envelope's hash, so
"where did this number come from" is always answerable with a path plus a hash
and can be re-checked without re-fetching.

Contract (one envelope per collector per key, JSON on disk)::

    {
      "source": "github-graphql:repository",
      "fetched_at": "2026-09-11T19:49:51.804283+00:00",
      "http_status": 200,
      "etag": null,
      "sha256": "<hex>",
      "collector_version": "gather/1.0",
      "data": ...
    }

``sha256`` is the SHA-256 hex digest of :func:`canonical_json` of the ``data``
field -- never of the whole file, so reformatting an envelope cannot change its
identity. An envelope with no ``sha256`` on disk is hashed by
:func:`read_envelope` at read time and reports
``hash_origin == "backfilled"``, which is how a manifest distinguishes a hash
the collector recorded from one the reader reconstructed.

This module owns the only canonical-JSON encoder and the only hash helper in
the codebase; everything that needs a content hash imports them here.
"""

from __future__ import annotations

import datetime as _datetime
import enum
import hashlib
import json
from collections.abc import Mapping, Sequence, Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional, Union

#: Version stamp written into every envelope this codebase collects. Bump it in
#: this module only: it is the single definition of "which collector wrote this".
COLLECTOR_VERSION = "gather/1.0"

#: ``native``: the collector wrote ``sha256``. ``backfilled``: absent on disk, so
#: the reader hashed the ``data`` field at read time.
HashOrigin = Literal["native", "backfilled"]


def _key(key: Any) -> str:
    """Encode a mapping key as the string the canonical form uses."""
    if isinstance(key, enum.Enum):
        return f"{type(key).__name__}.{key.name}"
    if isinstance(key, str):
        return key
    return str(key)


def _plain(value: Any) -> Any:
    """Reduce ``value`` to JSON primitives so the encoding is total and stable.

    Enums (including str-valued ones) become ``Type.MEMBER``, dates and datetimes
    become ISO-8601, sets and
    frozensets become sorted lists, anything else falls back to ``str``. Tuple
    and list both become JSON arrays, so a value that is semantically the same
    always encodes the same way.
    """
    if isinstance(value, enum.Enum):
        return f"{type(value).__name__}.{value.name}"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (_datetime.datetime, _datetime.date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Mapping):
        return {_key(name): _plain(item) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (AbstractSet, frozenset)):
        return sorted(_plain(item) for item in value)
    if isinstance(value, Sequence):
        return [_plain(item) for item in value]
    return str(value)


def canonical_json(value: Any) -> str:
    """Encode ``value`` as canonical JSON: sorted keys, no insignificant space.

    The one canonical-JSON definition in the codebase. It is a function of the
    value only, never of insertion order, container type or float ``repr``
    spacing, which is what makes a hash over it reproducible.
    """
    return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    """SHA-256 hex digest of ``text`` encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def envelope_hash(data: Any) -> str:
    """Hash an envelope's ``data`` field: ``sha256(canonical_json(data))``."""
    return sha256_hex(canonical_json(data))


def raw_envelope_path(data_dir: Union[Path, str], collector: str, key: str) -> str:
    """The one envelope-path formula: ``<data_dir>/raw/<collector>/<key>.json``.

    Every caller that needs a raw envelope path goes through this function, so
    the layout has a single definition.
    """
    return str(Path(data_dir) / "raw" / collector / f"{key}.json")


def relative_envelope_path(root: Union[Path, str], path: Union[Path, str]) -> str:
    """Express an envelope ``path`` relative to the data ``root``, as a
    POSIX string (docs/v2/00-README.md non-negotiables).

    Provenance is an envelope path plus its content hash, and that path
    must never be absolute: an absolute path names the machine that
    derived it, which makes the record non-portable, unreproducible on a
    different machine, and a filesystem-layout leak. A caller that has
    ``root`` and ``path`` already joined (for example ``raw_envelope_path
    (root, collector, key)``) gets the relative form straight back --
    ``raw/<collector>/<key>.json`` -- so recording the *relative* path
    costs nothing beyond calling this function instead of ``str(path)``.

    Raises :class:`ValueError` when ``path`` is not ``root`` or a
    descendant of it: a path outside the data root cannot be recorded as
    provenance at all (never silently truncated or left absolute)."""
    return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()


@dataclass(frozen=True)
class Envelope:
    """One raw fetch as read from disk, with the hash the reader attests.

    ``sha256`` is the attested hash: the one the collector recorded when it is
    present, otherwise the one computed here. ``computed_sha256`` is always the
    hash of the bytes on disk right now, so ``hash_matches_data is False`` means
    the envelope no longer says what it said when it was written.
    """

    path: str
    source: str
    fetched_at: str
    http_status: Optional[int]
    etag: Optional[str]
    collector_version: Optional[str]
    data: Any
    recorded_sha256: Optional[str]
    computed_sha256: str

    @property
    def sha256(self) -> str:
        """The hash the provenance chain repeats for this envelope."""
        return self.recorded_sha256 if self.recorded_sha256 else self.computed_sha256

    @property
    def hash_origin(self) -> HashOrigin:
        """``native`` when the collector recorded the hash, else ``backfilled``."""
        return "native" if self.recorded_sha256 else "backfilled"

    @property
    def hash_matches_data(self) -> bool:
        """Whether a recorded hash still describes the data on disk.

        A backfilled envelope recorded nothing, so there is nothing to
        contradict: it matches by definition. Only a recorded hash that differs
        from the data is a mismatch.
        """
        return self.recorded_sha256 is None or self.recorded_sha256 == self.computed_sha256

    @property
    def provenance(self) -> str:
        """The provenance string: envelope path plus its hash (`03` §5)."""
        return f"{self.path}#{self.sha256}"


def read_envelope(path: Union[Path, str]) -> Envelope:
    """Read an envelope, hashing its ``data`` field when no hash is recorded.

    Raises :class:`json.JSONDecodeError` for a file that is not JSON and
    :class:`FileNotFoundError` for a path with no envelope at it.
    """
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    data = document.get("data")
    recorded = document.get("sha256")
    return Envelope(
        path=str(path),
        source=document.get("source") or "",
        fetched_at=document.get("fetched_at") or "",
        http_status=document.get("http_status"),
        etag=document.get("etag"),
        collector_version=document.get("collector_version"),
        data=data,
        recorded_sha256=recorded if isinstance(recorded, str) and recorded else None,
        computed_sha256=envelope_hash(data),
    )


def write_envelope(
    path: Union[Path, str],
    source: str,
    data: Any,
    fetched_at: str,
    http_status: int = 200,
    etag: Optional[str] = None,
    collector_version: str = COLLECTOR_VERSION,
) -> Path:
    """Write one envelope with its hash and return the path written.

    ``fetched_at`` is passed in by the caller: a collector owns its own clock,
    this contract only records the value. ``http_status`` defaults to 200
    because the write path runs only after a transport that raises on a
    non-2xx response (`gh api` exits non-zero, ``urlopen`` raises ``HTTPError``).

    The body written is ``_plain(data)``, the same reduction
    :func:`envelope_hash` hashes, not ``data`` itself. The two differ for any
    value JSON does not represent natively -- an ``Ecosystem.PYPI`` hashes as
    ``"Ecosystem.PYPI"`` but ``json.dumps`` writes a ``str``-valued enum as
    ``"pypi"`` -- so writing the raw value produced a file whose recorded hash
    did not match its own contents. :func:`read_envelope` re-hashes the body
    it reads, so every such envelope reported itself as tampered with the
    moment it was read back, including immediately after being written.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = _plain(data)
    target.write_text(
        json.dumps(
            {
                "source": source,
                "fetched_at": fetched_at,
                "http_status": http_status,
                "etag": etag,
                "sha256": envelope_hash(body),
                "collector_version": collector_version,
                "data": body,
            }
        ),
        encoding="utf-8",
    )
    return target
