"""phone/termux runner: two-run completion, failure-count movement, resume, keyless layers, L1 entry choice, and the
diff against the workstation originals (only allowed hunks may differ)."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from phone import archive, runner
from porchlamp.collect import github_graphql, governance
from porchlamp.collect.envelope import write_envelope
from tests.phone import make_home

REPO = Path(__file__).resolve().parents[2]
TERMUX = REPO / "phone" / "termux"
ORIGINAL_DIR = Path(os.environ.get("PORCHLAMP_ORIGINAL_RUNNER", REPO / "tests" / "fixtures" / "original_runner"))

STUB = '''\
import os, pathlib, sys
args = sys.argv[1:]
cols = args[args.index("--collectors") + 1].split(",")
root = pathlib.Path(os.environ["PORCHLAMP_DATA_ROOT"])
state = pathlib.Path(os.environ["STUB_STATE"])
state.mkdir(parents=True, exist_ok=True)
run = len(list(state.glob("run-*")))
(state / f"run-{run}").write_text("x")
fails = [int(v) for v in os.environ.get("STUB_FAILS", "0").split(",")]
for c in cols:
    folder = root / "raw" / c
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{c}-{run}.json").write_text("{}")
print(f"done: 5 clean, {fails[min(run, len(fails) - 1)]} with failures", flush=True)
'''


def _env(home: Path, state: Path, stub: Path | None, **extra: str) -> dict:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "PORCHLAMP_PHONE_HOME": str(home),
        "PORCHLAMP_ENGINE": str(REPO),
        "PY": sys.executable,
        "KEYLESS": "1",
        "STUB_STATE": str(state),
    }
    if stub is not None:
        env["GATHER_ENTRY"] = str(stub)
    env.update(extra)
    return env


def _run_layer(home: Path, layer: str, cols: str, env: dict, maxruns: int = 5) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(TERMUX / "gather_layer_phone.sh"), layer, cols, str(maxruns)],
                          env=env, capture_output=True, text=True, timeout=300)


@pytest.fixture
def stub(tmp_path):
    path = tmp_path / "stub_gather.py"
    path.write_text(STUB, encoding="utf-8")
    return path


def test_runner_two_agreeing_runs_complete_a_layer(tmp_path, stub):
    home = tmp_path / "phone"
    home.mkdir()
    state = tmp_path / "stubstate"
    proc = _run_layer(home, "L1", "pr_window", _env(home, state, stub, STUB_FAILS="3,3"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "COMPLETE" in proc.stdout
    assert len(list(state.glob("run-*"))) == 2
    marker = home / "state" / "refresh" / "L1.complete"
    assert marker.is_file() and marker.read_text().strip()
    assert (home / "raw" / "pr_window").is_dir()


def test_runner_failure_count_moving_does_not_complete(tmp_path, stub):
    home = tmp_path / "phone"
    home.mkdir()
    state = tmp_path / "stubstate"
    proc = _run_layer(home, "L1", "pr_window", _env(home, state, stub, STUB_FAILS="3,2,1"), maxruns=3)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "NOT confirmed complete" in proc.stdout
    assert not (home / "state" / "refresh" / "L1.complete").exists()


def test_runner_resumes_after_complete_marker(tmp_path, stub):
    home = tmp_path / "phone"
    make_home(home, layers=(), files_per=0, token=None)
    (home / "state" / "refresh").mkdir(parents=True, exist_ok=True)
    (home / "state" / "refresh" / "L0.complete").write_text("2026-10-10T12:00:00Z\n", encoding="utf-8")
    state = tmp_path / "stubstate"
    proc = _run_layer(home, "L0", "tree", _env(home, state, stub, STUB_FAILS="0,0"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "already complete" in proc.stdout
    assert not state.exists() or not list(state.glob("run-*"))


def test_runner_keyless_layer_needs_no_token(tmp_path, stub):
    home = tmp_path / "phone"
    home.mkdir()
    assert not (home / "config" / "github_token").exists()
    state = tmp_path / "stubstate"
    proc = _run_layer(home, "LK", "depsdev,scorecard,downloads", _env(home, state, stub, STUB_FAILS="0,0"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (home / "state" / "refresh" / "LK.complete").is_file()
    assert "budget" not in proc.stdout  # KEYLESS=1 skips the GitHub budget probe entirely


def test_runner_l1_uses_pagesize_entry_others_gather(tmp_path):
    home = tmp_path / "phone"
    home.mkdir()
    state = tmp_path / "stubstate"
    env = _env(home, state, None)
    env.pop("GATHER_ENTRY", None)
    env["PHONE_DRY_RUN"] = "1"
    l1 = subprocess.run(["bash", str(TERMUX / "gather_layer_phone.sh"), "L1", "pr_window", "5"],
                        env=env, capture_output=True, text=True, timeout=120)
    assert l1.returncode == 0, l1.stderr
    assert l1.stdout.strip() == f"GATHER_ENTRY={REPO}/phone/gather_pagesize.py"
    assert (REPO / "phone" / "gather_pagesize.py").is_file()
    for layer, cols in (("L0", "tree"), ("L2", "repo_object"), ("L3g", "governance"), ("LK", "depsdev")):
        proc = subprocess.run(["bash", str(TERMUX / "gather_layer_phone.sh"), layer, cols, "5"],
                              env=env, capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "GATHER_ENTRY=scripts/gather.py", (layer, proc.stdout)


# Changed lines allowed by the PHONE-1 brief (A4). Each removed or added line must match one of these.
ALLOWED = [
    r"^#",                                  # comments (phone header, unchanged originals are not in the diff)
    r"^set -m$",
    r"^(WT|DATAROOT|RUN|LOGDIR|PY)=",     # paths and PY (PY=${PY:-python})
    r"^export PYTHONPATH=",                  # TMPDIR moves off /tmp
    r"^GATHER_ENTRY=|^if \[ -z \"\$\{GATHER_ENTRY",  # GATHER_ENTRY override + L1 entry
    r"^\[ \"\$\{PHONE_DRY_RUN",              # dry-run flag
    r"^disk_ok\(\)",                         # disk floor 5 GB
    r"local free_kb",
    r"\[ \"\$\{free_kb:-0\}\" -ge",
    r"BLOCKED: free disk under",              # message text for the 5 GB floor
    r"PORCHLAMP_DATA_ROOT=\"\$GROOT\" (setsid|nohup) ",  # setsid -> nohup
    r"^PHONE_HOME=|^HERE=|^mkdir -p \"\$RUN/logs\"$",   # refresh.sh paths
    r"^\[ -s \"\$PHONE_HOME/config/github_token\" \]",  # GH_TOKEN from config/github_token
    r"say \"start\"",                          # no /proc/self/cgroup
    r"n=\$\{MAIN_SHARDS:-1\}",                 # NSHARDS default 1
    r"NSHARDS=\$n bash \"\$HERE/gather_layer_phone.sh\"",
    r"MAIN\.done",                              # MAIN.done path
    r"KEYLESS_PARALLEL",                        # LK chain only when KEYLESS_PARALLEL=1
    r"KEYLESS=1 bash \"\$HERE/gather_layer_phone.sh\" LK",
    r"^\s*$",                                   # blank lines
    # removed originals of the same allowed hunks (the test matches both sides of each changed line)
    r"^mkdir -p \"\$RUN/logs\" /tmp/porchlamp-refresh$",
    r"n=\$\{MAIN_SHARDS:-2\}",
    r"NSHARDS=\$n bash \"\$RUN/gather_layer\.sh\"",
    r"say \"start \(scope ",
    r"if \[ \"\$\{ONLY:-\}\" != MAIN \]; then$",
    r"KEYLESS=1 bash \"\$RUN/gather_layer\.sh\" LK",
]


def _changed_lines(a: str, b: str) -> list[str]:
    out = []
    for line in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0):
        if line.startswith(("---", "+++", "@@")):
            continue
        if line[:1] in "+-":
            out.append(line)
    return out


def test_runner_diff_against_original_only_in_allowed_hunks():
    pairs = [
        (ORIGINAL_DIR / "gather_layer.sh", TERMUX / "gather_layer_phone.sh"),
        (ORIGINAL_DIR / "refresh.sh", TERMUX / "refresh_phone.sh"),
    ]
    checked = 0
    rejected = []
    for orig, phone in pairs:
        assert orig.is_file(), f"original runner missing: {orig}"
        assert phone.is_file(), f"phone runner missing: {phone}"
        changed = _changed_lines(orig.read_text(encoding="utf-8"), phone.read_text(encoding="utf-8"))
        assert changed, f"no changed lines between {orig.name} and {phone.name}: the copy was not edited"
        for line in changed:
            text = line[1:]
            checked += 1
            if not any(re.search(pat, text) for pat in ALLOWED):
                rejected.append(f"{phone.name}: {line}")
    assert checked > 0
    assert rejected == [], "\n".join(rejected)


# ---- the engine runner's per-step argv: the black-hole stop goes to the deep steps only (A-2) ------------------------
BASE = ["--all", "--graphql-bulk"]
SKIP = "--skip-ineligible"
SCREEN = "--screen-ineligible"
LAYER_ARGV = {                       # all seven pinned: SKIP on L2, L3g, L3c, L4 and LK, SCREEN on L1, nothing on L0
    "L1": [*BASE, "--collectors", "pr_window", SCREEN],
    "L0": [*BASE, "--collectors", "github_meta,pr_events,pr_reviews,contributors,actions_runs,tree,commits_180d"],
    "L2": [*BASE, "--collectors", "repo_object,gfi,dependents,releases,runs", SKIP],
    "L3g": [*BASE, "--collectors", "governance", SKIP],
    "L3c": [*BASE, "--collectors", "commits_365d,issues_counts", SKIP],
    "L4": [*BASE, "--collectors", "author_history", SKIP],
    "LK": [*BASE, "--collectors", "depsdev,scorecard,downloads", SKIP],
}


def _argv_per_layer(tmp_path: Path) -> dict[str, list[list[str]]]:
    """Every argv ``run_layer`` hands the gatherer, for each of the seven steps (a stub gatherer that always agrees)."""
    seen: dict[str, list[list[str]]] = {}
    for layer, cols in [*runner.MAIN_LAYERS, runner.KEYLESS_LAYER]:
        calls = seen.setdefault(layer, [])

        def gather_main(argv: list[str], calls: list[list[str]] = calls) -> int:
            calls.append(list(argv))
            print("done: 1 clean, 0 with failures")
            return 0

        assert runner.run_layer(tmp_path / "home", layer, cols, gather_main, free_gb=lambda _h: 100.0,
                                log=lambda _line: None) == 0
    return seen


def test_layer_argv_exact(tmp_path):
    seen = _argv_per_layer(tmp_path)
    assert {layer: runs[0] for layer, runs in seen.items()} == LAYER_ARGV
    assert all(argv == runs[0] for runs in seen.values() for argv in runs)       # every pass of a step alike


def test_no_dirty_flag_any_layer(tmp_path):
    seen = _argv_per_layer(tmp_path)
    assert len(seen) == 7 and all(runs for runs in seen.values())
    assert [layer for layer, runs in seen.items() if any("--dirty" in argv for argv in runs)] == []


def _write_black_hole(raw: Path) -> None:
    """o/bh's pr_window envelope confirms a black hole: 12 outside PRs, 30 days old, no maintainer answer (as in
    probes/p4_blackhole_progress.py)."""
    now = datetime.now(timezone.utc)
    created = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    prs = [{"number": i, "createdAt": created, "closedAt": None, "mergedAt": None, "state": "OPEN", "isDraft": False,
            "mergedBy": None, "author": {"__typename": "User", "login": f"ext{i}", "createdAt": "2015-01-01T00:00:00Z"},
            "authorAssociation": "NONE", "reviews": {"nodes": []}, "comments": {"nodes": []},
            "timelineItems": {"nodes": []}} for i in range(1, 13)]
    write_envelope(raw / "pr_window" / "o__bh.json", "github-graphql:pr_window",
                   {"window_anchor": now.isoformat(), "window_days": 180, "truncated": False, "prs": prs,
                    "derived": []}, now.isoformat())


@pytest.mark.usefixtures("clean_env")
def test_black_hole_stops_deep_step_not_pr_window(tmp_path, monkeypatch):
    home = tmp_path / "phone"
    (home / "raw").mkdir(parents=True)
    (home / "raw" / "candidates.jsonl").write_text(
        "".join(json.dumps({"owner": "o", "name": n, "ecosystem": "pypi"}) + "\n" for n in ("bh", "ok")),
        encoding="utf-8")
    _write_black_hole(home / "raw")                                              # last cycle's envelope, staged away by L1
    gather = runner.load_gather(REPO, home)                                      # the real scripts/gather.py
    for name in ("SLEEP_REST", "SLEEP_KEYLESS", "SLEEP_PYPISTATS"):
        monkeypatch.setattr(gather, name, 0.0)
    window: list[str] = []
    deep: list[str] = []

    def collect_pr_window(raw_dir, owner, name, **kwargs):
        window.append(f"{owner}/{name}")
        if name == "bh":
            _write_black_hole(Path(raw_dir))                                     # the fresh fetch confirms it again
        return "ok"

    def collect_governance(raw_dir, owner, name, force=False, graphql_transport=None):
        deep.append(f"{owner}/{name}")
        write_envelope(Path(raw_dir) / "governance" / f"{owner}__{name}.json", "stub", {"files": {}}, "2026-10-14T00:00:00Z")
        return "ok"

    monkeypatch.setattr(github_graphql, "collect_pr_window", collect_pr_window)
    monkeypatch.setattr(governance, "collect_governance", collect_governance)
    log: list[str] = []
    kw = {"free_gb": lambda _h: 100.0, "log": log.append}
    assert runner.run_layer(home, "L1", "pr_window", gather.main, **kw) == 0
    assert {"o/bh", "o/ok"} <= set(window)                                       # the pull-request fetch runs for the black hole
    assert runner.run_layer(home, "L3g", "governance", gather.main, **kw) == 0
    assert deep == ["o/ok"]                                                      # the deep collector is not reached for it
    assert any("o/bh" in line and "stopped at black_hole" in line for line in log)


# ---- the engine runner builds a step's archive when the step completes (A-7) --------------------------------------
def _writing_gatherer(home: Path):
    """A stub gatherer: one envelope in each selected collector's folder, then an agreeing done line."""
    def gather_main(argv: list[str]) -> int:
        for collector in argv[argv.index("--collectors") + 1].split(","):
            (home / "raw" / collector).mkdir(parents=True, exist_ok=True)
            (home / "raw" / collector / "o__a.json").write_text("{}", encoding="utf-8")
        print("done: 1 clean, 0 with failures")
        return 0
    return gather_main


def test_step_completion_prebuilds_archive(tmp_path):
    home = tmp_path / "home"
    assert runner.run_layer(home, "L1", "pr_window", _writing_gatherer(home), free_gb=lambda _h: 100.0,
                            log=lambda _line: None) == 0
    out, meta = home / "archives" / "L1.tar.gz", home / "archives" / "L1.meta.json"
    assert out.is_file() and meta.is_file()
    with tarfile.open(out) as tar:
        assert tar.getnames() == ["raw/pr_window/o__a.json"]
    # the manifest the puller asks for now finds the archive current: it is not built again
    t0 = time.time()
    os.utime(home / "state" / "refresh" / "L1.complete", (t0 - 100, t0 - 100))
    os.utime(out, (t0 - 50, t0 - 50))
    stamp = out.stat().st_mtime_ns
    manifest = archive.write_manifest(home)
    assert out.stat().st_mtime_ns == stamp
    assert manifest["layers"]["L1"]["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write into a read-only folder")
def test_archive_build_failure_does_not_stop_cycle(tmp_path):
    home = tmp_path / "home"
    (home / "archives").mkdir(parents=True)
    (home / "archives").chmod(0o555)
    log: list[str] = []
    try:
        for layer, cols in (("L1", "pr_window"), ("L0", "github_meta,tree")):
            assert runner.run_layer(home, layer, cols, _writing_gatherer(home), free_gb=lambda _h: 100.0,
                                    log=log.append) == 0
    finally:
        (home / "archives").chmod(0o755)
    assert (home / "state" / "refresh" / "L1.complete").is_file() and (home / "state" / "refresh" / "L0.complete").is_file()
    assert [line.split(":")[0] for line in log if "the pull builds it" in line] == ["archive for L1 not built",
                                                                                    "archive for L0 not built"]
    assert not list((home / "archives").glob("*.tar.gz"))
    assert sorted(archive.write_manifest(home)["layers"]) == ["L0", "L1"]            # and the pull does build them


def test_archive_build_bug_is_not_swallowed(tmp_path, monkeypatch):
    def build_archive(_home, _layer):
        raise ValueError("a bug, not a full disk")

    monkeypatch.setattr(archive, "build_archive", build_archive)
    home = tmp_path / "home"
    with pytest.raises(ValueError):
        runner.run_layer(home, "L1", "pr_window", _writing_gatherer(home), free_gb=lambda _h: 100.0,
                         log=lambda _line: None)
