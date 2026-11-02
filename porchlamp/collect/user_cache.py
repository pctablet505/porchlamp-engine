"""Global user cache for author eligibility (08 D-COL-5, packet W6-5).

A login's ``createdAt`` never changes and its public-event count (the 1-y
contribution calendar total, D-IN-3's proxy) moves slowly, yet the
author-history collector used to look both up again for every repository an
author touched. This cache keeps one row per login in the WAL-mode SQLite file
``<data_root>/state/users.sqlite`` (#89 Q3) so a login is looked up once across
the whole universe and again only after :data:`USER_CACHE_TTL_DAYS`.

The older layout, one file per login at ``<data_root>/state/users/<login>.json``,
stays readable. It is imported when the database is first opened and again when
that folder has changed, and its files are not removed: the app version before
this one still finds its cache after a rollback.

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

So a failure of the cache never fails a collection: when the database cannot be
used, the JSON files answer and take the writes, and a corrupt database is renamed
aside, never deleted.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import threading
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

#: D-COL-5: the public-event count is reused for at most 90 days.
USER_CACHE_TTL_DAYS: int = 90

#: Every record the cache writes names the query that produced it.
USER_CACHE_SOURCE: str = "github-graphql:AuthorEligibilityBatch"

#: How long a write waits for another connection before SQLite answers "database is locked".
SQLITE_BUSY_TIMEOUT_S: float = 30.0

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


def _utc_iso(value: datetime) -> Optional[str]:
    """``value`` as a UTC ISO string; None for a time at the very ends of the calendar, which has no UTC form."""
    try:
        return value.astimezone(timezone.utc).isoformat()
    except (OverflowError, ValueError):
        return None


def _storable_count(events: Any) -> bool:
    """A real integer that fits a SQLite INTEGER; a larger one raises OverflowError when bound."""
    return isinstance(events, int) and not isinstance(events, bool) and -(2 ** 63) <= events < 2 ** 63


def _give_root_its_own_db(db_path: Path) -> None:
    """Break hard links before SQLite writes in place.

    Data roots are made with ``cp -a --link``, so a database can share its inode with another root. A JSON write
    replaces the file, which breaks the link; SQLite does not. MEASURED 2026-11-02: a write in one root changed the
    other, and two processes on two linked roots left both ``database disk image is malformed``. The log is copied
    before the database: a crash between the two leaves a link, which the next open copies again.
    """
    paths = [Path(f"{db_path}-wal"), db_path]
    if not any(path.is_file() and path.stat().st_nlink > 1 for path in paths):
        return
    for path in paths:
        if path.is_file():
            tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            try:
                shutil.copyfile(path, tmp)
                os.replace(tmp, path)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
    Path(f"{db_path}-shm").unlink(missing_ok=True)


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
    legacy ``*.json`` files exist in ``root``, they are imported on first open and
    again when that folder has changed (#89 Q3).

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
        #: The class of the first SQLite failure in this process, for the gather log; None while it works.
        self.sqlite_error: Optional[str] = None
        self._use_sqlite = (
            backend == "sqlite"
            or self.root.suffix in (".sqlite", ".db")
            or self.db_path.is_file()
        )
        if self._use_sqlite and self.db_path.is_file():
            try:
                self._ensure_db()
            except (sqlite3.Error, OSError) as exc:
                self._sqlite_failed(exc)

    def _ensure_db(self) -> sqlite3.Connection:
        with self._db_lock:
            if self._conn is None:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                _give_root_its_own_db(self.db_path)
                conn = sqlite3.connect(str(self.db_path), timeout=SQLITE_BUSY_TIMEOUT_S, check_same_thread=False)
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA synchronous=NORMAL")
                    conn.executescript(_SQLITE_SCHEMA)
                    if self.root != self.db_path and self.root.is_dir():
                        self._migrate_json_dir_locked(conn)
                except BaseException:
                    conn.close()
                    raise
                self._conn = conn
            return self._conn

    def _sqlite_failed(self, exc: BaseException) -> None:
        """The cache is not evidence (see the module docstring): the caller goes on with the JSON files.

        A busy or full database that is open (``OperationalError``) costs only this call; the next one tries again.
        A database that cannot be opened, or a corrupt one, is left for the rest of this process. ``DatabaseError``
        itself, not a subclass, is a corrupt file or not a database at all: it is renamed aside, never deleted, and the
        next open starts a new database from the JSON files."""
        self.sqlite_error = self.sqlite_error or type(exc).__name__
        if isinstance(exc, sqlite3.OperationalError) and self._conn is not None:
            return
        with self._db_lock:
            conn, self._conn = self._conn, None
            self._use_sqlite = False
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            if type(exc) is sqlite3.DatabaseError:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                for suffix in ("", "-wal", "-shm"):
                    try:
                        os.replace(f"{self.db_path}{suffix}", f"{self.db_path}.corrupt-{stamp}{suffix}")
                    except OSError:
                        pass

    def _migrate_json_dir_locked(self, conn: sqlite3.Connection) -> int:
        """Import the JSON files, newest ``observed_at`` winning, unless the folder is as the last import left it:
        reading it costs 2 s of CPU per 120,000 files here and several times that on the phone. ``PRAGMA user_version``
        holds the folder's modification time (low bits of the nanoseconds; never 0, a new database's value), committed
        with the rows, so an interrupted import runs again."""
        stamp = (self.root.stat().st_mtime_ns & 0x7FFFFFFF) | 1
        if conn.execute("PRAGMA user_version").fetchone()[0] == stamp:
            return 0
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
            observed_utc = _utc_iso(observed) if observed is not None else None
            account = doc.get("account_created_at")
            events = doc.get("public_event_count")
            if observed_utc is None or not isinstance(account, str) or not _storable_count(events):
                continue  # read through the JSON path, as before
            source = str(doc.get("source") or USER_CACHE_SOURCE)
            rows.append((login, "User", account, events, observed_utc, source))
        with conn:
            if rows:
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
            conn.execute(f"PRAGMA user_version = {stamp}")
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
                except (sqlite3.Error, OSError) as exc:
                    self._sqlite_failed(exc)
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
            if self._use_sqlite and (self.root == self.db_path or not path.exists()):
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
        if not isinstance(account, str) or not _storable_count(events):
            return False

        if self._use_sqlite:
            obs_iso = (
                observed_at.astimezone(timezone.utc).isoformat()
                if observed_at.tzinfo is not None
                else observed_at.isoformat()
            )
            try:
                conn = self._ensure_db()
                with self._db_lock, conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO users"
                        " (login, typename, account_created_at, public_event_count, observed_at, source)"
                        " VALUES (?, 'User', ?, ?, ?, ?)",
                        (login, account, events, obs_iso, USER_CACHE_SOURCE),
                    )
                self._count("writes")
                return True
            except (sqlite3.Error, OSError) as exc:
                self._sqlite_failed(exc)

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
            obs_iso = _utc_iso(observed_at) if observed_at.tzinfo is not None else None
            if obs_iso is None or not isinstance(account, str) or not _storable_count(events):
                continue
            rows.append((login, "User", account, events, obs_iso, USER_CACHE_SOURCE))
        if not rows:
            return 0
        try:
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
        except (sqlite3.Error, OSError) as exc:
            self._sqlite_failed(exc)
            raise
        with self._stats_lock:
            self.stats["writes"] += len(rows)
        return len(rows)

    def count_rows(self) -> int:
        """Number of cached users currently stored."""
        if self._use_sqlite:
            if not self.db_path.is_file() and self._conn is None:
                return 0
            try:
                conn = self._ensure_db()
                with self._db_lock:
                    row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
                    return int(row[0]) if row else 0
            except (sqlite3.Error, OSError) as exc:
                self._sqlite_failed(exc)
        if not self.root.is_dir():
            return 0
        return sum(1 for p in self.root.glob("*.json") if p.is_file())

    def summary(self) -> str:
        """One line for the gather log."""
        s = self.stats
        return (f"user cache: {s['hits']} hits, {s['misses']} misses, "
                f"{s['expired']} expired, {s['unreadable']} unreadable, "
                f"{s['uncacheable']} uncacheable, {s['writes']} written"
                + (f"; SQLite failed ({self.sqlite_error}), JSON files used" if self.sqlite_error else ""))


def hydrate_from_author_history_dir(
    raw_dir: Path,
    cache: Optional[UserCache] = None,
) -> int:
    """Hydrate ``state/users.sqlite`` from all measured ``author_records`` in ``raw_dir/author_history/*.json`` (#89 Q3).

    Used by ``scripts/pull_from_phone.py`` and ``scripts/pull_from_drop.py`` after a pull of ``L4`` so the
    workstation holds what the phone looked up without copying its files. An envelope is input from outside:
    whatever is malformed is skipped, and a row enters only with the lookup time the envelope itself recorded, a
    real account date and a count that could be real. A record the collector resolved without a lookup is skipped.
    """
    ah_dir = Path(raw_dir) / "author_history"
    if not ah_dir.is_dir():
        return 0
    owns_cache = cache is None
    if cache is None:
        cache = UserCache(Path(raw_dir).parent / "state" / "users", backend="sqlite")

    try:
        if cache.sqlite_error:
            return 0  # not as 100,000 JSON files: the next pull fills the new database from all the envelopes
        best_by_login: dict[str, tuple[dict[str, Any], datetime]] = {}
        latest_allowed = datetime.now(timezone.utc) + timedelta(days=1)  # no lookup comes from the future
        for path in sorted(ah_dir.glob("*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, RecursionError):
                continue
            data = doc.get("data") if isinstance(doc, Mapping) else None
            records = data.get("author_records") if isinstance(data, Mapping) else None
            if not isinstance(records, list):
                continue
            for rec in records:
                if not isinstance(rec, Mapping):
                    continue
                if (
                    rec.get("eligibility_unmeasured")
                    or rec.get("eligibility_looked_up") is False
                    or rec.get("eligibility_resolved_locally")
                ):
                    continue
                login = rec.get("login")
                login = login.lower() if isinstance(login, str) else ""
                if not _CACHEABLE_LOGIN.fullmatch(login):
                    continue
                account = rec.get("account_created_at")
                events = rec.get("public_event_count")
                if _parse_iso(account) is None or not _storable_count(events) or events < 0:
                    continue
                obs_dt = _parse_iso(rec.get("eligibility_observed_at"))
                if obs_dt is None or obs_dt > latest_allowed or _utc_iso(obs_dt) is None:
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
    finally:
        if owns_cache:
            cache.close()
