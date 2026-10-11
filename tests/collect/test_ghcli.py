"""The ``ghcli`` transport: the ``gh`` subprocess by default, HTTPS with
``PORCHLAMP_GH=http``, and the rate-limit gate.

The HTTP tests talk to a fake GitHub on 127.0.0.1 (a ThreadingHTTPServer),
so no test reaches the network. A dummy token only.
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from porchlamp.collect import ghcli
from tests.helpers.gate import installed, loopback_gate

ROOT = Path(__file__).resolve().parents[2]
DUMMY_TOKEN = "dummy-phone2-token-not-real-0001"
NOW = 1000.0


class _Handler(BaseHTTPRequestHandler):
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
    recorded: list[float] = []
    monkeypatch.setattr(ghcli, "_BUDGET", {})
    monkeypatch.setattr(ghcli, "_sleep", recorded.append)
    monkeypatch.setattr(ghcli, "_now", lambda: NOW)
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "PORCHLAMP_GH", "PORCHLAMP_GH_API", "PORCHLAMP_ALLOW_TEST_API",
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
        assert "from porchlamp.collect import ghcli" in text, rel


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
