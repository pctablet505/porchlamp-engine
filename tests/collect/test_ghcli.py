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
    monkeypatch.setattr(ghcli, "_THROTTLE", ghcli._Throttle())  # a hold or strike left by one test never charges the next
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
    blocked = ghcli.run(["api", "-X", "PATCH", "repos/x/y", "-f", "name=z", "-F", "n=5", "-F", "ok=true"])
    assert blocked.returncode == 1 and fake.seen == []
    with installed(loopback_gate(allow_collect_mutations=True)):
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
    blocked = ghcli.run(["api", "-X", "POST", "repos/x/y/issues", "-f", "title=t"])
    assert blocked.returncode == 1 and authed.seen == []
    authed.replies = [(307, {"Location": "/again"}, ""), _ok()]
    with installed(loopback_gate(allow_collect_mutations=True)):
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
    # D4/RB5: the first wait is the hold the third refusal above left (240 s), served before this call sends; the
    # second is this call's own refusal, the fourth signal of the day (60 s doubled three times).
    assert len(fake.seen) == 2 and sleeps == [240, 480]
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


# ---- D4: the throttle (requests in flight, the shared hold, the drop to one at a time, pacing, the pin) ----------
GQL_ARGS = ["api", "graphql", "-f", "query={ viewer { login } }"]
SECONDARY_BODY = json.dumps({"message": "You have exceeded a secondary rate limit. Please wait a few minutes."})


@pytest.fixture
def throttle(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """A fresh process-wide throttle for this test (none exists before D4), the concurrency settings unset, and every
    event the throttle tells (``on_event``) recorded in the returned list."""
    if hasattr(ghcli, "_Throttle"):
        monkeypatch.setattr(ghcli, "_THROTTLE", ghcli._Throttle())
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(ghcli, "on_event", lambda level, text: events.append((level, text)), raising=False)
    for name in ("PORCHLAMP_GH_INFLIGHT", "PORCHLAMP_GH_PPM"):
        monkeypatch.setenv(name, "unset")   # recorded, so the variable is removed again after the test
        monkeypatch.delenv(name)
    return events


class Clock:
    """A test clock: ``sleep`` moves it, so a wait is served at once and its length is recorded."""

    def __init__(self, t: float = NOW) -> None:
        self.t = t
        self.sleeps: list[float] = []
        self._lock = threading.Lock()

    def now(self) -> float:
        with self._lock:
            return self.t

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.sleeps.append(seconds)
            self.t += seconds


class Wire:
    """Stands in for ``ghcli._send_following``: ``reply(n, thread name)`` answers the n-th request (1-based), and the
    requests in flight are counted. With ``hold`` set, each request stays open until ``hold`` is set."""

    def __init__(self, reply=None, hold: threading.Event | None = None) -> None:
        self.reply = reply or (lambda n, who: (200, {}, '{"data": {}}'))
        self.hold = hold
        self.lock = threading.Lock()
        self.n = 0
        self.in_flight = 0
        self.max_in_flight = 0
        self.order: list[tuple] = []

    def __call__(self, method: str, url: str, base: str, body: Any, headers: dict, timeout: float,
                 args: list) -> tuple:
        who = threading.current_thread().name
        with self.lock:
            self.n += 1
            n = self.n
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            self.order.append(("send", who))
        try:
            if self.hold is not None:
                self.hold.wait(10)
            return self.reply(n, who)
        finally:
            with self.lock:
                self.in_flight -= 1


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    """The HTTPS transport with ``_send_following`` replaced by a :class:`Wire` (a dummy token, nothing is sent)."""
    monkeypatch.setenv("PORCHLAMP_GH", "http")
    monkeypatch.setenv("GH_TOKEN", DUMMY_TOKEN)
    fake_wire = Wire()
    monkeypatch.setattr(ghcli, "_send_following", fake_wire)
    return fake_wire


def _wait_until(predicate, seconds: float = 10.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _most_at_once(wire: Wire, expect: int, threads: int = 6, args: list[str] | None = None) -> tuple[int, int]:
    """Send ``threads`` requests at once with every request held open; return (requests open once ``expect`` of them
    are open and a further 0.3 s has passed, the most ever open). A cap that leaks lets more in during the 0.3 s."""
    gate = threading.Event()
    wire.hold, wire.max_in_flight = gate, 0
    workers = [threading.Thread(target=ghcli.run, args=(args or GQL_ARGS,), name=f"w{i}", daemon=True)
               for i in range(threads)]
    for t in workers:
        t.start()
    try:
        assert _wait_until(lambda: wire.in_flight >= expect), f"never {expect} open (open: {wire.in_flight})"
        time.sleep(0.3)
        open_now = wire.in_flight
    finally:
        gate.set()
        for t in workers:
            t.join(10)
        wire.hold = None
    assert not any(t.is_alive() for t in workers)
    return open_now, wire.max_in_flight


def test_secondary_403_without_retry_after_waits_a_minute(fake: FakeGitHub, sleeps: list[float],
                                                          throttle: list) -> None:
    """T1: GitHub's secondary limit as a 403 with calls left and no ``retry-after``: wait a minute, then retry."""
    limited = (403, {"x-ratelimit-remaining": "4000", "x-ratelimit-reset": str(int(NOW) + 3000)}, SECONDARY_BODY)
    fake.replies = [limited, _ok(body='{"ok": 1}')]
    out = ghcli.run(["api", "repos/x/y"])
    assert sleeps == [60]
    assert len(fake.seen) == 2
    assert (out.returncode, out.stdout) == (0, '{"ok": 1}')


def test_graphql_200_with_secondary_limit_error_waits(fake: FakeGitHub, sleeps: list[float], throttle: list) -> None:
    """T2: a GraphQL answer with status 200 whose error names a secondary limit is a limit: wait, then retry. An
    error that names no limit is the caller's, as before: no wait."""
    other = (200, {}, json.dumps({"data": None, "errors": [{"message": "Could not resolve to a Repository."}]}))
    fake.replies = [other]
    assert ghcli.run(GQL_ARGS).returncode == 1
    assert sleeps == [] and len(fake.seen) == 1
    limited = (200, {}, json.dumps({"errors": [{"message": "You have exceeded a secondary rate limit."}]}))
    fake.replies = [limited, _ok(body='{"data": {"viewer": {"login": "x"}}}')]
    out = ghcli.run(GQL_ARGS)
    assert sleeps == [60]
    assert len(fake.seen) == 3
    assert out.returncode == 0


def test_hold_set_by_one_thread_blocks_the_others(wire: Wire, throttle: list,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """T3: A gets a 429 (retry after 60 s) and waits; B, started during A's wait, waits the same hold before it sends
    anything. The clock does not move: each waiter serves the hold once."""
    order: list[tuple] = []
    a_waiting, release_a = threading.Event(), threading.Event()

    def reply(n: int, who: str) -> tuple:
        if n == 1:
            return (429, {"retry-after": "60"}, SECONDARY_BODY)
        return (200, {}, '{"data": {}}')

    def sleep(seconds: float) -> None:
        who = threading.current_thread().name
        order.append(("wait", who, seconds))
        if who == "A":
            a_waiting.set()
            release_a.wait(10)

    wire.reply = reply
    wire.order = order
    monkeypatch.setattr(ghcli, "_sleep", sleep)
    a = threading.Thread(target=ghcli.run, args=(GQL_ARGS,), name="A", daemon=True)
    a.start()
    assert a_waiting.wait(10)
    b = threading.Thread(target=ghcli.run, args=(GQL_ARGS,), name="B", daemon=True)
    b.start()
    b.join(10)
    release_a.set()
    a.join(10)
    assert not a.is_alive() and not b.is_alive()
    assert [e for e in order if e[1] == "B"] == [("wait", "B", 60), ("send", "B")]
    assert [e for e in order if e[1] == "A"] == [("send", "A"), ("wait", "A", 60), ("send", "A")]


def test_inflight_cap_follows_env_and_drops_to_one_after_a_signal(wire: Wire, throttle: list,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """T4: unset means one request at a time; ``PORCHLAMP_GH_INFLIGHT=3`` keeps three open; a secondary limit drops
    it to one (for 30 minutes; the clock does not move here)."""
    assert _most_at_once(wire, expect=1) == (1, 1)
    monkeypatch.setenv("PORCHLAMP_GH_INFLIGHT", "3")
    assert _most_at_once(wire, expect=3) == (3, 3)
    monkeypatch.setenv("PORCHLAMP_GH_INFLIGHT", "9")   # clamped to 3
    assert _most_at_once(wire, expect=3) == (3, 3)
    monkeypatch.setenv("PORCHLAMP_GH_INFLIGHT", "3")
    first = wire.n + 1
    wire.reply = lambda n, who: (403, {"x-ratelimit-remaining": "4000"}, SECONDARY_BODY) if n == first \
        else (200, {}, '{"data": {}}')
    assert ghcli.run(GQL_ARGS).returncode == 0
    assert _most_at_once(wire, expect=1) == (1, 1)


def test_six_timeouts_in_five_minutes_drop_to_one(wire: Wire, throttle: list,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """T5: six GitHub 502/504 answers within 300 s give one request at a time for 30 minutes (no hold); five, or six
    spread over more than 300 s, do not."""
    clock = Clock()
    monkeypatch.setattr(ghcli, "_now", clock.now)
    monkeypatch.setattr(ghcli, "_sleep", clock.sleep)
    monkeypatch.setenv("PORCHLAMP_GH_INFLIGHT", "3")
    timeout = (504, {}, "Gateway Timeout")
    wire.reply = lambda n, who: timeout
    for i in range(5):
        clock.t = NOW + 50 * i
        assert ghcli.run(GQL_ARGS).returncode == 1
    wire.reply = lambda n, who: (200, {}, '{"data": {}}')
    assert _most_at_once(wire, expect=3) == (3, 3)
    wire.reply = lambda n, who: (502, {}, "Bad Gateway")
    clock.t = NOW + 250                                   # the sixth within 300 s of the first
    assert ghcli.run(GQL_ARGS).returncode == 1
    assert ("log", "6 GitHub timeouts in 5 min: one request at a time for 30 min") in throttle
    wire.reply = lambda n, who: (200, {}, '{"data": {}}')
    assert _most_at_once(wire, expect=1) == (1, 1)
    assert clock.sleeps == []                             # no hold: nobody waited
    clock.t = NOW + 250 + 1800                            # the 30 minutes are over
    assert _most_at_once(wire, expect=3) == (3, 3)
    assert ("log", "back to 3 requests at once") in throttle

    # six spread over 305 s: never six inside one 300 s window
    if hasattr(ghcli, "_Throttle"):
        monkeypatch.setattr(ghcli, "_THROTTLE", ghcli._Throttle())
    wire.reply = lambda n, who: timeout
    for i in range(6):
        clock.t = NOW + 10_000 + 61 * i
        ghcli.run(GQL_ARGS)
    wire.reply = lambda n, who: (200, {}, '{"data": {}}')
    assert _most_at_once(wire, expect=3) == (3, 3)


def test_graphql_points_per_minute_pacing(wire: Wire, throttle: list, monkeypatch: pytest.MonkeyPatch) -> None:
    """T6: ``PORCHLAMP_GH_PPM=3``: three 1-point GraphQL calls go at once, the fourth waits until the first has left
    the last 60 s. REST calls are not paced."""
    clock = Clock()
    monkeypatch.setattr(ghcli, "_now", clock.now)
    monkeypatch.setattr(ghcli, "_sleep", clock.sleep)
    monkeypatch.setenv("PORCHLAMP_GH_PPM", "3")
    wire.reply = lambda n, who: (200, {}, json.dumps({"data": {"rateLimit": {"cost": 1, "remaining": 4000}}}))
    for _ in range(3):
        assert ghcli.run(GQL_ARGS).returncode == 0
    assert clock.sleeps == []
    assert ghcli.run(["api", "repos/x/y"]).returncode == 0
    assert clock.sleeps == []
    assert ghcli.run(GQL_ARGS).returncode == 0
    assert clock.sleeps == [60]
    assert wire.n == 5


def test_three_signals_in_a_day_pin_one_at_a_time(wire: Wire, throttle: list,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """T7: the third secondary limit within 24 h, with more than one request allowed at once, pins one at a time for
    the rest of the process and tells it once as an error; the waits double (60, 120, 240 s)."""
    clock = Clock()
    monkeypatch.setattr(ghcli, "_now", clock.now)
    monkeypatch.setattr(ghcli, "_sleep", clock.sleep)
    monkeypatch.setenv("PORCHLAMP_GH_INFLIGHT", "3")
    refused: set[int] = set()

    def reply(n: int, who: str) -> tuple:
        if n in refused:
            return (403, {"retry-after": "30"}, SECONDARY_BODY)
        return (200, {}, '{"data": {}}')

    wire.reply = reply
    for k in range(3):
        clock.t = NOW + k * 2 * 3600
        refused.add(wire.n + 1)
        assert ghcli.run(GQL_ARGS).returncode == 0
    assert clock.sleeps == [60, 120, 240]
    assert [level for level, _ in throttle if level == "err"] == ["err"]
    clock.t = NOW + 4 * 3600 + 5 * 3600                   # past every hold and every 30-minute (doubled) drop
    assert _most_at_once(wire, expect=1) == (1, 1)

    # one request at a time already: three signals do not pin
    if hasattr(ghcli, "_Throttle"):
        monkeypatch.setattr(ghcli, "_THROTTLE", ghcli._Throttle())
    throttle.clear()
    monkeypatch.delenv("PORCHLAMP_GH_INFLIGHT")
    for k in range(3):
        clock.t = NOW + 20 * 3600 + k * 3600
        refused.add(wire.n + 1)
        assert ghcli.run(GQL_ARGS).returncode == 0
    assert [level for level, _ in throttle if level == "err"] == []


def test_the_hourly_line_counts_requests_points_and_the_most_at_once(wire: Wire, throttle: list,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """Once an hour the throttle tells how many requests and GraphQL points the last hour took, and the most it had
    in flight at once, so the phone measures its own rate."""
    clock = Clock()
    monkeypatch.setattr(ghcli, "_now", clock.now)
    monkeypatch.setenv("PORCHLAMP_GH_INFLIGHT", "2")
    wire.reply = lambda n, who: (200, {}, json.dumps({"data": {"rateLimit": {"cost": 2, "remaining": 4000}}}))
    assert _most_at_once(wire, expect=2, threads=4) == (2, 2)
    clock.t = NOW + 1800
    assert ghcli.run(["api", "repos/x/y"]).returncode == 0
    assert not any(text.startswith("GitHub, last hour") for _, text in throttle)
    clock.t = NOW + 3600
    assert ghcli.run(GQL_ARGS).returncode == 0
    assert ("log", "GitHub, last hour: 6 requests, 10 points, at most 2 at once") in throttle


def test_a_hold_is_told_without_echoing_the_header(wire: Wire, throttle: list, sleeps: list[float],
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    """The hold's line names the status and the wait GitHub asked for as a number; a ``retry-after`` that is not a
    number is not echoed into the log (it waits the minute)."""
    replies = iter([(429, {"retry-after": "120"}, SECONDARY_BODY), (200, {}, '{"data": {}}'),
                    (429, {"retry-after": "soon <b>x</b>"}, SECONDARY_BODY), (200, {}, '{"data": {}}')])
    wire.reply = lambda n, who: next(replies)
    assert ghcli.run(GQL_ARGS).returncode == 0 and ghcli.run(GQL_ARGS).returncode == 0
    texts = [text for level, text in throttle if level == "warn"]
    assert texts[0].startswith("GitHub secondary limit (HTTP 429, retry-after 120 s): every request waits 120 s")
    assert texts[1].startswith("GitHub secondary limit (HTTP 429, retry-after unreadable): every request waits 120 s")
    assert sleeps == [120, 120]                     # the second is the minute, doubled for the second signal
    assert "soon" not in capsys.readouterr().err + "".join(texts)


# ---- RB5: every thread obeys a hold, a primary pause never lifts a longer hold, one burst counts once ----------
def test_a_thread_that_exhausted_its_retries_waits_its_hold_before_its_next_call(
        wire: Wire, throttle: list, monkeypatch: pytest.MonkeyPatch) -> None:
    """RB5 S1: a call refused three times leaves a hold nobody waited (it gives up instead of retrying). The same
    thread's next call waits that hold before it sends anything."""
    order: list[tuple] = []
    wire.order = order
    monkeypatch.setattr(ghcli, "_sleep", lambda seconds: order.append(("wait", seconds)))
    wire.reply = lambda n, who: (429, {"retry-after": "1"}, SECONDARY_BODY) if n <= 3 else (200, {}, '{"data": {}}')
    assert ghcli.run(GQL_ARGS).returncode == 1
    assert ghcli.run(GQL_ARGS).returncode == 0
    assert [e if e[0] == "wait" else e[0] for e in order] == [
        "send", ("wait", 60), "send", ("wait", 120), "send", ("wait", 240), "send"]


def test_a_primary_pause_does_not_lift_a_longer_secondary_hold(wire: Wire, throttle: list,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """RB5 S2: A's secondary hold runs 600 s. B's recorded budget is under the floor, so B first pauses 35 s for the
    primary reset; that pause lasts until A's hold ends and does not lift it early. B and C send no earlier than A's
    end."""
    clock = Clock()
    monkeypatch.setattr(ghcli, "_now", clock.now)
    a_waiting, release_a = threading.Event(), threading.Event()

    def sleep(seconds: float) -> None:
        if threading.current_thread().name == "A":
            a_waiting.set()
            release_a.wait(10)
            return
        clock.sleep(seconds)

    monkeypatch.setattr(ghcli, "_sleep", sleep)
    sent: list[tuple[str, float]] = []

    def reply(n: int, who: str) -> tuple:
        sent.append((who, clock.now()))
        return (429, {"retry-after": "600"}, SECONDARY_BODY) if n == 1 else (200, {}, '{"data": {}}')

    wire.reply = reply
    a = threading.Thread(target=ghcli.run, args=(GQL_ARGS,), name="A", daemon=True)
    a.start()
    assert a_waiting.wait(10)                                           # A holds every thread until NOW + 600
    ghcli._TOKEN_LIMITS[0] = {"graphql": ghcli._Limit(10, NOW + 20)}    # under the floor, reset in 20 s
    for who in ("B", "C"):
        t = threading.Thread(target=ghcli.run, args=(GQL_ARGS,), name=who, daemon=True)
        t.start()
        t.join(10)
        assert not t.is_alive()
    release_a.set()
    a.join(10)
    assert [who for who, _ in sent] == ["A", "B", "C", "A"]
    assert [at for who, at in sent if who in ("B", "C")] == [NOW + 600, NOW + 600]


def test_signals_one_at_a_time_never_count_toward_the_pin(wire: Wire, throttle: list,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """RB5 ruling 2: three secondary limits while one request at a time was allowed (L1's usual limits), then one
    more with three allowed, all within 24 h: not pinned. Only strikes taken with several in flight count."""
    clock = Clock()
    monkeypatch.setattr(ghcli, "_now", clock.now)
    monkeypatch.setattr(ghcli, "_sleep", clock.sleep)
    refused: set[int] = set()
    wire.reply = lambda n, who: (403, {"retry-after": "30"}, SECONDARY_BODY) if n in refused \
        else (200, {}, '{"data": {}}')
    for k in range(4):
        if k == 3:
            monkeypatch.setenv("PORCHLAMP_GH_INFLIGHT", "3")
        clock.t = NOW + k * 3600
        refused.add(wire.n + 1)
        assert ghcli.run(GQL_ARGS).returncode == 0
    assert clock.sleeps == [60, 120, 240, 480]                          # the waits still double per signal
    assert not ghcli._THROTTLE.pinned
    assert [level for level, _ in throttle if level == "err"] == []


def test_one_burst_of_refusals_counts_once(wire: Wire, throttle: list, monkeypatch: pytest.MonkeyPatch) -> None:
    """RB5 S4: three requests in flight refused by one GitHub event (each was sent before the first refusal came
    back) are one signal: one strike, one warn event, no pin; each of the three still waits the hold, then retries."""
    clock = Clock()
    monkeypatch.setattr(ghcli, "_now", clock.now)
    monkeypatch.setenv("PORCHLAMP_GH_INFLIGHT", "3")
    gate = threading.Event()
    wire.hold = gate
    wire.reply = lambda n, who: (403, {"retry-after": "60"}, SECONDARY_BODY) if n <= 3 else (200, {}, '{"data": {}}')
    workers = [threading.Thread(target=ghcli.run, args=(GQL_ARGS,), name=f"w{i}", daemon=True) for i in range(3)]
    for t in workers:
        t.start()
    assert _wait_until(lambda: wire.in_flight == 3)
    clock.t = NOW + 5                                                   # the answers come back after all three left
    gate.set()
    for t in workers:
        t.join(10)
    assert not any(t.is_alive() for t in workers)
    assert wire.n == 6
    assert len(ghcli._THROTTLE.strikes) == 1
    assert [level for level, _ in throttle] == ["warn"]
    assert not ghcli._THROTTLE.pinned


def test_a_burst_with_different_retry_afters_holds_until_the_longest(
        wire: Wire, throttle: list, monkeypatch: pytest.MonkeyPatch) -> None:
    """RB6 F1: three requests in flight refused by one GitHub event, with ``retry-after`` 120, 300 and 60 s, answered
    in that order. The two later refusals fold into the first (one strike, one warn event, no pin), and each still
    extends the hold: it ends at the longest, 300 s after the answers came. No thread clears its hold before the
    whole burst is in (their waits block until then)."""
    clock = Clock()
    monkeypatch.setattr(ghcli, "_now", clock.now)
    monkeypatch.setenv("PORCHLAMP_GH_INFLIGHT", "3")
    retry = {1: "120", 2: "300", 3: "60"}
    answer = {n: threading.Event() for n in retry}

    def reply(n: int, who: str) -> tuple:
        if n in retry:
            answer[n].wait(10)
            return 403, {"retry-after": retry[n]}, SECONDARY_BODY
        return 200, {}, '{"data": {}}'

    signalled: list[float] = []
    real_signal = ghcli._THROTTLE.signal

    def signal(wait: float, why: str, sent_at: float) -> tuple[float, float]:
        held, until = real_signal(wait, why, sent_at)
        signalled.append(until)
        return held, until

    burst_in = threading.Event()

    def sleep(seconds: float) -> None:
        burst_in.wait(10)

    wire.reply = reply
    monkeypatch.setattr(ghcli._THROTTLE, "signal", signal)
    monkeypatch.setattr(ghcli, "_sleep", sleep)
    workers = [threading.Thread(target=ghcli.run, args=(GQL_ARGS,), name=f"w{i}", daemon=True) for i in range(3)]
    for t in workers:
        t.start()
    assert _wait_until(lambda: wire.in_flight == 3)
    clock.t = NOW + 5                                                   # the answers come back after all three left
    for n in retry:
        answer[n].set()
        assert _wait_until(lambda n=n: len(signalled) == n), n
    try:
        assert signalled == [NOW + 5 + 120, NOW + 5 + 300, NOW + 5 + 300]
        assert ghcli._THROTTLE.hold_until == NOW + 5 + 300
        assert len(ghcli._THROTTLE.strikes) == 1 and not ghcli._THROTTLE.pinned
        assert [level for level, _ in throttle] == ["warn"]
    finally:
        burst_in.set()
        for t in workers:
            t.join(10)
    assert not any(t.is_alive() for t in workers) and wire.n == 6


def test_a_request_admitted_before_a_signal_folds_however_late_it_reads_the_clock(
        wire: Wire, throttle: list, monkeypatch: pytest.MonkeyPatch) -> None:
    """RB6 F2 (E4): B is admitted while A's request is out, then descheduled before its send time is taken. A's
    refusal comes back and counts; only then does B go on, and GitHub refuses B for the same event. B's send time is
    its admission (taken under the throttle's lock), so B's refusal folds: one strike, one warn event."""
    clock = Clock()
    monkeypatch.setattr(ghcli, "_now", clock.now)
    monkeypatch.setenv("PORCHLAMP_GH_INFLIGHT", "3")
    a_answer, b_admitted, b_go = threading.Event(), threading.Event(), threading.Event()
    sent: dict[str, int] = {}

    def reply(n: int, who: str) -> tuple:
        sent[who] = sent.get(who, 0) + 1
        if sent[who] > 1:
            return 200, {}, '{"data": {}}'
        if who == "A":
            a_answer.wait(10)
        return 403, {"retry-after": "60"}, SECONDARY_BODY

    real_acquire = ghcli._THROTTLE.acquire

    def acquire(is_graphql: bool):
        admitted = real_acquire(is_graphql)
        if threading.current_thread().name == "B" and not b_admitted.is_set():
            b_admitted.set()
            b_go.wait(10)                                               # descheduled right after its admission
        return admitted

    wire.reply = reply
    monkeypatch.setattr(ghcli._THROTTLE, "acquire", acquire)
    a = threading.Thread(target=ghcli.run, args=(GQL_ARGS,), name="A", daemon=True)
    b = threading.Thread(target=ghcli.run, args=(GQL_ARGS,), name="B", daemon=True)
    a.start()
    assert _wait_until(lambda: wire.in_flight == 1)
    b.start()
    assert b_admitted.wait(10)
    clock.t = NOW + 5                                                   # A's refusal comes back and counts
    a_answer.set()
    assert _wait_until(lambda: len(ghcli._THROTTLE.strikes) == 1)
    clock.t = NOW + 6
    b_go.set()
    a.join(10)
    b.join(10)
    assert not a.is_alive() and not b.is_alive() and wire.n == 4
    assert len(ghcli._THROTTLE.strikes) == 1
    assert [level for level, _ in throttle] == ["warn"]


def test_a_thread_waiting_a_hold_stops_at_a_stop(wire: Wire, throttle: list, monkeypatch: pytest.MonkeyPatch) -> None:
    """RB5 OP1: with the engine's gate and no test sleep, a thread waiting another thread's hold is a GitHub wait the
    phone can see (``rate_state``), and a stop ends it within about a second."""
    monkeypatch.setattr(ghcli, "_sleep", None)
    gate = loopback_gate()
    outcome: list[str] = []

    def call() -> None:
        try:
            ghcli.run(GQL_ARGS)
            outcome.append("sent")
        except httpgate.Stopped:
            outcome.append("stopped")

    with installed(gate):
        ghcli._THROTTLE.hold(600)                                       # the clock is pinned: 600 s are left
        waiter = threading.Thread(target=call, daemon=True)
        waiter.start()
        assert _wait_until(lambda: gate.rate_state() is not None, 5)
        began = time.monotonic()
        gate.stop()
        waiter.join(3)
    assert outcome == ["stopped"] and time.monotonic() - began < 1.5
    assert wire.n == 0


def test_a_hold_that_ends_lifts_only_itself(wire: Wire, throttle: list, monkeypatch: pytest.MonkeyPatch) -> None:
    """RB5 OP2: B's 30-s hold is set, then A's 600-s hold; when B's ends, B clears only its own. C, after that, still
    waits until A's end."""
    clock = Clock()
    monkeypatch.setattr(ghcli, "_now", clock.now)
    monkeypatch.setattr(ghcli, "_sleep", clock.sleep)
    ends: dict[str, float] = {}

    def set_hold(who: str, seconds: float) -> None:
        ends[who] = ghcli._THROTTLE.hold(seconds)

    for who, seconds in (("B", 30), ("A", 600)):
        t = threading.Thread(target=set_hold, args=(who, seconds))
        t.start()
        t.join(5)
    assert ends == {"B": NOW + 30, "A": NOW + 600}
    clock.t = NOW + 30                                                  # B waited its 30 s
    ghcli._THROTTLE.clear_hold(ends["B"])
    sent: list[float] = []
    wire.reply = lambda n, who: (sent.append(clock.now()), (200, {}, '{"data": {}}'))[1]
    assert ghcli.run(GQL_ARGS).returncode == 0
    assert clock.sleeps == [570] and sent == [NOW + 600]


def test_a_request_that_fails_on_the_wire_gives_its_slot_back(wire: Wire, throttle: list,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """RB5 OP3: a request whose connection fails, or times out, still releases its slot (by name, not by a hang)."""
    def refused(n: int, who: str) -> tuple:
        raise ghcli._Transport("connection refused")

    wire.reply = refused
    assert ghcli.run(GQL_ARGS).returncode == 1
    assert ghcli._THROTTLE.in_flight == 0

    def too_slow(n: int, who: str) -> tuple:
        raise subprocess.TimeoutExpired(["gh", *GQL_ARGS], 1)

    wire.reply = too_slow
    with pytest.raises(subprocess.TimeoutExpired):
        ghcli.run(GQL_ARGS)
    assert ghcli._THROTTLE.in_flight == 0


class _BrokenStderr:
    """``sys.stderr`` that cannot be written (RB5 S6)."""

    def write(self, text: str) -> int:
        raise OSError(5, "Input/output error")

    def flush(self) -> None:
        pass


def test_the_back_to_n_line_failing_leaves_no_slot_counted(throttle: list, monkeypatch: pytest.MonkeyPatch) -> None:
    """RB5 S6 (follow-up 11a): after a secondary limit's one-at-a-time spell, the next request writes "back to 3
    requests at once" on stderr. If that write raises, the request is not sent and no slot stays counted; the next
    request is admitted without the line again."""
    clock = Clock()
    monkeypatch.setattr(ghcli, "_now", clock.now)
    monkeypatch.setenv("PORCHLAMP_GH_INFLIGHT", "3")
    t = ghcli._THROTTLE
    t.signal(60.0, "HTTP 429, retry-after 60 s", clock.now())
    clock.t = NOW + ghcli.DEGRADE_S + 1                                  # the hold and the spell are over
    with monkeypatch.context() as m:
        m.setattr("sys.stderr", _BrokenStderr())
        with pytest.raises(OSError):
            t.acquire(True)
    assert t.in_flight == 0
    t.acquire(True)
    assert t.in_flight == 1 and not [text for _, text in throttle if text.startswith("back to")]


def test_a_stop_ends_a_wait_for_a_free_slot(throttle: list) -> None:
    """RB5 follow-up 11b: with the cap (1) full, a thread waiting in ``acquire`` for a slot stops at a stop within
    about a second, with no slot counted, though no request ends meanwhile (checked by name, never a hang)."""
    t = ghcli._THROTTLE
    t.acquire(True)                                                     # the one slot is taken and stays taken
    outcome: list[str] = []

    def waiting() -> None:
        try:
            t.acquire(True)
            outcome.append("admitted")
        except httpgate.Stopped:
            outcome.append("stopped")

    gate = loopback_gate()
    waiter = threading.Thread(target=waiting, daemon=True)
    with installed(gate):
        waiter.start()
        time.sleep(0.3)
        assert not outcome                                              # it waits for the slot
        began = time.monotonic()
        gate.stop()
        waiter.join(3)
        stopped_in = time.monotonic() - began
        if waiter.is_alive():
            t.release(200, 0)                                           # let it go, then fail by name
            waiter.join(3)
            pytest.fail("the slot wait did not end at the stop")
    assert outcome == ["stopped"] and stopped_in < 1.5 and t.in_flight == 1
