"""Tests for scripts/gather.py (Packet 2: collector consolidation, black-hole stop, gather wiring)."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def gather_mod(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("PORCHLAMP_DATA_ROOT", str(tmp_path))
    spec = importlib.util.spec_from_file_location("test_gather_mod", ROOT / "scripts/gather.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["test_gather_mod"] = module
    spec.loader.exec_module(module)
    return module


def test_gather_one_is_alias_for_gather_repo(gather_mod: ModuleType) -> None:
    assert gather_mod.gather_one is gather_mod.gather_repo


def test_gather_one_black_hole_stops_deep_collectors(
    gather_mod: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When skip_ineligible=True and the repo is a confirmed black hole,

    Phase 1 collectors complete, status records skipped_deep_black_hole=True,
    and none of Phase 2 deep collectors are executed.
    """
    owner = "blackhole-org"
    name = "stalled-repo"
    key = f"{owner}__{name}"

    now = datetime.now(timezone.utc)
    pushed = (now - timedelta(days=2)).isoformat()
    created = (now - timedelta(days=200)).isoformat()

    # Pre-populate github_meta so no network is needed for meta
    gather_mod._env("github_meta", key, "github-graphql", {
        "stars": 150, "forks": 20, "pushed_at": pushed,
        "archived": False, "created_at": created,
        "license_spdx": "MIT", "default_branch": "main",
    })

    # Pre-populate tree, contributors, repo_object, commits_180d
    gather_mod._env("tree", key, "test", {"paths": ["README.md"], "truncated": False})
    gather_mod._env("contributors", key, "test", [{"login": "m1", "contributions": 100}])
    gather_mod._env("repo_object", key, "test", {"default_branch": "main"})
    gather_mod._env("commits_180d", key, "test", [{"author_login": "m1", "date": pushed}])

    # Build 15 outside PRs with zero maintainer responses
    prs = [
        {
            "number": i,
            "createdAt": (now - timedelta(days=30 + i)).isoformat(),
            "authorAssociation": "NONE",
            "author": {"login": f"user{i}", "__typename": "User"},
            "comments": {"nodes": []},
            "reviews": {"nodes": []},
        }
        for i in range(1, 16)
    ]

    gather_mod._env("pr_window", key, "test", {
        "window_anchor": now.isoformat(),
        "prs": prs,
        "derived": [
            {
                "number": pr["number"],
                "is_external": True,
                "is_bot_author": False,
                "first_substantive_response_at": None,
            }
            for pr in prs
        ],
    })

    # Track deep collectors touched
    deep_called: list[str] = []

    def _trap(collector_name: str):
        def _fn(*args, **kwargs):
            deep_called.append(collector_name)
            raise AssertionError(f"deep collector {collector_name} must not be called on black hole")
        return _fn

    monkeypatch.setattr("porchlamp.collect.governance.collect_governance", _trap("governance"))
    monkeypatch.setattr("porchlamp.collect.commits.collect_commits_365d", _trap("commits_365d"))
    monkeypatch.setattr("porchlamp.collect.github_graphql.collect_author_history", _trap("author_history"))
    monkeypatch.setattr("porchlamp.collect.pike_signals.collect_releases", _trap("releases"))
    monkeypatch.setattr("porchlamp.collect.pike_signals.collect_issue_counts_graphql", _trap("issues_counts"))
    monkeypatch.setattr("porchlamp.collect.pike_signals.collect_runs", _trap("runs"))

    status = gather_mod.gather_one(
        owner,
        name,
        "pypi",
        force=False,
        skip_ineligible=True,
    )

    assert status.get("skipped_deep_black_hole") is True
    assert deep_called == []
    # Ensure deep collectors were not marked ok or run
    assert "governance" not in status
    assert "commits_365d" not in status
    assert "author_history" not in status


def test_commits_180d_is_not_derived_from_a_commits_365d_envelope_on_disk(
    gather_mod: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A commits_365d envelope left by an earlier run is stale: when commits_365d is not fetched in this call,

    commits_180d is fetched by its own collector, as before the derive.
    """
    owner = "org"
    name = "repo"
    key = f"{owner}__{name}"

    now = datetime.now(timezone.utc)
    stale_c365 = {
        "earliest_date": (now - timedelta(days=250)).isoformat(),
        "truncated": False,
        "commits": [
            {
                "sha": "a" * 40,
                "author_login": "dev1",
                "author_email": "dev1@example.com",
                "author_type": "User",
                "date": (now - timedelta(days=50)).isoformat(),
                "committer_date": (now - timedelta(days=50)).isoformat(),
                "has_co_authors": False,
                "has_signed_off_by": False,
            },
        ],
    }
    gather_mod._env("commits_365d", key, "github-rest:commits", stale_c365)
    gather_mod._env("github_meta", key, "github-graphql", {"default_branch": "main"})

    landed_since = {
        "commit": {"author": {"date": (now - timedelta(days=1)).isoformat(), "email": "new@example.com"}, "message": "m"},
        "author": {"login": "newcomer", "type": "User"},
    }
    monkeypatch.setattr(gather_mod, "_gh", lambda *a, **k: [landed_since])
    monkeypatch.setattr(gather_mod.time, "sleep", lambda s: None)

    status = gather_mod.gather_one(
        owner,
        name,
        "pypi",
        collectors=frozenset({"commits_180d"}),
    )

    assert status["commits_180d"] == "ok"
    payload = json.loads((gather_mod.DATA / "commits_180d" / f"{key}.json").read_text(encoding="utf-8"))
    assert payload["source"] == "github-rest:commits"
    assert [r["author_login"] for r in payload["data"]] == ["newcomer"]


def test_commits_180d_holds_a_commit_that_landed_since_the_last_run(
    gather_mod: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both collectors refreshed twice with a new commit between the runs: the second commits_180d holds it."""
    now = datetime.now(timezone.utc)

    def commit(sha: str, days_ago: int, login: str) -> dict:
        when = (now - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"sha": sha, "commit": {"author": {"date": when, "email": f"{login}@x.org"}, "message": "m"},
                "author": {"login": login, "type": "User"}}

    history = [commit(f"{i:040x}", 20 + i, "alice") for i in range(30)]

    def fake_gh(path: str, *flags: str) -> list[dict]:
        page = int(next(f for f in flags if f.startswith("page=")).split("=")[1])
        return history[(page - 1) * 100: page * 100]

    monkeypatch.setattr(gather_mod, "_gh", fake_gh)
    monkeypatch.setattr(gather_mod.time, "sleep", lambda s: None)
    both = frozenset({"commits_180d", "commits_365d"})

    gather_mod.gather_repo("o", "n", "pypi", force=True, collectors=both)
    history.insert(0, commit("f" * 40, 1, "bob"))
    gather_mod.gather_repo("o", "n", "pypi", force=True, collectors=both)

    c180 = json.loads((gather_mod.DATA / "commits_180d" / "o__n.json").read_text(encoding="utf-8"))
    assert c180["data"][0]["author_login"] == "bob"
    assert len(c180["data"]) == 31


def test_cli_flags_graphql_bulk_default_and_overrides(
    gather_mod: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """--graphql-bulk defaults to True, --no-graphql-bulk sets False,

    --gh-cli preserves environment without setting PORCHLAMP_GH=http,
    and omitting --gh-cli sets PORCHLAMP_GH=http.
    """
    candidates = gather_mod.DATA / "candidates.jsonl"
    candidates.parent.mkdir(parents=True, exist_ok=True)
    candidates.write_text(json.dumps({"owner": "a", "name": "b", "ecosystem": "pypi"}) + "\n")

    captured_kwargs: list[dict] = []
    captured_gh_env: list[str | None] = []

    def _spy_gather(owner, name, eco, **kwargs):
        captured_kwargs.append(kwargs)
        captured_gh_env.append(os.environ.get("PORCHLAMP_GH"))
        return {"pr_window": "ok"}

    monkeypatch.setattr(gather_mod, "gather_repo", _spy_gather)

    # 1. Default invocation: graphql_bulk is True, PORCHLAMP_GH is "http" during run
    monkeypatch.delenv("PORCHLAMP_GH", raising=False)
    monkeypatch.setattr(sys, "argv", ["gather.py", "--limit", "1"])
    gather_mod.main()
    assert captured_kwargs[-1]["graphql_bulk"] is True
    assert captured_gh_env[-1] == "http"

    # 2. --no-graphql-bulk overrides to False
    monkeypatch.setattr(sys, "argv", ["gather.py", "--limit", "1", "--no-graphql-bulk"])
    gather_mod.main()
    assert captured_kwargs[-1]["graphql_bulk"] is False

    # 3. --gh-cli keeps PORCHLAMP_GH untouched if not already set
    monkeypatch.delenv("PORCHLAMP_GH", raising=False)
    monkeypatch.setattr(sys, "argv", ["gather.py", "--limit", "1", "--gh-cli"])
    gather_mod.main()
    assert captured_gh_env[-1] is None
    assert "PORCHLAMP_GH" not in os.environ


@pytest.fixture
def cost_ledger():
    """The in-memory cost ledger, empty before and after the test and not retaining at the end of it."""
    from porchlamp.collect import cost

    cost.clear_records()
    yield cost
    cost.retain_records(False)
    cost.clear_records()


def _one_candidate_that_records_a_call(gather_mod: ModuleType, monkeypatch: pytest.MonkeyPatch, cost: ModuleType) -> None:
    candidates = gather_mod.DATA / "candidates.jsonl"
    candidates.parent.mkdir(parents=True, exist_ok=True)
    candidates.write_text(json.dumps({"owner": "a", "name": "b", "ecosystem": "pypi"}) + "\n")

    def gather_repo(*args: object, **kwargs: object) -> dict[str, str]:
        cost.record_call(endpoint="repos/a/b", method="GET", status=200, repo_key="a__b", collector="test")
        return {"pr_window": "ok"}

    monkeypatch.setattr(gather_mod, "gather_repo", gather_repo)


def test_cli_cost_log_written_when_specified(
    gather_mod: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cost_ledger: ModuleType,
) -> None:
    """Specifying --cost-log keeps the calls made during the run and writes them with cost.write_cost_log at exit."""
    cost_path = tmp_path / "test_costs" / "gather-cost.jsonl"
    _one_candidate_that_records_a_call(gather_mod, monkeypatch, cost_ledger)
    monkeypatch.setattr(sys, "argv", ["gather.py", "--limit", "1", "--cost-log", str(cost_path)])

    gather_mod.main()
    assert cost_path.is_file()
    lines = cost_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    record = json.loads(lines[0])
    assert record["collector"] == "test"
    assert record["repo_key"] == "a__b"


def test_cli_keeps_no_cost_record_without_cost_log(
    gather_mod: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    cost_ledger: ModuleType,
) -> None:
    """Without --cost-log nothing is kept: a process that runs gather for days must not grow."""
    _one_candidate_that_records_a_call(gather_mod, monkeypatch, cost_ledger)
    monkeypatch.setattr(sys, "argv", ["gather.py", "--limit", "1"])

    gather_mod.main()
    assert cost_ledger.get_records() == []


EXPERIMENTAL = "EXPERIMENTAL: not equivalent to a full fetch; do not use for a published snapshot."


@pytest.mark.parametrize("flag", ["--incremental-ledgers", "--light-author-history"])
def test_experimental_flags_say_so_in_help_and_on_stderr(
    gather_mod: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flag: str,
) -> None:
    with pytest.raises(SystemExit):
        gather_mod.main(["--help"])
    assert f"{flag} {EXPERIMENTAL}" in " ".join(capsys.readouterr().out.split())

    monkeypatch.setattr(gather_mod, "gather_repo", lambda *a, **k: {"pr_window": "ok"})
    gather_mod.main(["--repo", "a", "b", flag])
    assert capsys.readouterr().err.splitlines().count(EXPERIMENTAL) == 1

    gather_mod.main(["--repo", "a", "b"])
    assert EXPERIMENTAL not in capsys.readouterr().err


def test_commits_365d_materializes_commits_180d_when_missing(
    gather_mod: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When commits_365d is gathered in Phase 2 and commits_180d envelope is missing,

    it materializes commits_180d using derive_commits_180d_rows.
    """
    owner = "active-org"
    name = "active-repo"
    key = f"{owner}__{name}"

    now = datetime.now(timezone.utc)
    gather_mod._env("github_meta", key, "github-graphql", {"default_branch": "main"})

    fake_commits = [
        {
            "commit": {"author": {"date": (now - timedelta(days=20)).isoformat(), "email": "a@ex.com"}, "message": "feat: test"},
            "author": {"login": "dev1", "type": "User"},
        },
        {
            "commit": {"author": {"date": (now - timedelta(days=200)).isoformat(), "email": "b@ex.com"}, "message": "fix: old"},
            "author": {"login": "dev2", "type": "User"},
        },
    ]

    monkeypatch.setattr(gather_mod, "_gh", lambda *a, **k: fake_commits)
    monkeypatch.setattr(gather_mod.time, "sleep", lambda s: None)

    status = gather_mod.gather_one(
        owner,
        name,
        "pypi",
        collectors=frozenset({"commits_365d"}),
    )

    assert status["commits_365d"] == "ok"
    c180_file = gather_mod.DATA / "commits_180d" / f"{key}.json"
    assert c180_file.is_file()
    c180_payload = json.loads(c180_file.read_text(encoding="utf-8"))
    assert c180_payload["source"] == "derived:commits_365d"
    assert len(c180_payload["data"]) == 1
    assert c180_payload["data"][0]["author_login"] == "dev1"
    c365_payload = json.loads((gather_mod.DATA / "commits_365d" / f"{key}.json").read_text(encoding="utf-8"))
    assert c180_payload["fetched_at"] == c365_payload["fetched_at"]


def test_gather_one_proceeds_to_deep_when_not_black_hole(
    gather_mod: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a repo is responsive (not a black hole), Phase 2 deep collectors

    are executed even if skip_ineligible=True.
    """
    owner = "responsive-org"
    name = "healthy-repo"
    key = f"{owner}__{name}"

    now = datetime.now(timezone.utc)
    gather_mod._env("github_meta", key, "github-graphql", {
        "stars": 200, "forks": 50, "pushed_at": now.isoformat(),
        "archived": False, "created_at": (now - timedelta(days=500)).isoformat(),
        "license_spdx": "Apache-2.0", "default_branch": "main",
    })
    gather_mod._env("tree", key, "test", {"paths": ["README.md"], "truncated": False})
    gather_mod._env("contributors", key, "test", [{"login": "m1", "contributions": 100}])
    gather_mod._env("repo_object", key, "test", {"default_branch": "main"})
    gather_mod._env("commits_180d", key, "test", [{"author_login": "m1", "date": now.isoformat()}])

    # PR window with fast responses
    prs = [
        {
            "number": i,
            "createdAt": (now - timedelta(days=30 + i)).isoformat(),
            "authorAssociation": "NONE",
            "author": {"login": f"user{i}", "__typename": "User"},
            "comments": {
                "nodes": [
                    {
                        "createdAt": (now - timedelta(days=29 + i)).isoformat(),
                        "author": {"login": "maintainer", "__typename": "User"},
                        "authorAssociation": "MEMBER",
                        "bodyText": "This is a detailed and substantive review comment exceeding forty characters.",
                    }
                ]
            },
            "reviews": {"nodes": []},
        }
        for i in range(1, 15)
    ]

    gather_mod._env("pr_window", key, "test", {
        "window_anchor": now.isoformat(),
        "prs": prs,
    })

    deep_called: list[str] = []

    def _trap(name: str):
        def _fn(*a, **k):
            deep_called.append(name)
            return "ok"
        return _fn

    monkeypatch.setattr("porchlamp.collect.governance.collect_governance", _trap("governance"))
    monkeypatch.setattr("porchlamp.collect.commits.collect_commits_365d", lambda *a, **k: {"commits": [], "truncated": False})
    monkeypatch.setattr("porchlamp.collect.github_graphql.collect_author_history", _trap("author_history"))
    monkeypatch.setattr("porchlamp.collect.pike_signals.collect_releases", _trap("releases"))
    monkeypatch.setattr("porchlamp.collect.pike_signals.collect_issue_counts_graphql", _trap("issues_counts"))
    monkeypatch.setattr("porchlamp.collect.pike_signals.collect_runs", _trap("runs"))
    monkeypatch.setattr(gather_mod, "_gh", lambda *a, **k: [])
    monkeypatch.setattr(gather_mod.time, "sleep", lambda s: None)

    status = gather_mod.gather_one(
        owner,
        name,
        "pypi",
        collectors=frozenset({"governance", "releases"}),
        skip_ineligible=True,
    )

    assert status.get("skipped_deep_black_hole") is not True
    assert "governance" in deep_called
    assert "releases" in deep_called
