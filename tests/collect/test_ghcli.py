"""The ``ghcli`` transport: the ``gh`` subprocess by default, HTTPS with
``PORCHLAMP_GH=http``, and the rate-limit gate.

The HTTP tests talk to a fake GitHub on 127.0.0.1 (a ThreadingHTTPServer),
so no test reaches the network. A dummy token only.
"""

from __future__ import annotations

import gc
import json
import re
import socket
import sqlite3
import subprocess
import threading
import time
import tracemalloc
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from phone import httpgate
from porchlamp.collect import cost, ghcli, net
from tests.helpers.gate import installed, loopback_gate

ROOT = Path(__file__).resolve().parents[2]
DUMMY_TOKEN = "dummy-phone2-token-not-real-0001"
CANARY_TOKEN = "canary-token-not-real-fixb-7c1e9a"
NOW = 1000.0


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _serve(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else ""
        fake = self.server.fake  # type: ignore[attr-defined]
        fake.seen.append({"method": self.command, "path": self.path,
                          "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
        reply = fake.next_reply()
        status, headers, text = reply() if callable(reply) else reply
        data = text.encode("utf-8")
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _serve

    def log_message(self, format: str, *args: Any) -> None:
        pass


class FakeGitHub:
    """Replies are queued; the last queued reply repeats. Each reply is a
    ``(status, headers, body)`` triple or a callable returning one."""

    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []
        self.replies: list[Any] = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.fake = self  # type: ignore[attr-defined]
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def next_reply(self) -> Any:
        if len(self.replies) > 1:
            return self.replies.pop(0)
        if self.replies:
            return self.replies[0]
        return (200, {}, "{}")

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture(autouse=True)
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Isolate module state, pin the clock, and record every gate sleep."""
    ghcli.clear_etag_cache()
    ghcli.close_pool()
    ghcli.clear_token_cache()
    cost.clear_records()
    recorded: list[float] = []
    monkeypatch.setattr(ghcli, "_BUDGET", {})
    monkeypatch.setattr(ghcli, "_LIMITS", ghcli._BUDGET)
    monkeypatch.setattr(ghcli, "_sleep", recorded.append)
    monkeypatch.setattr(ghcli, "_now", lambda: NOW)
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "PORCHLAMP_GH_TOKENS", "PORCHLAMP_GH", "PORCHLAMP_GH_API", "PORCHLAMP_ALLOW_TEST_API",
                 "PORCHLAMP_RATE_FLOOR"):
        monkeypatch.delenv(name, raising=False)
    return recorded


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch):
    srv = FakeGitHub()
    srv.thread.start()
    monkeypatch.setenv("PORCHLAMP_GH", "http")
    monkeypatch.setenv("PORCHLAMP_GH_API", srv.url)
    monkeypatch.setenv("PORCHLAMP_ALLOW_TEST_API", "1")
    with installed(loopback_gate()):  # the HTTP gate admits the fake's loopback address for this test
        yield srv
    srv.close()


@pytest.fixture
def authed(fake: FakeGitHub, monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    """The fake counts as the one origin that receives the token (stands in for api.github.com), and a token is set."""
    monkeypatch.setattr(ghcli, "AUTH_BASE", fake.url)
    monkeypatch.setenv("GH_TOKEN", DUMMY_TOKEN)
    return fake


def _closed_port_url() -> str:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def _ok(headers: dict[str, str] | None = None, body: str = "{}") -> tuple:
    return (200, headers or {}, body)


@pytest.fixture
def costs_kept():
    """Cost records are kept in memory only on request (``--cost-log`` asks for them)."""
    cost.retain_records(True)
    yield
    cost.retain_records(False)


def _gh_spawns(monkeypatch: pytest.MonkeyPatch, token: str | None = None) -> list[list[str]]:
    """Replace ``subprocess.run`` and return the list of every argv it was asked to spawn.

    With ``token`` a ``gh auth token`` call prints it; without, there is no ``gh`` binary.
    """
    spawns: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        spawns.append(list(argv))
        if token is None:
            raise FileNotFoundError(2, "No such file or directory: 'gh'")
        return subprocess.CompletedProcess(argv, 0, token + "\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    return spawns


def test_default_mode_runs_the_gh_subprocess_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "out", "err")

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = ghcli.run(["api", "-X", "GET", "repos/x/y", "-f", "a=1"], timeout=7)
    assert calls == [(["gh", "api", "-X", "GET", "repos/x/y", "-f", "a=1"],
                      {"capture_output": True, "text": True, "timeout": 7})]
    assert (out.returncode, out.stdout, out.stderr) == (0, "out", "err")


def test_rest_get_fields_become_the_query_string_in_order(fake: FakeGitHub) -> None:
    ghcli.run(["api", "-X", "GET", "search/repos", "-f", "b=2", "-f", "a=hello world"])
    req = fake.seen[-1]
    parts = urllib.parse.urlsplit(req["path"])
    assert req["method"] == "GET" and parts.path == "/search/repos"
    assert urllib.parse.parse_qsl(parts.query) == [("b", "2"), ("a", "hello world")]
    assert req["body"] == ""


def test_rest_non_get_fields_become_a_json_body(fake: FakeGitHub) -> None:
    ghcli.run(["api", "-X", "PATCH", "repos/x/y", "-f", "name=z", "-F", "n=5", "-F", "ok=true"])
    req = fake.seen[-1]
    assert req["method"] == "PATCH" and req["path"] == "/repos/x/y"
    assert json.loads(req["body"]) == {"name": "z", "n": 5, "ok": True}
    assert req["headers"]["content-type"] == "application/json"


def test_graphql_posts_query_and_coerced_variables(fake: FakeGitHub) -> None:
    ghcli.run(["api", "graphql", "-f", "query=query { x }", "-F", "n=5",
               "-F", "ok=true", "-f", "s=5"])
    req = fake.seen[-1]
    assert req["method"] == "POST" and req["path"] == "/graphql"
    variables = json.loads(req["body"])["variables"]
    assert variables == {"n": 5, "ok": True, "s": "5"}
    assert type(variables["n"]) is int and variables["ok"] is True
    assert type(variables["s"]) is str
    assert json.loads(req["body"])["query"] == "query { x }"


def test_http_404_message_matches_the_governance_regex(fake: FakeGitHub) -> None:
    fake.replies = [(404, {}, json.dumps({"message": "Not Found"}))]
    out = ghcli.run(["api", "-X", "GET", "repos/x/gone"])
    assert out.returncode == 1
    assert out.stderr == "gh: Not Found (HTTP 404)\n"
    assert re.search(r"\(HTTP (\d+)\)", out.stderr).group(1) == "404"
    assert json.loads(out.stdout) == {"message": "Not Found"}


def test_http_502_graphql_stderr(fake: FakeGitHub) -> None:
    fake.replies = [(502, {}, "bad gateway")]
    out = ghcli.run(["api", "graphql", "-f", "query=query { x }"])
    assert (out.returncode, out.stdout, out.stderr) == (1, "bad gateway", "gh: HTTP 502\n")


def test_graphql_errors_exit_1_with_body(fake: FakeGitHub) -> None:
    body = json.dumps({"data": None, "errors": [{"message": "boom"}]})
    fake.replies = [_ok(body=body)]
    out = ghcli.run(["api", "graphql", "-f", "query=query { x }"])
    assert out.returncode == 1
    assert out.stdout == body


@pytest.mark.parametrize("argv", [
    ["repo", "view"],
    ["api"],
    ["api", "-X"],
    ["api", "--paginate", "repos/x/y"],
    ["api", "repos/x/y", "-f", "novalue"],
    ["api", "a", "b"],
    ["api", "graphql", "-f", "other=1"],
])
def test_unsupported_arguments_raise(fake: FakeGitHub, argv: list[str]) -> None:
    with pytest.raises(ValueError, match="^ghcli: unsupported gh arguments"):
        ghcli.run(argv)
    assert fake.seen == []


def test_authorization_header_present_only_with_a_token(authed: FakeGitHub,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GH_TOKEN")
    ghcli.run(["api", "repos/x/y"])
    plain = authed.seen[-1]["headers"]
    assert "authorization" not in plain
    assert plain["accept"] == "application/vnd.github+json"
    assert plain["x-github-api-version"] == "2022-11-28"
    assert plain["user-agent"] == "porchlamp-phone/1"
    monkeypatch.setenv("GITHUB_TOKEN", DUMMY_TOKEN)
    ghcli.run(["api", "repos/x/y"])
    assert authed.seen[-1]["headers"]["authorization"] == f"Bearer {DUMMY_TOKEN}"


def test_authorization_goes_to_api_github_com_only(fake: FakeGitHub, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", DUMMY_TOKEN)
    # The real default origin gets the header ...
    assert ghcli.DEFAULT_BASE == "https://api.github.com" == ghcli.AUTH_BASE
    assert ghcli._request_headers("https://api.github.com", DUMMY_TOKEN, False)["Authorization"] == f"Bearer {DUMMY_TOKEN}"
    # ... any other base (here the loopback test server) gets a request without it, and without the token anywhere.
    for base in (fake.url, "https://api.github.com.evil.example", "http://api.github.com", "https://example.org"):
        assert "Authorization" not in ghcli._request_headers(base, DUMMY_TOKEN, True)
    out = ghcli.run(["api", "repos/x/y"])
    assert out.returncode == 0 and len(fake.seen) == 1
    assert "authorization" not in fake.seen[0]["headers"]
    assert DUMMY_TOKEN not in json.dumps(fake.seen[0])


@pytest.mark.parametrize("override", [
    "http://example.com", "http://192.168.1.5:8000", "http://127.0.0.1.evil.example:80", "http://localhost.evil.example",
    "http://evil.example/127.0.0.1", "ftp://127.0.0.1", "file:///etc/passwd", "http://user@evil.example", "", "127.0.0.1"])
def test_gh_api_override_ignored_unless_test_variable_and_loopback(monkeypatch: pytest.MonkeyPatch,
                                                                    override: str) -> None:
    monkeypatch.setenv("PORCHLAMP_GH_API", override)
    assert ghcli._api_base() == "https://api.github.com"  # no second variable
    monkeypatch.setenv("PORCHLAMP_ALLOW_TEST_API", "1")
    assert ghcli._api_base() == "https://api.github.com"  # second variable, but not a loopback host
    monkeypatch.setenv("PORCHLAMP_ALLOW_TEST_API", "yes")
    monkeypatch.setenv("PORCHLAMP_GH_API", "http://127.0.0.1:9")
    assert ghcli._api_base() == "https://api.github.com"  # anything but exactly "1" is off


def test_gh_api_override_honoured_for_loopback_with_test_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORCHLAMP_GH_API", "http://127.0.0.1:9/")
    assert ghcli._api_base() == "https://api.github.com"
    monkeypatch.setenv("PORCHLAMP_ALLOW_TEST_API", "1")
    for url in ("http://127.0.0.1:9", "http://localhost:9", "http://[::1]:9", "https://127.0.0.1:9"):
        monkeypatch.setenv("PORCHLAMP_GH_API", url)
        assert ghcli._api_base() == url


def test_ignored_override_sends_the_request_to_api_github_com(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []

    def record(method: str, url: str, body: Any, headers: dict, timeout: float, args: list) -> tuple:
        sent.append(url)
        return 200, {}, "{}"

    monkeypatch.setattr(ghcli, "_send", record)
    monkeypatch.setenv("PORCHLAMP_GH", "http")
    monkeypatch.setenv("PORCHLAMP_GH_API", "http://203.0.113.9:9")  # no PORCHLAMP_ALLOW_TEST_API
    assert ghcli.run(["api", "repos/x/y"]).returncode == 0
    assert sent == ["https://api.github.com/repos/x/y"]


def test_token_never_in_output_or_exception(authed: FakeGitHub, monkeypatch: pytest.MonkeyPatch) -> None:
    authed.replies = [(500, {}, "server exploded")]
    out = ghcli.run(["api", "repos/x/y"])
    assert out.returncode == 1 and DUMMY_TOKEN not in out.stdout + out.stderr
    assert authed.seen[-1]["headers"]["authorization"] == f"Bearer {DUMMY_TOKEN}"

    monkeypatch.setenv("PORCHLAMP_GH_API", _closed_port_url())
    failed = ghcli.run(["api", "repos/x/y"])
    assert failed.returncode == 1 and DUMMY_TOKEN not in failed.stderr

    monkeypatch.setenv("PORCHLAMP_GH_API", authed.url)
    authed.replies = [lambda: (time.sleep(1.0), _ok())[1]]
    with pytest.raises(subprocess.TimeoutExpired) as info:
        ghcli.run(["api", "repos/x/y"], timeout=0.2)
    assert DUMMY_TOKEN not in repr(info.value)
    assert DUMMY_TOKEN not in str(info.value)


@pytest.mark.parametrize("code", [301, 302, 307, 308, 303])
def test_cross_host_redirect_is_refused_and_the_other_host_sees_nothing(authed: FakeGitHub, code: int,
                                                                         capfd: pytest.CaptureFixture) -> None:
    other = FakeGitHub()
    other.thread.start()
    try:
        authed.replies = [(code, {"Location": other.url + "/steal"}, "")]
        out = ghcli.run(["api", "repos/x/y"])
        time.sleep(0.2)
        assert other.seen == []
        assert len(authed.seen) == 1
        assert out.returncode == 1 and out.stdout == ""
        assert out.stderr == f"gh: redirect to another host refused (HTTP {code})\n"
        captured = capfd.readouterr()
        for text in (out.stdout, out.stderr, captured.out, captured.err, repr(out)):
            assert DUMMY_TOKEN not in text
    finally:
        other.close()


@pytest.mark.parametrize("location", [
    "https://127.0.0.1:{port}/x",          # another scheme, same host and port
    "//localhost:{port}/x",                 # another host name for the same machine
    "http://127.0.0.1:{port}@127.0.0.1:9/x",  # user-info trick
    "http://127.0.0.1:{port}\\@127.0.0.1:9/x",  # backslash trick
    "http://127.0.0.1:{other}/x",           # another port
    "",                                     # no Location at all
])
def test_odd_redirect_targets_are_refused(authed: FakeGitHub, location: str) -> None:
    authed.replies = [(302, {"Location": location.format(port=authed.server.server_address[1], other=1)} if location else {}, "")]
    out = ghcli.run(["api", "repos/x/y"])
    assert out.returncode == 1 and out.stderr == "gh: redirect to another host refused (HTTP 302)\n"
    assert DUMMY_TOKEN not in out.stderr
    assert len(authed.seen) == 1


def test_same_host_redirect_is_followed_with_the_same_headers(authed: FakeGitHub) -> None:
    for code, location in ((301, "/moved/y"), (302, authed.url + "/moved/y"), (307, "moved/y"), (308, "/moved/y")):
        authed.seen.clear()
        authed.replies = [(code, {"Location": location}, ""), _ok(body='{"ok": 1}')]
        out = ghcli.run(["api", "repos/x/y"])
        assert out.returncode == 0 and json.loads(out.stdout) == {"ok": 1}, code
        assert [r["path"] for r in authed.seen][-1].endswith("/moved/y")
        assert len(authed.seen) == 2
        assert authed.seen[0]["headers"] == authed.seen[1]["headers"]
        assert authed.seen[1]["headers"]["authorization"] == f"Bearer {DUMMY_TOKEN}"


def test_same_host_redirect_keeps_method_and_body(authed: FakeGitHub) -> None:
    authed.replies = [(307, {"Location": "/again"}, ""), _ok()]
    ghcli.run(["api", "-X", "POST", "repos/x/y/issues", "-f", "title=t"])
    assert [(r["method"], r["body"]) for r in authed.seen] == [("POST", '{"title": "t"}')] * 2


def test_redirect_hop_limit_is_three(authed: FakeGitHub) -> None:
    authed.replies = [(302, {"Location": "/loop"}, "")]  # the last reply repeats forever
    out = ghcli.run(["api", "repos/x/y"])
    assert len(authed.seen) == 4  # the request plus three followed hops, then the fourth redirect is refused
    assert out.returncode == 1 and out.stderr == "gh: too many redirects (HTTP 302)\n"
    authed.seen.clear()
    authed.replies = [(302, {"Location": "/a"}, ""), (302, {"Location": "/b"}, ""), (302, {"Location": "/c"}, ""),
                      _ok(body='{"done": true}')]
    out = ghcli.run(["api", "repos/x/y"])
    assert out.returncode == 0 and len(authed.seen) == 4


def test_timeout_raises_timeoutexpired(fake: FakeGitHub) -> None:
    fake.replies = [lambda: (time.sleep(1.0), _ok())[1]]
    with pytest.raises(subprocess.TimeoutExpired) as info:
        ghcli.run(["api", "repos/x/y"], timeout=0.2)
    assert info.value.cmd == ["gh", "api", "repos/x/y"]
    assert info.value.timeout == 0.2


def test_transport_error_returncode_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORCHLAMP_GH", "http")
    monkeypatch.setenv("PORCHLAMP_ALLOW_TEST_API", "1")
    monkeypatch.setenv("PORCHLAMP_GH_API", _closed_port_url())
    out = ghcli.run(["api", "repos/x/y"])
    assert out.returncode == 1
    assert out.stdout == ""
    assert out.stderr.startswith("gh: ") and out.stderr.endswith("\n")


def test_gate_waits_when_remaining_under_floor(fake: FakeGitHub, sleeps: list[float]) -> None:
    fake.replies = [_ok({"x-ratelimit-remaining": "10", "x-ratelimit-reset": str(int(NOW) + 100)})]
    ghcli.run(["api", "-X", "GET", "repos/x/y"])
    assert sleeps == []
    fake.replies = [_ok({"x-ratelimit-remaining": "10", "x-ratelimit-reset": str(int(NOW) + 100)})]
    out = ghcli.run(["api", "-X", "GET", "repos/x/z"])
    assert sleeps == [100 + 15]
    assert out.returncode == 0
    assert len(fake.seen) == 2


def test_gate_does_not_wait_above_floor(fake: FakeGitHub, sleeps: list[float]) -> None:
    reset = str(int(NOW) + 100)
    fake.replies = [_ok({"x-ratelimit-remaining": "500", "x-ratelimit-reset": reset})]
    ghcli.run(["api", "repos/x/y"])
    ghcli.run(["api", "repos/x/y"])
    assert sleeps == []
    assert len(fake.seen) == 2


def test_gate_resources_are_independent(fake: FakeGitHub, sleeps: list[float]) -> None:
    fake.replies = [_ok({"x-ratelimit-remaining": "10", "x-ratelimit-reset": str(int(NOW) + 100)})]
    ghcli.run(["api", "repos/x/y"])
    ghcli.run(["api", "graphql", "-f", "query=query { x }"])
    assert sleeps == []


def test_gate_search_floor_is_5(fake: FakeGitHub, sleeps: list[float]) -> None:
    reset = str(int(NOW) + 60)
    fake.replies = [_ok({"x-ratelimit-remaining": "5", "x-ratelimit-reset": reset})]
    ghcli.run(["api", "-X", "GET", "search/repositories", "-f", "q=x"])
    ghcli.run(["api", "-X", "GET", "search/repositories", "-f", "q=x"])
    assert sleeps == []
    fake.replies = [_ok({"x-ratelimit-remaining": "4", "x-ratelimit-reset": reset})]
    ghcli.run(["api", "-X", "GET", "search/repositories", "-f", "q=x"])
    ghcli.run(["api", "-X", "GET", "search/repositories", "-f", "q=x"])
    assert sleeps == [60 + 15]


def test_retry_after_429_waits_then_retries_twice_at_most(fake: FakeGitHub, sleeps: list[float]) -> None:
    limited = (429, {"retry-after": "1"}, json.dumps({"message": "secondary rate limit"}))
    fake.replies = [limited]
    out = ghcli.run(["api", "repos/x/y"])
    assert len(fake.seen) == 3
    assert len(sleeps) == 2
    assert out.returncode == 1
    assert out.stderr == "gh: secondary rate limit (HTTP 429)\n"

    fake.seen.clear()
    sleeps.clear()
    fake.replies = [limited, _ok(body='{"ok": 1}')]
    out = ghcli.run(["api", "repos/x/y"])
    assert len(fake.seen) == 2 and len(sleeps) == 1
    assert (out.returncode, out.stdout) == (0, '{"ok": 1}')


def test_seven_call_sites_use_ghcli() -> None:
    files = ["scripts/gather.py", "porchlamp/collect/governance.py",
             "porchlamp/collect/pike_signals.py", "porchlamp/collect/github_graphql.py",
             "porchlamp/collect/repo_object.py"]
    texts = {rel: (ROOT / rel).read_text(encoding="utf-8") for rel in files}
    assert all(texts.values()), "scanned set must not be empty"
    assert sum(t.count("ghcli.run(") for t in texts.values()) == 7
    for rel, text in texts.items():
        assert '["gh"' not in text, rel
        assert re.search(r"from porchlamp\.collect.* import .*\bghcli\b", text), rel


UNSAFE_PATHS = [
    "repos/o/../../../../user/repos", "repos/o/n/../../x", "../x", "./x", "repos/o/./n", "repos/o/n?per_page=100",
    "repos/o/n#frag", "repos/o\\n", "repos/o/n x", "repos/o/n\tx", "repos/o/n\nx", "repos/o/n\x00", "repos//x",
    "repos/o/n/", "", "/", "//evil.example/x", "repos/%2e%2e/x", "repos/%2E/n", "repos/o%2Fn/x", "repos/o%5Cn",
    "repos/o/" + "../../../../user/repos?x", "repos/o/né",
]


@pytest.mark.parametrize("path", UNSAFE_PATHS)
def test_unsafe_rest_path_sends_nothing(authed: FakeGitHub, path: str) -> None:
    out = ghcli.run(["api", path])
    assert (out.returncode, out.stdout, out.stderr) == (1, "", "gh: refusing an unsafe path\n")
    out = ghcli.run(["api", "-X", "POST", path, "-f", "a=1"])
    assert out.returncode == 1 and out.stderr == "gh: refusing an unsafe path\n"
    assert authed.seen == []


def test_hostile_repo_name_in_a_path_never_makes_a_request(authed: FakeGitHub) -> None:
    name = "../../../../user/repos?x"
    out = ghcli.run(["api", "-X", "GET", f"repos/octo/{name}/commits", "-f", "per_page=1"])
    assert out.returncode == 1 and out.stderr == "gh: refusing an unsafe path\n"
    assert authed.seen == []


@pytest.mark.parametrize("path", ["repos/my-org.io/some_repo.js/commits", "/repos/o/n/git/trees/feature/x",
                                  "repos/o/n/contents/a%20b.txt", "search/issues", "repos/A.b/x-y_z.1/pulls",
                                  "repos/o/n/compare/v1.0...v1.1", "users/octo~cat"])
def test_plain_rest_paths_still_go_through(authed: FakeGitHub, path: str) -> None:
    out = ghcli.run(["api", path])
    assert out.returncode == 0 and len(authed.seen) == 1
    assert authed.seen[0]["path"] == "/" + path.lstrip("/")


def test_graphql_is_not_subject_to_the_rest_path_check(authed: FakeGitHub) -> None:
    out = ghcli.run(["api", "graphql", "-f", "query={ viewer { login } }", "-f", "owner=../x?y"])
    assert out.returncode == 0 and json.loads(authed.seen[0]["body"])["variables"] == {"owner": "../x?y"}


def test_etag_conditional_get_returns_cached_payload_and_records_status_304(
    fake: FakeGitHub, tmp_path: Path, costs_kept: None
) -> None:
    ghcli.configure_etag_cache(tmp_path / "etag.sqlite")
    cached_payload = json.dumps({"name": "octocat", "id": 123})
    etag = 'W/"abc"'

    # First GET returns 200 with ETag header
    fake.replies = [(200, {"ETag": etag}, cached_payload)]
    out1 = ghcli.run(["api", "repos/octo/cat"])
    assert out1.returncode == 0
    assert out1.stdout == cached_payload
    assert len(fake.seen) == 1
    assert "if-none-match" not in fake.seen[0]["headers"]

    records1 = cost.get_records()
    assert len(records1) == 1
    assert records1[0].status == 200
    assert records1[0].status_304 is False
    assert records1[0].endpoint == "/repos/octo/cat"

    # Second GET: server returns 304 Not Modified
    fake.replies = [(304, {"x-ratelimit-remaining": "4900", "x-ratelimit-reset": str(int(NOW) + 3600)}, "")]
    out2 = ghcli.run(["api", "repos/octo/cat"])
    assert out2.returncode == 0
    assert out2.stdout == cached_payload
    assert len(fake.seen) == 2
    assert fake.seen[1]["headers"]["if-none-match"] == etag

    records2 = cost.get_records()
    assert len(records2) == 2
    last_rec = records2[-1]
    assert last_rec.status == 304
    assert last_rec.status_304 is True
    assert last_rec.bytes_in == 0
    assert last_rec.endpoint == "/repos/octo/cat"
    assert ghcli._BUDGET["core"][0] == 4900


def test_persistent_keep_alive_connection_reuse_and_recovery(fake: FakeGitHub, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(net, "_gate", None)
    ghcli.close_pool()

    fake.replies = [_ok(body='{"call": 1}'), _ok(body='{"call": 2}')]
    out1 = ghcli.run(["api", "repos/x/y"])
    assert out1.returncode == 0 and json.loads(out1.stdout) == {"call": 1}
    assert len(ghcli._POOL) == 1

    scheme, host, port = ghcli._origin(fake.url)
    if port is None:
        port = 80
    pooled_conn = ghcli._POOL.get((scheme, host, port))
    assert pooled_conn is not None
    orig_sock = pooled_conn.sock
    assert orig_sock is not None

    out2 = ghcli.run(["api", "repos/x/z"])
    assert out2.returncode == 0 and json.loads(out2.stdout) == {"call": 2}
    assert ghcli._POOL.get((scheme, host, port)) is pooled_conn
    assert pooled_conn.sock is orig_sock

    # Recovery from abrupt socket close
    orig_sock.close()
    fake.replies = [_ok(body='{"call": 3}')]
    out3 = ghcli.run(["api", "repos/x/recovered"])
    assert out3.returncode == 0 and json.loads(out3.stdout) == {"call": 3}
    new_conn = ghcli._POOL.get((scheme, host, port))
    assert new_conn is not None
    assert new_conn is not pooled_conn

    # Clean close recovery
    new_conn.close()
    fake.replies = [_ok(body='{"call": 4}')]
    out4 = ghcli.run(["api", "repos/x/clean_recovered"])
    assert out4.returncode == 0 and json.loads(out4.stdout) == {"call": 4}


def test_multi_token_rotation_when_quota_exhausted(fake: FakeGitHub, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ghcli, "AUTH_BASE", fake.url)
    monkeypatch.setenv("PORCHLAMP_GH_TOKENS", "tok_a,tok_b")

    # tok_a is selected first; reports low remaining quota
    fake.replies = [
        (200, {"x-ratelimit-remaining": "10", "x-ratelimit-reset": str(int(NOW) + 300)}, '{"token": "a"}')
    ]
    out1 = ghcli.run(["api", "repos/x/y"])
    assert out1.returncode == 0
    assert fake.seen[-1]["headers"]["authorization"] == "Bearer tok_a"

    # tok_a is below floor; rotates to tok_b
    fake.replies = [
        (200, {"x-ratelimit-remaining": "4950", "x-ratelimit-reset": str(int(NOW) + 3600)}, '{"token": "b"}')
    ]
    out2 = ghcli.run(["api", "repos/x/y"])
    assert out2.returncode == 0
    assert fake.seen[-1]["headers"]["authorization"] == "Bearer tok_b"

    # tok_b has higher quota; continues with tok_b
    fake.replies = [
        (200, {"x-ratelimit-remaining": "4949", "x-ratelimit-reset": str(int(NOW) + 3600)}, '{"token": "b2"}')
    ]
    out3 = ghcli.run(["api", "repos/x/y"])
    assert out3.returncode == 0
    assert fake.seen[-1]["headers"]["authorization"] == "Bearer tok_b"

    # If all tokens are below floor, picks token with earliest reset
    fake.replies = [
        (200, {"x-ratelimit-remaining": "5", "x-ratelimit-reset": str(int(NOW) + 100)}, '{"token": "b3"}')
    ]
    ghcli.run(["api", "repos/x/y"])
    assert ghcli._select_token("core", ghcli._configured_tokens(fake.url)) == "tok_b"


def test_cached_gh_auth_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # A workstation run with the checkout on PYTHONPATH imports ``phone`` (``net._gate`` is set) but installs no gate.
    monkeypatch.setattr(httpgate, "_GATE", httpgate._IMPORT_GATE)
    assert net._gate is httpgate and not httpgate.installed()
    calls = []

    def fake_subprocess_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv == ["gh", "auth", "token"]:
            return subprocess.CompletedProcess(argv, 0, "token-from-gh-cli\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    tokens = ghcli._configured_tokens(ghcli.DEFAULT_BASE)
    assert tokens == ["token-from-gh-cli"]
    assert calls == [["gh", "auth", "token"]]

    # Cached on subsequent lookup
    tokens2 = ghcli._configured_tokens(ghcli.DEFAULT_BASE)
    assert tokens2 == ["token-from-gh-cli"]
    assert len(calls) == 1

    # Local test base never executes gh auth token
    calls.clear()
    ghcli.clear_token_cache()
    tokens_local = ghcli._configured_tokens("http://127.0.0.1:8000")
    assert tokens_local == []
    assert len(calls) == 0


def test_sqlite_etag_cache_persists_across_in_memory_reset(fake: FakeGitHub, tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "http_cache.sqlite"
    ghcli.configure_etag_cache(db_path)

    cached_payload = json.dumps({"name": "persisted", "id": 999})
    etag = 'W/"sqlite-etag-1"'
    fake.replies = [(200, {"ETag": etag}, cached_payload)]
    out1 = ghcli.run(["api", "repos/octo/persisted"])
    assert out1.returncode == 0
    assert out1.stdout == cached_payload

    # Simulate process restart: forget the configured DB, then re-configure it
    ghcli.clear_etag_cache()
    assert ghcli._etag_get(f"{fake.url}/repos/octo/persisted") is None
    ghcli.configure_etag_cache(db_path)

    fake.replies = [(304, {"x-ratelimit-remaining": "4800", "x-ratelimit-reset": str(int(NOW) + 3600)}, "")]
    out2 = ghcli.run(["api", "repos/octo/persisted"])
    assert out2.returncode == 0
    assert out2.stdout == cached_payload
    assert fake.seen[-1]["headers"]["if-none-match"] == etag


def test_concurrent_pooled_requests_do_not_serialize_under_pool_lock(
    fake: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setattr(net, "_gate", None)
    ghcli.close_pool()

    fake.replies = [_ok(body='{"ok": true}')]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: ghcli.run(["api", f"repos/x/r{i}"]), range(8)))
    assert all(r.returncode == 0 for r in results)
    assert len(ghcli._POOL) == 1
    ghcli.close_pool()
    assert ghcli._POOL == {} and ghcli._POOL_IDLE == {}




@pytest.mark.parametrize("with_db", [False, True], ids=["no-db", "db"])
def test_etag_cache_keeps_no_response_body_in_memory(fake: FakeGitHub, tmp_path: Path, with_db: bool) -> None:
    """400 distinct GETs of a 128 KB body (51 MB in all) leave under 8 MiB behind, with or without a database."""
    if with_db:
        ghcli.configure_etag_cache(tmp_path / "etag.sqlite")
    body = json.dumps({"tree": "x" * (128 * 1024)})
    fake.replies = [(200, {"ETag": '"tree-etag"'}, body)]
    gc.collect()
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        for i in range(400):
            out = ghcli.run(["api", f"repos/o/r{i}/git/trees/main"])
            assert out.returncode == 0 and len(out.stdout) == len(body)
        del out
        gc.collect()
        retained = tracemalloc.get_traced_memory()[0] - before
    finally:
        tracemalloc.stop()
    assert retained < 8 * 2**20, f"{retained / 2**20:.1f} MiB retained after 400 GETs"


def test_default_run_keeps_no_cost_record(fake: FakeGitHub) -> None:
    fake.replies = [_ok()]
    for i in range(3):
        assert ghcli.run(["api", f"repos/o/r{i}"]).returncode == 0
    assert cost.get_records() == []


def test_without_a_database_no_if_none_match_is_sent(fake: FakeGitHub) -> None:
    fake.replies = [(200, {"ETag": '"e1"'}, '{"n": 1}')]
    for _ in range(2):
        assert ghcli.run(["api", "repos/octo/cat"]).stdout == '{"n": 1}'
    assert [("if-none-match" in seen["headers"]) for seen in fake.seen] == [False, False]


def test_a_304_returns_the_body_stored_in_the_database(fake: FakeGitHub, tmp_path: Path) -> None:
    ghcli.configure_etag_cache(tmp_path / "etag.sqlite")
    fake.replies = [(200, {"ETag": '"e1"'}, '{"n": 1}'), (304, {}, "")]
    first = ghcli.run(["api", "repos/octo/cat"])
    second = ghcli.run(["api", "repos/octo/cat"])
    assert (first.returncode, first.stdout) == (0, '{"n": 1}')
    assert (second.returncode, second.stdout) == (0, '{"n": 1}')
    assert fake.seen[1]["headers"]["if-none-match"] == '"e1"'


def test_gh_cli_fallback_never_runs_behind_the_gate(authed: FakeGitHub, monkeypatch: pytest.MonkeyPatch) -> None:
    """A gate is installed (the phone): a token that passed no token check is never taken from ``gh``."""
    monkeypatch.delenv("GH_TOKEN")
    monkeypatch.setattr(ghcli, "DEFAULT_BASE", authed.url)
    spawns = _gh_spawns(monkeypatch, token="token-from-gh-cli")
    with installed(loopback_gate()):
        assert net._gate.installed()
        for i in range(2):
            assert ghcli.run(["api", f"repos/x/y{i}"]).returncode == 0
    assert spawns == []
    assert len(authed.seen) == 2
    assert all("authorization" not in seen["headers"] for seen in authed.seen)


def test_a_failed_gh_cli_lookup_is_cached_for_the_process(authed: FakeGitHub, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GH_TOKEN")
    monkeypatch.setattr(net, "_gate", None)
    ghcli.close_pool()
    monkeypatch.setattr(ghcli, "DEFAULT_BASE", authed.url)
    spawns = _gh_spawns(monkeypatch)
    for i in range(3):
        assert ghcli.run(["api", f"repos/x/y{i}"]).returncode == 0
    assert spawns == [["gh", "auth", "token"]]
    assert len(authed.seen) == 3


def test_each_request_looks_up_its_tokens_once(authed: FakeGitHub, monkeypatch: pytest.MonkeyPatch) -> None:
    looked_up: list[str] = []
    real = ghcli._configured_tokens

    def counting(base: str) -> list[str]:
        looked_up.append(base)
        return real(base)

    monkeypatch.setattr(ghcli, "_configured_tokens", counting)
    assert ghcli.run(["api", "repos/x/y"]).returncode == 0
    assert looked_up == [authed.url]


def test_a_token_reaches_no_file_and_no_log_line(
    authed: FakeGitHub, tmp_path: Path, costs_kept: None,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture,
) -> None:
    """With the ETag database and the cost log in use, the token sits in no byte written under the temp dir."""
    monkeypatch.setenv("GH_TOKEN", CANARY_TOKEN)
    db_path = tmp_path / "state" / "etag.sqlite"
    ghcli.configure_etag_cache(db_path)
    limits = {"x-ratelimit-remaining": "4900", "x-ratelimit-reset": str(int(NOW) + 3600)}
    authed.replies = [(200, {"ETag": '"e1"', **limits}, '{"ok": true}')]
    for i in range(3):
        ghcli.run(["api", f"repos/o/r{i}"])
    authed.replies = [(304, limits, "")]
    ghcli.run(["api", "repos/o/r0"])
    authed.replies = [(500, {}, "server exploded")]
    ghcli.run(["api", "repos/o/failing"])
    authed.replies = [_ok(body='{"data": {}}')]
    ghcli.run(["api", "graphql", "-f", "query={ viewer { login } }"])
    cost_log = tmp_path / "cost.jsonl"
    cost.write_cost_log(cost_log)

    assert len(authed.seen) == 6
    assert all(seen["headers"]["authorization"] == f"Bearer {CANARY_TOKEN}" for seen in authed.seen)
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert {db_path, cost_log} <= set(written)
    assert len(cost_log.read_text(encoding="utf-8").splitlines()) == 6
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM http_etag_cache").fetchone()[0] == 3
    for path in written:
        assert CANARY_TOKEN.encode() not in path.read_bytes(), path.name
    captured = capsys.readouterr()
    assert CANARY_TOKEN not in captured.out + captured.err + caplog.text
