"""Phone-side archive builder: one deterministic tar.gz per completed refresh layer, plus a manifest.

Layout (see PHONE-1 brief): ``<home>/raw/<collector>/*.json``, ``<home>/state/refresh/<LAYER>.complete``,
``<home>/state/refresh/MAIN.done``, ``<home>/raw/candidates.jsonl``. Archives go to ``<home>/archives/``.
Standard library only: the phone has no pip packages.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import tarfile
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

# Layer -> collectors, in MAIN order, then the keyless LK chain.
LAYERS: dict[str, tuple[str, ...]] = {
    "L1": ("pr_window",),
    "L0": ("github_meta", "pr_events", "pr_reviews", "contributors", "actions_runs", "tree", "commits_180d"),
    "L2": ("repo_object", "gfi", "dependents", "releases", "runs"),
    "L3g": ("governance",),
    "L3c": ("commits_365d", "issues_counts"),
    "L4": ("author_history",),
    "LK": ("depsdev", "scorecard", "downloads"),
}
MAIN_LAYERS: tuple[str, ...] = tuple(k for k in LAYERS if k != "LK")

#: One build at a time (the server answers from several threads); re-entrant because write_manifest builds layers.
_BUILD_LOCK = threading.RLock()


def _temp_file(folder: Path, name: str):
    """A new, uniquely named, world-readable temp file beside its final ``name``: returns ``(file object, path)``."""
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=name + ".", suffix=".tmp")
    os.chmod(tmp, 0o644)
    return os.fdopen(fd, "wb"), Path(tmp)


def _marker_text(path: Path) -> str | None:
    """The one-line text of a marker file, or None when it is absent."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None


def _complete_marker(home: Path, layer: str) -> Path:
    return home / "state" / "refresh" / f"{layer}.complete"


def completed_layers(home: Path) -> list[str]:
    """Layers whose ``state/refresh/<L>.complete`` marker exists, in LAYERS order."""
    return [layer for layer in LAYERS if _complete_marker(home, layer).is_file()]


def _layer_files(home: Path, layer: str) -> list[tuple[str, Path]]:
    """(arcname, path) for every ``raw/<collector>/*.json`` of the layer, sorted by arcname."""
    found: list[tuple[str, Path]] = []
    for collector in LAYERS[layer]:
        folder = home / "raw" / collector
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if path.is_file() and path.name.endswith(".json") and not path.name.startswith("."):
                found.append((f"raw/{collector}/{path.name}", path))
    return sorted(found, key=lambda item: item[0])


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_archive(out: Path, files: list[tuple[str, Path]]) -> None:
    """Write the archive to a unique temp file beside ``out`` and atomically replace ``out``. Deterministic: no
    mtimes, no names."""
    raw, tmp = _temp_file(out.parent, out.name)
    try:
        # filename="" keeps the gzip header free of the temp path; mtime=0 keeps it free of the clock.
        with raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz, \
                tarfile.open(fileobj=gz, mode="w") as tar:
            for arcname, path in files:
                info = tarfile.TarInfo(name=arcname)
                info.size = path.stat().st_size
                info.mtime = 0
                info.mode = 0o644
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                with open(path, "rb") as fh:
                    tar.addfile(info, fh)
        os.replace(tmp, out)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _write_text_atomic(target: Path, text: str) -> None:
    raw, tmp = _temp_file(target.parent, target.name)
    try:
        with raw:
            raw.write(text.encode("utf-8"))
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def build_archive(home: Path, layer: str) -> dict:
    """Build ``archives/<layer>.tar.gz`` unless a current one exists; return its manifest entry.

    Current means: the archive exists, its mtime is not older than the layer's ``.complete`` marker, and the
    cached ``archives/<layer>.meta.json`` was written for the same marker text and archive size.
    Raises KeyError for an unknown layer and FileNotFoundError when the layer has no marker.
    """
    if layer not in LAYERS:
        raise KeyError(layer)
    with _BUILD_LOCK:
        return _build_archive_locked(Path(home), layer)


def _build_archive_locked(home: Path, layer: str) -> dict:
    marker = _complete_marker(home, layer)
    completed_at = _marker_text(marker)
    if completed_at is None:
        raise FileNotFoundError(f"{layer} has no completion marker")
    archives = home / "archives"
    archives.mkdir(parents=True, exist_ok=True)
    out = archives / f"{layer}.tar.gz"
    meta_path = archives / f"{layer}.meta.json"

    if out.is_file() and meta_path.is_file() and out.stat().st_mtime >= marker.stat().st_mtime:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("completed_at") == completed_at and meta.get("bytes") == out.stat().st_size:
            return {**meta, "name": out.name}

    files = _layer_files(home, layer)
    _write_archive(out, files)
    entry = {
        "bytes": out.stat().st_size,
        "sha256": _sha256_file(out),
        "files": len(files),
        "completed_at": completed_at,
    }
    _write_text_atomic(meta_path, json.dumps(entry, sort_keys=True))
    return {**entry, "name": out.name}


def _candidates_sha256(home: Path) -> str | None:
    path = home / "raw" / "candidates.jsonl"
    return _sha256_file(path) if path.is_file() else None


def write_manifest(home: Path) -> dict:
    """Build missing or outdated archives for completed layers, write ``archives/manifest.json``, return it."""
    with _BUILD_LOCK:
        return _write_manifest_locked(Path(home))


def _write_manifest_locked(home: Path) -> dict:
    layers: dict[str, dict] = {}
    for layer in completed_layers(home):
        built = build_archive(home, layer)
        layers[layer] = {
            "archive": built["name"],
            "bytes": built["bytes"],
            "sha256": built["sha256"],
            "files": built["files"],
            "completed_at": built["completed_at"],
        }
    manifest = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "main_done": _marker_text(home / "state" / "refresh" / "MAIN.done"),
        "layers": layers,
        "candidates_sha256": _candidates_sha256(home),
    }
    archives = home / "archives"
    archives.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(archives / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest
