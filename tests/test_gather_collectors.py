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
from datetime import datetime, timezone
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

    def _fetch_status(url: str):
        touched.append(f"deps.dev:{url}")
        raise AssertionError(f"unexpected deps.dev GET {url}")

    monkeypatch.setattr(gather, "_gh", _gh)
    monkeypatch.setattr(gather.subprocess, "run", _run)
    monkeypatch.setattr(gather, "_get_json", _get_json)
    monkeypatch.setattr("porchlamp.collect.depsdev._fetch_status", _fetch_status)
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


def test_keyless_second_source_429_and_5xx_do_not_write_envelopes(
    gather: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Transient 429 or 5xx responses from deps.dev/scorecard must record 'fail'
    and leave no envelope on disk, whereas 404 still caches a negative envelope."""
    gather._env("github_meta", "org__alpha", "github-graphql", {"default_branch": "main"})
    responses = {
        "deps.dev": (None, 429, RuntimeError("429")),
        "securityscorecards.dev": (None, 503, RuntimeError("503")),
    }

    def _fake_fetch_json(url: str, headers=None):
        for host, resp in responses.items():
            if host in url:
                return resp
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(gather, "_fetch_json", _fake_fetch_json)
    monkeypatch.setattr(gather.time, "sleep", lambda s: None)
    status = gather.gather_repo("org", "alpha", "pypi", collectors=frozenset({"depsdev", "scorecard"}))
    assert status["depsdev"] == "fail"
    assert status["scorecard"] == "fail"
    assert not (gather.DATA / "depsdev" / "org__alpha.json").exists()
    assert not (gather.DATA / "scorecard" / "org__alpha.json").exists()

    # 404 is a genuine negative lookup and should still write a negative envelope.
    responses["deps.dev"] = (None, 404, RuntimeError("404"))
    gather.gather_repo("org", "alpha", "pypi", collectors=frozenset({"depsdev"}))
    env_path = gather.DATA / "depsdev" / "org__alpha.json"
    assert env_path.is_file()
    assert json.loads(env_path.read_text(encoding="utf-8"))["http_status"] == 404


def test_gfi_runs_only_via_labels_collector_without_duplicate_rest_call(
    gather: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Selecting 'gfi' must invoke porchlamp.collect.labels.collect_newcomer_issues
    once and never hit the removed legacy REST gfi tuple."""
    touched = _forbid_transports(gather, monkeypatch)
    gather._env("github_meta", "org__alpha", "github-graphql", {"default_branch": "main"})
    import porchlamp.collect.labels as labels_mod

    gfi_calls: list[tuple[str, str]] = []

    def _fake_collect_newcomer_issues(gh_fn, owner: str, name: str):
        gfi_calls.append((owner, name))
        return labels_mod.GfiResult(
            issues=[],
            labels_matched=[],
            queries=[],
            truncated=False,
            fetched_at="2026-09-12T00:00:00+00:00",
        )

    monkeypatch.setattr(labels_mod, "collect_newcomer_issues", _fake_collect_newcomer_issues)
    status = gather.gather_repo("org", "alpha", "pypi", collectors=frozenset({"gfi"}))
    assert status["gfi"] == "ok"
    assert gfi_calls == [("org", "alpha")]
    assert touched == []


def test_main_isolates_unexpected_gather_repo_exception(
    gather: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unhandled exception inside gather_repo for one candidate must not
    abort the entire sweep in main()."""
    rows = [
        {"owner": "o0", "name": "boom", "ecosystem": "pypi"},
        {"owner": "o1", "name": "ok", "ecosystem": "pypi"},
    ]
    gather.CANDIDATES.parent.mkdir(parents=True, exist_ok=True)
    gather.CANDIDATES.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    visited: list[str] = []

    def _flaky_gather(o, n, e, force=False, skip_pypistats=False, collectors=None):
        visited.append(f"{o}/{n}")
        if n == "boom":
            raise RuntimeError("simulated unexpected failure")
        return {"pr_window": "ok"}

    monkeypatch.setattr(gather, "gather_repo", _flaky_gather)
    monkeypatch.setattr(sys, "argv", ["gather.py", "--all", "--collectors", "pr_window"])
    assert gather.main() == 0
    assert visited == ["o0/boom", "o1/ok"]


def test_make_sample_overwrites_readonly_existing_files(tmp_path: Path) -> None:
    """Re-running copy_sample when destination envelopes are already 0o444 and
    the source file has changed must succeed instead of raising PermissionError."""
    spec = importlib.util.spec_from_file_location("porchlamp_make_sample", ROOT / "scripts/make_sample.py")
    make_sample = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(make_sample)

    src_data = tmp_path / "src_data"
    dst_sample = tmp_path / "dst_sample"
    meta_file = src_data / "raw" / "github_meta" / "a__b.json"
    meta_file.parent.mkdir(parents=True)
    meta_file.write_text('{"stars": 1}', encoding="utf-8")
    entries = [{"owner": "a", "name": "b", "ecosystem": "pypi", "selected_by": ["test"]}]

    _, stats1 = make_sample.copy_sample(src_data, dst_sample, entries)
    assert stats1["copied"] == 1
    meta_file.write_text('{"stars": 2}', encoding="utf-8")
    _, stats2 = make_sample.copy_sample(src_data, dst_sample, entries)
    assert stats2["copied"] == 1
    assert (dst_sample / "raw" / "github_meta" / "a__b.json").read_text(encoding="utf-8") == '{"stars": 2}'


def test_main_dirty_and_fingerprint_only_skip_clean_repos(
    gather: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--dirty runs the 25-repo fingerprint pass and only calls gather_repo on
    dirty repos with their invalidated collector subset; --fingerprint-only exits
    before calling gather_repo."""
    import porchlamp.collect.fingerprint as fp_mod

    rows = [
        {"owner": "acme", "name": "clean_repo", "ecosystem": "pypi"},
        {"owner": "acme", "name": "dirty_repo", "ecosystem": "pypi"},
    ]
    gather.CANDIDATES.parent.mkdir(parents=True, exist_ok=True)
    gather.CANDIDATES.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    fake_result = fp_mod.FingerprintSweepResult(
        fingerprints={},
        previous={},
        invalidated_by_repo={
            "acme__clean_repo": frozenset(),
            "acme__dirty_repo": frozenset({"releases"}),
        },
        dirty_by_repo={
            "acme__clean_repo": frozenset(),
            "acme__dirty_repo": frozenset({"releases"}),
        },
        tiers_by_repo={"acme__clean_repo": "warm", "acme__dirty_repo": "hot"},
        unresolved_keys=(),
        queries_executed=1,
        cost_points=1,
        rate_limit_remaining=4999,
    )
    monkeypatch.setattr(fp_mod, "run_fingerprint_pass", lambda *a, **k: fake_result)

    gathered: list[tuple[str, str, bool, object]] = []

    def _spy_gather(o, n, e, force=False, skip_pypistats=False, collectors=None, **kw):
        gathered.append((o, n, force, collectors))
        return {"releases": "ok"}

    monkeypatch.setattr(gather, "gather_repo", _spy_gather)

    # 1. --fingerprint-only exits after fingerprint pass with 0 gather_repo calls
    assert gather.main(["--all", "--fingerprint-only"]) == 0
    assert gathered == []

    # 2. --dirty skips clean_repo and gathers only releases (force=True) on dirty_repo
    assert gather.main(["--all", "--dirty"]) == 0
    assert gathered == [("acme", "dirty_repo", True, frozenset({"releases"}))]


# --------------------------------------------------------------------------- #
# --dirty correctness (PR #3 review, blocker 2): offline, fake GraphQL + gather #
# --------------------------------------------------------------------------- #


class _FakeFingerprintTransport:
    """One repo ``acme/widget``; ``mode`` picks what GitHub answers."""

    def __init__(self) -> None:
        self.pushed = "2026-10-01T00:00:00Z"
        self.mode = "ok"  # ok | all_alias_errors | http_502

    def execute(self, query, variables=None):
        if self.mode == "http_502":
            raise RuntimeError("gh api graphql rc=1: HTTP 502")
        if self.mode == "all_alias_errors":  # the live RESOURCE_LIMITS_EXCEEDED shape
            return {"data": {"rateLimit": {"cost": 3, "remaining": 4000}, "r0": None},
                    "errors": [{"type": "RESOURCE_LIMITS_EXCEEDED", "path": ["r0"]}]}
        return {"data": {"rateLimit": {"cost": 1, "remaining": 4000}, "r0": {
            "pushedAt": self.pushed, "updatedAt": self.pushed, "isArchived": False,
            "isDisabled": False, "stargazerCount": 1, "forkCount": 1,
            "pullRequests": {"nodes": [{"updatedAt": self.pushed}]},
            "issues": {"nodes": []}, "releases": {"nodes": []},
            "defaultBranchRef": {"target": {"oid": self.pushed}}}}}


@pytest.fixture
def dirty_env(gather: ModuleType, monkeypatch: pytest.MonkeyPatch):
    """gather.main with a fake GraphQL transport and a gather_repo that writes the
    envelopes it was asked for (or fails on request); returns (run, state)."""
    import porchlamp.collect.fingerprint as fp_mod

    gather.CANDIDATES.parent.mkdir(parents=True, exist_ok=True)
    gather.CANDIDATES.write_text(
        json.dumps({"owner": "acme", "name": "widget", "ecosystem": "pypi"}) + "\n")
    transport = _FakeFingerprintTransport()
    real_pass = fp_mod.run_fingerprint_pass
    monkeypatch.setattr(
        fp_mod, "run_fingerprint_pass",
        lambda repos, **kw: real_pass(repos, transport=transport, sleep=lambda s: None, **kw))
    calls: list[object] = []
    behaviour = {"fail": False, "raise": False}

    def _gather(o, n, e, force=False, skip_pypistats=False, collectors=None, **kw):
        calls.append(sorted(collectors) if collectors else None)
        if behaviour["raise"]:
            raise RuntimeError("network")
        names = collectors if collectors is not None else fp_mod.ALL_GATHER_COLLECTORS
        status = {}
        for c in names:
            if behaviour["fail"] and c == "pr_window":
                status[c] = "fail"
                continue
            key = f"{e}__{n}" if c == "downloads" else f"{o}__{n}"
            data = {"window_anchor": datetime.now(timezone.utc).isoformat()}
            gather._env(c, key, "test", data)
            status[c] = "ok"
        return status

    monkeypatch.setattr(gather, "gather_repo", _gather)

    def run(*args: str) -> list[object]:
        calls.clear()
        assert gather.main(["--all", *args]) == 0
        return list(calls)

    state = {"transport": transport, "behaviour": behaviour,
             "state_file": lambda: gather._fingerprint_state()}
    return run, state


def test_dirty_unresolved_repo_is_refetched_not_skipped_as_clean(dirty_env) -> None:
    run, state = dirty_env
    assert run("--dirty")  # first pass over a new repo: a full gather, state saved
    assert run("--dirty") == []  # now clean
    state["transport"].mode = "all_alias_errors"
    calls = run("--dirty")
    assert calls, "a repo the fingerprint could not resolve must be refetched"
    assert {"pr_window", "commits_365d", "releases"} <= set(calls[0])


def test_dirty_survives_a_failed_fingerprint_batch(dirty_env) -> None:
    run, state = dirty_env
    state["transport"].mode = "http_502"
    assert run("--dirty")  # exit 0, the repo is gathered


def test_fingerprint_only_does_not_consume_a_change(dirty_env) -> None:
    run, state = dirty_env
    run("--dirty")
    state["transport"].pushed = "2026-10-05T00:00:00Z"  # the repo changes
    before = state["state_file"]().read_bytes()
    assert run("--fingerprint-only") == []
    assert state["state_file"]().read_bytes() == before
    calls = run("--dirty")
    assert calls and "pr_window" in calls[0]


def test_failed_refetch_keeps_the_old_fingerprint_so_it_is_retried(dirty_env) -> None:
    run, state = dirty_env
    run("--dirty")
    state["transport"].pushed = "2026-10-06T00:00:00Z"
    state["behaviour"]["raise"] = True
    assert run("--dirty")
    state["behaviour"]["raise"] = False
    state["behaviour"]["fail"] = True  # pr_window reports fail
    assert run("--dirty")
    state["behaviour"]["fail"] = False
    retried = run("--dirty")
    assert retried and "pr_window" in retried[0]
    assert run("--dirty") == []  # consumed only after the success


def test_collectors_subset_never_persists_the_fingerprint(dirty_env) -> None:
    run, state = dirty_env
    run("--dirty")
    state["transport"].pushed = "2026-10-07T00:00:00Z"
    assert run("--dirty", "--collectors", "releases") == []  # nothing selected is dirty
    calls = run("--dirty", "--collectors", "pr_window")
    assert calls == [["pr_window"]]
    again = run("--dirty")  # the move was not consumed by the partial run
    assert again and "commits_365d" in again[0]


def _pr_run(sha: str, conclusion: str, minutes: int) -> dict:
    return {
        "head_sha": sha, "status": "completed", "conclusion": conclusion, "workflow_id": 1, "run_attempt": 1,
        "created_at": "2026-10-01T00:00:00Z", "run_started_at": "2026-10-01T00:00:00Z",
        "updated_at": f"2026-10-01T00:{minutes:02d}:00Z",
    }


def _serve_pages(gather: ModuleType, monkeypatch: pytest.MonkeyPatch, pages: list[list[dict]]) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def _gh(path: str, *flags: str):
        calls.append((path, *flags))
        page = int(next(f for f in flags if f.startswith("page=")).split("=")[1])
        return {"workflow_runs": pages[page - 1] if page <= len(pages) else []}

    monkeypatch.setattr(gather, "_gh", _gh)
    monkeypatch.setattr(gather.time, "sleep", lambda _s: None)
    return calls


def test_pr_runs_asks_for_day_old_pull_request_runs_and_stops_after_one_short_page(
    gather: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _serve_pages(gather, monkeypatch, [[_pr_run("a", "success", 12), _pr_run("a", "skipped", 0)]])
    rows = gather._fetch_pr_runs("acme", "widget", now=datetime(2026, 10, 13, 2, 30, 15, tzinfo=timezone.utc))
    assert calls == [("repos/acme/widget/actions/runs", "-X", "GET", "-f", "per_page=100",
                      "-f", "event=pull_request", "-f", "created=<=2026-10-12T02:30:15Z", "-f", "page=1")]
    assert [(r["head_sha"], r["conclusion"], r["run_duration_seconds"]) for r in rows] == [
        ("a", "success", 720), ("a", "skipped", 0)]


def test_pr_runs_fetches_no_second_page_once_five_commits_have_a_wait(
    gather: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full first page whose complete commits already give the 5.4 minimum
    sample costs one call; the commit the page cut through is not stored."""
    page = [_pr_run(f"c{i // 10}", "success", 5) for i in range(100)]  # 10 commits x 10 runs; c9 may be cut
    calls = _serve_pages(gather, monkeypatch, [page, [_pr_run("never", "success", 5)]])
    rows = gather._fetch_pr_runs("acme", "widget")
    assert len(calls) == 1
    assert {r["head_sha"] for r in rows} == {f"c{i}" for i in range(9)}


def test_pr_runs_fetches_another_page_while_fewer_than_five_commits_have_a_wait(
    gather: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Twenty-five runs per commit: a full page holds four commits, one of
    them cut. The second page completes it and adds more."""
    first = [_pr_run(f"c{i // 25}", "success", 5) for i in range(100)]            # c0..c3
    second = [_pr_run(f"c{4 + i // 25}", "success", 5) for i in range(60)]        # c4, c5, part of c6; a short page
    calls = _serve_pages(gather, monkeypatch, [first, second])
    rows = gather._fetch_pr_runs("acme", "widget")
    assert [c[-1] for c in calls] == ["page=1", "page=2"]
    assert {r["head_sha"] for r in rows} == {"c0", "c1", "c2", "c3", "c4", "c5", "c6"}


def test_pr_runs_stops_at_the_page_cap_and_drops_the_cut_commit(
    gather: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every commit superseded (a cancelled run on each): no page ever gives
    five waits, so the fetch stops at the cap instead of walking the history."""
    def full(page_no: int) -> list[dict]:
        return [_pr_run(f"p{page_no}c{i // 50}", "cancelled", 5) for i in range(100)]

    calls = _serve_pages(gather, monkeypatch, [full(1), full(2), full(3), full(4)])
    rows = gather._fetch_pr_runs("acme", "widget")
    assert [c[-1] for c in calls] == ["page=1", "page=2", "page=3"]
    assert "p3c1" not in {r["head_sha"] for r in rows} and "p3c0" in {r["head_sha"] for r in rows}


def test_gather_repo_writes_the_pr_runs_envelope(gather: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    _serve_pages(gather, monkeypatch, [[_pr_run("a", "success", 12)]])
    status = gather.gather_repo("acme", "widget", "pypi", collectors=frozenset({"actions_runs"}))
    assert status["actions_runs"] == "ok"
    envelope = json.loads((gather.DATA / "actions_runs" / "acme__widget.json").read_text())
    assert envelope["source"] == "github-rest:repos/acme/widget/actions/runs"
    assert [(r["head_sha"], r["run_duration_seconds"]) for r in envelope["data"]] == [("a", 720)]




def test_a_fetch_that_fails_on_a_later_page_writes_no_envelope(
    gather: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Page 1 answers with too few usable commits, page 2 fails. Half a
    listing must not be stored as the repository's CI record: the next run
    retries it."""
    first = [_pr_run(f"c{i // 50}", "cancelled", 5) for i in range(100)]

    def _gh(path: str, *flags: str):
        if "page=1" in flags:
            return {"workflow_runs": first}
        raise RuntimeError("gh api rc=1: HTTP 502")

    monkeypatch.setattr(gather, "_gh", _gh)
    monkeypatch.setattr(gather.time, "sleep", lambda _s: None)
    status = gather.gather_repo("acme", "widget", "pypi", collectors=frozenset({"actions_runs"}))
    assert status["actions_runs"] == "fail"
    assert not (gather.DATA / "actions_runs" / "acme__widget.json").exists()
