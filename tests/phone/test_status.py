"""status(): the state machine, the words, the measured numbers. A real Engine over a real home in tmp, no threads."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest

from phone import entry, runner
from tests.helpers.rig import GH, LAN, LAYERS, T0, WIFI, Rig, idle_rig

pytestmark = pytest.mark.usefixtures("clean_env", "utc")


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def keys(d: dict) -> set[str]:
    return set(d)


def repo_lines(n: int, upto: int, stop_every: int = 0, prune_every: int = 0, fail_every: int = 0) -> list[str]:
    """What gather prints for a run of ``n`` projects, up to the ``upto``-th: the size line, then one line per project
    (every ``stop_every``-th stopped at the black-hole check, else every ``prune_every``-th pruned, else every
    ``fail_every``-th failed)."""
    out = [f"collectors: repo_object; repos: {n}"]
    for i in range(1, upto + 1):
        if stop_every and i % stop_every == 0:
            what = "OK (stopped at black_hole)"
        elif prune_every and i % prune_every == 0:
            what = "SKIP (ineligible: stale)"
        elif fail_every and i % fail_every == 0:
            what = "FAIL governance"
        else:
            what = "OK"
        out.append(f"o{i}/n{i} [pypi]: {what}")
    return out


#: What `gather.main --skip-ineligible` printed for the two projects of probe P4 (one stops at the black-hole check).
P4_LINES = ["collectors: governance; repos: 2", "o/bh [pypi]: OK (stopped at black_hole)", "o/ok [pypi]: OK",
            "done: 2 clean, 0 with failures"]


def replay(rig: Rig, lines: list[str]) -> None:
    """Count gather's lines into the run state, then let the monitor's progress sample them at once."""
    rs = rig.eng.runstate
    for line in lines:
        runner.count_line(rs, line)
    rig.eng.progress.tick(rs.layer, rs.run, rs.checked, rs.total, active=True, force=True)


# ---- the schema -----------------------------------------------------------------------------------------------
def test_the_snapshot_carries_every_field_of_the_contract_and_its_amendments(rig):
    rig.run_on()
    s = rig.status()
    assert keys(s) >= {"schema", "now", "state", "headline", "detail", "reason", "progress", "cycle", "github",
                       "network", "phone", "contacts", "lan", "tokens", "alerts", "health", "device"}
    assert s["schema"] == 1 and datetime.fromisoformat(s["now"]).timestamp() == pytest.approx(T0, abs=1)
    assert keys(s["reason"]) == {"code", "text", "resume_at", "override"}
    assert keys(s["progress"]) == {"step_index", "step_count", "step_name", "done", "total", "pct", "eta_text",
                                   "last_progress_s", "checked", "stopped", "pruned", "failed"}
    assert keys(s["cycle"]) == {"started", "last_finished", "next_due", "interval_days", "steps"}
    assert keys(s["cycle"]["steps"][0]) == {"id", "name", "state", "pct", "finished"}
    assert keys(s["github"]) >= {"calls_left", "limit", "resets_in_s", "floor", "in_use"}
    assert keys(s["network"]) >= {"type", "mobile_allowed", "bytes_today", "bytes_7d"}
    assert keys(s["network"]["bytes_today"]) == {"wifi", "mobile"} and len(s["network"]["bytes_7d"]) == 7
    assert keys(s["phone"]) == {"battery_pct", "charging", "temp_c", "free_gb", "data_gb"}
    assert keys(s["lan"]) >= {"mode", "serving", "address", "share_until", "last_fetch", "ready_steps", "ready_gb"}
    assert keys(s["tokens"]) == {"github", "lan"} and keys(s["tokens"]["github"]) == {"set", "state", "tested_at"}
    assert keys(s["health"]) == {"last_error", "retries_today", "uptime_s"}
    assert keys(s["device"]) == {"power_save", "battery_optimized", "notifications_enabled", "data_saver", "wifi_ip"}
    assert [x["id"] for x in s["cycle"]["steps"]] == LAYERS
    assert all(isinstance(s[k], str) and s[k] for k in ("headline", "detail", "state"))


def test_status_is_cheap_even_with_thousands_of_envelopes(tmp_path):
    rig = Rig(tmp_path, candidates=3000)
    rig.envelopes("pr_window", 0, 3000)
    rig.run_on()
    began = time.perf_counter()
    for _ in range(50):
        rig.eng.status()
    assert (time.perf_counter() - began) / 50 < 0.05                    # under 50 ms a call, as the contract asks


# ---- first run, starting ------------------------------------------------------------------------------------------
def test_first_run_with_no_tokens_and_no_list(tmp_path):
    r = Rig(tmp_path, tokens=False, candidates=None)
    s = r.status()
    assert s["state"] == "first_run" and s["reason"]["code"] == "token_missing"
    assert s["headline"] == "Welcome to the collector"
    assert "Add the GitHub token and the link token" in s["detail"]
    assert {a["id"] for a in s["alerts"]} == {"no_token", "no_candidates"}
    assert s["tokens"] == {"github": {"set": False, "state": "untested", "tested_at": None}, "lan": {"set": False}}
    assert s["contacts"] == []


def test_first_run_with_tokens_but_no_repo_list(tmp_path):
    r = Rig(tmp_path, candidates=None)
    s = r.status()
    assert s["state"] == "first_run" and s["headline"] == "No repo list yet" and s["reason"]["code"] is None
    assert [(a["id"], a["level"]) for a in s["alerts"]] == [("no_candidates", "info")]


def test_the_engine_is_starting_until_the_first_device_facts_arrive(tmp_path):
    r = Rig(tmp_path, facts=None)
    s = r.status()
    assert s["state"] == "starting" and s["headline"] == "Starting up"
    r.eng.update_device(json.dumps(WIFI))
    r.eng.runstate.mode = "idle"
    r.mark("MAIN.done", T0 - 3600)
    assert r.status()["state"] != "starting"


def test_before_start_is_called_the_state_is_starting(tmp_path):
    r = Rig(tmp_path, started=False, facts=None)
    assert r.status()["state"] == "starting"


def test_a_token_that_has_not_been_tested_yet_says_so(tmp_path):
    r = Rig(tmp_path, accepted=False)
    s = r.status()
    assert s["state"] == "starting" and s["headline"] == "Checking the GitHub token"
    assert s["tokens"]["github"]["state"] == "untested"


# ---- working --------------------------------------------------------------------------------------------------------
def test_working_numbers_are_measured_from_the_outcome_lines_against_the_candidate_count(rig):
    rig.run_on("L1")
    replay(rig, ["o/r [pypi]: OK"] * 40)
    rig.clock.advance(12)
    s = rig.status()
    assert s["state"] == "working" and s["reason"]["code"] is None
    assert s["headline"] == "Collecting pull requests"
    assert s["detail"] == "Step 1 of 7 · 40 of 100 projects checked"
    p = s["progress"]
    assert (p["step_index"], p["step_count"], p["step_name"]) == (1, 7, "Pull requests")
    assert (p["done"], p["total"], p["pct"]) == (40, 100, 40)
    assert p["last_progress_s"] == 12                                  # since the last line was counted
    steps = {x["id"]: x for x in s["cycle"]["steps"]}
    assert steps["L1"]["state"] == "now" and steps["L1"]["pct"] == 40
    assert all(steps[i]["state"] == "later" for i in LAYERS[1:])
    assert s["github"]["in_use"] is True and s["cycle"]["next_due"] is None


def test_the_owners_layer_selection_changes_the_step_numbers(rig):
    rig.eng.settings.set({"layers": ["L0", "LK"]})
    rig.run_on("L0")
    s = rig.status()
    assert s["detail"].startswith("Step 1 of 2") and [x["id"] for x in s["cycle"]["steps"]] == ["L0", "LK"]
    assert s["progress"]["step_count"] == 2


def test_a_confirming_pass_and_mobile_data_and_a_restart_show_in_the_detail(tmp_path):
    r = Rig(tmp_path, started=False, facts=None)
    (r.home / "state" / "running").write_text("1\n")                    # left behind: Android killed the process
    r.eng.start(GH, LAN)
    r.eng.controls.set_token(True, "accepted", T0)
    r.eng.settings.set({"network": "wifi_mobile"})
    r.eng.update_device(json.dumps({**WIFI, "network": "mobile", "wifi_ip": None}))
    r.run_on("L1", run=2)
    assert r.eng.restart_ts == T0
    s = r.status()
    assert s["detail"] == ("Step 1 of 7 \u00b7 0 of 100 projects checked \u00b7 pass 2 \u00b7 using mobile data "
                           "\u00b7 restarted by Android at 10:00")
    assert s["network"]["type"] == "mobile" and s["network"]["mobile_allowed"] is True
    assert "Restarted by Android" in [e["text"] for e in r.eng.timeline.list(0, 50)]
    r.clock.advance(1801)
    assert "restarted by Android" not in r.status()["detail"]
    clean = Rig(tmp_path / "clean", facts=WIFI)                          # a normal start has no marker to find
    assert clean.eng.restart_ts is None


def test_eta_says_the_estimate_improves_until_twenty_minutes_of_data_then_measures_the_rate(tmp_path):
    r = Rig(tmp_path, candidates=120)
    r.run_on("L1")
    text = {}
    for tick in range(1, 101):
        r.clock.advance(15)
        runner.count_line(r.eng.runstate, "o/r [pypi]: OK")
        r.eng.tick()
        if tick in (1, 79, 80, 100):
            text[tick] = r.status()["progress"]["eta_text"]
    assert text[1] == text[79] == "Estimate improves as it runs"
    assert text[80] == "About 10 minutes left in this step"            # 80 done in 20 min, 40 to go
    assert text[100] == "About 5 minutes left in this step"            # 100 done in 25 min, 20 to go


def test_eta_does_not_count_time_spent_paused(tmp_path):
    r = Rig(tmp_path, candidates=1000)
    r.run_on("L1")
    for _ in range(5):
        r.clock.advance(15)
        runner.count_line(r.eng.runstate, "o/r [pypi]: OK")
        r.eng.tick()
    r.eng.pause()
    for _ in range(200):                                               # fifty minutes of pause
        r.clock.advance(15)
        r.eng.tick()
    r.eng.resume()
    assert r.eng.progress._active_s < 120                              # the pause added nothing to the active time
    assert r.status()["progress"]["eta_text"] == "Estimate improves as it runs"


def test_progress_counts_outcome_lines(rig):
    rig.run_on("L3g")
    replay(rig, P4_LINES)                           # probe P4: one project stops at the black-hole check, no envelope
    s = rig.status()
    p = s["progress"]
    assert (p["done"], p["total"], p["pct"]) == (2, 2, 100)
    assert (p["checked"], p["stopped"], p["pruned"], p["failed"]) == (2, 1, 0, 0)
    assert s["detail"] == "Step 4 of 7 \u00b7 2 of 2 projects checked \u00b7 1 needs only the pull-request check"


def test_one_stopped_project_reads_needs(rig):
    rig.run_on("L3g")
    replay(rig, P4_LINES)
    assert rig.status()["detail"].endswith("\u00b7 1 needs only the pull-request check")


def test_a_failed_project_counts_as_checked(rig):
    rig.run_on("L4")
    replay(rig, repo_lines(100, 100, stop_every=14, fail_every=50))
    s = rig.status()
    p = s["progress"]
    assert (p["checked"], p["stopped"], p["pruned"], p["failed"], p["total"], p["pct"]) == (100, 7, 0, 2, 100, 100)
    assert s["detail"] == "Step 6 of 7 \u00b7 100 of 100 projects checked \u00b7 7 need only the pull-request check"
    assert p["eta_text"] == "Finishing this step"


def test_pruned_projects_count_but_say_nothing(rig):
    rig.eng.settings.set({"layers": ["L0", "LK"]})
    rig.run_on("LK")
    replay(rig, repo_lines(40, 40, prune_every=5))
    s = rig.status()
    p = s["progress"]
    assert (p["checked"], p["stopped"], p["pruned"], p["failed"], p["total"], p["pct"]) == (40, 0, 8, 0, 40, 100)
    assert s["detail"] == "Step 2 of 2 \u00b7 40 of 40 projects checked"


def test_the_four_counts_are_zero_when_no_step_runs_and_in_the_cold_snapshot(rig):
    rig.run_on("L2")
    replay(rig, repo_lines(10, 10, stop_every=2, prune_every=3, fail_every=5))
    rs = rig.eng.runstate
    assert (rs.stopped, rs.pruned, rs.failed) == (5, 2, 1)             # the last pass's counts stay on the run state
    rs.mode, rs.layer = "idle", None
    p = rig.status()["progress"]
    assert (p["checked"], p["stopped"], p["pruned"], p["failed"]) == (0, 0, 0, 0)
    cold = json.loads(entry._cold_status())["progress"]
    assert keys(cold) == keys(p)
    assert (cold["checked"], cold["stopped"], cold["pruned"], cold["failed"]) == (0, 0, 0, 0)


def test_total_is_the_runs_repo_count(rig):
    rig.run_on("L1")                                # the candidates file has 100 rows; gather drops two illegal names
    replay(rig, repo_lines(98, 98))
    p = rig.status()["progress"]
    assert (p["done"], p["total"], p["pct"]) == (98, 98, 100)
    assert p["eta_text"] == "Finishing this step"


def test_total_falls_back_to_the_candidate_count(rig):
    rig.run_on("L1")                                # before gather prints its size line
    s = rig.status()
    assert (s["progress"]["done"], s["progress"]["total"]) == (0, 100)
    assert s["detail"] == "Step 1 of 7 \u00b7 0 of 100 projects checked"


def test_eta_reaches_finishing(tmp_path):
    r = Rig(tmp_path, candidates=120)
    r.run_on("L1")
    text = {}
    for tick in range(1, 121):
        r.clock.advance(15)
        runner.count_line(r.eng.runstate, "o/r [pypi]: OK")             # one project a tick
        r.eng.tick()
        if tick in (1, 79, 80, 100, 120):
            text[tick] = r.status()["progress"]["eta_text"]
    assert text[1] == text[79] == "Estimate improves as it runs"
    assert text[80] == "About 10 minutes left in this step"
    assert text[100] == "About 5 minutes left in this step"
    assert text[120] == "Finishing this step"                              # checked == total, not "less than a minute"


def test_a_new_pass_starts_the_estimate_afresh(tmp_path):
    r = Rig(tmp_path, candidates=120)
    r.run_on("L1")
    rs = r.eng.runstate
    for _ in range(80):                                                 # twenty minutes of lines in pass 1
        r.clock.advance(15)
        runner.count_line(rs, "o/r [pypi]: OK")
        r.eng.tick()
    assert r.status()["progress"]["eta_text"] == "About 10 minutes left in this step"
    rs.run = 2                                                          # what run_layer does at the start of a pass
    rs.checked = rs.stopped = rs.pruned = rs.failed = rs.total = 0
    r.clock.advance(15)
    r.eng.tick()
    p = r.status()["progress"]
    assert p["done"] == 0 and p["eta_text"] == "Estimate improves as it runs"


# ---- waiting, retrying ----------------------------------------------------------------------------------------------
def test_waiting_for_the_github_limit_names_the_clock_time_the_calls_left_and_the_floor(rig):
    rig.run_on("L1")
    rig.eng.gate.feed_budget({"x-ratelimit-remaining": "500", "x-ratelimit-limit": "5000",
                              "x-ratelimit-reset": str(int(T0 + 2280)), "x-ratelimit-resource": "core"})
    rig.eng.gate._rate = ("core", T0 + 2280)
    s = rig.status()
    assert s["state"] == "waiting" and s["reason"]["code"] == "rate_limit"
    assert s["headline"] == "Waiting for GitHub's hourly limit"
    assert s["detail"] == (f"Only 500 calls are left, and it keeps 500 for you. It carries on by itself at "
                           f"{rig.at(2280)}, in 38 minutes.")
    assert datetime.fromisoformat(s["reason"]["resume_at"]).timestamp() == pytest.approx(T0 + 2280, abs=1)
    assert (s["github"]["calls_left"], s["github"]["limit"], s["github"]["resets_in_s"]) == (500, 5000, 2280)


def test_no_wifi_says_wifi_lost_at_the_time_it_was_lost_and_waits_for_it_to_return(rig):
    rig.run_on("L1")
    rig.clock.advance(7 * 60)
    rig.eng.update_device(json.dumps({**WIFI, "network": "none", "wifi_ip": None}))
    s = rig.status()
    assert s["state"] == "waiting" and s["reason"]["code"] == "no_wifi" and s["network"]["type"] == "none"
    assert s["headline"] == "Wi-Fi lost at 10:07"
    assert "Wi-Fi was lost at 10:07 and mobile data is off" in s["detail"]
    rig.eng.update_device(json.dumps(WIFI))
    assert rig.status()["state"] == "working"
    texts = [e["text"] for e in reversed(rig.eng.timeline.list(0, 50))]
    assert "Wi-Fi lost at 10:07" in texts and "Wi-Fi is back. Carrying on." in texts


def test_no_wifi_from_the_start_and_data_saver_have_their_own_words(tmp_path):
    r = Rig(tmp_path, facts={**WIFI, "network": "none", "wifi_ip": None})
    r.run_on("L1")
    s = r.status()
    assert s["headline"] == "Waiting for Wi-Fi" and "Mobile data is off, so it waits" in s["detail"]
    r.eng.settings.set({"network": "wifi_mobile"})
    r.eng.update_device(json.dumps({**WIFI, "network": "mobile", "wifi_ip": None, "data_saver": True}))
    s = r.status()
    assert s["state"] == "waiting" and "Data Saver is on" in s["detail"]
    r.eng.update_device(json.dumps({**WIFI, "network": "none", "wifi_ip": None, "data_saver": False}))
    assert r.status()["headline"] == "Waiting for a network"


def test_a_streak_of_unanswered_requests_is_retrying_with_the_time_it_tries_again(rig):
    rig.run_on("L1")
    rig.eng.settings.set({"timeout_streak": 2})
    rig.eng.controls.on_failure("deps.dev", True)
    rig.eng.controls.on_failure("deps.dev", True)
    s = rig.status()
    assert s["state"] == "retrying" and s["reason"]["code"] == "slow_network"
    assert s["headline"] == "The network is slow"
    assert s["detail"] == "2 requests in a row got no answer from the network. Trying again in 10 minutes."
    assert datetime.fromisoformat(s["reason"]["resume_at"]).timestamp() == pytest.approx(T0 + 600, abs=1)
    assert s["health"]["last_error"]["text"] == "2 requests in a row got no answer from the network"
    assert s["health"]["last_error"]["action"].startswith("It waits 10 minutes")
    rig.clock.advance(601)
    assert rig.status()["state"] == "working"


# ---- paused -------------------------------------------------------------------------------------------------------------
def test_battery_heat_and_disk_pauses_have_their_numbers_their_override_and_their_events(rig):
    rig.run_on("L1")
    seen = rig.eng.timeline.list(0, 1)[0]["id"]
    rig.eng.update_device(json.dumps({**WIFI, "battery_pct": 18, "charging": False}))
    s = rig.status()
    assert s["state"] == "paused" and s["reason"]["code"] == "battery" and s["reason"]["override"] == "battery"
    assert s["headline"] == "Battery is at 18%" and s["detail"] == "The limit is 20%. It carries on at 25%."
    assert s["cycle"]["steps"][0]["state"] == "paused"
    rig.eng.override("battery", True)
    assert rig.status()["state"] == "working"
    rig.eng.update_device(json.dumps({**WIFI, "temp_c": 41.0}))
    s = rig.status()
    assert s["state"] == "paused" and s["reason"]["code"] == "heat" and s["reason"]["override"] == "heat"
    assert s["headline"] == "The phone is hot (41 \u00b0C)"
    assert "cools below 37 \u00b0C" in s["detail"]
    rig.eng.update_device(json.dumps({**WIFI, "temp_c": 30.0}))
    rig.free[0] = 1.2
    rig.clock.advance(2)                                               # the rules are looked at again every second
    s = rig.status()
    assert s["state"] == "paused" and s["reason"]["code"] == "disk_low" and s["reason"]["override"] is None
    assert s["headline"] == "Only 1.2 GB of space is left" and "resumes by itself above 3 GB" in s["detail"]
    assert [a["id"] for a in s["alerts"]] == ["disk_low"]
    rig.free[0] = 3.5
    rig.clock.advance(2)
    s = rig.status()
    assert s["state"] == "working" and s["alerts"] == []
    texts = [e["text"] for e in reversed(rig.eng.timeline.list(seen, 50))]
    assert texts == ["Paused: battery is at 18%", "Keep going anyway turned on for low battery",
                     "Carrying on, because Keep going anyway is on.", "Paused: the battery is hot (41 \u00b0C)",
                     "The battery cooled down. Carrying on.", "Paused: only 1.2 GB of space is left",
                     "There is space again. Carrying on."]


def test_every_change_of_reason_is_one_event(rig):
    rig.run_on("L1")
    before = rig.eng.timeline.count()
    rig.eng.update_device(json.dumps({**WIFI, "battery_pct": 10}))
    rig.eng.update_device(json.dumps({**WIFI, "battery_pct": 11}))       # same reason: no new event
    rig.eng.update_device(json.dumps({**WIFI, "battery_pct": 40}))
    rig.eng.update_device(json.dumps({**WIFI, "battery_pct": 41}))
    assert rig.eng.timeline.count() - before == 2


def test_the_users_pause_and_resume(rig):
    rig.run_on("L1")
    rig.eng.pause()
    s = rig.status()
    assert s["state"] == "paused_by_user" and s["reason"]["code"] == "user"
    assert s["headline"] == "Paused by you" and "Tap Resume" in s["detail"]
    assert s["cycle"]["steps"][0]["state"] == "paused"
    rig.eng.resume()
    assert rig.status()["state"] == "working"
    assert [e["text"] for e in rig.eng.timeline.list(0, 5)][:2] == ["Resumed", "Paused by you"]


# ---- attention ------------------------------------------------------------------------------------------------------------
def test_stuck_rejected_too_powerful_missing_token_and_repeated_errors_are_attention_with_alerts(rig):
    rig.run_on("L1")
    rig.eng.stuck = True
    s = rig.status()
    assert s["state"] == "attention" and s["reason"]["code"] == "stuck"
    assert s["headline"] == "No progress for 25 minutes" and "Tap Resume, or run the self-check" in s["detail"]
    assert [a["id"] for a in s["alerts"]] == ["stuck"]
    rig.eng.stuck = False
    rig.eng.controls.set_token(True, "rejected", T0)
    s = rig.status()
    assert (s["state"], s["reason"]["code"], s["headline"]) == ("attention", "token_rejected",
                                                               "GitHub rejected the token")
    assert [a["id"] for a in s["alerts"]] == ["token_rejected"] and s["tokens"]["github"]["state"] == "rejected"
    assert s["cycle"]["steps"][0]["state"] == "stopped"
    rig.eng.controls.set_token(True, "too_powerful", T0)
    s = rig.status()
    assert s["headline"] == "That token is too powerful" and [a["id"] for a in s["alerts"]] == ["token_too_powerful"]
    rig.eng.controls.set_token(False)
    rig.mark("L1.staged", T0 - 100)                                    # there is history now, so this is not a first run
    s = rig.status()
    assert (s["state"], s["reason"]["code"], s["headline"]) == ("attention", "token_missing", "No GitHub token")
    assert [a["id"] for a in s["alerts"]] == ["no_token"]
    rig.eng.controls.set_token(True, "accepted", T0)
    rig.eng.runstate.failures = 3
    rig.eng.runstate.last_failure = "a step stopped with code 4"
    s = rig.status()
    assert s["state"] == "attention" and s["reason"]["code"] == "repeated_errors"
    assert [a["id"] for a in s["alerts"]] == ["repeated_errors"] and "3 times in a row" in s["detail"]


def test_no_repo_list_after_a_first_cycle_is_attention_not_first_run(rig):
    rig.mark("L1.complete", T0 - 86400)
    (rig.home / "raw" / "candidates.jsonl").unlink()
    rig.eng.runstate.mode = "no_candidates"
    s = rig.status()
    assert s["state"] == "attention" and s["reason"]["code"] == "no_candidates"
    assert [(a["id"], a["level"]) for a in s["alerts"]] == [("no_candidates", "attention")]


# ---- idle, finished, stopped ---------------------------------------------------------------------------------------------
def test_idle_is_the_state_of_a_home_with_every_step_complete_and_a_fresh_main_done(tmp_path):
    r, lines = idle_rig(tmp_path)
    s = r.status()
    assert s["state"] == "idle" and s["reason"]["code"] is None
    assert s["headline"] == "Idle, next cycle due 8 Nov"
    assert s["detail"] == ("Last cycle finished 11 Oct, 04:04. Next cycle starts 8 Nov (in 26 days). "
                           "Nothing is running, so nothing is using the network.")
    assert any(line == "Idle, next cycle due 2026-11-08" for line in lines)         # logged, not a silent sleep
    assert "Idle, next cycle due 2026-11-08" in r.eng.techlog.read_text()
    assert datetime.fromisoformat(s["cycle"]["next_due"]).timestamp() == pytest.approx(
        datetime(2026, 11, 8, 4, 4, 6, tzinfo=timezone.utc).timestamp(), abs=1)
    assert datetime.fromisoformat(s["cycle"]["last_finished"]).timestamp() == pytest.approx(
        datetime(2026, 10, 11, 4, 4, 6, tzinfo=timezone.utc).timestamp(), abs=1)
    assert s["cycle"]["started"] is None and s["progress"]["pct"] == 100
    assert [x["state"] for x in s["cycle"]["steps"]] == ["done"] * 7 and all(x["finished"] for x in s["cycle"]["steps"])
    assert s["lan"]["ready_steps"] == 7 and s["github"]["in_use"] is False
    assert "Idle. The next cycle is due 8 Nov" in [e["text"] for e in r.eng.timeline.list(0, 20)]


def test_the_next_cycle_is_counted_in_whole_days_rounded_down_and_follows_the_interval(tmp_path):
    r, _ = idle_rig(tmp_path)
    r.clock.advance(26 * 86400 + 3 * 3600)                     # 15 hours to go: less than a day, so "today"
    assert "Next cycle starts 8 Nov (today)" in r.status()["detail"]
    r.clock.now = T0
    assert "(in 26 days)" in r.status()["detail"]
    r.clock.advance(-86400)                                    # 27 d 18 h to go: rounded down, not up
    assert "(in 27 days)" in r.status()["detail"]
    r.eng.settings.set({"interval_days": 7})
    r.clock.now = T0
    s = r.status()
    assert s["headline"] == "Idle, next cycle due 18 Oct" and "(in 5 days)" in s["detail"]
    assert s["cycle"]["interval_days"] == 7


def test_finished_shows_for_an_hour_after_the_cycle_then_idle(tmp_path):
    r, _ = idle_rig(tmp_path)
    r.eng.runstate.finished_at = r.clock() - 100
    s = r.status()
    assert s["state"] == "finished" and s["headline"] == "Cycle finished: 7 of 7 steps"
    assert s["detail"] == "The data is ready to pull from the workstation."
    r.clock.advance(3600)
    assert r.status()["state"] == "idle"


def test_stopped_says_it_is_stopped_and_nothing_uses_the_network(rig):
    rig.run_on("L1")
    rig.eng.stop()
    s = rig.status()
    assert s["state"] == "stopped" and s["headline"] == "Stopped" and s["reason"]["code"] == "user"
    assert "Tap Start" in s["detail"] and s["cycle"]["steps"][0]["state"] == "stopped"


# ---- the numbers around the state -------------------------------------------------------------------------------------
def test_contacts_and_the_day_totals_are_one_table(rig):
    stats = rig.eng.stats
    stats.add("api.github.com", "wifi", calls=3, sent=900, recv=40_000, token_sent=True)
    stats.add("deps.dev", "wifi", calls=2, sent=300, recv=6_000)
    stats.add("pypistats.org", "mobile", calls=1, sent=100, recv=500)
    stats.add("192.168.1.11", "wifi", calls=1, sent=2_000_000, recv=800)
    stats.add("evil.test", "wifi", calls=1, sent=1, recv=1, token_sent=True)   # a token flag on a host that is not GitHub
    s = rig.status()
    by_host = {c["host"]: c for c in s["contacts"]}
    assert sum(c["bytes"] for c in s["contacts"]) == sum(s["network"]["bytes_today"].values())
    assert by_host["api.github.com"]["token_sent"] is True and by_host["api.github.com"]["calls"] == 3
    assert by_host["api.github.com"]["what"] == "Pull requests and project details"
    assert all(c["token_sent"] is False for h, c in by_host.items() if h != "api.github.com")
    assert by_host["192.168.1.11"]["what"].startswith("Your workstation")
    assert by_host["deps.dev"]["what"] == "Which package belongs to which project"
    assert [c["bytes_7d"] for c in s["contacts"]] == sorted((c["bytes_7d"] for c in s["contacts"]), reverse=True)
    assert s["network"]["bytes_today"] == {"wifi": 40_900 + 6_300 + 2_000_800 + 2, "mobile": 600}
    assert s["health"]["retries_today"] == 0


def test_health_device_phone_and_lan_blocks(rig):
    rig.clock.advance(3661)
    rig.eng.update_device(json.dumps({**WIFI, "power_save": True, "battery_optimized": True,
                                      "notifications_enabled": False, "data_saver": True}))
    rig.eng.stats.error()
    rig.eng.stats.error()
    s = rig.status()
    assert s["health"]["uptime_s"] == 3661 and s["health"]["retries_today"] == 2
    assert s["device"] == {"power_save": True, "battery_optimized": True, "notifications_enabled": False,
                           "data_saver": True, "wifi_ip": "192.168.1.3"}
    assert s["phone"]["battery_pct"] == 82 and s["phone"]["charging"] is True and s["phone"]["temp_c"] == 33.0
    assert s["phone"]["free_gb"] == 150.0
    assert s["lan"]["mode"] == "manual" and s["lan"]["serving"] is False and s["lan"]["address"] is None
    assert s["lan"]["share_until"] is None and s["tokens"]["lan"] == {"set": True}
    assert s["tokens"]["github"]["state"] == "accepted" and s["tokens"]["github"]["tested_at"]


def test_data_size_and_ready_steps_are_measured(tmp_path):
    r = Rig(tmp_path)
    folder = r.home / "raw" / "pr_window"
    folder.mkdir(parents=True)
    (folder / "a.json").write_bytes(b"x" * 3_000_000)
    r.mark("L1.complete", T0 - 10)
    r.eng.datasize.maybe_refresh(force=True, block=True)
    s = r.status()
    assert s["phone"]["data_gb"] == 0.0 and s["lan"]["ready_steps"] == 1       # 3 MB rounds to 0.0 GB
    with open(folder / "b.json", "wb") as big:
        big.truncate(2_500_000_000)                                           # sparse: 2.5 GB without using disk
    r.eng.datasize.maybe_refresh(force=True, block=True)
    s = r.status()
    assert s["phone"]["data_gb"] == 2.5 and s["lan"]["ready_gb"] == 2.5


def test_the_device_block_carries_the_wifi_address_whether_or_not_the_phone_is_sharing(rig):
    s = rig.status()
    assert s["device"]["wifi_ip"] == "192.168.1.3"                      # the Wi-Fi row shows it from the start
    assert s["lan"]["serving"] is False and s["lan"]["address"] is None  # ... although nothing is shared
    rig.eng.update_device(json.dumps({**WIFI, "wifi_ip": " 10.0.0.7 "}))
    assert rig.status()["device"]["wifi_ip"] == "10.0.0.7"               # the checked text, trimmed
    for bad in ("0.0.0.0", "224.0.0.1", "255.255.255.255", "::1", "fe80::1", "not an address", "", 5, ["1.2.3.4"]):
        rig.eng.update_device(json.dumps({**WIFI, "wifi_ip": bad}))
        assert rig.status()["device"]["wifi_ip"] is None, bad            # never an address the app cannot use
    rig.eng.update_device(json.dumps({**WIFI, "wifi_ip": "192.168.1.3"}))
    rig.eng.update_device(json.dumps({**WIFI, "network": "mobile", "wifi_ip": None}))
    assert rig.status()["device"]["wifi_ip"] is None                     # off Wi-Fi: no address


def test_the_device_block_before_the_first_facts_has_no_wifi_address(monkeypatch):
    from phone import entry
    monkeypatch.setattr(entry, "_ENGINE", None)                           # start() has not been called yet
    assert json.loads(entry.status())["device"]["wifi_ip"] is None


def test_delivery_block_alerts_and_finished_detail(tmp_path):
    r, _ = idle_rig(tmp_path)
    s = r.status()
    assert s["delivery"] == {
        "mode": "lan",
        "drop_repo": "",
        "upload_token": {"set": False, "state": "untested", "tested_at": None},
        "uploading": False,
        "last_upload": None,
        "last_tag": None,
        "last_error": None,
    }
    r.eng.settings.set({"delivery_mode": "github_drop"})
    assert "no_drop_repo" in {a["id"] for a in r.status()["alerts"]}

    r.eng.settings.set({"drop_repo": "pctablet505/porchlamp-drop"})
    assert "no_upload_token" in {a["id"] for a in r.status()["alerts"]}

    r.eng.set_tokens(r.eng._gh_token, r.eng._lan_token, "github_pat_11ABCDEF0123456789abcdef_0123456789abcdefABCDEF0123456789")
    r.eng.upload_token_state = "accepted"
    r.eng.last_upload_tag = "cycle-2026-10-11-deadbeefcafe"
    r.eng.runstate.finished_at = r.clock() - 30
    done = r.status()
    assert done["state"] == "finished"
    assert done["detail"] == "Uploaded cycle-2026-10-11-deadbeefcafe to pctablet505/porchlamp-drop."
    assert done["delivery"]["upload_token"]["set"] is True
    assert done["delivery"]["last_tag"] == "cycle-2026-10-11-deadbeefcafe"

