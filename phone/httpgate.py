"""The one function every outgoing HTTP request of the engine passes through. Standard library only.

``urlopen(request_or_url, timeout)`` is a drop-in for ``urllib.request.urlopen`` that

* refuses a host that is not on ``ALLOWED_HOSTS`` (and a plain-http URL, a URL with user-info, an odd port, or an
  ``Authorization`` header for any host but ``api.github.com``): it raises ``HostRefused``, counts and logs the
  refusal, and sends nothing;
* counts calls and bytes sent and received per host, per day and per network type in ``NetStats``;
* is the pause point: before each request it blocks while the controls say so (user pause, battery, heat, disk, no
  Wi-Fi, a slow-network wait) and raises ``Stopped`` once ``stop()`` was called, checking every ``slice_s`` seconds;
* feeds the GitHub budget (``x-ratelimit-*`` of every api.github.com reply) and the "last progress" clock;
* never writes a header, a URL query or a token into the log: a log line is the method, the host, the status;
* treats every hop of a redirect as a new request: the new address goes through the same checks (allow-list, https on
  443, no user-info), is held and counted under the host that really serves it, and ``Authorization`` is dropped when
  the host changes. A redirect to an unlisted host raises ``HostRefused`` and the target sees nothing.

``Stopped`` derives from ``BaseException`` on purpose: the gatherer wraps whole collectors in ``except Exception``, and
a stop must not be swallowed there and counted as a failed repo.

Bytes are counted at the HTTP level (request line, headers, body; status line, headers, body), not TLS framing.
"""

from __future__ import annotations

import contextlib
import errno
import io
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any

from phone.netstats import NetStats

#: Every host the code may reach. ``contacts`` on the status screen lists only hosts actually used.
ALLOWED_HOSTS: frozenset[str] = frozenset({
    "api.github.com",              # GitHub: project facts, pull requests, release metadata
    "uploads.github.com",          # GitHub Release asset uploads for the private drop-box repo
    "raw.githubusercontent.com",   # public files of a repo (governance, README, pom.xml)
    "deps.dev", "api.deps.dev",    # which package belongs to which project
    "ecosyste.ms", "packages.ecosyste.ms",   # how many projects depend on a package
    "registry.npmjs.org", "api.npmjs.org",   # npm download counts
    "pypi.org", "pypistats.org",             # PyPI download counts
    "crates.io", "static.crates.io",         # crates.io download counts
    "api.securityscorecards.dev",            # OpenSSF Scorecard
    "data.gharchive.org",                    # GH Archive hourly dumps (not used by the phone steps)
})
#: What the phone engine may reach: every listed host except the ones no phone step uses.
PHONE_HOSTS: frozenset[str] = ALLOWED_HOSTS - {"data.gharchive.org"}
#: The only hosts a credential may be sent to.
TOKEN_HOSTS: frozenset[str] = frozenset({"api.github.com", "uploads.github.com"})

WHAT = {
    "api.github.com": "Pull requests and project details",
    "uploads.github.com": "Drop-box archive upload",
    "raw.githubusercontent.com": "Public files of each project",
    "deps.dev": "Which package belongs to which project",
    "api.deps.dev": "Which package belongs to which project",
    "ecosyste.ms": "How many projects depend on each package",
    "packages.ecosyste.ms": "How many projects depend on each package",
    "registry.npmjs.org": "Download counts",
    "api.npmjs.org": "Download counts",
    "pypi.org": "Download counts",
    "pypistats.org": "Download counts",
    "crates.io": "Download counts",
    "static.crates.io": "Download counts",
    "api.securityscorecards.dev": "Security scorecard of each project",
    "data.gharchive.org": "Public GitHub activity archive",
}

MAX_ERROR_BODY = 4 * 1024 * 1024
#: ``urllib.request.urlopen`` as imported; a test that replaces it with a fake is still honoured by the default path.
_REAL_URLOPEN = urllib.request.urlopen
LOGGED_STATUSES = (401, 403, 429)  # plus every 5xx; 2xx and 404 are counted, not logged


class Stopped(BaseException):
    """``stop()`` was called: the collector must unwind. Not an ``Exception`` so ``except Exception`` lets it by."""


class HostRefused(urllib.error.URLError):
    """The request was refused before anything was sent (host not allowed, plain http, credential for another host)."""


class WouldBlock(Exception):
    """``wait=False`` and the controls say the request must wait: ``.reason`` is the controls' reason object."""

    def __init__(self, reason: Any) -> None:
        super().__init__(getattr(reason, "code", "blocked"))
        self.reason = reason


def is_timeout(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", None)
    return isinstance(exc, (TimeoutError, socket.timeout)) or isinstance(reason, (TimeoutError, socket.timeout))


#: errno values of a network that is down or unreachable, not a fault of the request.
_UNREACHABLE = frozenset({errno.ENETDOWN, errno.ENETUNREACH, errno.EHOSTUNREACH})


def is_no_answer(exc: BaseException) -> bool:
    """Whether a failed request never got an answer from the other end: a timeout, a name that did not resolve,
    a refused, reset or dropped connection, or a network that is down or unreachable. These come in bursts when the
    internet is lost for a moment, and each one counts toward the slow-network wait. A certificate failure, a bad URL
    or a bug in our own code is not one: waiting would not cure it."""
    if is_timeout(exc):
        return True
    cause = getattr(exc, "reason", exc)
    if isinstance(cause, (socket.gaierror, ConnectionError)):
        return True
    return isinstance(cause, OSError) and cause.errno in _UNREACHABLE


def _header_bytes(headers: Any) -> int:
    try:
        return sum(len(str(k)) + len(str(v)) + 4 for k, v in headers.items())
    except AttributeError:
        return 0


def _request_size(req: urllib.request.Request, parts: urllib.parse.SplitResult) -> int:
    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query
    head = len(req.get_method()) + len(target) + 11 + len(parts.netloc) + 8  # "GET /x HTTP/1.1" and "Host: h"
    head += sum(len(k) + len(v) + 4 for k, v in req.header_items())
    body = req.data
    if isinstance(body, (bytes, bytearray)):
        return head + len(body)
    if body is not None:
        for key, value in req.header_items():
            if key.lower() == "content-length":
                with contextlib.suppress(ValueError):
                    return head + max(0, int(value))
    return head


class _Counted:
    """The response of an allowed request: counts every byte that is read, otherwise behaves like the response."""

    def __init__(self, resp: Any, gate: Gate, host: str, net: str, scope: str = "collect") -> None:
        self._resp = resp
        self._gate = gate
        self._host = host
        self._net = net
        self._scope = scope

    def _count(self, data: bytes) -> bytes:
        if data and self._gate.stats is not None:
            self._gate.stats.add(self._host, self._net, recv=len(data))
        return data

    def _guard(self, fn: Callable[..., bytes], *args: Any) -> bytes:
        try:
            return self._count(fn(*args))
        except BaseException as exc:
            if is_timeout(exc):
                self._gate._timed_out(self._host, self._scope)
            raise

    def read(self, *args: Any) -> bytes:
        return self._guard(self._resp.read, *args)

    def read1(self, *args: Any) -> bytes:
        return self._guard(self._resp.read1, *args)

    def readline(self, *args: Any) -> bytes:
        return self._guard(self._resp.readline, *args)

    def __iter__(self) -> Iterator[bytes]:
        while True:
            line = self.readline()
            if not line:
                return
            yield line

    def __enter__(self) -> _Counted:
        return self

    def __exit__(self, *exc: Any) -> Any:
        leave = getattr(self._resp, "__exit__", None)
        if leave is not None:
            return leave(*exc)
        self.close()
        return None

    def close(self) -> None:
        self._resp.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resp, name)


def _host_of(url: str | None, fallback: str = "") -> str:
    """The lower-case host of ``url``, or ``fallback`` when there is none (a fake response has no address)."""
    if not url:
        return fallback
    try:
        return (urllib.parse.urlsplit(url).hostname or fallback).lower()
    except ValueError:
        return fallback


class _GateRedirect(urllib.request.HTTPRedirectHandler):
    """Each redirect hop is a new request for the gate: admitted, held, and counted under the host that serves it.

    urllib's own limit on hops (10, and 4 repeats of one address) stays in force."""

    def __init__(self, gate: Gate) -> None:
        self.gate = gate

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        gate = self.gate
        scope, wait = getattr(gate._tls, "call", ("collect", True))
        old_host = _host_of(req.full_url)
        net = gate._net()
        if gate.stats is not None:
            gate.stats.add(old_host, net, recv=_header_bytes(headers) + 15)       # the redirect reply itself
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        parts = urllib.parse.urlsplit(new.full_url)
        host = (parts.hostname or "").lower()
        if scope == "upload":
            raise gate._refuse(host, "drop-box upload requests do not follow redirects")
        if host != old_host:                                  # a credential never follows a change of host
            for key in [k for k in new.headers if k.lower() == "authorization"]:
                del new.headers[key]
            for key in [k for k in new.unredirected_hdrs if k.lower() == "authorization"]:
                del new.unredirected_hdrs[key]
        gate._admit(new, parts, host, scope)
        seen = getattr(req, "redirect_dict", None)
        if seen is not None and (seen.get(newurl, 0) >= self.max_repeats or len(seen) >= self.max_redirections):
            return new                                        # urllib stops here without sending: nothing to count
        gate._await_clear(host, scope, wait)
        if gate.stats is not None:
            gate.stats.add(host, net, calls=1, sent=_request_size(new, parts),
                           token_sent=new.has_header("Authorization") and host in gate.token_hosts)
        gate.log(f"redirect {old_host} -> {host}")
        return new


_KNOWN_SCOPES: frozenset[str] = frozenset({"collect", "upload", "token_test", "self_check"})
_RESERVED_UPLOAD_REPOS: frozenset[str] = frozenset({
    "porchlamp",
    "porchlamp-engine",
    "porchlamp-phone",
    "contributor-value-index",
})


def _upload_route_methods(tail: list[str]) -> tuple[str, ...] | None:
    """The methods the drop-box code uses on one route below ``/repos/<owner>/<drop-repo>``, or ``None`` for no route."""
    if not tail:
        return ("GET",)
    if tail == ["contents", ".github", "workflows"]:
        return ("GET",)
    if tail == ["releases"]:
        return ("GET", "POST")
    if len(tail) == 2 and tail[0] == "releases" and tail[1].isdigit():
        return ("GET", "PATCH")
    if len(tail) == 3 and tail[0] == "releases" and tail[1].isdigit() and tail[2] == "assets":
        return ("POST",)
    if len(tail) == 3 and tail[0] == "releases" and tail[1] == "assets" and tail[2].isdigit():
        return ("DELETE",)
    if len(tail) == 3 and tail[0] == "releases" and tail[1] == "tags" and tail[2]:
        return ("GET",)
    return None


def _admit_upload_path(path: str, method: str) -> str | None:
    """Return ``None`` if ``method`` on ``path`` stays strictly inside a non-reserved drop-box repository's release/safety surface."""
    decoded = urllib.parse.unquote(path or "")
    if "\\" in decoded or "//" in decoded:
        return "malformed upload path"
    segs = decoded.split("/")
    if any(seg in ("..", ".") for seg in segs[1:]):
        return "path traversal in upload path"
    if len(segs) < 4 or segs[0] != "" or segs[1] != "repos" or not segs[2] or not segs[3]:
        return "upload scope only reaches /repos/<owner>/<drop-repo>"
    repo_name = segs[3].lower()
    if repo_name in _RESERVED_UPLOAD_REPOS:
        return "upload scope never touches production or code repositories"
    methods = _upload_route_methods(segs[4:])
    if methods is None:
        return "upload scope only reaches release and workflow-absence endpoints"
    if method not in methods:
        return "method not allowed on this drop-box route"
    return None


class Gate:
    """State of the gate: the allow-list, the counters, the controls, the stop flag and the GitHub budget."""

    def __init__(self, *, allowed: frozenset[str] | set[str] = ALLOWED_HOSTS,
                 token_hosts: frozenset[str] | set[str] = TOKEN_HOSTS,
                 http_hosts: frozenset[str] | set[str] = frozenset(),
                 budget_hosts: frozenset[str] | set[str] = frozenset({"api.github.com"}),
                 allow_collect_mutations: bool = False,
                 stats: NetStats | None = None, controls: Any = None,
                 log: Callable[[str], object] | None = None,
                 clock: Callable[[], float] = time.time, slice_s: float = 0.5,
                 wait_fn: Callable[[float], None] | None = None) -> None:
        self.allowed = frozenset(allowed)
        self.token_hosts = frozenset(token_hosts)
        self.http_hosts = frozenset(http_hosts)  # hosts reachable without TLS or on a non-default port (tests only)
        self.budget_hosts = frozenset(budget_hosts)  # hosts whose x-ratelimit-* headers feed the GitHub budget
        self.allow_collect_mutations = bool(allow_collect_mutations)
        self.stats = stats
        self.controls = controls
        self._log = log
        self.clock = clock
        self.slice_s = slice_s
        self._cond = threading.Condition()
        self._wait_fn = wait_fn
        self._stopped = threading.Event()
        self._tls = threading.local()
        self._redirecting_opener = urllib.request.build_opener(_GateRedirect(self))
        self._lock = threading.Lock()
        self._budget: dict[str, dict[str, float]] = {}
        self._rates: dict[int, tuple[str, float]] = {}   # thread id -> (resource, resume epoch) of its rate wait
        self.progress_ts: float = clock()
        self.on_rate_wait_end: Callable[[str, float], object] | None = None

    # ---- logging ----------------------------------------------------------------------------------------------
    def log(self, text: str) -> None:
        if self._log is not None:
            self._log(text)

    # ---- stop / wake ------------------------------------------------------------------------------------------
    def stop(self) -> None:
        """Every request from now on, and every wait in progress, raises ``Stopped`` until ``rearm()``."""
        self._stopped.set()
        self.kick()

    def rearm(self) -> None:
        self._stopped.clear()

    @property
    def stopped(self) -> bool:
        return self._stopped.is_set()

    def bind_cancel(self, event: threading.Event | None) -> None:
        """Make this thread's requests raise ``Stopped`` when ``event`` is set (a restarted collector's old thread)."""
        self._tls.cancel = event

    def cancel_event(self) -> threading.Event | None:
        """This thread's cancel binding (set by :meth:`bind_cancel`), or None: a worker thread adopts it."""
        return getattr(self._tls, "cancel", None)

    def kick(self) -> None:
        """Wake every waiting request so it re-reads the controls at once (resume, settings change, new facts)."""
        with self._cond:
            self._cond.notify_all()

    def _check_stop(self) -> None:
        cancel = getattr(self._tls, "cancel", None)
        if self._stopped.is_set() or (cancel is not None and cancel.is_set()):
            raise Stopped("collection was stopped")

    def _wait(self, seconds: float) -> None:
        if self._wait_fn is not None:
            self._wait_fn(seconds)
            return
        with self._cond:
            self._cond.wait(seconds)

    # ---- the GitHub budget and the progress clock --------------------------------------------------------------
    def touch_progress(self) -> None:
        self.progress_ts = self.clock()

    def feed_budget(self, headers: Any, default_resource: str = "core") -> None:
        def num(name: str) -> float | None:
            try:
                return float(headers.get(name))
            except (TypeError, ValueError, AttributeError):
                return None

        remaining, reset, limit = num("x-ratelimit-remaining"), num("x-ratelimit-reset"), num("x-ratelimit-limit")
        if remaining is None or reset is None:
            return
        try:
            resource = headers.get("x-ratelimit-resource") or default_resource
        except AttributeError:
            resource = default_resource
        with self._lock:
            old = self._budget.get(resource, {})
            self._budget[resource] = {"remaining": remaining, "reset": reset,
                                      "limit": limit if limit is not None else old.get("limit", 0.0),
                                      "t": self.clock()}

    def budget(self) -> dict[str, float] | None:
        """The most recently updated resource's ``{remaining, reset, limit}``, or None before any reply."""
        with self._lock:
            if not self._budget:
                return None
            return dict(max(self._budget.values(), key=lambda r: r["t"]))

    def rate_state(self) -> tuple[str, float] | None:
        """``(resource, resume epoch)`` while a rate-limit wait is in progress: of several threads waiting, the wait
        that ends last."""
        with self._lock:
            if not self._rates:
                return None
            return max(self._rates.values(), key=lambda rate: rate[1])

    # ---- admission --------------------------------------------------------------------------------------------
    def _refuse(self, host: str, why: str) -> HostRefused:
        if self.stats is not None:
            self.stats.refuse(host)
        self.log(f"REFUSED {host or '?'}: {why}; nothing was sent")
        return HostRefused(f"{host or '?'}: {why}")

    def _admit(
        self,
        req: urllib.request.Request,
        parts: urllib.parse.SplitResult,
        host: str,
        scope: str = "collect",
    ) -> None:
        if scope not in _KNOWN_SCOPES:
            raise self._refuse(host, "unknown request scope")
        if parts.scheme not in ("http", "https"):
            raise self._refuse(host, "only http and https are used")
        if not host or not host.isascii():
            raise self._refuse(host, "host name not accepted")
        if "@" in parts.netloc:
            raise self._refuse(host, "a URL with user-info is not accepted")
        try:
            port = parts.port
        except ValueError:
            raise self._refuse(host, "bad port") from None
        if host not in self.allowed:
            raise self._refuse(host, "host is not on the allow-list")
        if host not in self.http_hosts and (parts.scheme != "https" or port not in (None, 443)):
            raise self._refuse(host, "only https on the default port is used")

        method = req.get_method().upper()
        if scope == "upload":
            if host not in self.token_hosts:
                raise self._refuse(host, "drop-box uploads only contact GitHub")
            if method not in ("GET", "POST", "PATCH", "DELETE"):
                raise self._refuse(host, "method not allowed for drop-box upload")
            bad_path = _admit_upload_path(parts.path, method)
            if bad_path is not None:
                raise self._refuse(host, bad_path)
            return

        # Non-upload scopes ("collect", "token_test", "self_check") can never contact uploads.github.com or mutate state.
        if host == "uploads.github.com":
            raise self._refuse(host, "uploads.github.com is only used for drop-box uploads")
        if req.has_header("Authorization") and host not in (self.token_hosts - {"uploads.github.com"}):
            raise self._refuse(host, "a credential is only sent to GitHub")
        if scope in ("token_test", "self_check"):
            if method != "GET":
                raise self._refuse(host, "only GET is allowed for token_test and self_check")
            return
        if not self.allow_collect_mutations:
            if method not in ("GET", "POST"):
                raise self._refuse(host, "collector requests never modify remote state")
            if method == "POST" and parts.path != "/graphql":
                raise self._refuse(host, "collector POST is only allowed for /graphql")

    def _await_clear(self, host: str, scope: str, wait: bool) -> None:
        announced = False
        while True:
            self._check_stop()
            block = self.controls.block_reason(host, scope) if self.controls is not None else None
            if block is None:
                return
            if not wait:
                raise WouldBlock(block)
            if not announced:
                announced = True
                self.log(f"waiting before {host}: {getattr(block, 'code', 'blocked')}")
            self._wait(self.slice_s)

    def _net(self) -> str:
        return self.controls.network_type() if self.controls is not None else "wifi"

    # ---- the request ------------------------------------------------------------------------------------------
    def urlopen(self, req: urllib.request.Request | str, timeout: float = 30, *, opener: Any = None,
                scope: str = "collect", wait: bool = True) -> Any:
        """Open ``req`` like ``urllib.request.urlopen`` does, through the checks in the module docstring.

        ``scope`` is ``"collect"`` (the engine's own work), ``"upload"`` (drop-box release upload: held by pause,
        battery, heat, disk and network rules, independent of the collector token), or ``"token_test"`` /
        ``"self_check"`` (an act of the owner: held only by the network rule and the allow-list). ``wait=False`` raises
        ``WouldBlock`` instead of waiting.
        """
        if isinstance(req, str):
            req = urllib.request.Request(req)
        parts = urllib.parse.urlsplit(req.full_url)
        host = (parts.hostname or "").lower()
        self._admit(req, parts, host, scope)
        self._await_clear(host, scope, wait)
        net = self._net()
        authed = req.has_header("Authorization")
        if self.stats is not None:
            self.stats.add(host, net, calls=1, sent=_request_size(req, parts),
                           token_sent=authed and host in self.token_hosts)
        try:
            if opener is not None:
                resp = opener.open(req, timeout=timeout)
            elif urllib.request.urlopen is not _REAL_URLOPEN:
                resp = urllib.request.urlopen(req, timeout=timeout)
            else:
                self._tls.call = (scope, wait)
                resp = self._redirecting_opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc, req, host, net, authed, scope) from None
        except BaseException as exc:
            if not isinstance(exc, (Stopped, HostRefused, WouldBlock, KeyboardInterrupt)):
                self._failed(host, exc, scope)
            raise
        status = int(getattr(resp, "status", 200) or 200)
        served = _host_of(getattr(resp, "url", None), host)             # after a redirect, the host that answered
        if self.stats is not None:
            self.stats.add(served, net, recv=_header_bytes(getattr(resp, "headers", None)) + 15)
        self._answered(served, req.get_method(), status, getattr(resp, "headers", None),
                       authed and served == host, scope)
        return _Counted(resp, self, served, net, scope)

    def _http_error(self, exc: urllib.error.HTTPError, req: urllib.request.Request, host: str, net: str,
                    authed: bool, scope: str) -> urllib.error.HTTPError:
        try:
            body = exc.read(MAX_ERROR_BODY)
        except (OSError, ValueError):
            body = b""  # the connection died or the body was already read: the status line still counts
        served = _host_of(exc.url, host)
        if self.stats is not None:
            self.stats.add(served, net, recv=_header_bytes(exc.headers) + 15 + len(body))
        self._answered(served, req.get_method(), exc.code, exc.headers, authed and served == host, scope)
        with contextlib.suppress(OSError):  # already closed
            exc.close()
        return urllib.error.HTTPError(exc.url, exc.code, exc.msg, exc.headers, io.BytesIO(body))

    def _answered(self, host: str, method: str, status: int, headers: Any, authed: bool, scope: str) -> None:
        if host in self.budget_hosts:
            self.feed_budget(headers)
        limited = status in (403, 429) and headers is not None and (
            headers.get("x-ratelimit-remaining") == "0" or headers.get("retry-after") is not None)
        if (status >= 500 or limited or status == 429) and self.stats is not None:
            self.stats.error()
        if status >= 500 or status in LOGGED_STATUSES:
            self.log(f"{method} {host} {status}{' (rate limit)' if limited else ''}")
        if scope in ("collect", "upload"):
            if status < 500 and not limited:
                self.touch_progress()
            if self.controls is not None:
                self.controls.on_response(host, status, authed and scope == "collect")

    def _failed(self, host: str, exc: BaseException, scope: str = "collect") -> None:
        if self.stats is not None:
            self.stats.error()
        timeout = is_timeout(exc)
        self.log(f"{host}: {'timed out' if timeout else type(exc).__name__}")
        if self.controls is not None and scope in ("collect", "upload"):
            self.controls.on_failure(host, is_no_answer(exc))

    def _timed_out(self, host: str, scope: str = "collect") -> None:
        """A read timed out after the reply began."""
        self._failed(host, TimeoutError(), scope)

    # ---- the rate-limit wait ------------------------------------------------------------------------------------
    def rate_wait(self, resource: str, remaining: int, seconds: float,
                  sleep: Callable[[float], object] | None = None) -> None:
        """Wait ``seconds`` for a GitHub reset. Stop ends it within ``slice_s``; pause does not shorten it.

        ``sleep`` replaces the wait (a test seam: ghcli passes its own ``_sleep`` only when it was replaced).
        """
        started = self.clock()
        until = started + seconds
        me = threading.get_ident()
        with self._lock:
            self._rates[me] = (resource, until)
        self.log(f"waiting {int(seconds)}s for the GitHub {resource} reset (remaining {remaining})")
        try:
            if sleep is not None:
                sleep(seconds)
            else:
                while True:
                    self._check_stop()
                    left = until - self.clock()
                    if left <= 0:
                        break
                    self._wait(min(left, self.slice_s))
        finally:
            with self._lock:
                self._rates.pop(me, None)
        if self.on_rate_wait_end is not None:
            self.on_rate_wait_end(resource, self.clock() - started)

    def sleep(self, seconds: float) -> None:
        """A stop-aware sleep (used where the engine must not hold a stop back)."""
        until = self.clock() + seconds
        while True:
            self._check_stop()
            left = until - self.clock()
            if left <= 0:
                return
            self._wait(min(left, self.slice_s))


_GATE = Gate()
_IMPORT_GATE = _GATE


def install(gate: Gate) -> Gate:
    """Make ``gate`` the process's gate (the engine does this at ``start``); returns the old one."""
    global _GATE
    old, _GATE = _GATE, gate
    return old


def current() -> Gate:
    return _GATE


def installed() -> bool:
    """True once an engine has installed its own gate; False for the one made at import."""
    return _GATE is not _IMPORT_GATE


def urlopen(req: urllib.request.Request | str, timeout: float = 30, *, opener: Any = None,
            scope: str = "collect", wait: bool = True) -> Any:
    """The module-level entry every collector calls."""
    return _GATE.urlopen(req, timeout, opener=opener, scope=scope, wait=wait)


def rate_wait(resource: str, remaining: int, seconds: float,
              sleep: Callable[[float], object] | None = None) -> None:
    _GATE.rate_wait(resource, remaining, seconds, sleep)


def binding() -> threading.Event | None:
    """The calling thread's cancel binding on the process's gate (``Gate.cancel_event``)."""
    return _GATE.cancel_event()


def bind(event: threading.Event | None) -> None:
    """Adopt ``event`` as the calling thread's cancel binding (a worker started by the collector thread)."""
    _GATE.bind_cancel(event)


def sleep(seconds: float) -> None:
    """A stop-aware sleep on the process's gate."""
    _GATE.sleep(seconds)
