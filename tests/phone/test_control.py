"""Settings, the pause rules and the LAN listener, with real files and real sockets."""

from __future__ import annotations

import json
import socket

import pytest

from phone import control, serve
from phone.control import DEFAULTS, Controls, LanManager, Settings
from tests.helpers.fakes import FakeClock

pytestmark = pytest.mark.usefixtures("clean_env", "utc")

TOKEN = "link-token-canary-0123456789abcdef"
BOUNDS = {"battery_pause_pct": (5, 50), "heat_pause_c": (35, 50), "timeout_streak": (2, 20),
          "interval_days": (7, 90), "rate_floor": (100, 2000), "l4_workers": (1, 3), "github_workers": (1, 2)}


# ---- settings -----------------------------------------------------------------------------------------------
def test_the_defaults_are_the_contracts_and_the_lan_is_manual_and_overrides_are_off(tmp_path):
    s = Settings(tmp_path / "s.json")
    assert s.get() == {"network": "wifi", "battery_pause_pct": 20, "battery_override": False, "heat_pause_c": 40,
                       "heat_override": False, "timeout_streak": 5, "interval_days": 28,
                       "layers": ["L1", "L0", "L2", "L3g", "L3c", "L4", "LK"], "rate_floor": 500,
                       "lan_mode": "manual", "delivery_mode": "lan", "drop_repo": "", "l4_workers": 1,
                       "github_workers": 1}
    assert s.get() == DEFAULTS and s.get() is not DEFAULTS and s.get()["layers"] is not DEFAULTS["layers"]


@pytest.mark.parametrize("field", sorted(BOUNDS))
def test_every_numeric_field_accepts_exactly_its_bounds(tmp_path, field):
    low, high = BOUNDS[field]
    s = Settings(tmp_path / "s.json")
    for good in (low, high, (low + high) // 2, float(low)):
        reply = s.set({field: good})
        assert reply["ok"] and reply["errors"] == {} and s.value(field) == int(good), good
    before = s.get()
    for bad in (low - 1, high + 1, 0, -5, True, False, "20", "", None, 20.5, [low], {"v": low}, float("nan"),
                float("inf")):
        reply = s.set({field: bad})
        assert not reply["ok"] and list(reply["errors"]) == [field], bad
        assert reply["errors"][field]            # a sentence, not an empty string
        assert s.get() == before                 # invalid input changes nothing


def test_invalid_input_changes_nothing_even_for_the_valid_fields_sent_with_it(tmp_path):
    path = tmp_path / "s.json"
    s = Settings(path)
    s.set({"interval_days": 30})
    on_disk = path.read_bytes()
    reply = s.set({"interval_days": 45, "battery_pause_pct": 30, "heat_pause_c": 99, "lan_mode": "sometimes"})
    assert reply["ok"] is False and set(reply["errors"]) == {"heat_pause_c", "lan_mode"}
    assert s.value("interval_days") == 30 and s.value("battery_pause_pct") == 20
    assert path.read_bytes() == on_disk and reply["settings"]["interval_days"] == 30


def test_enums_booleans_layers_and_unknown_fields_are_validated_and_named(tmp_path):
    s = Settings(tmp_path / "s.json")
    assert s.set({"network": "wifi_mobile", "lan_mode": "auto", "battery_override": True})["ok"]
    for patch, field in [({"network": "mobile"}, "network"), ({"network": 1}, "network"),
                         ({"lan_mode": "AUTO"}, "lan_mode"), ({"battery_override": 1}, "battery_override"),
                         ({"heat_override": "true"}, "heat_override"), ({"layers": []}, "layers"),
                         ({"layers": ["L9"]}, "layers"), ({"layers": ["L1", "L1"]}, "layers"),
                         ({"layers": "L1"}, "layers"), ({"layers": [1]}, "layers"),
                         ({"colour": "red"}, "colour")]:
        reply = s.set(patch)
        assert not reply["ok"] and list(reply["errors"]) == [field], patch
    reply = s.set({"layers": ["LK", "L0"]})
    assert reply["ok"] and s.value("layers") == ["L0", "LK"]       # kept in step order
    assert not s.set(["not", "a", "dict"])["ok"] and not s.set(None)["ok"]


def test_settings_persist_and_a_corrupt_file_is_set_aside_and_replaced_by_defaults(tmp_path):
    path = tmp_path / "config" / "s.json"
    s = Settings(path)
    s.set({"interval_days": 60, "layers": ["L1", "LK"]})
    again = Settings(path)
    assert again.value("interval_days") == 60 and again.value("layers") == ["L1", "LK"]
    assert json.loads(path.read_text())["interval_days"] == 60
    path.write_text("{ not json", encoding="utf-8")
    broken = Settings(path)
    assert broken.get() == DEFAULTS and broken.load_note
    assert [p.read_text() for p in path.parent.glob("s.json.bad-*")] == ["{ not json"]
    path.write_text(json.dumps({"interval_days": 5000, "rate_floor": 900, "bogus": 1}))
    partial = Settings(path)
    assert partial.value("rate_floor") == 900 and partial.value("interval_days") == 28 and partial.load_note


def test_on_change_fires_once_with_old_and_new_and_not_for_a_no_op(tmp_path):
    seen = []
    s = Settings(tmp_path / "s.json", on_change=lambda old, new: seen.append((old["rate_floor"], new["rate_floor"])))
    s.set({"rate_floor": 700})
    s.set({"rate_floor": 700})
    assert seen == [(500, 700)]


# ---- pause rules ------------------------------------------------------------------------------------------------
WIFI = {"battery_pct": 80, "charging": False, "temp_c": 30.0, "network": "wifi", "wifi_ip": "127.0.0.1",
        "data_saver": False}


class Rules:
    def __init__(self, tmp_path):
        self.clock = FakeClock()
        self.free = [100.0]
        self.changes: list[tuple] = []
        self.said: list[tuple] = []
        self.settings = Settings(tmp_path / "s.json")
        self.controls = Controls(self.settings, clock=self.clock, free_gb=lambda: self.free[0], state_dir=tmp_path,
                                 emit=lambda *a: self.said.append(a),
                                 on_reason=lambda old, new: self.changes.append((old and old.code, new and new.code)))
        self.controls.update_device(WIFI)
        self.changes.clear()

    def device(self, **facts):
        self.controls.update_device({**self.controls.facts(), **facts})

    def reason(self, host="deps.dev", scope="collect"):
        self.controls.evaluate(force=True)
        r = self.controls.block_reason(host, scope)
        return r.code if r else None


def test_nothing_holds_a_healthy_phone_on_wifi(tmp_path):
    r = Rules(tmp_path)
    assert r.reason() is None and r.changes == []


def test_battery_pauses_below_the_level_and_resumes_at_level_plus_five(tmp_path):
    r = Rules(tmp_path)
    for pct, held in [(20, False), (19, True), (24, True), (25, False), (30, False)]:
        r.device(battery_pct=pct)
        assert (r.reason() == "battery") is held, pct
    assert r.changes == [(None, "battery"), ("battery", None)]            # one change per reason change
    r.settings.set({"battery_pause_pct": 30})
    r.device(battery_pct=29)
    assert r.reason() == "battery"
    r.device(battery_pct=34)
    assert r.reason() == "battery"
    r.device(battery_pct=35)
    assert r.reason() is None


def test_the_battery_reason_offers_its_override_and_the_override_lifts_the_pause(tmp_path):
    r = Rules(tmp_path)
    r.device(battery_pct=10)
    held = r.controls.current_reason(False)
    assert held.code == "battery" and held.state == "paused" and held.override == "battery"
    r.settings.set({"battery_override": True})
    assert r.reason() is None
    r.device(battery_pct=1)
    assert r.reason() is None                                               # still ignored at 1%
    r.controls.cycle_boundary()                                             # the cycle ended: off again
    assert r.settings.value("battery_override") is False and r.reason() == "battery"


def test_an_unknown_battery_level_does_not_pause(tmp_path):
    r = Rules(tmp_path)
    r.device(battery_pct=-1)
    assert r.reason() is None


def test_heat_pauses_above_the_level_and_resumes_three_degrees_below(tmp_path):
    r = Rules(tmp_path)
    for temp, held in [(40.0, False), (40.1, True), (38.0, True), (37.0, True), (36.9, False)]:
        r.device(temp_c=temp)
        assert (r.reason() == "heat") is held, temp
    r.device(temp_c=45.0)
    assert r.controls.current_reason(False).override == "heat"
    r.controls.settings.set({"heat_override": True})
    assert r.reason() is None
    r.controls.cycle_boundary()
    assert r.settings.value("heat_override") is False and r.reason() == "heat"


def test_disk_pauses_below_two_gb_and_resumes_above_three(tmp_path):
    r = Rules(tmp_path)
    for free, held in [(2.0, False), (1.99, True), (2.5, True), (3.0, True), (3.01, False), (2.5, False)]:
        r.free[0] = free
        assert (r.reason() == "disk_low") is held, free
    assert r.changes == [(None, "disk_low"), ("disk_low", None)]


def test_the_order_is_user_then_disk_then_heat_then_battery_then_network(tmp_path):
    r = Rules(tmp_path)
    r.device(battery_pct=5, temp_c=50.0, network="none", wifi_ip=None)
    r.free[0] = 0.5
    r.controls.set_user_pause(True)
    seen = [r.reason()]
    r.controls.set_user_pause(False)
    seen.append(r.reason())
    r.free[0] = 50.0
    seen.append(r.reason())
    r.device(temp_c=30.0)
    seen.append(r.reason())
    r.device(battery_pct=80)
    seen.append(r.reason())
    assert seen == ["user", "disk_low", "heat", "battery", "no_wifi"]


def test_mobile_data_is_never_used_unless_the_setting_allows_it(tmp_path):
    r = Rules(tmp_path)
    r.device(network="mobile", wifi_ip=None)
    assert r.reason() == "no_wifi"                                                 # default: Wi-Fi only
    assert r.controls.network_type() == "mobile"
    for host in ("api.github.com", "deps.dev", "pypistats.org"):
        assert r.reason(host) == "no_wifi"
    r.settings.set({"network": "wifi_mobile"})
    assert r.reason() is None
    r.device(network="mobile", wifi_ip=None, data_saver=True)
    assert r.reason() == "no_wifi" and "Data Saver" in r.controls.block_reason("deps.dev").text
    r.device(network="none", wifi_ip=None, data_saver=False)
    assert r.reason() == "no_wifi"
    r.device(network="wifi")
    assert r.reason() is None


def test_before_the_first_device_facts_everything_waits(tmp_path):
    settings = Settings(tmp_path / "s.json")
    controls = Controls(settings, clock=FakeClock(), state_dir=tmp_path)
    assert controls.block_reason("deps.dev", "collect").code == "starting"
    assert controls.block_reason("api.github.com", "token_test").code == "starting"
    controls.update_device({"network": "wifi", "battery_pct": 50})
    assert controls.block_reason("deps.dev", "collect") is None


def test_the_owners_own_acts_wait_only_for_the_network(tmp_path):
    r = Rules(tmp_path)
    r.device(battery_pct=3, temp_c=48.0)
    r.free[0] = 0.1
    r.controls.set_user_pause(True)
    assert r.reason() == "user"
    assert r.controls.block_reason("api.github.com", "token_test") is None
    assert r.controls.block_reason("api.github.com", "self_check") is None
    r.device(network="none", wifi_ip=None)
    assert r.controls.block_reason("api.github.com", "token_test").code == "no_wifi"


def test_a_streak_of_unanswered_requests_holds_requests_for_ten_minutes_then_lets_them_go(tmp_path):
    r = Rules(tmp_path)
    r.settings.set({"timeout_streak": 3})
    r.controls.on_failure("deps.dev", no_answer=True)
    r.controls.on_failure("deps.dev", no_answer=False)                            # it answered, or failed locally: not counted
    r.controls.on_failure("deps.dev", no_answer=True)
    assert r.reason() is None
    r.controls.on_response("deps.dev", 200, False)                                 # a good reply: the streak restarts
    r.controls.on_failure("deps.dev", no_answer=True)
    r.controls.on_failure("deps.dev", no_answer=True)
    assert r.reason() is None
    r.controls.on_failure("deps.dev", no_answer=True)
    held = r.controls.block_reason("deps.dev")
    assert held.code == "slow_network" and held.state == "retrying" and held.resume_at == r.clock() + 600
    r.clock.advance(599)
    assert r.reason() == "slow_network"
    r.clock.advance(2)
    assert r.reason() is None and r.controls.timeout_streak == 0
    assert [c[1] for c in r.changes] == ["slow_network", None]
    assert any("slow" in text[1].lower() for text in r.said)


def test_three_refused_tries_in_a_row_with_the_token_make_it_rejected(tmp_path):
    r = Rules(tmp_path)
    r.controls.set_token(True, "accepted", r.clock())
    r.controls.on_response("api.github.com", 401, True)
    r.controls.on_response("api.github.com", 401, True)
    r.controls.on_response("api.github.com", 200, True)                            # a good reply clears the count
    r.controls.on_response("api.github.com", 401, True)
    r.controls.on_response("api.github.com", 401, True)
    assert r.controls.token_state == "accepted"
    r.controls.on_response("api.github.com", 401, True)
    assert r.controls.token_state == "rejected"
    assert r.reason("api.github.com") == "token_rejected" and r.reason("deps.dev") is None
    assert r.controls.last_error["text"].startswith("GitHub answered")


def test_the_token_state_gates_only_github_and_only_for_collection(tmp_path):
    r = Rules(tmp_path)
    assert r.reason("api.github.com") == "token_missing" and r.reason("deps.dev") is None
    r.controls.set_token(True)
    assert r.reason("api.github.com") == "token_untested"
    assert r.controls.block_reason("api.github.com", "token_test") is None       # the check itself is allowed
    r.controls.set_token(True, "too_powerful", 1.0)
    assert r.reason("api.github.com") == "token_too_powerful"
    r.controls.set_token(True, "accepted", 1.0)
    assert r.reason("api.github.com") is None
    r.controls.set_token(False)
    assert r.reason("api.github.com") == "token_missing"


def test_the_users_pause_survives_a_restart(tmp_path):
    r = Rules(tmp_path)
    r.controls.set_user_pause(True)
    assert (tmp_path / "user_pause").is_file()
    again = Controls(Settings(tmp_path / "s.json"), clock=r.clock, state_dir=tmp_path)
    assert again.user_paused is True
    again.set_user_pause(False)
    assert not (tmp_path / "user_pause").exists()
    assert Controls(Settings(tmp_path / "s.json"), clock=r.clock, state_dir=tmp_path).user_paused is False


def test_bad_device_facts_are_ignored_field_by_field(tmp_path):
    r = Rules(tmp_path)
    r.controls.update_device({"battery_pct": "full", "temp_c": float("nan"), "network": "satellite",
                              "wifi_ip": "0.0.0.0", "charging": "yes"})
    facts = r.controls.facts()
    assert facts["battery_pct"] == 80 and facts["temp_c"] == 30.0 and facts["network"] == "wifi"
    assert facts["wifi_ip"] is None and facts["charging"] is False
    r.controls.update_device("not a dict")
    assert r.controls.facts()["battery_pct"] == 80


# ---- the LAN listener -----------------------------------------------------------------------------------------------
def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def can_connect(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


class Lan:
    def __init__(self, tmp_path, **facts):
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.clock = FakeClock()
        self.said: list[tuple] = []
        self.logged: list[str] = []
        self.port = free_port()
        self.settings = Settings(tmp_path / "s.json")
        self.controls = Controls(self.settings, clock=self.clock, state_dir=tmp_path)
        self.controls.update_device({**WIFI, **facts})
        self.manager = LanManager(
            make_server=lambda ip, port, token: serve.make_server(self.home, bind=ip, port=port, token=token),
            settings=self.settings, controls=self.controls, clock=self.clock, port=self.port,
            emit=lambda *a: self.said.append(a), log=self.logged.append)
        self.manager.set_token(TOKEN)

    def device(self, **facts):
        self.controls.update_device({**WIFI, **facts})
        self.manager.update()

    def close(self):
        self.manager.stop_server()


@pytest.fixture
def lan(tmp_path):
    rig = Lan(tmp_path)
    yield rig
    rig.close()


def test_by_default_nothing_listens_even_on_wifi_with_a_link_token(lan):
    lan.manager.update()
    assert lan.settings.value("lan_mode") == "manual"
    assert not lan.manager.serving and lan.manager.address is None and lan.manager.share_until is None
    assert not can_connect("127.0.0.1", lan.port)


def test_share_serves_for_the_asked_minutes_and_then_stops_by_itself(lan):
    lan.manager.share(5)
    assert lan.manager.serving and lan.manager.address == f"127.0.0.1:{lan.port}"
    assert can_connect("127.0.0.1", lan.port)
    assert lan.manager.share_until == lan.clock() + 300
    lan.clock.advance(299)
    lan.manager.update()
    assert lan.manager.serving
    lan.clock.advance(2)
    lan.manager.update()
    assert not lan.manager.serving and lan.manager.share_until is None and not can_connect("127.0.0.1", lan.port)
    texts = [s[1] for s in lan.said]
    assert texts == ["Sharing with the workstation started", "Sharing with the workstation stopped"]
    assert all(s[3] == "network" for s in lan.said)


def test_share_defaults_to_thirty_minutes_and_clamps_nonsense(lan):
    lan.manager.share()
    assert lan.manager.share_until == lan.clock() + 30 * 60
    lan.manager.share(0)
    assert lan.manager.share_until == lan.clock() + 30 * 60
    lan.manager.share("soon")
    assert lan.manager.share_until == lan.clock() + 30 * 60
    lan.manager.share(100_000)
    assert lan.manager.share_until == lan.clock() + control.SHARE_MAX_MIN * 60


def test_it_binds_only_the_wifi_address_and_nothing_else(tmp_path):
    # precondition: this box routes 127.0.0.2 to loopback, so a wildcard listener WOULD answer on it
    wide = socket.socket()
    wide.bind(("0.0.0.0", 0))
    wide.listen(1)
    assert can_connect("127.0.0.2", wide.getsockname()[1]), "the test box must route 127.0.0.2"
    wide.close()
    rig = Lan(tmp_path)
    try:
        rig.manager.share(10)
        assert rig.manager._server.server_address[0] == "127.0.0.1"
        assert can_connect("127.0.0.1", rig.port)
        assert not can_connect("127.0.0.2", rig.port)                  # another local address: refused
    finally:
        rig.close()


def test_it_stops_when_the_wifi_address_goes_away_and_returns_when_it_comes_back(lan):
    lan.manager.share(30)
    assert lan.manager.serving
    lan.device(network="none", wifi_ip=None)
    assert not lan.manager.serving and not can_connect("127.0.0.1", lan.port)
    lan.device(network="mobile", wifi_ip=None)
    assert not lan.manager.serving                                      # never on mobile data
    lan.device()
    assert lan.manager.serving and can_connect("127.0.0.1", lan.port)   # the share was still running


def test_it_moves_to_a_new_wifi_address_and_leaves_the_old_one(lan):
    lan.manager.share(30)
    lan.device(wifi_ip="127.0.0.3")
    assert lan.manager.address == f"127.0.0.3:{lan.port}"
    assert can_connect("127.0.0.3", lan.port) and not can_connect("127.0.0.1", lan.port)


def test_auto_mode_follows_wifi_and_manual_mode_stops_it(lan):
    lan.settings.set({"lan_mode": "auto"})
    lan.manager.update()
    assert lan.manager.serving and lan.manager.share_until is None
    lan.device(network="none", wifi_ip=None)
    assert not lan.manager.serving
    lan.device()
    assert lan.manager.serving
    lan.settings.set({"lan_mode": "manual"})
    lan.manager.update()
    assert not lan.manager.serving


@pytest.mark.parametrize("bad", ["0.0.0.0", "255.255.255.255", "224.0.0.1", "not-an-ip", "::", "", None, 12345])
def test_a_wildcard_or_nonsense_address_never_gets_a_listener(tmp_path, bad):
    rig = Lan(tmp_path)
    try:
        rig.settings.set({"lan_mode": "auto"})
        rig.device(wifi_ip=bad)
        assert not rig.manager.serving
    finally:
        rig.close()


def test_without_a_link_token_there_is_no_listener(tmp_path):
    rig = Lan(tmp_path)
    try:
        rig.manager.set_token(None)
        rig.settings.set({"lan_mode": "auto"})
        rig.manager.update()
        assert not rig.manager.serving
        rig.manager.set_token("short")                                   # too short: the server refuses it
        rig.manager.update()
        assert not rig.manager.serving and rig.said[-1][1] == "Could not start sharing"
    finally:
        rig.close()


def test_the_link_secret_comes_from_memory_and_no_token_file_is_written(lan):
    lan.manager.share(5)
    assert lan.manager._server.token == TOKEN
    assert not (lan.home / "config" / "lan_token").exists()
    lan.manager.set_token(TOKEN + "-rotated")
    assert lan.manager._server.token == TOKEN + "-rotated"
    assert TOKEN not in "".join(lan.logged) and TOKEN not in json.dumps(lan.said)


def test_a_failed_bind_is_reported_once_and_retried_later(tmp_path):
    rig = Lan(tmp_path)
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    blocker.bind(("127.0.0.1", rig.port))
    blocker.listen(1)
    try:
        rig.manager.share(30)
        assert not rig.manager.serving
        rig.manager.update()                                         # inside the retry delay: no second attempt
        assert [s[1] for s in rig.said].count("Could not start sharing") == 1
        blocker.close()
        rig.clock.advance(control.LAN_RETRY_S + 1)
        rig.manager.update()
        assert rig.manager.serving
    finally:
        blocker.close()
        rig.close()


def test_delivery_mode_and_drop_repo_validation_refuse_reserved_and_malformed_names(tmp_path):
    s = Settings(tmp_path / "s.json")
    for mode in ("lan", "github_drop", "both"):
        assert s.set({"delivery_mode": mode})["ok"]
    assert not s.set({"delivery_mode": "s3"})["ok"]

    assert s.set({"drop_repo": " pctablet505/porchlamp-drop "})["ok"]
    assert s.value("drop_repo") == "pctablet505/porchlamp-drop"
    assert s.set({"drop_repo": "   "})["ok"] and s.value("drop_repo") == ""

    for bad in (
        "pctablet505/porchlamp",
        "pctablet505/PORCHLAMP",
        "pctablet505/porchlamp-engine",
        "pctablet505/porchlamp-phone",
        "pctablet505/contributor-value-index",
        "onlyone",
        "a/b/c",
        "../porchlamp-drop",
        "owner/..",
        "owner/repo?x=1",
        123,
    ):
        reply = s.set({"drop_repo": bad})
        assert not reply["ok"] and list(reply["errors"]) == ["drop_repo"], bad


def test_upload_scope_obeys_pause_and_device_rules_without_the_collector_token(tmp_path):
    r = Rules(tmp_path)
    # No collector github_token set: collect is held by token_missing, upload is clear on healthy Wi-Fi.
    assert r.reason("api.github.com", "collect") == "token_missing"
    assert r.reason("api.github.com", "upload") is None
    assert r.reason("uploads.github.com", "upload") is None

    # User pause, low battery, and Wi-Fi loss hold upload just like collect.
    r.controls.set_user_pause(True)
    assert r.reason("uploads.github.com", "upload") == "user"
    r.controls.set_user_pause(False)
    r.device(battery_pct=15)
    assert r.reason("uploads.github.com", "upload") == "battery"
    r.device(battery_pct=50, network="none")
    assert r.reason("uploads.github.com", "upload") == "no_wifi"



def test_worker_settings_ranges_and_defaults(tmp_path):
    """T21 (D4): ``l4_workers`` (projects at once in step L4) is 1-3, default 1; ``github_workers`` (L0, L3g, L3c) is
    1-2, default 1. Anything else is refused with the range, and nothing is saved."""
    s = Settings(tmp_path / "settings.json")
    assert (s.value("l4_workers"), s.value("github_workers")) == (1, 1)
    for key, low, high in (("l4_workers", 1, 3), ("github_workers", 1, 2)):
        for good in (low, high):
            assert s.set({key: good})["ok"] is True and s.value(key) == good
        for bad in (low - 1, high + 1, "2", 2.5, True, None):
            reply = s.set({key: bad})
            assert reply["ok"] is False and reply["errors"] == {key: f"Pick a number from {low} to {high}."}
            assert s.value(key) == high
    assert Settings(tmp_path / "settings.json").get()["github_workers"] == 2      # saved, and read back
