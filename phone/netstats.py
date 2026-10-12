"""Per-host, per-day, per-network counters of every outgoing request, kept in ``state/netstats.json``.

One table feeds both the day totals (``status().network``) and the per-host list (``status().contacts``), so the
two can never disagree: the sum of the per-host bytes of a day is the day total. Bytes are counted at the HTTP
level: request line, headers and body sent; status line, headers and body received. TLS framing is not counted.
A day is the phone's local calendar day. Standard library only.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA = 1
KEEP_DAYS = 14
NETS = ("wifi", "mobile")
FLUSH_EVERY_S = 10.0
MAX_REFUSED_HOSTS = 50  # a hostile repo list must not grow the file without bound
MAX_HOST_LEN = 100
MAX_HOSTS = 200  # distinct hosts in one day; more (a flood from many addresses) are counted as "(other)"
OTHER = "(other)"


def day_of(ts: float) -> str:
    """The local calendar day of an epoch time, ``YYYY-MM-DD``."""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _net(net: str) -> str:
    return net if net in NETS else "wifi"


def _empty_cell() -> dict[str, int]:
    return {"calls": 0, "sent": 0, "recv": 0}


class NetStats:
    """Thread-safe counters; ``flush`` writes them atomically (temp file, then ``os.replace``)."""

    def __init__(self, path: Path | str, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self._clock = clock
        self._lock = threading.Lock()
        self._days: dict[str, dict[str, Any]] = {}
        self._meta: dict[str, Any] = {}
        self._dirty = False
        self._last_flush = clock()
        self._load()

    # ---- persistence ------------------------------------------------------------------------------------------
    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            self._set_aside()
            return
        if not isinstance(data, dict) or not isinstance(data.get("days"), dict):
            self._set_aside()
            return
        for day, entry in data["days"].items():
            if isinstance(entry, dict) and isinstance(entry.get("hosts"), dict):
                self._days[str(day)] = entry
        meta = data.get("meta")
        self._meta = meta if isinstance(meta, dict) else {}

    def _set_aside(self) -> None:
        """An unreadable counter file is renamed, never deleted, and the counters start again."""
        stamp = datetime.fromtimestamp(self._clock()).strftime("%Y%m%dT%H%M%S")
        with contextlib.suppress(OSError):  # nothing to move aside; the counters start empty either way
            self.path.rename(self.path.with_name(f"{self.path.name}.bad-{stamp}"))

    def _trim_locked(self) -> None:
        cutoff = day_of(self._clock() - KEEP_DAYS * 86400)
        for day in [d for d in self._days if d < cutoff]:
            del self._days[day]

    def flush(self, force: bool = True) -> None:
        """Write the counters when they changed (``force=False``: at most every ``FLUSH_EVERY_S``)."""
        with self._lock:
            now = self._clock()
            if not self._dirty or (not force and now - self._last_flush < FLUSH_EVERY_S):
                return
            self._trim_locked()
            payload = json.dumps({"schema": SCHEMA, "days": self._days, "meta": self._meta},
                                 sort_keys=True).encode("utf-8")
            self._dirty = False
            self._last_flush = now
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            with self._lock:
                self._dirty = True
            raise

    # ---- counting ---------------------------------------------------------------------------------------------
    def _day_locked(self) -> dict[str, Any]:
        day = day_of(self._clock())
        return self._days.setdefault(day, {"hosts": {}, "refused": {}, "errors": 0})

    def add(self, host: str, net: str, *, calls: int = 0, sent: int = 0, recv: int = 0,
            token_sent: bool = False) -> None:
        """Count one request (``calls=1``) and/or bytes against ``host`` on network type ``net``."""
        host = host.lower()[:MAX_HOST_LEN]
        with self._lock:
            hosts = self._day_locked()["hosts"]
            if host not in hosts and len(hosts) >= MAX_HOSTS:
                host = OTHER
            entry = hosts.setdefault(host, {"wifi": _empty_cell(), "mobile": _empty_cell()})
            cell = entry[_net(net)]
            cell["calls"] += int(calls)
            cell["sent"] += int(sent)
            cell["recv"] += int(recv)
            if token_sent:
                entry["token_sent"] = True
            self._dirty = True

    def refuse(self, host: str) -> None:
        """Count a request that was refused before anything was sent."""
        host = (host or "?").lower()[:MAX_HOST_LEN]
        with self._lock:
            refused = self._day_locked()["refused"]
            if host in refused or len(refused) < MAX_REFUSED_HOSTS:
                refused[host] = refused.get(host, 0) + 1
            self._dirty = True

    def error(self) -> None:
        """Count one failed or retried request (a timeout, a transport error, a 5xx or a rate-limit answer)."""
        with self._lock:
            entry = self._day_locked()
            entry["errors"] = entry.get("errors", 0) + 1
            self._dirty = True

    # ---- meta (small facts that outlive a restart) --------------------------------------------------------------
    def meta_get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._meta.get(key, default)

    def meta_set(self, key: str, value: Any) -> None:
        with self._lock:
            self._meta[key] = value
            self._dirty = True

    # ---- reading ------------------------------------------------------------------------------------------------
    @staticmethod
    def _total(entry: dict[str, Any], net: str) -> int:
        cell = entry.get(net) or {}
        return int(cell.get("sent", 0)) + int(cell.get("recv", 0))

    def bytes_for_day(self, day: str) -> dict[str, int]:
        with self._lock:
            hosts = (self._days.get(day) or {}).get("hosts", {})
            return {net: sum(self._total(h, net) for h in hosts.values()) for net in NETS}

    def bytes_today(self) -> dict[str, int]:
        return self.bytes_for_day(day_of(self._clock()))

    def bytes_7d(self) -> list[dict[str, Any]]:
        """Seven entries, oldest first, ending today; a day with no traffic reads zero."""
        today = datetime.fromtimestamp(self._clock())
        out = []
        for back in range(6, -1, -1):
            day = (today - timedelta(days=back)).strftime("%Y-%m-%d")
            out.append({"day": day, **self.bytes_for_day(day)})
        return out

    def errors_today(self) -> int:
        with self._lock:
            return int((self._days.get(day_of(self._clock())) or {}).get("errors", 0))

    def refused_today(self) -> dict[str, int]:
        with self._lock:
            return dict((self._days.get(day_of(self._clock())) or {}).get("refused", {}))

    def hosts(self, window_days: int = 7) -> list[dict[str, Any]]:
        """Every host seen in the last ``window_days`` days: today's calls and bytes, and the window's."""
        today = day_of(self._clock())
        first = day_of(self._clock() - (window_days - 1) * 86400)
        found: dict[str, dict[str, Any]] = {}
        with self._lock:
            for day, entry in self._days.items():
                if not first <= day <= today:
                    continue
                for host, cells in entry.get("hosts", {}).items():
                    row = found.setdefault(host, {"host": host, "calls": 0, "bytes": 0, "calls_7d": 0,
                                                  "bytes_7d": 0, "token_sent": False})
                    calls = sum(int((cells.get(n) or {}).get("calls", 0)) for n in NETS)
                    size = sum(self._total(cells, n) for n in NETS)
                    row["calls_7d"] += calls
                    row["bytes_7d"] += size
                    if day == today:
                        row["calls"] += calls
                        row["bytes"] += size
                    if cells.get("token_sent"):
                        row["token_sent"] = True
        return sorted(found.values(), key=lambda r: (-r["bytes_7d"], r["host"]))
