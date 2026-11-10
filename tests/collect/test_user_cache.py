"""D-COL-5 user cache (``porchlamp.collect.user_cache``): TTL, no look-ahead,
failures never cached, file names never built from unsafe logins."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from porchlamp.collect.envelope import write_envelope
from porchlamp.collect.user_cache import USER_CACHE_TTL_DAYS, UserCache, hydrate_from_author_history_dir

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
MEASURED = {"account_created_at": "2015-01-01T00:00:00Z", "public_event_count": 40,
            "eligibility_unmeasured": False}


def test_miss_then_hit_returns_the_stored_values(tmp_path):
    cache = UserCache(tmp_path)
    assert cache.get("octocat", NOW) is None
    assert cache.put("octocat", MEASURED, NOW) is True
    assert cache.get("octocat", NOW) == {"account_created_at": "2015-01-01T00:00:00Z",
                                         "public_event_count": 40,
                                         "observed_at": NOW.isoformat()}
    assert cache.stats["misses"] == 1
    assert cache.stats["hits"] == 1
    assert cache.stats["writes"] == 1


def test_ttl_boundary_exactly_90_days_is_a_hit_one_second_more_is_expired(tmp_path):
    cache = UserCache(tmp_path)
    cache.put("octocat", MEASURED, NOW)
    edge = NOW + timedelta(days=USER_CACHE_TTL_DAYS)
    assert cache.get("octocat", edge) is not None
    assert cache.get("octocat", edge + timedelta(seconds=1)) is None
    assert cache.stats["expired"] == 1


def test_a_record_observed_after_now_is_never_used(tmp_path):
    """No look-ahead: a backdated run must not read a later lookup."""
    cache = UserCache(tmp_path)
    cache.put("octocat", MEASURED, NOW)
    assert cache.get("octocat", NOW - timedelta(seconds=1)) is None
    assert cache.get("octocat", NOW) is not None


def test_unmeasured_and_incomplete_records_are_not_cached(tmp_path):
    cache = UserCache(tmp_path)
    unmeasured = {"account_created_at": None, "public_event_count": None,
                  "eligibility_unmeasured": True}
    assert cache.put("ghost", unmeasured, NOW) is False
    assert cache.put("half", {**MEASURED, "public_event_count": None}, NOW) is False
    assert cache.put("flag", {**MEASURED, "public_event_count": True}, NOW) is False
    # The flag alone forbids caching, even beside complete-looking values.
    assert cache.put("flagged", {**MEASURED, "eligibility_unmeasured": True}, NOW) is False
    assert list(tmp_path.iterdir()) == []
    assert cache.stats["writes"] == 0


def test_unsafe_logins_never_become_paths(tmp_path):
    root = tmp_path / "users"
    cache = UserCache(root)
    for login in ("../escape", "a/b", "a.b", "", "-lead", "UPPER"):
        assert cache.put(login, MEASURED, NOW) is False
        assert cache.get(login, NOW) is None
    assert not root.exists()
    assert not (tmp_path / "escape.json").exists()
    assert cache.stats["uncacheable"] == 6
    # Over-correction guard: the logins GitHub actually issues ARE cached,
    # including the underscore of managed-user logins.
    for login in ("octocat", "a-b-c", "x0", "dev_acme"):
        assert cache.put(login, MEASURED, NOW) is True
        assert cache.get(login, NOW) is not None


def test_unreadable_or_malformed_file_is_a_miss_and_is_overwritten(tmp_path):
    cache = UserCache(tmp_path)
    (tmp_path / "broken.json").write_text("{not json")
    (tmp_path / "typed.json").write_text(json.dumps(
        {"account_created_at": "2015-01-01T00:00:00Z", "public_event_count": "40",
         "observed_at": NOW.isoformat()}))
    (tmp_path / "naive.json").write_text(json.dumps(
        {"account_created_at": "2015-01-01T00:00:00Z", "public_event_count": 40,
         "observed_at": "2026-09-22T12:00:00"}))
    for login in ("broken", "typed", "naive"):
        assert cache.get(login, NOW) is None
    assert cache.stats["unreadable"] == 3
    assert cache.put("broken", MEASURED, NOW) is True
    assert cache.get("broken", NOW)["public_event_count"] == 40


def test_write_is_atomic_and_leaves_no_temporary_file(tmp_path):
    cache = UserCache(tmp_path)
    cache.put("octocat", MEASURED, NOW)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["octocat.json"]
    doc = json.loads((tmp_path / "octocat.json").read_text())
    assert doc["source"] == "github-graphql:AuthorEligibilityBatch"
    assert doc["typename"] == "User"
    assert doc["login"] == "octocat"


def test_get_rejects_mismatched_login_in_cached_document(tmp_path):
    cache = UserCache(tmp_path)
    (tmp_path / "octocat.json").write_text(json.dumps({
        "login": "other_user",
        "account_created_at": "2015-01-01T00:00:00Z",
        "public_event_count": 40,
        "observed_at": NOW.isoformat(),
    }))
    assert cache.get("octocat", NOW) is None
    assert cache.stats["unreadable"] == 1


def test_put_cleans_up_temp_file_on_replace_failure(tmp_path, monkeypatch):
    import os
    import pytest

    cache = UserCache(tmp_path)

    def _fail_replace(src, dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", _fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        cache.put("octocat", MEASURED, NOW)
    assert list(tmp_path.iterdir()) == []



def test_concurrent_puts_of_one_login_both_succeed(tmp_path, monkeypatch):
    """T18 (D4): two projects in flight store the same author at once. Each thread writes its own temporary file,
    so neither ``os.replace`` finds its file gone (with one name per process, the loser raised
    ``FileNotFoundError`` and its whole project failed); the record left is one of the two, whole."""
    import threading
    from pathlib import Path

    barrier = threading.Barrier(2, timeout=10)
    real_write_text = Path.write_text

    def write_then_meet(self, data, *args, **kwargs):
        written = real_write_text(self, data, *args, **kwargs)
        barrier.wait()                       # both temporary files are written before either is renamed
        return written

    monkeypatch.setattr(Path, "write_text", write_then_meet)
    cache = UserCache(tmp_path)
    results, errors = [], []

    def put(events):
        try:
            results.append(cache.put("octocat", {**MEASURED, "public_event_count": events}, NOW))
        except Exception as exc:  # the failure under test: collected, then asserted absent
            errors.append(exc)

    threads = [threading.Thread(target=put, args=(events,)) for events in (40, 41)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert errors == [] and results == [True, True]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["octocat.json"]
    assert json.loads((tmp_path / "octocat.json").read_text())["public_event_count"] in (40, 41)
    assert cache.stats["writes"] == 2


# --- Q3: SQLite backend, JSON migration, author_history hydration, and LAN gzip sync ---------------------------------

def test_sqlite_backend_roundtrip_and_auto_migration_from_json(tmp_path):
    """Q3: UserCache with backend='sqlite' writes to state/users.sqlite in WAL mode, auto-imports existing
    state/users/*.json files, and subsequent UserCache(root) calls auto-detect users.sqlite."""
    users_dir = tmp_path / "state" / "users"
    legacy = UserCache(users_dir)
    assert legacy.put("legacy_user", MEASURED, NOW - timedelta(days=5)) is True
    assert (users_dir / "legacy_user.json").is_file()

    sqlite_cache = UserCache(users_dir, backend="sqlite")
    assert sqlite_cache.put("fresh_user", {**MEASURED, "public_event_count": 99}, NOW) is True
    assert (tmp_path / "state" / "users.sqlite").is_file()
    assert sqlite_cache.get("legacy_user", NOW)["public_event_count"] == 40
    assert sqlite_cache.get("fresh_user", NOW)["public_event_count"] == 99
    assert sqlite_cache.count_rows() == 2
    sqlite_cache.close()

    # Auto-detects users.sqlite even without backend="sqlite"
    auto_cache = UserCache(users_dir)
    assert auto_cache.get("fresh_user", NOW)["public_event_count"] == 99
    auto_cache.close()


def test_hydrate_from_author_history_dir_populates_sqlite_with_newest_observations(tmp_path):
    """Q3: hydrate_from_author_history_dir extracts measured author_records from raw/author_history/*.json
    into state/users.sqlite and skips unmeasured or unlooked-up records."""
    from porchlamp.collect.envelope import write_envelope
    from porchlamp.collect.user_cache import hydrate_from_author_history_dir

    raw_dir = tmp_path / "raw"
    older = NOW - timedelta(days=10)
    write_envelope(
        raw_dir / "author_history" / "acme__one.json",
        source="github-graphql:author-history",
        data={
            "window_anchor": older.isoformat(),
            "author_records": [
                {"login": "alice", "account_created_at": "2018-01-01T00:00:00Z", "public_event_count": 12,
                 "eligibility_unmeasured": False, "eligibility_observed_at": older.isoformat()},
                {"login": "skipped_user", "account_created_at": None, "public_event_count": None,
                 "eligibility_unmeasured": False, "eligibility_looked_up": False},
                {"login": "unmeasured_user", "account_created_at": None, "public_event_count": None,
                 "eligibility_unmeasured": True},
            ],
        },
        fetched_at=older.isoformat(),
    )
    write_envelope(
        raw_dir / "author_history" / "acme__two.json",
        source="github-graphql:author-history",
        data={
            "window_anchor": NOW.isoformat(),
            "author_records": [
                {"login": "alice", "account_created_at": "2018-01-01T00:00:00Z", "public_event_count": 25,
                 "eligibility_unmeasured": False, "eligibility_observed_at": NOW.isoformat()},
                {"login": "bob", "account_created_at": "2019-06-01T00:00:00Z", "public_event_count": 8,
                 "eligibility_unmeasured": False, "eligibility_observed_at": NOW.isoformat()},
            ],
        },
        fetched_at=NOW.isoformat(),
    )

    count = hydrate_from_author_history_dir(raw_dir)
    assert count == 2
    cache = UserCache(tmp_path / "state" / "users")
    assert cache.get("alice", NOW) == {
        "account_created_at": "2018-01-01T00:00:00Z",
        "public_event_count": 25,
        "observed_at": NOW.isoformat(),
    }
    assert cache.get("bob", NOW)["public_event_count"] == 8
    assert cache.get("skipped_user", NOW) is None
    assert cache.get("unmeasured_user", NOW) is None
    cache.close()


# --- rv-103 rework: a cache failure never fails a collection; one import; own inode; malformed input ------------------

_ONE = {"account_created_at": "2015-01-01T00:00:00Z", "public_event_count": 40, "eligibility_unmeasured": False}
_YESTERDAY = NOW - timedelta(days=1)


def _users_dir(tmp_path):
    root = tmp_path / "state" / "users"
    root.mkdir(parents=True)
    return root


def _ro_rows(db):
    conn = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    try:
        return {row[0]: row[1:] for row in conn.execute(
            "SELECT login, account_created_at, public_event_count, observed_at FROM users")}
    finally:
        conn.close()


@pytest.mark.parametrize("garbage", [b"this is not a database " * 300, b"SQLite format 3\x00" + b"\xff" * 8192])
def test_a_corrupt_database_is_set_aside_and_the_json_files_answer(tmp_path, garbage):
    root = _users_dir(tmp_path)
    UserCache(root).put("old_entry", _ONE, _YESTERDAY)  # JSON layout: no database yet
    db = tmp_path / "state" / "users.sqlite"
    db.write_bytes(garbage)

    cache = UserCache(root, backend="sqlite")  # must not raise
    assert cache.get("old_entry", NOW)["public_event_count"] == 40
    assert cache.put("new_entry", _ONE, NOW) is True
    assert cache.get("new_entry", NOW)["public_event_count"] == 40
    assert cache.sqlite_error == "DatabaseError" and "SQLite failed (DatabaseError)" in cache.summary()
    assert [p.read_bytes() for p in tmp_path.glob("state/users.sqlite.corrupt-*")] == [garbage]  # kept, not deleted
    assert not db.exists()

    # The next process starts a new database from the JSON files, with the row written meanwhile.
    next_process = UserCache(root, backend="sqlite")
    assert next_process.put("third", _ONE, NOW) is True
    assert db.is_file() and sorted(_ro_rows_after_close(next_process, db)) == ["new_entry", "old_entry", "third"]


def _ro_rows_after_close(cache, db):
    cache.close()
    return _ro_rows(db)


def test_a_database_cut_short_while_running_falls_back_to_json_for_get_and_put(tmp_path):
    root = _users_dir(tmp_path)
    UserCache(root).put("old_entry", _ONE, _YESTERDAY)
    cache = UserCache(root, backend="sqlite")
    cache.put_many((f"u{i}", _ONE, NOW) for i in range(5000))
    cache.close()
    db = tmp_path / "state" / "users.sqlite"
    with open(db, "r+b") as fh:
        fh.truncate(db.stat().st_size // 2)

    cache = UserCache(root, backend="sqlite")
    assert cache.get("old_entry", NOW)["public_event_count"] == 40
    assert cache.put("after_cut", _ONE, NOW) is True
    assert cache.get("after_cut", NOW) is not None


def test_a_locked_database_never_fails_get_or_put(tmp_path, monkeypatch):
    monkeypatch.setattr("porchlamp.collect.user_cache.SQLITE_BUSY_TIMEOUT_S", 0.05)
    root = _users_dir(tmp_path)
    UserCache(root).put("old_entry", _ONE, _YESTERDAY)
    cache = UserCache(root, backend="sqlite")
    assert cache.put("in_db", _ONE, NOW) is True
    db = tmp_path / "state" / "users.sqlite"
    holder = sqlite3.connect(db, timeout=0)
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO users VALUES ('held', 'User', 'x', 1, 'x', 'x')")
    try:
        assert cache.put("while_locked", _ONE, NOW) is True  # to JSON, not an exception
        assert (root / "while_locked.json").is_file()
        assert cache.get("while_locked", NOW) is not None
        assert cache.get("in_db", NOW) is not None  # the database still answers
        assert cache.sqlite_error == "OperationalError"
        assert db.is_file()  # busy is not corrupt: never moved
        assert not list(tmp_path.glob("state/users.sqlite.corrupt-*"))
    finally:
        holder.rollback()
        holder.close()
    assert cache.put("after_lock", _ONE, NOW) is True  # busy costs one call: SQLite again
    assert not (root / "after_lock.json").exists()
    cache.close()
    assert sorted(_ro_rows(db)) == ["after_lock", "in_db", "old_entry"]


@pytest.mark.parametrize("bad", [
    {"account_created_at": "2015-01-01T00:00:00Z", "public_event_count": 10 ** 30, "observed_at": "2026-09-21T00:00:00+00:00"},
    {"account_created_at": "2015-01-01T00:00:00Z", "public_event_count": 1, "observed_at": "0001-01-01T00:00:00+14:00"},
    {"account_created_at": "2015-01-01T00:00:00Z", "public_event_count": 1, "observed_at": "9999-12-31T23:59:59-14:00"},
])
def test_one_impossible_json_file_does_not_stop_the_import(tmp_path, bad):
    root = _users_dir(tmp_path)
    UserCache(root).put("good", _ONE, _YESTERDAY)
    (root / "impossible.json").write_text(json.dumps(bad))
    cache = UserCache(root, backend="sqlite")
    assert cache.put("new_entry", _ONE, NOW) is True
    cache.close()
    assert sorted(_ro_rows(tmp_path / "state" / "users.sqlite")) == ["good", "new_entry"]


def test_a_second_open_does_not_read_the_json_again_unless_the_folder_changed(tmp_path):
    root = _users_dir(tmp_path)
    json_cache = UserCache(root)
    json_cache.put("entry", _ONE, NOW - timedelta(days=9))
    os.utime(root, (1_700_000_000, 1_700_000_000))
    cache = UserCache(root, backend="sqlite")
    cache.put("trigger", _ONE, NOW)
    cache.close()
    db = tmp_path / "state" / "users.sqlite"
    assert _ro_rows(db)["entry"][1] == 40

    # In place, so the folder's time stays: a second open must not see it. It reads no file at all.
    doc = json.loads((root / "entry.json").read_text())
    doc.update(public_event_count=41, observed_at=(NOW - timedelta(days=2)).isoformat())
    (root / "entry.json").write_text(json.dumps(doc))
    os.utime(root, (1_700_000_000, 1_700_000_000))
    UserCache(root, backend="sqlite").close()
    assert _ro_rows(db)["entry"][1] == 40

    # The folder changed (a rollback wrote JSON, or an adb seed): the next open imports, newest wins.
    os.utime(root, (1_700_000_100, 1_700_000_100))
    UserCache(root, backend="sqlite").close()
    assert _ro_rows(db)["entry"][1] == 41


def test_an_interrupted_import_runs_again(tmp_path):
    """The marker is committed with the rows: a first open that fails half-way leaves none of them marked as done."""
    root = _users_dir(tmp_path)
    UserCache(root).put("entry", _ONE, _YESTERDAY)
    db = tmp_path / "state" / "users.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("CREATE TABLE users (login TEXT PRIMARY KEY, typename TEXT NOT NULL, account_created_at TEXT NOT NULL,"
                       " public_event_count INTEGER NOT NULL, observed_at TEXT NOT NULL, source TEXT NOT NULL);")
    conn.close()  # the state a kill leaves: schema in place, no rows, no marker
    cache = UserCache(root, backend="sqlite")
    assert cache.get("entry", NOW)["public_event_count"] == 40
    assert cache.count_rows() == 1
    cache.close()


def test_a_hard_linked_database_gets_its_own_inode_before_any_write(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    cache = UserCache(a / "state" / "users", backend="sqlite")
    cache.put("alice", _ONE, _YESTERDAY)
    cache.close()
    db_a, db_b = a / "state" / "users.sqlite", b / "state" / "users.sqlite"
    db_b.parent.mkdir(parents=True)
    os.link(db_a, db_b)
    before = db_a.read_bytes()

    cache = UserCache(b / "state" / "users", backend="sqlite")
    cache.put("bob", _ONE, NOW)
    cache.close()
    assert db_a.read_bytes() == before and db_a.stat().st_nlink == 1
    assert sorted(_ro_rows(db_a)) == ["alice"] and sorted(_ro_rows(db_b)) == ["alice", "bob"]


def test_a_hard_linked_database_with_a_log_keeps_its_rows_and_its_source_keeps_its_log(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    (a / "state").mkdir(parents=True)
    (b / "state").mkdir(parents=True)
    db_a = a / "state" / "users.sqlite"
    conn = sqlite3.connect(db_a)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.executescript("CREATE TABLE users (login TEXT PRIMARY KEY, typename TEXT NOT NULL, account_created_at TEXT NOT NULL,"
                       " public_event_count INTEGER NOT NULL, observed_at TEXT NOT NULL, source TEXT NOT NULL);")
    conn.execute("INSERT INTO users VALUES ('in_log', 'User', ?, 40, ?, 's')", (_ONE["account_created_at"], NOW.isoformat()))
    conn.commit()
    # A copy taken while a process holds the database, as ``cp -a --link`` after a crash sees it.
    for suffix in ("", "-wal", "-shm"):
        os.link(f"{db_a}{suffix}", b / "state" / f"users.sqlite{suffix}")
    log_a = Path(f"{db_a}-wal").read_bytes()
    cache = UserCache(b / "state" / "users", backend="sqlite")
    assert cache.get("in_log", NOW)["public_event_count"] == 40
    assert Path(f"{db_a}-shm").stat().st_ino != (b / "state" / "users.sqlite-shm").stat().st_ino
    cache.put("bob", _ONE, NOW)
    cache.close()
    assert Path(f"{db_a}-wal").read_bytes() == log_a
    conn.close()


def _envelope(raw_dir, name, records):
    write_envelope(raw_dir / "author_history" / f"acme__{name}.json", source="github-graphql:author-history",
                   data={"window_anchor": NOW.isoformat(), "author_records": records}, fetched_at=NOW.isoformat())


def _rec(login, **kw):
    return {"login": login, "account_created_at": "2018-01-01T00:00:00Z", "public_event_count": 12,
            "eligibility_unmeasured": False, "eligibility_observed_at": NOW.isoformat(), **kw}


def test_hydrate_caches_only_what_a_lookup_recorded(tmp_path):
    raw = tmp_path / "raw"
    far_future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    _envelope(raw, "one", [
        _rec("real"),
        _rec("UPPER_case"),
        _rec("no_time", eligibility_observed_at=None),
        _rec("from_the_future", eligibility_observed_at=far_future),
        _rec("skipped", eligibility_looked_up=False),
        _rec("failed", eligibility_unmeasured=True),
        _rec("no_lookup", eligibility_resolved_locally=True),
        _rec("no_date", account_created_at=None),
        _rec("bad_date", account_created_at="not a date"),
        _rec("no_count", public_event_count=None),
        _rec("text_count", public_event_count="12"),
        _rec("negative", public_event_count=-1),
        _rec("bool_count", public_event_count=True),
        _rec("../escape"),
        {"login": 12345, "account_created_at": "2018-01-01T00:00:00Z", "public_event_count": 1,
         "eligibility_observed_at": NOW.isoformat()},
    ])
    assert hydrate_from_author_history_dir(raw) == 2
    assert sorted(_ro_rows(tmp_path / "state" / "users.sqlite")) == ["real", "upper_case"]


@pytest.mark.parametrize("bad", [
    {"data": {"author_records": 5}},
    {"data": {"author_records": {"login": "x"}}},
    {"data": 7},
    [1, 2, 3],
    {"data": {"author_records": [5, None, "x", [1]]}},
    {"data": {"author_records": [{"login": "huge", "account_created_at": "2018-01-01T00:00:00Z", "public_event_count": 10 ** 30,
                                  "eligibility_observed_at": NOW.isoformat()}]}},
    {"data": {"author_records": [{"login": "ancient", "account_created_at": "2018-01-01T00:00:00Z", "public_event_count": 1,
                                  "eligibility_observed_at": "0001-01-01T00:00:00+14:00"}]}},
])
def test_hydrate_skips_a_malformed_envelope_and_keeps_the_rest(tmp_path, bad):
    raw = tmp_path / "raw"
    _envelope(raw, "good", [_rec("real")])
    (raw / "author_history" / "acme__bad.json").write_text(json.dumps(bad))
    assert hydrate_from_author_history_dir(raw) == 1
    assert sorted(_ro_rows(tmp_path / "state" / "users.sqlite")) == ["real"]


def test_hydrate_skips_json_nested_too_deep_to_parse(tmp_path):
    raw = tmp_path / "raw"
    _envelope(raw, "good", [_rec("real")])
    (raw / "author_history" / "acme__deep.json").write_text("[" * 200_000)
    assert hydrate_from_author_history_dir(raw) == 1


def test_the_database_is_wal_with_normal_sync(tmp_path):
    cache = UserCache(_users_dir(tmp_path), backend="sqlite")
    cache.put("entry", _ONE, NOW)
    assert cache._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert cache._conn.execute("PRAGMA synchronous").fetchone()[0] == 1
    cache.close()


def test_the_import_never_replaces_a_newer_row(tmp_path):
    root = _users_dir(tmp_path)
    UserCache(root).put("entry", _ONE, NOW - timedelta(days=9))
    cache = UserCache(root, backend="sqlite")
    cache.put("entry", {**_ONE, "public_event_count": 99}, NOW)  # a lookup after the upgrade
    cache.close()
    os.utime(root, (1_700_000_200, 1_700_000_200))  # the folder changed: the next open imports again
    UserCache(root, backend="sqlite").close()
    assert _ro_rows(tmp_path / "state" / "users.sqlite")["entry"][1] == 99


def test_hydrate_over_a_corrupt_database_writes_no_json_and_the_next_one_fills_a_new_database(tmp_path):
    raw = tmp_path / "raw"
    _envelope(raw, "one", [_rec("real")])
    (tmp_path / "state").mkdir()
    db = tmp_path / "state" / "users.sqlite"
    db.write_bytes(b"not a database " * 500)
    assert hydrate_from_author_history_dir(raw) == 0
    assert not (tmp_path / "state" / "users").exists()
    assert len(list(tmp_path.glob("state/users.sqlite.corrupt-*"))) == 1
    assert hydrate_from_author_history_dir(raw) == 1
    assert sorted(_ro_rows(db)) == ["real"]


def test_a_database_that_breaks_while_open_falls_back_for_get(tmp_path):
    root = _users_dir(tmp_path)
    UserCache(root).put("old_entry", _ONE, _YESTERDAY)
    cache = UserCache(root, backend="sqlite")
    cache.put_many((f"u{i}", _ONE, NOW) for i in range(5000))
    conn = cache._ensure_db()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("PRAGMA cache_size=1")
    conn.execute("PRAGMA shrink_memory")
    db = tmp_path / "state" / "users.sqlite"
    with open(db, "r+b") as fh:
        fh.truncate(4096 * 3)
    assert cache.get("old_entry", NOW)["public_event_count"] == 40  # from its JSON file
    assert cache.sqlite_error == "DatabaseError"
    assert cache.put("after_break", _ONE, NOW) is True and (root / "after_break.json").is_file()
    assert len(list(tmp_path.glob("state/users.sqlite.corrupt-*"))) == 1
