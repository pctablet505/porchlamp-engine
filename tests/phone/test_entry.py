"""The engine's behaviours through phone.entry: tokens, the stuck detector, the self-check, danger, the collector."""

from __future__ import annotations

import hashlib
import io
import json
import os
import time
import urllib.request
import urllib.response
from http.client import HTTPMessage
from pathlib import Path

import pytest

from phone import entry, httpgate, runner
from phone.entry import Engine
from phone.httpgate import HostRefused, WouldBlock
from porchlamp.collect import ghcli
from porchlamp.collect import net as collect_net
from tests.helpers.fakes import FakeHTTP, github_responder, loopback_gate_options
from tests.helpers.rig import GH, LAN, T0, WIFI, Rig, leaks

pytestmark = pytest.mark.usefixtures("clean_env", "utc")

DONE_LINE = "done: 2 clean, 0 with failures"
LOOPBACK = {**WIFI, "wifi_ip": "127.0.0.1"}


def wait_until(predicate, seconds: float = 10.0, step: float = 0.05) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(step)
    return False


def stub_gather(calls: list[str] | None = None):
    def main(argv: list[str]) -> int:
        if calls is not None:
            calls.append(argv[3])
        print(DONE_LINE)
        return 0
    return main


def snapshot(root: Path) -> dict[str, str]:
    """relative path -> sha256 of the content, for every file under root."""
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


# ---- the module-level functions the app calls ---------------------------------------------------------------
@pytest.fixture
def api(tmp_path):
    entry._ENGINE = None
    entry._PENDING_DEVICE = None
    yield tmp_path / "files"
    if entry._ENGINE is not None:
        entry._ENGINE.stop()
    entry._ENGINE = None
    entry._PENDING_DEVICE = None


def test_every_contract_function_returns_its_schema(api):
    cold = json.loads(entry.status())                                  # before start(): a valid "starting" snapshot
    assert cold["state"] == "starting" and cold["schema"] == 1
    assert json.loads(entry.settings_get())["lan_mode"] == "manual" and entry.events(0, 10) == "[]"
    entry.update_device(json.dumps({"battery_pct": 70, "charging": False, "temp_c": 31.5, "network": "none",
                                    "wifi_ip": None, "data_saver": False}))     # pushed before start: kept for it
    entry.start(str(api), None, LAN)
    entry.start(str(api), None, LAN)                                   # idempotent
    first = entry.engine()
    s = json.loads(entry.status())
    assert s["schema"] == 1 and s["phone"]["battery_pct"] == 70 and s["network"]["type"] == "none"
    assert s["tokens"]["lan"] == {"set": True} and s["tokens"]["github"]["set"] is False
    reply = json.loads(entry.settings_set(json.dumps({"interval_days": 30, "rate_floor": 700})))
    assert reply["ok"] is True and reply["errors"] == {} and reply["settings"]["interval_days"] == 30
    assert os.environ["PORCHLAMP_RATE_FLOOR"] == "700"
    bad = json.loads(entry.settings_set(json.dumps({"interval_days": 3, "colour": 1})))
    assert bad["ok"] is False and set(bad["errors"]) == {"interval_days", "colour"}
    assert json.loads(entry.settings_get())["interval_days"] == 30
    assert json.loads(entry.settings_set("not json"))["ok"] is False
    entry.override("battery", True)
    assert json.loads(entry.settings_get())["battery_override"] is True
    entry.override("nonsense", True)                                   # ignored
    rows = json.loads(entry.events(0, 50))
    assert rows and set(rows[0]) == {"id", "ts", "level", "text", "detail", "kind"}
    assert rows[0]["id"] > rows[-1]["id"]
    assert [r["id"] for r in json.loads(entry.events(rows[1]["id"], 50))] == [rows[0]["id"]]
    exported = Path(entry.export_log())
    assert exported.parent == api / "exports" and exported.is_file()
    check = json.loads(entry.self_check())
    assert [c["id"] for c in check["checks"]] == ["github", "network", "link", "space", "progress", "battery"]
    assert all(set(c) == {"id", "title", "ok", "detail"} for c in check["checks"])
    assert isinstance(check["all_ok"], bool) and isinstance(check["took_s"], float)
    assert json.loads(entry.test_token("short")) == {"state": "rejected", "detail": "That does not look like a GitHub token."}
    entry.share(5)
    assert json.loads(entry.status())["lan"]["share_until"] is not None
    assert json.loads(entry.danger("reset", "wrong"))["ok"] is False
    entry.pause()
    assert entry.engine().controls.user_paused is True
    assert "Paused by you" in [e["text"] for e in json.loads(entry.events(0, 50))]
    entry.resume()
    assert entry.engine().controls.user_paused is False
    entry.stop()
    assert json.loads(entry.status())["state"] == "stopped"
    assert entry.engine() is first
    entry.start(str(api), None, LAN)                                   # Start again after Stop
    assert json.loads(entry.status())["state"] != "stopped"


def test_start_does_not_let_an_environment_hook_move_the_api_host(api, monkeypatch):
    monkeypatch.setenv("PORCHLAMP_GH_API", "http://127.0.0.1:9")
    monkeypatch.setenv("PORCHLAMP_ALLOW_TEST_API", "1")
    entry.start(str(api), None, None)
    assert "PORCHLAMP_GH_API" not in os.environ and "PORCHLAMP_ALLOW_TEST_API" not in os.environ
    assert os.environ["PORCHLAMP_GH"] == "http"
    assert httpgate.current() is entry.engine().gate


# ---- tokens --------------------------------------------------------------------------------------------------------
@pytest.fixture
def github():
    srv = FakeHTTP(github_responder(scopes="", private=[], remaining=3410))
    yield srv
    srv.close()


def token_rig(tmp_path, github, **kw):
    r = Rig(tmp_path, accepted=False, **kw)
    r.eng._api_base = github.url
    return r


def test_test_token_tells_the_four_outcomes_and_never_repeats_the_token(tmp_path, github):
    r = token_rig(tmp_path, github)
    tok = "ghp_TESTTOKENoutcomes0123456789abcdef"
    reply = json.loads(r.eng.test_token(tok))
    assert reply == {"state": "accepted", "detail": "GitHub accepted the token."}
    github.responder = github_responder(scopes="repo")
    assert json.loads(r.eng.test_token(tok))["state"] == "too_powerful"
    github.responder = github_responder(user_status=401)
    assert json.loads(r.eng.test_token(tok))["state"] == "rejected"
    github.close()
    assert json.loads(r.eng.test_token(tok))["state"] == "unreachable"
    blob = json.dumps(r.eng.timeline.list(0, 50)) + r.eng.techlog.read_text() + r.eng.status()
    assert tok not in blob
    results = [e["text"] for e in reversed(r.eng.timeline.list(0, 50)) if e["text"].startswith("Token tested")]
    assert results == ["Token tested: accepted", "Token tested: too powerful", "Token tested: rejected"]


def test_the_token_in_use_follows_its_test_result_and_a_too_powerful_one_is_never_used(tmp_path, github):
    r = token_rig(tmp_path, github)
    tok = r.eng._gh_token
    github.responder = github_responder(scopes="repo, read:org")
    assert json.loads(r.eng.test_token(tok))["state"] == "too_powerful"
    assert "GH_TOKEN" not in os.environ
    s = r.status()
    assert s["tokens"]["github"]["state"] == "too_powerful" and s["state"] == "attention"
    assert [a["id"] for a in s["alerts"]] == ["token_too_powerful"]
    with pytest.raises(WouldBlock) as held:                                # the gate holds every GitHub call
        r.eng.gate.urlopen(urllib.request.Request("https://api.github.com/user"), 3, wait=False)
    assert held.value.reason.code == "token_too_powerful"
    github.responder = github_responder(scopes="", private=[])
    assert json.loads(r.eng.test_token(tok))["state"] == "accepted"
    assert os.environ["GH_TOKEN"] == tok and r.status()["tokens"]["github"]["state"] == "accepted"
    github.responder = github_responder(user_status=401)
    r.eng.test_token(tok)
    assert "GH_TOKEN" not in os.environ and r.status()["tokens"]["github"]["state"] == "rejected"
    assert [a["id"] for a in r.status()["alerts"]] == ["token_rejected"]


def test_a_different_token_tested_first_is_adopted_when_it_is_then_started_with(tmp_path, github):
    r = token_rig(tmp_path, github)
    other = "ghp_ANOTHERtoken0123456789abcdefABCDEF"
    assert json.loads(r.eng.test_token(other))["state"] == "accepted"
    assert r.eng.controls.token_state == "untested"                      # it is not the token in use yet
    assert "GH_TOKEN" not in os.environ
    r.eng.set_tokens(other, LAN)                                         # Kotlin stores it and starts again
    assert r.eng.controls.token_state == "accepted" and os.environ["GH_TOKEN"] == other
    r.eng.set_tokens(other + "-new", LAN)                                # a token never tested stays untested
    assert r.eng.controls.token_state == "untested" and "GH_TOKEN" not in os.environ


def test_a_started_engine_checks_its_own_token_before_using_it(tmp_path, github, monkeypatch):
    monkeypatch.setattr(entry, "TOKEN_BLOCKED_RETRY_S", 0.2)
    eng = Engine(tmp_path / "files", clock=time.time, gather_main=stub_gather(), api_base=github.url,
                 gate_options=loopback_gate_options(), tick_s=0.2)
    try:
        eng.start(GH, LAN)
        assert eng.controls.token_state == "untested" and "GH_TOKEN" not in os.environ
        eng.update_device(json.dumps(WIFI))
        assert wait_until(lambda: eng.controls.token_state == "accepted", 15)
        assert os.environ["GH_TOKEN"] == GH
        assert [r.path for r in github.seen if r.path.startswith("/user")][:2] == [
            "/user", "/user/repos?visibility=private&per_page=1"]
    finally:
        eng.stop()


def test_remove_tokens_forgets_both_and_stops_the_listener_and_deletes_nothing(tmp_path, github):
    r = token_rig(tmp_path, github, facts=LOOPBACK)
    r.eng.test_token(r.eng._gh_token)
    assert os.environ["GH_TOKEN"] == GH
    r.eng.settings.set({"lan_mode": "auto"})
    r.eng.lan.update()
    assert r.eng.lan.serving
    before = snapshot(r.home)
    reply = json.loads(r.eng.danger("remove_tokens", "remove_tokens"))
    assert reply["ok"] is True
    s = r.status()
    assert s["tokens"] == {"github": {"set": False, "state": "untested", "tested_at": None}, "lan": {"set": False}}
    assert "GH_TOKEN" not in os.environ and not r.eng.lan.serving
    after = snapshot(r.home)
    assert all(after.get(rel) == digest for rel, digest in before.items() if not rel.startswith("state/"))
    assert not leaks(r.home, [GH, LAN])
    assert r.eng.redactor(LAN) == LAN                                    # forgotten: no exact secret is held any more


LIST_CANARY = "ghp_CANARYlist0123456789abcdefABCDEFG"
ENV_CANARY = "ghp_CANARYenv00123456789abcdefABCDEFGH"
TOKEN_ENV = ("PORCHLAMP_GH_TOKENS", "GITHUB_TOKEN")


class RecordingOpener:
    """ghcli's opener, replaced: records each request's Authorization header and answers 200 with ``{}``."""

    def __init__(self) -> None:
        self.auth: list[str | None] = []

    def open(self, req, timeout=None):
        self.auth.append(req.get_header("Authorization"))
        return urllib.response.addinfourl(io.BytesIO(b"{}"), HTTPMessage(), req.full_url, 200)


def test_tokens_env_cannot_outrank_accepted_token(tmp_path, github, monkeypatch):
    opener = RecordingOpener()
    monkeypatch.setattr(ghcli, "_OPENER", opener)
    r = token_rig(tmp_path, github)
    assert json.loads(r.eng.test_token(GH))["state"] == "accepted"
    r.eng.stop()                                                         # Start again: the token is the same, so only start() pops
    monkeypatch.setenv("PORCHLAMP_GH_TOKENS", f"{LIST_CANARY},{LIST_CANARY}-2")
    monkeypatch.setenv("GITHUB_TOKEN", ENV_CANARY)
    r.eng.start(GH, LAN)
    assert r.eng.controls.block_reason("api.github.com") is None         # so the request below cannot wait
    assert ghcli.run(["api", "repos/o/n"]).returncode == 0
    assert opener.auth == [f"Bearer {GH}"]                               # the accepted token, and no canary in any header
    assert [n for n in TOKEN_ENV if n in os.environ] == []


def test_set_tokens_pops_tokens_env(tmp_path, github, monkeypatch):
    r = token_rig(tmp_path, github)
    monkeypatch.setenv("PORCHLAMP_GH_TOKENS", LIST_CANARY)
    monkeypatch.setenv("GITHUB_TOKEN", ENV_CANARY)
    r.eng.set_tokens(GH + "-new", LAN)                                   # a different token: GH_TOKEN is popped, so are they
    assert [n for n in TOKEN_ENV if n in os.environ] == []


def test_forget_tokens_clears_token_env(tmp_path, github, monkeypatch):
    r = token_rig(tmp_path, github)
    assert json.loads(r.eng.test_token(GH))["state"] == "accepted" and os.environ["GH_TOKEN"] == GH
    monkeypatch.setenv("PORCHLAMP_GH_TOKENS", LIST_CANARY)
    monkeypatch.setenv("GITHUB_TOKEN", ENV_CANARY)
    assert json.loads(r.eng.danger("remove_tokens", "remove_tokens"))["ok"] is True
    assert [n for n in ("GH_TOKEN", *TOKEN_ENV) if n in os.environ] == []


# ---- danger ---------------------------------------------------------------------------------------------------------
def populated(tmp_path) -> Rig:
    r = Rig(tmp_path)
    for layer in ("L1", "L0"):
        r.mark(f"{layer}.complete", T0 - 100)
    r.mark("MAIN.done", T0 - 90)
    for collector in ("pr_window", "github_meta", "depsdev"):
        r.envelopes(collector, 0, 5)
    (r.home / "archives").mkdir()
    (r.home / "archives" / "L1.tar.gz").write_bytes(b"archive bytes")
    return r


def test_danger_refuses_a_wrong_confirm_and_an_unknown_action_and_touches_nothing(tmp_path):
    r = populated(tmp_path)
    before = snapshot(r.home)
    names = {p.name for p in r.home.iterdir()}
    for action, confirm in [("reset", "wrong"), ("reset", ""), ("reset", "RESET"), ("move_data_aside", "reset"),
                            ("remove_tokens", "reset"), ("explode", "explode"), ("", ""), ("reset", None)]:
        reply = json.loads(r.eng.danger(action, confirm))
        assert reply["ok"] is False and reply["detail"], (action, confirm)
    assert {p.name for p in r.home.iterdir()} == names
    assert snapshot(r.home) == before                                    # not one byte moved or changed
    assert (r.home / "state" / "refresh" / "MAIN.done").is_file() and (r.home / "raw" / "pr_window").is_dir()
    assert r.eng.controls.token_present is True and r.eng.lan_token_set is True and r.eng.controls.user_paused is False


def moved_aside_ok(r: Rig, before: dict[str, str], action: str) -> tuple[Path, Path]:
    raw_dest = next(p for p in r.home.iterdir() if p.name.startswith(f"raw-stale-{action}-"))
    marker_dest = next(p for p in (r.home / "state").iterdir() if p.name.startswith(f"refresh-prev-{action}-"))
    after = snapshot(r.home)
    for rel, digest in before.items():                                    # every file is still there, same bytes
        if rel.startswith("raw/") and rel.count("/") >= 2:
            moved = f"{raw_dest.name}/{rel[len('raw/'):]}"
            assert after.get(moved) == digest, rel
        elif rel.startswith("state/refresh/"):
            assert after.get(f"state/{marker_dest.name}/{rel[len('state/refresh/'):]}") == digest, rel
        elif not rel.startswith("state/log") and not rel.startswith("state/events"):
            assert after.get(rel) == digest, rel
    return raw_dest, marker_dest


def test_reset_moves_the_collector_folders_and_the_markers_aside_and_deletes_nothing(tmp_path):
    r = populated(tmp_path)
    before = snapshot(r.home)
    reply = json.loads(r.eng.danger("reset", "reset"))
    assert reply["ok"] is True and "Nothing was deleted" in reply["detail"] and "raw-stale-reset-" in reply["detail"]
    raw_dest, marker_dest = moved_aside_ok(r, before, "reset")
    assert sorted(p.name for p in raw_dest.iterdir()) == ["depsdev", "github_meta", "pr_window"]
    assert sorted(p.name for p in marker_dest.iterdir()) == ["L0.complete", "L1.complete", "MAIN.done"]
    assert not any((r.home / "raw" / c).exists() for c in ("pr_window", "github_meta", "depsdev"))
    assert (r.home / "raw" / "candidates.jsonl").is_file()                  # the repo list stays
    assert (r.home / "state" / "refresh").is_dir() and list((r.home / "state" / "refresh").iterdir()) == []
    assert (r.home / "archives" / "L1.tar.gz").read_bytes() == b"archive bytes"
    assert r.eng.controls.user_paused is False                              # a reset starts over by itself
    assert "Reset: progress moved aside" in [e["text"] for e in r.eng.timeline.list(0, 5)]


def test_move_data_aside_does_the_same_and_then_waits_for_the_owner(tmp_path):
    r = populated(tmp_path)
    before = snapshot(r.home)
    reply = json.loads(r.eng.danger("move_data_aside", "move_data_aside"))
    assert reply["ok"] is True and "Tap Resume" in reply["detail"]
    moved_aside_ok(r, before, "move_data_aside")
    assert r.eng.controls.user_paused is True and r.status()["state"] == "paused_by_user"


def test_danger_with_nothing_to_move_is_ok_and_says_so(tmp_path):
    r = Rig(tmp_path)
    reply = json.loads(r.eng.danger("reset", "reset"))
    assert reply == {"ok": True, "detail": "There was nothing to move. Nothing was deleted."}


def test_danger_stops_the_running_collector_first_and_starts_it_again(tmp_path):
    seen = {"stopped": 0, "runs": 0}

    def blocking(argv):
        seen["runs"] += 1
        try:
            while True:
                httpgate.current().sleep(0.05)       # a collector busy "between requests": stop must unwind it
        finally:
            seen["stopped"] += 1

    eng = Engine(tmp_path / "files", clock=time.time, gather_main=blocking, gate_options=loopback_gate_options(),
                 tick_s=0.2)
    try:
        r_candidates = eng.home / "raw" / "candidates.jsonl"
        r_candidates.write_text(json.dumps({"owner": "a", "name": "b", "ecosystem": "pypi"}) + "\n")
        eng.start(None, LAN)
        eng.settings.set({"layers": ["LK"]})
        eng.update_device(json.dumps(WIFI))
        assert wait_until(lambda: seen["runs"] == 1)
        (eng.home / "raw" / "depsdev").mkdir(parents=True, exist_ok=True)
        (eng.home / "raw" / "depsdev" / "x.json").write_text("{}")
        reply = json.loads(eng.danger("reset", "reset"))
        assert reply["ok"] is True and seen["stopped"] >= 1
        assert wait_until(lambda: seen["runs"] == 2)                       # a fresh collector took over
        aside = [p for p in eng.home.iterdir() if p.name.startswith("raw-stale-reset-")]
        assert len(aside) == 1 and (aside[0] / "depsdev" / "x.json").read_text() == "{}"
    finally:
        eng.stop()


# ---- the stuck detector -------------------------------------------------------------------------------------------------
class CountingRestarts:
    def __init__(self, eng: Engine) -> None:
        self.calls = 0
        eng.restart_collector = self.restart          # type: ignore[method-assign]

    def restart(self) -> None:
        self.calls += 1


def working(tmp_path) -> tuple[Rig, CountingRestarts]:
    r = Rig(tmp_path)
    r.run_on("L1")
    return r, CountingRestarts(r.eng)


def test_no_progress_for_twenty_five_minutes_while_working_raises_stuck_once_and_restarts_once(tmp_path):
    r, restarts = working(tmp_path)
    r.eng.tick()
    r.clock.advance(24 * 60)
    r.eng.tick()
    assert r.eng.stuck is False and restarts.calls == 0
    assert r.status()["state"] == "working"
    r.clock.advance(2 * 60)
    r.eng.tick()
    assert r.eng.stuck is True and restarts.calls == 1
    s = r.status()
    assert s["state"] == "attention" and s["reason"]["code"] == "stuck" and [a["id"] for a in s["alerts"]] == ["stuck"]
    events = [e for e in r.eng.timeline.list(0, 50) if e["text"] == "No progress for 25 minutes"]
    assert len(events) == 1 and events[0]["level"] == "warn" and "restarted" in events[0]["detail"]
    assert "restarting the collector thread once" in r.eng.techlog.read_text()
    for _ in range(20):                                             # still stuck, still one restart and one alert
        r.clock.advance(60)
        r.eng.tick()
    assert restarts.calls == 1 and r.eng.stuck is True
    r.eng.gate.touch_progress()                                     # a request finally completes
    r.clock.advance(1)
    r.eng.tick()
    assert r.eng.stuck is False and r.status()["state"] == "working"
    assert "Progress is back" in [e["text"] for e in r.eng.timeline.list(0, 3)]


def test_progress_in_the_folders_or_a_finished_repo_keeps_it_from_being_stuck(tmp_path):
    r, restarts = working(tmp_path)
    r.eng.tick()
    for minute in range(1, 60):
        r.clock.advance(60)
        r.envelopes("pr_window", minute - 1, minute)             # one envelope a minute
        r.eng.tick()
    assert restarts.calls == 0 and r.eng.stuck is False
    r2, restarts2 = working(tmp_path / "second")
    r2.eng.tick()
    for _ in range(59):
        r2.clock.advance(60)
        r2.eng._runner_log("pallets/flask [pypi]: OK")            # gather says it finished a repo
        r2.eng.tick()
    assert restarts2.calls == 0 and r2.eng.stuck is False


def test_a_pause_a_wait_or_a_rate_limit_is_not_stuck_and_resume_starts_the_clock_afresh(tmp_path):
    r, restarts = working(tmp_path)
    r.eng.tick()
    r.eng.pause()
    r.clock.advance(3 * 3600)
    r.eng.tick()
    r.eng.resume()
    r.clock.advance(60)
    r.eng.tick()
    assert restarts.calls == 0 and r.eng.stuck is False              # three hours paused is not three hours stuck
    r.eng.gate._rate = ("core", r.clock() + 7200)                    # a long wait for GitHub's limit
    r.clock.advance(2 * 3600)
    r.eng.tick()
    assert restarts.calls == 0 and r.eng.stuck is False
    r.eng.update_device(json.dumps({**WIFI, "network": "none", "wifi_ip": None}))
    r.clock.advance(3600)
    r.eng.tick()
    assert restarts.calls == 0


def test_resume_clears_a_stuck_alert_and_gives_the_collector_a_fresh_window(tmp_path):
    r, restarts = working(tmp_path)
    r.eng.tick()
    r.clock.advance(26 * 60)
    r.eng.tick()
    assert r.eng.stuck is True
    r.eng.resume()
    assert r.eng.stuck is False and r.status()["alerts"] == []
    r.clock.advance(20 * 60)
    r.eng.tick()
    assert r.eng.stuck is False


# ---- the self-check ----------------------------------------------------------------------------------------------------
def test_the_self_check_has_six_plain_checks_and_one_free_rate_limit_call(tmp_path, github):
    r = token_rig(tmp_path, github, facts=LOOPBACK)
    r.eng.controls.set_token(True, "accepted", T0)
    r.eng.settings.set({"lan_mode": "auto"})
    r.eng.lan.update()
    try:
        reply = json.loads(r.eng.self_check())
        checks = {c["id"]: c for c in reply["checks"]}
        assert [c["id"] for c in reply["checks"]] == ["github", "network", "link", "space", "progress", "battery"]
        assert reply["all_ok"] is True and 0 <= reply["took_s"] < 5
        assert [x.path for x in github.seen] == ["/rate_limit"]                        # exactly one free call
        assert checks["github"]["detail"].startswith("Accepted. 3,410 calls left this hour, resets at ")
        assert checks["network"]["detail"] == "Wi-Fi. Mobile data is off."
        assert checks["link"]["detail"] == (f"Listening on {r.eng.lan.address}. Link token is set. "
                                            "Whether the workstation is on is not checked.")
        assert checks["space"]["detail"] == "150 GB free. The collector uses 0.0 GB."
        assert checks["progress"]["detail"].startswith("Last progress ") and checks["progress"]["detail"].endswith(
            "Not stuck.")
        assert checks["battery"]["detail"] == "82% and charging, 33 °C battery. Both pause rules are on."
    finally:
        r.eng.lan.stop_server()


def test_the_self_check_names_every_problem_in_plain_words(tmp_path, github):
    r = token_rig(tmp_path, github, tokens=True)
    r.eng.controls.set_token(True, "accepted", T0)
    r.eng.update_device(json.dumps({**WIFI, "network": "none", "wifi_ip": None, "battery_pct": 9, "temp_c": 47.0}))
    r.free[0] = 1.0
    r.eng.stuck = True
    r.clock.advance(1700)
    reply = json.loads(r.eng.self_check())
    checks = {c["id"]: c for c in reply["checks"]}
    assert reply["all_ok"] is False
    assert not any(checks[i]["ok"] for i in ("github", "network", "space", "progress", "battery"))
    assert checks["github"]["detail"] == "Not checked: there is no usable network right now."
    assert checks["network"]["detail"] == "No network. It waits until Wi-Fi returns."
    assert checks["space"]["detail"] == "Only 1.0 GB free. Free some space."
    assert checks["progress"]["detail"] == "No progress for 28 minutes. It looks stuck."
    assert github.count == 0                                                       # nothing was sent while offline
    r.eng.controls.set_token(False)
    r.eng._gh_token = None
    again = {c["id"]: c for c in json.loads(r.eng.self_check())["checks"]}
    assert again["github"]["ok"] is False and again["github"]["detail"] == "No GitHub token yet. Add it in Settings."
    r.eng._lan_token = None
    assert {c["id"]: c for c in json.loads(r.eng.self_check())["checks"]}["link"]["detail"] == (
        "The link token is not set. Add it in Settings.")


def test_the_self_check_marks_a_rejected_token_without_asking_github(tmp_path, github):
    r = token_rig(tmp_path, github)
    r.eng.controls.set_token(True, "rejected", T0)
    check = {c["id"]: c for c in json.loads(r.eng.self_check())["checks"]}["github"]
    assert check["ok"] is False and "rejected" in check["detail"] and github.count == 0
    r.eng.controls.set_token(True, "accepted", T0)
    github.responder = lambda req: (401, {}, b"{}")
    check = {c["id"]: c for c in json.loads(r.eng.self_check())["checks"]}["github"]
    assert check["ok"] is False and r.eng.controls.token_state == "rejected"


# ---- settings, events, the collector end to end ------------------------------------------------------------------------
def test_settings_changes_are_told_in_the_timeline(tmp_path):
    r = Rig(tmp_path, facts=LOOPBACK)
    mark = r.eng.timeline.list(0, 1)[0]["id"]
    r.eng.settings_set(json.dumps({"network": "wifi_mobile"}))
    r.eng.settings_set(json.dumps({"network": "wifi"}))
    r.eng.override("heat", True)
    r.eng.override("heat", False)
    r.eng.settings_set(json.dumps({"lan_mode": "auto"}))
    r.eng.settings_set(json.dumps({"lan_mode": "auto"}))              # no change, no event
    texts = [(e["text"], e["kind"]) for e in reversed(r.eng.timeline.list(mark, 50))]
    assert texts == [("Mobile data switched on", "network"), ("Mobile data switched off", "network"),
                     ("Keep going anyway turned on for a hot battery", "progress"),
                     ("Keep going anyway turned off for a hot battery", "progress"),
                     ("Workstation link set to auto", "network")]
    r.eng.lan.stop_server()


def test_the_users_pause_survives_an_engine_restart(tmp_path):
    r = Rig(tmp_path)
    r.eng.pause()
    again = Rig(tmp_path)                                           # the same files directory: a new process
    assert again.eng.controls.user_paused is True and again.status()["state"] == "paused_by_user"
    again.eng.resume()
    assert Rig(tmp_path).eng.controls.user_paused is False


def test_a_full_cycle_runs_the_selected_steps_only_and_tells_the_timeline(tmp_path):
    calls: list[str] = []
    eng = Engine(tmp_path / "files", clock=time.time, gather_main=stub_gather(calls),
                 gate_options=loopback_gate_options(), tick_s=0.2)
    try:
        (eng.home / "raw").mkdir(parents=True, exist_ok=True)
        (eng.home / "raw" / "candidates.jsonl").write_text(
            json.dumps({"owner": "a", "name": "b", "ecosystem": "pypi"}) + "\n")
        eng.settings.set({"layers": ["LK"], "battery_override": True})
        eng.start(None, LAN)
        eng.update_device(json.dumps(WIFI))
        assert wait_until(lambda: (eng.home / "state/refresh/LK.complete").is_file(), 20)
        assert wait_until(lambda: not (eng.home / "state/refresh/cycle.open").exists(), 20)
        assert set(calls) == {"depsdev,scorecard,downloads"}              # no GitHub step ran: none was selected
        assert (eng.home / "state/refresh/MAIN.done").is_file()
        assert not (eng.home / "state/refresh/L1.complete").exists()
        texts = [e["text"] for e in reversed(eng.timeline.list(0, 100))]
        for wanted in ("Started a new cycle", "Started step 1 of 1: package data", "Finished step 1 of 1: Package data",
                       "Cycle finished"):
            assert wanted in texts, wanted
        assert texts.index("Started a new cycle") < texts.index("Cycle finished")
        assert eng.settings.value("battery_override") is False              # overrides end with the cycle
        assert wait_until(lambda: json.loads(eng.status())["state"] in ("finished", "idle"), 10)
        assert wait_until(lambda: "Idle, next cycle due" in eng.techlog.read_text(), 10)
    finally:
        eng.stop()


def test_a_gatherer_that_raises_with_the_secret_in_its_message_leaves_no_secret_behind(tmp_path):
    def failing(argv):
        raise RuntimeError(f"upstream said no to {GH}")

    eng = Engine(tmp_path / "files", clock=time.time, gather_main=failing, gate_options=loopback_gate_options(),
                 tick_s=0.2)
    try:
        (eng.home / "raw").mkdir(parents=True, exist_ok=True)
        (eng.home / "raw" / "candidates.jsonl").write_text(
            json.dumps({"owner": "a", "name": "b", "ecosystem": "pypi"}) + "\n")
        eng.start(GH, LAN)
        eng.controls.set_token(True, "accepted", time.time())
        eng.settings.set({"layers": ["L1"]})
        eng.update_device(json.dumps(WIFI))
        assert wait_until(lambda: eng.runstate.failures >= 1, 15)
        blob = eng.status() + json.dumps(eng.timeline.list(0, 100)) + eng.techlog.read_text()
        assert GH not in blob
        assert "gatherer raised" in blob and "RuntimeError: upstream said no to [redacted]" in blob
    finally:
        eng.stop()
    assert GH not in Path(eng.export_log()).read_text() and not leaks(eng.files_dir, [GH, LAN])


def test_a_collector_that_cannot_even_load_tells_it_without_the_secret_and_tries_again(tmp_path, monkeypatch):
    attempts = []

    def cannot_load(engine_root, home):
        attempts.append(1)
        raise RuntimeError(f"cannot load the gatherer near {GH}")

    monkeypatch.setattr(runner, "load_gather", cannot_load)
    eng = Engine(tmp_path / "files", clock=time.time, gate_options=loopback_gate_options(), tick_s=0.2)
    try:
        (eng.home / "raw").mkdir(parents=True, exist_ok=True)
        (eng.home / "raw" / "candidates.jsonl").write_text(
            json.dumps({"owner": "a", "name": "b", "ecosystem": "pypi"}) + "\n")
        eng.start(GH, LAN)
        assert wait_until(lambda: any(e["text"] == "The collector hit an error" for e in eng.timeline.list(0, 50)), 15)
        s = json.loads(eng.status())
        assert GH not in eng.status() and s["health"]["last_error"]["text"] == "RuntimeError: cannot load the gatherer near [redacted]"
        assert s["health"]["last_error"]["action"] == "It tries again in a minute."
        assert len(attempts) == 1
    finally:
        eng.stop()
    assert not leaks(eng.files_dir, [GH, LAN])


# ---- the phone gate's own host list, and a start that fails closed -------------------------------------------------
def test_the_phone_engines_gate_does_not_list_the_archive_host_and_refuses_it_with_nothing_sent(tmp_path, monkeypatch):
    sent: list[object] = []

    def would_send(req, timeout=None):                               # nothing may leave the machine, whatever the gate does
        sent.append(req)
        raise AssertionError(f"a request was sent to {getattr(req, 'full_url', req)}")

    monkeypatch.setattr(urllib.request, "urlopen", would_send)
    eng = Engine(tmp_path / "files", threads=False)                  # no test options: the gate a phone really gets
    assert eng.gate.allowed == httpgate.PHONE_HOSTS and "data.gharchive.org" not in eng.gate.allowed
    assert httpgate.PHONE_HOSTS == httpgate.ALLOWED_HOSTS - {"data.gharchive.org"}
    eng.start(None, None)
    eng.update_device(json.dumps(WIFI))                              # the network is clear: only the allow-list can stop it
    with pytest.raises(HostRefused, match="allow-list"):
        collect_net.urlopen("https://data.gharchive.org/2026-10-01-0.json.gz", 3)         # the collectors' own path
    assert sent == []
    assert eng.stats.refused_today() == {"data.gharchive.org": 1}
    assert eng.stats.bytes_today() == {"wifi": 0, "mobile": 0}                             # nothing was sent
    hosts_on_screen = {c["host"] for c in json.loads(eng.status())["contacts"]}
    assert "data.gharchive.org" not in hosts_on_screen
    assert "data.gharchive.org" in httpgate.ALLOWED_HOSTS and "data.gharchive.org" in httpgate.Gate().allowed   # workstation


def test_start_refuses_to_run_when_the_collectors_do_not_go_through_the_gate_it_installed(tmp_path, monkeypatch):
    for broken in (None, object()):                                   # phone not importable, or some other module
        monkeypatch.setattr(collect_net, "_gate", broken)
        before = httpgate.current()
        eng = Engine(tmp_path / f"files{id(broken)}", threads=False)
        with pytest.raises(RuntimeError, match="not routed through the HTTP gate") as caught:
            eng.start(GH, LAN)
        assert GH not in str(caught.value) and LAN not in str(caught.value)
        assert eng.started is False and eng._gh_token is None and eng._lan_token is None
        assert "GH_TOKEN" not in os.environ and httpgate.current() is before        # nothing installed, no token held
        assert not (eng.home / "state" / "running").exists()
        assert GH not in eng.techlog.read_text() + json.dumps(eng.timeline.list(0, 50))
    monkeypatch.setattr(collect_net, "_gate", httpgate)
    eng = Engine(tmp_path / "ok", threads=False)
    eng.start(GH, LAN)                                                # routed: it starts
    assert eng.started is True and httpgate.current() is eng.gate
