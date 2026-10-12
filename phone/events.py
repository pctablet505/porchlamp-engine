"""The readable timeline, the technical log file and the redaction both pass through. Standard library only.

* ``Timeline``: ``state/events.jsonl``, one JSON object per line, 14 days kept. ``events()`` of the app reads it.
* ``TechLog``: ``state/log/porchlamp-YYYY-MM-DD.log``, one file a day, 14 days kept. Every line is redacted when it
  is written and again when it is read back, so a secret that was registered after the line was written (or a line
  another writer put in the file) still never leaves the phone. ``export`` and ``GET /log.txt`` serve this text.
* ``Redactor``: the token shapes ``gh[pousr]_...`` and ``github_pat_...``, an ``Authorization`` header value, and the
  exact secrets this process holds in memory (the GitHub token and the link token).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import urllib.parse
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

KEEP_DAYS = 14
MAX_LINE = 2000
MIN_SECRET_LEN = 6  # a shorter "secret" would redact ordinary words
REDACTED = "[redacted]"
LEVELS = ("info", "ok", "warn", "err")
TRIM_EVERY_S = 3600.0

_SHAPES = (
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{6,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{6,}"),
)
_AUTH = re.compile(r"(?i)(authorization\W+(?:bearer|token|basic)\s+)\S+")
_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9_\-.=+/]{16,}")


class Redactor:
    """Holds the exact secrets of this process (memory only) and removes them, and token shapes, from text."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._secrets: list[str] = []

    def add(self, secret: str | None) -> None:
        if not secret or len(secret) < MIN_SECRET_LEN:
            return
        variants = {secret, urllib.parse.quote(secret, safe="")}
        with self._lock:
            for variant in variants:
                if variant not in self._secrets:
                    self._secrets.append(variant)
            self._secrets.sort(key=len, reverse=True)

    def discard(self, secret: str | None) -> None:
        if not secret:
            return
        variants = {secret, urllib.parse.quote(secret, safe="")}
        with self._lock:
            self._secrets = [s for s in self._secrets if s not in variants]

    def __call__(self, text: object) -> str:
        out = str(text)
        with self._lock:
            secrets = list(self._secrets)
        for secret in secrets:
            out = out.replace(secret, REDACTED)
        for shape in _SHAPES:
            out = shape.sub(REDACTED, out)
        out = _AUTH.sub(lambda m: m.group(1) + REDACTED, out)
        return _BEARER.sub(lambda m: m.group(1) + REDACTED, out)


#: A redactor with no exact secrets: token shapes only (used where no engine exists).
redact = Redactor()


def iso_local(ts: float) -> str:
    """Local time with its offset, to the second: ``2026-10-11T14:40:18+05:30``."""
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Timeline:
    """The readable event list, newest first when read. Entries older than ``KEEP_DAYS`` are removed."""

    def __init__(self, path: Path | str, clock: Callable[[], float] = time.time,
                 redactor: Callable[[str], str] = redact) -> None:
        self.path = Path(path)
        self._clock = clock
        self._redact = redactor
        self._lock = threading.Lock()
        self._next_id = 1
        self._last_trim = 0.0
        self._scan_ids()
        self.trim()

    def _rows(self) -> list[dict[str, Any]]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        rows = []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue  # a torn last line from a crash: skipped, rewritten away at the next trim
            if isinstance(row, dict):
                rows.append(row)
        return rows

    def _scan_ids(self) -> None:
        seen = 0
        for row in self._rows():
            value = row.get("id", row.get("seq", 0))
            if isinstance(value, int) and not isinstance(value, bool):
                seen = max(seen, value)
        self._next_id = seen + 1

    def add(self, level: str, text: str, detail: str = "", kind: str = "progress") -> int:
        """Append one entry and return its id. ``text`` and ``detail`` are redacted before they are written."""
        level = level if level in LEVELS else "info"
        now = self._clock()
        with self._lock:
            entry = {"id": self._next_id, "t": round(now, 3), "level": level,
                     "text": self._redact(text)[:MAX_LINE], "detail": self._redact(detail)[:MAX_LINE],
                     "kind": kind}
            self._next_id += 1
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
            due = now - self._last_trim >= TRIM_EVERY_S
        if due:
            self.trim()
        return int(entry["id"])

    def trim(self) -> int:
        """Remove entries older than ``KEEP_DAYS``; returns how many went. The id counter never goes back."""
        with self._lock:
            now = self._clock()
            self._last_trim = now
            cutoff = now - KEEP_DAYS * 86400
            data_rows = [r for r in self._rows() if isinstance(r.get("id"), int)]
            keep = [r for r in data_rows if float(r.get("t", 0)) >= cutoff]
            dropped = len(data_rows) - len(keep)
            if dropped == 0 and not self._torn():
                return 0
            lines = [json.dumps({"seq": self._next_id - 1})]
            lines += [json.dumps(r, sort_keys=True) for r in keep]
            _atomic_write(self.path, ("\n".join(lines) + "\n").encode("utf-8"))
            return dropped

    def _torn(self) -> bool:
        """True when the file does not end in a newline (a write was cut short)."""
        try:
            data = self.path.read_bytes()
        except FileNotFoundError:
            return False
        return bool(data) and not data.endswith(b"\n")

    def list(self, after_id: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        """Entries with ``id > after_id``, newest first, at most ``limit``, in the shape the app reads."""
        with self._lock:
            rows = [r for r in self._rows() if isinstance(r.get("id"), int) and r["id"] > after_id]
        rows.sort(key=lambda r: r["id"], reverse=True)
        return [{"id": r["id"], "ts": iso_local(float(r.get("t", 0))), "level": r.get("level", "info"),
                 "text": self._redact(r.get("text", "")), "detail": self._redact(r.get("detail", "")),
                 "kind": r.get("kind", "progress")} for r in rows[: max(0, int(limit))]]

    def count(self) -> int:
        with self._lock:
            return sum(1 for r in self._rows() if "id" in r)


class TechLog:
    """One redacted log file a day under ``dir``; the files of the last ``KEEP_DAYS`` days are kept."""

    NAME = re.compile(r"^porchlamp-(\d{4}-\d{2}-\d{2})\.log$")

    def __init__(self, folder: Path | str, clock: Callable[[], float] = time.time,
                 redactor: Callable[[str], str] = redact) -> None:
        self.dir = Path(folder)
        self._clock = clock
        self._redact = redactor
        self._lock = threading.Lock()
        self._last_trim = 0.0
        self.trim()

    def _path(self, ts: float) -> Path:
        return self.dir / f"porchlamp-{datetime.fromtimestamp(ts).strftime('%Y-%m-%d')}.log"

    def write(self, text: object) -> None:
        """Append ``text`` (one or more lines) with a local timestamp each, redacted, to today's file."""
        now = self._clock()
        stamp = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S")
        lines = [ln for ln in self._redact(text).splitlines() if ln.strip()]
        if not lines:
            return
        body = "".join(f"{stamp} {ln[:MAX_LINE]}\n" for ln in lines)
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            with open(self._path(now), "a", encoding="utf-8") as fh:
                fh.write(body)
            due = now - self._last_trim >= TRIM_EVERY_S
        if due:
            self.trim()

    def files(self) -> list[Path]:
        if not self.dir.is_dir():
            return []
        return sorted(p for p in self.dir.iterdir() if self.NAME.match(p.name))

    def trim(self) -> int:
        """Keep today and the 13 days before it (``KEEP_DAYS`` files); remove older day files, returns how many."""
        self._last_trim = self._clock()
        cutoff = datetime.fromtimestamp(self._last_trim - (KEEP_DAYS - 1) * 86400).strftime("%Y-%m-%d")
        gone = 0
        for path in self.files():
            match = self.NAME.match(path.name)
            if match and match.group(1) < cutoff:
                path.unlink(missing_ok=True)
                gone += 1
        return gone

    def read_text(self) -> str:
        """All kept days, oldest first, redacted again on the way out."""
        parts = []
        for path in self.files():
            try:
                parts.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue  # a file that vanished between the listing and the read
        return self._redact("".join(parts))

    def export(self, exports_dir: Path | str, name: str = "porchlamp-log.txt") -> Path:
        """Write the redacted text to ``exports_dir/name`` (atomic) and return the path."""
        target = Path(exports_dir) / name
        _atomic_write(target, self.read_text().encode("utf-8"))
        return target
