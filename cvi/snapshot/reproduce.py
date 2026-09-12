"""Rebuild a snapshot from ``raw/`` and compare its canonical dump hash.

Reproduction is the proof that a snapshot is a real artifact and not a story:
every envelope in the manifest is re-hashed, the ``derive -> verify -> promote``
chain runs again over exactly those envelopes with the snapshot's ``as_of``, and
the resulting SQLite file is reduced to a canonical dump whose hash must equal
the one the manifest recorded (`03` §5).

Canonical dump: tables sorted by name, the rows of a table sorted by their
canonical JSON, columns in declared order, and floats rendered with
:data:`FIXED_FLOAT_DECIMALS` fixed decimal places, so neither row insertion
order nor a platform-dependent float ``repr`` can move the hash.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Optional, Sequence

from cvi.audit.coverage import GITHUB_KEYED_COLLECTORS
from cvi.collect.envelope import canonical_json, read_envelope, sha256_hex
from cvi.models import Ecosystem
from cvi.snapshot.manifest import (
    envelope_entries,
    entry_leaf,
    load_manifest,
    merkle_root,
    snapshot_dir,
)

#: Decimal places used for every float in the canonical dump. Fixed, so a
#: shorter ``repr`` on another build cannot silently change a hash.
FIXED_FLOAT_DECIMALS = 9

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"


class SnapshotError(RuntimeError):
    """The snapshot cannot be rebuilt as recorded, with the reason inside."""


@dataclass(frozen=True)
class EnvelopeCheck:
    """Result of re-hashing every envelope a manifest lists.

    ``mismatched`` names the envelopes whose bytes no longer hash to the frozen
    value, ``missing`` the ones that are gone, ``unexpected`` the ones added
    after the freeze. Keys are ``<collector>/<key>``, the same form the manifest
    uses in ``backfilled_keys``.
    """

    root: str
    matched: int
    mismatched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        """True when the data dir still holds exactly the frozen envelopes."""
        return not (self.mismatched or self.missing or self.unexpected)


@dataclass(frozen=True)
class ReproduceReport:
    """One reproduction attempt: the rebuilt dump hash against the frozen one."""

    snapshot_id: str
    dump_hash: str
    frozen_dump_hash: str
    envelope_check: EnvelopeCheck

    @property
    def ok(self) -> bool:
        """True only when the envelopes and the rebuilt database both match."""
        return self.envelope_check.clean and self.dump_hash == self.frozen_dump_hash


def format_cell(value: Any) -> Any:
    """Render one SQLite value as a JSON-safe cell of the canonical dump.

    Floats become fixed-decimal strings (the one place a float is rendered),
    ``NULL`` stays JSON null so it cannot be confused with the string "null",
    integers keep their value, and anything exotic falls back to the canonical
    encoder.
    """
    if isinstance(value, float):
        return f"{value:.{FIXED_FLOAT_DECIMALS}f}"
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, bytes):
        return value.hex()
    return canonical_json(value)


def canonical_dump(db_path: Path) -> list[str]:
    """Return the canonical dump of a database: sorted ``<table> <row>`` lines."""
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    lines: list[str] = []
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            columns = [
                row[1] for row in conn.execute(f"PRAGMA table_info({quoted})").fetchall()
            ]
            selected = ", ".join('"' + column.replace('"', '""') + '"' for column in columns)
            rows = [
                f"{table} {canonical_json([format_cell(cell) for cell in row])}"
                for row in conn.execute(f"SELECT {selected} FROM {quoted}").fetchall()
            ]
            lines.extend(sorted(rows))
    finally:
        conn.close()
    return lines


def canonical_dump_hash(db_path: Path) -> str:
    """Hash of :func:`canonical_dump`: the value a snapshot stores and compares."""
    return sha256_hex("\n".join(canonical_dump(db_path)))


def resolve_ecosystems(data_dir: Path, repos: Sequence[str]) -> dict[str, str]:
    """Map ``owner/name`` to the ecosystem the data dir recorded for it.

    The ecosystem is universe metadata, not envelope content, so it is read from
    the sample manifest (``repos[].ecosystem``) first and
    ``raw/candidates.jsonl`` second. A repo neither names is left out: nothing is
    guessed, and :func:`rebuild` refuses to invent one.
    """
    root = Path(data_dir)
    known: dict[str, str] = {}
    sample = root / "manifest.json"
    if sample.is_file():
        for row in json.loads(sample.read_text(encoding="utf-8")).get("repos", []):
            if isinstance(row, dict) and row.get("repo") and row.get("ecosystem"):
                known[str(row["repo"])] = str(row["ecosystem"])
    candidates = root / "raw" / "candidates.jsonl"
    if candidates.is_file():
        for line in candidates.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("owner") and row.get("name") and row.get("ecosystem"):
                known.setdefault(f"{row['owner']}/{row['name']}", str(row["ecosystem"]))
    return {repo: known[repo] for repo in repos if repo in known}


def manifest_repos(manifest: dict[str, Any]) -> list[str]:
    """Repositories whose GitHub-keyed envelopes the manifest lists."""
    repos = set()
    for entry in manifest["envelopes"]:
        if entry["collector"] not in GITHUB_KEYED_COLLECTORS or "__" not in entry["key"]:
            continue
        owner, _, name = entry["key"].partition("__")
        repos.add(f"{owner}/{name}")
    return sorted(repos)


def check_envelopes(data_dir: Path, manifest: dict[str, Any]) -> EnvelopeCheck:
    """Re-hash the frozen envelopes and report every difference by key."""
    mismatched: list[str] = []
    missing: list[str] = []
    matched = 0
    for entry in manifest["envelopes"]:
        key = f"{entry['collector']}/{entry['key']}"
        try:
            envelope = read_envelope(Path(data_dir) / entry["path"])
        except (FileNotFoundError, ValueError):
            missing.append(key)
            continue
        if envelope.computed_sha256 == entry["sha256"]:
            matched += 1
        else:
            mismatched.append(key)
    current = envelope_entries(data_dir)
    frozen = {f"{entry['collector']}/{entry['key']}" for entry in manifest["envelopes"]}
    unexpected = sorted(
        f"{entry['collector']}/{entry['key']}"
        for entry in current
        if f"{entry['collector']}/{entry['key']}" not in frozen
    )
    return EnvelopeCheck(
        root=merkle_root([entry_leaf(entry) for entry in current]),
        matched=matched,
        mismatched=mismatched,
        missing=missing,
        unexpected=unexpected,
    )


def _scripts() -> dict[str, ModuleType]:
    """Load the pipeline scripts the rebuild chains, by path."""
    loaded: dict[str, ModuleType] = {}
    for name in ("derive", "verify", "promote"):
        module_name = f"cvi_snapshot_{name}"
        existing = sys.modules.get(module_name)
        if existing is not None:
            loaded[name] = existing
            continue
        spec = importlib.util.spec_from_file_location(module_name, _SCRIPTS / f"{name}.py")
        if spec is None or spec.loader is None:
            raise SnapshotError(f"cannot load pipeline script: {_SCRIPTS / f'{name}.py'}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        loaded[name] = module
    return loaded


def rebuild(data_dir: Path, manifest: dict[str, Any], work_dir: Path) -> Path:
    """Rerun derive, verify and promote over the manifest's envelopes.

    ``derived`` and ``verified`` are written inside ``work_dir``, never in the
    data dir, and ``work_dir/raw`` is a symlink to the frozen ``raw`` so the
    second-source checks still find their envelopes. Returns the fresh database.
    """
    root = Path(data_dir)
    work = Path(work_dir)
    if work.exists():
        shutil.rmtree(work)
    (work / "derived").mkdir(parents=True)
    (work / "verified").mkdir()
    os.symlink(root / "raw", work / "raw")

    scripts = _scripts()
    as_of = datetime.fromisoformat(manifest["as_of"])
    repos = manifest_repos(manifest)
    ecosystems = resolve_ecosystems(root, repos)
    unresolved = [repo for repo in repos if repo not in ecosystems]
    if unresolved:
        raise SnapshotError(
            "no ecosystem recorded for "
            f"{len(unresolved)} repo(s) in the manifest, first: {unresolved[0]}"
        )
    for repo in repos:
        owner, _, name = repo.partition("/")
        key = f"{owner}__{name}"
        bundle = scripts["derive"].derive_repo(
            owner, name, Ecosystem(ecosystems[repo]), data_dir=root, fetch=False, now=as_of
        )
        (work / "derived" / f"{key}.json").write_text(
            json.dumps(bundle, indent=2, sort_keys=True), encoding="utf-8"
        )
        scripts["verify"].verify_repo(owner, name, work)

    bundles = scripts["promote"].load_clean(work)
    database = work / "cvi.db"
    scripts["promote"].promote(bundles, str(database), as_of, work)
    return database


def reproduce(
    data_dir: Path, snapshot: str, work_dir: Optional[Path] = None
) -> ReproduceReport:
    """Rebuild snapshot ``snapshot`` and compare it with what the manifest says."""
    root = Path(data_dir)
    manifest = load_manifest(root, snapshot)
    check = check_envelopes(root, manifest)
    work = Path(work_dir) if work_dir else snapshot_dir(root, snapshot) / "reproduce"
    dump_hash = canonical_dump_hash(rebuild(root, manifest, work))
    return ReproduceReport(
        snapshot_id=snapshot,
        dump_hash=dump_hash,
        frozen_dump_hash=manifest["canonical_dump_hash"],
        envelope_check=check,
    )
