"""The counters, the timeline, the technical log and the redaction."""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime
from pathlib import Path

import pytest

from phone import events as ev
from phone.events import Redactor, TechLog, Timeline
from phone.netstats import MAX_HOSTS, NetStats, day_of
from tests.helpers.fakes import FakeClock

pytestmark = pytest.mark.usefixtures("clean_env", "utc")

DAY = 86400
GH_CANARY = "ghp_CANARYevents0123456789abcdefghijkl"
LAN_CANARY = "lan-secret-canary-0123456789-abcdef"


# ---- NetStats -------------------------------------------------------------------------------------------------
def test_the_day_total_is_the_sum_of_the_per_host_bytes(tmp_path):
    clock = FakeClock()
    stats = NetStats(tmp_path / "n.json", clock)
    stats.add("api.github.com", "wifi", calls=1, sent=300, recv=0, token_sent=True)
    stats.add("api.github.com", "wifi", recv=9000)
    stats.add("deps.dev", "wifi", calls=1, sent=120, recv=500)
    stats.add("pypistats.org", "mobile", calls=2, sent=200, recv=4000)
    stats.add("192.168.1.11", "wifi", calls=1, sent=70_000, recv=900)       # the workstation, over the LAN
    hosts = stats.hosts()
    assert sum(h["bytes"] for h in hosts) == sum(stats.bytes_today().values()) == 300 + 9000 + 620 + 4200 + 70_900
    assert stats.bytes_today() == {"wifi": 300 + 9000 + 620 + 70_900, "mobile": 4200}
    assert [h["calls"] for h in hosts if h["host"] == "pypistats.org"] == [2]
    assert next(h for h in hosts if h["host"] == "api.github.com")["token_sent"] is True
    assert next(h for h in hosts if h["host"] == "deps.dev")["token_sent"] is False


def test_days_roll_over_and_seven_days_are_zero_filled_oldest_first(tmp_path):
    clock = FakeClock(1_790_000_000.0)
    stats = NetStats(tmp_path / "n.json", clock)
    stats.add("a.example", "wifi", sent=10, recv=20)
    first_day = day_of(clock())
    clock.advance(2 * DAY)
    stats.add("a.example", "mobile", sent=1, recv=2)
    week = stats.bytes_7d()
    assert len(week) == 7 and week[-1]["day"] == day_of(clock())
    assert [d["day"] for d in week] == sorted(d["day"] for d in week)
    by_day = {d["day"]: d for d in week}
    assert by_day[first_day]["wifi"] == 30 and by_day[day_of(clock())]["mobile"] == 3
    assert sum(1 for d in week if d["wifi"] == d["mobile"] == 0) == 5
    assert stats.bytes_today() == {"wifi": 0, "mobile": 3}                  # today only
    assert next(h for h in stats.hosts() if h["host"] == "a.example")["bytes_7d"] == 33


def test_counters_survive_a_restart_and_old_days_are_trimmed_at_fourteen(tmp_path):
    clock = FakeClock()
    path = tmp_path / "n.json"
    stats = NetStats(path, clock)
    stats.add("old.example", "wifi", calls=1, recv=1)
    clock.advance(15 * DAY)
    stats.add("new.example", "wifi", calls=1, recv=5)
    stats.flush()
    again = NetStats(path, clock)
    days = json.loads(path.read_text())["days"]
    assert list(days) == [day_of(clock())]                       # the 15-day-old day is gone
    assert again.bytes_today()["wifi"] == 5
    assert [h["host"] for h in again.hosts()] == ["new.example"]


def test_an_unreadable_counter_file_is_set_aside_not_deleted(tmp_path):
    path = tmp_path / "n.json"
    path.write_text("{not json", encoding="utf-8")
    stats = NetStats(path, FakeClock())
    assert stats.bytes_today() == {"wifi": 0, "mobile": 0}
    kept = list(tmp_path.glob("n.json.bad-*"))
    assert len(kept) == 1 and kept[0].read_text() == "{not json"


def test_a_flood_of_distinct_addresses_cannot_grow_the_table_without_bound(tmp_path):
    stats = NetStats(tmp_path / "n.json", FakeClock())
    for i in range(MAX_HOSTS + 50):
        stats.add(f"10.0.{i // 250}.{i % 250}", "wifi", calls=1, recv=1)
    names = {h["host"] for h in stats.hosts()}
    assert len(names) <= MAX_HOSTS + 1 and "(other)" in names
    assert sum(stats.bytes_today().values()) == MAX_HOSTS + 50          # nothing lost, only folded


# ---- Redactor ---------------------------------------------------------------------------------------------------
def test_token_shapes_and_headers_are_redacted_without_knowing_the_secret():
    redact = Redactor()
    for shape in ("ghp_abcdefghijklmnopqrstuvwxyz0123456789", "gho_aaaaaaaaaaaaaaaaaaaaaaaa", "ghu_" + "b" * 30,
                  "ghs_" + "c" * 30, "ghr_" + "d" * 30, "github_pat_11ABCDEFG0abcdefghij_klmnopqrstuvwxyz"):
        assert shape not in redact(f"token was {shape} today") and "[redacted]" in redact(f"x {shape} y")
    assert redact("Authorization: Bearer abcdef0123456789abcdef") == "Authorization: Bearer [redacted]"
    assert redact("authorization: token abcdef0123456789abcdef").endswith("[redacted]")
    assert redact("plain text with ghost and github words") == "plain text with ghost and github words"


def test_exact_secrets_held_in_memory_are_redacted_in_every_spelling():
    redact = Redactor()
    redact.add(LAN_CANARY)
    redact.add("short")                                  # too short to be a secret: ordinary words are left alone
    quoted = urllib.parse.quote(LAN_CANARY + " ok&", safe="")
    assert LAN_CANARY not in redact(f"link {LAN_CANARY} up") and "[redacted]" in redact(f"link {LAN_CANARY} up")
    assert redact("the short answer") == "the short answer"
    redact.add(LAN_CANARY + " ok&")
    assert quoted not in redact("GET /x?t=" + quoted)
    redact.discard(LAN_CANARY)
    assert LAN_CANARY in redact(LAN_CANARY)


# ---- Timeline ------------------------------------------------------------------------------------------------------
def test_events_come_back_newest_first_with_after_id_and_limit(tmp_path):
    clock = FakeClock()
    tl = Timeline(tmp_path / "e.jsonl", clock)
    ids = []
    for n in range(5):
        clock.advance(60)
        ids.append(tl.add("ok" if n % 2 else "info", f"event {n}", f"detail {n}", "network" if n == 2 else "progress"))
    assert ids == [1, 2, 3, 4, 5]
    rows = tl.list(0, 100)
    assert [r["text"] for r in rows] == [f"event {n}" for n in (4, 3, 2, 1, 0)]
    assert set(rows[0]) == {"id", "ts", "level", "text", "detail", "kind"}
    assert datetime.fromisoformat(rows[0]["ts"]).timestamp() == pytest.approx(clock(), abs=1)
    assert [r["id"] for r in tl.list(2, 100)] == [5, 4, 3]
    assert [r["id"] for r in tl.list(0, 2)] == [5, 4]
    assert next(r for r in rows if r["id"] == 3)["kind"] == "network"
    assert tl.add("bogus-level", "x") == 6 and tl.list(5, 1)[0]["level"] == "info"


def test_events_older_than_fourteen_days_are_removed_and_ids_never_repeat(tmp_path):
    clock = FakeClock()
    path = tmp_path / "e.jsonl"
    tl = Timeline(path, clock)
    tl.add("info", "very old")
    clock.advance(10 * DAY)
    tl.add("info", "ten days old")
    clock.advance(5 * DAY)                                  # "very old" is now 15 days old, "ten days old" is 5
    tl.add("info", "fresh")                                 # the add notices an hour has passed and trims
    assert [r["text"] for r in tl.list(0, 10)] == ["fresh", "ten days old"]
    clock.advance(10 * DAY)
    assert tl.trim() == 1 and [r["text"] for r in tl.list(0, 10)] == ["fresh"]
    clock.advance(20 * DAY)
    assert tl.trim() == 1 and tl.list(0, 10) == []
    reopened = Timeline(path, clock)
    assert reopened.add("info", "after a quiet month") == 4      # ids continue past everything ever written


def test_a_torn_last_line_is_skipped_and_cleaned_at_the_next_trim(tmp_path):
    clock = FakeClock()
    path = tmp_path / "e.jsonl"
    tl = Timeline(path, clock)
    tl.add("info", "one")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"id": 2, "t": 1, "level"')                 # a crash in the middle of a write
    assert [r["text"] for r in Timeline(path, clock).list(0, 10)] == ["one"]


def test_timeline_text_and_detail_are_redacted_on_the_way_in_and_out(tmp_path):
    redact = Redactor()
    tl = Timeline(tmp_path / "e.jsonl", FakeClock(), redact)
    tl.add("err", f"bad token {GH_CANARY}", f"sent as Authorization: Bearer {GH_CANARY}")
    assert GH_CANARY not in (tmp_path / "e.jsonl").read_text()
    redact.add("late-secret-value-123")                      # learned after the line was written
    (tmp_path / "e.jsonl").write_text((tmp_path / "e.jsonl").read_text().replace("bad token", "late-secret-value-123"))
    assert "late-secret-value-123" not in json.dumps(tl.list(0, 10))


# ---- TechLog ----------------------------------------------------------------------------------------------------------
def test_the_technical_log_is_one_file_a_day_and_keeps_fourteen_days(tmp_path):
    clock = FakeClock()
    log = TechLog(tmp_path / "log", clock)
    for day in range(20):
        log.write(f"line for day {day}")
        if day < 19:
            clock.advance(DAY)
    names = [p.name for p in log.files()]
    assert len(names) == 14                                                    # one file a day, 14 days
    assert names[-1] == f"porchlamp-{day_of(clock())}.log"
    assert names[0] == f"porchlamp-{day_of(clock() - 13 * DAY)}.log"
    text = log.read_text()
    assert "line for day 19" in text and "line for day 6\n" in text and "line for day 5\n" not in text
    assert text.index("line for day 6") < text.index("line for day 19")         # oldest first
    assert all(line.startswith("2026-") for line in text.splitlines())          # every line carries its date


def test_every_log_line_is_redacted_when_written_and_again_when_read(tmp_path):
    redact = Redactor()
    clock = FakeClock()
    log = TechLog(tmp_path / "log", clock, redact)
    redact.add(LAN_CANARY)
    log.write(f"serving with {LAN_CANARY} and {GH_CANARY}\nsecond line")
    path = log.files()[0]
    assert LAN_CANARY not in path.read_text() and GH_CANARY not in path.read_text()
    # a line another writer put in the file, or one written before the secret was known
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"2026-10-11 10:00:00 stray {LAN_CANARY} {GH_CANARY}\n")
    shown = log.read_text()
    assert LAN_CANARY not in shown and GH_CANARY not in shown and "stray" in shown
    exported = log.export(tmp_path / "exports")
    assert exported == tmp_path / "exports" / "porchlamp-log.txt"
    assert LAN_CANARY not in exported.read_text() and GH_CANARY not in exported.read_text()
    assert exported.read_text() == shown


def test_the_log_helpers_do_nothing_for_empty_text_and_cap_a_huge_line(tmp_path):
    log = TechLog(tmp_path / "log", FakeClock())
    log.write("")
    log.write("\n \n")
    assert log.files() == []
    log.write("x" * 50_000)
    assert max(len(line) for line in log.read_text().splitlines()) < ev.MAX_LINE + 40


def test_a_directory_with_other_files_is_not_confused_with_log_files(tmp_path):
    folder = Path(tmp_path / "log")
    folder.mkdir()
    (folder / "notes.txt").write_text("keep me")
    log = TechLog(folder, FakeClock())
    log.write("hello")
    assert (folder / "notes.txt").read_text() == "keep me" and "hello" in log.read_text()
    assert "keep me" not in log.read_text()
