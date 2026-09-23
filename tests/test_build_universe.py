"""Tests for scripts/build_universe.py dedup/merge/cap/checkpoint logic.

Per D-IN-5 (`docs/v2/03-ANTI-MANIPULATION.md` section 6 `star_burst`),
also covers the star-velocity audit `build()` applies to every
candidate that makes the `--max-total` cut before `candidates.jsonl` is
written -- the universe manifest this module writes.
"""
from __future__ import annotations

import importlib.util
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import porchlamp.collect.universe as universe_module

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("build_universe", ROOT / "scripts" / "build_universe.py")
bu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bu)

NOW = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)


def _clean_star_fetch(owner: str, name: str) -> dict:
    """No burst, no throwaways -- every candidate audits to False."""
    return {"events": [], "repo_forks_delta_90d": 0}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(bu, "DATA", tmp_path)
    monkeypatch.setattr(bu, "CHECKPOINT", tmp_path / "universe_checkpoint.json")
    monkeypatch.setattr(bu, "CANDIDATES", tmp_path / "candidates.jsonl")
    # Most tests in this file assert nothing about the star-velocity fields;
    # give them a working, network-free default transport so build() does not
    # raise NotImplementedError from
    # porchlamp.collect.universe.default_star_history_transport.
    monkeypatch.setattr(bu, "STAR_HISTORY_TRANSPORT", _clean_star_fetch)
    return tmp_path


def test_dedup_merges_selected_by(sandbox, monkeypatch):
    def fake_search(query, page, per_page=100):
        return [{"owner": "Same", "name": "Repo", "stars": 100},
                {"owner": "other", "name": "thing", "stars": 50}]

    monkeypatch.setattr(bu, "gh_search", fake_search)
    monkeypatch.setattr(
        bu, "crates_top",
        lambda limit: [{"owner": "same", "name": "repo", "stars": 77}])
    bu.build(max_per_criterion=100, max_total=10000, only="crates", sleep_s=0)
    rows = [json.loads(line) for line in (sandbox / "candidates.jsonl").read_text().splitlines()]
    same = [r for r in rows if r["name"] == "Repo"]
    assert len(same) == 1  # deduped across criteria (case-insensitive)
    crits = same[0]["selected_by"]
    assert any(c.startswith("github-stars:") for c in crits)
    assert "crates-downloads" in crits  # merged from the registry criterion


def test_cap_and_sort_by_stars(sandbox, monkeypatch):
    monkeypatch.setattr(bu, "crates_top", lambda limit: [])
    monkeypatch.setattr(
        bu, "gh_search",
        lambda q, p, per_page=100: [{"owner": f"o{i}", "name": f"r{i}", "stars": i}
                                    for i in range(1, 6)])
    bu.build(max_per_criterion=100, max_total=3, only="crates", sleep_s=0)
    rows = [json.loads(line) for line in (sandbox / "candidates.jsonl").read_text().splitlines()]
    assert len(rows) == 3
    assert [r["stars_at_selection"] for r in rows] == [5, 4, 3]  # sorted desc


def test_checkpoint_skips_done_pages(sandbox, monkeypatch):
    calls = []
    monkeypatch.setattr(bu, "crates_top", lambda limit: [])

    def fake(q, p, per_page=100):
        calls.append(p)
        return []

    monkeypatch.setattr(bu, "gh_search", fake)
    bu.build(max_per_criterion=100, max_total=100, only="crates", sleep_s=0)
    assert calls == [1]
    bu.build(max_per_criterion=100, max_total=100, only="crates", sleep_s=0)
    assert calls == [1]  # second run: page 1 already checkpointed


def test_resume_merges_existing_candidates(sandbox, monkeypatch):
    (sandbox / "candidates.jsonl").write_text(json.dumps(
        {"owner": "keep", "name": "me", "ecosystem": "crates",
         "selected_by": ["old"], "stars_at_selection": 999,
         "added_at": "x"}) + "\n")
    monkeypatch.setattr(bu, "crates_top", lambda limit: [])
    monkeypatch.setattr(
        bu, "gh_search",
        lambda q, p, per_page=100: [{"owner": "new", "name": "one", "stars": 1}])
    bu.build(max_per_criterion=100, max_total=100, only="crates", sleep_s=0)
    rows = [json.loads(line) for line in (sandbox / "candidates.jsonl").read_text().splitlines()]
    assert {r["name"] for r in rows} == {"me", "one"}


def test_crates_top_strips_git_suffix(monkeypatch):
    # crates.io repository URLs sometimes end in ".git"; the GitHub API 404s on
    # that name, so crates_top must strip it (github.com/fizyk20/generic-array.git).
    payload = {"crates": [
        {"repository": "https://github.com/fizyk20/generic-array.git", "downloads": 10},
        {"repository": "https://github.com/foo/bar", "downloads": 5},
        {"repository": "https://github.com/baz/qux/", "downloads": 1},
    ]}

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(bu.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(json.dumps(payload).encode()))
    hits = bu.crates_top(10)
    assert [h["name"] for h in hits] == ["generic-array", "bar", "qux"]


# --------------------------------------------------------------------------- #
# The star-velocity filter is applied to every candidate at entry.          #
# --------------------------------------------------------------------------- #


def test_star_velocity_filter_called_once_per_candidate(sandbox, monkeypatch):
    """Patches where the audit is actually invoked
    (``porchlamp.collect.universe.audit_star_velocity``, not
    ``porchlamp.starscout.audit_star_velocity`` -- ``porchlamp.collect.universe``
    imports the name directly, so patching the defining module would not
    affect the call site) and asserts it ran exactly once per candidate
    that made the final cut.
    """
    calls = []
    real_audit = universe_module.audit_star_velocity

    def counting_audit(events, forks_delta, now):
        calls.append((events, forks_delta, now))
        return real_audit(events, forks_delta, now)

    monkeypatch.setattr(universe_module, "audit_star_velocity", counting_audit)
    monkeypatch.setattr(
        bu, "gh_search",
        lambda q, p, per_page=100: [{"owner": f"o{i}", "name": f"r{i}", "stars": i}
                                    for i in range(1, 6)])
    monkeypatch.setattr(bu, "crates_top", lambda limit: [])
    bu.build(max_per_criterion=100, max_total=100, only="crates", sleep_s=0, now=NOW)
    assert len(calls) == 5  # one call per candidate that made the cut


def test_candidates_carry_star_velocity_fields(sandbox, monkeypatch):
    monkeypatch.setattr(bu, "crates_top", lambda limit: [])
    monkeypatch.setattr(
        bu, "gh_search",
        lambda q, p, per_page=100: [{"owner": "acme", "name": "widget", "stars": 10}])
    bu.build(max_per_criterion=100, max_total=100, only="crates", sleep_s=0, now=NOW)
    rows = [json.loads(line) for line in (sandbox / "candidates.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["measured"] is True
    assert row["star_velocity_flag"] is False
    assert row["stars_audited"] == 0
    assert row["star_velocity"] == {"recent_stars": 0, "window_days": 90}
    assert row["provenance"]["path"].endswith("star_history/acme__widget.json")
    assert (sandbox / "star_history" / "acme__widget.json").is_file()
    assert (sandbox / "universe_entry" / "acme__widget.json").is_file()


def test_flagged_lockstep_candidate_carries_true(sandbox, monkeypatch):
    def burst_fetch(owner, name):
        events = [{"user_login": f"bot_{i}", "starred_at": NOW.isoformat(),
                    "user_total_platform_events": 1} for i in range(200)]
        events += [{"user_login": f"real_{i}", "starred_at": NOW.isoformat(),
                     "user_total_platform_events": 100} for i in range(334)]
        return {"events": events, "repo_forks_delta_90d": 1}

    monkeypatch.setattr(bu, "STAR_HISTORY_TRANSPORT", burst_fetch)
    monkeypatch.setattr(bu, "crates_top", lambda limit: [])
    monkeypatch.setattr(
        bu, "gh_search",
        lambda q, p, per_page=100: [{"owner": "acme", "name": "burst-repo", "stars": 10}])
    bu.build(max_per_criterion=100, max_total=100, only="crates", sleep_s=0, now=NOW)
    rows = [json.loads(line) for line in (sandbox / "candidates.jsonl").read_text().splitlines()]
    assert rows[0]["star_velocity_flag"] is True
    assert rows[0]["stars_audited"] == 100


def test_failed_star_history_fetch_never_silently_false(sandbox, monkeypatch):
    def failing_fetch(owner, name):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(bu, "STAR_HISTORY_TRANSPORT", failing_fetch)
    monkeypatch.setattr(bu, "crates_top", lambda limit: [])
    monkeypatch.setattr(
        bu, "gh_search",
        lambda q, p, per_page=100: [{"owner": "acme", "name": "unreachable", "stars": 10}])
    bu.build(max_per_criterion=100, max_total=100, only="crates", sleep_s=0, now=NOW)
    rows = [json.loads(line) for line in (sandbox / "candidates.jsonl").read_text().splitlines()]
    row = rows[0]
    assert row["measured"] is False
    assert row["reason"] == "collector_failure"
    assert "star_velocity_flag" not in row
    assert "stars_audited" not in row
    assert "star_velocity" not in row


def test_dry_run_skips_the_star_velocity_audit_entirely(sandbox, monkeypatch):
    calls = []
    monkeypatch.setattr(bu, "STAR_HISTORY_TRANSPORT", lambda o, n: calls.append((o, n)) or {"events": [], "repo_forks_delta_90d": 0})
    monkeypatch.setattr(bu, "crates_top", lambda limit: [])
    monkeypatch.setattr(
        bu, "gh_search",
        lambda q, p, per_page=100: [{"owner": "acme", "name": "widget", "stars": 10}])
    bu.build(max_per_criterion=100, max_total=100, only="crates", sleep_s=0, now=NOW, dry_run=True)
    assert calls == []
    assert not (sandbox / "candidates.jsonl").is_file()


def test_star_velocity_step_makes_no_live_network_call(sandbox, monkeypatch, block_live_network):
    """The suite-wide network guard: zero real socket/subprocess attempts
    across the whole build() call, star-velocity step included."""
    monkeypatch.setattr(bu, "crates_top", lambda limit: [])
    monkeypatch.setattr(
        bu, "gh_search",
        lambda q, p, per_page=100: [{"owner": "acme", "name": "widget", "stars": 10}])
    bu.build(max_per_criterion=100, max_total=100, only="crates", sleep_s=0, now=NOW)
    rows = [json.loads(line) for line in (sandbox / "candidates.jsonl").read_text().splitlines()]
    assert rows[0]["measured"] is True
