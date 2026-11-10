"""Request signing for the phone LAN server. Standard library only.

A client signs every request with HMAC-SHA256 keyed by the shared LAN token. The token itself never crosses the wire,
a captured request cannot be replayed (timestamp window plus one-time nonce), and the server signs its JSON replies
with the client's nonce so the client can tell a genuine answer from a forged or replayed one.

Request headers: ``X-Porch-Ts`` (unix seconds), ``X-Porch-Nonce`` (32 lowercase hex characters) and
``Authorization: Porch-HMAC-SHA256 <hex>`` where the MAC covers
``"{method}\\n{target}\\n{ts}\\n{nonce}\\n{sha256(body)}"``. Reply header: ``X-Porch-Mac`` over
``"{request_nonce}\\n{status}\\n{sha256(reply body)}"``.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import OrderedDict

TS_HEADER = "X-Porch-Ts"
NONCE_HEADER = "X-Porch-Nonce"
MAC_HEADER = "X-Porch-Mac"
SCHEME = "Porch-HMAC-SHA256"
TS_WINDOW_S = 120
NONCE_TTL_S = 300
NONCE_CAP = 10_000
LOCK_FAILURES = 10
LOCK_WINDOW_S = 60
LOCK_DURATION_S = 300
THROTTLE_IPS = 1024

_TS = re.compile(r"[0-9]{1,12}")
_NONCE = re.compile(r"[0-9a-f]{32}")
_MAC = re.compile(r"[0-9a-f]{64}")
_AUTH = re.compile(r"Porch-HMAC-SHA256 ([0-9a-f]{64})")


def _key(token: str) -> bytes:
    return token.encode("utf-8")


def request_mac(token: str, method: str, target: str, body: bytes, ts: int | str, nonce: str) -> str:
    """The hex MAC of one request."""
    message = f"{method}\n{target}\n{ts}\n{nonce}\n{hashlib.sha256(body).hexdigest()}"
    return hmac.new(_key(token), message.encode("utf-8"), hashlib.sha256).hexdigest()


def sign_request(token: str, method: str, target: str, body: bytes, ts: int | None = None,
                 nonce: str | None = None) -> dict[str, str]:
    """The three headers that authenticate one request. ``target`` is path plus query exactly as sent."""
    ts = int(time.time()) if ts is None else int(ts)
    nonce = secrets.token_hex(16) if nonce is None else nonce
    mac = request_mac(token, method, target, body, ts, nonce)
    return {TS_HEADER: str(ts), NONCE_HEADER: nonce, "Authorization": f"{SCHEME} {mac}"}


def response_mac(token: str, request_nonce: str, status: int, body: bytes) -> str:
    """The hex MAC the server puts in ``X-Porch-Mac`` on a JSON reply."""
    message = f"{request_nonce}\n{status}\n{hashlib.sha256(body).hexdigest()}"
    return hmac.new(_key(token), message.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_response(token: str, request_nonce: str, status: int, body: bytes, mac: str | None) -> bool:
    """True when ``mac`` is the server's MAC for this nonce, status and body."""
    if not isinstance(mac, str) or not _MAC.fullmatch(mac):
        return False
    return hmac.compare_digest(mac.encode("ascii"), response_mac(token, request_nonce, status, body).encode("ascii"))


def _header(headers, name: str) -> str | None:
    """Case-insensitive header lookup over an ``email.message.Message`` or a plain mapping."""
    value = headers.get(name)
    if value is not None:
        return value
    wanted = name.lower()
    for key in list(headers.keys()):
        if isinstance(key, str) and key.lower() == wanted:
            return headers[key]
    return None


class NonceStore:
    """Nonces already used: at most ``cap`` entries, each kept ``ttl`` seconds. Thread-safe, fails closed when full."""

    def __init__(self, cap: int = NONCE_CAP, ttl: float = NONCE_TTL_S):
        self.cap = cap
        self.ttl = ttl
        self._items: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, nonce: str) -> bool:
        return nonce in self._items

    def _purge(self, now: float) -> None:
        while self._items:
            oldest = next(iter(self._items))
            if self._items[oldest] > now - self.ttl:
                break
            del self._items[oldest]

    def add_if_new(self, nonce: str, now: float) -> bool:
        """Record ``nonce``; False when it was already seen or the store is full of live entries."""
        with self._lock:
            self._purge(now)
            if nonce in self._items or len(self._items) >= self.cap:
                return False
            self._items[nonce] = now
            return True


def check_request(token: str, method: str, target: str, body: bytes, headers, now: float,
                  seen: NonceStore) -> str:
    """``"ok"`` for a genuine, fresh, unused request, else the category of the failure (never a header value)."""
    ts = _header(headers, TS_HEADER)
    nonce = _header(headers, NONCE_HEADER)
    auth = _header(headers, "Authorization")
    if ts is None or nonce is None or auth is None:
        return "missing-headers"
    got = _AUTH.fullmatch(auth)
    if not _TS.fullmatch(ts) or not _NONCE.fullmatch(nonce) or got is None:
        return "malformed"
    if abs(now - int(ts)) > TS_WINDOW_S:
        return "stale"
    want = request_mac(token, method, target, body, ts, nonce)
    if not hmac.compare_digest(got.group(1).encode("ascii"), want.encode("ascii")):
        return "bad-mac"
    if not seen.add_if_new(nonce, now):
        return "replay"
    return "ok"


def verify_request(token: str, method: str, target: str, body: bytes, headers, now: float,
                   seen: NonceStore) -> bool:
    """True only for a request signed with ``token`` within the time window and not seen before."""
    return check_request(token, method, target, body, headers, now, seen) == "ok"


def headers_look_signed(headers, now: float) -> bool:
    """Cheap shape and freshness check (no MAC), for replies that must be decided before the body can be read."""
    ts = _header(headers, TS_HEADER)
    nonce = _header(headers, NONCE_HEADER)
    auth = _header(headers, "Authorization")
    if ts is None or nonce is None or auth is None:
        return False
    if not _TS.fullmatch(ts) or not _NONCE.fullmatch(nonce) or _AUTH.fullmatch(auth) is None:
        return False
    return abs(now - int(ts)) <= TS_WINDOW_S


class FailureThrottle:
    """Per client IP: ``LOCK_FAILURES`` failed authentications within ``LOCK_WINDOW_S`` lock the IP for
    ``LOCK_DURATION_S``. The table holds at most ``max_ips`` addresses; the oldest is dropped first."""

    def __init__(self, clock=time.monotonic, max_ips: int = THROTTLE_IPS, failures: int = LOCK_FAILURES,
                 window: float = LOCK_WINDOW_S, lockout: float = LOCK_DURATION_S):
        self.clock = clock
        self.max_ips = max_ips
        self.failures = failures
        self.window = window
        self.lockout = lockout
        self._table: OrderedDict[str, list] = OrderedDict()  # ip -> [failure times, locked-until]
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._table)

    def locked(self, ip: str) -> bool:
        with self._lock:
            row = self._table.get(ip)
            if row is None:
                return False
            if row[1] > self.clock():
                return True
            if row[1]:  # lockout over: start clean
                del self._table[ip]
            return False

    def record_failure(self, ip: str) -> None:
        with self._lock:
            now = self.clock()
            row = self._table.get(ip)
            if row is None:
                row = self._table[ip] = [[], 0.0]
                while len(self._table) > self.max_ips:
                    self._table.popitem(last=False)
            row[0] = [t for t in row[0] if t > now - self.window]
            row[0].append(now)
            if len(row[0]) >= self.failures:
                row[1] = now + self.lockout
                row[0] = []
