"""Re-collecting the records an older collector cut short, and only those.

Envelopes written when the commit list stopped at 1,000, the author history and the pull-request window at 500 are
marked ``truncated``, and the ranking refuses them. The current collectors read the whole window, so one fetch of
exactly those collectors clears the cut; a record cut at today's cap is genuinely too busy and is left alone.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from porchlamp.collect import commits, github_graphql
from porchlamp.collect.cut_records import CUT_COLLECTORS, legacy_cut_collectors
from porchlamp.collect.envelope import write_envelope

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("porchlamp_gather_recut", ROOT / "scripts" / "gather.py")
gather = importlib.util.module_from_spec(_spec)
sys.modules["porchlamp_gather_recut"] = gather
_spec.loader.exec_module(gather)

AT = "2026-10-20T00:00:00+00:00"


def _write(raw: Path, collector: str, key: str, data: dict) -> None:
    write_envelope(raw / collector / f"{key}.json", source="test", data=data, fetched_at=AT)


def _cut(raw: Path, key: str, collector: str, *, cap: int | None, truncated: bool = True) -> None:
    data: dict = {"count": 3, "truncated": truncated, "rows": [], "prs": []}
    if cap is not None:
        data["cap"] = cap
    _write(raw, collector, key, data)


LEGACY_CAPS = {"commits_365d": 1000, "author_history": 500, "pr_window": 500}
CURRENT_CAPS = {
    "commits_365d": commits.COMMIT_CAP,
    "author_history": github_graphql.AUTHOR_WINDOW_CAP,
    "pr_window": github_graphql.PR_CAP,
}


def test_the_collectors_checked_are_the_three_whose_caps_were_raised():
    assert set(CUT_COLLECTORS) == {"commits_365d", "author_history", "pr_window"}
    assert all(LEGACY_CAPS[c] < CURRENT_CAPS[c] for c in CUT_COLLECTORS)


@pytest.mark.parametrize("collector", sorted(LEGACY_CAPS))
def test_a_record_cut_at_an_older_smaller_cap_is_a_legacy_cut(tmp_path, collector):
    _cut(tmp_path, "org__a", collector, cap=LEGACY_CAPS[collector])
    assert legacy_cut_collectors(tmp_path, "org", "a") == frozenset({collector})


@pytest.mark.parametrize("collector", sorted(LEGACY_CAPS))
def test_a_record_cut_at_todays_cap_is_left_alone(tmp_path, collector):
    _cut(tmp_path, "org__a", collector, cap=CURRENT_CAPS[collector])
    assert legacy_cut_collectors(tmp_path, "org", "a") == frozenset()


@pytest.mark.parametrize("collector", sorted(LEGACY_CAPS))
def test_a_complete_record_is_left_alone(tmp_path, collector):
    _cut(tmp_path, "org__a", collector, cap=LEGACY_CAPS[collector], truncated=False)
    assert legacy_cut_collectors(tmp_path, "org", "a") == frozenset()


def test_a_cut_record_that_names_no_cap_predates_the_cap_and_counts_as_legacy(tmp_path):
    _cut(tmp_path, "org__a", "commits_365d", cap=None)
    assert legacy_cut_collectors(tmp_path, "org", "a") == frozenset({"commits_365d"})


def test_several_cut_collectors_of_one_repo_are_all_named(tmp_path):
    _cut(tmp_path, "org__a", "author_history", cap=500)
    _cut(tmp_path, "org__a", "pr_window", cap=500)
    _cut(tmp_path, "org__a", "commits_365d", cap=CURRENT_CAPS["commits_365d"])
    assert legacy_cut_collectors(tmp_path, "org", "a") == frozenset({"author_history", "pr_window"})


def test_missing_and_unreadable_envelopes_are_not_cut_records(tmp_path):
    (tmp_path / "commits_365d").mkdir()
    (tmp_path / "commits_365d" / "org__broken.json").write_text("{not json")
    (tmp_path / "pr_window").mkdir()
    (tmp_path / "pr_window" / "org__list.json").write_text(json.dumps([1, 2]))
    assert legacy_cut_collectors(tmp_path, "org", "broken") == frozenset()
    assert legacy_cut_collectors(tmp_path, "org", "list") == frozenset()
    assert legacy_cut_collectors(tmp_path, "org", "never-collected") == frozenset()


def test_what_the_current_commit_collector_writes_is_never_a_legacy_cut(tmp_path):
    # tie the rule to the collector's own output: a window it truncated records today's cap
    _write(tmp_path, "commits_365d", "org__a", commits._window_data([], "2025-10-20", total=None, truncated=True))
    assert legacy_cut_collectors(tmp_path, "org", "a") == frozenset()


# --------------------------------------------------------------------------- #
# gather --refetch-cut                                                        #
# --------------------------------------------------------------------------- #

def _run(tmp_path, monkeypatch, capsys, args):
    raw = tmp_path / "raw"
    monkeypatch.setattr(gather, "DATA", raw)
    cands = tmp_path / "candidates.jsonl"
    names = ("one", "two", "clean", "busy")
    cands.write_text("".join(json.dumps({"owner": "org", "name": n, "ecosystem": "pypi"}) + "\n" for n in names))
    monkeypatch.setattr(gather, "CANDIDATES", cands)
    _cut(raw, "org__one", "commits_365d", cap=1000)
    _cut(raw, "org__two", "author_history", cap=500)
    _cut(raw, "org__two", "pr_window", cap=500)
    _cut(raw, "org__clean", "commits_365d", cap=1000, truncated=False)
    _cut(raw, "org__busy", "commits_365d", cap=CURRENT_CAPS["commits_365d"])
    seen: dict[str, dict] = {}

    def fake_gather_repo(owner, name, eco, **kwargs):
        seen[name] = kwargs
        return {c: "ok" for c in kwargs["collectors"]}

    monkeypatch.setattr(gather, "gather_repo", fake_gather_repo)
    assert gather.main(["--all", *args]) == 0
    return seen, capsys.readouterr().out


def test_refetch_cut_fetches_only_the_cut_collectors_of_only_the_cut_repos_and_forces_them(tmp_path, monkeypatch, capsys):
    seen, out = _run(tmp_path, monkeypatch, capsys, ["--refetch-cut"])
    assert set(seen) == {"one", "two"}, out
    assert seen["one"]["collectors"] == frozenset({"commits_365d"}) and seen["one"]["force"] is True
    assert seen["two"]["collectors"] == frozenset({"author_history", "pr_window"}) and seen["two"]["force"] is True
    assert "org/clean [pypi]: SKIP (no cut record)" in out
    assert "org/busy [pypi]: SKIP (no cut record)" in out


def test_refetch_cut_honours_a_collector_selection(tmp_path, monkeypatch, capsys):
    seen, _out = _run(tmp_path, monkeypatch, capsys, ["--refetch-cut", "--collectors", "pr_window"])
    assert set(seen) == {"two"} and seen["two"]["collectors"] == frozenset({"pr_window"})


def test_without_the_flag_nothing_is_refetched(tmp_path, monkeypatch, capsys):
    seen, _out = _run(tmp_path, monkeypatch, capsys, ["--collectors", "commits_365d"])
    assert seen["one"]["force"] is False  # the ordinary layer pass: an existing envelope is "done", cut or not


@pytest.mark.parametrize("other", ["--force", "--dirty", "--fingerprint-only", "--screen-ineligible"])
def test_refetch_cut_refuses_the_flags_that_would_widen_or_replace_it(other, capsys):
    with pytest.raises(SystemExit) as stop:
        gather.main(["--refetch-cut", other])
    assert stop.value.code == 2
    assert "--refetch-cut" in capsys.readouterr().err
