"""Tests for scripts/build_universe.py dedup/merge/cap/checkpoint logic."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("build_universe", ROOT / "scripts" / "build_universe.py")
bu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bu)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(bu, "DATA", tmp_path)
    monkeypatch.setattr(bu, "CHECKPOINT", tmp_path / "universe_checkpoint.json")
    monkeypatch.setattr(bu, "CANDIDATES", tmp_path / "candidates.jsonl")
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
