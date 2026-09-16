"""Tests for :mod:`cvi.snapshot.cli`: ``cvi snapshot freeze`` and
``cvi snapshot reproduce``.

``main`` returns the exit code; the CLI layer handles user-facing output.
The data directory is a synthetic one-repo sample built by
``test_reproduce``'s fixture shape.
"""
from __future__ import annotations

import ast
import inspect
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


# ---------------------------------------------------------------------------
# Corrupt-JSON envelope and stale native hash recorded in the frozen manifest
# ---------------------------------------------------------------------------


def _corrupt(path: Path) -> None:
    """Break the JSON in place: the first '}' becomes 'x'."""
    text = path.read_text(encoding="utf-8")
    i = text.index("}")
    path.write_text(text[:i] + "x" + text[i + 1:], encoding="utf-8")


def test_reproduce_of_a_corrupt_envelope_exits_one_and_names_it(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A corrupt-JSON envelope is reported as INVALID_JSON, exit 1."""
    main(["freeze", "--data-dir", str(data_dir), "--as-of", AS_OF, "--code-commit", COMMIT])
    snapshot_id = _only_snapshot(data_dir)
    _corrupt(data_dir / "raw/github_meta/org__alpha.json")
    capsys.readouterr()
    code = main(["reproduce", snapshot_id, "--data-dir", str(data_dir)])
    printed = capsys.readouterr().out
    assert code == 1
    assert "INVALID_JSON github_meta/org__alpha" in printed
    assert "MISMATCH" in printed
    assert "Traceback" not in printed


def test_freeze_of_a_dir_with_a_corrupt_envelope_completes_and_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With --allow-invalid, freeze completes over the readable
    envelopes, names the corrupt key, records it in the manifest, and exits
    non-zero (writing a degraded manifest is now the operator's explicit
    choice, not the unconditional default)."""
    root = tmp_path / "data"
    _legacy(root, "github_meta", "org__alpha", META)
    _legacy(root, "pr_events", "org__alpha", [])
    _corrupt(_legacy(root, "downloads", "pypi__alpha", {"monthly": 4000}))
    (root / "manifest.json").write_text(
        json.dumps({"repos": [{"repo": "org/alpha", "ecosystem": "pypi"}]}), encoding="utf-8"
    )
    code = main([
        "freeze", "--data-dir", str(root), "--as-of", AS_OF, "--code-commit", COMMIT,
        "--allow-invalid",
    ])
    printed = capsys.readouterr().out
    assert code == 1
    assert "INVALID_JSON downloads/pypi__alpha" in printed
    snapshot_id = _only_snapshot(root)
    manifest = load_manifest(root, snapshot_id)
    assert manifest["envelope_count"] == 2
    assert manifest["invalid_json"] == ["downloads/pypi__alpha"]
    assert manifest["merkle_root"] in printed


# ---------------------------------------------------------------------------
# Freeze must refuse a degraded manifest by default, and a frozen
# invalid_json must fail reproduce forever, even after the corrupt file
# is gone (the manifest is the promotion boundary).
# ---------------------------------------------------------------------------


def test_freeze_refuses_a_corrupt_envelope_without_the_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without --allow-invalid, freeze must not write any manifest when an
    envelope is unreadable."""
    root = tmp_path / "data"
    _legacy(root, "github_meta", "org__alpha", META)
    _legacy(root, "pr_events", "org__alpha", [])
    _corrupt(_legacy(root, "downloads", "pypi__alpha", {"monthly": 4000}))
    (root / "manifest.json").write_text(
        json.dumps({"repos": [{"repo": "org/alpha", "ecosystem": "pypi"}]}), encoding="utf-8"
    )
    code = main(["freeze", "--data-dir", str(root), "--as-of", AS_OF, "--code-commit", COMMIT])
    printed = capsys.readouterr().out
    assert code == 1
    assert "INVALID_JSON downloads/pypi__alpha" in printed
    assert "refusing to freeze" in printed
    assert not (root / "snapshots").exists()


def test_reproduce_of_a_degraded_snapshot_fails_after_the_corrupt_file_is_gone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Freeze --allow-invalid over one corrupt envelope, delete the corrupt
    file, then reproduce. It must fail, naming the key with
    INVALID_JSON_FROZEN, because the frozen manifest still records it."""
    root = tmp_path / "data"
    _legacy(root, "github_meta", "org__alpha", META)
    _legacy(root, "pr_events", "org__alpha", [])
    corrupt_path = _legacy(root, "downloads", "pypi__alpha", {"monthly": 4000})
    _corrupt(corrupt_path)
    (root / "manifest.json").write_text(
        json.dumps({"repos": [{"repo": "org/alpha", "ecosystem": "pypi"}]}), encoding="utf-8"
    )
    code = main([
        "freeze", "--data-dir", str(root), "--as-of", AS_OF, "--code-commit", COMMIT,
        "--allow-invalid",
    ])
    assert code == 1
    snapshot_id = _only_snapshot(root)
    corrupt_path.unlink()
    capsys.readouterr()
    code = main(["reproduce", snapshot_id, "--data-dir", str(root)])
    printed = capsys.readouterr().out
    assert code == 1
    assert "INVALID_JSON_FROZEN downloads/pypi__alpha" in printed
    assert "MISMATCH" in printed


def test_every_name_cli_imports_from_the_snapshot_package_is_exported() -> None:
    """A consumer importing from the package root can name every symbol
    the CLI itself relies on -- EnvelopeScan included, since
    envelope_entries's return type must be nameable too."""
    import cvi.snapshot as snapshot_pkg

    source = Path(inspect.getsourcefile(main)).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("cvi.snapshot."):
            imported.update(alias.name for alias in node.names)
    assert imported  # anti-vacuity: the scan actually found cli.py's two import blocks
    assert imported <= set(snapshot_pkg.__all__)


def test_reproduce_of_a_stale_native_hash_exits_one_and_names_the_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A non-empty hash_mismatches in the frozen manifest cannot
    produce MATCH / exit 0."""
    root = tmp_path / "data"
    _legacy(root, "github_meta", "org__alpha", META)
    _legacy(root, "pr_events", "org__alpha", [])
    (root / "manifest.json").write_text(
        json.dumps({"repos": [{"repo": "org/alpha", "ecosystem": "pypi"}]}), encoding="utf-8"
    )
    path = root / "raw/downloads/pypi__alpha.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "fetched_at": FETCHED_AT, "source": "github-rest:downloads",
            "sha256": "0" * 64, "http_status": 200, "collector_version": "test/1",
            "data": {"monthly": 4000},
        }),
        encoding="utf-8",
    )
    capsys.readouterr()
    code = main(["freeze", "--data-dir", str(root), "--as-of", AS_OF, "--code-commit", COMMIT])
    assert code == 0
    snapshot_id = _only_snapshot(root)
    manifest = load_manifest(root, snapshot_id)
    assert manifest["hash_mismatches"] == ["downloads/pypi__alpha"]
    capsys.readouterr()
    code = main(["reproduce", snapshot_id, "--data-dir", str(root)])
    printed = capsys.readouterr().out
    assert code == 1
    assert "STALE_HASH downloads/pypi__alpha" in printed
    assert "MISMATCH" in printed


def test_build_creates_v2_snapshot_dir(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """cvi snapshot build creates manifest.json, populations.json, and bundles/*.json."""
    from cvi.snapshot.fixture_io import load_frozen_fixture

    _legacy(data_dir, "tree", "org__alpha", {"paths": ["pyproject.toml", "LICENSE"]})
    _legacy(data_dir, "commits_180d", "org__alpha", [{"author_login": "alice"}])
    derived = data_dir / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    derived_doc = {
        "repo": "org/alpha",
        "meta": {
            "owner": "org",
            "name": "alpha",
            "ecosystem": "pypi",
            "stars": 1000,
            "forks": 12,
            "monthly_downloads": 4000,
            "dependents_count": 10,
            "elephant_factor": 2,
            "active_nonbot_commits_180d": 1,
            "scorecard_score": 8.0,
            "legal_ip_model": "DCO",
            "ai_posture": "AD_HOC",
            "promotion_ladder": "AD_HOC",
            "hard_ai_ban": False,
            "off_platform_reviews": False,
            "dx_ci_minutes": 10.0,
            "active_unclaimed_gfis": 1,
            "rr_90": 0.5,
            "mean_inline_comments": 2.0,
            "openssf_signals": None,
        },
        "pr_events": [],
        "provenance": {
            "stars": {"source": str(data_dir / "raw/github_meta/org__alpha.json"), "fetched_at": FETCHED_AT},
            "monthly_downloads": {
                "source": str(data_dir / "raw/downloads/pypi__alpha.json"),
                "fetched_at": FETCHED_AT,
            },
        },
        "confidence": {"stars": "verified", "monthly_downloads": "verified"},
    }
    (derived / "org__alpha.json").write_text(json.dumps(derived_doc), encoding="utf-8")

    out_dir = tmp_path / "out_snap"
    code = main([
        "build",
        "--data-dir", str(data_dir),
        "--out-dir", str(out_dir),
        "--snapshot-id", "snap-test-1",
        "--as-of", AS_OF,
        "--code-commit", COMMIT,
    ])
    printed = capsys.readouterr().out
    assert code == 0
    assert "snapshot snap-test-1" in printed
    assert (out_dir / "manifest.json").is_file()
    assert (out_dir / "populations.json").is_file()
    assert (out_dir / "bundles" / "org__alpha.json").is_file()

    frozen = load_frozen_fixture(out_dir)
    assert frozen.snapshot_id == "snap-test-1"
    assert len(frozen.bundles) == 1
    assert frozen.gate_evidence["org/alpha"].manifest_found is True
    assert frozen.gate_evidence["org/alpha"].license_file_present is True
