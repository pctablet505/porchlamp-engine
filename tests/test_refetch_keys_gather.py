"""gather --refetch-keys, and the phone's stale-anchor refresh on the real gather and the real collectors.

Only the HTTP transports are replaced: what is read and written on disk is what the phone does (Q4, owner's ruling of
2026-11-02). A failed fetch leaves the old record byte for byte, and no record ever leaves ``raw/``.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from phone import runner as R
from porchlamp.collect import github_graphql, pike_signals
from porchlamp.collect.envelope import write_envelope

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("porchlamp_gather_refetch", ROOT / "scripts" / "gather.py")
gather = importlib.util.module_from_spec(_spec)
sys.modules["porchlamp_gather_refetch"] = gather
_spec.loader.exec_module(gather)

KEYS = ("org__old", "org__young", "org__gone")


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    raw = home / "raw"
    raw.mkdir(parents=True)
    (home / "state/refresh").mkdir(parents=True)
    monkeypatch.setattr(gather, "DATA", raw)
    monkeypatch.setattr(gather, "CANDIDATES", raw / "candidates.jsonl")
    names = [key.split("__")[1] for key in KEYS]
    (raw / "candidates.jsonl").write_text(
        "".join(json.dumps({"owner": "org", "name": n, "ecosystem": "pypi"}) + "\n" for n in names))
    for collector in ("releases", "issues_counts"):
        for key, age in (("org__old", 20.0), ("org__young", 1.0), ("org__gone", 20.0)):
            write_envelope(raw / collector / f"{key}.json", source="test",
                           data={"window_anchor": _iso(age), "gen": "old"}, fetched_at=_iso(age))
    for layer in ("L2", "L3c"):
        (home / "state/refresh" / f"{layer}.complete").write_text("2026-01-01T00:00:00Z\n")
    return home


class Rest:
    """``GhRestTransport``: answers one empty page of releases and of tags; a repo named in ``down`` raises."""

    down: frozenset[str] = frozenset()
    calls: list[str] = []

    def get(self, path, **params):
        Rest.calls.append(path)
        if any(f"/{name}/" in path for name in Rest.down):
            raise RuntimeError("gh: HTTP 502")
        return []


class Graphql:
    """``GhCliTransport``: a complete answer; a repo named in ``Rest.down`` raises."""

    def execute(self, query, variables):
        if variables["name"] in Rest.down:
            raise RuntimeError("gh: HTTP 502")
        return {"data": {"closed90d": {"issueCount": 7},
                         "repository": {"updated90d": {"totalCount": 9},
                                        "sample": {"totalCount": 0, "nodes": []}}}}


@pytest.fixture(autouse=True)
def transports(monkeypatch):
    Rest.down, Rest.calls = frozenset(), []
    monkeypatch.setattr(pike_signals, "GhRestTransport", Rest)
    monkeypatch.setattr(github_graphql, "GhCliTransport", Graphql)


def _data(home, collector, key):
    return json.loads((home / "raw" / collector / f"{key}.json").read_text())["data"]


def _refresh(home, logs=None):
    R.refresh_stale_anchors(home, gather.main, free_gb=lambda _h: 100.0, now=time.time,
                            log=(logs.append if logs is not None else (lambda _line: None)))


@pytest.mark.parametrize("collector", ["releases", "issues_counts"])
def test_refetch_keys_replaces_the_listed_envelopes_in_place_and_skips_the_others(home, capsys, collector):
    assert gather.main(["--all", "--collectors", collector, "--refetch-keys", "org__old"]) == 0
    out = capsys.readouterr().out
    assert "org/old [pypi]: OK" in out and "org/young [pypi]: SKIP (clean fingerprint)" in out
    assert "done: 3 clean, 0 with failures" in out
    assert "gen" not in _data(home, collector, "org__old")                        # the collector's own new envelope
    assert _data(home, collector, "org__young")["gen"] == _data(home, collector, "org__gone")["gen"] == "old"


def test_a_failed_refetch_leaves_the_old_envelope_byte_for_byte(home, capsys):
    Rest.down = frozenset({"old"})
    path = home / "raw/releases/org__old.json"
    before = path.read_bytes()
    assert gather.main(["--all", "--collectors", "releases", "--refetch-keys", "org__old"]) == 0
    out = capsys.readouterr().out
    assert "org/old [pypi]: FAIL releases" in out and "done: 2 clean, 1 with failures" in out
    assert path.read_bytes() == before
    assert not list((home / "raw/releases").glob("*.tmp-*"))


@pytest.mark.parametrize("args", [["--collectors", "releases"], ["--all"]])
def test_refetch_keys_needs_all_and_collectors(args, capsys):
    with pytest.raises(SystemExit) as stop:
        gather.main([*args, "--refetch-keys", "org__old"])
    assert stop.value.code == 2 and "--refetch-keys" in capsys.readouterr().err


@pytest.mark.parametrize("other", ["--force", "--dirty", "--fingerprint-only", "--screen-ineligible",
                                   "--skip-ineligible", "--refetch-cut", "--repair-replies"])
def test_refetch_keys_refuses_the_flags_that_would_widen_or_replace_it(other, capsys):
    with pytest.raises(SystemExit) as stop:
        gather.main(["--all", "--collectors", "releases", "--refetch-keys", "org__old", other])
    assert stop.value.code == 2 and "--refetch-keys" in capsys.readouterr().err


def test_the_refresh_on_the_real_gather_refetches_the_stale_records_and_keeps_the_failed_one(home, capsys):
    """The runner's arguments against gather's parser, gather's lines against the runner's: a mismatch would be swallowed by
    the rule that a refresh never stops the cycle, and the refresh would silently do nothing."""
    Rest.down = frozenset({"gone"})
    logs: list[str] = []
    gone = home / "raw/releases/org__gone.json"
    gone_before = gone.read_bytes()
    _refresh(home, logs)
    assert "gen" not in _data(home, "releases", "org__old")                       # refreshed in place
    assert "gen" not in _data(home, "issues_counts", "org__old")
    assert _data(home, "releases", "org__young")["gen"] == "old"                  # young: not listed, not read
    assert gone.read_bytes() == gone_before                                       # failed twice: the record stays
    assert sum("anchor refresh run" in line and "summary" in line and line.startswith("L2") for line in logs) == 2
    assert _data(home, "issues_counts", "org__gone")["gen"] == "old"
    assert (home / "state/refresh/L2.complete").read_text() != "2026-01-01T00:00:00Z\n"
