"""GitHub calls for the collectors: the ``gh`` CLI, or HTTPS with ``urllib``.

``PORCHLAMP_GH=http`` selects the HTTPS transport (an Android app has no
``gh``). Any other value, unset included, runs ``gh`` exactly as before.

The HTTPS transport also keeps the rate-limit floor that ``gather_layer.sh``
enforced by sampling and killing: before a request, when the last response
for its resource reported fewer calls left than the floor and a reset still
lies in the future, the transport sleeps until that reset.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from typing import Any

API_VERSION = "2022-11-28"
USER_AGENT = "porchlamp-phone/1"
DEFAULT_BASE = "https://api.github.com"
DEFAULT_FLOOR = 500
SEARCH_FLOOR = 5
RESET_PAD = 15
MIN_WAIT = 10
NO_RESET_WAIT = 60
MAX_RETRIES = 2

#: resource -> (remaining calls, reset epoch seconds), from the last response.
_BUDGET: dict[str, tuple[int, float]] = {}
_sleep = time.sleep
_now = time.time

_INT = re.compile(r"-?\d+\Z")


class _Transport(Exception):
    """A transport failure (DNS, refused, reset); the message has no secrets."""


def run(args: Sequence[str], timeout: float = 90) -> subprocess.CompletedProcess[str]:
    """Run one ``gh`` call; ``args`` is what followed ``gh`` on the command line."""
    if os.environ.get("PORCHLAMP_GH") != "http":
        return subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)
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


def _record(headers: Any, default: str) -> None:
    remaining = _header(headers, "x-ratelimit-remaining")
    reset = _header(headers, "x-ratelimit-reset")
    if remaining is None or reset is None:
        return
    key = _header(headers, "x-ratelimit-resource") or default
    try:
        _BUDGET[key] = (int(remaining), float(reset))
    except ValueError:
        return


def _pause(resource: str, remaining: int, seconds: float) -> None:
    wait = max(MIN_WAIT, seconds)
    print(f"ghcli: waiting {math.ceil(wait)}s for the {resource} reset "
          f"(remaining {remaining})", file=sys.stderr)
    _BUDGET.pop(resource, None)
    _sleep(wait)


def _gate(resource: str) -> None:
    record = _BUDGET.get(resource)
    if record is None:
        return
    remaining, reset = record
    now = _now()
    if remaining < _floor(resource) and reset > now:
        _pause(resource, remaining, reset - now + RESET_PAD)


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


def _send(method: str, url: str, body: bytes | None, headers: dict[str, str],
          timeout: float, args: list[str]) -> tuple[int, Any, str]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.headers, _body_text(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, _body_text(exc.read())
    except OSError as exc:
        if _is_timeout(exc):
            raise subprocess.TimeoutExpired(["gh", *args], timeout) from None
        raise _Transport(str(exc)) from None


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
    base = os.environ.get("PORCHLAMP_GH_API", DEFAULT_BASE).rstrip("/")
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
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": USER_AGENT,
    }
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    attempts = 0
    while True:
        _gate(resource)
        status, resp_headers, text = _send(method, url, body, headers, timeout, args)
        _record(resp_headers, resource)
        if status in (403, 429) and attempts < MAX_RETRIES and _limited(resp_headers):
            attempts += 1
            key = _header(resp_headers, "x-ratelimit-resource") or resource
            remaining = _header(resp_headers, "x-ratelimit-remaining") or "0"
            _pause(key, int(remaining) if remaining.isdigit() else 0, _retry_seconds(resp_headers))
            continue
        return _result(args, is_graphql, status, text)
