"""phone/archive.py: deterministic archives, marker gating, manifest contents."""

from __future__ import annotations

import pytest

from phone.archive import build_archive, completed_layers, write_manifest
from tests.phone import MARKER_TEXT, make_home


def test_archive_is_deterministic(tmp_path):
    home = make_home(tmp_path / "a", layers=("L1", "L0"))
    first = build_archive(home, "L1")
    (home / "archives" / "L1.tar.gz").unlink()
    (home / "archives" / "L1.meta.json").unlink()
    second = build_archive(home, "L1")
    assert first["sha256"] == second["sha256"]
    assert first["bytes"] == second["bytes"]
    # The same files in a different home (other path, other mtimes) give the same bytes.
    other = make_home(tmp_path / "b", layers=("L1", "L0"))
    for p in (other / "raw").rglob("*.json"):
        p.touch()
    assert build_archive(other, "L1")["sha256"] == first["sha256"]


def test_archive_skips_a_layer_without_its_marker(tmp_path):
    home = make_home(tmp_path, layers=("L1", "L0"))
    # L2 has envelopes on disk but no .complete marker: it must never be archived or listed.
    (home / "raw" / "repo_object").mkdir(parents=True)
    (home / "raw" / "repo_object" / "partial.json").write_text("{}", encoding="utf-8")
    manifest = write_manifest(home)
    assert list(manifest["layers"]) == ["L1", "L0"]
    assert not (home / "archives" / "L2.tar.gz").exists()
    with pytest.raises(FileNotFoundError):
        build_archive(home, "L2")


def test_manifest_lists_only_completed_layers(tmp_path):
    home = make_home(tmp_path, layers=("L4", "L1"), files_per=3)
    assert completed_layers(home) == ["L1", "L4"]  # LAYERS order, not marker-creation order
    manifest = write_manifest(home)
    assert list(manifest["layers"]) == ["L1", "L4"]
    assert manifest["candidates_sha256"] is None
    entry = manifest["layers"]["L4"]
    assert entry["archive"] == "L4.tar.gz" and entry["files"] == 3 and entry["completed_at"] == MARKER_TEXT
    (home / "raw" / "candidates.jsonl").write_text('{"repo": "a/b"}\n', encoding="utf-8")
    import hashlib

    expected = hashlib.sha256((home / "raw" / "candidates.jsonl").read_bytes()).hexdigest()
    assert write_manifest(home)["candidates_sha256"] == expected


def test_manifest_main_done_follows_marker(tmp_path):
    home = make_home(tmp_path, layers=("L1",), files_per=2)
    assert write_manifest(home)["main_done"] is None
    done = home / "state" / "refresh" / "MAIN.done"
    done.write_text("2026-10-11T04:00:00Z\n", encoding="utf-8")
    manifest = write_manifest(home)
    assert manifest["main_done"] == "2026-10-11T04:00:00Z"
    assert (home / "archives" / "manifest.json").is_file()
