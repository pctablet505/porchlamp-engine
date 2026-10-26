"""Tests for phone/runner.py with stub gatherers: no real scripts/gather.py, no network."""

from __future__ import annotations

import re
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

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


def test_counts_reset_each_run(home, kw):
    state, seen = R.RunState(), []

    def log(line):
        if line.startswith("o/"):                                       # the counts as the app would read them
            seen.append((state.run, state.checked, state.stopped))

    assert R.run_layer(home, "L3g", "governance", _p4_gather, state=state, **{**kw, "log": log}) == 0
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


def test_moving_counts_do_not_complete(home, kw, tmp_path):
    counts = [3, 2, 1, 0, 0]
    g = Gather(lambda c, n: counts[n - 1])
    assert R.run_layer(home, "L1", PR, g, max_runs=4, **kw) == 2
    assert len(g.calls) == 4 and not _ex(home, "L1.complete")
    home2 = tmp_path / "home2"
    home2.mkdir()
    g2 = Gather(lambda c, n: counts[n - 1])
    assert R.run_layer(home2, "L1", PR, g2, **kw) == 0
    assert len(g2.calls) == 5


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
    assert g.calls.count(L2) == 2  # the staged layer reruns to two agreeing runs
    assert g.calls.count(KEYLESS) == 2
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
    assert g.calls == [KEYLESS, KEYLESS]  # only the keyless layer reran
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
    assert g.calls.count(PR) == 2  # L1 reran
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
