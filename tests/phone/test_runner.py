"""phone/termux runner: two-run completion, failure-count movement, resume, keyless layers, L1 entry choice, and the
diff against the workstation originals (only allowed hunks may differ)."""

from __future__ import annotations

import difflib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

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
