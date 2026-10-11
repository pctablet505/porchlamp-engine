"""Read-mostly LAN server for the phone's refresh home. Standard library only.

``python -m phone.serve --home DIR --port 8765 --bind 0.0.0.0``

Every request needs ``Authorization: Bearer <config/lan_token>``. The server refuses to start without a token of at
least 24 characters. Writes are limited to two PUT paths, each atomic; nothing else under HOME is ever written.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from phone.archive import LAYERS, completed_layers, write_manifest, _marker_text

TOKEN_MIN_LEN = 24
CANDIDATES_CAP = 8 * 1024 * 1024
TOKEN_CAP = 256
RANGE_FROM = re.compile(r"^bytes=(\d+)-$")


class TokenError(Exception):
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


def _atomic_write(target: Path, data: bytes, mode: int | None = None) -> None:
    """Write ``data`` to ``target`` through ``<target>.tmp`` and ``os.replace``; ``mode`` is applied before the rename."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if mode is not None else 0o644)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, target)


class PhoneHandler(BaseHTTPRequestHandler):
    server_version = "porchlamp-phone"
    sys_version = ""

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
        self._reply(status, (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("utf-8"))

    def _authorized(self) -> bool:
        got = (self.headers.get("Authorization") or "").encode("utf-8")
        want = f"Bearer {self.server.token}".encode("utf-8")  # type: ignore[attr-defined]
        return hmac.compare_digest(got, want)

    def _deny(self) -> None:
        self.close_connection = True
        self._reply(401, b"", extra=[("WWW-Authenticate", "Bearer")])

    def _path(self) -> str:
        return urlsplit(self.path).path

    # ---- routes ---------------------------------------------------------------------------------------------------
    def do_GET(self) -> None:
        if not self._authorized():
            return self._deny()
        path = self._path()
        if path == "/status.json":
            return self._status()
        if path == "/manifest.json":
            return self._json(200, write_manifest(self.home))
        if path.startswith("/archive/"):
            return self._archive(path[len("/archive/"):])
        return self._reply(404)

    def do_PUT(self) -> None:
        if not self._authorized():
            return self._deny()
        path = self._path()
        if path == "/config/candidates.jsonl":
            return self._put_body(self.home / "raw" / "candidates.jsonl", CANDIDATES_CAP, None)
        if path == "/config/github_token":
            return self._put_body(self.home / "config" / "github_token", TOKEN_CAP, 0o600)
        return self._reply(404)

    def _method_not_allowed(self) -> None:
        if not self._authorized():
            return self._deny()
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

    def _put_body(self, target: Path, cap: int, mode: int | None) -> None:
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if length < 0:
            self.close_connection = True
            return self._reply(411)
        if length > cap:
            self.close_connection = True  # do not read the body; nothing is written
            return self._reply(413)
        body = self.rfile.read(length)
        if len(body) != length:
            self.close_connection = True
            return self._reply(400)
        _atomic_write(target, body, mode)
        return self._reply(204)

    def log_message(self, format: str, *args) -> None:  # request line only, never headers
        sys.stderr.write("%s %s\n" % (self.command, self.path.split("?", 1)[0]))


def make_server(home: Path, bind: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    """A ready (not yet serving) server bound to ``bind``:``port``; raises TokenError without a usable token."""
    home = Path(home)
    token = load_token(home)
    server = ThreadingHTTPServer((bind, port), PhoneHandler)
    server.daemon_threads = True
    server.home = home  # type: ignore[attr-defined]
    server.token = token  # type: ignore[attr-defined]
    server.started = time.monotonic()  # type: ignore[attr-defined]
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="phone.serve", description=__doc__.splitlines()[0])
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--bind", default="0.0.0.0")
    args = parser.parse_args(argv)
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
