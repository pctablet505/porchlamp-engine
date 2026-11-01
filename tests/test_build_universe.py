"""Tests for scripts/build_universe.py star-range slicing, floor, carry, dedup/cap/checkpoint, pacing.

Per D-IN-5 (`docs/v2/03-ANTI-MANIPULATION.md` section 6 `star_burst`),
also covers the star-velocity audit `build()` applies to every
candidate that makes the `--max-total` cut before `candidates.jsonl` is
written -- the universe manifest this module writes.
"""
from __future__ import annotations

import importlib.util
import io
import json
import re
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
    # Slicing probes the search total; one result per query means one slice.
    monkeypatch.setattr(bu, "gh_count", lambda q: 1)
    return tmp_path


def _build(**kw):
    kw.setdefault("min_stars", 0)
    return bu.build(**kw)


def _rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_dedup_merges_selected_by(sandbox, monkeypatch):
    def fake_search(query, page, per_page=100):
        return [{"owner": "Same", "name": "Repo", "stars": 100},
                {"owner": "other", "name": "thing", "stars": 50}]

    monkeypatch.setattr(bu, "gh_search", fake_search)
    monkeypatch.setattr(
        bu, "crates_top",
        lambda limit: [{"owner": "same", "name": "repo", "downloads": 77}])
    _build(max_per_criterion=100, max_total=10000, only="crates", sleep_s=0)
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
    _build(max_per_criterion=100, max_total=3, only="crates", sleep_s=0)
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
    _build(max_per_criterion=100, max_total=100, only="crates", sleep_s=0)
    assert calls == [1]
    _build(max_per_criterion=100, max_total=100, only="crates", sleep_s=0)
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
    _build(max_per_criterion=100, max_total=100, only="crates", sleep_s=0)
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
# A registry list's downloads are never a star count (issue #66, C4).        #
# --------------------------------------------------------------------------- #


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_crates_top_reports_downloads_never_stars(monkeypatch):
    payload = {"crates": [{"repository": "https://github.com/serde-rs/serde", "downloads": 812_345_678},
                          {"repository": "https://github.com/a/b", "downloads": None}]}
    monkeypatch.setattr(bu.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(json.dumps(payload).encode()))
    assert bu.crates_top(10) == [{"owner": "serde-rs", "name": "serde", "downloads": 812_345_678},
                                 {"owner": "a", "name": "b", "downloads": 0}]


def test_registry_only_candidate_records_downloads_and_no_stars(sandbox, monkeypatch):
    monkeypatch.setattr(bu, "gh_search",
                        lambda q, p, per_page=100: [{"owner": "o", "name": "starred", "stars": 5000}])
    monkeypatch.setattr(bu, "crates_top",
                        lambda limit: [{"owner": "dtolnay", "name": "syn", "downloads": 900_000_000}])
    _build(max_per_criterion=100, max_total=100, only="crates", sleep_s=0, now=NOW)
    rows = _rows(sandbox / "candidates.jsonl")
    assert [r["name"] for r in rows] == ["starred", "syn"]          # unknown stars sort last
    syn = rows[1]
    assert syn["stars_at_selection"] is None
    assert syn["downloads_at_selection"] == 900_000_000
    assert syn["selected_by"] == ["crates-downloads"]
    assert rows[0]["stars_at_selection"] == 5000 and "downloads_at_selection" not in rows[0]


def test_registry_candidate_is_not_cut_for_its_star_count(sandbox, monkeypatch):
    """D-UNI-2: the universe is the union of the criteria. The cap orders the
    star-search repos only; the registry list's repo keeps its place."""
    monkeypatch.setattr(bu, "gh_search",
                        lambda q, p, per_page=100: [{"owner": f"o{i}", "name": f"r{i}", "stars": 1000 + i}
                                                    for i in range(1, 6)])
    monkeypatch.setattr(bu, "crates_top",
                        lambda limit: [{"owner": "dtolnay", "name": "syn", "downloads": 900_000_000}])
    _build(max_per_criterion=100, max_total=3, only="crates", sleep_s=0, now=NOW)
    rows = _rows(sandbox / "candidates.jsonl")
    assert [(r["name"], r["stars_at_selection"]) for r in rows] == [
        ("r5", 1005), ("r4", 1004), ("syn", None)]


def test_a_later_star_search_hit_fills_a_registry_rows_stars(sandbox, monkeypatch):
    """crates_top runs inside the crates pass; a repo whose primary language is
    Go is found again by the go pass, and that search reports its stars."""
    def search(q, page, per_page=100):
        return [{"owner": "o", "name": "x", "stars": 7000}] if "language:Go" in q else []

    monkeypatch.setattr(bu, "gh_search", search)
    monkeypatch.setattr(bu, "crates_top", lambda limit: [{"owner": "o", "name": "x", "downloads": 42}])
    _build(max_per_criterion=100, max_total=100, sleep_s=0, now=NOW)
    (row,) = _rows(sandbox / "candidates.jsonl")
    assert row["selected_by"] == ["crates-downloads", "github-stars:language:Go"]
    assert row["stars_at_selection"] == 7000 and row["downloads_at_selection"] == 42
    assert row["ecosystem"] == "crates"                             # the first criterion's ecosystem


def test_carried_row_holding_downloads_as_stars_is_repaired(sandbox, monkeypatch):
    """A row a pre-fix build created from crates_top carries the download count
    as stars; its first criterion says so. A row a star search created is
    untouched even when crates_top also listed it."""
    old = [{"owner": "dtolnay", "name": "syn", "ecosystem": "crates", "selected_by": ["crates-downloads"],
            "stars_at_selection": 1_234_567_890, "added_at": "2026-09-11T00:00:00+00:00"},
           {"owner": "serde-rs", "name": "serde", "ecosystem": "crates",
            "selected_by": ["github-stars:language:Rust", "crates-downloads"],
            "stars_at_selection": 9_000, "added_at": "2026-09-11T00:00:00+00:00"}]
    (sandbox / "candidates.jsonl").write_text("".join(json.dumps(r) + "\n" for r in old))
    monkeypatch.setattr(bu, "gh_search", lambda q, p, per_page=100: [])
    monkeypatch.setattr(bu, "crates_top", lambda limit: [])
    _build(max_per_criterion=100, max_total=100, only="crates", sleep_s=0, now=NOW)
    by = {r["name"]: r for r in _rows(sandbox / "candidates.jsonl")}
    assert by["syn"]["stars_at_selection"] is None
    assert by["syn"]["downloads_at_selection"] == 1_234_567_890
    assert by["serde"]["stars_at_selection"] == 9_000 and "downloads_at_selection" not in by["serde"]
    assert [r["name"] for r in _rows(sandbox / "candidates.jsonl")] == ["serde", "syn"]


def test_repair_registry_stars_is_idempotent_and_narrow():
    row = {"selected_by": ["crates-downloads"], "stars_at_selection": 10}
    once = bu.repair_registry_stars(row)
    assert once == {"selected_by": ["crates-downloads"], "stars_at_selection": None,
                    "downloads_at_selection": 10}
    assert bu.repair_registry_stars(once) == once
    filled = dict(once, stars_at_selection=7000)                   # a later search filled the stars
    assert bu.repair_registry_stars(filled) == filled
    for keep in ({"selected_by": ["github-stars:language:Rust", "crates-downloads"], "stars_at_selection": 10},
                 {"selected_by": ["crates-downloads"], "stars_at_selection": True},
                 {"selected_by": [], "stars_at_selection": 10}):
        assert bu.repair_registry_stars(keep) == keep


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
    _build(max_per_criterion=100, max_total=100, only="crates", sleep_s=0, now=NOW)
    assert len(calls) == 5  # one call per candidate that made the cut


def test_candidates_carry_star_velocity_fields(sandbox, monkeypatch):
    monkeypatch.setattr(bu, "crates_top", lambda limit: [])
    monkeypatch.setattr(
        bu, "gh_search",
        lambda q, p, per_page=100: [{"owner": "acme", "name": "widget", "stars": 10}])
    _build(max_per_criterion=100, max_total=100, only="crates", sleep_s=0, now=NOW)
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
    _build(max_per_criterion=100, max_total=100, only="crates", sleep_s=0, now=NOW)
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
    _build(max_per_criterion=100, max_total=100, only="crates", sleep_s=0, now=NOW)
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
    out = sandbox / "dry" / "out.jsonl"
    _build(max_per_criterion=100, max_total=100, only="crates", sleep_s=0, now=NOW, dry_run=True, out=out)
    assert calls == []
    assert not (sandbox / "candidates.jsonl").is_file()
    assert not (sandbox / "universe_checkpoint.json").is_file()
    assert out.is_file() and (out.parent / "out.jsonl.summary.json").is_file()
    assert "star_velocity_flag" not in _rows(out)[0]
    with pytest.raises(ValueError):
        _build(max_per_criterion=100, only="crates", sleep_s=0, dry_run=True)


def test_star_velocity_step_makes_no_live_network_call(sandbox, monkeypatch, block_live_network):
    """The suite-wide network guard: zero real socket/subprocess attempts
    across the whole build() call, star-velocity step included."""
    monkeypatch.setattr(bu, "crates_top", lambda limit: [])
    monkeypatch.setattr(
        bu, "gh_search",
        lambda q, p, per_page=100: [{"owner": "acme", "name": "widget", "stars": 10}])
    _build(max_per_criterion=100, max_total=100, only="crates", sleep_s=0, now=NOW)
    rows = [json.loads(line) for line in (sandbox / "candidates.jsonl").read_text().splitlines()]
    assert rows[0]["measured"] is True


# --------------------------------------------------------------------------- #
# Star-range slicing, floor, carry, resume, cap, pacing (fake GitHub).       #
# --------------------------------------------------------------------------- #


class FakeGH:
    """A search API over an in-memory repo list that enforces GitHub's cap: it
    returns at most 1,000 results per query and reports the true total_count."""

    def __init__(self, repos, clock=None):
        self.repos = repos  # (language, owner, name, stars)
        self.clock = clock
        self.search_calls = []
        self.count_calls = []

    def _match(self, q):
        found = re.search(r'language:(?:"([^"]+)"|(\S+))', q)
        lang = found.group(1) or found.group(2)          # language:"Vim Script" or language:Go
        m = re.search(r"stars:>=(\d+)", q)
        lo, hi = (int(m.group(1)), 10**9) if m else map(int, re.search(r"stars:(\d+)\.\.(\d+)", q).groups())
        return sorted((r for r in self.repos if r[0] == lang and lo <= r[3] <= hi), key=lambda r: -r[3])

    def count(self, q):
        self.count_calls.append((q, self.clock() if self.clock else None))
        return len(self._match(q))

    def search(self, q, page, per_page=100):
        self.search_calls.append((q, page, self.clock() if self.clock else None))
        hits = self._match(q)[:1000][(page - 1) * per_page: page * per_page]
        return [{"owner": o, "name": n, "stars": st} for _, o, n, st in hits]


def _install(monkeypatch, gh):
    monkeypatch.setattr(bu, "gh_count", gh.count)
    monkeypatch.setattr(bu, "gh_search", gh.search)
    monkeypatch.setattr(bu, "crates_top", lambda limit: [])


def _go(n, lo=1000):
    return [("Go", f"o{i}", f"r{i}", lo + i) for i in range(n)]


def test_plan_slices_splits_a_slice_that_reaches_the_cap():
    at_cap = FakeGH([("Go", f"o{i}", "r", i + 1) for i in range(1000)])
    slices = bu.plan_slices("language:Go", 1, 1000, at_cap.count)
    assert len(slices) > 1  # exactly 1,000 would be truncated, so it splits
    assert all(n < 1000 for _, _, n in slices)
    assert sum(n for _, _, n in slices) == 1000
    assert slices[0][0] == 1 and slices[-1][1] == 1000
    under = FakeGH([("Go", f"o{i}", "r", i + 1) for i in range(999)])
    assert bu.plan_slices("language:Go", 1, 1000, under.count) == [(1, 1000, 999)]


def test_slices_cover_every_repo_beyond_the_per_query_cap(sandbox, monkeypatch):
    gh = FakeGH(_go(3000))  # distinct stars 1000..3999: any gap between slices loses a repo
    _install(monkeypatch, gh)
    _build(max_total=100000, only="go", sleep_s=0, min_stars=1000)
    rows = _rows(sandbox / "candidates.jsonl")
    assert len(rows) == 3000  # one unsliced query would stop at 1,000
    assert {r["stars_at_selection"] for r in rows} == set(range(1000, 4000))
    assert all(len(gh._match(q)) < 1000 for q, _, _ in gh.search_calls)  # no slice hit the cap


def test_floor_is_respected_and_inclusive(sandbox, monkeypatch):
    gh = FakeGH([("Go", "a", "below", 999), ("Go", "b", "at", 1000), ("Go", "c", "above", 1500)])
    _install(monkeypatch, gh)
    leaky = gh.search
    monkeypatch.setattr(bu, "gh_search", lambda q, p, per_page=100: leaky(q, p, per_page)
                        + [{"owner": "x", "name": "leak", "stars": 5}])  # ignores the range
    _build(max_total=100, only="go", sleep_s=0, min_stars=1000)
    assert {r["name"] for r in _rows(sandbox / "candidates.jsonl")} == {"at", "above"}


def test_effective_floor_rises_to_fit_max_total():
    gh = FakeGH(_go(2000))
    qs = ["language:Go"]
    assert bu.effective_floor(qs, 1000, 5000, gh.count) == 1000  # fewer than max_total: keep the floor
    f = bu.effective_floor(qs, 1000, 500, gh.count)
    assert gh.count(f"language:Go stars:>={f}") >= 500
    assert gh.count(f"language:Go stars:>={f + f // 50 + 1}") < 500  # within about 1%


def test_every_current_repo_is_carried_even_below_floor_and_cap(sandbox, monkeypatch):
    old = [{"owner": "o49", "name": "r49", "ecosystem": "go", "selected_by": ["github-stars:language:Go"],
            "stars_at_selection": 1049, "added_at": "2026-09-23T01:00:00+00:00"},
           {"owner": "gone", "name": "low", "ecosystem": "go", "selected_by": ["github-stars:language:Go"],
            "stars_at_selection": 300, "added_at": "2026-09-23T01:00:00+00:00"},
           {"owner": "gone", "name": "mid", "ecosystem": "maven", "selected_by": ["github-stars:language:Java"],
            "stars_at_selection": 5000, "added_at": "2026-09-23T01:00:00+00:00"}]
    (sandbox / "candidates.jsonl").write_text("".join(json.dumps(r) + "\n" for r in old))
    _install(monkeypatch, FakeGH(_go(50)))
    _build(max_total=5, only="go", sleep_s=0, min_stars=1000)
    by = {(r["owner"], r["name"]): r for r in _rows(sandbox / "candidates.jsonl")}
    assert {("gone", "low"), ("gone", "mid"), ("o49", "r49")} <= set(by)  # none dropped by floor or cap
    assert "carried:2026-09-23" in by[("gone", "low")]["selected_by"]
    assert "carried:2026-09-23" in by[("gone", "mid")]["selected_by"]
    assert not any(c.startswith("carried:") for c in by[("o49", "r49")]["selected_by"])  # re-qualified
    assert len(by) == 5  # carried count toward --max-total; the rest filled by stars


def test_dedup_across_languages_merges_selected_by(sandbox, monkeypatch):
    gh = FakeGH([("JavaScript", "Same", "Repo", 5000), ("TypeScript", "same", "repo", 5000),
                 ("TypeScript", "t", "only", 4000)])
    _install(monkeypatch, gh)
    _build(max_total=100, only="npm", sleep_s=0, min_stars=1000)
    rows = _rows(sandbox / "candidates.jsonl")
    assert len(rows) == 2
    both = next(r for r in rows if r["name"].lower() == "repo")
    assert set(both["selected_by"]) == {"github-stars:language:JavaScript", "github-stars:language:TypeScript"}


def test_resume_replays_checkpointed_pages_without_refetching(sandbox, monkeypatch):
    gh = FakeGH(_go(250))  # 3 pages
    _install(monkeypatch, gh)

    def dies_on_page_2(q, page, per_page=100):
        if page == 2:
            raise SystemExit("killed")
        return gh.search(q, page, per_page)

    monkeypatch.setattr(bu, "gh_search", dies_on_page_2)
    with pytest.raises(SystemExit):
        _build(max_total=1000, only="go", sleep_s=0, min_stars=1000)
    assert not (sandbox / "candidates.jsonl").is_file()
    gh.search_calls.clear()
    monkeypatch.setattr(bu, "gh_search", gh.search)
    _build(max_total=1000, only="go", sleep_s=0, min_stars=1000)
    assert [p for _, p, _ in gh.search_calls] == [2, 3]  # page 1 came from the checkpoint
    assert len(_rows(sandbox / "candidates.jsonl")) == 250  # ... and its items were replayed


def test_max_total_keeps_the_highest_stars(sandbox, monkeypatch):
    _install(monkeypatch, FakeGH(_go(250)))
    _build(max_total=100, only="go", sleep_s=0, min_stars=1000)
    stars = [r["stars_at_selection"] for r in _rows(sandbox / "candidates.jsonl")]
    assert stars == sorted(range(1150, 1250), reverse=True)


class _FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def test_pacer_never_exceeds_30_per_minute_with_a_fake_clock():
    clk = _FakeClock()
    pacer = bu.Pacer(2.0, 30, clock=clk, sleep=clk.sleep)
    for _ in range(95):
        pacer.wait()
    s = pacer.stamps
    assert all(b - a >= 2.0 for a, b in zip(s, s[1:]))
    assert all(s[i + 30] - s[i] >= 60.0 for i in range(len(s) - 30))  # 31 calls never fit in 60 s
    assert clk.t > 0


def test_build_search_calls_are_paced_to_30_per_minute(sandbox, monkeypatch):
    clk = _FakeClock()
    monkeypatch.setattr(bu, "_CLOCK", clk)
    monkeypatch.setattr(bu, "_SLEEP", clk.sleep)
    langs = ["Python", "JavaScript", "TypeScript", "Rust", "Go", "Java", "C", "C++"]
    gh = FakeGH([(lang, f"{lang}{i}", "r", 2000 + i) for lang in langs for i in range(150)], clock=clk)
    _install(monkeypatch, gh)
    n = bu.build(max_total=100000, sleep_s=2.0, min_stars=1000, now=NOW)
    assert n == 150 * 8
    stamps = sorted([t for _, t in gh.count_calls] + [t for _, _, t in gh.search_calls])
    assert len(stamps) > 30  # more calls than one minute allows
    assert all(b - a >= 2.0 for a, b in zip(stamps, stamps[1:]))
    assert all(stamps[i + 30] - stamps[i] >= 60.0 for i in range(len(stamps) - 30))


def test_rate_limit_error_backs_off_and_retries(sandbox, monkeypatch):
    clk = _FakeClock()
    monkeypatch.setattr(bu, "_CLOCK", clk)
    monkeypatch.setattr(bu, "_SLEEP", clk.sleep)
    gh = FakeGH(_go(5))
    _install(monkeypatch, gh)
    hits = []

    def flaky(q, page, per_page=100):
        if not hits:
            hits.append(1)
            raise RuntimeError("gh search failed rc=1: API rate limit exceeded")
        return gh.search(q, page, per_page)

    monkeypatch.setattr(bu, "gh_search", flaky)
    _build(max_total=100, only="go", sleep_s=2.0, min_stars=1000)
    assert 60 in clk.sleeps
    assert len(_rows(sandbox / "candidates.jsonl")) == 5


def test_dry_run_writes_only_out_with_summary_and_carries(sandbox, monkeypatch):
    old = {"owner": "gone", "name": "low", "ecosystem": "go", "selected_by": ["old"],
           "stars_at_selection": 300, "added_at": "2026-09-23T01:00:00+00:00"}
    existing = sandbox / "current.jsonl"
    existing.write_text(json.dumps(old) + "\n")
    _install(monkeypatch, FakeGH(_go(30)))
    out = sandbox / "dry.jsonl"
    n = bu.build(max_total=100, only="go", sleep_s=0, min_stars=1000, dry_run=True, out=out, existing=existing)
    assert n == 31
    summ = json.loads((sandbox / "dry.jsonl.summary.json").read_text())
    assert summ["total"] == 31 and summ["existing_carried_total"] == 1 and summ["new_repos"] == 30
    assert summ["per_language"] == {"Go": 30} and summ["min_stars_reached"] == 1000
    assert not (sandbox / "candidates.jsonl").is_file()


# --------------------------------------------------------------------------- #
# RFC-0023: languages by modeled ecosystem, one star floor (issue #66, C4).  #
# --------------------------------------------------------------------------- #


def test_search_criteria_admit_languages_only_with_a_modeled_ecosystem():
    """Kotlin, Scala and Groovy publish to Maven Central (maven); Zig, Vim Script
    and LLVM have no package registry (systems, D-MISS-5). Languages whose
    registry is not modeled (C#, Ruby, PHP, Dart, Swift, Lua, Clojure) stay out."""
    assert bu.SEARCH_CRITERIA == {
        "pypi": ["language:Python"],
        "npm": ["language:JavaScript", "language:TypeScript"],
        "crates": ["language:Rust"],
        "go": ["language:Go"],
        "maven": ["language:Java", "language:Kotlin", "language:Scala", "language:Groovy"],
        "systems": ["language:C", "language:C++", "language:Zig", 'language:"Vim Script"', "language:LLVM"],
    }


def test_added_languages_enter_with_their_ecosystem_and_criterion(sandbox, monkeypatch):
    """The four repos the published 2026-10-21 universe lacked for their
    language (stars as of 2026-10-22) enter under the ecosystem their language
    maps to, each recording the criterion that selected it (D-UNI-2)."""
    gh = FakeGH([("Scala", "apache", "spark", 44270), ("Vim Script", "neovim", "neovim", 103113),
                 ("LLVM", "llvm", "llvm-project", 41159), ("Kotlin", "JetBrains", "kotlin", 53498)])
    _install(monkeypatch, gh)
    out = sandbox / "dry.jsonl"
    assert bu.build(max_total=100, sleep_s=0, min_stars=1000, dry_run=True, out=out) == 4
    by = {f"{r['owner']}/{r['name']}": r for r in _rows(out)}
    assert {k: (r["ecosystem"], r["selected_by"]) for k, r in by.items()} == {
        "neovim/neovim": ("systems", ['github-stars:language:"Vim Script"']),
        "JetBrains/kotlin": ("maven", ["github-stars:language:Kotlin"]),
        "apache/spark": ("maven", ["github-stars:language:Scala"]),
        "llvm/llvm-project": ("systems", ["github-stars:language:LLVM"]),
    }
    summ = json.loads((sandbox / "dry.jsonl.summary.json").read_text())
    assert summ["per_language"] == {"Vim Script": 1, "Kotlin": 1, "Scala": 1, "LLVM": 1}
    assert summ["per_ecosystem"] == {"systems": 2, "maven": 2}


def test_added_languages_face_the_same_star_floor_and_cap(sandbox, monkeypatch):
    """One floor and one cap for every language: a Kotlin repo competes with a
    Java repo on stars alone."""
    gh = FakeGH([("Java", "j", "low", 1500), ("Java", "j", "high", 9000), ("Kotlin", "k", "mid", 5000),
                 ("Zig", "z", "below", 999)])
    _install(monkeypatch, gh)
    _build(max_total=2, sleep_s=0, min_stars=1000, now=NOW)
    assert [(r["name"], r["ecosystem"]) for r in _rows(sandbox / "candidates.jsonl")] == [
        ("high", "maven"), ("mid", "maven")]


def _carried(owner, name, ecosystem="go"):
    return {"owner": owner, "name": name, "ecosystem": ecosystem, "selected_by": ["old"],
            "stars_at_selection": 5000, "added_at": "2026-09-23T01:00:00+00:00"}


def test_a_carried_row_that_is_not_one_path_segment_is_dropped_and_nothing_is_written_outside_the_data_root(
    sandbox, monkeypatch, capsys
):
    # build_universe's DATA is a module constant: point it at a nested root, so an escape through `..` still lands in tmp_path
    root = sandbox / "root"
    data = root / "data" / "raw"
    data.mkdir(parents=True)
    monkeypatch.setattr(bu, "DATA", data)
    monkeypatch.setattr(bu, "CHECKPOINT", data / "universe_checkpoint.json")
    monkeypatch.setattr(bu, "CANDIDATES", data / "candidates.jsonl")
    existing = sandbox / "current.jsonl"
    existing.write_text("".join(json.dumps(r) + "\n" for r in [
        _carried("../../../esc", "x"), _carried("ok", "../../../e2"), _carried("ok", "fine", "../../e"),
        _carried("981011512", "--")]))
    _install(monkeypatch, FakeGH(_go(2)))
    before = {str(p.relative_to(root)) for p in root.rglob("*")}
    bu.build(max_total=100, only="go", sleep_s=0, min_stars=1000, existing=existing)
    created = {str(p.relative_to(root)) for p in root.rglob("*")} - before
    assert all(name == "data" or name.startswith("data/") for name in created), sorted(created)
    owners = {r["owner"] for r in _rows(data / "candidates.jsonl")}
    assert owners == {"981011512", "o0", "o1"}
    assert "dropped 3 carried candidates" in capsys.readouterr().out
