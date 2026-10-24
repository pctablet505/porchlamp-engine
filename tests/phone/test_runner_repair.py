"""The repair after step 1: a cycle that was open when the engine changed reads again what the old query cut short.

An envelope on disk counts as done, so a pull-request step finished by the engine before RFC-0022 holds records whose
reply lists stopped at 10 reviews and 20 comments. ``gather --repair-replies`` reads those PRs again by number; the
runner runs it once per cycle, right after step 1 and before any step that reads the step's records.
"""
from __future__ import annotations

import tarfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from phone import runner as R

NOW = datetime(2026, 10, 25, 3, 0, tzinfo=timezone.utc).timestamp()
STAMP = "2026-10-25T03:00:00Z"
OLD = "2026-10-24T18:51:45Z"
REPAIR_ARGV = ["--all", "--repair-replies"]
L0 = "github_meta,pr_events,pr_reviews,contributors,actions_runs,tree,commits_180d"


@pytest.fixture
def home(tmp_path: Path) -> Path:
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


@pytest.fixture
def kw():
    logs: list[str] = []
    return {"free_gb": lambda _h: 100.0, "now": lambda: NOW, "log": logs.append, "logs": logs}


def _kw(kw):
    return {k: v for k, v in kw.items() if k != "logs"}


def _mark(home: Path, name: str, text: str = OLD) -> None:
    path = home / "state/refresh" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


def _text(home: Path, name: str) -> str | None:
    path = home / "state/refresh" / name
    return path.read_text().strip() if path.is_file() else None


class Repair:
    """A stub gather.main for the repair run: ``runs`` is one ``(repaired repos, failed repos)`` per run."""

    def __init__(self, runs=((0, 0),), before=None):
        self.runs, self.before, self.calls = list(runs), before, []

    def __call__(self, argv):
        self.calls.append(list(argv))
        n = len(self.calls)
        if self.before:
            self.before(n)
        repaired, failed = self.runs[min(n, len(self.runs)) - 1]
        print("collectors: all; repos: 5")
        for i in range(repaired):
            print(f"org/fixed{i} [pypi]: OK")
        for i in range(failed):
            print(f"org/broken{i} [pypi]: FAIL boom")
        print("org/whole [pypi]: SKIP (no cut record)")
        print(f"done: {5 - failed} clean, {failed} with failures")
        return 0


def test_the_repair_runs_gather_with_the_repair_flag_and_nothing_else(home, kw):
    g = Repair()
    assert R.run_repair(home, g, **_kw(kw)) == 0
    assert g.calls == [REPAIR_ARGV]


def test_a_clean_run_completes_at_once_and_writes_the_marker(home, kw):
    g = Repair(runs=[(0, 0)])
    _mark(home, "L1.complete")
    assert R.run_repair(home, g, **_kw(kw)) == 0
    assert len(g.calls) == 1 and _text(home, "L1.repaired") == STAMP
    assert _text(home, "L1.complete") == OLD                    # nothing changed: the step's archive is still current


def test_a_run_that_repaired_records_writes_the_step_complete_again_and_builds_the_archive_again(home, kw):
    record = home / "raw/pr_window/org__a.json"
    record.parent.mkdir(parents=True)
    record.write_text('{"repaired": false}')
    _mark(home, "L1.complete")

    def rewrite(n):
        record.write_text('{"repaired": true}')

    assert R.run_repair(home, Repair(runs=[(2, 0)], before=rewrite), **_kw(kw)) == 0
    assert _text(home, "L1.complete") == STAMP
    meta = (home / "archives/L1.meta.json").read_text()
    assert f'"completed_at": "{STAMP}"' in meta and '"files": 1' in meta
    with tarfile.open(home / "archives/L1.tar.gz") as tar:
        assert tar.extractfile("raw/pr_window/org__a.json").read() == b'{"repaired": true}'


def test_failures_are_tried_again_until_two_runs_agree(home, kw):
    g = Repair(runs=[(3, 3), (1, 1), (0, 1)])
    assert R.run_repair(home, g, **_kw(kw)) == 0
    assert len(g.calls) == 3 and _text(home, "L1.repaired") == STAMP


def test_failures_that_do_not_change_end_the_repair_without_failing_the_cycle(home, kw):
    g = Repair(runs=[(0, 2), (0, 2)])
    assert R.run_repair(home, g, **_kw(kw)) == 0
    assert len(g.calls) == 2


def test_a_repair_that_never_settles_stops_at_max_runs_and_leaves_no_marker(home, kw):
    g = Repair(runs=[(0, 5), (0, 4), (0, 3), (0, 2)])
    assert R.run_repair(home, g, max_runs=3, **_kw(kw)) == 2
    assert _text(home, "L1.repaired") is None and len(g.calls) == 3


def test_an_existing_marker_skips_the_repair(home, kw):
    _mark(home, "L1.repaired")
    g = Repair()
    assert R.run_repair(home, g, **_kw(kw)) == 0
    assert g.calls == [] and "already repaired" in " ".join(kw["logs"])


@pytest.mark.parametrize("make, rc", [
    (lambda argv: (_ for _ in ()).throw(RuntimeError("gather blew up")), 3),
    (lambda argv: (_ for _ in ()).throw(SystemExit(2)), 3),
    (lambda argv: print("nothing useful") or 0, 4),
])
def test_a_repair_that_raises_or_prints_no_summary_stops_the_cycle_and_leaves_no_marker(home, kw, make, rc):
    assert R.run_repair(home, make, **_kw(kw)) == rc
    assert _text(home, "L1.repaired") is None


def test_low_disk_stops_the_repair_before_it_starts(home, kw):
    g = Repair()
    kw["free_gb"] = lambda _h: 0.5
    assert R.run_repair(home, g, min_free_gb=2.0, **_kw(kw)) == 6
    assert g.calls == [] and _text(home, "L1.repaired") is None


def test_the_state_names_step_one_and_counts_the_repair_run(home, kw):
    state, seen = R.RunState(), []

    def look(n):
        seen.append((state.layer, state.collectors, state.run, state.max_runs))

    g = Repair(runs=[(2, 0)], before=look)
    R.run_repair(home, g, state=state, max_runs=9, **_kw(kw))
    assert seen == [("L1", ("pr_window",), 1, 9)]
    assert (state.checked, state.failed, state.total) == (3, 0, 5)           # two repaired and one whole of five


def test_the_notifications_say_when_the_repair_starts_and_what_it_read_again(home, kw):
    told: list[tuple] = []
    R.run_repair(home, Repair(runs=[(2, 0)]), notify=lambda name, **info: told.append((name, info)), **_kw(kw))
    assert told == [("repair_start", {"layer": "L1"}), ("repair_done", {"layer": "L1", "repaired": 2})]


def test_a_skip_for_no_cut_record_counts_as_a_project_checked():
    state = R.RunState()
    R.count_line(state, "org/whole [pypi]: SKIP (no cut record)")
    assert (state.checked, state.pruned, state.failed) == (1, 0, 0)


def test_the_repair_marker_is_retired_with_the_cycles_other_markers(home):
    for name in ("L1.complete", "L1.repaired", "MAIN.done"):
        _mark(home, name)
    R._retire_markers(home, lambda: NOW, lambda line: None)
    assert _text(home, "L1.repaired") is None
    assert (home / "state/refresh-prev/L1.repaired").read_text() == OLD + "\n"


# --------------------------------------------------------------------------- #
# where the cycle runs it                                                     #
# --------------------------------------------------------------------------- #

class Cycle:
    """A stub gather.main for a whole cycle: every layer clean, the repair call recorded as ``repair``."""

    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, argv):
        self.calls.append("repair" if "--repair-replies" in argv else argv[3])
        print("done: 1 clean, 0 with failures")
        return 0


@pytest.fixture(autouse=True)
def no_pagesize(monkeypatch):
    monkeypatch.setattr(R, "apply_pagesize", lambda: None)


def test_a_cycle_open_before_the_repair_existed_repairs_before_it_goes_on_with_step_two(home, kw):
    # the phone on 2026-10-25: step 1 finished by the old engine, step 2 begun, the app then updated and restarted
    _mark(home, "L1.staged")
    _mark(home, "L1.complete")
    _mark(home, "L0.staged")
    g = Cycle()
    assert R.run_cycle(home, g, **_kw(kw)) == 0
    assert g.calls[:3] == ["repair", L0, L0]
    assert "pr_window" not in g.calls                            # step 1 itself is not collected again
    assert _text(home, "L1.repaired") == STAMP


def test_a_cycle_that_repaired_does_not_repair_again_after_a_restart(home, kw):
    _mark(home, "L1.complete")
    _mark(home, "L1.repaired")
    g = Cycle()
    R.run_cycle(home, g, **_kw(kw))
    assert "repair" not in g.calls


def test_no_repair_when_step_one_is_not_selected(home, kw):
    g = Cycle()
    R.run_cycle(home, g, layers=lambda: ["L0"], **_kw(kw))
    assert "repair" not in g.calls and g.calls[0] == L0


def test_a_failed_step_one_is_not_followed_by_a_repair(home, kw):
    calls: list[list[str]] = []

    def broken(argv):
        calls.append(argv)
        print("no summary line")
        return 0

    assert R.run_cycle(home, broken, **_kw(kw)) == 4
    assert all("--repair-replies" not in argv for argv in calls) and _text(home, "L1.repaired") is None


def test_a_failing_repair_stops_the_cycle_before_step_two(home, kw):
    _mark(home, "L1.complete")

    def run(argv):
        if "--repair-replies" in argv:
            raise RuntimeError("gather blew up")
        pytest.fail("a step ran after a failed repair")

    assert R.run_cycle(home, run, **_kw(kw)) == 3
    assert any("cycle stopped at L1 repair with rc 3" in line for line in kw["logs"])


def test_the_repairs_own_output_is_logged_line_by_line_like_any_steps(home, kw):
    R.run_repair(home, Repair(runs=[(1, 0)]), **_kw(kw))
    assert "org/fixed0 [pypi]: OK" in kw["logs"] and "done: 5 clean, 0 with failures" in kw["logs"]
