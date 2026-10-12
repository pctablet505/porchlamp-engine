"""run_forever for the app: the idle line, the owner's layers and interval, the step shown, the events, the list check."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from phone import runner as R
import contextlib

NOW = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc).timestamp()
MAIN_DONE = "2026-10-11T04:04:06Z"
DUE = datetime(2026, 11, 8, 4, 4, 6, tzinfo=timezone.utc).timestamp()          # 28 days after MAIN.done
KEYLESS = "depsdev,scorecard,downloads"


class Stop(BaseException):
    pass


def raise_stop(_s):
    raise Stop


@pytest.fixture(autouse=True)
def no_pagesize(monkeypatch):
    monkeypatch.setattr(R, "apply_pagesize", lambda: None)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


def mark(home: Path, name: str, text: str = MAIN_DONE) -> None:
    path = home / "state" / "refresh" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


class Gather:
    def __init__(self, state: R.RunState | None = None, fails=lambda cols, n: 0):
        self.calls: list[str] = []
        self.seen_state: list[tuple] = []
        self.state, self.fails, self.counts = state, fails, {}

    def __call__(self, argv):
        cols = argv[3]
        n = self.counts[cols] = self.counts.get(cols, 0) + 1
        self.calls.append(cols)
        if self.state is not None:
            self.seen_state.append((self.state.layer, self.state.collectors, self.state.run))
        print(f"done: 3 clean, {self.fails(cols, n)} with failures")
        return 0


def run(home, gather, **kw):
    logs: list[str] = []
    kw.setdefault("sleep", raise_stop)
    with contextlib.suppress(Stop):
        R.run_forever(home, gather, now=lambda: NOW, log=logs.append, poll_s=5, **kw)
    return logs


def test_idle_says_when_the_next_cycle_is_due_instead_of_sleeping_silently(home):
    mark(home, "MAIN.done")
    state, told = R.RunState(), []
    logs = run(home, Gather(), state=state, notify=lambda name, **info: told.append((name, info)))
    assert "Idle, next cycle due 2026-11-08" in logs
    assert state.mode == "idle" and state.idle_next_due == DUE and state.layer is None
    assert told == [("idle", {"next_due": DUE})]


def test_the_idle_line_repeats_hourly_not_on_every_poll(home):
    mark(home, "MAIN.done")
    clock = [NOW]
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        clock[0] += 600 if len(sleeps) < 12 else 0
        if len(sleeps) >= 14:
            raise Stop

    logs: list[str] = []
    with contextlib.suppress(Stop):
        R.run_forever(home, Gather(), now=lambda: clock[0], log=logs.append, poll_s=600, sleep=sleep)
    idle = [line for line in logs if line.startswith("Idle, next cycle due")]
    assert len(sleeps) == 14 and 2 <= len(idle) <= 3        # about one line an hour over two hours, not one per poll


def test_the_owners_interval_is_read_afresh_on_every_pass(home):
    mark(home, "MAIN.done", "2026-10-02T10:00:00Z")          # ten days old
    days = [28]
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        if len(sleeps) == 1:
            days[0] = 7                                       # the owner shortens the interval while it idles
        else:
            raise Stop

    g = Gather()
    logs: list[str] = []
    with contextlib.suppress(Stop):
        R.run_forever(home, g, now=lambda: NOW, log=logs.append, poll_s=5, sleep=sleep,
                      every_days=lambda: days[0], layers=lambda: ["LK"])
    assert any(line.startswith("Idle, next cycle due 2026-10-30") for line in logs)       # 28 days: idle
    assert g.calls == [KEYLESS, KEYLESS]                                                  # 7 days: a new cycle ran
    assert any("starting a new cycle" in line for line in logs)


def test_only_the_selected_layers_run_and_the_cycle_still_completes(home):
    g = Gather()
    logs = run(home, g, layers=lambda: ["L0", "LK"])
    assert g.calls == ["github_meta,pr_events,pr_reviews,contributors,actions_runs,tree,commits_180d"] * 2 + [
        KEYLESS] * 2
    names = {p.name for p in (home / "state" / "refresh").iterdir()}
    assert {"L0.complete", "LK.complete", "MAIN.done"} <= names
    assert not any(n in names for n in ("L1.complete", "L2.complete", "L3g.complete", "L4.complete", "cycle.open"))
    assert "L1: not selected -- skipping" in logs and "L4: not selected -- skipping" in logs


def test_a_layer_deselected_mid_cycle_is_skipped_at_the_next_boundary(home):
    chosen = [["L1", "L0", "LK"]]

    class Switching(Gather):
        def __call__(self, argv):
            out = super().__call__(argv)
            if argv[3] == "pr_window" and self.counts["pr_window"] == 2:
                chosen[0] = ["L1", "LK"]                       # L1 just completed: now drop L0
            return out

    g = Switching()
    run(home, g, layers=lambda: chosen[0])
    assert "github_meta,pr_events,pr_reviews,contributors,actions_runs,tree,commits_180d" not in g.calls
    assert g.calls.count("pr_window") == 2 and g.calls.count(KEYLESS) == 2


def test_the_state_shows_the_step_its_collectors_and_the_pass_while_the_gatherer_runs(home):
    state = R.RunState()
    g = Gather(state)
    run(home, g, state=state, layers=lambda: ["L1", "LK"])
    assert g.seen_state == [("L1", ("pr_window",), 1), ("L1", ("pr_window",), 2),
                           ("LK", ("depsdev", "scorecard", "downloads"), 1),
                           ("LK", ("depsdev", "scorecard", "downloads"), 2)]
    assert state.failures == 0 and state.last_rc == 0 and state.finished_at == NOW
    assert state.cycle_started == NOW


def test_the_notifications_follow_the_cycle_in_order(home):
    told: list[str] = []
    run(home, Gather(), layers=lambda: ["L1", "LK"], notify=lambda name, **info: told.append(
        name + (":" + info["layer"] if "layer" in info else "")))
    assert told == ["cycle_start", "step_start:L1", "step_done:L1", "step_start:LK", "step_done:LK", "cycle_done",
                    "idle"]


def test_a_failed_cycle_counts_the_failures_in_a_row_and_tells_so(home):
    state, told, sleeps = R.RunState(), [], []

    class Broken:
        def __call__(self, argv):
            print("no summary line")
            return 0

    def sleep(s):
        sleeps.append(s)
        if len(sleeps) >= 3:
            raise Stop

    logs: list[str] = []
    with contextlib.suppress(Stop):
        R.run_forever(home, Broken(), now=lambda: NOW, log=logs.append, poll_s=5, sleep=sleep, layers=lambda: ["L1"],
                      state=state, notify=lambda name, **info: told.append((name, info.get("rc"))))
    assert state.failures == 3 and state.last_rc == 4 and state.last_failure == "a step stopped with code 4"
    assert told.count(("cycle_fail", 4)) == 3 and state.finished_at is None


def test_the_cycle_waits_for_the_repo_list_but_an_idle_runner_never_asks(home):
    answers = iter([False, False, True])
    asked, sleeps = [], []

    def ready():
        asked.append(1)
        return next(answers)

    state, told = R.RunState(), []
    g = Gather(state)
    logs: list[str] = []

    def sleep(s):
        sleeps.append(state.mode)
        if len(sleeps) > 6:
            raise Stop

    with contextlib.suppress(Stop):
        R.run_forever(home, g, now=lambda: NOW, log=logs.append, poll_s=5, sleep=sleep, layers=lambda: ["LK"],
                      state=state, ready=ready, notify=lambda name, **info: told.append(name))
    assert sleeps[:2] == ["no_candidates", "no_candidates"]
    assert logs.count("run_forever: waiting for the repo list") == 1 and told.count("no_candidates") == 1
    assert g.calls == [KEYLESS, KEYLESS]                           # once the list came, the cycle ran
    asked_before_idle = len(asked)
    assert asked_before_idle == 3                                   # asked at each wait and once more, never while idle
