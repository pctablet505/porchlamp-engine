"""D-COL-5 user cache (``porchlamp.collect.user_cache``): TTL, no look-ahead,
failures never cached, file names never built from unsafe logins."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from porchlamp.collect.user_cache import USER_CACHE_TTL_DAYS, UserCache

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
                 "eligibility_unmeasured": False},
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


def test_export_and_import_sqlite_gz_merges_by_newer_observed_at(tmp_path):
    """Q3: export_sqlite_gz and import_sqlite_gz round-trip SQLite user caches and keep newer observations."""
    import pytest
    from porchlamp.collect.user_cache import export_sqlite_gz, import_sqlite_gz

    src_cache = UserCache(tmp_path / "src" / "state" / "users", backend="sqlite")
    src_cache.put("alice", {"account_created_at": "2018-01-01T00:00:00Z", "public_event_count": 50}, NOW)
    src_cache.put("carol", {"account_created_at": "2020-01-01T00:00:00Z", "public_event_count": 15}, NOW - timedelta(days=20))
    gz_bytes = export_sqlite_gz(src_cache)
    src_cache.close()

    dst_cache = UserCache(tmp_path / "dst" / "state" / "users", backend="sqlite")
    # dst has an older observation for alice and a newer observation for carol
    dst_cache.put("alice", {"account_created_at": "2018-01-01T00:00:00Z", "public_event_count": 10}, NOW - timedelta(days=5))
    dst_cache.put("carol", {"account_created_at": "2020-01-01T00:00:00Z", "public_event_count": 99}, NOW)

    imported = import_sqlite_gz(dst_cache, gz_bytes)
    assert imported == 2
    assert dst_cache.get("alice", NOW)["public_event_count"] == 50
    assert dst_cache.get("carol", NOW)["public_event_count"] == 99

    with pytest.raises(ValueError):
        import_sqlite_gz(dst_cache, b"not-gzip-bytes")
    dst_cache.close()

