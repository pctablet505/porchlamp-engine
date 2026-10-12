"""A whole run with made-up canary tokens: the real gatherer, the real transport, the real gate, the real LAN server.

Afterwards the canaries must be in NO status, event, log, export, archive, served file, stderr or exception text. The
fake GitHub even echoes the Authorization header back in its 403 messages, so the one place a token could slip into text
(a server that repeats it) is exercised on purpose.
"""

from __future__ import annotations

import io
import json
import socket
import tarfile
import time
import urllib.request
from pathlib import Path

import pytest

from phone import entry, runner
from phone.auth import sign_request
from phone.entry import Engine
from porchlamp.collect import ghcli
from tests.helpers.fakes import FakeHTTP, github_responder, loopback_gate_options
from tests.helpers.rig import leaks
from tests.test_name_injection import FAKE_META

pytestmark = pytest.mark.usefixtures("clean_env", "utc")

GH_CANARY = "ghp_" + "CANARYfullrun0123456789abcdefghijklmnop"
LAN_CANARY = "lan-canary-secret-0123456789-abcdefghijklmnop"        # no token shape: only exact redaction can hide it
OTHER_CANARY = "ghp_" + "CANARYother9876543210zyxwvutsrqponm"
LOOPBACK = {"battery_pct": 82, "charging": True, "temp_c": 33.0, "network": "wifi", "wifi_ip": "127.0.0.1",
            "data_saver": False}


def wait_until(predicate, seconds: float = 60.0, step: float = 0.1) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(step)
    return False


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def echoing_github():
    ok = github_responder(scopes="", private=[], remaining=4000)

    def respond(req):
        path = req.path.split("?")[0]
        if path in ("/user", "/user/repos", "/rate_limit"):
            return ok(req)
        headers = {"x-ratelimit-limit": "5000", "x-ratelimit-remaining": "4000",
                   "x-ratelimit-reset": str(int(time.time()) + 3600)}
        if path == "/graphql":
            return 200, headers, json.dumps(FAKE_META)
        return 403, headers, json.dumps({"message": "denied for " + req.headers.get("authorization", "")})

    return respond


def signed_get(port: int, token: str, path: str) -> bytes:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=sign_request(token, "GET", path, b""))
    with urllib.request.urlopen(request, timeout=20) as resp:
        return resp.read()


def test_the_scanner_itself_finds_a_planted_canary_in_a_file_and_inside_an_archive(tmp_path):
    (tmp_path / "plain.txt").write_text(f"hello {GH_CANARY}")
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as tar:
        data = f"inside {LAN_CANARY}".encode()
        info = tarfile.TarInfo("raw/x/y.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    (tmp_path / "archives").mkdir()
    (tmp_path / "archives" / "L0.tar.gz").write_bytes(raw.getvalue())
    (tmp_path / "clean.txt").write_text("nothing here")
    found = leaks(tmp_path, [GH_CANARY, LAN_CANARY])
    assert str(tmp_path / "plain.txt") in found
    assert f"{tmp_path / 'archives' / 'L0.tar.gz'}:raw/x/y.json" in found
    assert str(tmp_path / "clean.txt") not in found and leaks(tmp_path / "archives", ["not present"]) == []


def test_canary_tokens_appear_nowhere_after_a_full_run(tmp_path, monkeypatch, capfd):
    srv = FakeHTTP(echoing_github())
    port = free_port()
    eng = Engine(tmp_path / "files", clock=time.time, gate_options=loopback_gate_options(), api_base=srv.url,
                 tick_s=0.2, lan_port=port)
    try:
        gather = runner.load_gather(entry.ENGINE_ROOT, eng.home)          # the real scripts/gather.py
        for name in ("SLEEP_REST", "SLEEP_KEYLESS", "SLEEP_PYPISTATS"):
            monkeypatch.setattr(gather, name, 0.0)
        monkeypatch.setattr(ghcli, "AUTH_BASE", srv.url)                  # the fake stands in for api.github.com
        (eng.home / "raw").mkdir(parents=True, exist_ok=True)
        (eng.home / "raw" / "candidates.jsonl").write_text("".join(
            json.dumps({"owner": o, "name": n, "ecosystem": "pypi"}) + "\n" for o, n in (("pallets", "flask"),
                                                                                         ("psf", "requests"))))
        eng.settings.set({"layers": ["L0"], "lan_mode": "auto"})
        eng.start(GH_CANARY, LAN_CANARY)
        monkeypatch.setenv("PORCHLAMP_GH_API", srv.url)                   # test hooks, set after start() cleared them
        monkeypatch.setenv("PORCHLAMP_ALLOW_TEST_API", "1")
        eng.update_device(json.dumps(LOOPBACK))
        assert wait_until(lambda: eng.controls.token_state == "accepted", 20)
        assert wait_until(lambda: (eng.home / "state/refresh/L0.complete").is_file(), 90), eng.techlog.read_text()[-2000:]
        assert eng.lan.serving and eng.lan.address == f"127.0.0.1:{port}"

        # the positive control: the fake really repeats the credential in its error text, and the transport hands
        # that text on -- so only the engine's own redaction can be keeping it out of everything below
        echoed = ghcli.run(["api", "repos/pallets/flask/pulls"])
        assert GH_CANARY in echoed.stderr
        eng.event("err", "a caller put the raw text in an event", echoed.stderr)
        eng.log(echoed.stderr)

        outputs = {
            "status": eng.status(),
            "settings": json.dumps(eng.settings.get()),
            "events": eng.events(0, 500),
            "self_check": eng.self_check(),
            "test_token (the token in use)": eng.test_token(GH_CANARY),
            "test_token (another token)": eng.test_token(OTHER_CANARY),
            "danger (wrong confirm)": eng.danger("reset", GH_CANARY),
            "export": Path(eng.export_log()).read_text(),
            "served status.json": signed_get(port, LAN_CANARY, "/status.json").decode(),
            "served manifest.json": signed_get(port, LAN_CANARY, "/manifest.json").decode(),
            "served log.txt": signed_get(port, LAN_CANARY, "/log.txt").decode(),
        }
        archive = signed_get(port, LAN_CANARY, "/archive/L0.tar.gz")
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            outputs["served archive"] = "\n".join(
                tar.extractfile(m).read().decode("utf-8", "replace") for m in tar.getmembers() if m.isfile())
        seen = capfd.readouterr()
        outputs["stdout"], outputs["stderr"] = seen.out, seen.err
        for where, text in outputs.items():
            for canary in (GH_CANARY, OTHER_CANARY, LAN_CANARY):
                assert canary not in text, f"{canary} leaked into {where}"
        assert "[redacted]" in outputs["served log.txt"] and "[redacted]" in outputs["export"]
        assert outputs["served log.txt"] == eng.techlog.read_text()       # the same text as the export
        assert json.loads(outputs["test_token (the token in use)"])["state"] == "accepted"
        assert json.loads(outputs["served status.json"])["lan_token_set"] is True
        assert "Authorization" not in json.dumps(json.loads(outputs["status"]))
        assert any(header.startswith("Bearer ") for r in srv.seen if (header := r.headers.get("authorization", "")))
    finally:
        eng.stop()
        srv.close()
    assert leaks(eng.files_dir, [GH_CANARY, OTHER_CANARY, LAN_CANARY]) == []     # every file, archives opened too
    envelopes = list((eng.home / "raw").rglob("*.json"))
    assert envelopes, "the run produced envelopes"                          # the scan above had real data to look at
