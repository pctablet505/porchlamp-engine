"""In-process layer runner for the phone app: a port of refresh.sh + gather_layer.sh.

Each layer calls scripts/gather.py main() in this process and reruns until two consecutive
runs report the same failure count, then writes a marker. There is no rate-limit sampling,
no kill, no threads and no shards: the GitHub rate-limit wait lives in PHONE-2's transport.
Standard library only.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import os
import re
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Callable

MAIN_LAYERS: list[tuple[str, str]] = [
    ("L1", "pr_window"),
    ("L0", "github_meta,pr_events,pr_reviews,contributors,actions_runs,tree,commits_180d"),
    ("L2", "repo_object,gfi,dependents,releases,runs"),
    ("L3g", "governance"),
    ("L3c", "commits_365d,issues_counts"),
    ("L4", "author_history"),
]
KEYLESS_LAYER = ("LK", "depsdev,scorecard,downloads")

PAGE = 25
PAGE_RETRY = 10
_HEAVY = ("504", "502", "end of JSON", "timed out", "timeout", "Timeout")
_DONE = re.compile(r"done: (\d+) clean, (\d+) with failures")
_GATHER_CACHE: dict[str, ModuleType] = {}

GatherMain = Callable[[list[str]], int]


def _utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_marker(path: Path, now: Callable[[], float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_utc(now()) + "\n", encoding="utf-8")


def shutil_free(home: Path | str) -> float:
    """Free GB on the filesystem holding home (or its nearest existing parent)."""
    p = Path(home)
    while not p.exists():
        p = p.parent
    return shutil.disk_usage(p).free / 2**30


def load_gather(engine: Path, home: Path) -> ModuleType:
    """Import engine/scripts/gather.py by path, once per process.

    gather.py reads PORCHLAMP_DATA_ROOT at import time, so the data root is fixed for the
    process: a later call with another home raises instead of returning a stale DATA.
    """
    os.environ["PORCHLAMP_DATA_ROOT"] = str(home)
    path = (Path(engine) / "scripts" / "gather.py").resolve()
    key = str(path)
    if key in _GATHER_CACHE:
        mod = _GATHER_CACHE[key]
        if mod.DATA != Path(home) / "raw":
            raise RuntimeError(f"gather.py already loaded with data root {mod.DATA.parent}")
        return mod
    if str(engine) not in sys.path:
        sys.path.insert(0, str(engine))
    spec = importlib.util.spec_from_file_location("phone_gather", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phone_gather"] = mod
    spec.loader.exec_module(mod)
    _GATHER_CACHE[key] = mod
    return mod


def apply_pagesize() -> None:
    """Default collect_pr_window to page_size 25; retry once at 10 on query-weight errors.

    Idempotent: the wrapper is marked, so a second call does nothing.
    """
    g = importlib.import_module("porchlamp.collect.github_graphql")
    current = g.collect_pr_window
    if getattr(current, "_phone_pagesize", False):
        return
    orig = current

    def collect_pr_window(*args, **kwargs):
        kwargs.setdefault("page_size", PAGE)
        try:
            return orig(*args, **kwargs)
        except Exception as e:  # gather.py logs and counts any failure
            if kwargs["page_size"] > PAGE_RETRY and any(m in str(e) for m in _HEAVY):
                kwargs["page_size"] = PAGE_RETRY
                return orig(*args, **kwargs)
            raise

    collect_pr_window._phone_pagesize = True  # type: ignore[attr-defined]
    g.collect_pr_window = collect_pr_window


class _Tee:
    """Captures stdout for one gather run and logs each complete line as it arrives."""

    def __init__(self, log: Callable[[str], object]) -> None:
        self._log = log
        self._out = sys.stdout  # the real stdout: log=print must not write back into this tee
        self._parts: list[str] = []
        self._partial = ""

    def _emit(self, line: str) -> None:
        with contextlib.redirect_stdout(self._out):
            self._log(line)

    def write(self, s: str) -> int:
        self._parts.append(s)
        *lines, self._partial = (self._partial + s).split("\n")
        for line in lines:
            self._emit(line)
        return len(s)

    def flush(self) -> None:
        """No-op: print(flush=True) must not split a line in the log."""

    def finish(self) -> None:
        if self._partial:
            self._emit(self._partial)
            self._partial = ""

    def text(self) -> str:
        return "".join(self._parts)


def _stage(
    home: Path,
    layer: str,
    collectors: list[str],
    now: Callable[[], float],
    log: Callable[[str], object],
) -> int:
    """Move each collector's old envelope folder to raw-stale-<date>/ (rename, never delete)."""
    raw = home / "raw"
    stale = home / f"raw-stale-{datetime.fromtimestamp(now(), timezone.utc):%Y-%m-%d}"
    moves = [(raw / c, stale / c) for c in collectors if (raw / c).exists()]
    if any(dst.exists() for _, dst in moves):
        log(f"{layer}: BLOCKED staging: a target under {stale} already exists")
        return 5
    for src, dst in moves:
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        log(f"{layer}: staged old {src.name} -> {dst}")
    _write_marker(home / "state" / "refresh" / f"{layer}.staged", now)
    for c in collectors:
        (raw / c).mkdir(parents=True, exist_ok=True)
    return 0


def run_layer(
    home,
    layer: str,
    collectors: str,
    gather_main: GatherMain,
    *,
    max_runs: int = 60,
    min_free_gb: float = 2.0,
    free_gb: Callable[[Path], float] = shutil_free,
    log: Callable[[str], object] = print,
    now: Callable[[], float] = time.time,
) -> int:
    """Run one layer until two consecutive runs agree on the failure count.

    Returns 0 complete; 2 not confirmed after max_runs; 3 the gatherer raised (SystemExit
    included); 4 a run printed no done line; 5 staging refused; 6 free disk under min_free_gb.
    """
    home = Path(home)
    state = home / "state" / "refresh"
    if (state / f"{layer}.complete").exists():
        log(f"{layer}: already complete -- skipping")
        return 0
    if not (state / f"{layer}.staged").exists():
        rc = _stage(home, layer, [c for c in collectors.split(",") if c], now, log)
        if rc:
            return rc
    prev: int | None = None
    for run in range(1, max_runs + 1):
        free = free_gb(home)
        if free < min_free_gb:
            log(f"{layer}: BLOCKED free disk {free:.1f} GB under {min_free_gb} GB")
            return 6
        log(f"{layer}: === gather run {run} of {max_runs} (collectors: {collectors}) ===")
        tee = _Tee(log)
        try:
            with contextlib.redirect_stdout(tee):  # type: ignore[arg-type]
                gather_main(["--all", "--graphql-bulk", "--collectors", collectors])
        except (Exception, SystemExit):
            tee.finish()
            log(f"{layer}: gatherer raised in run {run}")
            log(traceback.format_exc())
            return 3
        tee.finish()
        found = _DONE.findall(tee.text())  # this run's output only: the tee is fresh per run
        if not found:
            log(f"{layer}: run {run} printed no done line")
            return 4
        fail = int(found[-1][1])
        log(f"{layer}: run {run} summary: {found[-1][0]} clean, {fail} with failures")
        if prev is not None and fail == prev:
            _write_marker(state / f"{layer}.complete", now)
            log(f"{layer}: COMPLETE, two runs agree on {fail} failures")
            return 0
        prev = fail
    log(f"{layer}: NOT confirmed after {max_runs} runs")
    return 2


def run_cycle(
    home,
    gather_main: GatherMain,
    *,
    max_runs: int = 60,
    min_free_gb: float = 2.0,
    free_gb: Callable[[Path], float] = shutil_free,
    log: Callable[[str], object] = print,
    now: Callable[[], float] = time.time,
) -> int:
    """MAIN_LAYERS in order, then KEYLESS_LAYER. 0 only if every layer completes."""
    home = Path(home)
    kw = {
        "max_runs": max_runs,
        "min_free_gb": min_free_gb,
        "free_gb": free_gb,
        "log": log,
        "now": now,
    }
    apply_pagesize()
    for layer, cols in MAIN_LAYERS:
        rc = run_layer(home, layer, cols, gather_main, **kw)
        if rc:
            log(f"cycle stopped at {layer} with rc {rc}")
            return rc
    main_done = home / "state" / "refresh" / "MAIN.done"
    if not main_done.is_file():  # a keyless resume must not re-stamp the MAIN time
        _write_marker(main_done, now)
    layer, cols = KEYLESS_LAYER
    return run_layer(home, layer, cols, gather_main, **kw)


def _cycle_open(home: Path) -> Path:
    """Marker of a cycle in progress: written when a cycle starts, removed when it returns 0."""
    return home / "state" / "refresh" / "cycle.open"


def _main_fresh(home: Path, every_days: float, now_ts: float) -> bool:
    p = home / "state" / "refresh" / "MAIN.done"
    if not p.is_file():
        return False
    try:
        ts = datetime.strptime(p.read_text(encoding="utf-8").strip(), "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return now_ts - ts.replace(tzinfo=timezone.utc).timestamp() <= every_days * 86400


def _retire_markers(home: Path, now: Callable[[], float], log: Callable[[str], object]) -> None:
    """Rename (never delete) the markers of the previous cycle into state/refresh-prev/."""
    state = home / "state" / "refresh"
    prev = home / "state" / "refresh-prev"
    stamp = datetime.fromtimestamp(now(), timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for p in sorted(state.glob("*")):
        if not p.is_file() or not (p.suffix in (".complete", ".staged") or p.name == "MAIN.done"):
            continue
        prev.mkdir(parents=True, exist_ok=True)
        target = prev / p.name
        if target.exists():
            target = prev / f"{p.name}.{stamp}"
        p.rename(target)
        log(f"retired marker {p.name} -> {target}")


def run_forever(
    home,
    gather_main: GatherMain,
    *,
    every_days: float = 28,
    poll_s: float = 3600,
    sleep: Callable[[float], object] = time.sleep,
    now: Callable[[], float] = time.time,
    log: Callable[[str], object] = print,
) -> None:
    """Each pass: resume a cycle in progress (cycle.open), else start a new one when MAIN.done
    is missing or old, else sleep.

    A cycle in progress is resumed without retiring any marker and without restaging: its
    staged envelopes stay. cycle.open is removed only when run_cycle returns 0 (MAIN and keyless
    layers all complete), so a restart or a failed keyless layer resumes the same way.
    """
    home = Path(home)
    while True:
        if _cycle_open(home).is_file():
            log("run_forever: cycle.open present -- resuming the cycle in progress")
        elif not _main_fresh(home, every_days, now()):
            log("run_forever: MAIN.done missing or old -- starting a new cycle")
            _retire_markers(home, now, log)
            _write_marker(_cycle_open(home), now)
        else:
            sleep(poll_s)
            continue
        rc = run_cycle(home, gather_main, log=log, now=now)
        if rc == 0:
            _cycle_open(home).unlink(missing_ok=True)
        else:
            log(f"run_forever: cycle stopped with rc {rc}; resuming after {poll_s} s")
            sleep(poll_s)
