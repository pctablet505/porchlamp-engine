"""Pause and stop act between HTTP requests (within 10 s) even in the middle of a real layer pass.

A real ``scripts/gather.py`` loop over 80 repos runs against a deliberately slow fake GitHub on loopback, through the real
transport and the real gate; the test pauses it, watches the requests stop, resumes it, and stops it.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from phone import entry, httpgate, runner
from phone.entry import Engine
from porchlamp.collect import ghcli
from tests.helpers.fakes import FakeHTTP, loopback_gate_options
from tests.test_name_injection import FAKE_META

pytestmark = pytest.mark.usefixtures("clean_env", "utc")

GH = "ghp_" + "PAUSEtest0123456789abcdefghijklmnopqrs"
LAN = "lan-pause-test-0123456789-abcdefghijklmnop"
WIFI = {"battery_pct": 90, "charging": True, "temp_c": 30.0, "network": "wifi", "wifi_ip": "127.0.0.1",
        "data_saver": False}


def slow_github(req):
    headers = {"x-ratelimit-limit": "5000", "x-ratelimit-remaining": "4000",
               "x-ratelimit-reset": str(int(time.time()) + 3600), "x-ratelimit-resource": "graphql"}
    return 200, headers, json.dumps(FAKE_META)


def test_pause_stops_the_requests_within_ten_seconds_resume_restarts_them_and_stop_ends_the_pass(tmp_path, monkeypatch):
    srv = FakeHTTP(slow_github, delay=0.25)
    eng = Engine(tmp_path / "files", clock=time.time, threads=False, gate_options=loopback_gate_options(),
                 api_base=srv.url, lan_port=0)
    cancel = threading.Event()
    outcome: list = []
    worker: threading.Thread | None = None
    try:
        gather = runner.load_gather(entry.ENGINE_ROOT, eng.home)
        monkeypatch.setattr(gather, "SLEEP_REST", 0.0)
        monkeypatch.setattr(ghcli, "AUTH_BASE", srv.url)
        (eng.home / "raw").mkdir(parents=True, exist_ok=True)
        (eng.home / "raw" / "candidates.jsonl").write_text("".join(
            json.dumps({"owner": f"owner{i}", "name": f"repo{i}", "ecosystem": "pypi"}) + "\n" for i in range(80)))
        eng.start(GH, LAN)
        eng.controls.set_token(True, "accepted", time.time())
        monkeypatch.setenv("GH_TOKEN", GH)
        monkeypatch.setenv("PORCHLAMP_GH_API", srv.url)
        monkeypatch.setenv("PORCHLAMP_ALLOW_TEST_API", "1")
        eng.update_device(json.dumps(WIFI))

        def run_pass():
            eng.gate.bind_cancel(cancel)
            try:
                outcome.append(gather.main(["--all", "--graphql-bulk", "--collectors", "github_meta"]))
            except httpgate.Stopped:
                outcome.append("stopped")

        worker = threading.Thread(target=run_pass, daemon=True)
        worker.start()
        deadline = time.monotonic() + 20
        while srv.count < 5 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert srv.count >= 5, "the pass should be making requests"

        pressed = time.monotonic()
        before = srv.count
        eng.pause()
        last, last_change = srv.count, time.monotonic()
        while time.monotonic() - last_change < 1.5:                       # quiet for a second and a half = stopped
            time.sleep(0.05)
            if srv.count != last:
                last, last_change = srv.count, time.monotonic()
            assert time.monotonic() - pressed < 10, "requests did not stop within 10 s"
        assert last_change - pressed < 10 and last - before <= 2          # only the request already in flight
        assert eng.status() and json.loads(eng.status())["state"] == "paused_by_user"
        time.sleep(3)
        assert srv.count == last and worker.is_alive()                    # nothing leaks out while paused

        eng.resume()
        deadline = time.monotonic() + 8
        while srv.count < last + 3 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert srv.count >= last + 3, "the pass should carry on after Resume"

        eng.gate.stop()
        began = time.monotonic()
        worker.join(10)
        assert not worker.is_alive() and outcome == ["stopped"] and time.monotonic() - began < 10
        settled = srv.count
        time.sleep(1.0)
        assert srv.count == settled                                       # and no request after the stop
    finally:
        cancel.set()
        eng.gate.stop()
        if worker is not None:
            worker.join(5)
        srv.close()
