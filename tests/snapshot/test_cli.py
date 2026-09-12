"""Tests for :mod:`cvi.snapshot.cli` (packet W0-3): ``cvi snapshot freeze`` and
``cvi snapshot reproduce``.

``main`` returns the exit code; the CLI layer is the only place in the packet
that prints (R-8). The data directory is a synthetic one-repo sample built by
``test_reproduce``'s fixture shape.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from cvi.snapshot.cli import main
from cvi.snapshot.manifest import MANIFEST_FILENAME, load_manifest

FETCHED_AT = "2026-09-11T19:49:53.263996+00:00"
AS_OF = "2026-09-12T00:00:00+00:00"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
META: dict[str, Any] = {
    "stars": 1000,
    "forks": 12,
    "pushed_at": "2026-09-01T00:00:00Z",
    "archived": False,
    "created_at": "2020-01-01T00:00:00Z",
    "license_spdx": "MIT",
    "default_branch": "main",
}


def _legacy(data_dir: Path, collector: str, key: str, data: Any) -> Path:
    path = data_dir / "raw" / collector / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"fetched_at": FETCHED_AT, "source": f"github-rest:{collector}", "data": data}),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    _legacy(root, "github_meta", "org__alpha", META)
    _legacy(root, "pr_events", "org__alpha", [])
    _legacy(root, "downloads", "pypi__alpha", {"monthly": 4000})
    (root / "manifest.json").write_text(
        json.dumps({"repos": [{"repo": "org/alpha", "ecosystem": "pypi"}]}), encoding="utf-8"
    )
    return root


def _only_snapshot(data_dir: Path) -> str:
    """The id of the single snapshot a freeze wrote."""
    return next(p.name for p in (data_dir / "snapshots").iterdir() if p.is_dir())


def test_main_prints_usage_with_no_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "usage" in capsys.readouterr().out.lower()


def test_freeze_writes_a_manifest_with_the_frozen_hashes(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main([
        "freeze", "--data-dir", str(data_dir), "--as-of", AS_OF, "--code-commit", COMMIT,
    ])
    printed = capsys.readouterr().out
    snapshots = data_dir / "snapshots"
    ids = [path.name for path in snapshots.iterdir() if path.is_dir()]
    assert code == 0
    assert len(ids) == 1
    manifest = load_manifest(data_dir, ids[0])
    assert manifest["as_of"] == AS_OF
    assert manifest["code_commit"] == COMMIT
    assert manifest["envelope_count"] == 3
    assert manifest["hash_origins"] == {"native": 0, "backfilled": 3}
    assert (snapshots / ids[0] / MANIFEST_FILENAME).is_file()
    assert ids[0] in printed
    assert manifest["merkle_root"] in printed


def test_reproduce_of_an_untouched_sample_exits_zero(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["freeze", "--data-dir", str(data_dir), "--as-of", AS_OF, "--code-commit", COMMIT])
    snapshot_id = _only_snapshot(data_dir)
    capsys.readouterr()
    code = main(["reproduce", snapshot_id, "--data-dir", str(data_dir)])
    printed = capsys.readouterr().out
    assert code == 0
    assert "MATCH" in printed


def test_reproduce_of_a_flipped_envelope_exits_one_and_names_the_key(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["freeze", "--data-dir", str(data_dir), "--as-of", AS_OF, "--code-commit", COMMIT])
    snapshot_id = _only_snapshot(data_dir)
    path = data_dir / "raw/github_meta/org__alpha.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"stars": 1000', '"stars": 9000'),
                    encoding="utf-8")
    capsys.readouterr()
    code = main(["reproduce", snapshot_id, "--data-dir", str(data_dir)])
    printed = capsys.readouterr().out
    assert code == 1
    assert "github_meta/org__alpha" in printed
    assert "MISMATCH" in printed
