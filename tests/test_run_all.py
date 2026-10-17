"""Tests for scripts/run_all.py's freeze-then-promote wiring.

Verification: :func:`freeze_and_promote` must promote under
the id ITS OWN freeze call reported, never by re-discovering "the newest
manifest" (a forward-dated or stray manifest already under
``snapshots/`` wins a lexical sort over the run's own freeze). The sweep
loop itself (gather -> derive -> verify per candidate repo) does live
network calls and is out of scope for an offline test; this file exercises
only the freeze/promote tail ``main`` runs after the sweep.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("run_all", ROOT / "scripts" / "run_all.py")
run_all = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_all)

from porchlamp.snapshot.manifest import build_manifest, write_manifest  # noqa: E402

#: A real repository's envelopes, used so the freeze this test runs covers
#: a genuine, non-empty envelope set committed under ``tests/fixtures/`` rather than read from
#: ``~/Projects/cvi-scratch/sample-200``, absent on any machine but this
#: one, CI included.
_SAMPLE_RAW = Path(__file__).resolve().parent / "fixtures" / "numpy-envelopes"
_SAMPLE_KEY = "numpy__numpy"
_SAMPLE_OWNER, _SAMPLE_NAME, _SAMPLE_ECO = "numpy", "numpy", "pypi"


def _copy_sample_envelopes(data_dir: Path) -> None:
    for collector_dir in sorted(p for p in _SAMPLE_RAW.iterdir() if p.is_dir()):
        src = collector_dir / f"{_SAMPLE_KEY}.json"
        if src.is_file():
            dest = data_dir / "raw" / collector_dir.name / f"{_SAMPLE_KEY}.json"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)


def _derive_and_verify_sample(data_dir: Path) -> None:
    """Derive + verify numpy/numpy for real, so ``load_clean`` finds one
    genuine clean bundle for :func:`run_all.freeze_and_promote` to promote."""
    _copy_sample_envelopes(data_dir)
    candidates = data_dir / "raw" / "candidates.jsonl"
    candidates.parent.mkdir(parents=True, exist_ok=True)
    candidates.write_text(
        json.dumps({"owner": _SAMPLE_OWNER, "name": _SAMPLE_NAME, "ecosystem": _SAMPLE_ECO})
        + "\n", encoding="utf-8")
    bundle = run_all.derive.derive_repo(
        _SAMPLE_OWNER, _SAMPLE_NAME, run_all.derive.Ecosystem(_SAMPLE_ECO),
        data_dir, fetch=False)
    derived_dir = data_dir / "derived"
    derived_dir.mkdir(parents=True, exist_ok=True)
    (derived_dir / f"{_SAMPLE_KEY}.json").write_text(
        json.dumps(bundle, sort_keys=True), encoding="utf-8")
    report = run_all.verify.verify_repo(_SAMPLE_OWNER, _SAMPLE_NAME, data_dir)
    assert report["clean"], f"sample bundle is not clean: {report['flags']}"


def _stray_manifest(data_dir: Path, as_of: str) -> None:
    """Write an empty, unrelated manifest under ``snapshots/`` -- a stand-in
    for a forward-dated or hand-made manifest already sitting there."""
    manifest = build_manifest([], as_of=as_of, code_commit="unknown",
                              canonical_dump_hash="pending")
    write_manifest(data_dir, manifest)


def test_freeze_and_promote_uses_the_id_it_just_froze(tmp_path):
    _derive_and_verify_sample(tmp_path)

    # A forward-dated manifest already present: "lexically-newest
    # directory name" default would pick THIS one over the run's own freeze.
    _stray_manifest(tmp_path, as_of="2027-01-01T00:00:00+00:00")
    # And a stray, oddly-named one -- sorts after every real YYYYMMDD... name.
    hollow = build_manifest([], as_of="2026-01-01T00:00:00+00:00",
                            code_commit="unknown", canonical_dump_hash="pending")
    write_manifest(tmp_path, hollow)
    (tmp_path / "snapshots" / "HOLLOW-SNAPSHOT").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        tmp_path / "snapshots" / hollow["snapshot_id"] / "manifest.json",
        tmp_path / "snapshots" / "HOLLOW-SNAPSHOT" / "manifest.json")

    db = str(tmp_path / "porchlamp.db")
    as_of = datetime(2026, 9, 14, tzinfo=timezone.utc)
    rc = run_all.freeze_and_promote(db, tmp_path, as_of)
    assert rc == 0

    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT owner, name FROM repositories").fetchall()
        bound_snapshot_id = conn.execute(
            "SELECT snapshot_id FROM snapshot_meta").fetchone()[0]
    finally:
        conn.close()
    assert rows == [(_SAMPLE_OWNER, _SAMPLE_NAME)]
    # The bound id names THIS run's own as_of, not the 2027 or hollow ones.
    assert bound_snapshot_id.startswith("20260914T0000000000-")
    assert bound_snapshot_id not in ("2027-01-01T00:00:00+00:00", "HOLLOW-SNAPSHOT")


def test_freeze_and_promote_refuses_when_the_freeze_covers_nothing_to_promote(tmp_path):
    # No candidate repo was ever derived/verified here: freeze succeeds (an
    # empty raw/ is a valid, if trivial, envelope set) but there is nothing
    # in verified/ to promote.
    db = str(tmp_path / "porchlamp.db")
    as_of = datetime(2026, 9, 14, tzinfo=timezone.utc)
    rc = run_all.freeze_and_promote(db, tmp_path, as_of)
    assert rc == 1
    assert not Path(db).exists()


def _record_sweep(monkeypatch, tmp_path, repos):
    """Replace the network steps of ``run_all.main`` (gather, verify) and the freeze with recorders."""
    calls: list[tuple] = []
    monkeypatch.setattr(run_all, "_candidates", lambda limit: list(repos))
    monkeypatch.setattr(run_all.gather, "gather_repo",
                        lambda owner, name, eco, skip_pypistats: calls.append(("gather", owner)))

    def derive_repo(owner, name, eco, data_dir=None, fetch=False, now=None):
        calls.append(("derive", owner, now))
        return {"repo": f"{owner}/{name}"}

    monkeypatch.setattr(run_all.derive, "derive_repo", derive_repo)
    monkeypatch.setattr(run_all.derive, "DATA_DIR", tmp_path)
    monkeypatch.setattr(run_all.verify, "verify_repo", lambda owner, name: {"clean": True})
    monkeypatch.setattr(run_all, "freeze_and_promote", lambda db, data_dir, as_of: calls.append(("freeze", as_of)) or 0)
    return calls


def test_one_as_of_runs_through_every_derivation_and_the_freeze(monkeypatch, tmp_path):
    repos = [("a", "x", "pypi"), ("b", "y", "npm"), ("c", "z", "go")]
    calls = _record_sweep(monkeypatch, tmp_path, repos)
    assert run_all.main(["--all"]) == 0
    kinds = [c[0] for c in calls]
    assert kinds == ["gather"] * 3 + ["derive"] * 3 + ["freeze"], "derivation starts only after the gather sweep"
    stamps = {c[-1] for c in calls if c[0] in ("derive", "freeze")}
    assert len(stamps) == 1 and None not in stamps

    calls.clear()
    assert run_all.main(["--all", "--as-of", "2026-10-13T04:32:26Z"]) == 0
    stamps = {c[-1] for c in calls if c[0] in ("derive", "freeze")}
    assert stamps == {datetime(2026, 10, 13, 4, 32, 26, tzinfo=timezone.utc)}
