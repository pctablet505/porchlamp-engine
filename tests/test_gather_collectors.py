"""``scripts/gather.py --collectors``: breadth-first collection.

A layer-by-layer (BFS) sweep fetches ONE collector for every candidate before
the next collector for any of them. ``gather_repo(..., collectors=...)``
restricts a call to the named collectors; everything else is reported
``"skip"`` and no transport is touched for it. ``None`` keeps the original
fetch-everything behaviour.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def gather(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("PORCHLAMP_DATA_ROOT", str(tmp_path))
    spec = importlib.util.spec_from_file_location("porchlamp_gather_collectors", ROOT / "scripts/gather.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["porchlamp_gather_collectors"] = module
    spec.loader.exec_module(module)
    return module


def _forbid_transports(gather: ModuleType, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every transport gather.py can reach records its collector name and
    raises; a selected-only run must never reach an unselected one."""
    touched: list[str] = []

    def _gh(path: str, *flags: str):
        touched.append(f"gh:{path}")
        raise AssertionError(f"unexpected REST call {path}")

    def _run(cmd, **kwargs):
        touched.append("subprocess:" + " ".join(cmd[:3]))
        raise AssertionError(f"unexpected subprocess {cmd[:3]}")

    def _get_json(url: str, headers=None):
        touched.append(f"http:{url}")
        raise AssertionError(f"unexpected HTTP GET {url}")

    monkeypatch.setattr(gather, "_gh", _gh)
    monkeypatch.setattr(gather.subprocess, "run", _run)
    monkeypatch.setattr(gather, "_get_json", _get_json)
    monkeypatch.setattr(gather.time, "sleep", lambda s: None)
    return touched


def test_only_the_selected_collector_runs(gather: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    touched = _forbid_transports(gather, monkeypatch)
    # github_meta exists already (so `branch` resolves from the cache, as in a
    # real layer-1 run over the cached layer-0 corpus).
    gather._env("github_meta", "org__alpha", "github-graphql", {"default_branch": "main"})
    calls: list[tuple[str, str]] = []
    import porchlamp.collect.github_graphql as prw

    def fake_pr_window(raw_dir: Path, owner: str, name: str, *a, **k) -> str:
        calls.append((owner, name))
        return "ok"

    monkeypatch.setattr(prw, "collect_pr_window", fake_pr_window)

    status = gather.gather_repo("org", "alpha", "pypi", collectors=frozenset({"pr_window"}))

    assert calls == [("org", "alpha")]
    assert status["pr_window"] == "ok"
    assert touched == [], touched
    others = {k: v for k, v in status.items() if k != "pr_window"}
    assert others and set(others.values()) == {"skip"}, others


def test_none_means_everything_as_before(gather: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """``collectors=None`` must still try the first (GraphQL) block -- the
    original fetch-everything contract is unchanged."""
    touched = _forbid_transports(gather, monkeypatch)
    with pytest.raises(AssertionError):
        gather.gather_repo("org", "alpha", "pypi", collectors=None)
    assert touched and touched[0].startswith("subprocess:gh api graphql")


def test_parse_collectors_rejects_unknown_names(gather: ModuleType) -> None:
    assert gather.parse_collectors("pr_window, repo_object") == frozenset({"pr_window", "repo_object"})
    assert gather.parse_collectors("") is None
    with pytest.raises(SystemExit):
        gather.parse_collectors("pr_window,not_a_collector")


def test_collector_names_cover_every_envelope_directory(gather: ModuleType) -> None:
    """The documented name list is the set the CLI accepts; each name is an
    envelope directory gather writes (the BFS runbook lists them)."""
    assert set(gather.COLLECTORS) == {
        "github_meta", "pr_events", "pr_reviews", "contributors", "gfi", "actions_runs", "tree",
        "commits_180d", "depsdev", "scorecard", "downloads", "repo_object", "commits_365d",
        "dependents", "pr_window", "releases", "issues_counts", "runs", "governance",
        "author_history",
    }


def test_main_all_reads_every_candidate(gather: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rows = [{"owner": f"o{i}", "name": f"n{i}", "ecosystem": "pypi"} for i in range(13)]
    gather.CANDIDATES.parent.mkdir(parents=True, exist_ok=True)
    gather.CANDIDATES.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    seen: list[tuple[str, str, str, object]] = []
    monkeypatch.setattr(gather, "gather_repo",
                        lambda o, n, e, force=False, skip_pypistats=False, collectors=None:
                        seen.append((o, n, e, collectors)) or {"pr_window": "ok"})
    monkeypatch.setattr(sys, "argv", ["gather.py", "--all", "--collectors", "pr_window"])
    assert gather.main() == 0
    assert len(seen) == 13
    assert {c for *_, c in seen} == {frozenset({"pr_window"})}
    seen.clear()
    monkeypatch.setattr(sys, "argv", ["gather.py", "--limit", "4"])
    assert gather.main() == 0
    assert [(o, n) for o, n, *_ in seen] == [("o0", "n0"), ("o1", "n1"), ("o2", "n2"), ("o3", "n3")]
    assert {c for *_, c in seen} == {None}
