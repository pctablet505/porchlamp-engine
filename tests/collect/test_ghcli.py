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
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "PORCHLAMP_GH", "PORCHLAMP_GH_API",
                 "PORCHLAMP_RATE_FLOOR"):
        monkeypatch.delenv(name, raising=False)
    return recorded


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch):
    srv = FakeGitHub()
    srv.thread.start()
    monkeypatch.setenv("PORCHLAMP_GH", "http")
    monkeypatch.setenv("PORCHLAMP_GH_API", srv.url)
    yield srv
    srv.close()


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


def test_authorization_header_present_only_with_a_token(fake: FakeGitHub,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    ghcli.run(["api", "repos/x/y"])
    plain = fake.seen[-1]["headers"]
    assert "authorization" not in plain
    assert plain["accept"] == "application/vnd.github+json"
    assert plain["x-github-api-version"] == "2022-11-28"
    assert plain["user-agent"] == "porchlamp-phone/1"
    monkeypatch.setenv("GITHUB_TOKEN", DUMMY_TOKEN)
    ghcli.run(["api", "repos/x/y"])
    assert fake.seen[-1]["headers"]["authorization"] == f"Bearer {DUMMY_TOKEN}"


def test_token_never_in_output_or_exception(fake: FakeGitHub, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", DUMMY_TOKEN)
    fake.replies = [(500, {}, "server exploded")]
    out = ghcli.run(["api", "repos/x/y"])
    assert out.returncode == 1 and DUMMY_TOKEN not in out.stdout + out.stderr
    assert fake.seen[-1]["headers"]["authorization"] == f"Bearer {DUMMY_TOKEN}"

    monkeypatch.setenv("PORCHLAMP_GH_API", _closed_port_url())
    failed = ghcli.run(["api", "repos/x/y"])
    assert failed.returncode == 1 and DUMMY_TOKEN not in failed.stderr

    monkeypatch.setenv("PORCHLAMP_GH_API", fake.url)
    fake.replies = [lambda: (time.sleep(1.0), _ok())[1]]
    with pytest.raises(subprocess.TimeoutExpired) as info:
        ghcli.run(["api", "repos/x/y"], timeout=0.2)
    assert DUMMY_TOKEN not in repr(info.value)
    assert DUMMY_TOKEN not in str(info.value)


def test_timeout_raises_timeoutexpired(fake: FakeGitHub) -> None:
    fake.replies = [lambda: (time.sleep(1.0), _ok())[1]]
    with pytest.raises(subprocess.TimeoutExpired) as info:
        ghcli.run(["api", "repos/x/y"], timeout=0.2)
    assert info.value.cmd == ["gh", "api", "repos/x/y"]
    assert info.value.timeout == 0.2


def test_transport_error_returncode_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORCHLAMP_GH", "http")
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
