"""Unit tests for :mod:`porchlamp.collect.envelope`.

The envelope contract is `03-ANTI-MANIPULATION.md` section 5: a raw fetch is
stored as ``{source, fetched_at, http_status, etag, sha256,
collector_version, data}`` and its hash is the sha256 of the canonical JSON of
the ``data`` field. Envelopes written before that field existed carry no hash
and are hashed at read time, flagged ``backfilled``.
"""
from __future__ import annotations

import enum
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from porchlamp.collect.envelope import (
    COLLECTOR_VERSION,
    canonical_json,
    envelope_hash,
    raw_envelope_path,
    read_envelope,
    relative_envelope_path,
    sha256_hex,
    write_envelope,
)

FETCHED_AT = "2026-09-12T00:00:00+00:00"
DATA: dict[str, Any] = {"b": 2, "a": [1, {"z": None, "y": True}], "c": 0.5}
# The same object as DATA with its mapping insertion order reversed: canonical
# JSON must not depend on it. List order is data, so it stays as written.
DATA_REORDERED: dict[str, Any] = {"c": 0.5, "b": 2, "a": [1, {"z": None, "y": True}]}
# Golden value of ``canonical_json(DATA)``: keys sorted, no insignificant
# whitespace.
CANONICAL_DATA = '{"a":[1,{"y":true,"z":null}],"b":2,"c":0.5}'
# Golden value of ``envelope_hash(DATA)`` computed independently with hashlib
# over the canonical line above.
DATA_HASH = hashlib.sha256(CANONICAL_DATA.encode("utf-8")).hexdigest()


class Colour(str, enum.Enum):
    """Local enum used to pin how the encoder treats non-str dict keys."""

    RED = "red"


def _legacy(path: Path, data: Any) -> Path:
    """Write a legacy envelope: hash, status, version absent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"fetched_at": FETCHED_AT, "source": "github-rest:issues", "data": data}),
        encoding="utf-8",
    )
    return path


def test_canonical_json_sorts_keys_and_drops_whitespace() -> None:
    assert canonical_json(DATA) == CANONICAL_DATA


def test_canonical_json_ignores_insertion_order() -> None:
    assert canonical_json(DATA_REORDERED) == CANONICAL_DATA


def test_canonical_json_encodes_enums_tuples_and_datetimes() -> None:
    value = {Colour.RED: (1, 2), "when": datetime(2026, 9, 12, 0, 0, tzinfo=timezone.utc)}
    assert canonical_json(value) == (
        '{"Colour.RED":[1,2],"when":"2026-09-12T00:00:00+00:00"}'
    )


def test_envelope_hash_is_sha256_of_the_canonical_data_field() -> None:
    assert envelope_hash(DATA) == DATA_HASH
    assert envelope_hash(DATA) == sha256_hex(canonical_json(DATA))
    assert envelope_hash(DATA_REORDERED) == DATA_HASH


def test_envelope_hash_separates_values_that_differ_by_one_byte() -> None:
    assert envelope_hash({"stars": 1000}) != envelope_hash({"stars": 9000})


def test_write_envelope_writes_the_full_contract(tmp_path: Path) -> None:
    path = write_envelope(
        tmp_path / "raw/github_meta/org__alpha.json",
        source="github-graphql:repository",
        data=DATA,
        fetched_at=FETCHED_AT,
        http_status=200,
        etag=None,
    )
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert sorted(doc) == [
        "collector_version",
        "data",
        "etag",
        "fetched_at",
        "http_status",
        "sha256",
        "source",
    ]
    assert doc["sha256"] == DATA_HASH
    assert doc["http_status"] == 200
    assert doc["collector_version"] == COLLECTOR_VERSION
    assert doc["source"] == "github-graphql:repository"
    assert doc["fetched_at"] == FETCHED_AT
    assert doc["data"] == DATA


def test_read_envelope_of_a_written_envelope_is_native(tmp_path: Path) -> None:
    path = write_envelope(
        tmp_path / "raw/gfi/org__alpha.json",
        source="github-rest:issues",
        data=DATA,
        fetched_at=FETCHED_AT,
    )
    env = read_envelope(path)
    assert env.sha256 == DATA_HASH
    assert env.recorded_sha256 == DATA_HASH
    assert env.hash_origin == "native"
    assert env.hash_matches_data is True
    assert env.http_status == 200
    assert env.collector_version == COLLECTOR_VERSION
    assert env.data == DATA
    assert env.provenance == f"{path}#{DATA_HASH}"


def test_write_then_read_round_trips_the_data_field(tmp_path: Path) -> None:
    path = write_envelope(
        tmp_path / "raw/pr_events/org__alpha.json",
        source="github-graphql:pullRequests",
        data=[{"number": 3, "merged_at": None}],
        fetched_at=FETCHED_AT,
    )
    assert read_envelope(path).data == [{"number": 3, "merged_at": None}]


def test_envelope_without_a_hash_is_hashed_at_read_time(tmp_path: Path) -> None:
    """The read-time backfill rule: ``sha256`` absent hashes the data field."""
    path = _legacy(tmp_path / "raw/gfi/org__alpha.json", DATA)
    env = read_envelope(path)
    assert env.recorded_sha256 is None
    assert env.hash_origin == "backfilled"
    assert env.hash_matches_data is True
    assert env.sha256 == DATA_HASH
    assert env.http_status is None
    assert env.collector_version is None
    assert env.provenance == f"{path}#{DATA_HASH}"


def test_read_time_backfill_is_stable_across_reads(tmp_path: Path) -> None:
    path = _legacy(tmp_path / "raw/gfi/org__alpha.json", DATA)
    assert read_envelope(path).sha256 == read_envelope(path).sha256


def test_recorded_hash_that_disagrees_with_the_data_is_reported(tmp_path: Path) -> None:
    path = write_envelope(
        tmp_path / "raw/github_meta/org__alpha.json",
        source="github-graphql:repository",
        data={"stars": 1000},
        fetched_at=FETCHED_AT,
    )
    tampered = path.read_text(encoding="utf-8").replace('"stars": 1000', '"stars": 9000')
    path.write_text(tampered, encoding="utf-8")
    env = read_envelope(path)
    assert env.hash_origin == "native"
    assert env.hash_matches_data is False
    assert env.sha256 == envelope_hash({"stars": 1000})
    assert env.computed_sha256 == envelope_hash({"stars": 9000})


def test_read_envelope_rejects_a_file_that_is_not_json(tmp_path: Path) -> None:
    path = tmp_path / "raw/gfi/org__alpha.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        read_envelope(path)


# --------------------------------------------------------------------------- #
# relative_envelope_path: provenance is an envelope path plus its             #
# hash, and that path must be relative to the data root -- never absolute,   #
# never a leak of this machine's filesystem layout.                          #
# --------------------------------------------------------------------------- #


def test_relative_envelope_path_strips_the_data_root(tmp_path: Path) -> None:
    envelope = raw_envelope_path(tmp_path, "gfi", "torvalds__linux")
    assert relative_envelope_path(tmp_path, envelope) == "raw/gfi/torvalds__linux.json"


def test_relative_envelope_path_is_posix_even_when_root_has_trailing_slash(tmp_path: Path) -> None:
    envelope = raw_envelope_path(tmp_path, "gfi", "torvalds__linux")
    assert relative_envelope_path(str(tmp_path) + "/", envelope) == "raw/gfi/torvalds__linux.json"


def test_relative_envelope_path_rejects_a_path_outside_the_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / "other-root" / "raw" / "gfi" / "torvalds__linux.json"
    with pytest.raises(ValueError):
        relative_envelope_path(tmp_path, outside)


def test_relative_envelope_path_is_stable_across_two_different_data_roots() -> None:
    """The reproducibility property: the same collector/key pair
    relativises to the identical string under any data root -- two
    "machines" with the same corpus copied to different absolute locations
    produce the same provenance, and therefore the same
    ``feature_provenance_hash``."""
    machine_a = Path("/home/alice/porchlamp-data")
    machine_b = Path("/var/lib/porchlamp/data-copy")
    envelope_a = raw_envelope_path(machine_a, "gfi", "torvalds__linux")
    envelope_b = raw_envelope_path(machine_b, "gfi", "torvalds__linux")
    assert relative_envelope_path(machine_a, envelope_a) == relative_envelope_path(machine_b, envelope_b)
