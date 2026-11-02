"""Global user cache for author eligibility (08 D-COL-5, packet W6-5).

A login's ``createdAt`` never changes and its public-event count (the 1-y
contribution calendar total, D-IN-3's proxy) moves slowly, yet the
author-history collector used to look both up again for every repository an
author touched. This cache keeps one file per login at
``<data_root>/state/users/<login>.json`` so a login is looked up once across
the whole universe and again only after :data:`USER_CACHE_TTL_DAYS`.

MEASURED 2026-09-22 on the live API: one ``AuthorEligibilityBatch`` call of 15
logins takes 6.3-9.9 s (the contribution calendar is the slow field; a batch
of 30 timed out and returned nothing, 50 hit ``RESOURCE_LIMITS_EXCEEDED``).
Across the L1 PR windows, 134,036 (repo, external author) pairs name only
94,235 distinct logins, so the same sweep needs 30 % fewer lookups with the
cache -- and a later sweep needs lookups only for logins never seen before.

What the cache is not: evidence. ``derive`` never reads it. The
``author_history`` envelope still embeds every value it used, and each record
there carries ``eligibility_observed_at``, the time that value was actually
fetched, so a value reused from an earlier lookup is never presented as one
fetched with the envelope.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import re
import sqlite3
import tempfile
import threading
import zlib
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional

#: D-COL-5: the public-event count is reused for at most 90 days.
USER_CACHE_TTL_DAYS: int = 90

#: Every record the cache writes names the query that produced it.
USER_CACHE_SOURCE: str = "github-graphql:AuthorEligibilityBatch"

#: Maximum uncompressed size accepted when importing a gzipped SQLite user cache over LAN (#89 Q3).
MAX_SQLITE_UNCOMPRESSED_BYTES: int = 64 * 1024 * 1024

#: The logins the cache stores: a lower-cased GitHub login is letters,
#: digits and hyphens, plus the underscore of managed-user logins
#: (``name_shortcode``). Anything else -- dots, slashes, an empty string --
#: is never turned into a path; such a login is simply looked up uncached.
_CACHEABLE_LOGIN = re.compile(r"[a-z0-9][a-z0-9_-]{0,99}")

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    login TEXT PRIMARY KEY,
    typename TEXT NOT NULL,
    account_created_at TEXT NOT NULL,
    public_event_count INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL
);
"""


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def resolve_db_path(root: Path) -> Path:
    """Resolve the SQLite database path corresponding to ``root`` (#89 Q3)."""
    p = Path(root)
    if p.suffix in (".sqlite", ".db"):
        return p
    if p.name == "users":
        return p.parent / "users.sqlite"
    return p / "users.sqlite"


class UserCache:
    """Global user eligibility cache under ``root``; counts what it did in ``stats``.

    Supports both the WAL-mode SQLite backend (``<state>/users.sqlite``, enabled
    when ``backend="sqlite"``, when ``root`` names a ``.sqlite``/``.db`` file, or
    when ``users.sqlite`` already exists on disk) and the legacy per-login JSON
    directory layout (``<state>/users/<login>.json``). When SQLite is active and
    legacy ``*.json`` files exist in ``root``, they are automatically imported
    on first open (#89 Q3).

    :meth:`get` answers only with a record observed inside the TTL and not
    after ``now`` -- a lookup made later than the run's own clock is never
    used, so a backdated collection cannot read the future. :meth:`put`
    stores only complete, measured records; an ``eligibility_unmeasured``
    outcome is never cached, so a failed lookup is retried next time rather
    than remembered.
    """

    def __init__(
        self,
        root: Path,
        ttl_days: int = USER_CACHE_TTL_DAYS,
        *,
        backend: str = "auto",
    ) -> None:
        self.root = Path(root)
        self.db_path = resolve_db_path(self.root)
        self.ttl = timedelta(days=ttl_days)
        self.stats: dict[str, int] = {
            "hits": 0, "misses": 0, "expired": 0, "unreadable": 0,
            "uncacheable": 0, "writes": 0,
        }
        # D4: projects in flight on several threads share one cache; a count is a read-modify-write.
        self._stats_lock = threading.Lock()
        self._db_lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._use_sqlite = (
            backend == "sqlite"
            or self.root.suffix in (".sqlite", ".db")
            or self.db_path.is_file()
        )
        if self._use_sqlite and self.db_path.is_file():
            self._ensure_db()

    def _ensure_db(self) -> sqlite3.Connection:
        with self._db_lock:
            if self._conn is None:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(str(self.db_path), timeout=30.0, check_same_thread=False)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.executescript(_SQLITE_SCHEMA)
                self._conn = conn
                if self.root != self.db_path and self.root.is_dir():
                    self._migrate_json_dir_locked(conn)
            return self._conn

    def _migrate_json_dir_locked(self, conn: sqlite3.Connection) -> int:
        rows: list[tuple[str, str, str, int, str, str]] = []
        for path in sorted(self.root.glob("*.json")):
            login = path.stem
            if not _CACHEABLE_LOGIN.fullmatch(login):
                continue
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(doc, Mapping):
                continue
            if doc.get("login") is not None and doc.get("login") != login:
                continue
            observed = _parse_iso(doc.get("observed_at"))
            account = doc.get("account_created_at")
            events = doc.get("public_event_count")
            if (
                observed is None
                or not isinstance(account, str)
                or isinstance(events, bool)
                or not isinstance(events, int)
            ):
                continue
            source = str(doc.get("source") or USER_CACHE_SOURCE)
            rows.append((login, "User", account, events, str(doc["observed_at"]), source))
        if rows:
            with conn:
                conn.executemany(
                    "INSERT INTO users (login, typename, account_created_at, public_event_count, observed_at, source)"
                    " VALUES (?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(login) DO UPDATE SET"
                    " account_created_at = excluded.account_created_at,"
                    " public_event_count = excluded.public_event_count,"
                    " observed_at = excluded.observed_at,"
                    " source = excluded.source"
                    " WHERE excluded.observed_at >= users.observed_at",
                    rows,
                )
        return len(rows)

    def close(self) -> None:
        with self._db_lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                self._conn = None

    def _count(self, name: str) -> None:
        with self._stats_lock:
            self.stats[name] += 1

    def _path(self, login: str) -> Optional[Path]:
        if not _CACHEABLE_LOGIN.fullmatch(login):
            return None
        return self.root / f"{login}.json"

    def _validate_doc(self, login: str, doc: Any, now: datetime) -> tuple[str, Optional[dict[str, Any]]]:
        observed = _parse_iso(doc.get("observed_at")) if isinstance(doc, Mapping) else None
        account = doc.get("account_created_at") if isinstance(doc, Mapping) else None
        events = doc.get("public_event_count") if isinstance(doc, Mapping) else None
        if (
            observed is None
            or not isinstance(account, str)
            or isinstance(events, bool)
            or not isinstance(events, int)
            or (isinstance(doc, Mapping) and doc.get("login") is not None and doc.get("login") != login)
        ):
            return "unreadable", None
        if observed > now or now - observed > self.ttl:
            return "expired", None
        return "hits", {
            "account_created_at": account,
            "public_event_count": events,
            "observed_at": doc["observed_at"],
        }

    def get(self, login: str, now: datetime) -> Optional[dict[str, Any]]:
        """The cached eligibility of ``login`` as of ``now``, or ``None``.

        Returns ``{"account_created_at", "public_event_count",
        "observed_at"}``. ``None`` means "look it up": no file/row, an
        unreadable or malformed record, a record older than the TTL, one
        observed after ``now``, or a login the cache never stores.
        """
        path = self._path(login)
        if path is None:
            self._count("uncacheable")
            return None

        if self._use_sqlite:
            row = None
            if self.db_path.is_file() or self._conn is not None:
                try:
                    conn = self._ensure_db()
                    with self._db_lock:
                        cur = conn.execute(
                            "SELECT login, account_created_at, public_event_count, observed_at"
                            " FROM users WHERE login = ?",
                            (login,),
                        )
                        row = cur.fetchone()
                except sqlite3.Error:
                    self._count("unreadable")
                    return None
            if row is not None:
                doc = {
                    "login": row[0],
                    "account_created_at": row[1],
                    "public_event_count": row[2],
                    "observed_at": row[3],
                }
                outcome, result = self._validate_doc(login, doc, now)
                self._count(outcome)
                return result
            if self.root == self.db_path or not path.exists():
                self._count("misses")
                return None

        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._count("misses")
            return None
        except (OSError, ValueError):
            self._count("unreadable")
            return None
        outcome, result = self._validate_doc(login, doc, now)
        self._count(outcome)
        return result

    def put(self, login: str, record: Mapping[str, Any], observed_at: datetime) -> bool:
        """Store one measured record; returns whether it was written.

        Written atomically (temporary file, then ``os.replace`` in JSON mode, or
        a WAL transaction in SQLite mode), so a reader never sees half a record.
        """
        path = self._path(login)
        if path is None or record.get("eligibility_unmeasured"):
            return False
        account = record.get("account_created_at")
        events = record.get("public_event_count")
        if not isinstance(account, str) or isinstance(events, bool) or not isinstance(events, int):
            return False

        if self._use_sqlite:
            conn = self._ensure_db()
            with self._db_lock, conn:
                conn.execute(
                    "INSERT OR REPLACE INTO users"
                    " (login, typename, account_created_at, public_event_count, observed_at, source)"
                    " VALUES (?, 'User', ?, ?, ?, ?)",
                    (login, account, events, observed_at.isoformat(), USER_CACHE_SOURCE),
                )
            self._count("writes")
            return True

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp.write_text(json.dumps({
                "login": login, "typename": "User", "account_created_at": account,
                "public_event_count": events, "observed_at": observed_at.isoformat(),
                "source": USER_CACHE_SOURCE,
            }, sort_keys=True))
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        self._count("writes")
        return True

    def put_many(self, records: Iterable[tuple[str, Mapping[str, Any], datetime]]) -> int:
        """Bulk-store measured eligibility records in a single SQLite transaction (#89 Q3).

        When two records exist for the same login, keeps the one with the newer ``observed_at``.
        """
        if not self._use_sqlite:
            written = 0
            for login, rec, obs in records:
                if self.put(login, rec, obs):
                    written += 1
            return written

        rows: list[tuple[str, str, str, int, str, str]] = []
        for login, record, observed_at in records:
            if not _CACHEABLE_LOGIN.fullmatch(login):
                continue
            if record.get("eligibility_unmeasured"):
                continue
            account = record.get("account_created_at")
            events = record.get("public_event_count")
            if not isinstance(account, str) or isinstance(events, bool) or not isinstance(events, int):
                continue
            rows.append((login, "User", account, events, observed_at.isoformat(), USER_CACHE_SOURCE))
        if not rows:
            return 0
        conn = self._ensure_db()
        with self._db_lock, conn:
            conn.executemany(
                "INSERT INTO users (login, typename, account_created_at, public_event_count, observed_at, source)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(login) DO UPDATE SET"
                " account_created_at = excluded.account_created_at,"
                " public_event_count = excluded.public_event_count,"
                " observed_at = excluded.observed_at,"
                " source = excluded.source"
                " WHERE excluded.observed_at >= users.observed_at",
                rows,
            )
        with self._stats_lock:
            self.stats["writes"] += len(rows)
        return len(rows)

    def count_rows(self) -> int:
        """Number of cached users currently stored."""
        if self._use_sqlite:
            if not self.db_path.is_file() and self._conn is None:
                return 0
            conn = self._ensure_db()
            with self._db_lock:
                row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
                return int(row[0]) if row else 0
        if not self.root.is_dir():
            return 0
        return sum(1 for p in self.root.glob("*.json") if p.is_file())

    def summary(self) -> str:
        """One line for the gather log."""
        s = self.stats
        return (f"user cache: {s['hits']} hits, {s['misses']} misses, "
                f"{s['expired']} expired, {s['unreadable']} unreadable, "
                f"{s['uncacheable']} uncacheable, {s['writes']} written")


def hydrate_from_author_history_dir(
    raw_dir: Path,
    cache: Optional[UserCache] = None,
) -> int:
    """Hydrate ``state/users.sqlite`` from all measured ``author_records`` in ``raw_dir/author_history/*.json`` (#89 Q3).

    Used by ``scripts/pull_from_phone.py`` and ``scripts/pull_from_drop.py`` after extracting ``L4`` so the
    workstation's SQLite user cache automatically stays warm from phone cycles without copying 94,000 tiny files.
    """
    ah_dir = Path(raw_dir) / "author_history"
    if not ah_dir.is_dir():
        return 0
    if cache is None:
        cache = UserCache(Path(raw_dir).parent / "state" / "users", backend="sqlite")

    best_by_login: dict[str, tuple[dict[str, Any], datetime]] = {}
    for path in sorted(ah_dir.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        data = doc.get("data") if isinstance(doc, Mapping) else None
        if not isinstance(data, Mapping):
            continue
        anchor_raw = data.get("window_anchor")
        for rec in data.get("author_records") or []:
            if not isinstance(rec, Mapping):
                continue
            if rec.get("eligibility_unmeasured") or rec.get("eligibility_looked_up") is False:
                continue
            login = str(rec.get("login") or "").lower()
            if not _CACHEABLE_LOGIN.fullmatch(login):
                continue
            account = rec.get("account_created_at")
            events = rec.get("public_event_count")
            if not isinstance(account, str) or isinstance(events, bool) or not isinstance(events, int):
                continue
            obs_raw = rec.get("eligibility_observed_at") or anchor_raw
            obs_dt = _parse_iso(obs_raw)
            if obs_dt is None:
                continue
            prev = best_by_login.get(login)
            if prev is None or obs_dt >= prev[1]:
                best_by_login[login] = (
                    {"account_created_at": account, "public_event_count": events, "eligibility_unmeasured": False},
                    obs_dt,
                )

    if not best_by_login:
        return 0
    return cache.put_many(
        (login, rec, obs_dt) for login, (rec, obs_dt) in sorted(best_by_login.items())
    )


def export_sqlite_gz(cache: UserCache) -> bytes:
    """Export ``cache`` as a gzip-compressed SQLite database image (#89 Q3)."""
    conn = cache._ensure_db()
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with cache._db_lock:
            dst = sqlite3.connect(str(tmp_path))
            try:
                conn.backup(dst)
            finally:
                dst.close()
        raw_bytes = tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)
    buf = io.BytesIO()
    with gzip.GzipFile(filename="users.sqlite", mode="wb", fileobj=buf, mtime=0) as gz:
        gz.write(raw_bytes)
    return buf.getvalue()


def import_sqlite_gz(
    cache: UserCache,
    gz_bytes: bytes,
    *,
    max_uncompressed_bytes: int = MAX_SQLITE_UNCOMPRESSED_BYTES,
) -> int:
    """Validate and merge a gzip-compressed SQLite user cache image into ``cache`` (#89 Q3).

    Raises ``ValueError`` when ``gz_bytes`` is not valid gzip, exceeds ``max_uncompressed_bytes``,
    is not a valid SQLite 3 database, or carries no readable ``users`` table.
    """
    try:
        decomp = zlib.decompressobj(16 + zlib.MAX_WBITS)
        chunks: list[bytes] = []
        total = 0
        mv = memoryview(gz_bytes)
        step = 65536
        for offset in range(0, len(mv), step):
            out = decomp.decompress(mv[offset:offset + step], max_uncompressed_bytes + 1 - total)
            total += len(out)
            if total > max_uncompressed_bytes or decomp.unconsumed_tail:
                raise ValueError("gzip payload exceeds uncompressed size cap")
            chunks.append(out)
        tail = decomp.flush()
        total += len(tail)
        if total > max_uncompressed_bytes or not decomp.eof:
            raise ValueError("corrupt or truncated gzip stream")
        chunks.append(tail)
        raw_db = b"".join(chunks)
    except zlib.error as exc:
        raise ValueError(f"invalid gzip stream: {exc}") from None

    if len(raw_db) < 100 or not raw_db.startswith(b"SQLite format 3\x00"):
        raise ValueError("payload is not a SQLite 3 database")

    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
        tmp.write(raw_db)
        tmp_path = Path(tmp.name)

    try:
        src = sqlite3.connect(f"file:{tmp_path}?mode=ro", uri=True, timeout=10.0)
        try:
            src.execute("PRAGMA trusted_schema = OFF")
            cur = src.execute(
                "SELECT login, account_created_at, public_event_count, observed_at FROM users"
            )
            entries: list[tuple[str, Mapping[str, Any], datetime]] = []
            for row in cur:
                if len(row) != 4:
                    continue
                login, account, events, obs_raw = row
                if not isinstance(login, str) or not _CACHEABLE_LOGIN.fullmatch(login):
                    continue
                obs_dt = _parse_iso(obs_raw)
                if (
                    obs_dt is None
                    or not isinstance(account, str)
                    or isinstance(events, bool)
                    or not isinstance(events, int)
                ):
                    continue
                entries.append((
                    login,
                    {"account_created_at": account, "public_event_count": events, "eligibility_unmeasured": False},
                    obs_dt,
                ))
        except sqlite3.Error as exc:
            raise ValueError(f"invalid users.sqlite schema: {exc}") from None
        finally:
            src.close()
    finally:
        tmp_path.unlink(missing_ok=True)

    cache._use_sqlite = True
    cache._ensure_db()
    return cache.put_many(entries)

