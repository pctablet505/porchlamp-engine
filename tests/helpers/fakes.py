"""Fakes for the engine tests: a clock that only moves when told, and a real HTTP server on loopback.

``FakeHTTP`` is a real socket server (``ThreadingHTTPServer`` on 127.0.0.1) that records every request it receives, so a
test can prove both what was sent and, for a refused request, that nothing was.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any

from phone import httpgate


class FakeClock:
    """Epoch seconds that move only when ``advance`` says so."""

    def __init__(self, start: float = 1_790_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


Responder = Callable[[SimpleNamespace], "tuple[int, dict[str, str], bytes | str]"]


class FakeHTTP:
    """A loopback server. ``responder(req)`` returns ``(status, headers, body)``; ``delay`` slows every reply."""

    def __init__(self, responder: Responder | None = None, delay: float = 0.0, bind: str = "127.0.0.1") -> None:
        self.responder: Responder = responder or (lambda req: (200, {}, b"{}"))
        self.delay = delay
        self.seen: list[SimpleNamespace] = []
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                req = SimpleNamespace(method=self.command, path=self.path, body=body,
                                      headers={k.lower(): v for k, v in self.headers.items()})
                with outer._lock:
                    outer.seen.append(req)
                if outer.delay:
                    time.sleep(outer.delay)
                status, headers, payload = outer.responder(req)
                data = payload.encode() if isinstance(payload, str) else payload
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = _handle

            def log_message(self, *args: Any) -> None:
                return None

        class Quiet(ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request, client_address) -> None:   # a client that timed out is not news
                return None

        self.server = Quiet((bind, 0), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://{bind}:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    @property
    def count(self) -> int:
        with self._lock:
            return len(self.seen)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def github_responder(*, scopes: str | None = "", private: list[Any] | None = None, user_status: int = 200,
                     remaining: int = 4000, limit: int = 5000, reset: int = 1_790_003_600) -> Responder:
    """A fake api.github.com for the token checks: /user, /user/repos, /rate_limit, anything else is ``{}``."""
    base = {"x-ratelimit-limit": str(limit), "x-ratelimit-remaining": str(remaining),
            "x-ratelimit-reset": str(reset), "x-ratelimit-resource": "core"}

    def respond(req: SimpleNamespace) -> tuple[int, dict[str, str], bytes | str]:
        path = req.path.split("?")[0]
        if path == "/user":
            headers = dict(base)
            if scopes is not None:
                headers["x-oauth-scopes"] = scopes
            if user_status != 200:
                return user_status, headers, json.dumps({"message": "Bad credentials"})
            return 200, headers, json.dumps({"login": "someone"})
        if path == "/user/repos":
            return 200, dict(base), json.dumps(private or [])
        if path == "/rate_limit":
            return 200, dict(base), json.dumps({"resources": {"core": {"limit": limit, "remaining": remaining,
                                                                         "reset": reset}}})
        return 200, dict(base), "{}"

    return respond


def loopback_gate_options(*hosts: str) -> dict[str, Any]:
    """Gate keyword options that admit loopback addresses (127.0.0.1 and any given) as plain-http, token-bearing hosts."""
    names = frozenset({"127.0.0.1", *hosts})
    return {"allowed": httpgate.PHONE_HOSTS | names, "http_hosts": names,
            "token_hosts": httpgate.TOKEN_HOSTS | names, "budget_hosts": names}
