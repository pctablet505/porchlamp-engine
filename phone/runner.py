"""In-process layer runner for the phone app: a port of refresh.sh + gather_layer.sh.

Each layer calls scripts/gather.py main() in this process and reruns until a run has no failure or two
consecutive runs report the same failure count, then writes a marker. There is no rate-limit sampling,
no kill, no threads and no shards: the GitHub rate-limit wait lives in PHONE-2's transport,
and pause and stop live in the HTTP gate (``phone.httpgate``).

``run_forever`` also tells the app what it is doing: a ``RunState`` (the current step, the pass number,
the pass's per-repo counts) for the status screen, a ``notify(name, **info)`` callback for the timeline, the
owner's layer selection and interval read afresh on every pass, and an "Idle, next cycle due DATE" line
instead of a silent sleep. Standard library only.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import os
import re
import shutil
import sys
import threading
import time
import traceback
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Callable

from phone import archive

#: A layer's name in plain words (the app's vocabulary is "step", never "layer").
STEP_NAMES: dict[str, str] = {
    "L1": "Pull requests",
    "L0": "Project details",
    "L2": "Dependents and releases",
    "L3g": "Governance files",
    "L3c": "Commits and issues",
    "L4": "Contributor history",
    "LK": "Package data",
}

MAIN_LAYERS: list[tuple[str, str]] = [
    ("L1", "pr_window"),
    ("L0", "github_meta,pr_events,pr_reviews,contributors,actions_runs,tree,commits_180d"),
    ("L2", "repo_object,gfi,dependents,releases,runs"),
    ("L3g", "governance"),
    ("L3c", "commits_365d,issues_counts"),
    ("L4", "author_history"),
]
KEYLESS_LAYER = ("LK", "depsdev,scorecard,downloads")

#: Extra gather.py flags per step. ``--skip-ineligible`` prunes ineligible repos and stops a confirmed black hole after
#: its pull-request fetch; it reads github_meta and tree (L0), pr_window (L1) and repo_object (L2) from disk, so it goes
#: only on the steps after those were refreshed in the same cycle. The pull-request layer comes first, before any of
#: those is fresh: it gets ``--screen-ineligible`` instead, which asks GitHub (25 repos a point) whether a repo is
#: archived, disabled or idle and prunes on that and on the slow facts of the cycle before, never on last cycle's commits
#: or pull requests. L0 is left alone: it is how a repo that woke up is found. Never ``--dirty``: under ``--collectors`` it forces
#: every repo on every run.
LAYER_FLAGS: dict[str, tuple[str, ...]] = {
    "L1": ("--screen-ineligible",),
    "L2": ("--skip-ineligible",),
    "L3g": ("--skip-ineligible",),
    "L3c": ("--skip-ineligible",),
    "L4": ("--skip-ineligible",),
    "LK": ("--skip-ineligible",),
}

#: Written when the repair after step 1 is done in this cycle (:func:`run_repair`).
REPAIR_MARKER = "L1.repaired"

#: D4 (rollout 2026-11-01): the setting that says how many projects a step fetches side by side (gather.py
#: ``--workers``). Only these GitHub steps may: L1 stays at one (its heavy pages drew 15 of the phone's 16 limits one
#: at a time), L2 until deps.dev's and ecosyste.ms's published limits are read, LK for good (pypistats allows about 5
#: calls a minute per address). The repair after L1 always runs at one.
#: ``porchlamp.collect.ghcli.INFLIGHT_ENV``: the requests in flight the GitHub transport allows; set per step.
INFLIGHT_ENV = "PORCHLAMP_GH_INFLIGHT"
#: ``porchlamp.collect.github_graphql.L4_REUSE_L1_ENV``: "0" when the ``l4_reuse_l1`` setting is off.
L4_REUSE_ENV = "PORCHLAMP_L4_REUSE_L1"
WORKER_SETTING: dict[str, str] = {"L4": "l4_workers", "L0": "github_workers", "L3g": "github_workers",
                                  "L3c": "github_workers"}

PAGE = 25
_DONE = re.compile(r"done: (\d+) clean, (\d+) with failures")
#: The one line gather.main prints per repo (the prefix is ``phone.entry.REPO_LINE``) and the run's size line.
_OUTCOME = re.compile(r"^[\w.-]+/[\w.-]+ \[[^\]]*\]: (?:(?P<stopped>OK \(stopped at black_hole\))|(?P<ok>OK)"
                      r"|(?P<pruned>SKIP \(ineligible: [^)]+\))"
                      r"|(?P<clean>SKIP \((?:clean fingerprint|selected collectors clean|no cut record)\))|(?P<failed>FAIL .*))$")
#: The repo line of a record ``--repair-replies`` rewrote.
_REPAIRED = re.compile(r"(?m)^[\w.-]+/[\w.-]+ \[[^\]]*\]: OK$")
_REPOS = re.compile(r"^collectors: .*; repos: (\d+)$")
_GATHER_CACHE: dict[str, ModuleType] = {}

GatherMain = Callable[[list[str]], int]
Notify = Callable[..., object]


class RunState:
    """What the runner is doing now. The runner thread writes it, the status screen reads it."""

    def __init__(self) -> None:
        self.mode = "starting"           # starting | running | idle
        self.layer: str | None = None    # the step being worked on
        self.collectors: tuple[str, ...] = ()
        self.run = 0                     # gather run number inside the step (2 or more = confirming pass)
        self.max_runs = 0
        self.checked = 0                 # repo lines of this gather run (reset at each run): looked at in this pass
        self.stopped = 0                 # ... of which stopped at the black-hole check
        self.pruned = 0                  # ... of which ineligible
        self.failed = 0                  # ... of which failed (retried in the next pass)
        self.total = 0                   # the run's repo count from gather's "repos: N" line; 0 = not yet known
        self.cycle_started: float | None = None
        self.idle_next_due: float | None = None
        self.failures = 0                # cycle passes in a row that ended with a non-zero rc
        self.last_rc: int | None = None
        self.last_failure: str | None = None
        self.finished_at: float | None = None   # when the last cycle of this run completed


def count_line(state: RunState, line: str) -> None:
    """Count one gather output line: ``repos: N`` sets the total; a repo line is checked, and maybe stopped, pruned or failed."""
    m = _REPOS.match(line)
    if m:
        state.total = int(m.group(1))
        return
    m = _OUTCOME.match(line)
    if m:
        state.checked += 1
        if m.lastgroup in ("stopped", "pruned", "failed"):
            setattr(state, m.lastgroup, getattr(state, m.lastgroup) + 1)


def _note(notify: Notify | None, name: str, **info: object) -> None:
    if notify is not None:
        notify(name, **info)


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


def same_path(a: Path | str, b: Path | str) -> bool:
    """True when both names lead to the same directory.

    On Android the files directory has two names (``/data/user/0/<pkg>/files`` and the symlink
    ``/data/data/<pkg>/files``), so paths are never compared as text: the file system decides, and a path that
    does not exist yet is compared after resolving its links.
    """
    try:
        return os.path.samefile(a, b)
    except OSError:
        return Path(a).resolve() == Path(b).resolve()


def load_gather(engine: Path, home: Path) -> ModuleType:
    """Import engine/scripts/gather.py by path, once per process.

    gather.py reads PORCHLAMP_DATA_ROOT at import time (and resolves it), so the data root is fixed for the
    process: a later call with another home raises instead of returning a stale DATA. The same directory under
    another name (a symlink alias) is the same home.
    """
    os.environ["PORCHLAMP_DATA_ROOT"] = str(home)
    path = (Path(engine) / "scripts" / "gather.py").resolve()
    key = str(path)
    if key in _GATHER_CACHE:
        mod = _GATHER_CACHE[key]
        if not same_path(mod.DATA.parent, home):
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
    """Default collect_pr_window to page_size 25.

    A page that fails with a query-weight error is read again in pages of 10 by the collector itself, which then
    goes on at 25 (``github_graphql._PageSizer``), so the project is never fetched again from its first page. A
    project that still fails is counted by gather.py and fetched again by the layer's next run.

    Idempotent: the wrapper is marked, so a second call does nothing.
    """
    g = importlib.import_module("porchlamp.collect.github_graphql")
    current = g.collect_pr_window
    if getattr(current, "_phone_pagesize", False):
        return
    orig = current

    def collect_pr_window(*args, **kwargs):
        kwargs.setdefault("page_size", PAGE)
        return orig(*args, **kwargs)

    collect_pr_window._phone_pagesize = True  # type: ignore[attr-defined]
    g.collect_pr_window = collect_pr_window


class _Tee:
    """Captures stdout for one gather run and logs each complete line as it arrives.

    Several gather worker threads write here at once (D4): writes and the log are serialized by one lock. While a line
    is being logged, what the logging thread itself writes (``log=print``) goes to the real stdout, never back into the
    tee; ``sys.stdout`` is no longer swapped for the whole process meanwhile, which sent another thread's line past
    the tee."""

    def __init__(self, log: Callable[[str], object]) -> None:
        self._log = log
        self._out = sys.stdout  # the real stdout: log=print must not write back into this tee
        self._parts: list[str] = []
        self._partial = ""
        self._lock = threading.Lock()
        self._logging = threading.local()

    def _emit(self, line: str) -> None:
        self._logging.active = True
        try:
            self._log(line)
        finally:
            self._logging.active = False

    def write(self, s: str) -> int:
        if getattr(self._logging, "active", False):
            return self._out.write(s)
        with self._lock:
            self._parts.append(s)
            *lines, self._partial = (self._partial + s).split("\n")
            for line in lines:
                self._emit(line)
        return len(s)

    def flush(self) -> None:
        """No-op: print(flush=True) must not split a line in the log."""

    def finish(self) -> None:
        with self._lock:
            if self._partial:
                self._emit(self._partial)
                self._partial = ""

    def text(self) -> str:
        return "".join(self._parts)


def _lexists(path: Path) -> bool:
    try:
        return path.exists() or path.is_symlink()
    except OSError:
        return True


def _has_entries(path: Path) -> bool:
    if not _lexists(path):
        return False
    if not path.is_dir():
        return True
    try:
        return any(path.iterdir())
    except OSError:
        return True


def _has_retired_stage_marker(home: Path, layer: str) -> bool:
    state_dir = home / "state"
    try:
        if not state_dir.is_dir():
            return False
        return any(p.is_dir() and any(p.glob(f"{layer}.staged*")) for p in state_dir.glob("refresh-prev*"))
    except OSError:
        return False


def _stage(
    home: Path,
    layer: str,
    collectors: list[str],
    now: Callable[[], float],
    log: Callable[[str], object],
) -> int:
    """Move each collector's old envelope folder to raw-stale-<date>/ (rename, never delete)."""
    raw = home / "raw"
    ts = datetime.fromtimestamp(now(), timezone.utc)
    stale = home / f"raw-stale-{ts:%Y-%m-%d}"
    sources = [c for c in collectors if _has_entries(raw / c)]
    if any(_lexists(stale / c) for c in sources):
        if _has_retired_stage_marker(home, layer):
            candidate = home / f"{stale.name}-{ts:%H%M%SZ}"
            seq = 2
            while any(_lexists(candidate / c) for c in sources):
                candidate = home / f"{stale.name}-{ts:%H%M%SZ}-{seq}"
                seq += 1
            stale = candidate
        else:
            log(f"{layer}: BLOCKED staging: a target under {stale} already exists")
            return 5
    for c in sources:
        src, dst = raw / c, stale / c
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        log(f"{layer}: staged old {src.name} -> {dst}")
    for c in collectors:
        (raw / c).mkdir(parents=True, exist_ok=True)
    _write_marker(home / "state" / "refresh" / f"{layer}.staged", now)
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
    state: RunState | None = None,
    notify: Notify | None = None,
    workers: int = 1,
) -> int:
    """Run one layer until a run has no failure, or two consecutive runs agree on the failure count.

    ``workers`` above 1 runs gather with ``--workers`` (projects side by side, D4) when the layer is one of
    :data:`WORKER_SETTING`'s; any other layer runs one project at a time whatever it says.

    A run with no failure ends the layer at once: every project has its record, so a second run would only check
    them all again. A run that checked nothing (``done: 0 clean``) is no proof, so it still needs a second run.

    Returns 0 complete; 2 not confirmed after max_runs; 3 the gatherer raised (SystemExit
    included); 4 a run printed no done line; 5 staging refused; 6 free disk under min_free_gb.
    """
    home = Path(home)
    marker_dir = home / "state" / "refresh"
    if (marker_dir / f"{layer}.complete").exists():
        log(f"{layer}: already complete -- skipping")
        return 0
    if state is not None:
        state.layer, state.collectors, state.run, state.max_runs = layer, tuple(collectors.split(",")), 0, max_runs
        state.checked = state.stopped = state.pruned = state.failed = state.total = 0
    _note(notify, "step_start", layer=layer)
    if not (marker_dir / f"{layer}.staged").exists():
        rc = _stage(home, layer, [c for c in collectors.split(",") if c], now, log)
        if rc:
            return rc

    def counted(line: str) -> None:
        if state is not None:
            count_line(state, line)
        log(line)

    side_by_side = ("--workers", str(workers)) if workers > 1 and layer in WORKER_SETTING else ()
    # RB5 ruling 2: the GitHub transport's in-flight cap is this step's own (ghcli reads it on every request), so a
    # step run one at a time never counts its limits toward the pin.
    os.environ[INFLIGHT_ENV] = str(workers if side_by_side else 1)
    if side_by_side:
        log(f"{layer}: {workers} projects at once")
    prev: int | None = None
    for run in range(1, max_runs + 1):
        if state is not None:
            state.run = run
            state.checked = state.stopped = state.pruned = state.failed = state.total = 0
        free = free_gb(home)
        if free < min_free_gb:
            log(f"{layer}: BLOCKED free disk {free:.1f} GB under {min_free_gb} GB")
            return 6
        log(f"{layer}: === gather run {run} of {max_runs} (collectors: {collectors}) ===")
        tee = _Tee(counted)
        try:
            with contextlib.redirect_stdout(tee):  # type: ignore[arg-type]
                gather_main(["--all", "--graphql-bulk", "--collectors", collectors, *LAYER_FLAGS.get(layer, ()),
                             *side_by_side])
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
        clean, fail = int(found[-1][0]), int(found[-1][1])
        log(f"{layer}: run {run} summary: {clean} clean, {fail} with failures")
        if (fail == 0 and clean > 0) or (prev is not None and fail == prev):
            _write_marker(marker_dir / f"{layer}.complete", now)
            log(f"{layer}: COMPLETE, " + ("a run with no failure" if fail == 0 and clean > 0
                                            else f"two runs agree on {fail} failures"))
            counts = {} if state is None else {"checked": state.checked, "stopped": state.stopped}
            _note(notify, "step_done", layer=layer, **counts)
            try:
                archive.build_archive(home, layer)  # so GET /manifest.json answers at once, not after minutes of gzip
            except OSError as exc:
                log(f"archive for {layer} not built: {exc}; the pull builds it")
            return 0
        prev = fail
    log(f"{layer}: NOT confirmed after {max_runs} runs")
    return 2


def run_repair(
    home,
    gather_main: GatherMain,
    *,
    max_runs: int = 60,
    min_free_gb: float = 2.0,
    free_gb: Callable[[Path], float] = shutil_free,
    log: Callable[[str], object] = print,
    now: Callable[[], float] = time.time,
    state: RunState | None = None,
    notify: Notify | None = None,
) -> int:
    """After step 1: read again the pull requests that the query before RFC-0022 cut short in its records.

    A cycle that was open when the engine changed keeps the records it already wrote (an envelope on disk counts as
    done), so its pull-request step can hold records of the old query: reply lists stopped at 10 reviews and 20
    comments and a timeline at 5 events, with no sign that more existed. ``gather --repair-replies`` reads exactly
    those pull requests again, by number, and stamps the record with the current query. A cycle that fetched the
    whole step with the current query has nothing to repair, and the run only looks. Runs until one run has no
    failure or two agree; ``state/refresh/L1.repaired`` then tells later starts of this cycle to skip it. When a
    record changed, ``L1.complete`` is written again, so the step's archive is built again for the pull.

    Returns 0 done; 2 not confirmed after max_runs; 3 the gatherer raised; 4 a run printed no done line; 6 free disk
    under min_free_gb.
    """
    home = Path(home)
    marker_dir = home / "state" / "refresh"
    if (marker_dir / REPAIR_MARKER).exists():
        log("L1: replies already repaired -- skipping")
        return 0
    layer = "L1"
    os.environ[INFLIGHT_ENV] = "1"   # the repair runs one request at a time (RB5 ruling 2)
    if state is not None:
        state.layer, state.collectors, state.run, state.max_runs = layer, ("pr_window",), 0, max_runs
        state.checked = state.stopped = state.pruned = state.failed = state.total = 0
    _note(notify, "repair_start", layer=layer)

    def counted(line: str) -> None:
        if state is not None:
            count_line(state, line)
        log(line)

    prev: int | None = None
    repaired = 0
    for run in range(1, max_runs + 1):
        if state is not None:
            state.run = run
            state.checked = state.stopped = state.pruned = state.failed = state.total = 0
        free = free_gb(home)
        if free < min_free_gb:
            log(f"{layer}: BLOCKED free disk {free:.1f} GB under {min_free_gb} GB")
            return 6
        log(f"{layer}: === repair run {run} of {max_runs} (pr_window) ===")
        tee = _Tee(counted)
        try:
            with contextlib.redirect_stdout(tee):  # type: ignore[arg-type]
                gather_main(["--all", "--repair-replies"])
        except (Exception, SystemExit):
            tee.finish()
            log(f"{layer}: repair raised in run {run}")
            log(traceback.format_exc())
            return 3
        tee.finish()
        found = _DONE.findall(tee.text())
        if not found:
            log(f"{layer}: repair run {run} printed no done line")
            return 4
        fail = int(found[-1][1])
        repaired += len(_REPAIRED.findall(tee.text()))
        log(f"{layer}: repair run {run} summary: {found[-1][0]} clean, {fail} with failures")
        if fail == 0 or (prev is not None and fail == prev):
            _write_marker(marker_dir / REPAIR_MARKER, now)
            log(f"{layer}: repair COMPLETE, {repaired} records read again, {fail} failures")
            if repaired:
                _write_marker(marker_dir / f"{layer}.complete", now)
                try:
                    archive.build_archive(home, layer)
                except OSError as exc:
                    log(f"archive for {layer} not built: {exc}; the pull builds it")
            _note(notify, "repair_done", layer=layer, repaired=repaired)
            return 0
        prev = fail
    log(f"{layer}: repair NOT confirmed after {max_runs} runs")
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
    layers: Callable[[], Iterable[str]] | None = None,
    state: RunState | None = None,
    notify: Notify | None = None,
    workers: Callable[[str], int] | None = None,
) -> int:
    """MAIN_LAYERS in order, then KEYLESS_LAYER. 0 only if every selected layer completes.

    ``layers`` returns the owner's selection; it is read before each layer, so a change takes effect at the next
    layer boundary. A layer that is not selected is skipped (its marker, if any, is left alone). ``workers(layer)``
    says how many projects the layer fetches side by side (read before each layer; :func:`run_layer` honours it only
    for :data:`WORKER_SETTING`'s layers); None is one at a time.
    """
    home = Path(home)
    kw = {
        "max_runs": max_runs,
        "min_free_gb": min_free_gb,
        "free_gb": free_gb,
        "log": log,
        "now": now,
        "state": state,
        "notify": notify,
    }

    def selected(layer: str) -> bool:
        return layers is None or layer in set(layers())

    apply_pagesize()
    for layer, cols in MAIN_LAYERS:
        if not selected(layer):
            log(f"{layer}: not selected -- skipping")
            continue
        rc = run_layer(home, layer, cols, gather_main, workers=workers(layer) if workers else 1, **kw)
        if rc:
            log(f"cycle stopped at {layer} with rc {rc}")
            return rc
        if layer == "L1":
            rc = run_repair(home, gather_main, **kw)
            if rc:
                log(f"cycle stopped at {layer} repair with rc {rc}")
                return rc
    main_done = home / "state" / "refresh" / "MAIN.done"
    if not main_done.is_file():  # a keyless resume must not re-stamp the MAIN time
        _write_marker(main_done, now)
    layer, cols = KEYLESS_LAYER
    if not selected(layer):
        log(f"{layer}: not selected -- skipping")
        return 0
    return run_layer(home, layer, cols, gather_main, workers=workers(layer) if workers else 1, **kw)


def _cycle_open(home: Path) -> Path:
    """Marker of a cycle in progress: written when a cycle starts, removed when it returns 0."""
    return home / "state" / "refresh" / "cycle.open"


def _main_done_ts(home: Path) -> float | None:
    """The time in ``state/refresh/MAIN.done`` as epoch seconds, or None when it is missing or unreadable."""
    p = home / "state" / "refresh" / "MAIN.done"
    if not p.is_file():
        return None
    try:
        ts = datetime.strptime(p.read_text(encoding="utf-8").strip(), "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return ts.replace(tzinfo=timezone.utc).timestamp()


def _main_fresh(home: Path, every_days: float, now_ts: float) -> bool:
    done = _main_done_ts(home)
    return done is not None and now_ts - done <= every_days * 86400


def next_due(home: Path, every_days: float) -> float | None:
    """When the next cycle is due: MAIN.done plus ``every_days``; None before a first cycle."""
    done = _main_done_ts(Path(home))
    return None if done is None else done + every_days * 86400


def _retire_markers(home: Path, now: Callable[[], float], log: Callable[[str], object]) -> None:
    """Rename (never delete) the markers of the previous cycle into state/refresh-prev/ (a D4 concurrency pin,
    ``*.pinned``, lasts one cycle the same way: retiring it also lifts the GitHub transport's pin in this process)."""
    state = home / "state" / "refresh"
    prev = home / "state" / "refresh-prev"
    stamp = datetime.fromtimestamp(now(), timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    unpinned = False
    for p in sorted(state.glob("*")):
        if not p.is_file() or not (p.suffix in (".complete", ".staged", ".repaired", ".pinned")
                                   or p.name == "MAIN.done"):
            continue
        prev.mkdir(parents=True, exist_ok=True)
        target = prev / p.name
        if target.exists():
            target = prev / f"{p.name}.{stamp}"
        p.rename(target)
        log(f"retired marker {p.name} -> {target}")
        unpinned = unpinned or p.suffix == ".pinned"
    # RB5 follow-up 8: the transport keeps its pin in memory, so without this a step at 3 workers would still have one
    # request in flight until the app restarts. A transport not loaded in this process holds no pin.
    transport = sys.modules.get("porchlamp.collect.ghcli")
    if unpinned and transport is not None:
        transport._THROTTLE.unpin()


def _value(v):
    return v() if callable(v) else v


IDLE_LOG_EVERY_S = 3600.0


def run_forever(
    home,
    gather_main: GatherMain,
    *,
    every_days: float | Callable[[], float] = 28,
    poll_s: float = 3600,
    sleep: Callable[[float], object] = time.sleep,
    now: Callable[[], float] = time.time,
    log: Callable[[str], object] = print,
    layers: Callable[[], Iterable[str]] | None = None,
    state: RunState | None = None,
    notify: Notify | None = None,
    ready: Callable[[], bool] | None = None,
    workers: Callable[[str], int] | None = None,
) -> None:
    """Each pass: resume a cycle in progress (cycle.open), else start a new one when MAIN.done
    is missing or old, else say it is idle and sleep.

    A cycle in progress is resumed without retiring any marker and without restaging: its
    staged envelopes stay. cycle.open is removed only when run_cycle returns 0 (MAIN and keyless
    layers all complete), so a restart or a failed keyless layer resumes the same way.

    ``every_days`` and ``layers`` may be callables, read afresh on every pass, so the owner's settings apply
    without a restart. While idle the runner logs "Idle, next cycle due DATE" (at most once an hour).
    ``ready`` is asked before a cycle starts or resumes (never while idle): False means "the repo list is not here
    yet", and the runner waits ``poll_s`` and asks again. ``workers(layer)`` goes to :func:`run_cycle`.
    """
    home = Path(home)
    last_idle_log = -IDLE_LOG_EVERY_S
    was_waiting = False
    while True:
        days = _value(every_days)
        cycle_due = _cycle_open(home).is_file() or not _main_fresh(home, days, now())
        if ready is not None and cycle_due and not ready():
            if not was_waiting:
                log("run_forever: waiting for the repo list")
                _note(notify, "no_candidates")
            was_waiting = True
            if state is not None:
                state.mode = "no_candidates"
            sleep(poll_s)
            continue
        was_waiting = False
        if _cycle_open(home).is_file():
            log("run_forever: cycle.open present -- resuming the cycle in progress")
            if state is not None:
                state.cycle_started = marker_ts(_cycle_open(home)) or now()
            _note(notify, "cycle_resume")
        elif not _main_fresh(home, days, now()):
            log("run_forever: MAIN.done missing or old -- starting a new cycle")
            _retire_markers(home, now, log)
            _write_marker(_cycle_open(home), now)
            if state is not None:
                state.cycle_started = now()
            _note(notify, "cycle_start")
        else:
            due = next_due(home, days)
            if state is not None:
                state.mode, state.idle_next_due, state.layer, state.run = "idle", due, None, 0
            if now() - last_idle_log >= IDLE_LOG_EVERY_S and due is not None:
                last_idle_log = now()
                log(f"Idle, next cycle due {datetime.fromtimestamp(due):%Y-%m-%d}")
                _note(notify, "idle", next_due=due)
            sleep(poll_s)
            continue
        if state is not None:
            state.mode, state.idle_next_due = "running", None
        rc = run_cycle(home, gather_main, log=log, now=now, layers=layers, state=state, notify=notify,
                       workers=workers)
        if state is not None:
            state.last_rc = rc
        if rc == 0:
            _cycle_open(home).unlink(missing_ok=True)
            last_idle_log = -IDLE_LOG_EVERY_S
            if state is not None:
                state.failures, state.finished_at, state.layer, state.run = 0, now(), None, 0
            _note(notify, "cycle_done")
        else:
            log(f"run_forever: cycle stopped with rc {rc}; resuming after {poll_s} s")
            if state is not None:
                state.failures += 1
                state.last_failure = f"a step stopped with code {rc}"
            _note(notify, "cycle_fail", rc=rc)
            sleep(poll_s)


def marker_ts(path: Path) -> float | None:
    try:
        ts = datetime.strptime(path.read_text(encoding="utf-8").strip(), "%Y-%m-%dT%H:%M:%SZ")
    except (OSError, ValueError):
        return None
    return ts.replace(tzinfo=timezone.utc).timestamp()
