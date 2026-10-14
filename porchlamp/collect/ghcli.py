"""GitHub calls for the collectors: the ``gh`` CLI, or HTTPS with ``urllib``.

``PORCHLAMP_GH=http`` selects the HTTPS transport (an Android app has no
``gh``). Any other value, unset included, runs ``gh`` exactly as before.

The HTTPS transport also keeps the rate-limit floor that ``gather_layer.sh``
enforced by sampling and killing: before a request, when the last response
for its resource reported fewer calls left than the floor and a reset still
lies in the future, the transport sleeps until that reset.

Only ``https://api.github.com`` ever receives the Authorization header. ``PORCHLAMP_GH_API`` is a test hook: it is
honoured only together with ``PORCHLAMP_ALLOW_TEST_API=1`` and only for a loopback host, and the header is not sent to
it. Redirects are never followed by urllib: a same-origin one is followed here (3 hops at most), any other is refused.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from porchlamp.collect import cost, net

API_VERSION = "2022-11-28"
USER_AGENT = "porchlamp-phone/1"
DEFAULT_BASE = "https://api.github.com"
DEFAULT_FLOOR = 500
SEARCH_FLOOR = 5
RESET_PAD = 15
MIN_WAIT = 10
NO_RESET_WAIT = 60
MAX_RETRIES = 2
MAX_REDIRECTS = 3
REDIRECT_CODES = (301, 302, 307, 308)
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
#: The only origin that is sent the Authorization header.
AUTH_BASE = DEFAULT_BASE


class _Limit(NamedTuple):
    remaining: int
    reset: float


#: resource -> (remaining calls, reset epoch seconds), from the last response.
_BUDGET: dict[str, tuple[int, float]] = {}
_LIMITS: dict[str, tuple[int, float]] = _BUDGET
_TOKEN_LIMITS: dict[int, dict[str, _Limit]] = {}

#: Persistent keep-alive connection pool for workstation mode.
#: ``_POOL`` holds the primary idle connection per origin; ``_POOL_IDLE`` holds
#: overflow idle connections when multiple worker threads run concurrently.
_POOL: dict[tuple[str, str, int], http.client.HTTPConnection] = {}
_POOL_IDLE: dict[tuple[str, str, int], list[http.client.HTTPConnection]] = {}
_POOL_LOCK = threading.Lock()

#: ETag cache mapping URL -> (etag, response_headers_dict, body_text).
_ETAG_CACHE: dict[str, tuple[str, dict[str, str], str]] = {}
_ETAG_DB_PATH: Path | None = None
_ETAG_CACHE_LOCK = threading.Lock()

#: Cached `gh auth token` when no environment token is set.
_CACHED_GH_CLI_TOKEN: str | None = None
_TOKEN_LOCK = threading.Lock()

#: None: the wait goes through the gate (a stop ends it within a second); a test may replace it with a recorder.
_sleep: Callable[[float], object] | None = None
_now = time.time

_INT = re.compile(r"-?\d+\Z")

_RETRY_EXC = (
    http.client.RemoteDisconnected,
    http.client.CannotSendRequest,
    BrokenPipeError,
    ConnectionResetError,
    TimeoutError,
    OSError,
)


class _Transport(Exception):
    """A transport failure (DNS, refused, reset); the message has no secrets."""


class _Refused(_Transport):
    """A redirect the transport will not follow; the message is ``<reason> (HTTP <code>)`` with no URL or header."""


def close_pool() -> None:
    """Close all open keep-alive connections and clear the pool."""
    with _POOL_LOCK:
        for conn in list(_POOL.values()):
            with contextlib.suppress(Exception):
                conn.close()
        _POOL.clear()
        for conns in list(_POOL_IDLE.values()):
            for conn in conns:
                with contextlib.suppress(Exception):
                    conn.close()
        _POOL_IDLE.clear()


def configure_etag_cache(db_path: Path | str | None = None) -> None:
    """Configure optional SQLite persistence for conditional ETag responses."""
    global _ETAG_DB_PATH
    with _ETAG_CACHE_LOCK:
        if db_path is None:
            _ETAG_DB_PATH = None
            return
        resolved = Path(db_path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(resolved)) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS http_etag_cache (
                    url TEXT PRIMARY KEY,
                    etag TEXT NOT NULL,
                    headers_json TEXT NOT NULL,
                    body TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.commit()
        _ETAG_DB_PATH = resolved


def _active_etag_db() -> Path | None:
    if _ETAG_DB_PATH is not None:
        return _ETAG_DB_PATH
    env_db = os.environ.get("PORCHLAMP_ETAG_CACHE_DB")
    if env_db:
        return Path(env_db)
    return None


def _etag_get(url: str) -> tuple[str, dict[str, str], str] | None:
    with _ETAG_CACHE_LOCK:
        cached = _ETAG_CACHE.get(url)
        if cached is not None:
            return cached
        db = _active_etag_db()
        if db is None or not db.is_file():
            return None
        try:
            with sqlite3.connect(str(db)) as conn:
                row = conn.execute(
                    "SELECT etag, headers_json, body FROM http_etag_cache WHERE url = ?",
                    (url,),
                ).fetchone()
            if row is not None:
                etag, headers_json, body = row
                headers_dict = json.loads(headers_json)
                entry = (str(etag), dict(headers_dict), str(body))
                _ETAG_CACHE[url] = entry
                return entry
        except Exception:
            return None
        return None


def _etag_put(url: str, etag: str, headers_dict: dict[str, str], body: str) -> None:
    with _ETAG_CACHE_LOCK:
        _ETAG_CACHE[url] = (etag, headers_dict, body)
        db = _active_etag_db()
        if db is None:
            return
        try:
            db.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(str(db)) as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS http_etag_cache (
                        url TEXT PRIMARY KEY,
                        etag TEXT NOT NULL,
                        headers_json TEXT NOT NULL,
                        body TEXT NOT NULL,
                        updated_at REAL NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO http_etag_cache (url, etag, headers_json, body, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (url, etag, json.dumps(headers_dict), body, _now()),
                )
                conn.commit()
        except Exception:
            pass


def clear_etag_cache() -> None:
    """Clear the conditional ETag cache."""
    global _ETAG_DB_PATH
    with _ETAG_CACHE_LOCK:
        _ETAG_CACHE.clear()
        _ETAG_DB_PATH = None


def clear_token_cache() -> None:
    """Clear cached gh CLI token and per-token rate limit records."""
    global _CACHED_GH_CLI_TOKEN
    with _TOKEN_LOCK:
        _CACHED_GH_CLI_TOKEN = None
        _TOKEN_LIMITS.clear()


def run(args: Sequence[str], timeout: float = 90) -> subprocess.CompletedProcess[str]:
    """Run one ``gh`` call; ``args`` is what followed ``gh`` on the command line."""
    if os.environ.get("PORCHLAMP_GH") != "http":
        t0 = time.perf_counter()
        proc = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)
        duration_ms = (time.perf_counter() - t0) * 1000.0
        with contextlib.suppress(Exception):
            method, endpoint, _fields, is_graphql = _parse(list(args))
            norm_endpoint = f"/{endpoint.lstrip('/')}"
            status = 200 if proc.returncode == 0 else 500
            gql_pts = 0
            if is_graphql and proc.returncode == 0:
                gql_pts = 1
                with contextlib.suppress(Exception):
                    payload = json.loads(proc.stdout)
                    rl = payload.get("data", {}).get("rateLimit") if isinstance(payload, dict) else None
                    if isinstance(rl, dict) and isinstance(rl.get("cost"), int):
                        gql_pts = rl["cost"]
            cost.record_call(
                endpoint=norm_endpoint,
                method=method,
                status=status,
                status_304=False,
                graphql_points=gql_pts,
                bytes_in=len((proc.stdout or "").encode("utf-8")),
                duration_ms=duration_ms,
            )
        return proc
    try:
        return _http_run(list(args), timeout)
    except _Transport as exc:
        return subprocess.CompletedProcess(["gh", *args], 1, "", f"gh: {exc}\n")


def _bad(args: list[str]) -> ValueError:
    return ValueError(f"ghcli: unsupported gh arguments: {args!r}")


def _coerce(value: str) -> Any:
    """``gh api -F`` typing: booleans, null and integers, else the string."""
    if value == "true":
        return True
    if value == "false":
        return False
    if value == "null":
        return None
    if _INT.match(value):
        return int(value)
    return value


def _parse(args: list[str]) -> tuple[str, str, list[tuple[str, Any]], bool]:
    """Return ``(method, endpoint, fields, is_graphql)`` for the supported shapes."""
    if not args or args[0] != "api":
        raise _bad(args)
    method: str | None = None
    positional: list[str] = []
    fields: list[tuple[str, Any]] = []
    i = 1
    while i < len(args):
        tok = args[i]
        if tok == "-X" and i + 1 < len(args):
            method = args[i + 1].upper()
            i += 2
        elif tok in ("-f", "-F") and i + 1 < len(args) and "=" in args[i + 1]:
            key, _, value = args[i + 1].partition("=")
            if not key:
                raise _bad(args)
            fields.append((key, _coerce(value) if tok == "-F" else value))
            i += 2
        elif tok.startswith("-"):
            raise _bad(args)
        else:
            positional.append(tok)
            i += 1
    if len(positional) != 1:
        raise _bad(args)
    endpoint = positional[0]
    if endpoint == "graphql":
        if method not in (None, "POST") or not any(k == "query" for k, _ in fields):
            raise _bad(args)
        return "POST", endpoint, fields, True
    return method or ("POST" if fields else "GET"), endpoint, fields, False


def _text(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    return str(value)


def _resource_for(endpoint: str, is_graphql: bool) -> str:
    if is_graphql:
        return "graphql"
    return "search" if endpoint.startswith("search/") else "core"


def _floor(resource: str) -> int:
    if resource == "search":
        return SEARCH_FLOOR
    return int(os.environ.get("PORCHLAMP_RATE_FLOOR", str(DEFAULT_FLOOR)))


def _header(headers: Any, name: str) -> str | None:
    return None if headers is None else headers.get(name)


def _configured_tokens(base: str) -> list[str]:
    tokens_env = os.environ.get("PORCHLAMP_GH_TOKENS")
    if tokens_env:
        toks = [t.strip() for t in tokens_env.split(",") if t.strip()]
        if toks:
            return toks
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if tok:
        return [tok]
    if base == DEFAULT_BASE:
        global _CACHED_GH_CLI_TOKEN
        with _TOKEN_LOCK:
            if _CACHED_GH_CLI_TOKEN is None:
                try:
                    res = subprocess.run(
                        ["gh", "auth", "token"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if res.returncode == 0 and res.stdout.strip():
                        _CACHED_GH_CLI_TOKEN = res.stdout.strip()
                except (OSError, subprocess.SubprocessError):
                    pass
            if _CACHED_GH_CLI_TOKEN:
                return [_CACHED_GH_CLI_TOKEN]
    return []


def _select_token(resource: str, base: str | None = None) -> str | None:
    if base is None:
        base = _api_base()
    tokens = _configured_tokens(base)
    if not tokens:
        return None
    if len(tokens) == 1:
        return tokens[0]

    floor = _floor(resource)
    now = _now()

    best_tok: str | None = None
    best_remaining: float = -1.0
    all_below_floor = True

    for i, tok in enumerate(tokens):
        limit = _TOKEN_LIMITS.get(i, {}).get(resource)
        if limit is None:
            remaining = float("inf")
        else:
            remaining = float(limit.remaining)
            if limit.reset <= now:
                remaining = float("inf")

        if remaining >= floor:
            all_below_floor = False

        if remaining > best_remaining:
            best_remaining = remaining
            best_tok = tok

    if all_below_floor:
        earliest_tok: str | None = None
        earliest_reset = float("inf")
        for i, tok in enumerate(tokens):
            limit = _TOKEN_LIMITS.get(i, {}).get(resource)
            reset = limit.reset if limit else 0.0
            if reset < earliest_reset:
                earliest_reset = reset
                earliest_tok = tok
        return earliest_tok or tokens[0]

    return best_tok or tokens[0]


def _record(headers: Any, default: str, token_idx: int | None = None) -> None:
    remaining = _header(headers, "x-ratelimit-remaining")
    reset = _header(headers, "x-ratelimit-reset")
    if remaining is None or reset is None:
        return
    key = _header(headers, "x-ratelimit-resource") or default
    try:
        limit = _Limit(int(remaining), float(reset))
        _BUDGET[key] = limit
        _LIMITS[key] = limit
        if token_idx is not None:
            if token_idx not in _TOKEN_LIMITS:
                _TOKEN_LIMITS[token_idx] = {}
            _TOKEN_LIMITS[token_idx][key] = limit
    except ValueError:
        return


def _pause(resource: str, remaining: int, seconds: float, token_idx: int | None = None) -> None:
    wait = max(MIN_WAIT, seconds)
    sys.stderr.write(
        f"ghcli: waiting {math.ceil(wait)}s for the {resource} reset "
        f"(remaining {remaining})\n"
    )
    if token_idx is not None:
        if token_idx in _TOKEN_LIMITS:
            _TOKEN_LIMITS[token_idx].pop(resource, None)
    else:
        _BUDGET.pop(resource, None)
        _LIMITS.pop(resource, None)
        for t_limits in _TOKEN_LIMITS.values():
            t_limits.pop(resource, None)
    net.rate_wait(resource, remaining, wait, _sleep)


def _gate(resource: str, token_idx: int | None = None) -> None:
    if token_idx is not None:
        record = _TOKEN_LIMITS.get(token_idx, {}).get(resource)
    else:
        record = _BUDGET.get(resource)
    if record is None:
        return
    remaining, reset = record
    now = _now()
    if remaining < _floor(resource) and reset > now:
        _pause(resource, remaining, reset - now + RESET_PAD, token_idx=token_idx)


def _limited(headers: Any) -> bool:
    return _header(headers, "x-ratelimit-remaining") == "0" or _header(headers, "retry-after") is not None


def _retry_seconds(headers: Any) -> float:
    retry_after = _header(headers, "retry-after")
    if retry_after is not None:
        try:
            return float(retry_after)
        except ValueError:
            pass
    reset = _header(headers, "x-ratelimit-reset")
    if reset is not None:
        try:
            return max(0.0, float(reset) - _now()) + RESET_PAD
        except ValueError:
            pass
    return NO_RESET_WAIT


def _url(base: str, endpoint: str, fields: list[tuple[str, Any]]) -> str:
    url = f"{base}/{endpoint.lstrip('/')}"
    if not fields:
        return url
    query = urllib.parse.urlencode([(k, _text(v)) for k, v in fields])
    return f"{url}{'&' if '?' in url else '?'}{query}"


def _body_text(raw: bytes) -> str:
    return raw.decode("utf-8", "replace")


def _is_timeout(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", None)
    return isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Turn every redirect into an HTTPError so the caller decides; urllib never re-sends headers on its own."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _checkout_conn(pool_key: tuple[str, str, int]) -> tuple[http.client.HTTPConnection | None, bool]:
    with _POOL_LOCK:
        if pool_key in _POOL:
            conn = _POOL.pop(pool_key)
            idle = _POOL_IDLE.get(pool_key)
            if idle:
                _POOL[pool_key] = idle.pop()
            return conn, True
        idle = _POOL_IDLE.get(pool_key)
        if idle:
            return idle.pop(), True
        return None, False


def _checkin_conn(pool_key: tuple[str, str, int], conn: http.client.HTTPConnection) -> None:
    with _POOL_LOCK:
        if pool_key not in _POOL:
            _POOL[pool_key] = conn
        else:
            _POOL_IDLE.setdefault(pool_key, []).append(conn)


def _new_conn(scheme: str, host: str, port: int, timeout: float) -> http.client.HTTPConnection:
    if scheme == "https":
        return http.client.HTTPSConnection(host, port, timeout=timeout)
    return http.client.HTTPConnection(host, port, timeout=timeout)


def _send_pooled(method: str, url: str, body: bytes | None, headers: dict[str, str],
                 timeout: float, args: list[str]) -> tuple[int, Any, str]:
    scheme, host, port = _origin(url)
    if port is None:
        port = 443 if scheme == "https" else 80
    pool_key = (scheme, host, port)

    req_headers = dict(headers)
    req_headers.setdefault("Connection", "keep-alive")

    parts = urllib.parse.urlsplit(url)
    selector = parts.path or "/"
    if parts.query:
        selector = f"{selector}?{parts.query}"

    conn, was_pooled = _checkout_conn(pool_key)
    if conn is None:
        conn = _new_conn(scheme, host, port, timeout)

    try:
        conn.request(method, selector, body=body, headers=req_headers)
        resp = conn.getresponse()
        raw = resp.read()
        _checkin_conn(pool_key, conn)
        return resp.status, resp.headers, _body_text(raw)
    except _RETRY_EXC as exc:
        with contextlib.suppress(Exception):
            conn.close()

        if not was_pooled:
            if _is_timeout(exc):
                raise subprocess.TimeoutExpired(["gh", *args], timeout) from None
            raise _Transport(str(exc)) from None

        # Retry once on a fresh connection outside the pool lock
        fresh_conn = _new_conn(scheme, host, port, timeout)
        try:
            fresh_conn.request(method, selector, body=body, headers=req_headers)
            resp = fresh_conn.getresponse()
            raw = resp.read()
            _checkin_conn(pool_key, fresh_conn)
            return resp.status, resp.headers, _body_text(raw)
        except _RETRY_EXC as retry_exc:
            with contextlib.suppress(Exception):
                fresh_conn.close()
            if _is_timeout(retry_exc):
                raise subprocess.TimeoutExpired(["gh", *args], timeout) from None
            raise _Transport(str(retry_exc)) from None


def _send(method: str, url: str, body: bytes | None, headers: dict[str, str],
          timeout: float, args: list[str]) -> tuple[int, Any, str]:
    if net._gate is not None:
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            try:
                with net.urlopen(req, timeout, opener=_OPENER) as resp:
                    return resp.status, resp.headers, _body_text(resp.read())
            except urllib.error.HTTPError as exc:
                return exc.code, exc.headers, _body_text(exc.read())
        except OSError as exc:
            if _is_timeout(exc):
                raise subprocess.TimeoutExpired(["gh", *args], timeout) from None
            raise _Transport(str(exc)) from None

    return _send_pooled(method, url, body, headers, timeout, args)


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urllib.parse.urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        port = -1
    if port is None:
        port = {"http": 80, "https": 443}.get(parts.scheme)
    return parts.scheme, (parts.hostname or "").lower(), port


def _plain_url(url: str) -> bool:
    """No backslash, control or non-ASCII character, and no user-info: nothing that two URL parsers could read differently."""
    return url.isascii() and url.isprintable() and "\\" not in url and " " not in url \
        and "@" not in urllib.parse.urlsplit(url).netloc


_SEGMENT = re.compile(r"[A-Za-z0-9._~%-]+")


def _safe_endpoint(endpoint: str) -> bool:
    """A REST path of plain segments: no empty, ``.`` or ``..`` segment (also percent-encoded), and no ``?``, ``#``, backslash, whitespace or
    control character (the query string is built from ``-f`` fields only)."""
    segments = endpoint[1:].split("/") if endpoint.startswith("/") else endpoint.split("/")
    return all(_SEGMENT.fullmatch(seg) and _decoded_ok(seg) for seg in segments)


def _decoded_ok(segment: str) -> bool:
    """The segment is not ``.`` or ``..`` even after percent-decoding, and decodes to no slash or backslash."""
    decoded = urllib.parse.unquote(segment)
    return decoded not in (".", "..") and "/" not in decoded and "\\" not in decoded


def _api_base() -> str:
    """``https://api.github.com``, or the loopback test server when both test variables say so."""
    override = os.environ.get("PORCHLAMP_GH_API")
    if override and os.environ.get("PORCHLAMP_ALLOW_TEST_API") == "1":
        scheme, host, _ = _origin(override)
        if scheme in ("http", "https") and host in LOOPBACK_HOSTS:
            return override.rstrip("/")
    return DEFAULT_BASE


def _request_headers(base: str, token: str | None, has_body: bool) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": USER_AGENT,
    }
    if token and base == AUTH_BASE:
        headers["Authorization"] = f"Bearer {token}"
    if has_body:
        headers["Content-Type"] = "application/json"
    return headers


def _send_following(method: str, url: str, base: str, body: bytes | None, headers: dict[str, str],
                    timeout: float, args: list[str]) -> tuple[int, Any, str]:
    """``_send``, following same-origin redirects by hand (at most ``MAX_REDIRECTS``); a redirect anywhere else is
    refused and nothing is sent to the other origin."""
    hops = 0
    while True:
        status, resp_headers, text = _send(method, url, body, headers, timeout, args)
        if status == 304 or not 300 <= status < 400:
            return status, resp_headers, text
        location = _header(resp_headers, "location")
        target = urllib.parse.urljoin(url, location) if location else None
        same_origin = target is not None and _plain_url(target) and _origin(target) == _origin(base)
        if not same_origin:
            raise _Refused(f"redirect to another host refused (HTTP {status})")
        if hops >= MAX_REDIRECTS:
            raise _Refused(f"too many redirects (HTTP {status})")
        hops += 1
        url = target


def _message(text: str) -> str | None:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    msg = data.get("message") if isinstance(data, dict) else None
    return msg if isinstance(msg, str) and msg else None


def _has_errors(text: str) -> bool:
    try:
        data = json.loads(text)
    except ValueError:
        return False
    return isinstance(data, dict) and bool(data.get("errors"))


def _result(args: list[str], is_graphql: bool, status: int, text: str) -> subprocess.CompletedProcess[str]:
    argv = ["gh", *args]
    if is_graphql:
        if status >= 400:
            return subprocess.CompletedProcess(argv, 1, text, f"gh: HTTP {status}\n")
        return subprocess.CompletedProcess(argv, 1 if _has_errors(text) else 0, text, "")
    if status >= 400:
        msg = _message(text)
        stderr = f"gh: {msg} (HTTP {status})\n" if msg else f"gh: HTTP {status}\n"
        return subprocess.CompletedProcess(argv, 1, text, stderr)
    return subprocess.CompletedProcess(argv, 0, text, "")


def _http_run(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    method, endpoint, fields, is_graphql = _parse(args)
    if not is_graphql and not _safe_endpoint(endpoint):
        return subprocess.CompletedProcess(["gh", *args], 1, "", "gh: refusing an unsafe path\n")
    base = _api_base()
    resource = _resource_for(endpoint, is_graphql)
    body: bytes | None = None
    if is_graphql:
        url = f"{base}/graphql"
        variables = {k: v for k, v in fields if k != "query"}
        query = next(v for k, v in fields if k == "query")
        body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    elif method == "GET":
        url = _url(base, endpoint, fields)
    else:
        url = f"{base}/{endpoint.lstrip('/')}"
        if fields:
            body = json.dumps(dict(fields)).encode("utf-8")

    attempts = 0
    norm_endpoint = f"/{endpoint.lstrip('/')}"
    is_get = (method == "GET" and body is None)

    while True:
        token = _select_token(resource, base)
        tokens = _configured_tokens(base)
        token_idx = tokens.index(token) if (token and token in tokens) else None

        headers = _request_headers(base, token, body is not None)
        cached_entry = _etag_get(url) if is_get else None
        if cached_entry is not None:
            headers["If-None-Match"] = cached_entry[0]

        _gate(resource, token_idx)
        t0 = time.perf_counter()
        try:
            status, resp_headers, text = _send_following(method, url, base, body, headers, timeout, args)
        except (_Transport, subprocess.TimeoutExpired):
            duration_ms = (time.perf_counter() - t0) * 1000.0
            cost.record_call(
                endpoint=norm_endpoint,
                method=method,
                status=0,
                status_304=False,
                graphql_points=0,
                bytes_in=0,
                duration_ms=duration_ms,
            )
            raise

        duration_ms = (time.perf_counter() - t0) * 1000.0

        if status == 304 and is_get and cached_entry is not None:
            _record(resp_headers, resource, token_idx)
            cost.record_call(
                endpoint=norm_endpoint,
                method=method,
                status=304,
                status_304=True,
                graphql_points=0,
                bytes_in=0,
                duration_ms=duration_ms,
            )
            return _result(args, is_graphql, 200, cached_entry[2])

        _record(resp_headers, resource, token_idx)

        graphql_points = 0
        if is_graphql and status == 200:
            graphql_points = 1
            try:
                payload = json.loads(text)
                if isinstance(payload, dict):
                    rl = payload.get("data", {}).get("rateLimit")
                    if isinstance(rl, dict) and isinstance(rl.get("cost"), int):
                        graphql_points = rl["cost"]
            except Exception:
                pass

        bytes_in = len(text.encode("utf-8"))
        cost.record_call(
            endpoint=norm_endpoint,
            method=method,
            status=status,
            status_304=False,
            graphql_points=graphql_points,
            bytes_in=bytes_in,
            duration_ms=duration_ms,
        )

        if is_get and status == 200:
            etag_val = _header(resp_headers, "etag")
            if etag_val:
                headers_dict = {str(k): str(v) for k, v in resp_headers.items()}
                for k, v in list(headers_dict.items()):
                    headers_dict[k.lower()] = v
                _etag_put(url, etag_val, headers_dict, text)

        if status in (403, 429) and attempts < MAX_RETRIES and _limited(resp_headers):
            attempts += 1
            key = _header(resp_headers, "x-ratelimit-resource") or resource
            remaining = _header(resp_headers, "x-ratelimit-remaining") or "0"
            _pause(key, int(remaining) if remaining.isdigit() else 0, _retry_seconds(resp_headers), token_idx=token_idx)
            continue
        return _result(args, is_graphql, status, text)
