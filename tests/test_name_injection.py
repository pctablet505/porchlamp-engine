"""Name injection: a hostile owner or name must never reach a GraphQL text or a REST path (SEC-1 G)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from porchlamp.collect import ghcli, repo_object as ro_mod
from porchlamp.collect import validate
from porchlamp.collect.names import valid_name, valid_ref
from tests.collect.test_ghcli import FakeGitHub
from tests.helpers.gate import installed, loopback_gate

ROOT = Path(__file__).resolve().parents[1]
INJECTION = ('a", name: "b") { object(expression: "HEAD:README.md") { ... on Blob { text } } } '
             'r2: repository(owner: "c')
LEGIT = [("pallets", "flask"), ("my-org", "some_repo.js"), ("A.b", "x-y_z.1"), ("o", "a" * 100)]
FAKE_META = {"data": {"r": {"stargazerCount": 1, "forkCount": 2, "pushedAt": "2026-01-01T00:00:00Z",
                            "isArchived": False, "createdAt": "2020-01-01T00:00:00Z", "licenseInfo": {"spdxId": "MIT"},
                            "defaultBranchRef": {"name": "main"}, "pullRequests": {"nodes": []}}}}


@pytest.fixture
def gather(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("PORCHLAMP_DATA_ROOT", str(tmp_path))
    spec = importlib.util.spec_from_file_location("porchlamp_gather_names", ROOT / "scripts/gather.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["porchlamp_gather_names"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch):
    srv = FakeGitHub()
    srv.thread.start()
    monkeypatch.setattr(ghcli, "_BUDGET", {})
    monkeypatch.setattr(ghcli, "_sleep", lambda s: None)
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PORCHLAMP_GH", "http")
    monkeypatch.setenv("PORCHLAMP_GH_API", srv.url)
    monkeypatch.setenv("PORCHLAMP_ALLOW_TEST_API", "1")
    with installed(loopback_gate()):  # the HTTP gate admits the fake's loopback address for this test
        yield srv
    srv.close()


def test_names_module_accepts_only_plain_names():
    for ok in ("a", "A-b_c.9", "x" * 100, "..a", "a..", "...", ".hidden"):
        assert valid_name(ok), ok
    for bad in ("-XPOST", "--hostname=evil.example.com", "-", "--", "", ".", "..", "x" * 101, "a b", "a/b", "a\nb", "a\n", 'a"', "a;b", "é", "a\\b", "a%2e", None, 7, b"a",
                INJECTION, "../../../../user/repos?x"):
        assert not valid_name(bad), bad
    assert valid_ref("o", "n") and not valid_ref("o", "..") and not valid_ref("", "n") and not valid_ref("o", None)


def test_the_loader_skips_the_injection_line_written_straight_into_the_file(gather, capsys):
    lines = [json.dumps({"owner": o, "name": n, "ecosystem": "npm"}) for o, n in LEGIT[:2]]
    lines.insert(1, json.dumps({"owner": INJECTION, "name": "b", "ecosystem": "npm"}))
    lines.insert(2, json.dumps({"owner": "o", "name": "../../x", "ecosystem": "npm"}))
    lines.insert(3, "{not json")
    lines.insert(4, json.dumps({"owner": "o", "name": "..", "ecosystem": "npm"}))
    repos = gather.load_candidates(lines, None)
    assert repos == [("pallets", "flask", "npm"), ("my-org", "some_repo.js", "npm")]
    out = capsys.readouterr().out
    assert "skipped 4 invalid candidates" in out
    assert "object(expression" not in out and INJECTION not in out and '"' not in out
    assert out.count("\n") == 1


def test_the_loader_prints_at_most_five_examples(gather, capsys):
    lines = [json.dumps({"owner": f"bad owner {i}", "name": "n", "ecosystem": "npm"}) for i in range(12)]
    assert gather.load_candidates(lines, None) == []
    out = capsys.readouterr().out
    assert "skipped 12 invalid candidates" in out
    assert out.count("/n") == 5


def test_main_all_never_gathers_a_hostile_candidate(gather, monkeypatch, tmp_path):
    cands = tmp_path / "raw" / "candidates.jsonl"
    cands.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"owner": INJECTION, "name": "b", "ecosystem": "npm"}] + [
        {"owner": o, "name": n, "ecosystem": "npm"} for o, n in LEGIT]
    cands.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    monkeypatch.setattr(gather, "CANDIDATES", cands)
    seen = []
    monkeypatch.setattr(gather, "gather_repo", lambda owner, name, eco, **kw: seen.append((owner, name)) or {})
    assert gather.main(["--all"]) == 0
    assert seen == LEGIT


def test_main_repo_with_an_invalid_pair_exits_2_and_gathers_nothing(gather, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(gather, "gather_repo", lambda *a, **kw: seen.append(a) or {})
    for pair in ((INJECTION, "b"), ("o", ".."), ("o", "x/y"), ("", "n")):
        assert gather.main(["--repo", *pair]) == 2
    assert seen == []
    assert gather.main(["--repo", "my-org", "some_repo.js"]) == 0 and seen == [("my-org", "some_repo.js", "pypi")]


def _without_entry_check(monkeypatch):
    """The entry check rejects INJECTION first; take it away to prove the query layer is safe on its own."""
    monkeypatch.setattr(validate, "github_segment", lambda value, label="segment": value)


def test_gather_meta_query_carries_names_as_variables_not_text(gather, monkeypatch):
    _without_entry_check(monkeypatch)
    calls = []

    def run(args, timeout=90):
        calls.append(args)
        return subprocess.CompletedProcess(["gh", *args], 1, "", "")

    monkeypatch.setattr(gather.ghcli, "run", run)
    monkeypatch.setattr(gather.time, "sleep", lambda s: None)
    gather.gather_repo(INJECTION, "b", "npm", collectors=frozenset({"github_meta"}))
    (args,) = calls
    query = next(a for a in args if a.startswith("query="))
    assert INJECTION not in query and "object(expression" not in query and "HEAD:README" not in query
    assert "$owner" in query and "$name" in query and "%s" not in query
    assert f"owner={INJECTION}" in args and "name=b" in args


def test_repo_object_query_carries_names_as_variables_not_text(monkeypatch, tmp_path):
    _without_entry_check(monkeypatch)
    calls = []

    def run(args, timeout=90):
        calls.append(args)
        return subprocess.CompletedProcess(["gh", *args], 0, "{}", "")

    monkeypatch.setattr(ro_mod.ghcli, "run", run)
    assert ro_mod.collect_repo_object(tmp_path, INJECTION, "b", "main") == "fail"
    (args,) = calls
    query = next(a for a in args if a.startswith("query="))
    assert INJECTION not in query and "object(expression" not in query and "%s" not in query
    assert "$owner" in query and "$name" in query
    assert f"owner={INJECTION}" in args and "name=b" in args


@pytest.mark.parametrize("owner,name", LEGIT)
def test_legitimate_names_work_end_to_end_through_the_loader_and_both_queries(gather, fake, monkeypatch, tmp_path,
                                                                              owner, name):
    rows = [json.dumps({"owner": owner, "name": name, "ecosystem": "pypi"})]
    assert gather.load_candidates(rows, None) == [(owner, name, "pypi")]
    # gather.py's metadata query, through the real HTTP transport, to a real socket
    monkeypatch.setattr(gather.time, "sleep", lambda s: None)
    fake.replies = [(200, {}, json.dumps(FAKE_META))]
    status = gather.gather_repo(owner, name, "pypi", force=True, collectors=frozenset({"github_meta"}))
    assert status["github_meta"] == "ok"
    body = json.loads(fake.seen[-1]["body"])
    assert fake.seen[-1]["path"] == "/graphql"
    assert body["variables"] == {"owner": owner, "name": name}
    assert "$owner" in body["query"] and f'"{owner}"' not in body["query"] and f'"{name}"' not in body["query"]
    assert (tmp_path / "raw" / "github_meta" / f"{owner}__{name}.json").is_file()
    # repo_object's query
    fake.replies = [(200, {}, json.dumps({"data": {"r": {"isArchived": False, "pullRequests": {"totalCount": 3}}}}))]
    r, err = ro_mod._gh_graphql(ro_mod.REPO_OBJECT_QUERY, {"owner": owner, "name": name})
    assert r == {"isArchived": False, "pullRequests": {"totalCount": 3}} and err is None
    body = json.loads(fake.seen[-1]["body"])
    assert body["variables"] == {"owner": owner, "name": name} and f'"{owner}"' not in body["query"]


def test_validate_rules_refuse_flags_traversal_control_characters_and_other_hosts():
    assert validate.github_segment("my-org", "owner") == "my-org" and validate.github_segment(".github") == ".github"
    for bad in ("-XPOST", "--hostname=evil.example.com", "..", ".", "a/b", "a\nb", "", None):
        with pytest.raises(ValueError, match="invalid GitHub owner"):
            validate.github_segment(bad, "owner")
    assert validate.gh_api_path("repos/o/n/git/trees/main") == "repos/o/n/git/trees/main"
    for bad in ("-HAuthorization:x", "repos/o/../../admin", "repos/o/n\r\nX: 1", "repos/o/n\x00", "", None):
        with pytest.raises(ValueError, match="invalid gh api path"):
            validate.gh_api_path(bad)
    assert validate.relative_file_path("docs/CONTRIBUTING.md", "governance file path") == "docs/CONTRIBUTING.md"
    for bad in ("/etc/passwd", "../secret", "a/../b", "a\nb", ""):
        with pytest.raises(ValueError, match="invalid governance file path"):
            validate.relative_file_path(bad, "governance file path")
    ok = "https://raw.githubusercontent.com/o/n/HEAD/README.md"
    assert validate.https_url(ok, validate.RAW_GITHUB_HOSTS, "raw content") == ok
    for bad in ("http://raw.githubusercontent.com/o/n", "https://evil.example.com/o/n", "file:///etc/passwd",
                "https://raw.githubusercontent.com/o/n/../../x", "https://169.254.169.254/latest/meta-data/"):
        with pytest.raises(ValueError, match="disallowed raw content URL"):
            validate.https_url(bad, validate.RAW_GITHUB_HOSTS, "raw content")
