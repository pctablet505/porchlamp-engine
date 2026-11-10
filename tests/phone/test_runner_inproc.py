"""Tests for phone/runner.py with stub gatherers: no real scripts/gather.py, no network."""

from __future__ import annotations

import contextlib
import errno
import json
import re
import sys
import tarfile
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

from phone import archive
from phone import runner as R

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc).timestamp()
STALE_DIR = "raw-stale-2026-10-11"
ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
PR = "pr_window"
L0 = "github_meta,pr_events,pr_reviews,contributors,actions_runs,tree,commits_180d"
L2 = "repo_object,gfi,dependents,releases,runs"
L4 = "author_history"
KEYLESS = "depsdev,scorecard,downloads"
OLD = "2026-09-01T00:00:00Z"  # 40 days before NOW: MAIN.done is old
FRESH = "2026-10-10T00:00:00Z"  # one day before NOW: MAIN.done is fresh
OPEN = "cycle.open"


class _Stop(Exception):
    """Ends run_forever from an injected sleep."""


def _raise_stop(_s):
    raise _Stop


class Gather:
    """Stub gather.main. rule(cols, n) gives the n-th action for a layer: an int (a done line
    with that failure count), "nodone", or an exception instance (raised)."""

    def __init__(self, rule=lambda c, n: 0, before=None):
        self.rule, self.before, self.calls, self.counts = rule, before, [], {}

    def __call__(self, argv):
        cols = "repair" if "--repair-replies" in argv else argv[3]
        n = self.counts[cols] = self.counts.get(cols, 0) + 1
        self.calls.append(cols)
        if self.before:
            self.before(cols, n)
        act = self.rule(cols, n)
        if isinstance(act, BaseException):
            raise act
        print("no summary line" if act == "nodone" else f"done: 1 clean, {act} with failures")
        return 0


def _put(path: Path, text: str = "{}") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _mark(home: Path, name: str, text: str = OLD) -> None:
    _put(home / "state/refresh" / name, text + "\n")


def _ex(home: Path, name: str) -> bool:
    return (home / "state/refresh" / name).is_file()


def _listing(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


@pytest.fixture(autouse=True)
def fake_gql(monkeypatch):
    """A fake porchlamp.collect.github_graphql, so apply_pagesize never imports the real one."""
    mod = types.ModuleType("porchlamp.collect.github_graphql")
    mod.pages, mod.mode = [], "fail25"

    def collect_pr_window(*args, **kwargs):
        page = kwargs.get("page_size")
        mod.pages.append(page)
        if mod.mode == "other":
            raise ValueError("bad input")
        if mod.mode == "always" or (mod.mode == "fail25" and page == 25):
            raise RuntimeError("HTTP 504 Gateway Timeout")
        return f"ok{page}"

    mod.collect_pr_window = collect_pr_window
    monkeypatch.setitem(sys.modules, "porchlamp.collect.github_graphql", mod)
    return mod


@pytest.fixture
def home(tmp_path: Path) -> Path:
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


@pytest.fixture
def logs() -> list[str]:
    return []


@pytest.fixture
def kw(logs):
    return {"free_gb": lambda _h: 100.0, "now": lambda: NOW, "log": logs.append}


@pytest.fixture
def fkw(kw):
    """run_forever has no free_gb parameter: it uses the real shutil_free default."""
    return {k: v for k, v in kw.items() if k != "free_gb"}


def test_outcome_lines_are_classified():
    none = (0, 0, 0, 0, 0)                      # (checked, stopped, pruned, failed, total) after one line
    cases = [
        ("collectors: governance; repos: 3", (0, 0, 0, 0, 3)),
        ("o/r.x-1 [pypi]: SKIP (ineligible: no_external_workflow)", (1, 0, 1, 0, 0)),
        ("o/r.x-1 [pypi]: SKIP (clean fingerprint)", (1, 0, 0, 0, 0)),
        ("o/r.x-1 [pypi]: SKIP (selected collectors clean)", (1, 0, 0, 0, 0)),
        ("o/r.x-1 [pypi]: FAIL HTTP 502: Bad Gateway", (1, 0, 0, 1, 0)),
        ("o/r.x-1 [pypi]: OK (stopped at black_hole)", (1, 1, 0, 0, 0)),
        ("o/r.x-1 [pypi]: OK", (1, 0, 0, 0, 0)),
        ("o/r.x-1 [pypi]: FAIL governance,gfi", (1, 0, 0, 1, 0)),
        ("o/r.x-1 [pypi]: FAIL ", (1, 0, 0, 1, 0)),
        ("done: 2 clean, 0 with failures", none),
        ("  WARN tree o/r.x-1: HTTP 502: Bad Gateway", none),
    ]
    for line, want in cases:
        state = R.RunState()
        R.count_line(state, line)
        assert (state.checked, state.stopped, state.pruned, state.failed, state.total) == want, line


def _p4_gather(argv):
    """Stub gather.main printing the lines of probe P4 under --skip-ineligible: one project is stopped, one is OK."""
    print("collectors: x; repos: 2")
    print("o/ok [pypi]: OK")
    print("o/bh [pypi]: OK (stopped at black_hole)")
    print("done: 2 clean, 0 with failures")
    return 0


def _p4_gather_with_a_failure(argv):
    """The same lines as :func:`_p4_gather` with one project that fails every run, so a second run is needed."""
    print("collectors: x; repos: 2")
    print("o/ok [pypi]: OK")
    print("o/bh [pypi]: OK (stopped at black_hole)")
    print("done: 2 clean, 1 with failures")
    return 0


def test_counts_reset_each_run(home, kw):
    state, seen = R.RunState(), []

    def log(line):
        if line.startswith("o/"):                                       # the counts as the app would read them
            seen.append((state.run, state.checked, state.stopped))

    assert R.run_layer(home, "L3g", "governance", _p4_gather_with_a_failure, state=state, **{**kw, "log": log}) == 0
    assert seen == [(1, 1, 0), (1, 2, 1), (2, 1, 0), (2, 2, 1)]        # the second run starts again from 0
    assert _ex(home, "L3g.complete")                                    # two runs agree: the step completes at run 2


def test_counts_reset_at_step_start(home, kw):
    state, at_start = R.RunState(), []

    def notify(name, **info):
        if name == "step_start":
            at_start.append((info["layer"], state.checked, state.total))

    for layer, cols in (("L3g", "governance"), ("L3c", "commits_365d,issues_counts")):
        assert R.run_layer(home, layer, cols, _p4_gather, state=state, notify=notify, **kw) == 0
    assert at_start == [("L3g", 0, 0), ("L3c", 0, 0)]                   # the second step does not inherit the first's


def test_step_done_carries_the_last_runs_counts(home, kw):
    told = []

    def notify(name, **info):
        told.append((name, info))

    R.run_layer(home, "L3g", "governance", _p4_gather, state=R.RunState(), notify=notify, **kw)
    R.run_layer(home, "L3c", "commits_365d,issues_counts", _p4_gather, notify=notify, **kw)   # no state, no counts
    assert [x for x in told if x[0] == "step_done"] == [
        ("step_done", {"layer": "L3g", "checked": 2, "stopped": 1}), ("step_done", {"layer": "L3c"})]


def test_two_agreeing_runs_complete_a_layer(home, kw):
    g = Gather(lambda c, n: 3)
    assert R.run_layer(home, "L1", PR, g, **kw) == 0
    assert len(g.calls) == 2
    assert ISO.fullmatch((home / "state/refresh/L1.complete").read_text().strip())


def test_two_runs_where_every_project_failed_do_not_complete_a_layer(home, kw):
    """A failure that hits every project (a rejected token, a corrupt state file) repeats the same count, and must not
    read as a finished step with nothing fetched: the step stops at max_runs, and the app shows it."""
    calls = []

    def gather(argv):
        calls.append(argv)
        print("done: 0 clean, 9 with failures")
        return 0

    assert R.run_layer(home, "L4", L4, gather, max_runs=3, **kw) == 2
    assert len(calls) == 3 and not _ex(home, "L4.complete")


def test_moving_counts_do_not_complete(home, kw, tmp_path):
    counts = [3, 2, 1, 2, 2]
    g = Gather(lambda c, n: counts[n - 1])
    assert R.run_layer(home, "L1", PR, g, max_runs=4, **kw) == 2
    assert len(g.calls) == 4 and not _ex(home, "L1.complete")
    home2 = tmp_path / "home2"
    home2.mkdir()
    g2 = Gather(lambda c, n: counts[n - 1])
    assert R.run_layer(home2, "L1", PR, g2, **kw) == 0
    assert len(g2.calls) == 5


def test_a_clean_first_run_completes_a_layer(home, kw):
    """A run with no failure leaves nothing for a second run to read again (every envelope exists), so the layer is
    complete at once: the second pass over thousands of projects cost a whole skip-check of the universe."""
    g = Gather(lambda c, n: 0)
    assert R.run_layer(home, "L4", L4, g, **kw) == 0
    assert len(g.calls) == 1
    assert ISO.fullmatch((home / "state/refresh/L4.complete").read_text().strip())


def test_a_first_run_that_checked_nothing_is_not_taken_as_clean(home, kw):
    """``done: 0 clean, 0 with failures`` (an empty candidate list, a gatherer that did not look) is no proof: a
    second run must still agree before the layer completes."""
    def empty(argv):
        print("done: 0 clean, 0 with failures")
        return 0

    calls = []
    assert R.run_layer(home, "L4", L4, lambda argv: calls.append(argv) or empty(argv), **kw) == 0
    assert len(calls) == 2


def test_max_runs_returns_2(home, kw):
    g = Gather(lambda c, n: n)
    assert R.run_layer(home, "L1", PR, g, max_runs=3, **kw) == 2
    assert len(g.calls) == 3 and not _ex(home, "L1.complete")


def test_complete_marker_skips_the_layer(home, kw):
    _mark(home, "L1.complete", "2026-10-10T00:00:00Z")
    g = Gather()
    assert R.run_layer(home, "L1", PR, g, **kw) == 0 and g.calls == []


def test_staging_renames_never_deletes(home, kw):
    _put(home / "raw/pr_window/old.json")
    assert R.run_layer(home, "L1", PR, Gather(), **kw) == 0
    assert (home / STALE_DIR / "pr_window/old.json").read_text() == "{}"
    assert not (home / "raw/pr_window/old.json").exists()
    assert (home / "raw/pr_window").is_dir()


def test_staging_refuses_existing_target_rc5(home, kw):
    _put(home / "raw/pr_window/new.json")
    (home / STALE_DIR / "pr_window").mkdir(parents=True)
    g = Gather()
    assert R.run_layer(home, "L1", PR, g, **kw) == 5
    assert g.calls == [] and (home / "raw/pr_window/new.json").exists()
    assert not _ex(home, "L1.staged")


def test_staging_recovers_after_partial_interruption_or_empty_dir(home, kw):
    # Suppose github_meta was already moved to raw-stale-<date>/github_meta and recreated empty before crash,
    # while pr_events still has envelopes to stage and L0.staged was not yet written.
    _put(home / STALE_DIR / "github_meta/old_meta.json")
    (home / "raw/github_meta").mkdir(parents=True)
    _put(home / "raw/pr_events/old_events.json")
    assert R.run_layer(home, "L0", L0, Gather(), **kw) == 0
    assert (home / STALE_DIR / "github_meta/old_meta.json").read_text() == "{}"
    assert (home / STALE_DIR / "pr_events/old_events.json").read_text() == "{}"
    assert _ex(home, "L0.staged") and _ex(home, "L0.complete")


def test_second_cycle_on_same_utc_day_disambiguates_stale_dir(home, kw):
    # First cycle staged earlier today and its markers were retired to state/refresh-prev/.
    _put(home / STALE_DIR / "pr_window/cycle1.json", '{"cycle": 1}')
    _put(home / "state/refresh-prev/L1.staged", FRESH + "\n")
    _put(home / "raw/pr_window/cycle2.json", '{"cycle": 2}')
    assert R.run_layer(home, "L1", PR, Gather(), **kw) == 0
    assert (home / STALE_DIR / "pr_window/cycle1.json").read_text() == '{"cycle": 1}'
    assert (home / f"{STALE_DIR}-120000Z" / "pr_window/cycle2.json").read_text() == '{"cycle": 2}'
    # A third cycle on the same UTC day (even at the same clock second) disambiguates with -2.
    R._retire_markers(home, lambda: NOW, lambda _m: None)
    _put(home / "raw/pr_window/cycle3.json", '{"cycle": 3}')
    assert R.run_layer(home, "L1", PR, Gather(), **kw) == 0
    assert (home / f"{STALE_DIR}-120000Z-2" / "pr_window/cycle3.json").read_text() == '{"cycle": 3}'


def test_staging_happens_once(home, kw):
    _put(home / "raw/pr_window/old.json")
    new = home / "raw/pr_window/new.json"
    g1 = Gather(lambda c, n: RuntimeError("boom"), lambda c, n: _put(new))
    assert R.run_layer(home, "L1", PR, g1, **kw) == 3
    before = _listing(home / STALE_DIR)
    assert R.run_layer(home, "L1", PR, Gather(), **kw) == 0
    assert (home / "raw/pr_window/new.json").exists()
    assert _listing(home / STALE_DIR) == before


def test_exception_returns_3_and_logs_traceback(home, kw, logs):
    assert R.run_layer(home, "L1", PR, Gather(lambda c, n: RuntimeError("boom")), **kw) == 3
    text = "\n".join(logs)
    assert "Traceback (most recent call last)" in text and "boom" in text


def test_systemexit_returns_3(home, kw):
    g = Gather(lambda c, n: SystemExit("unknown collector"))
    assert R.run_layer(home, "L1", PR, g, **kw) == 3


def test_no_done_line_returns_4(home, kw):
    assert R.run_layer(home, "L1", PR, Gather(lambda c, n: "nodone"), **kw) == 4


def test_only_this_runs_done_line_counts(home, kw):
    # run 1 prints a done line with 3 failures; run 2 prints none. The old line must not count.
    g = Gather(lambda c, n: 3 if n == 1 else "nodone")
    assert R.run_layer(home, "L1", PR, g, **kw) == 4
    assert g.calls == [PR, PR]


def test_low_disk_returns_6(home, kw):
    g = Gather()
    kw["free_gb"] = lambda _h: 0.5
    assert R.run_layer(home, "L1", PR, g, min_free_gb=2.0, **kw) == 6
    assert g.calls == []


def test_main_order_l1_l0_l2_l3g_l3c_l4(home, kw):
    g = Gather()
    assert R.run_cycle(home, g, **kw) == 0
    order = [c for i, c in enumerate(g.calls) if i == 0 or g.calls[i - 1] != c]
    assert order == [PR, "repair", L0, L2, "governance", "commits_365d,issues_counts", L4, KEYLESS]  # the repair follows step 1


def test_cycle_stops_at_first_failing_layer_and_no_main_done(home, kw):
    g = Gather(lambda c, n: "nodone" if c.startswith("repo_object") else 0)
    assert R.run_cycle(home, g, **kw) == 4
    assert "depsdev" not in " ".join(g.calls) and not _ex(home, "MAIN.done")


def test_main_done_written_after_l4_before_keyless(home, kw):
    seen = {}

    def record(cols, n):
        if n == 1:
            seen[cols] = _ex(home, "MAIN.done")

    assert R.run_cycle(home, Gather(before=record), **kw) == 0
    assert seen[L4] is False and seen[KEYLESS] is True


def test_keyless_failure_keeps_main_done(home, kw):
    g = Gather(lambda c, n: "nodone" if c == KEYLESS else 0)
    assert R.run_cycle(home, g, **kw) == 4
    assert _ex(home, "MAIN.done")


def test_apply_pagesize_is_idempotent_and_never_refetches_a_failed_project(fake_gql):
    """The collector reads a failing page again in pages of 10 itself, so the wrapper only sets the page size: a
    project that still fails is not fetched again from its first page (the layer's next run does that)."""
    R.apply_pagesize()
    R.apply_pagesize()
    g = fake_gql.collect_pr_window
    fake_gql.mode = "ok"
    assert g("x") == "ok25"
    assert g("x", page_size=10) == "ok10"  # a page size the caller passes stands
    assert fake_gql.pages == [25, 10]  # a double wrap would call twice
    fake_gql.pages.clear()
    fake_gql.mode = "fail25"
    with pytest.raises(RuntimeError):
        g("x")
    assert fake_gql.pages == [25]  # a query-weight failure is not retried for the whole project
    fake_gql.pages.clear()
    fake_gql.mode = "other"
    with pytest.raises(ValueError):
        g("x")
    assert fake_gql.pages == [25]


def test_run_forever_starts_when_main_done_missing_or_old_and_sleeps_otherwise(home, fkw):
    sleeps = []

    def stop_after_one(s):
        sleeps.append(s)
        raise _Stop

    g = Gather()
    with pytest.raises(_Stop):
        R.run_forever(home, g, poll_s=5, sleep=stop_after_one, **fkw)
    assert len(g.calls) > 0 and sleeps == [5] and _ex(home, "MAIN.done")
    g2 = Gather()
    sleeps.clear()
    with pytest.raises(_Stop):
        R.run_forever(home, g2, poll_s=5, sleep=stop_after_one, **fkw)
    assert g2.calls == [] and sleeps == [5]
    _mark(home, "MAIN.done", OLD)
    g3 = Gather()
    with pytest.raises(_Stop):
        R.run_forever(home, g3, poll_s=5, sleep=stop_after_one, **fkw)
    assert len(g3.calls) > 0


def test_run_forever_moves_old_markers_to_refresh_prev_not_deleting(home, fkw):
    _mark(home, "MAIN.done")
    _mark(home, "L1.complete")
    with pytest.raises(_Stop):
        R.run_forever(home, Gather(), poll_s=5, sleep=_raise_stop, **fkw)
    prev = home / "state/refresh-prev"
    assert (prev / "L1.complete").read_text() == OLD + "\n"
    assert (prev / "MAIN.done").read_text() == OLD + "\n"


def test_run_forever_retry_after_failure_resumes_without_restaging(home, fkw, logs):
    _put(home / "raw/pr_window/old.json")
    g = Gather(lambda c, n: "nodone" if c == L4 and n == 1 else 0)
    sleeps = []

    def stop_at_third(s):
        sleeps.append(s)
        if len(sleeps) >= 3:
            raise _Stop

    with pytest.raises(_Stop):
        R.run_forever(home, g, poll_s=5, sleep=stop_at_third, **fkw)
    text = "\n".join(logs)
    assert "resuming after 5 s" in text and "BLOCKED staging" not in text
    assert _ex(home, "MAIN.done")
    assert sorted(p.name for p in (home / STALE_DIR).iterdir()) == ["pr_window"]


def test_log_print_does_not_recurse_through_the_stdout_tee(home, capsys):
    # log=print writes to sys.stdout, which the runner redirects to its tee while the gatherer
    # runs. Each logged line must reach the real stdout, not re-enter the tee.
    g = Gather()
    assert R.run_layer(home, "L1", PR, g, free_gb=lambda _h: 100.0, now=lambda: NOW) == 0
    out = capsys.readouterr().out
    assert "L1: COMPLETE" in out and "done: 1 clean, 0 with failures" in out


def test_restart_mid_cycle_resumes_without_retiring_markers_or_rc5(home, fkw, logs):
    # A restart on the same day: cycle.open present, L1 and L0 complete, L2 staged, no MAIN.done.
    for layer in ("L1", "L0"):
        _mark(home, f"{layer}.staged")
        _mark(home, f"{layer}.complete")
    _mark(home, "L2.staged")
    _mark(home, OPEN)
    _put(home / "raw/repo_object/new.json")
    _put(home / STALE_DIR / "repo_object/old.json")  # left by the staging earlier today
    stale = _listing(home / STALE_DIR)
    g = Gather()
    with pytest.raises(_Stop):
        R.run_forever(home, g, poll_s=5, sleep=_raise_stop, **fkw)
    assert PR not in g.calls and L0 not in g.calls  # finished layers are skipped
    assert g.calls.count(L2) == 1  # the staged layer reruns once, and a run with no failure completes it
    assert g.calls.count(KEYLESS) == 1
    assert (home / "state/refresh/L1.complete").read_text() == OLD + "\n"  # nothing retired
    assert not (home / "state/refresh-prev").exists()
    assert (home / "raw/repo_object/new.json").exists()  # not restaged
    assert _listing(home / STALE_DIR) == stale  # no rc 5 collision, nothing moved
    assert not any("BLOCKED" in line for line in logs)
    assert _ex(home, "MAIN.done") and not _ex(home, OPEN)


def test_cycle_open_removed_only_after_a_fully_complete_cycle(home, fkw):
    st = {"ok": False, "sleeps": []}
    g = Gather(lambda c, n: "nodone" if c == KEYLESS and not st["ok"] else 0)

    def sleep(s):
        st["sleeps"].append(s)
        if len(st["sleeps"]) > 1:
            raise _Stop
        st.update(open=_ex(home, OPEN), main=_ex(home, "MAIN.done"), ok=True)

    with pytest.raises(_Stop):
        R.run_forever(home, g, poll_s=5, sleep=sleep, **fkw)
    assert st["open"] and st["main"]
    assert st["sleeps"] == [5, 5] and not _ex(home, OPEN)


def test_restart_during_keyless_layer_with_main_done_present_resumes_the_keyless_layer(home, fkw):
    for layer, _cols in R.MAIN_LAYERS:
        _mark(home, f"{layer}.staged")
        _mark(home, f"{layer}.complete")
    _mark(home, "L1.repaired")
    _mark(home, "MAIN.done", FRESH)
    _mark(home, "LK.staged", FRESH)
    _mark(home, OPEN, FRESH)
    _put(home / "raw/depsdev/new.json")
    g = Gather()
    with pytest.raises(_Stop):
        R.run_forever(home, g, poll_s=5, sleep=_raise_stop, **fkw)
    assert g.calls == [KEYLESS]  # only the keyless layer reran
    assert _ex(home, "LK.complete") and not _ex(home, OPEN)
    assert (home / "state/refresh/MAIN.done").read_text() == FRESH + "\n"  # not re-stamped
    assert not (home / "state/refresh-prev").exists()
    assert (home / "raw/depsdev/new.json").exists()


def test_old_main_done_without_cycle_open_starts_a_new_cycle(home, fkw):
    _mark(home, "L1.complete")
    _mark(home, "MAIN.done")
    seen = {}  # the cycle is marked open before its first layer
    g = Gather(before=lambda c, n: seen.setdefault("open", _ex(home, OPEN)))
    with pytest.raises(_Stop):
        R.run_forever(home, g, poll_s=5, sleep=_raise_stop, **fkw)
    prev = home / "state/refresh-prev"
    assert seen["open"] is True
    assert (prev / "L1.complete").read_text() == OLD + "\n"  # retired, not deleted
    assert (prev / "MAIN.done").read_text() == OLD + "\n"
    assert g.calls.count(PR) == 1  # L1 reran
    assert (home / "state/refresh/MAIN.done").read_text() != OLD + "\n"
    assert not _ex(home, OPEN)


def test_fresh_main_done_without_cycle_open_only_sleeps(home, fkw):
    _mark(home, "L1.complete")
    _mark(home, "MAIN.done", FRESH)
    g = Gather()
    sleeps = []

    def stop(s):
        sleeps.append(s)
        raise _Stop

    with pytest.raises(_Stop):
        R.run_forever(home, g, poll_s=5, sleep=stop, **fkw)
    assert sleeps == [5] and g.calls == []
    assert _ex(home, "L1.complete") and not (home / "state/refresh-prev").exists()
    assert not _ex(home, OPEN)  # nothing started


def test_main_layers_keep_the_order_the_as_of_rule_needs():
    # The as_of rule (porchlamp.snapshot.asof) needs L2 and L3c right after L1 and L4 last: reordering moves the
    # releases/issues_counts anchors out of 14 days of the newest pull-request record.
    assert [layer for layer, _ in R.MAIN_LAYERS] == ["L1", "L0", "L2", "L3g", "L3c", "L4"]


# ---- D4: projects side by side in the GitHub steps that may use it ----------------------------------------------
def test_workers_flag_only_for_l4_l0_l3g_l3c(home, kw, logs):
    """T20: with ``workers`` saying 3 for every step, only L0, L3g, L3c and L4 run gather with ``--workers 3`` (and
    say so once); L1 (its heavy pages draw GitHub's limits one at a time), L2 (third-party hosts) and LK (pypistats'
    5 calls a minute) never get the flag, nor does the repair after L1. At 1 no step gets it."""
    argvs: dict[str, list[list[str]]] = {}

    def gather(argv):
        cols = "repair" if "--repair-replies" in argv else argv[3]
        argvs.setdefault(cols, []).append(list(argv))
        print("done: 1 clean, 0 with failures")
        return 0

    assert R.run_cycle(home, gather, workers=lambda layer: 3, **kw) == 0
    flagged = {cols for cols, runs in argvs.items() if any("--workers" in argv for argv in runs)}
    assert flagged == {L0, L4, "governance", "commits_365d,issues_counts"}
    assert all(argv[-2:] == ["--workers", "3"] for cols in flagged for argv in argvs[cols])
    assert {PR, "repair", L2, KEYLESS} <= set(argvs)
    assert [line for line in logs if line.endswith("projects at once")] == [
        "L0: 3 projects at once", "L3g: 3 projects at once", "L3c: 3 projects at once", "L4: 3 projects at once"]

    other = home.parent / "other"
    other.mkdir()
    argvs.clear()
    logs.clear()
    assert R.run_cycle(other, gather, workers=lambda layer: 1, **kw) == 0
    assert argvs and not any("--workers" in argv for runs in argvs.values() for argv in runs)
    assert not any(line.endswith("projects at once") for line in logs)


def test_a_new_cycle_retires_the_concurrency_pin(home, fkw):
    """D4: a pin to one request at a time (``state/refresh/concurrency.pinned``) lasts one cycle: the next cycle's
    start renames it into ``refresh-prev`` with the other markers, never deletes it."""
    _mark(home, "MAIN.done")
    _mark(home, "concurrency.pinned", "2026-09-20T10:00:00Z")
    with pytest.raises(_Stop):
        R.run_forever(home, Gather(lambda c, n: _Stop()), sleep=_raise_stop, **fkw)
    assert not _ex(home, "concurrency.pinned")
    assert (home / "state" / "refresh-prev" / "concurrency.pinned").read_text().strip() == "2026-09-20T10:00:00Z"


def _pinned_throttle(monkeypatch: pytest.MonkeyPatch):
    """A fresh process-wide GitHub throttle pinned the way a cycle pins it: three secondary limits, each a new GitHub
    event, while three requests may be in flight (``PORCHLAMP_GH_INFLIGHT=3``)."""
    from porchlamp.collect import ghcli

    clock = [NOW]
    throttle = ghcli._Throttle()
    monkeypatch.setattr(ghcli, "_THROTTLE", throttle)
    monkeypatch.setattr(ghcli, "_now", lambda: clock[0])
    monkeypatch.setattr(ghcli, "on_event", None)
    monkeypatch.setenv(R.INFLIGHT_ENV, "3")
    for _ in range(3):
        clock[0] += 1.0
        throttle.signal(60.0, "HTTP 403, retry-after", clock[0])
    assert throttle.pinned
    return throttle


def test_a_new_cycle_that_retires_the_pin_file_unpins_the_transport(home, monkeypatch):
    """RB5 follow-up 8: the pin file's retirement at the next cycle's start also lifts the transport's pin in this
    process, so a step at 3 workers has 3 requests in flight again (the hold and the plain strikes stay)."""
    throttle = _pinned_throttle(monkeypatch)
    later = NOW + 28 * 86400.0                                         # the next cycle: every degrade has run out
    strikes, hold = list(throttle.strikes), throttle.hold_until
    _mark(home, "MAIN.done")
    _mark(home, "concurrency.pinned", "2026-10-11T12:00:03Z")
    assert throttle.cap(later) == 1                                    # pinned until the file is retired
    R._retire_markers(home, lambda: later, lambda line: None)
    assert not _ex(home, "concurrency.pinned")
    assert throttle.cap(later) == 3 and not throttle.pinned and not throttle.pin_strikes
    assert list(throttle.strikes) == strikes and throttle.hold_until == hold


def test_a_cycle_start_with_no_pin_file_leaves_a_live_pin(home, monkeypatch):
    """RB5 follow-up 8: a cycle start that retires no pin file (other markers only) leaves a pin the transport holds
    in this process, whose file is not written yet."""
    throttle = _pinned_throttle(monkeypatch)
    _mark(home, "MAIN.done")
    R._retire_markers(home, lambda: NOW + 10.0, lambda line: None)
    assert not _ex(home, "MAIN.done")
    assert throttle.pinned and len(throttle.pin_strikes) == 3 and throttle.cap(NOW + 28 * 86400.0) == 1


def test_a_line_printed_while_another_is_logged_stays_in_the_tee(capsys):
    """D4: worker threads print repo lines while the runner logs another. The tee used to swap ``sys.stdout`` for the
    whole process while it logged a line, so a line another thread printed meanwhile went to the real stdout and
    was never counted or logged."""
    import threading
    import time

    logged: list[str] = []
    in_log, printed = threading.Event(), threading.Event()

    def slow_log(line: str) -> None:
        logged.append(line)
        if line == "first":
            in_log.set()
            printed.wait(5)
            time.sleep(0.2)              # the other thread's print happens while this line is being logged

    def second() -> None:
        printed.set()
        print("second")

    tee = R._Tee(slow_log)
    with contextlib.redirect_stdout(tee):
        a = threading.Thread(target=print, args=("first",))
        a.start()
        assert in_log.wait(5)
        b = threading.Thread(target=second)
        b.start()
        b.join(5)
        a.join(5)
    tee.finish()
    assert sorted(logged) == ["first", "second"]
    assert sorted(tee.text().splitlines()) == ["first", "second"]
    assert "second" not in capsys.readouterr().out


def test_inflight_env_is_the_layers_own(home, kw, monkeypatch):
    """RB5 ruling 2: the GitHub transport's in-flight value is set per step before gather runs: the step's worker
    count where it runs side by side (L0, L3g, L3c, L4), 1 everywhere else (L1, L2, LK and the repair). L1's
    limits, taken one request at a time, then never count toward the pin."""
    monkeypatch.setenv("PORCHLAMP_GH_INFLIGHT", "9")                 # put back after the test
    seen: dict[str, list[str | None]] = {}

    def gather(argv):
        cols = "repair" if "--repair-replies" in argv else argv[3]
        seen.setdefault(cols, []).append(R.os.environ.get("PORCHLAMP_GH_INFLIGHT"))
        print("done: 1 clean, 0 with failures")
        return 0

    assert R.run_cycle(home, gather, workers=lambda layer: 3, **kw) == 0
    assert seen == {PR: ["1"], "repair": ["1"], L0: ["3"], L2: ["1"], "governance": ["3"],
                    "commits_365d,issues_counts": ["3"], L4: ["3"], KEYLESS: ["1"]}
    monkeypatch.setenv("PORCHLAMP_GH_INFLIGHT", "9")
    seen.clear()
    (home / "state" / "refresh" / R.REPAIR_MARKER).unlink()
    assert R.run_repair(home, gather, **kw) == 0
    assert seen == {"repair": ["1"]}


# ---- Q4: the stale-anchor refresh before MAIN.done -----------------------------------------------------------------
DAY = 86400.0
#: 16.5 days and 6.5 days before NOW.
OLD_ANCHOR, YOUNG_ANCHOR = "2026-09-25T00:00:00Z", "2026-10-05T00:00:00Z"


def _env(anchor: str, gen: str = "old") -> str:
    return json.dumps({"data": {"window_anchor": anchor, "gen": gen}})


def _anchor(home: Path, collector: str, key: str) -> str:
    return json.loads((home / "raw" / collector / f"{key}.json").read_text())["data"]["window_anchor"]


def _gen(home: Path, collector: str, key: str) -> str:
    return json.loads((home / "raw" / collector / f"{key}.json").read_text())["data"]["gen"]


def _layers_done(home: Path) -> None:
    for layer, _cols in R.MAIN_LAYERS:
        _mark(home, f"{layer}.complete")
        _mark(home, f"{layer}.staged")
    _mark(home, R.REPAIR_MARKER)


def _stale_home(home: Path) -> None:
    _layers_done(home)
    for collector, name in (("releases", "repo"), ("issues_counts", "issue")):
        _put(home / "raw" / collector / f"stale__{name}.json", _env(OLD_ANCHOR))
        _put(home / "raw" / collector / f"fresh__{name}.json", _env(YOUNG_ANCHOR))


def _files(home: Path) -> dict[str, list[str]]:
    return {c: sorted(p.name for p in (home / "raw" / c).glob("*.json")) for c in ("releases", "issues_counts")}


class Refetch:
    """gather.main as ``--refetch-keys`` runs it: each listed key is replaced by a fresh envelope unless ``fail(key, n)``
    says no (the old file stays, a FAIL line); the keyless layer only prints its done line. ``during(n)`` runs inside."""

    def __init__(self, home, fail=lambda key, n: False, during=None, stamp=FRESH):
        self.home, self.fail, self.during, self.stamp = home, fail, during, stamp
        self.calls: list[str] = []
        self.keys: list[tuple[str, list[str]]] = []
        self.files_at_call: list[dict[str, list[str]]] = []
        self.main_done_at_call: list[bool] = []

    def __call__(self, argv):
        cols = argv[3]
        self.calls.append(cols)
        self.main_done_at_call.append(_ex(self.home, "MAIN.done"))
        if "--refetch-keys" not in argv:
            print("done: 1 clean, 0 with failures")
            return 0
        keys = argv[argv.index("--refetch-keys") + 1].split(",")
        self.keys.append((cols, keys))
        self.files_at_call.append(_files(self.home))
        n = len(self.keys)
        if self.during:
            self.during(n)
        bad = 0
        for key in keys:
            if self.fail(key, n):
                bad += 1
            else:
                _put(self.home / "raw" / cols / f"{key}.json", _env(self.stamp, "new"))
        print(f"done: 1 clean, {bad} with failures")
        return 0


def test_refresh_fetches_again_only_the_stale_envelopes_before_main_done_and_every_file_stays_in_raw(home, kw):
    """Q4: gather gets the stale keys and nothing leaves ``raw/``: a process killed at any moment finds every record."""
    _stale_home(home)
    before = _files(home)
    g = Refetch(home)
    assert R.run_cycle(home, g, **kw) == 0
    assert g.keys == [("releases", ["stale__repo"]), ("issues_counts", ["stale__issue"])]
    assert g.files_at_call == [before, before]                                    # all still there while gather runs
    assert g.calls == ["releases", "issues_counts", KEYLESS]
    assert g.main_done_at_call == [False, False, True]                            # MAIN.done after the refresh
    assert _gen(home, "releases", "stale__repo") == _gen(home, "issues_counts", "stale__issue") == "new"
    assert _gen(home, "releases", "fresh__repo") == _gen(home, "issues_counts", "fresh__issue") == "old"
    kept = next(home.glob("raw-stale-*-anchor-*"))                                # the old record is kept, as a copy
    assert json.loads((kept / "releases/stale__repo.json").read_text())["data"]["gen"] == "old"
    assert json.loads((kept / "issues_counts/stale__issue.json").read_text())["data"]["gen"] == "old"
    assert (home / "state/refresh/L2.complete").read_text().strip() == "2026-10-11T12:00:00Z"
    assert (home / "state/refresh/L3c.complete").read_text().strip() == "2026-10-11T12:00:00Z"
    for layer, member in (("L2", "raw/releases/stale__repo.json"), ("L3c", "raw/issues_counts/stale__issue.json")):
        with tarfile.open(home / "archives" / f"{layer}.tar.gz") as tar:
            assert json.loads(tar.extractfile(member).read())["data"]["gen"] == "new"


@pytest.mark.parametrize("age_s, stale", [(13 * DAY + 1, True), (13 * DAY - 1, False)])
def test_refresh_threshold_is_thirteen_days(home, kw, age_s, stale):
    _layers_done(home)
    anchor = datetime.fromtimestamp(NOW - age_s, timezone.utc).isoformat()
    _put(home / "raw/releases/o__r.json", _env(anchor))
    g = Refetch(home)
    assert R.run_cycle(home, g, **kw) == 0
    assert g.keys == ([("releases", ["o__r"])] if stale else [])


def test_refresh_is_not_run_when_main_done_is_there_nor_for_a_layer_not_selected(home, kw):
    _stale_home(home)
    g = Refetch(home)
    assert R.run_cycle(home, g, layers=lambda: ["L1", "L0", "L3g", "L3c", "L4", "LK"], **kw) == 0
    assert g.keys == [("issues_counts", ["stale__issue"])]                        # L2 is not selected
    assert _gen(home, "releases", "stale__repo") == "old"
    (home / "state/refresh/LK.complete").unlink()
    g = Refetch(home)
    assert R.run_cycle(home, g, **kw) == 0                                        # a keyless resume: MAIN.done is there
    assert g.keys == [] and g.calls == [KEYLESS]


def test_a_layer_that_did_not_run_this_cycle_is_not_refreshed_and_gets_no_marker(home, kw):
    """Switched on during L4: its files are another cycle's, and a marker would say the layer ran."""
    _stale_home(home)
    (home / "state/refresh/L2.complete").unlink()
    (home / "state/refresh/L2.staged").unlink()
    asked = {"n": 0}

    def selection():
        asked["n"] += 1
        return ["L1", "L0", "L3g", "L3c", "L4", "LK"] if asked["n"] <= 6 else ["L1", "L0", "L2", "L3g", "L3c", "L4", "LK"]

    g = Refetch(home)
    assert R.run_cycle(home, g, layers=selection, **kw) == 0
    assert g.keys == [("issues_counts", ["stale__issue"])]
    assert not _ex(home, "L2.complete") and _gen(home, "releases", "stale__repo") == "old"


class _Killed(BaseException):
    """Stands for a stop (``phone.httpgate.Stopped`` is a ``BaseException`` too): nothing may catch it on the way out."""


def test_a_stop_during_the_refresh_goes_through_and_the_next_start_carries_on(home, kw):
    _stale_home(home)
    before = _files(home)

    def stop(n):
        raise _Killed

    g = Refetch(home, during=stop)
    with pytest.raises(_Killed):
        R.run_cycle(home, g, **kw)
    assert _files(home) == before and not _ex(home, "MAIN.done")
    g = Refetch(home)
    assert R.run_cycle(home, g, **kw) == 0
    assert g.keys == [("releases", ["stale__repo"]), ("issues_counts", ["stale__issue"])]
    assert _ex(home, "MAIN.done") and _gen(home, "releases", "stale__repo") == "new"


def test_a_process_dead_after_part_of_the_fetches_leaves_the_archive_marked_out_of_date(home, kw):
    """What was fetched before the death reaches the pull: the marker is written before the first fetch."""
    _stale_home(home)
    archive.build_archive(home, "L2")

    def stop(n):
        _put(home / "raw/releases/stale__repo.json", _env(FRESH, "new"))
        raise _Killed

    with pytest.raises(_Killed):
        R.run_cycle(home, Refetch(home, during=stop), **kw)
    manifest = archive.write_manifest(home)
    with tarfile.open(home / "archives/L2.tar.gz") as tar:
        assert json.loads(tar.extractfile("raw/releases/stale__repo.json").read())["data"]["gen"] == "new"
    assert manifest["layers"]["L2"]["files"] == 2


def test_a_pull_during_the_refresh_does_not_freeze_the_archive(home, kw):
    """The marker is written again at the end, so an archive built meanwhile is not taken for current."""
    _stale_home(home)
    clock = {"t": NOW}

    def tick():
        clock["t"] += 5.0
        return clock["t"]

    def pull(n):
        if n == 1:
            archive.build_archive(home, "L2")

    fkw = {**kw, "now": tick}
    assert R.run_cycle(home, Refetch(home, during=pull), **fkw) == 0
    with tarfile.open(home / "archives/L2.tar.gz") as tar:
        assert json.loads(tar.extractfile("raw/releases/stale__repo.json").read())["data"]["gen"] == "new"


@pytest.mark.parametrize("failure", ["raises", "exits", "no_done_line"])
def test_a_refresh_that_fails_never_stops_the_cycle(home, kw, logs, failure):
    """MAIN.done, the keyless step and the upload after it never wait on a failed refresh: the records stay as they were."""
    _stale_home(home)
    before = _files(home)
    calls: list[str] = []

    def g(argv):
        calls.append(argv[3])
        if argv[3] in ("releases", "issues_counts"):
            if failure == "raises":
                raise RuntimeError("HTTP 401")
            if failure == "exits":
                raise SystemExit(2)
            print("no summary line")
            return 0
        print("done: 1 clean, 0 with failures")
        return 0

    assert R.run_cycle(home, g, **kw) == 0
    assert calls == ["releases", "issues_counts", KEYLESS]
    assert _ex(home, "MAIN.done") and _ex(home, "LK.complete") and _files(home) == before
    assert _gen(home, "releases", "stale__repo") == "old"


def test_a_copy_that_fails_never_stops_the_cycle_and_nothing_moved(home, kw, logs, monkeypatch):
    _stale_home(home)
    before = _files(home)

    def full(src, dst):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(R.shutil, "copy2", full)
    g = Refetch(home)
    assert R.run_cycle(home, g, **kw) == 0
    assert g.keys == [] and _ex(home, "MAIN.done") and _files(home) == before
    assert any("anchor refresh gave up" in line for line in logs)


def test_the_disk_floor_ends_the_refresh_not_the_cycle(home, kw, logs):
    _stale_home(home)
    before = _files(home)
    g = Refetch(home)
    free = iter([1.0, 1.0, 100.0])                                                # the refresh's two layers, then LK
    fkw = {**kw, "free_gb": lambda _h: next(free)}
    assert R.run_cycle(home, g, **fkw) == 0
    assert g.keys == [] and g.calls == [KEYLESS] and _ex(home, "MAIN.done") and _files(home) == before
    assert not list(home.glob("raw-stale-*-anchor-*"))                            # no copy under the floor either


def test_the_disk_floor_is_read_again_before_each_refresh_run(home, kw, logs):
    _stale_home(home)
    g = Refetch(home, fail=lambda key, n: True)
    free = iter([100.0, 100.0, 1.0, 1.0, 100.0])                                  # L2 start, run 1, run 2; L3c start; LK
    assert R.run_cycle(home, g, **{**kw, "free_gb": lambda _h: next(free)}) == 0
    assert g.keys == [("releases", ["stale__repo"])] and _ex(home, "MAIN.done")


def test_two_refresh_runs_that_agree_end_the_layer_and_failed_records_stay(home, kw):
    _stale_home(home)
    g = Refetch(home, fail=lambda key, n: key == "stale__repo")
    assert R.run_cycle(home, g, **kw) == 0
    assert g.keys[:2] == [("releases", ["stale__repo"]), ("releases", ["stale__repo"])] and len(g.keys) == 3
    assert _gen(home, "releases", "stale__repo") == "old" and _ex(home, "MAIN.done")


def test_a_failure_count_that_keeps_changing_ends_at_max_runs_and_main_done_follows(home, kw):
    _stale_home(home)
    for i in range(6):
        _put(home / f"raw/releases/more{i}__r.json", _env(OLD_ANCHOR))
    counts = {1: 3, 2: 2, 3: 1}                                                  # 3, 2, 1 failures: no two runs agree

    def fail(key, n):
        wanted = counts.get(n, 0)
        seen = fail.seen.setdefault(n, 0)
        fail.seen[n] = seen + 1
        return seen < wanted

    fail.seen = {}
    g = Refetch(home, fail=fail)
    assert R.run_cycle(home, g, max_runs=3, **kw) == 0
    assert [cols for cols, _keys in g.keys].count("releases") == 3 and _ex(home, "MAIN.done")


def test_a_second_run_lists_only_what_is_still_stale_at_the_time_the_layer_was_checked(home, kw):
    """What turns 13 days old during the refresh does not lengthen it: L2's anchors cross the line all the time."""
    _layers_done(home)
    _put(home / "raw/releases/a__r.json", _env(OLD_ANCHOR))
    nearly = datetime.fromtimestamp(NOW - 13 * DAY + 60, timezone.utc).isoformat()
    _put(home / "raw/releases/b__r.json", _env(nearly))
    clock = {"t": NOW}

    def during(n):
        clock["t"] = NOW + 3600.0                                                 # b is past 13 days from now on

    g = Refetch(home, fail=lambda key, n: n == 1, during=during)
    assert R.run_cycle(home, g, **{**kw, "now": lambda: clock["t"]}) == 0
    assert g.keys == [("releases", ["a__r"]), ("releases", ["a__r"])]
    assert _gen(home, "releases", "b__r") == "old"


def test_the_budget_counts_from_the_first_stale_envelope_of_the_cycle_across_restarts(home, kw, logs):
    _stale_home(home)
    stamp = "2026-10-11T12:00:00Z"
    with pytest.raises(_Killed):
        R.run_cycle(home, Refetch(home, during=lambda n: (_ for _ in ()).throw(_Killed())), **kw)
    marker = home / "state/refresh" / R.ANCHOR_REFRESH_MARKER
    assert marker.read_text().strip() == stamp
    later = {**kw, "now": lambda: NOW + R.ANCHOR_REFRESH_BUDGET_S - 60}
    with pytest.raises(_Killed):
        R.run_cycle(home, Refetch(home, during=lambda n: (_ for _ in ()).throw(_Killed())), **later)
    assert marker.read_text().strip() == stamp                                    # a new start does not move it
    spent = {**kw, "now": lambda: NOW + R.ANCHOR_REFRESH_BUDGET_S}
    marked = (home / "state/refresh/L2.complete").read_text()
    g = Refetch(home)
    assert R.run_cycle(home, g, **spent) == 0
    assert g.keys == [] and _ex(home, "MAIN.done") and _gen(home, "releases", "stale__repo") == "old"
    assert (home / "state/refresh/L2.complete").read_text() == marked             # nothing touched: no marker, no archive
    assert any("anchor refresh out of time" in line for line in logs)


def test_the_budget_also_ends_the_runs_of_a_layer_and_sets_the_monitors_deadline(home, kw, logs):
    _stale_home(home)
    state = R.RunState()
    clock = {"t": NOW}
    seen = []

    def during(n):
        seen.append(state.refresh_deadline)
        clock["t"] = NOW + R.ANCHOR_REFRESH_BUDGET_S

    g = Refetch(home, fail=lambda key, n: True, during=during)
    assert R.run_cycle(home, g, state=state, **{**kw, "now": lambda: clock["t"]}) == 0
    assert seen == [NOW + R.ANCHOR_REFRESH_BUDGET_S]                              # one run, with the deadline set
    assert state.refresh_deadline is None and _ex(home, "MAIN.done")


def test_the_refresh_marker_is_retired_with_the_cycle_so_the_next_cycle_has_its_own_budget(home, kw):
    _stale_home(home)
    assert R.run_cycle(home, Refetch(home), **kw) == 0
    assert (home / "state/refresh" / R.ANCHOR_REFRESH_MARKER).is_file()
    R._retire_markers(home, kw["now"], kw["log"])
    assert not (home / "state/refresh" / R.ANCHOR_REFRESH_MARKER).exists()


def test_every_pass_starts_with_a_clean_count_of_zero(home, kw):
    """Q8: the clean count feeds the status estimate (pruned + clean = skipped), so each pass counts its own."""
    state = R.RunState()
    seen = []

    def before(cols, n):
        seen.append((cols, n, state.clean))
        print("o/r [pypi]: SKIP (no cut record)")

    g = Gather(rule=lambda c, n: 1 if n == 1 else 0, before=before)
    assert R.run_layer(home, "L1", PR, g, state=state, **kw) == 0
    assert R.run_repair(home, g, state=state, **kw) == 0
    assert seen == [(PR, 1, 0), (PR, 2, 0), ("repair", 1, 0), ("repair", 2, 0)]
