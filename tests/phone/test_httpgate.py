"""The one HTTP function: allow-list, counters, pause and stop, the GitHub budget. Real sockets on loopback."""

from __future__ import annotations

import errno
import http.client
import json
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from phone import httpgate
from phone.control import Controls, Settings
from phone.httpgate import Gate, HostRefused, Stopped, WouldBlock
from phone.netstats import NetStats
from tests.helpers.fakes import FakeHTTP, github_responder, loopback_gate_options

pytestmark = pytest.mark.usefixtures("clean_env")

WIFI = {"battery_pct": 80, "temp_c": 30.0, "network": "wifi", "wifi_ip": "127.0.0.1", "data_saver": False}
CANARY = "ghp_CANARYgate0123456789abcdefABCDEF"


class Rig:
    """A gate with real counters and real controls, writing under ``tmp_path``."""

    def __init__(self, tmp_path: Path, *, loopback: bool = True, facts: dict | None = WIFI, **gate_kw) -> None:
        self.tmp = tmp_path
        self.lines: list[str] = []
        self.stats = NetStats(tmp_path / "netstats.json")
        self.settings = Settings(tmp_path / "settings.json")
        self.controls = Controls(self.settings, free_gb=lambda: 100.0, state_dir=tmp_path)
        if facts is not None:
            self.controls.update_device(facts)
        opts = loopback_gate_options() if loopback else {}
        opts.update(gate_kw)
        self.gate = Gate(stats=self.stats, controls=self.controls, log=self.lines.append, **opts)

    def get(self, url: str, timeout: float = 5, **kw):
        with self.gate.urlopen(url, timeout, **kw) as resp:
            return resp.status, resp.read()


@pytest.fixture
def server():
    srv = FakeHTTP()
    yield srv
    srv.close()


def test_an_unlisted_host_is_refused_and_nothing_is_sent(tmp_path, server):
    # 127.0.0.1 is made reachable for plain http, so the ONLY thing between the request and the socket is the allow-list
    rig = Rig(tmp_path, loopback=False, http_hosts=frozenset({"127.0.0.1"}))
    with pytest.raises(HostRefused, match="allow-list"):
        rig.get(server.url + "/x")
    assert server.count == 0
    assert rig.stats.refused_today() == {"127.0.0.1": 1}
    assert any("REFUSED 127.0.0.1" in line and "nothing was sent" in line for line in rig.lines)


def test_a_made_up_host_is_refused_without_a_lookup(tmp_path):
    rig = Rig(tmp_path, loopback=False)
    started = time.monotonic()
    with pytest.raises(HostRefused):
        rig.get("https://exfiltrate.invalid/steal?x=1")
    assert time.monotonic() - started < 1.0   # refused before any name lookup could stall
    assert rig.stats.refused_today() == {"exfiltrate.invalid": 1}


@pytest.mark.parametrize("url", [
    "http://api.github.com/user",              # plain http for a listed host
    "https://api.github.com:8443/user",        # a port that is not 443
    "https://user:pw@api.github.com/user",     # user-info in the URL
    "file:///etc/passwd",                      # not http at all
    "https://api.github.com.evil.invalid/",    # a look-alike suffix
])
def test_unsafe_urls_for_a_listed_host_are_refused(tmp_path, url):
    rig = Rig(tmp_path, loopback=False)
    with pytest.raises(HostRefused):
        rig.get(url)
    assert rig.stats.bytes_today() == {"wifi": 0, "mobile": 0}   # nothing was sent, so nothing was counted


def test_the_token_header_is_refused_for_any_host_but_api_github_com(tmp_path, server):
    opts = loopback_gate_options()
    opts["token_hosts"] = httpgate.TOKEN_HOSTS    # the fake is allowed, but is not a token host
    rig = Rig(tmp_path, loopback=False, **opts)
    req = urllib.request.Request(server.url + "/user", headers={"Authorization": f"Bearer {CANARY}"})
    with pytest.raises(HostRefused, match="credential"):
        rig.gate.urlopen(req, 5)
    assert server.count == 0
    assert CANARY not in "\n".join(rig.lines)


def test_calls_and_bytes_are_counted_per_host_and_network_and_saved(tmp_path, server):
    body = b'{"payload": "' + b"x" * 5000 + b'"}'
    server.responder = lambda req: (200, {"X-Test": "1"}, body)
    rig = Rig(tmp_path)
    status, data = rig.get(server.url + "/a")
    assert (status, data) == (200, body)
    rig.get(server.url + "/b")
    hosts = {row["host"]: row for row in rig.stats.hosts()}
    row = hosts["127.0.0.1"]
    assert row["calls"] == 2
    assert row["bytes"] >= 2 * len(body)                 # received payload alone
    assert row["bytes"] < 2 * len(body) + 2000           # plus a little for headers, not a different order
    assert rig.stats.bytes_today()["wifi"] == row["bytes"] and rig.stats.bytes_today()["mobile"] == 0
    rig.stats.flush()
    saved = json.loads((tmp_path / "netstats.json").read_text())
    cell = next(iter(saved["days"].values()))["hosts"]["127.0.0.1"]["wifi"]
    assert cell["calls"] == 2 and cell["recv"] >= 2 * len(body) and cell["sent"] > 0


def test_the_same_traffic_on_mobile_data_is_counted_as_mobile(tmp_path, server):
    rig = Rig(tmp_path)
    rig.settings.set({"network": "wifi_mobile"})
    rig.controls.update_device({**WIFI, "network": "mobile", "wifi_ip": None})
    rig.get(server.url + "/m")
    today = rig.stats.bytes_today()
    assert today["mobile"] > 0 and today["wifi"] == 0


def test_an_error_reply_keeps_its_body_and_is_counted(tmp_path, server):
    server.responder = lambda req: (404, {}, b'{"message": "Not Found"}')
    rig = Rig(tmp_path)
    with pytest.raises(urllib.error.HTTPError) as caught:
        rig.get(server.url + "/missing")
    assert caught.value.code == 404 and json.loads(caught.value.read()) == {"message": "Not Found"}
    assert rig.stats.hosts()[0]["bytes"] >= len(b'{"message": "Not Found"}')


def test_ratelimit_headers_of_a_github_reply_feed_the_budget_and_the_progress_clock(tmp_path):
    srv = FakeHTTP(github_responder(remaining=77, limit=5000, reset=1_790_003_999))
    try:
        rig = Rig(tmp_path)
        rig.gate.progress_ts = 0.0
        assert rig.gate.budget() is None
        rig.get(srv.url + "/user")
        budget = rig.gate.budget()
        assert budget["remaining"] == 77 and budget["limit"] == 5000 and budget["reset"] == 1_790_003_999
        assert rig.gate.progress_ts > 0.0   # a completed reply is progress
    finally:
        srv.close()


def test_a_reply_from_a_host_that_is_not_github_does_not_touch_the_budget(tmp_path):
    srv = FakeHTTP(github_responder(remaining=77))
    try:
        rig = Rig(tmp_path, budget_hosts=frozenset({"api.github.com"}))
        rig.get(srv.url + "/user")
        assert rig.gate.budget() is None
    finally:
        srv.close()


def test_pause_holds_the_request_until_resume_and_nothing_is_sent_meanwhile(tmp_path, server):
    rig = Rig(tmp_path)
    rig.controls.set_user_pause(True)
    done: list = []
    worker = threading.Thread(target=lambda: done.append(rig.get(server.url + "/p")), daemon=True)
    worker.start()
    time.sleep(1.6)
    assert server.count == 0 and not done and worker.is_alive()
    rig.controls.set_user_pause(False)
    rig.gate.kick()
    worker.join(5)
    assert done and done[0][0] == 200 and server.count == 1


def test_stop_ends_a_waiting_request_with_stopped_within_two_seconds(tmp_path, server):
    rig = Rig(tmp_path)
    rig.controls.set_user_pause(True)
    outcome: list = []

    def call():
        try:
            rig.get(server.url + "/s")
        except Stopped as exc:
            outcome.append(exc)

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    time.sleep(0.6)
    began = time.monotonic()
    rig.gate.stop()
    worker.join(3)
    assert outcome and time.monotonic() - began < 2.0 and server.count == 0


def test_stopped_is_not_an_exception_so_the_gatherers_broad_except_cannot_swallow_it(tmp_path, server):
    rig = Rig(tmp_path)
    rig.gate.stop()
    swallowed = []
    with pytest.raises(Stopped):
        try:
            rig.get(server.url + "/z")
        except Exception as exc:      # the gatherer's pattern: it would count a failed repo and carry on
            swallowed.append(exc)
    assert swallowed == [] and not isinstance(Stopped("x"), Exception)


def test_wait_false_raises_would_block_instead_of_waiting(tmp_path, server):
    rig = Rig(tmp_path)
    rig.controls.update_device({**WIFI, "network": "none", "wifi_ip": None})
    with pytest.raises(WouldBlock) as caught:
        rig.get(server.url + "/w", wait=False)
    assert caught.value.reason.code == "no_wifi" and server.count == 0


def test_a_streak_of_timeouts_makes_the_gate_wait_and_a_good_reply_resets_the_streak(tmp_path, server):
    server.delay = 0.6
    rig = Rig(tmp_path)
    rig.settings.set({"timeout_streak": 2})
    for _ in range(2):
        with pytest.raises((TimeoutError, urllib.error.URLError)):
            rig.get(server.url + "/slow", timeout=0.2)
    assert rig.controls.timeout_streak == 2
    blocked = rig.controls.block_reason("127.0.0.1", "collect")
    assert blocked is not None and blocked.code == "slow_network"
    assert rig.stats.errors_today() >= 2


class _Raising:
    """An opener that fails the way the network does."""

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    def open(self, req, timeout=None):
        raise self.exc


NO_ANSWER = {
    "dns": urllib.error.URLError(socket.gaierror(-2, "Name or service not known")),
    "refused": urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")),
    "reset": ConnectionResetError(104, "Connection reset by peer"),
    "dropped": http.client.RemoteDisconnected("Remote end closed connection without response"),
    "no route": urllib.error.URLError(OSError(errno.ENETUNREACH, "Network is unreachable")),
    "no host": urllib.error.URLError(OSError(errno.EHOSTUNREACH, "No route to host")),
    "timeout": TimeoutError("timed out"),
}


@pytest.mark.parametrize("name", sorted(NO_ANSWER))
def test_a_request_the_network_never_answered_counts_toward_the_wait(tmp_path, name):
    # 10-21: a few seconds of lost internet wrote off 90 to 350 projects a second because only timeouts counted
    rig = Rig(tmp_path)
    rig.settings.set({"timeout_streak": 2})
    for _ in range(2):
        with pytest.raises(type(NO_ANSWER[name])):
            rig.gate.urlopen("http://127.0.0.1:9/x", 1, opener=_Raising(NO_ANSWER[name]))
    assert rig.controls.timeout_streak == 2
    blocked = rig.controls.block_reason("127.0.0.1", "collect")
    assert blocked is not None and blocked.code == "slow_network"


@pytest.mark.parametrize("exc", [
    urllib.error.URLError("unknown url type: foo"),
    ValueError("bad request"),
    ssl.SSLCertVerificationError("certificate verify failed"),
])
def test_a_failure_that_is_not_a_missing_answer_is_not_counted(tmp_path, exc):
    rig = Rig(tmp_path)
    rig.settings.set({"timeout_streak": 2})
    for _ in range(3):
        with pytest.raises(type(exc)):
            rig.gate.urlopen("http://127.0.0.1:9/x", 1, opener=_Raising(exc))
    assert rig.controls.timeout_streak == 0 and rig.controls.block_reason("127.0.0.1", "collect") is None


def test_a_server_that_answers_with_an_error_is_not_a_missing_answer(tmp_path):
    gone = FakeHTTP(lambda req: (404, {}, b"not found"))
    try:
        rig = Rig(tmp_path)
        rig.settings.set({"timeout_streak": 2})
        for _ in range(3):
            with pytest.raises(urllib.error.HTTPError):
                rig.get(gone.url + "/gone")
        assert rig.controls.timeout_streak == 0
    finally:
        gone.close()


def test_stop_ends_a_rate_limit_wait_within_a_second(tmp_path):
    rig = Rig(tmp_path)
    ended = []
    rig.gate.on_rate_wait_end = lambda resource, seconds: ended.append(resource)

    def waiting():
        try:
            rig.gate.rate_wait("core", 10, 600)
        except Stopped:
            ended.append("stopped")

    worker = threading.Thread(target=waiting, daemon=True)
    worker.start()
    time.sleep(0.7)
    assert rig.gate.rate_state() is not None and rig.gate.rate_state()[0] == "core"
    began = time.monotonic()
    rig.gate.stop()
    worker.join(3)
    assert ended == ["stopped"] and time.monotonic() - began < 1.5 and rig.gate.rate_state() is None


def test_the_token_never_reaches_the_log_or_the_counters(tmp_path, server):
    rig = Rig(tmp_path)
    req = urllib.request.Request(server.url + "/user?access_token=" + CANARY,
                                 headers={"Authorization": f"Bearer {CANARY}"})
    with rig.gate.urlopen(req, 5) as resp:
        resp.read()
    server.responder = lambda r: (500, {}, b"boom")
    with pytest.raises(urllib.error.HTTPError):
        rig.gate.urlopen(urllib.request.Request(server.url + "/z", headers={"Authorization": f"Bearer {CANARY}"}), 5)
    rig.stats.flush()
    assert server.seen[0].headers["authorization"] == f"Bearer {CANARY}"   # it WAS sent to the (test) token host
    assert CANARY not in "\n".join(rig.lines)
    assert CANARY not in (tmp_path / "netstats.json").read_text()
    assert any("500" in line for line in rig.lines)                       # the failure was logged, without the token
    assert rig.stats.hosts()[0]["token_sent"] is True


# ---- redirects: every hop is a new request for the gate ------------------------------------------------------------
def redirect_to(location: str):
    return lambda req: (302, {"Location": location}, b"")


def servers(*hosts: str):
    """One FakeHTTP per loopback address, so each address stands for a different host."""
    return [FakeHTTP(bind=h) for h in hosts]


@pytest.fixture
def pair():
    origin, target = servers("127.0.0.1", "127.0.0.2")
    yield origin, target
    origin.close()
    target.close()


def test_a_redirect_from_a_listed_host_to_an_unlisted_host_is_refused_and_the_target_sees_nothing(tmp_path, pair):
    origin, target = pair
    origin.responder = redirect_to(target.url + "/secret")
    opts = loopback_gate_options()                                   # only 127.0.0.1 is listed; 127.0.0.2 is not
    rig = Rig(tmp_path, loopback=False, **opts)
    with pytest.raises(HostRefused, match="allow-list"):
        rig.get(origin.url + "/start")
    assert origin.count == 1 and target.count == 0
    assert rig.stats.refused_today() == {"127.0.0.2": 1}
    assert [h["host"] for h in rig.stats.hosts()] == ["127.0.0.1"]   # the unlisted host is not among the contacts
    assert any("REFUSED 127.0.0.2" in line for line in rig.lines)


def test_a_redirect_to_another_local_address_and_port_is_refused_too(tmp_path):
    router, origin = servers("127.0.0.1", "127.0.0.2")               # the "router on the owner's network"
    try:
        origin.responder = redirect_to(router.url + "/admin")
        opts = loopback_gate_options()
        opts.update(allowed=httpgate.PHONE_HOSTS | {"127.0.0.2"}, http_hosts=frozenset({"127.0.0.2"}))
        rig = Rig(tmp_path, loopback=False, **opts)
        with pytest.raises(HostRefused):
            rig.get(origin.url + "/")
        assert router.count == 0 and origin.count == 1
    finally:
        router.close()
        origin.close()


@pytest.mark.parametrize("location", ["http://deps.dev/x", "https://deps.dev:8443/x", "https://user:pw@deps.dev/x",
                                      "ftp://deps.dev/x", "https://exfiltrate.invalid/x"])
def test_a_redirect_to_a_downgrade_an_odd_port_user_info_or_a_made_up_host_is_refused_without_a_lookup(
        tmp_path, location):
    origin = FakeHTTP(redirect_to(location))
    try:
        rig = Rig(tmp_path)
        began = time.monotonic()
        with pytest.raises(HostRefused):
            rig.get(origin.url + "/")
        assert time.monotonic() - began < 2.0 and origin.count == 1
        assert sum(rig.stats.refused_today().values()) == 1
    finally:
        origin.close()


def test_a_redirect_to_another_listed_host_is_followed_and_counted_under_the_host_that_served_it(tmp_path, pair):
    origin, target = pair
    body = b'{"data": "' + b"y" * 20_000 + b'"}'
    origin.responder = redirect_to(target.url + "/final")
    target.responder = lambda req: (200, {}, body)
    rig = Rig(tmp_path, loopback=False, **loopback_gate_options("127.0.0.2"))
    status, data = rig.get(origin.url + "/start")
    assert (status, data) == (200, body) and origin.count == 1 and target.count == 1
    hosts = {h["host"]: h for h in rig.stats.hosts()}
    assert set(hosts) == {"127.0.0.1", "127.0.0.2"}
    assert hosts["127.0.0.1"]["calls"] == 1 and hosts["127.0.0.2"]["calls"] == 1
    assert hosts["127.0.0.2"]["bytes"] >= len(body)                  # the payload is the second host's
    assert hosts["127.0.0.1"]["bytes"] < 2_000                       # the first host only sent the redirect
    assert sum(h["bytes"] for h in hosts.values()) == sum(rig.stats.bytes_today().values())   # one table, one total
    assert rig.stats.refused_today() == {}


def test_the_credential_is_not_forwarded_after_a_change_of_host_but_stays_on_the_same_host(tmp_path, pair):
    origin, target = pair
    origin.responder = lambda req: (302, {"Location": target.url + "/x"}, b"") if req.path == "/start" else (
        (302, {"Location": "/again"}, b"") if req.path == "/same" else (200, {}, b"{}"))
    rig = Rig(tmp_path, loopback=False, **loopback_gate_options("127.0.0.2"))   # BOTH may take a token
    req = urllib.request.Request(origin.url + "/start", headers={"Authorization": f"Bearer {CANARY}"})
    with rig.gate.urlopen(req, 5) as resp:
        resp.read()
    assert origin.seen[0].headers["authorization"] == f"Bearer {CANARY}"
    assert "authorization" not in target.seen[0].headers
    req = urllib.request.Request(origin.url + "/same", headers={"Authorization": f"Bearer {CANARY}"})
    with rig.gate.urlopen(req, 5) as resp:
        resp.read()
    assert [r.headers.get("authorization") for r in origin.seen[1:]] == [f"Bearer {CANARY}"] * 2    # same host keeps it
    hosts = {h["host"]: h for h in rig.stats.hosts()}
    assert hosts["127.0.0.1"]["token_sent"] is True and hosts["127.0.0.2"]["token_sent"] is False
    assert CANARY not in "\n".join(rig.lines)


def test_the_redirect_limit_of_urllib_still_ends_a_loop(tmp_path):
    origin = FakeHTTP()
    origin.responder = redirect_to(origin.url + "/loop")
    try:
        rig = Rig(tmp_path)
        with pytest.raises(urllib.error.HTTPError):
            rig.get(origin.url + "/loop")
        assert 2 <= origin.count <= 11
        assert rig.stats.hosts()[0]["calls"] == origin.count        # every hop was counted as a call
    finally:
        origin.close()


def test_a_redirect_hop_waits_for_a_pause_like_any_other_request(tmp_path, pair):
    origin, target = pair

    def first_hop(req):
        rig.controls.set_user_pause(True)                           # the owner pauses while the redirect is in flight
        return 302, {"Location": target.url + "/x"}, b""

    origin.responder = first_hop
    rig = Rig(tmp_path, loopback=False, **loopback_gate_options("127.0.0.2"))
    done: list = []
    worker = threading.Thread(target=lambda: done.append(rig.get(origin.url + "/start")), daemon=True)
    worker.start()
    time.sleep(1.6)
    assert target.count == 0 and not done and worker.is_alive()
    rig.controls.set_user_pause(False)
    rig.gate.kick()
    worker.join(5)
    assert done and target.count == 1


def test_installed_is_false_for_the_gate_made_at_import_and_true_for_an_engines_own(tmp_path):
    before = httpgate.install(httpgate._IMPORT_GATE)
    try:
        assert not httpgate.installed()
        mine = Gate(**loopback_gate_options())
        httpgate.install(mine)
        assert httpgate.installed() and httpgate.current() is mine
        httpgate.install(httpgate._IMPORT_GATE)
        assert not httpgate.installed()
    finally:
        httpgate.install(before)


def test_uploads_github_com_is_allowed_for_phone_and_counts_streamed_content_length(tmp_path, server):
    assert "uploads.github.com" in httpgate.ALLOWED_HOSTS
    assert "uploads.github.com" in httpgate.PHONE_HOSTS
    assert "uploads.github.com" in httpgate.TOKEN_HOSTS
    assert "uploads.github.com" not in Gate().budget_hosts

    payload = b"z" * 8192
    received: list[bytes] = []

    def handle(req):
        received.append(req.body)
        return 201, {"Content-Type": "application/json"}, b'{"state":"uploaded"}'

    server.responder = handle
    rig = Rig(tmp_path)
    import io

    stream = io.BytesIO(payload)
    req = urllib.request.Request(
        server.url + "/repos/u/porchlamp-drop/releases/1/assets?name=L1.tar.gz",
        data=stream,
        headers={"Authorization": f"Bearer {CANARY}", "Content-Length": str(len(payload))},
        method="POST",
    )
    with rig.gate.urlopen(req, 5, scope="upload") as resp:
        assert resp.status == 201
        resp.read()
    assert received == [payload]
    row = {r["host"]: r for r in rig.stats.hosts()}["127.0.0.1"]
    assert row["bytes"] >= len(payload) and row["token_sent"] is True


def test_cross_scope_compartmentalization_refuses_privilege_confusion(tmp_path, server):
    rig = Rig(tmp_path)

    # 1. Collector scope can NEVER contact uploads.github.com (even without or with a credential).
    with pytest.raises(HostRefused, match="uploads.github.com"):
        rig.gate.urlopen(
            urllib.request.Request(
                "https://uploads.github.com/repos/u/porchlamp-drop/releases/1/assets?name=a",
                headers={"Authorization": f"Bearer {CANARY}"},
                method="POST",
            ),
            5,
            scope="collect",
        )

    # 2. Collector scope can NEVER send POST outside /graphql or send PATCH / DELETE / PUT anywhere.
    for method, path in [
        ("POST", "/repos/u/porchlamp-drop/releases"),
        ("PATCH", "/repos/u/porchlamp-drop/releases/1"),
        ("DELETE", "/repos/u/porchlamp-drop/releases/assets/1"),
        ("PUT", "/repos/u/porchlamp/contents/index.html"),
    ]:
        with pytest.raises(HostRefused):
            rig.gate.urlopen(urllib.request.Request(server.url + path, data=b"{}", method=method), 5, scope="collect")

    # 3. token_test and self_check scopes allow GET only.
    for sc in ("token_test", "self_check"):
        with pytest.raises(HostRefused, match="only GET"):
            rig.gate.urlopen(urllib.request.Request(server.url + "/graphql", data=b"{}", method="POST"), 5, scope=sc)

    # 4. Upload scope is locked strictly to non-reserved /repos/<owner>/<drop-repo> release & workflow-check paths.
    for bad_path in [
        "/user",
        "/graphql",
        "/repos/pctablet505/porchlamp/releases",
        "/repos/pctablet505/porchlamp-engine/releases",
        "/repos/pctablet505/porchlamp-phone/releases",
        "/repos/pctablet505/contributor-value-index/releases",
        "/repos/pctablet505/porchlamp-drop/contents/README.md",
        "/repos/pctablet505/porchlamp-drop/git/refs",
        "/repos/pctablet505/porchlamp-drop/../porchlamp/releases",
        "/repos/pctablet505/porchlamp-drop/%2e%2e/porchlamp/releases",
    ]:
        with pytest.raises(HostRefused):
            rig.gate.urlopen(urllib.request.Request(server.url + bad_path, method="GET"), 5, scope="upload")

    # 5. Upload scope cannot contact non-GitHub hosts (e.g., deps.dev).
    with pytest.raises(HostRefused, match="only contact GitHub"):
        rig.gate.urlopen(urllib.request.Request("https://deps.dev/x", method="GET"), 5, scope="upload")

    assert server.count == 0
    assert CANARY not in "\n".join(rig.lines)



def test_upload_scope_pins_every_drop_box_route_to_the_methods_it_needs(tmp_path, server):
    server.responder = lambda req: (200, {"Content-Type": "application/json"}, b"{}")
    rig = Rig(tmp_path)
    repo = "/repos/pctablet505/porchlamp-drop"
    allowed = [
        ("GET", repo), ("GET", repo + "/contents/.github/workflows"),
        ("GET", repo + "/releases?per_page=20"), ("POST", repo + "/releases"),
        ("PATCH", repo + "/releases/7"), ("POST", repo + "/releases/7/assets?name=a"),
        ("DELETE", repo + "/releases/assets/9"), ("GET", repo + "/releases/tags/cycle-x"),
    ]
    for method, path in allowed:
        body = b"{}" if method in ("POST", "PATCH") else None
        with rig.gate.urlopen(urllib.request.Request(server.url + path, data=body, method=method), 5, scope="upload"):
            pass
    reached = server.count
    assert reached == len(allowed)

    refused = [
        ("PATCH", repo), ("DELETE", repo), ("POST", repo),
        ("POST", repo + "/contents/.github/workflows"), ("DELETE", repo + "/contents/.github/workflows"),
        ("PATCH", repo + "/releases"), ("DELETE", repo + "/releases"),
        ("DELETE", repo + "/releases/7"), ("POST", repo + "/releases/7"),
        ("PATCH", repo + "/releases/7/assets"), ("DELETE", repo + "/releases/7/assets"),
        ("POST", repo + "/releases/assets/9"), ("PATCH", repo + "/releases/assets/9"),
        ("POST", repo + "/releases/tags/cycle-x"), ("DELETE", repo + "/releases/tags/cycle-x"),
    ]
    for method, path in refused:
        body = b"{}" if method in ("POST", "PATCH") else None
        with pytest.raises(HostRefused):
            rig.gate.urlopen(urllib.request.Request(server.url + path, data=body, method=method), 5, scope="upload")
    assert server.count == reached


# ---- D4: several collector threads share one gate ----------------------------------------------------------------
def test_rate_state_survives_overlapping_waits(tmp_path):
    """T8: two threads wait for GitHub at once. When the shorter wait ends, the other is still a wait in progress
    (the engine's watchdog must not take the collector for stuck), and the state names the wait that ends last."""
    clock = [1000.0]
    rig = Rig(tmp_path, clock=lambda: clock[0])
    release = {"graphql": threading.Event(), "core": threading.Event()}
    started = {"graphql": threading.Event(), "core": threading.Event()}

    def waiting(resource: str, seconds: float) -> None:
        def sleep(_s: float) -> None:
            started[resource].set()
            release[resource].wait(10)
        rig.gate.rate_wait(resource, 0, seconds, sleep)

    long = threading.Thread(target=waiting, args=("graphql", 600), daemon=True)
    short = threading.Thread(target=waiting, args=("core", 300), daemon=True)
    long.start()
    assert started["graphql"].wait(5)
    short.start()
    assert started["core"].wait(5)
    assert rig.gate.rate_state() == ("graphql", 1600.0)
    release["core"].set()
    short.join(5)
    assert not short.is_alive()
    assert rig.gate.rate_state() == ("graphql", 1600.0)
    release["graphql"].set()
    long.join(5)
    assert rig.gate.rate_state() is None


def test_cancel_binding_is_adopted_by_another_thread(tmp_path):
    """T9: a worker thread that adopts the caller's cancel binding stops when the caller's collector is cancelled
    (a watchdog restart), at its next request or inside a wait, through the engine's gate and through ``net``."""
    from porchlamp.collect import net

    rig = Rig(tmp_path)
    cancel = threading.Event()
    old = httpgate.install(rig.gate)
    try:
        rig.gate.bind_cancel(cancel)
        binding = httpgate.binding()
        assert binding is cancel and net.binding() is cancel
        outcome: list[str] = []
        adopted = threading.Event()

        def worker(adopt) -> None:
            adopt(binding)
            adopted.set()
            try:
                net.sleep(5)
                outcome.append("slept")
            except Stopped:
                outcome.append("stopped")

        for adopt in (httpgate.bind, net.bind):
            outcome.clear()
            adopted.clear()
            cancel.clear()
            t = threading.Thread(target=worker, args=(adopt,), daemon=True)
            began = time.monotonic()
            t.start()
            assert adopted.wait(5)
            cancel.set()
            t.join(5)
            assert outcome == ["stopped"] and time.monotonic() - began < 2.5
        unbound: list[object] = []
        t = threading.Thread(target=lambda: unbound.append(httpgate.binding()), daemon=True)
        t.start()
        t.join(5)
        assert unbound == [None]                       # a new thread has no binding until it adopts one
    finally:
        rig.gate.bind_cancel(None)
        httpgate.install(old)
