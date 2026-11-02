"""Read-mostly LAN server for the phone's refresh home. Standard library only.

``python -m phone.serve --home DIR --port 8765 --bind 127.0.0.1``

Every request must be signed with the LAN token (``config/lan_token``) as described in ``phone.auth``; the token itself
is never sent. The server refuses to start without a token of at least 24 characters. The only write is
``PUT /config/candidates.jsonl`` (validated, atomic); nothing else under HOME is ever written. At most 8 connections are
served at once, a silent client is dropped after 20 s, and an IP with 10 failed authentications in 60 s is locked out
for 300 s.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from phone.archive import LAYERS, completed_layers, write_manifest, _marker_text
from porchlamp.collect.names import valid_name
from phone.auth import MAC_HEADER, FailureThrottle, NonceStore, check_request, headers_look_signed, response_mac

TOKEN_MIN_LEN = 24
CANDIDATES_CAP = 8 * 1024 * 1024
CANDIDATES_MAX_LINES = 20_000
CANDIDATE_FIELDS = ("owner", "name", "ecosystem")
SOCKET_TIMEOUT_S = 20
MAX_CONNECTIONS = 8
LOCKOUT_RETRY_AFTER_S = 300
RANGE_FROM = re.compile(r"^bytes=(\d+)-$")


class TokenError(ValueError):
    """config/lan_token is missing or too short."""


def load_token(home: Path) -> str:
    """The LAN bearer token from ``config/lan_token``; raises TokenError when absent or shorter than 24 characters."""
    path = Path(home) / "config" / "lan_token"
    try:
        token = path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, UnicodeDecodeError):
        raise TokenError("config/lan_token is missing") from None
    if len(token) < TOKEN_MIN_LEN:
        raise TokenError(f"config/lan_token is shorter than {TOKEN_MIN_LEN} characters")
    return token


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write(target: Path, data: bytes) -> None:
    """Write ``data`` to ``target`` through a unique temp file beside it and ``os.replace``."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def validate_candidates(body: bytes) -> bool:
    """True when ``body`` is JSON Lines of objects with string owner, name and ecosystem, within the line cap."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return False
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if len(lines) > CANDIDATES_MAX_LINES:
        return False
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            return False
        if not isinstance(row, dict):
            return False
        for field in CANDIDATE_FIELDS:
            value = row.get(field)
            if not valid_name(value):
                return False
    return True


class BoundedServer(ThreadingHTTPServer):
    """A threading server that serves at most ``MAX_CONNECTIONS`` connections at once; the next gets 503 and is closed."""

    daemon_threads = True
    request_queue_size = 64

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self._active = 0
        self._count_lock = threading.Lock()
        super().__init__(*args, **kwargs)

    @property
    def active(self) -> int:
        return self._active

    def _release(self) -> None:
        with self._count_lock:
            self._active -= 1
        self._slots.release()

    def process_request(self, request, client_address) -> None:
        if not self._slots.acquire(blocking=False):
            try:
                request.settimeout(2)
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass  # the refused client is already gone; nothing else to tell it
            self.shutdown_request(request)
            return
        with self._count_lock:
            self._active += 1
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._release()


class _CountingFile:
    """A socket file that counts the bytes read from it and written to it."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.count = 0

    def read(self, *args):
        data = self._inner.read(*args)
        self.count += len(data)
        return data

    def readline(self, *args):
        data = self._inner.readline(*args)
        self.count += len(data)
        return data

    def write(self, data) -> int:
        written = self._inner.write(data)
        self.count += len(data)
        return written

    def __getattr__(self, name):
        return getattr(self._inner, name)


class PhoneHandler(BaseHTTPRequestHandler):
    server_version = "porchlamp-phone"
    sys_version = ""
    timeout = SOCKET_TIMEOUT_S  # a client that stalls is dropped
    _authed = False

    def setup(self) -> None:
        super().setup()
        self.rfile = _CountingFile(self.rfile)
        self.wfile = _CountingFile(self.wfile)

    def finish(self) -> None:
        """Tell the owner of the server what this connection moved (``server.on_transfer``), then close."""
        try:
            super().finish()
        finally:
            report = getattr(self.server, "on_transfer", None)
            if report is not None and getattr(self, "command", None):
                fetched = self._authed and self.command == "GET" and getattr(self, "_status", 0) in (200, 206)
                report(self.client_address[0], self.wfile.count, self.rfile.count, fetched)

    @property
    def home(self) -> Path:
        return self.server.home  # type: ignore[attr-defined]

    # ---- plumbing -------------------------------------------------------------------------------------------------
    def _reply(self, status: int, body: bytes = b"", ctype: str = "application/json", extra=()) -> None:
        self.send_response(status)
        for key, value in extra:
            self.send_header(key, value)
        if body or status == 200:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, status: int, obj) -> None:
        body = (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("utf-8")
        mac = response_mac(self.server.token, self._nonce, status, body)  # type: ignore[attr-defined]
        self._reply(status, body, extra=[(MAC_HEADER, mac)])

    def _deny(self, category: str) -> None:
        """The one answer to every authentication failure: 401, empty body. The category goes to stderr only."""
        self.close_connection = True
        self.server.throttle.record_failure(self.client_address[0])  # type: ignore[attr-defined]
        sys.stderr.write(f"auth-fail {category}\n")
        self._reply(401)

    def _target(self) -> str:
        """The request target exactly as the client sent it (path plus query)."""
        words = self.requestline.split()
        return words[1] if len(words) >= 2 else self.path

    def _path(self) -> str:
        return urlsplit(self.path).path

    def _authenticate(self) -> bytes | None:
        """The signed request's body when it is genuine; otherwise None after the error reply has been sent."""
        server = self.server
        if server.throttle.locked(self.client_address[0]):  # type: ignore[attr-defined]
            self.close_connection = True
            self._reply(429, extra=[("Retry-After", str(LOCKOUT_RETRY_AFTER_S))])
            return None
        now = server.clock()  # type: ignore[attr-defined]
        body = b""
        raw_length = self.headers.get("Content-Length")
        if self.command == "PUT":
            try:
                length = int(raw_length if raw_length is not None else "-1")
            except ValueError:
                length = -1
            if length < 0 or length > CANDIDATES_CAP:
                # The MAC binds the body, so it cannot be checked without reading it. Unsigned callers still get 401.
                if not headers_look_signed(self.headers, now):
                    self._deny("malformed")
                    return None
                self.close_connection = True  # do not read the body; nothing is written
                self._reply(411 if length < 0 else 413)
                return None
            try:
                body = self.rfile.read(length)
            except OSError:
                self.close_connection = True  # the client stalled mid-body or vanished
                return None
            if len(body) != length:
                self.close_connection = True
                return None
        elif raw_length not in (None, "0"):
            self.close_connection = True  # a body we will not read must not be parsed as the next request
        category = check_request(server.token, self.command, self._target(), body,  # type: ignore[attr-defined]
                                 self.headers, now, server.nonces)  # type: ignore[attr-defined]
        if category != "ok":
            self._deny(category)
            return None
        self._nonce = self.headers.get("X-Porch-Nonce")
        self._authed = True
        return body

    # ---- routes ---------------------------------------------------------------------------------------------------
    def do_GET(self) -> None:
        if self._authenticate() is None:
            return
        path = self._path()
        if path == "/status.json":
            return self._status()
        if path == "/manifest.json":
            return self._json(200, write_manifest(self.home))
        if path == "/log.txt":
            return self._log_txt()
        if path == "/state/users.sqlite.gz":
            return self._get_user_cache_gz()
        if path.startswith("/archive/"):
            return self._archive(path[len("/archive/"):])
        return self._reply(404)

    def do_PUT(self) -> None:
        body = self._authenticate()
        if body is None:
            return
        path = self._path()
        if path == "/config/candidates.jsonl":
            return self._put_candidates(body)
        if path == "/state/users.sqlite.gz":
            return self._put_user_cache_gz(body)
        return self._reply(404)

    def _method_not_allowed(self) -> None:
        if self._authenticate() is None:
            return
        self.close_connection = True
        self._reply(405, b"", extra=[("Allow", "GET, PUT")])

    do_POST = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _method_not_allowed

    # ---- handlers -------------------------------------------------------------------------------------------------
    def _status(self) -> None:
        files = {}
        for collector in sorted({c for cols in LAYERS.values() for c in cols}):
            folder = self.home / "raw" / collector
            files[collector] = sum(1 for p in folder.iterdir() if p.suffix == ".json") if folder.is_dir() else 0
        self._json(200, {
            "now": _utc_now(),
            "uptime_s": round(time.monotonic() - self.server.started, 1),  # type: ignore[attr-defined]
            "completed_layers": completed_layers(self.home),
            "main_done": _marker_text(self.home / "state" / "refresh" / "MAIN.done"),
            "files": files,
            "free_gb": round(shutil.disk_usage(self.home).free / 1e9, 2),
            "lan_token_set": True,
        })

    def _log_txt(self) -> None:
        """The same redacted technical log the app exports, signed like every reply."""
        provider = getattr(self.server, "log_text", None)
        if provider is None:
            return self._reply(404)
        body = provider().encode("utf-8", "replace")
        mac = response_mac(self.server.token, self._nonce, 200, body)  # type: ignore[attr-defined]
        self._reply(200, body, ctype="text/plain; charset=utf-8", extra=[(MAC_HEADER, mac)])

    def _archive(self, name: str) -> None:
        manifest = write_manifest(self.home)
        if not name.endswith(".tar.gz"):
            return self._reply(404)
        entry = manifest["layers"].get(name[: -len(".tar.gz")])  # plain match against the manifest's layers
        if entry is None or entry["archive"] != name:
            return self._reply(404)
        target = self.home / "archives" / name
        if not target.is_file():
            return self._reply(404)
        size = target.stat().st_size
        start, status = 0, 200
        rng = self.headers.get("Range")
        match = RANGE_FROM.fullmatch(rng) if rng else None
        extra = [("Accept-Ranges", "bytes")]
        if match:
            start = int(match.group(1))
            if start >= size:
                return self._reply(416, b"", extra=[("Content-Range", f"bytes */{size}")])
            status = 206
            extra.append(("Content-Range", f"bytes {start}-{size - 1}/{size}"))
        self.send_response(status)
        self.send_header("Content-Type", "application/gzip")
        self.send_header("Content-Length", str(size - start))
        for key, value in extra:
            self.send_header(key, value)
        self.end_headers()
        with open(target, "rb") as fh:
            fh.seek(start)
            shutil.copyfileobj(fh, self.wfile, 1 << 20)

    def _put_candidates(self, body: bytes) -> None:
        if not validate_candidates(body):
            return self._reply(400)  # nothing is written
        _atomic_write(self.home / "raw" / "candidates.jsonl", body)
        return self._reply(204)

    def _get_user_cache_gz(self) -> None:
        from porchlamp.collect.user_cache import UserCache, export_sqlite_gz

        db_path = self.home / "state" / "users.sqlite"
        users_dir = self.home / "state" / "users"
        if not db_path.is_file() and not (users_dir.is_dir() and any(users_dir.glob("*.json"))):
            return self._reply(404)
        cache = UserCache(users_dir, backend="sqlite")
        try:
            body = export_sqlite_gz(cache)
        finally:
            cache.close()
        mac = response_mac(self.server.token, self._nonce, 200, body)  # type: ignore[attr-defined]
        return self._reply(200, body, ctype="application/gzip", extra=[(MAC_HEADER, mac)])

    def _put_user_cache_gz(self, body: bytes) -> None:
        from porchlamp.collect.user_cache import UserCache, import_sqlite_gz

        cache = UserCache(self.home / "state" / "users", backend="sqlite")
        try:
            import_sqlite_gz(cache, body)
        except ValueError:
            return self._reply(400)
        finally:
            cache.close()
        return self._reply(204)

    def send_response(self, code: int, message: str | None = None) -> None:
        self._status = code
        super().send_response(code, message)

    def log_message(self, format: str, *args) -> None:
        """Method and status code only: never the path, the query, a header or the library's message text."""
        sys.stderr.write(f"{getattr(self, 'command', None) or '-'} {getattr(self, '_status', None) or '-'}\n")


def make_server(home: Path, bind: str = "127.0.0.1", port: int = 8765, token: str | None = None, *,
                on_transfer=None, log_text=None) -> BoundedServer:
    """A ready (not yet serving) server bound to ``bind``:``port``; raises TokenError without a usable token.

    A ``token`` passed in is used as is (the app keeps it in memory) and ``config/lan_token`` is not read.
    ``on_transfer(client_ip, bytes_sent, bytes_received, fetched)`` is called after each connection (the engine counts
    it under the client's address); ``log_text()`` returns the text ``GET /log.txt`` serves (404 without it)."""
    home = Path(home)
    if token is None:
        token = load_token(home)
    elif len(token) < TOKEN_MIN_LEN:
        raise TokenError(f"the LAN token is shorter than {TOKEN_MIN_LEN} characters")
    server = BoundedServer((bind, port), PhoneHandler)
    server.home = home  # type: ignore[attr-defined]
    server.token = token  # type: ignore[attr-defined]
    server.started = time.monotonic()  # type: ignore[attr-defined]
    server.clock = time.time  # type: ignore[attr-defined]
    server.nonces = NonceStore()  # type: ignore[attr-defined]
    server.throttle = FailureThrottle()  # type: ignore[attr-defined]
    server.on_transfer = on_transfer  # type: ignore[attr-defined]
    server.log_text = log_text  # type: ignore[attr-defined]
    return server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="phone.serve", description=__doc__.splitlines()[0])
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--bind", default="127.0.0.1")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        server = make_server(args.home, args.bind, args.port)
    except TokenError as exc:
        print(f"refusing to start: {exc}", file=sys.stderr)
        return 2
    print(f"serving {args.home} on {args.bind}:{server.server_address[1]}", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
