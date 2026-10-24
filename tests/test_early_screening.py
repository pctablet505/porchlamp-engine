"""Screening before the pull-request layer, and discovery that does not list what is already dead.

The pull-request layer (L1) is the longest of the cycle and runs first, so its screen cannot lean on this cycle's
metadata, commits or pull requests. It uses the 25-per-point fingerprint (archived, disabled, pushed) and the slow
facts stored from the cycle before (license, file tree, mirror); a project that woke up since, or one whose pull
requests were few last time, is still collected.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from porchlamp.collect import fingerprint
from porchlamp.collect.envelope import write_envelope
from porchlamp.collect.fingerprint import RepoFingerprint, classify_prune_reason, run_fingerprint_pass
from scripts import build_universe
from tests.test_universe_pruning_and_blackholes import NOW, _FakeTransport

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("porchlamp_gather_screen", ROOT / "scripts" / "gather.py")
gather = importlib.util.module_from_spec(_spec)
sys.modules["porchlamp_gather_screen"] = gather
_spec.loader.exec_module(gather)

WOKE_PUSH = "2026-10-10T00:00:00Z"
LONG_AGO = "2025-12-01T00:00:00Z"


def _write(raw: Path, collector: str, key: str, data) -> None:
    write_envelope(raw / collector / f"{key}.json", source="test", data=data, fetched_at=NOW.isoformat())


def _repo_object(raw: Path, key: str, **over) -> None:
    data = {
        "archived": False, "mirror_url": None, "fork": False, "license_spdx_id": "MIT", "pull_requests_total": 12,
        "tree_paths_le2": ["pyproject.toml", "LICENSE", "README.md"], "tree_truncated": False,
        "offplatform_paths": [], "maintainers_has_l_lines": False, "readme_off_platform_hits": [],
    }
    _write(raw, "repo_object", key, {**data, **over})


def _fp(name: str, *, pushed: str = WOKE_PUSH, archived: bool = False) -> RepoFingerprint:
    return RepoFingerprint(owner="org", name=name, observed_at=NOW.isoformat(), pushed_at=pushed, is_archived=archived)


def test_a_fresh_screen_does_not_trust_last_cycles_commit_list_or_pull_requests(tmp_path):
    # last cycle the commits were bots' only, and the repository had no pull request at all: but GitHub now says it was
    # pushed days ago, so a project that woke up is not pruned before its pull-request layer
    _write(tmp_path, "commits_180d", "org__woke", [{"author_login": "dependabot[bot]", "author_type": "Bot", "date": "2026-10-01T00:00:00Z"}])
    _repo_object(tmp_path, "org__no-prs", pull_requests_total=0)
    woke, no_prs = _fp("woke"), _fp("no-prs")
    assert classify_prune_reason(woke, raw_dir=tmp_path, owner="org", name="woke", now=NOW) == "stale"
    assert classify_prune_reason(woke, raw_dir=tmp_path, owner="org", name="woke", now=NOW, fresh_only=True) is None
    assert classify_prune_reason(no_prs, raw_dir=tmp_path, owner="org", name="no-prs", now=NOW) == "no_external_workflow"
    assert classify_prune_reason(no_prs, raw_dir=tmp_path, owner="org", name="no-prs", now=NOW, fresh_only=True) is None


def test_a_fresh_screen_still_prunes_what_the_fingerprint_and_the_slow_facts_say(tmp_path):
    _repo_object(tmp_path, "org__unlicensed", license_spdx_id="NOASSERTION", tree_paths_le2=["pyproject.toml", "README.md"])
    _repo_object(tmp_path, "org__list", tree_paths_le2=["README.md"])
    _repo_object(tmp_path, "org__mirror", mirror_url="https://example.org/org/mirror.git")
    cases = {
        "archived": (_fp("archived", archived=True), "archived"),
        "idle": (_fp("idle", pushed=LONG_AGO), "stale"),
        "unlicensed": (_fp("unlicensed"), "unlicensed"),
        "list": (_fp("list"), "non_software"),
        "mirror": (_fp("mirror"), "mirror"),
    }
    for name, (fp, reason) in cases.items():
        assert classify_prune_reason(fp, raw_dir=tmp_path, owner="org", name=name, now=NOW, fresh_only=True) == reason, name


def test_the_fingerprint_pass_passes_the_fresh_only_rule_on(tmp_path):
    _write(tmp_path, "commits_180d", "org__woke", [{"author_login": "dependabot[bot]", "author_type": "Bot", "date": "2026-10-01T00:00:00Z"}])
    node = {
        "pushedAt": WOKE_PUSH, "updatedAt": WOKE_PUSH, "isArchived": False, "isDisabled": False, "stargazerCount": 5000,
        "forkCount": 1, "pullRequests": {"nodes": []}, "issues": {"nodes": []}, "releases": {"nodes": []},
        "defaultBranchRef": {"target": {"oid": "abc"}},
    }
    kwargs = {"state_file": tmp_path / "fp.jsonl", "raw_dir": tmp_path, "transport": _FakeTransport({"r0": node}), "now": NOW}
    assert run_fingerprint_pass([("org", "woke")], prune_ineligible=True, **kwargs).pruned_by_repo == {"org__woke": "stale"}
    assert run_fingerprint_pass([("org", "woke")], prune_ineligible=True, prune_fresh_only=True, **kwargs).pruned_by_repo == {}


def test_a_fresh_screen_does_not_trust_a_stored_push_date_when_the_fingerprint_is_unreadable(tmp_path):
    _write(tmp_path, "github_meta", "org__quiet", {"stars": 5, "forks": 1, "pushed_at": LONG_AGO, "archived": False,
                                                    "created_at": "2020-01-01T00:00:00Z", "license_spdx": "MIT", "default_branch": "main"})
    assert classify_prune_reason(None, raw_dir=tmp_path, owner="org", name="quiet", now=NOW) == "stale"
    assert classify_prune_reason(None, raw_dir=tmp_path, owner="org", name="quiet", now=NOW, fresh_only=True) is None


def test_an_unresolved_repository_is_screened_on_the_slow_facts_alone(tmp_path):
    _write(tmp_path, "commits_180d", "org__woke", [{"author_login": "dependabot[bot]", "author_type": "Bot", "date": "2026-10-01T00:00:00Z"}])
    _repo_object(tmp_path, "org__mirror", mirror_url="https://example.org/org/mirror.git")
    state = tmp_path / "fp.jsonl"
    fingerprint.append_fingerprints(state, [_fp("old", pushed=LONG_AGO, archived=True)])
    kwargs = {"state_file": state, "raw_dir": tmp_path, "transport": _FakeTransport({}), "now": NOW, "prune_ineligible": True}
    repos = [("org", "woke"), ("org", "mirror"), ("org", "old")]
    assert run_fingerprint_pass(repos, **kwargs).pruned_by_repo == {"org__woke": "stale", "org__mirror": "mirror", "org__old": "archived"}
    fresh = run_fingerprint_pass(repos, prune_fresh_only=True, **kwargs)
    assert fresh.pruned_by_repo == {"org__mirror": "mirror"}


def _node(*, archived: bool = False, pushed: str = WOKE_PUSH) -> dict:
    return {
        "pushedAt": pushed, "updatedAt": pushed, "isArchived": archived, "isDisabled": False, "stargazerCount": 5000,
        "forkCount": 1, "pullRequests": {"nodes": []}, "issues": {"nodes": []}, "releases": {"nodes": []},
        "defaultBranchRef": {"target": {"oid": "abc"}},
    }


def _screened_run(tmp_path, monkeypatch, capsys, args):
    """``gather.py --all --collectors pr_window <args>`` over four candidates; returns what was gathered and what it said."""
    raw = tmp_path / "raw"
    monkeypatch.setattr(gather, "DATA", raw)
    cands = tmp_path / "candidates.jsonl"
    names = ("archived", "idle", "woke", "done")
    cands.write_text("".join(json.dumps({"owner": "org", "name": n, "ecosystem": "pypi"}) + "\n" for n in names))
    monkeypatch.setattr(gather, "CANDIDATES", cands)
    _write(raw, "commits_180d", "org__woke", [{"author_login": "dependabot[bot]", "author_type": "Bot", "date": "2026-10-01T00:00:00Z"}])
    _write(raw, "pr_window", "org__done", {"count": 0, "prs": []})
    nodes = {"r0": _node(archived=True), "r1": _node(pushed=LONG_AGO), "r2": _node()}
    asked: list[list[tuple[str, str]]] = []
    real = fingerprint.run_fingerprint_pass

    def asking(repos, **kwargs):
        asked.append(list(repos))
        return real(repos, **{**kwargs, "transport": _FakeTransport(nodes), "now": NOW, "state_file": tmp_path / "fp.jsonl"})

    monkeypatch.setattr(fingerprint, "run_fingerprint_pass", asking)
    seen: list[tuple[str, dict]] = []

    def fake_gather_repo(owner, name, eco, **kwargs):
        seen.append((f"{owner}__{name}", kwargs))
        return {"pr_window": "ok"}

    monkeypatch.setattr(gather, "gather_repo", fake_gather_repo)
    assert gather.main(["--all", "--collectors", "pr_window", *args]) == 0
    return seen, asked, capsys.readouterr().out


def test_screen_ineligible_skips_the_dead_before_the_pr_layer_and_still_gathers_a_project_that_woke_up(tmp_path, monkeypatch, capsys):
    seen, asked, out = _screened_run(tmp_path, monkeypatch, capsys, ["--screen-ineligible"])
    assert [key for key, _ in seen] == ["org__woke", "org__done"], out
    assert "org/archived [pypi]: SKIP (ineligible: archived)" in out
    assert "org/idle [pypi]: SKIP (ineligible: stale)" in out
    assert all(kwargs["force"] is False and kwargs["collectors"] == frozenset({"pr_window"}) for _, kwargs in seen)


def test_screen_ineligible_asks_only_about_projects_whose_layer_is_not_done(tmp_path, monkeypatch, capsys):
    _seen, asked, _out = _screened_run(tmp_path, monkeypatch, capsys, ["--screen-ineligible"])
    assert asked == [[("org", "archived"), ("org", "idle"), ("org", "woke")]]


def test_a_screen_saves_no_fingerprint(tmp_path, monkeypatch, capsys):
    # no --collectors: a full gather would persist the fingerprint of every repo it finished; a screen must not,
    # or the next --dirty run would take a pass it never made for a clean one
    raw = tmp_path / "raw"
    monkeypatch.setattr(gather, "DATA", raw)
    cands = tmp_path / "candidates.jsonl"
    cands.write_text(json.dumps({"owner": "org", "name": "woke", "ecosystem": "pypi"}) + "\n")
    monkeypatch.setattr(gather, "CANDIDATES", cands)
    real = fingerprint.run_fingerprint_pass
    monkeypatch.setattr(fingerprint, "run_fingerprint_pass", lambda repos, **kw: real(
        repos, **{**kw, "transport": _FakeTransport({"r0": _node()}), "now": NOW, "state_file": tmp_path / "fp.jsonl"}))
    monkeypatch.setattr(gather, "gather_repo", lambda owner, name, eco, **kw: {"pr_window": "ok"})
    assert gather.main(["--all", "--screen-ineligible"]) == 0
    assert "org/woke [pypi]: OK" in capsys.readouterr().out
    assert not (tmp_path / "state" / "fingerprints.jsonl").exists()


@pytest.mark.parametrize("other", ["--skip-ineligible", "--dirty", "--fingerprint-only"])
def test_the_screen_refuses_the_flags_it_would_silently_override(other, capsys):
    with pytest.raises(SystemExit) as stop:
        gather.main(["--screen-ineligible", other])
    assert stop.value.code == 2
    assert "--screen-ineligible" in capsys.readouterr().err


def test_without_the_flag_nothing_is_screened(tmp_path, monkeypatch, capsys):
    seen, asked, out = _screened_run(tmp_path, monkeypatch, capsys, [])
    assert [key for key, _ in seen] == ["org__archived", "org__idle", "org__woke", "org__done"]
    assert asked == [] and "SKIP" not in out


# --------------------------------------------------------------------------- #
# Discovery: the filters are on unless asked off                              #
# --------------------------------------------------------------------------- #

def _run_build(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setattr(build_universe, "CANDIDATES", tmp_path / "candidates.jsonl")
    monkeypatch.setattr(build_universe, "DATA", tmp_path)
    queries: list[str] = []

    def fake_search(query, page):
        queries.append(query)
        return []

    monkeypatch.setattr(build_universe, "gh_search", fake_search)
    monkeypatch.setattr(build_universe, "gh_count", lambda q: queries.append(q) or 0)
    monkeypatch.setattr(build_universe, "crates_top", lambda n: [])
    build_universe.build(only="go", dry_run=True, out=tmp_path / "out.jsonl", now=NOW, sleep_s=0, **kwargs)
    return queries


def test_discovery_searches_live_repositories_only_by_default(tmp_path, monkeypatch):
    queries = _run_build(tmp_path, monkeypatch)
    assert queries and all("archived:false fork:false mirror:false pushed:>=2026-04-15" in q for q in queries), queries[:3]


def test_discovery_filters_can_be_turned_off(tmp_path, monkeypatch):
    queries = _run_build(tmp_path, monkeypatch, search_filters=False)
    assert queries and not any("archived:false" in q for q in queries), queries[:3]


def test_discovery_filters_do_not_drop_a_listed_project_that_is_ineligible(tmp_path, monkeypatch):
    existing = tmp_path / "existing.jsonl"
    row = {"owner": "org", "name": "dead", "ecosystem": "go", "selected_by": ["github-stars:language:Go"], "stars_at_selection": 3000,
           "added_at": "2026-09-11T00:00:00+00:00"}
    existing.write_text(json.dumps(row) + "\n")
    _repo_object(tmp_path, "org__dead", archived=True)
    monkeypatch.setattr(build_universe, "CANDIDATES", tmp_path / "candidates.jsonl")
    monkeypatch.setattr(build_universe, "DATA", tmp_path)
    monkeypatch.setattr(build_universe, "gh_search", lambda q, p: [])
    monkeypatch.setattr(build_universe, "gh_count", lambda q: 0)
    monkeypatch.setattr(build_universe, "crates_top", lambda n: [])
    out = tmp_path / "out.jsonl"
    build_universe.build(only="go", dry_run=True, out=out, existing=existing, now=NOW, sleep_s=0)
    assert [json.loads(line)["name"] for line in out.read_text().splitlines()] == ["dead"]


@pytest.mark.parametrize("flag,expected", [([], True), (["--no-search-filters"], False)])
def test_the_command_line_turns_the_discovery_filters_off_with_one_flag(monkeypatch, flag, expected):
    seen = {}
    monkeypatch.setattr(build_universe, "build", lambda *a, **kw: seen.update(kw) or 0)
    monkeypatch.setattr(sys, "argv", ["build_universe.py", *flag])
    assert build_universe.main() == 0
    assert seen["search_filters"] is expected
    assert seen["prune_ineligible"] is False


def test_the_layer_flags_screen_the_pr_layer_and_prune_the_layers_after_it():
    from phone import runner

    assert runner.LAYER_FLAGS["L1"] == ("--screen-ineligible",)
    assert all(runner.LAYER_FLAGS[layer] == ("--skip-ineligible",) for layer in ("L2", "L3g", "L3c", "L4", "LK"))
    assert "L0" not in runner.LAYER_FLAGS, "the metadata layer sees every project: it is how a project that woke up is found"
