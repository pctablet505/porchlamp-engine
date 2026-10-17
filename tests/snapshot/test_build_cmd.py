"""``porchlamp snapshot build`` driver: the manifest Merkle root covers populations.

The Merkle root in ``manifest.json`` is the integrity anchor of a built
snapshot (D-VER-3). It must cover *every* file the build writes, so that two
builds over the same bundles but different ``populations.json`` produce
different roots (E-01). These tests build a snapshot directory from a copy of
the committed fixture bundles and pin that property.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from porchlamp.core import constants
from porchlamp.core.models import sha256_hex
from porchlamp.snapshot.build_cmd import build_snapshot_dir
from porchlamp.snapshot.manifest import merkle_root
from porchlamp.snapshot.populations import Population, PopulationSet

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "snapshot_50"
_AS_OF = datetime(2026, 9, 12, tzinfo=timezone.utc)
_COMMIT = "0123456789abcdef0123456789abcdef012345678"


def _make_data_dir(tmp_path: Path, count: int = 3) -> Path:
    """Copy ``count`` fixture bundles into a ``data_dir/derived`` with empty ``raw``.

    The fixture bundles are v2 documents, so ``load_bundle`` reads them without
    needing the raw envelopes; an empty ``raw/`` yields all-false gate evidence,
    which is fine because these tests control populations directly."""
    data_dir = tmp_path / "data"
    (data_dir / "derived").mkdir(parents=True)
    (data_dir / "raw").mkdir(parents=True)
    for source in sorted((_FIXTURE / "bundles").glob("*.json"))[:count]:
        shutil.copy(source, data_dir / "derived" / source.name)
    return data_dir


def _pop_set(snapshot_id: str, param_values: dict[str, float]) -> PopulationSet:
    """A structurally valid :class:`PopulationSet` with one population."""
    return PopulationSet(
        snapshot_id=snapshot_id,
        benchmark_version=constants.BENCHMARK_VERSION,
        constants_hash=constants.CONSTANTS_HASH,
        populations=(
            Population("pypi", "ttm_p90_hours", len(param_values), False, dict(param_values)),
        ),
    )


def _merkle_root_of(out_dir: Path) -> str:
    return json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))["merkle_root"]


def test_merkle_root_covers_populations(tmp_path, monkeypatch):
    """Two builds over identical bundles but different populations differ (E-01)."""
    data_dir = _make_data_dir(tmp_path)

    def build(popset_a: bool) -> str:
        def fake_build_populations(members, snapshot_id):
            if popset_a:
                return _pop_set(snapshot_id, {"a/b": 1.0})
            return _pop_set(snapshot_id, {"a/b": 1.0, "c/d": 2.0})

        monkeypatch.setattr(
            "porchlamp.snapshot.build_cmd.build_populations", fake_build_populations
        )
        out_dir = tmp_path / ("out_a" if popset_a else "out_b")
        build_snapshot_dir(
            data_dir,
            out_dir,
            snapshot_id="TEST-POP",
            as_of=_AS_OF,
            code_commit_sha=_COMMIT,
        )
        return _merkle_root_of(out_dir)

    root_a = build(True)
    root_b = build(False)
    assert root_a != root_b, (
        "merkle_root ignored populations.json: identical bundles with different "
        f"populations produced the same root {root_a!r}"
    )


def test_merkle_root_is_deterministic(tmp_path):
    """The same input built twice yields the same Merkle root."""
    data_dir = _make_data_dir(tmp_path)
    roots = set()
    for name in ("run_1", "run_2"):
        out_dir = tmp_path / name
        build_snapshot_dir(
            data_dir,
            out_dir,
            snapshot_id="TEST-DET",
            as_of=_AS_OF,
            code_commit_sha=_COMMIT,
        )
        roots.add(_merkle_root_of(out_dir))
    assert len(roots) == 1


def test_merkle_root_matches_documented_recipe(tmp_path):
    """manifest.merkle_root == merkle_root(sorted(raw_hashes + populations leaf))."""
    data_dir = _make_data_dir(tmp_path)
    out_dir = tmp_path / "out"
    frozen = build_snapshot_dir(
        data_dir,
        out_dir,
        snapshot_id="TEST-RECIPE",
        as_of=_AS_OF,
        code_commit_sha=_COMMIT,
    )
    populations_leaf = sha256_hex((out_dir / "populations.json").read_text(encoding="utf-8"))
    expected = merkle_root(sorted([*frozen.raw_hashes.values(), populations_leaf]))
    assert _merkle_root_of(out_dir) == expected


def test_manifest_carries_the_review_handoff_so_the_stored_snapshot_scores_it(tmp_path):
    """``snapshot store`` scores the evidence it reads back from ``manifest.json``, so a hand-off
    found while building must be written there (RFC-0007), not only used in-process."""
    data_dir = _make_data_dir(tmp_path)
    first, second = sorted((data_dir / "derived").glob("*.json"))[:2]
    notice = {"author": {"login": "gopherbot", "__typename": "User"}, "authorAssociation": "NONE",
              "bodyText": "This PR has been imported to Gerrit for code review.",
              "createdAt": "2026-09-01T01:00:00Z"}
    prs = [{"number": n, "author": {"login": f"c{n}", "__typename": "User"}, "authorAssociation": "NONE",
            "createdAt": "2026-09-01T00:00:00Z", "comments": {"nodes": [notice]}} for n in range(1, 6)]
    envelope = {"data": {"prs": prs, "derived": [
        {"number": n, "is_external": True, "is_bot_author": False} for n in range(1, 6)]}}
    (data_dir / "raw" / "pr_window").mkdir()
    (data_dir / "raw" / "pr_window" / first.name).write_text(json.dumps(envelope), encoding="utf-8")

    out_dir = tmp_path / "out"
    frozen = build_snapshot_dir(
        data_dir, out_dir, snapshot_id="TEST-HANDOFF", as_of=_AS_OF, code_commit_sha=_COMMIT)

    handoff = {key: ev.review_handoff for key, ev in frozen.gate_evidence.items()}
    first_key = first.stem.replace("__", "/", 1)
    assert handoff[first_key] is True
    assert all(value is None for key, value in handoff.items() if key != first_key)
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    by_repo = {f"{r['owner']}/{r['name']}": r["gate_evidence"] for r in manifest["repos"]}
    assert by_repo[first_key]["review_handoff"] is True
    assert second.stem.replace("__", "/", 1) in by_repo


def test_the_stored_snapshot_reads_back_whether_each_pr_window_recorded_the_rule_inputs(tmp_path):
    """``snapshot store`` scores the gate evidence it reads back from ``manifest.json``. A window fetched
    before the query asked for drafts and close actors must reach that scoring as "inputs not recorded",
    so D-GATE-5 cannot fail it; a current window as "recorded"; a repository with no window as no opinion."""
    from porchlamp.snapshot.fixture_io import load_frozen_fixture

    data_dir = _make_data_dir(tmp_path)
    old, current, absent = sorted((data_dir / "derived").glob("*.json"))
    pr = {"number": 1, "author": {"login": "c1", "__typename": "User"}, "authorAssociation": "NONE",
          "createdAt": "2026-09-01T00:00:00Z", "comments": {"nodes": []}}
    windows = {old: [pr], current: [{**pr, "isDraft": False, "timelineItems": {"nodes": []}}]}
    (data_dir / "raw" / "pr_window").mkdir()
    for path, prs in windows.items():
        envelope = {"data": {"prs": prs, "derived": [{"number": 1, "is_external": True, "is_bot_author": False}]}}
        (data_dir / "raw" / "pr_window" / path.name).write_text(json.dumps(envelope), encoding="utf-8")

    out_dir = tmp_path / "out"
    build_snapshot_dir(data_dir, out_dir, snapshot_id="TEST-RULE-INPUTS", as_of=_AS_OF, code_commit_sha=_COMMIT)

    read_back = load_frozen_fixture(out_dir).gate_evidence
    key = lambda path: path.stem.replace("__", "/", 1)  # noqa: E731
    assert read_back[key(old)].pr_rule_inputs_recorded is False
    assert read_back[key(current)].pr_rule_inputs_recorded is True
    assert read_back[key(absent)].pr_rule_inputs_recorded is None


def test_the_stored_snapshot_reads_back_whether_each_pr_window_covers_the_window(tmp_path):
    """``snapshot store`` scores the gate evidence it reads back from ``manifest.json``, so a record cut at
    the collector's PR cap must reach it as "does not cover the window", with its cut-off, or D-GATE-5 would
    judge the newest weeks as the window (RFC-0011); an uncut record as covering; no record as no opinion."""
    from porchlamp.snapshot.fixture_io import load_frozen_fixture

    data_dir = _make_data_dir(tmp_path)
    cut, uncut, absent = sorted((data_dir / "derived").glob("*.json"))
    pr = {"number": 1, "author": {"login": "c1", "__typename": "User"}, "authorAssociation": "NONE",
          "createdAt": "2026-08-20T00:00:00Z", "comments": {"nodes": []}, "isDraft": False,
          "timelineItems": {"nodes": []}}
    (data_dir / "raw" / "pr_window").mkdir()
    for path, truncated in ((cut, True), (uncut, False)):
        envelope = {"fetched_at": _AS_OF.isoformat(),
                    "data": {"window_anchor": _AS_OF.isoformat(), "window_days": 180, "truncated": truncated,
                             "prs": [pr], "derived": [{"number": 1, "is_external": True, "is_bot_author": False}]}}
        (data_dir / "raw" / "pr_window" / path.name).write_text(json.dumps(envelope), encoding="utf-8")

    out_dir = tmp_path / "out"
    build_snapshot_dir(data_dir, out_dir, snapshot_id="TEST-WINDOW-COVERAGE", as_of=_AS_OF, code_commit_sha=_COMMIT)

    read_back = load_frozen_fixture(out_dir).gate_evidence
    key = lambda path: path.stem.replace("__", "/", 1)  # noqa: E731
    covered = {k: (read_back[key(p)].pr_window_complete, read_back[key(p)].pr_window_cutoff)
               for k, p in (("cut", cut), ("uncut", uncut), ("absent", absent))}
    assert covered == {"cut": (False, "2026-08-20T00:00:00Z"), "uncut": (True, None), "absent": (None, None)}


def test_commit_data_fetched_before_as_of_is_judged_at_its_fetch(tmp_path):
    """The commits were fetched 10 days before ``as_of``; nothing after the fetch was seen. A commit 175 days
    before the fetch (185 before ``as_of``) keeps the project out of ``stale``: the window ends where the data
    does. With no commit in the 180 days before the fetch, the project is still stale."""
    from datetime import timedelta

    from porchlamp.snapshot.store_cmd import score_snapshot_dir

    data_dir = _make_data_dir(tmp_path, count=2)
    dead, alive = sorted((data_dir / "derived").glob("*.json"))
    commits = data_dir / "raw" / "commits_180d"
    commits.mkdir()
    fetched = _AS_OF - timedelta(days=10)
    for path, days_before_fetch in ((dead, 185), (alive, 175)):
        when = (fetched - timedelta(days=days_before_fetch)).isoformat().replace("+00:00", "Z")
        envelope = {"fetched_at": fetched.isoformat(), "data": [{"author_login": "maintainer", "date": when}]}
        (commits / path.name).write_text(json.dumps(envelope), encoding="utf-8")

    out_dir = tmp_path / "out"
    frozen = build_snapshot_dir(data_dir, out_dir, snapshot_id="TEST-EARLY-FETCH", as_of=_AS_OF, code_commit_sha=_COMMIT)
    alive_key, dead_key = (p.stem.replace("__", "/", 1) for p in (alive, dead))
    assert frozen.gate_evidence[alive_key].vitality_commits_180d == 1
    assert frozen.gate_evidence[dead_key].vitality_commits_180d == 0

    scored, _ = score_snapshot_dir(out_dir)
    reasons = {f"{r.repo.owner}/{r.repo.name}": r.gate.reason for r in scored.records}
    assert reasons[alive_key] != "stale"
    assert reasons[dead_key] == "stale"


def test_a_project_whose_last_human_commit_is_181_days_before_as_of_fails_the_vitality_gate(tmp_path):
    """D-GATE-4 is judged on the 180 days ending at the snapshot's ``as_of``. The ``commits_180d`` envelope
    was fetched later than ``as_of`` here, so its own window still holds a commit 181 days before
    ``as_of``; that commit must not keep the project out of ``stale``. A commit 179 days before passes."""
    from datetime import timedelta

    from porchlamp.snapshot.store_cmd import score_snapshot_dir

    data_dir = _make_data_dir(tmp_path, count=2)
    old, recent = sorted((data_dir / "derived").glob("*.json"))
    commits = data_dir / "raw" / "commits_180d"
    commits.mkdir()
    for path, days in ((old, 181), (recent, 179)):
        when = (_AS_OF - timedelta(days=days)).isoformat().replace("+00:00", "Z")
        envelope = {"fetched_at": (_AS_OF + timedelta(days=20)).isoformat(),
                    "data": [{"author_login": "maintainer", "date": when}]}
        (commits / path.name).write_text(json.dumps(envelope), encoding="utf-8")

    out_dir = tmp_path / "out"
    frozen = build_snapshot_dir(data_dir, out_dir, snapshot_id="TEST-VITALITY", as_of=_AS_OF, code_commit_sha=_COMMIT)
    old_key, recent_key = (p.stem.replace("__", "/", 1) for p in (old, recent))
    assert frozen.gate_evidence[old_key].vitality_commits_180d == 0
    assert frozen.gate_evidence[recent_key].vitality_commits_180d == 1

    scored, _ = score_snapshot_dir(out_dir)
    reasons = {f"{r.repo.owner}/{r.repo.name}": r.gate.reason for r in scored.records}
    assert reasons[old_key] == "stale"
    assert reasons[recent_key] != "stale"


def _files_of(out_dir: Path) -> dict[str, bytes]:
    return {p.relative_to(out_dir).as_posix(): p.read_bytes() for p in sorted(out_dir.rglob("*")) if p.is_file()}


def _with_review_handoff(data_dir: Path) -> str:
    """Give the first repository a pr_window envelope whose PRs carry a hand-off notice, so its gate
    evidence differs from the others'; returns its key."""
    first = sorted((data_dir / "derived").glob("*.json"))[0]
    notice = {"author": {"login": "gopherbot", "__typename": "User"}, "authorAssociation": "NONE",
              "bodyText": "This PR has been imported to Gerrit for code review.",
              "createdAt": "2026-09-01T01:00:00Z"}
    prs = [{"number": n, "author": {"login": f"c{n}", "__typename": "User"}, "authorAssociation": "NONE",
            "createdAt": "2026-09-01T00:00:00Z", "comments": {"nodes": [notice]}} for n in range(1, 6)]
    envelope = {"data": {"prs": prs, "derived": [
        {"number": n, "is_external": True, "is_bot_author": False} for n in range(1, 6)]}}
    (data_dir / "raw" / "pr_window").mkdir(exist_ok=True)
    (data_dir / "raw" / "pr_window" / first.name).write_text(json.dumps(envelope), encoding="utf-8")
    return first.stem.replace("__", "/", 1)


class _RecordingPool:
    """Stands in for ``ProcessPoolExecutor`` in ``build_cmd`` and records each pool's size."""

    sizes: list[int] = []

    def __init__(self, max_workers: int, **options) -> None:
        from concurrent.futures import ProcessPoolExecutor

        _RecordingPool.sizes.append(max_workers)
        self._pool = ProcessPoolExecutor(max_workers=max_workers, **options)

    def __enter__(self):
        return self._pool.__enter__()

    def __exit__(self, *exc):
        return self._pool.__exit__(*exc)


def test_a_build_on_several_processes_writes_the_same_files_as_a_serial_one(tmp_path):
    """The per-repository loading runs on a process pool; every file it leads to (bundles,
    populations.json, manifest.json) is byte-identical to a one-process build's."""
    data_dir = _make_data_dir(tmp_path, count=12)
    handoff_key = _with_review_handoff(data_dir)
    for name, workers in (("serial", 1), ("parallel", 3)):
        build_snapshot_dir(data_dir, tmp_path / name, snapshot_id="TEST-WORKERS", as_of=_AS_OF,
                           code_commit_sha=_COMMIT, workers=workers)
    serial, parallel = _files_of(tmp_path / "serial"), _files_of(tmp_path / "parallel")
    assert len(serial) == 12 + 2
    assert parallel == serial
    manifest = json.loads(parallel["manifest.json"])
    by_repo = {f"{r['owner']}/{r['name']}": r["gate_evidence"] for r in manifest["repos"]}
    assert by_repo[handoff_key]["review_handoff"] is True  # evidence computed in a worker came back


def test_the_build_loads_on_one_process_per_cpu_unless_told_otherwise(tmp_path, monkeypatch):
    """``None``: one process per CPU. A count: that many. One, the library default: no pool at all (a caller that
    patches this module keeps its patch)."""
    data_dir = _make_data_dir(tmp_path, count=6)
    monkeypatch.setattr("porchlamp.snapshot.build_cmd.ProcessPoolExecutor", _RecordingPool)
    monkeypatch.setattr("porchlamp.snapshot.build_cmd.os.cpu_count", lambda: 4)
    sizes = []
    for name, workers in (("auto", {"workers": None}), ("two", {"workers": 2}), ("one", {"workers": 1}), ("default", {})):
        _RecordingPool.sizes = []
        build_snapshot_dir(data_dir, tmp_path / name, snapshot_id="TEST-POOL", as_of=_AS_OF,
                           code_commit_sha=_COMMIT, **workers)
        sizes.append(_RecordingPool.sizes)
    assert sizes == [[4], [2], [], []]


def test_the_build_command_runs_on_every_cpu_unless_given_a_worker_count(tmp_path, monkeypatch):
    from porchlamp.snapshot.cli import main

    data_dir = _make_data_dir(tmp_path, count=4)
    monkeypatch.setattr("porchlamp.snapshot.build_cmd.ProcessPoolExecutor", _RecordingPool)
    monkeypatch.setattr("porchlamp.snapshot.build_cmd.os.cpu_count", lambda: 3)
    argv = ["build", "--data-dir", str(data_dir), "--snapshot-id", "TEST-CLI",
            "--as-of", "2026-09-12T00:00:00+00:00", "--code-commit", _COMMIT]
    _RecordingPool.sizes = []
    assert main([*argv, "--out-dir", str(tmp_path / "auto")]) == 0
    assert _RecordingPool.sizes == [3]
    _RecordingPool.sizes = []
    assert main([*argv, "--out-dir", str(tmp_path / "out"), "--workers", "2"]) == 0
    assert _RecordingPool.sizes == [2]
    with pytest.raises(SystemExit) as refused:
        main([*argv, "--out-dir", str(tmp_path / "zero"), "--workers", "0"])
    assert refused.value.code == 2
