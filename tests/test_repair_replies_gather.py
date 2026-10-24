"""``gather --repair-replies``: read again the PRs a stored pr_window record cut short, and nothing else (RFC-0022)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from porchlamp.collect import github_graphql
from porchlamp.collect.envelope import read_envelope
from tests.collect.test_repair_pr_window_replies import LEGACY, RepairWindow, _store
from tests.collect.test_pr_window import _threads

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("porchlamp_gather_repair", ROOT / "scripts" / "gather.py")
gather = importlib.util.module_from_spec(_spec)
sys.modules["porchlamp_gather_repair"] = gather
_spec.loader.exec_module(gather)


def _candidates(tmp_path, monkeypatch, names):
    raw = tmp_path / "raw"
    monkeypatch.setattr(gather, "DATA", raw)
    cands = tmp_path / "candidates.jsonl"
    cands.write_text("".join(json.dumps({"owner": "org", "name": n, "ecosystem": "pypi"}) + "\n" for n in names))
    monkeypatch.setattr(gather, "CANDIDATES", cands)

    def no_ordinary_pass(*args, **kwargs):
        raise AssertionError("--repair-replies must not run the ordinary collectors")

    monkeypatch.setattr(gather, "gather_repo", no_ordinary_pass)
    return raw


def test_every_repo_is_visited_and_the_run_says_what_it_did(tmp_path, monkeypatch, capsys):
    raw = _candidates(tmp_path, monkeypatch, ("cut", "whole", "broken"))
    seen = []

    def fake(raw_dir, owner, name, now=None, transport=None):
        seen.append((raw_dir, owner, name))
        if name == "broken":
            raise RuntimeError("pr replies batch failed for org/broken PRs: [2]")
        return "ok" if name == "cut" else "skip"

    monkeypatch.setattr(github_graphql, "repair_pr_window_replies", fake)
    assert gather.main(["--all", "--repair-replies"]) == 0
    out = capsys.readouterr().out
    assert seen == [(raw, "org", n) for n in ("cut", "whole", "broken")]
    assert "collectors: all; repos: 3" in out
    assert "org/cut [pypi]: OK\n" in out
    assert "org/whole [pypi]: SKIP (no cut record)\n" in out
    assert "org/broken [pypi]: FAIL pr replies batch failed for org/broken PRs: [2]\n" in out
    assert out.rstrip().endswith("done: 2 clean, 1 with failures")


def test_a_failure_does_not_stop_the_repos_after_it(tmp_path, monkeypatch, capsys):
    _candidates(tmp_path, monkeypatch, ("a", "b", "c"))
    seen = []

    def fake(raw_dir, owner, name, now=None, transport=None):
        seen.append(name)
        if name == "a":
            raise RuntimeError("HTTP 502")
        return "skip"

    monkeypatch.setattr(github_graphql, "repair_pr_window_replies", fake)
    gather.main(["--all", "--repair-replies"])
    assert seen == ["a", "b", "c"] and "done: 2 clean, 1 with failures" in capsys.readouterr().out


def test_end_to_end_a_stored_legacy_record_is_repaired_and_the_next_run_finds_nothing(tmp_path, monkeypatch, capsys):
    raw = _candidates(tmp_path, monkeypatch, ("widget", "absent"))
    nodes = _threads()
    _store(raw, nodes)
    (raw / "pr_window" / "acme__widget.json").rename(raw / "pr_window" / "org__widget.json")
    window = RepairWindow(nodes)
    monkeypatch.setattr(github_graphql, "GhCliTransport", lambda: window)

    assert gather.main(["--all", "--repair-replies"]) == 0
    first = capsys.readouterr().out
    assert "org/widget [pypi]: OK\n" in first and "org/absent [pypi]: SKIP (no cut record)\n" in first
    assert read_envelope(raw / "pr_window" / "org__widget.json").data["query_sha256"] == github_graphql.PR_WINDOW_QUERY_SHA256

    calls = len(window.calls)
    assert gather.main(["--all", "--repair-replies"]) == 0
    assert "org/widget [pypi]: SKIP (no cut record)\n" in capsys.readouterr().out
    assert len(window.calls) == calls


def test_a_record_of_another_query_is_not_this_flags_to_touch(tmp_path, monkeypatch, capsys):
    raw = _candidates(tmp_path, monkeypatch, ("widget",))
    _store(raw, _threads(), query="0" * 64)
    (raw / "pr_window" / "acme__widget.json").rename(raw / "pr_window" / "org__widget.json")
    window = RepairWindow(_threads())
    monkeypatch.setattr(github_graphql, "GhCliTransport", lambda: window)
    gather.main(["--all", "--repair-replies"])
    assert "org/widget [pypi]: SKIP (no cut record)" in capsys.readouterr().out and window.calls == []
    assert read_envelope(raw / "pr_window" / "org__widget.json").data["query_sha256"] == "0" * 64 != LEGACY


@pytest.mark.parametrize("other", [["--force"], ["--dirty"], ["--fingerprint-only"], ["--screen-ineligible"],
                                   ["--skip-ineligible"], ["--refetch-cut"], ["--collectors", "pr_window"],
                                   ["--priority-keys", "keys.txt"]])
def test_repair_replies_refuses_the_flags_that_would_widen_or_replace_it(other, capsys):
    with pytest.raises(SystemExit) as stop:
        gather.main(["--repair-replies", *other])
    assert stop.value.code == 2
    assert "--repair-replies" in capsys.readouterr().err
