"""Regression tests for data collection & pipeline cost-vs-quality optimizations (Issue #118)."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

import pytest

from porchlamp.collect import depsdev, governance, labels, pike_signals, repo_object
from porchlamp.collect.envelope import read_envelope, write_envelope

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def gather_mod(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("PORCHLAMP_DATA_ROOT", str(tmp_path))
    spec = importlib.util.spec_from_file_location("porchlamp_gather_opt_test", ROOT / "scripts/gather.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["porchlamp_gather_opt_test"] = module
    spec.loader.exec_module(module)
    return module


def test_opt1_gather_repo_short_circuits_after_archived_meta_and_tree(
    gather_mod: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OPT-1: Once github_meta + tree confirm a repo is archived/unlicensed/non_software,
    contributors, commits_180d, and actions_runs are skipped without extra REST calls."""
    monkeypatch.setattr(gather_mod.time, "sleep", lambda _s: None)

    def _fake_gh_run(cmd, timeout=90):
        import subprocess
        payload = {
            "data": {
                "r": {
                    "stargazerCount": 10,
                    "forkCount": 1,
                    "pushedAt": "2026-10-01T00:00:00Z",
                    "isArchived": True,
                    "createdAt": "2020-01-01T00:00:00Z",
                    "mirrorUrl": None,
                    "isFork": False,
                    "parent": None,
                    "licenseInfo": {"spdxId": "MIT"},
                    "defaultBranchRef": {"name": "main"},
                    "pullRequests": {"totalCount": 5, "nodes": []},
                }
            }
        }
        return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

    rest_calls: list[str] = []

    def _fake_gh(path: str, *flags: str):
        rest_calls.append(path)
        if "/git/trees/" in path:
            return {"tree": [{"path": "pyproject.toml"}], "truncated": False}
        raise AssertionError(f"Unexpected downstream REST call for archived repo: {path}")

    monkeypatch.setattr(gather_mod.ghcli, "run", _fake_gh_run)
    monkeypatch.setattr(gather_mod, "_gh", _fake_gh)

    st = gather_mod.gather_repo(
        "acme",
        "archived-lib",
        "pypi",
        collectors=frozenset({"github_meta", "pr_events", "pr_reviews", "tree", "contributors", "commits_180d", "actions_runs"}),
    )
    assert st["github_meta"] == "ok"
    assert st["tree"] == "ok"
    assert st["skipped_ineligible"] == "archived"
    assert st["contributors"] == "skip"
    assert st["commits_180d"] == "skip"
    assert st["actions_runs"] == "skip"
    assert rest_calls == ["repos/acme/archived-lib/git/trees/main"]


def test_opt2_commits_180d_shapes_actor_fields_and_stops_after_page_1_human_commit(
    gather_mod: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OPT-2: commits_180d preserves author_type/author_email and stops after page 1
    once at least one non-bot commit is recorded."""
    monkeypatch.setattr(gather_mod.time, "sleep", lambda _s: None)
    pages_requested: list[str] = []

    def _fake_gh(path: str, *flags: str):
        page_flag = next(f for f in flags if f.startswith("page="))
        pages_requested.append(page_flag)
        # Return 100 commits on page 1 so len(d) == 100 would previously trigger page 2
        return [
            {
                "sha": f"sha-{i}",
                "author": {"login": "alice-bot", "type": "User"},
                "commit": {
                    "author": {"date": "2026-10-01T00:00:00Z", "email": "alice@example.com"},
                    "message": "feat: add widget\n\nSigned-off-by: Alice <alice@example.com>",
                },
            }
            for i in range(100)
        ]

    monkeypatch.setattr(gather_mod, "_gh", _fake_gh)
    st = gather_mod.gather_repo("acme", "active-lib", "pypi", collectors=frozenset({"commits_180d"}))
    assert st["commits_180d"] == "ok"
    assert pages_requested == ["page=1"]
    env = read_envelope(gather_mod.DATA / "commits_180d" / "acme__active-lib.json")
    assert len(env.data) == 100
    assert env.data[0]["author_login"] == "alice-bot"
    assert env.data[0]["author_type"] == "User"
    assert env.data[0]["author_email"] == "alice@example.com"
    assert env.data[0]["signed_off"] is True


def test_opt2_commits_180d_continues_past_bot_only_page_1(
    gather_mod: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OPT-2: When page 1 consists of 100 bot commits (0 human commits), commits_180d
    continues to page 2 so human commits behind a bot flood are not missed."""
    monkeypatch.setattr(gather_mod.time, "sleep", lambda _s: None)
    pages_requested: list[str] = []

    def _fake_gh(path: str, *flags: str):
        page_flag = next(f for f in flags if f.startswith("page="))
        pages_requested.append(page_flag)
        if page_flag == "page=1":
            return [
                {
                    "sha": f"bot-sha-{i}",
                    "author": {"login": "dependabot[bot]", "type": "Bot"},
                    "commit": {
                        "author": {"date": "2026-10-02T00:00:00Z", "email": "dependabot@users.noreply.github.com"},
                        "message": "chore(deps): bump dep",
                    },
                }
                for i in range(100)
            ]
        return [
            {
                "sha": "human-sha-1",
                "author": {"login": "alice", "type": "User"},
                "commit": {
                    "author": {"date": "2026-10-01T00:00:00Z", "email": "alice@example.com"},
                    "message": "fix: real human commit",
                },
            }
        ]

    monkeypatch.setattr(gather_mod, "_gh", _fake_gh)
    st = gather_mod.gather_repo("acme", "bot-flood", "pypi", collectors=frozenset({"commits_180d"}))
    assert st["commits_180d"] == "ok"
    assert pages_requested == ["page=1", "page=2"]
    env = read_envelope(gather_mod.DATA / "commits_180d" / "acme__bot-flood.json")
    assert len(env.data) == 101
    assert env.data[-1]["author_login"] == "alice"


def test_opt3_collect_releases_uses_single_graphql_call_for_tag_fallback(tmp_path: Path) -> None:
    """OPT-3: When a repo has 0 GitHub releases, collect_releases resolves tag commit dates
    in 1 GraphQL query instead of 100 sequential REST /commits/{sha} calls."""
    now = datetime(2026, 10, 15, tzinfo=timezone.utc)
    rest_calls: list[str] = []

    class _RestStub:
        def get(self, path: str, **params):
            rest_calls.append(path)
            if path.endswith("/releases"):
                return []
            raise AssertionError(f"Unexpected REST fallback call: {path}")

    gql_calls: list[dict] = []

    class _GqlStub:
        def execute(self, query: str, variables=None):
            gql_calls.append(dict(variables or {}))
            return {
                "data": {
                    "r": {
                        "refs": {
                            "nodes": [
                                {
                                    "name": "v1.2.0",
                                    "target": {
                                        "oid": "tag-oid-1",
                                        "target": {
                                            "oid": "commit-sha-1",
                                            "author": {"date": "2026-09-01T00:00:00Z"},
                                        },
                                    },
                                },
                                {
                                    "name": "v0.1.0",
                                    "target": {
                                        "oid": "commit-sha-0",
                                        "author": {"date": "2023-01-01T00:00:00Z"},
                                    },
                                },
                            ]
                        }
                    }
                }
            }

    res = pike_signals.collect_releases(
        tmp_path, "acme", "tagged", now=now, transport=_RestStub(), graphql_transport=_GqlStub()
    )
    assert res == "ok"
    assert rest_calls == ["repos/acme/tagged/releases"]
    assert len(gql_calls) == 1
    env = read_envelope(tmp_path / "releases" / "acme__tagged.json")
    assert env.data["source"] == "tags"
    assert env.data["recent_count"] == 1
    assert env.data["tags"] == [
        {"name": "v1.2.0", "sha": "commit-sha-1", "commit_date": "2026-09-01T00:00:00Z"},
        {"name": "v0.1.0", "sha": "commit-sha-0", "commit_date": "2023-01-01T00:00:00Z"},
    ]


def test_opt4_collect_repo_object_reuses_expanded_github_meta_without_graphql_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OPT-4: collect_repo_object reuses github_meta when mirror_url, fork, and
    pull_requests_total are already recorded."""
    write_envelope(
        tmp_path / "github_meta" / "acme__widget.json",
        source="github-graphql",
        data={
            "stars": 100,
            "forks": 10,
            "pushed_at": "2026-10-01T00:00:00Z",
            "archived": False,
            "created_at": "2021-01-01T00:00:00Z",
            "license_spdx": "Apache-2.0",
            "default_branch": "main",
            "mirror_url": None,
            "fork": False,
            "parent": None,
            "pull_requests_total": 42,
        },
        fetched_at="2026-10-01T00:00:00Z",
    )
    write_envelope(
        tmp_path / "tree" / "acme__widget.json",
        source="github-rest",
        data={"paths": ["pyproject.toml", "LICENSE"], "truncated": False},
        fetched_at="2026-10-01T00:00:00Z",
    )
    monkeypatch.setattr(
        repo_object,
        "_gh_graphql",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("GraphQL should not be called")),
    )
    assert repo_object.collect_repo_object(tmp_path, "acme", "widget", "main") == "ok"
    env = read_envelope(tmp_path / "repo_object" / "acme__widget.json")
    assert env.data["archived"] is False
    assert env.data["fork"] is False
    assert env.data["mirror_url"] is None
    assert env.data["pull_requests_total"] == 42
    assert env.data["license_spdx_id"] == "Apache-2.0"


def test_opt5_governance_reuses_pr_window_and_caches_org_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OPT-5: collect_governance skips GOVERNANCE_PRS_QUERY when pr_window already proves
    0 merged PRs on an exhausted window, and caches <owner>/.github org-default fetches."""
    governance.clear_org_default_file_cache()
    write_envelope(
        tmp_path / "pr_window" / "acme__r1.json",
        source="github-graphql:pr_window",
        data={
            "window_days": 180,
            "window_anchor": "2026-10-01T00:00:00Z",
            "truncated": False,
            "window_exhausted": True,
            "prs": [],
        },
        fetched_at="2026-10-01T00:00:00Z",
    )
    write_envelope(
        tmp_path / "pr_window" / "acme__r2.json",
        source="github-graphql:pr_window",
        data={
            "window_days": 180,
            "window_anchor": "2026-10-01T00:00:00Z",
            "truncated": False,
            "window_exhausted": True,
            "prs": [],
        },
        fetched_at="2026-10-01T00:00:00Z",
    )
    for repo in ("r1", "r2"):
        write_envelope(
            tmp_path / "tree" / f"acme__{repo}.json",
            source="github-rest",
            data={"paths": ["README.md"], "truncated": False},
            fetched_at="2026-10-01T00:00:00Z",
        )

    org_fetch_calls: list[tuple[str, str]] = []

    def _fake_fetch_file_text(owner: str, name: str, **_kw):
        def _fetch(path: str):
            if name == ".github":
                org_fetch_calls.append((owner, path))
                if path == "SECURITY.md":
                    return "Report vulnerabilities to security@acme.org"
            return None
        return _fetch

    monkeypatch.setattr(governance, "fetch_file_text", _fake_fetch_file_text)

    class _ForbidGql:
        def execute(self, query: str, variables=None):
            raise AssertionError(f"Unexpected GraphQL query: {query[:60]}")

    assert governance.collect_governance(
        tmp_path, "acme", "r1", graphql_transport=_ForbidGql(), org_tree_paths=["SECURITY.md"]
    ) == "ok"
    assert governance.collect_governance(
        tmp_path, "acme", "r2", graphql_transport=_ForbidGql(), org_tree_paths=["SECURITY.md"]
    ) == "ok"
    # <owner>/.github SECURITY.md fetched only ONCE across both repos in the same org
    assert org_fetch_calls == [("acme", "SECURITY.md")]

    # When boundary_overflow_prs is non-empty, 0 merged PRs in 180d does NOT mean 0 all-time merged PRs
    write_envelope(
        tmp_path / "pr_window" / "acme__r3.json",
        source="github-graphql:pr_window",
        data={
            "window_days": 180,
            "window_anchor": "2026-10-01T00:00:00Z",
            "truncated": False,
            "window_exhausted": True,
            "boundary_overflow_prs": [{"number": 1, "createdAt": "2025-01-01T00:00:00Z"}],
            "prs": [],
        },
        fetched_at="2026-10-01T00:00:00Z",
    )
    assert governance._reusable_merged_prs_from_pr_window(tmp_path, "acme__r3") is None


def test_opt6_depsdev_fetches_latest_only_for_winning_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    """OPT-6: resolve_repo_package does not call _depsdev_latest for losing candidates."""
    monkeypatch.setattr(
        depsdev,
        "_fetch_status",
        lambda _url: (
            200,
            {
                "versions": [
                    {
                        "versionKey": {"system": "NPM", "name": f"pkg-{i}", "version": "1.0.0"},
                        "relationType": "SOURCE_REPO",
                    }
                    for i in range(5)
                ]
            },
        ),
    )
    monkeypatch.setattr(
        depsdev,
        "_ecosystems_count",
        lambda _eco, pkg: ("count", 500 if pkg == "pkg-3" else 10, f"https://ecosyste.ms/{pkg}"),
    )
    latest_calls: list[str] = []

    def _spy_latest(system: str, pkg: str):
        latest_calls.append(pkg)
        return ("1.0.0", depsdev.DependentsRecord(100, 80, 20, None))

    monkeypatch.setattr(depsdev, "_depsdev_latest", _spy_latest)
    res = depsdev.resolve_repo_package("acme", "mono", "npm")
    assert res is not None and res.verdict == "found"
    assert res.package == "pkg-3"
    assert latest_calls == ["pkg-3"]


def test_opt7_labels_stops_paging_once_page_1_crosses_90d_cutoff() -> None:
    """OPT-7: collect_newcomer_issues does not request page 2 when page 1's oldest issue
    is already older than the 90-day recency cutoff."""
    now = datetime(2026, 10, 15, tzinfo=timezone.utc)
    pages_called: list[str] = []

    def _transport(endpoint: str, *flags: str):
        if endpoint.endswith("/labels"):
            return [{"name": "good first issue"}]
        page_flag = next(f for f in flags if f.startswith("page="))
        pages_called.append(page_flag)
        if page_flag == "page=1":
            return [
                {
                    "number": 200 - i,
                    "state": "open",
                    "assignee": None,
                    "updated_at": (now - timedelta(days=i + 1)).isoformat(),
                    "labels": [{"name": "good first issue"}],
                }
                for i in range(100)
            ]
        raise AssertionError("Page 2 should not be fetched when page 1 already reached >90d ago")

    result = labels.collect_newcomer_issues(_transport, "acme", "widget", now=now)
    assert pages_called == ["page=1"]
    assert len(result.issues) == 90
    assert result.truncated is False
