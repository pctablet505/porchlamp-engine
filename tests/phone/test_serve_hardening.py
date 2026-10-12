"""phone/serve.py hardening over REAL loopback sockets: signed requests, replay, tampering, writes, availability."""

from __future__ import annotations

import http.client
import inspect
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from phone import serve
from phone.auth import FailureThrottle, sign_request, verify_response
from phone.serve import CANDIDATES_CAP, PhoneHandler, TokenError, make_server
from tests.phone import DUMMY_TOKEN, make_home

REPO = Path(__file__).resolve().parents[2]
ROW = b'{"owner": "octo", "name": "cat.js", "ecosystem": "npm"}\n'
CAND = "/config/candidates.jsonl"
INJECTION = ('a", name: "b") { object(expression: "HEAD:README.md") { ... on Blob { text } } } '
             'r2: repository(owner: "c')


def _start(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _stop(server):
    server.shutdown()
    server.server_close()


@pytest.fixture
def served(tmp_path):
    home = make_home(tmp_path / "home", layers=("L1", "L0"), files_per=5)
    server = _start(make_server(home, "127.0.0.1", 0))
    yield server
    _stop(server)


def send(server, method, target, body=b"", headers=None, token=DUMMY_TOKEN, source=None, sign_target=None,
         sign_body=None):
    """One request on a fresh connection; signed with ``token`` unless ``headers`` is given. Returns (status, headers, body)."""
    if headers is None:
        headers = sign_request(token, method, sign_target or target, body if sign_body is None else sign_body)
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=20,
                                      source_address=(source, 0) if source else None)
    try:
        conn.request(method, target, body=body or None, headers=headers)
        resp = conn.getresponse()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
    finally:
        conn.close()


def _wait(predicate, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# ---- A. signed requests -------------------------------------------------------------------------------------------
def test_valid_signed_get_is_200_and_json_replies_carry_a_verifiable_mac(served):
    for target in ("/status.json", "/manifest.json"):
        headers = sign_request(DUMMY_TOKEN, "GET", target, b"")
        status, resp, body = send(served, "GET", target, headers=headers)
        assert status == 200, target
        assert verify_response(DUMMY_TOKEN, headers["X-Porch-Nonce"], 200, body, resp["x-porch-mac"]) is True
        # The MAC is bound to this request's nonce, this status and these bytes.
        assert verify_response(DUMMY_TOKEN, "f" * 32, 200, body, resp["x-porch-mac"]) is False
        assert verify_response(DUMMY_TOKEN, headers["X-Porch-Nonce"], 200, body + b" ", resp["x-porch-mac"]) is False
        assert verify_response("z" * 32, headers["X-Porch-Nonce"], 200, body, resp["x-porch-mac"]) is False
    assert json.loads(body)["layers"]


def test_bearer_is_rejected_even_with_the_right_token(served):
    status, resp, body = send(served, "GET", "/status.json", headers={"Authorization": f"Bearer {DUMMY_TOKEN}"})
    assert (status, body) == (401, b"")
    both = {**sign_request(DUMMY_TOKEN, "GET", "/status.json", b""), "Authorization": f"Bearer {DUMMY_TOKEN}"}
    assert send(served, "GET", "/status.json", headers=both)[0] == 401


def test_wrong_token_is_401(served):
    assert send(served, "GET", "/status.json", token="w" * 32)[:1] == (401,)


@pytest.mark.parametrize("skew", [-121, 121, -3600, 3600])
def test_stale_or_future_timestamp_is_401(served, skew):
    now = int(time.time())
    headers = sign_request(DUMMY_TOKEN, "GET", "/status.json", b"", ts=now + skew)
    assert send(served, "GET", "/status.json", headers=headers)[::2] == (401, b"")
    # The same request stamped now would have been fine: only the timestamp was wrong.
    assert send(served, "GET", "/status.json")[0] == 200


def test_replayed_request_is_401_the_second_time(served):
    headers = sign_request(DUMMY_TOKEN, "GET", "/status.json", b"")
    assert send(served, "GET", "/status.json", headers=headers)[0] == 200
    assert send(served, "GET", "/status.json", headers=headers)[::2] == (401, b"")
    assert len(served.nonces) == 1


def test_a_failed_request_does_not_burn_its_nonce(served):
    headers = sign_request(DUMMY_TOKEN, "GET", "/status.json", b"")
    assert send(served, "GET", "/manifest.json", headers=headers)[0] == 401  # wrong target for this MAC
    assert send(served, "GET", "/status.json", headers=headers)[0] == 200


def test_nonce_store_on_the_server_is_bounded(served):
    for i in range(10_000):
        served.nonces.add_if_new(f"{i:032x}", time.time())
    assert len(served.nonces) == 10_000
    assert send(served, "GET", "/status.json")[0] == 401  # fails closed rather than forgetting a live nonce
    assert len(served.nonces) == 10_000


def test_tampered_body_is_401_and_writes_nothing(served):
    target = served.home / "raw" / "candidates.jsonl"
    evil = b'{"owner": "evil", "name": "x", "ecosystem": "npm"}\n'
    status, _, body = send(served, "PUT", CAND, body=evil, sign_body=ROW)
    assert (status, body) == (401, b"")
    assert not target.exists()
    assert send(served, "PUT", CAND, body=ROW)[0] == 204 and target.read_bytes() == ROW


def test_tampered_target_is_401(served):
    for sent in ("/manifest.json", "/status.json?x=1", "/status.json/", "//status.json", "/archive/L1.tar.gz"):
        status, _, body = send(served, "GET", sent, sign_target="/status.json")
        assert (status, body) == (401, b""), sent
    assert send(served, "GET", "/status.json?x=1", sign_target="/status.json?x=1")[0] == 200
    assert send(served, "PUT", CAND + "?v=2", body=ROW, sign_target=CAND)[0] == 401


def test_missing_or_malformed_headers_are_401(served):
    good = sign_request(DUMMY_TOKEN, "GET", "/status.json", b"")
    cases = [{}, {k: v for k, v in good.items() if k != "X-Porch-Ts"}, {k: v for k, v in good.items() if k != "X-Porch-Nonce"},
             {k: v for k, v in good.items() if k != "Authorization"},
             {**good, "X-Porch-Nonce": good["X-Porch-Nonce"].upper()}, {**good, "X-Porch-Nonce": "abc"},
             {**good, "X-Porch-Ts": "yesterday"}, {**good, "Authorization": "Porch-HMAC-SHA256 zz"}]
    for headers in cases:
        assert send(served, "GET", "/status.json", headers=headers)[::2] == (401, b""), headers


def test_every_authentication_failure_looks_the_same(served, capfd):
    good = sign_request(DUMMY_TOKEN, "GET", "/status.json", b"")
    send(served, "GET", "/status.json", headers=good)  # now `good` is a replay
    variants = [
        good, {}, {"Authorization": f"Bearer {DUMMY_TOKEN}"}, sign_request("w" * 32, "GET", "/status.json", b""),
        sign_request(DUMMY_TOKEN, "GET", "/status.json", b"", ts=int(time.time()) - 500),
        {**sign_request(DUMMY_TOKEN, "GET", "/status.json", b""), "X-Porch-Nonce": "short"},
    ]
    shapes = set()
    for headers in variants:
        status, resp, body = send(served, "GET", "/status.json", headers=headers)
        shapes.add((status, body, tuple(sorted(k for k in resp if k != "date"))))
    assert len(shapes) == 1 and next(iter(shapes))[0] == 401
    err = capfd.readouterr().err
    assert DUMMY_TOKEN not in err and good["Authorization"].split()[1] not in err and good["X-Porch-Nonce"] not in err
    assert "auth-fail" in err


# ---- B. writes ---------------------------------------------------------------------------------------------------
def test_put_github_token_is_404_and_writes_nothing(served):
    before = sorted(p.relative_to(served.home).as_posix() for p in served.home.rglob("*"))
    assert send(served, "PUT", "/config/github_token", body=b"g" * 40)[::2] == (404, b"")
    assert not (served.home / "config" / "github_token").exists()
    assert sorted(p.relative_to(served.home).as_posix() for p in served.home.rglob("*")) == before
    # An unsigned caller learns nothing about the route either.
    assert send(served, "PUT", "/config/github_token", body=b"g", headers={})[0] == 401


def _row(**fields):
    base = {"owner": "octo", "name": "cat", "ecosystem": "npm"}
    base.update(fields)
    return (json.dumps(base) + "\n").encode()


@pytest.mark.parametrize("label,body", [
    ("not json", b"this is not json\n"),
    ("one bad line among good", ROW * 3 + b"{broken\n" + ROW),
    ("truncated", ROW[:-5]),
    ("json array", b'["octo", "cat", "npm"]\n'),
    ("json scalar", b"42\n"),
    ("json null", b"null\n"),
    ("missing owner", b'{"name": "cat", "ecosystem": "npm"}\n'),
    ("missing name", b'{"owner": "o", "ecosystem": "npm"}\n'),
    ("missing ecosystem", b'{"owner": "o", "name": "n"}\n'),
    ("number field", _row(owner=7)),
    ("null field", _row(name=None)),
    ("list field", _row(ecosystem=["npm"])),
    ("space in value", _row(owner="oc to")),
    ("slash in value", _row(name="a/b")),
    ("newline in value", _row(name="ab\n")),
    ("unicode in value", _row(owner="octö")),
    ("semicolon", _row(ecosystem="npm;rm")),
    ("empty value", _row(owner="")),
    ("101 characters", _row(name="a" * 101)),
    ("invalid utf-8", b'{"owner": "\xff", "name": "n", "ecosystem": "npm"}\n'),
    ("deeply nested", b"[" * 100_000 + b"]" * 100_000 + b"\n"),
    ("20001 lines", ROW * 20_001),
    ("graphql injection", json.dumps({"owner": INJECTION, "name": "b", "ecosystem": "npm"}).encode() + b"\n"),
    ("dot-dot owner", _row(owner="..")),
    ("dot name", _row(name=".")),
    ("path traversal name", _row(name="../../../../user/repos?x")),
    ("injection in ecosystem", _row(ecosystem=INJECTION)),
])
def test_candidates_validation_rejects_and_writes_nothing(served, label, body):
    target = served.home / "raw" / "candidates.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"PREVIOUS\n")
    status, _, reply = send(served, "PUT", CAND, body=body)
    assert (status, reply) == (400, b""), label
    assert target.read_bytes() == b"PREVIOUS\n"
    assert not (target.parent / "candidates.jsonl.tmp").exists()


def test_candidates_validation_accepts_the_boundaries(served):
    target = served.home / "raw" / "candidates.jsonl"
    for label, body in (("20000 lines", ROW * 20_000), ("blank lines and no final newline", b"\n" + ROW + b"\n\n" + ROW.rstrip()),
                        ("100 characters", _row(name="a" * 100)), ("allowed punctuation", _row(owner="A.b_c-9")),
                        ("empty file", b"")):
        assert send(served, "PUT", CAND, body=body)[0] == 204, label
        assert target.read_bytes() == body


def test_candidates_over_8_mib_by_one_byte_is_rejected_unread(served):
    target = served.home / "raw" / "candidates.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"PREVIOUS\n")
    conn = http.client.HTTPConnection("127.0.0.1", served.server_address[1], timeout=20)
    conn.putrequest("PUT", CAND)
    for key, value in sign_request(DUMMY_TOKEN, "PUT", CAND, b"x").items():
        conn.putheader(key, value)
    conn.putheader("Content-Length", str(CANDIDATES_CAP + 1))
    conn.endheaders()  # no body is sent: the server must answer from the headers alone
    assert conn.getresponse().status == 413
    conn.close()
    # An unsigned caller with the same oversized header gets the plain 401.
    conn = http.client.HTTPConnection("127.0.0.1", served.server_address[1], timeout=20)
    conn.putrequest("PUT", CAND)
    conn.putheader("Content-Length", str(CANDIDATES_CAP + 1))
    conn.endheaders()
    assert conn.getresponse().status == 401
    conn.close()
    assert target.read_bytes() == b"PREVIOUS\n"


# ---- C. availability ---------------------------------------------------------------------------------------------
def test_handler_has_a_20_second_socket_timeout():
    assert PhoneHandler.timeout == 20


def test_a_stalled_client_is_disconnected_within_the_timeout(served):
    sock = socket.create_connection(("127.0.0.1", served.server_address[1]))
    sock.settimeout(40)
    sock.sendall(b"GET /status.json HTT")  # half a request line, then silence
    started = time.monotonic()
    try:
        assert sock.recv(4096) == b""  # the server closed without answering
    finally:
        sock.close()
    assert time.monotonic() - started < 25


def test_a_client_that_stalls_in_the_body_is_dropped_and_nothing_is_written(served, monkeypatch):
    monkeypatch.setattr(PhoneHandler, "timeout", 1)
    headers = sign_request(DUMMY_TOKEN, "PUT", CAND, ROW)
    sock = socket.create_connection(("127.0.0.1", served.server_address[1]))
    sock.settimeout(10)
    head = f"PUT {CAND} HTTP/1.1\r\nHost: x\r\nContent-Length: {len(ROW)}\r\n" + "".join(
        f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n"
    sock.sendall(head.encode() + ROW[:10])
    try:
        assert sock.recv(4096) == b""
    finally:
        sock.close()
    assert not (served.home / "raw" / "candidates.jsonl").exists()


def test_the_ninth_concurrent_connection_gets_503(served, monkeypatch):
    monkeypatch.setattr(PhoneHandler, "timeout", 15)
    holders = []
    try:
        for _ in range(8):
            holders.append(socket.create_connection(("127.0.0.1", served.server_address[1])))
        assert _wait(lambda: served.active == 8)
        ninth = socket.create_connection(("127.0.0.1", served.server_address[1]))
        ninth.settimeout(5)
        data = b""
        while True:
            chunk = ninth.recv(4096)
            if not chunk:
                break
            data += chunk
        ninth.close()
        assert data.startswith(b"HTTP/1.0 503 "), data
        assert served.active == 8
        # A freed slot is reusable: close one holder and a signed request goes through.
        holders.pop().close()
        assert _wait(lambda: served.active == 7)
        assert send(served, "GET", "/status.json")[0] == 200
    finally:
        for sock in holders:
            sock.close()


def test_ten_failures_from_one_ip_lock_it_out_with_429_and_no_mac_work(served, monkeypatch):
    calls = []
    real = serve.check_request

    def spy(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(serve, "check_request", spy)
    for _ in range(10):
        assert send(served, "GET", "/status.json", token="w" * 32)[0] == 401
    assert len(calls) == 10
    for headers in (None, {}):  # even a perfectly signed request is refused while locked out
        status, resp, body = send(served, "GET", "/status.json", headers=headers)
        assert (status, body) == (429, b"")
        assert resp["retry-after"] == "300"
    assert len(calls) == 10  # no MAC was computed for the locked-out client
    # A put in the lockout writes nothing either.
    assert send(served, "PUT", CAND, body=ROW)[0] == 429
    assert not (served.home / "raw" / "candidates.jsonl").exists()


def test_a_good_client_on_another_address_is_unaffected_by_a_lockout(served):
    for _ in range(10):
        send(served, "GET", "/status.json", token="w" * 32, source="127.0.0.1")
    assert send(served, "GET", "/status.json", source="127.0.0.1")[0] == 429
    assert send(served, "GET", "/status.json", source="127.0.0.2")[0] == 200
    assert send(served, "GET", "/status.json", source="127.0.0.3", token="w" * 32)[0] == 401


def test_nine_failures_do_not_lock_and_lockout_expires_with_the_clock(served):
    clock = [5000.0]
    served.throttle = FailureThrottle(clock=lambda: clock[0])
    for _ in range(9):
        assert send(served, "GET", "/status.json", token="w" * 32)[0] == 401
    assert send(served, "GET", "/status.json")[0] == 200  # the 10th request is good: no lockout yet
    assert send(served, "GET", "/status.json", token="w" * 32)[0] == 401  # the 10th failure
    assert send(served, "GET", "/status.json")[0] == 429
    clock[0] += 299
    assert send(served, "GET", "/status.json")[0] == 429
    clock[0] += 2
    assert send(served, "GET", "/status.json")[0] == 200


# ---- D. defaults -------------------------------------------------------------------------------------------------
def test_default_bind_is_loopback_everywhere():
    assert inspect.signature(make_server).parameters["bind"].default == "127.0.0.1"
    assert serve.build_parser().parse_args(["--home", "h"]).bind == "127.0.0.1"


def test_python_m_phone_serve_listens_on_loopback_only(tmp_path):
    home = make_home(tmp_path / "home", layers=("L1",), files_per=1)
    proc = subprocess.Popen([sys.executable, "-m", "phone.serve", "--home", str(home), "--port", "0"], cwd=REPO,
                            stderr=subprocess.PIPE, text=True)
    try:
        line = proc.stderr.readline()
        assert " on 127.0.0.1:" in line, line
        port = int(line.rsplit(":", 1)[1])
        with socket.create_connection(("127.0.0.1", port), timeout=5):
            pass
        with pytest.raises(OSError):  # a server bound to 0.0.0.0 would accept this one too
            socket.create_connection(("127.0.0.2", port), timeout=5)
    finally:
        proc.terminate()
        proc.wait(timeout=20)
        proc.stderr.close()


def test_make_server_with_a_token_argument_does_not_read_the_token_file(tmp_path):
    home = make_home(tmp_path / "home", layers=("L1",), files_per=1, token="f" * 32)  # the file holds another token
    token_file = home / "config" / "lan_token"
    token_file.unlink()
    token_file.mkdir()  # reading it as a file would raise IsADirectoryError
    server = _start(make_server(home, "127.0.0.1", 0, token="g" * 32))
    try:
        assert send(server, "GET", "/status.json", token="g" * 32)[0] == 200
        assert send(server, "GET", "/status.json", token="f" * 32)[0] == 401
    finally:
        _stop(server)
    with pytest.raises(TokenError):
        make_server(home, "127.0.0.1", 0, token="short")
    token_file.rmdir()
    token_file.write_text("f" * 23 + "\n", encoding="utf-8")
    with pytest.raises(TokenError):  # without the argument the file is read, as before (24-character minimum)
        make_server(home, "127.0.0.1", 0)
    token_file.write_text("f" * 24 + "\n", encoding="utf-8")
    server = make_server(home, "127.0.0.1", 0)
    assert server.token == "f" * 24
    server.server_close()


# ---- J. logging ---------------------------------------------------------------------------------------------------
def test_the_log_has_method_and_status_only_never_the_path_or_query(served, capfd):
    canary = "CANARY-path-9f8e7d"
    send(served, "GET", f"/{canary}/status.json?q={canary}-query")  # authenticated 404
    send(served, "GET", f"/{canary}/x?token={canary}", headers={})  # unauthenticated 401
    send(served, "PUT", f"/config/{canary}", body=b"x")
    err = capfd.readouterr().err
    assert canary not in err and "token=" not in err
    lines = [line for line in err.splitlines() if line and not line.startswith("auth-fail")]
    assert lines and all(line.split()[0] in ("GET", "PUT") and line.split()[1].isdigit() and len(line.split()) == 2
                         for line in lines), lines
    assert "GET 404" in lines and "GET 401" in lines and "PUT 404" in lines


def test_a_timed_out_client_logs_without_raising_attribute_error(served, monkeypatch, capfd):
    monkeypatch.setattr(PhoneHandler, "timeout", 1)
    sock = socket.create_connection(("127.0.0.1", served.server_address[1]))
    sock.settimeout(10)
    sock.sendall(b"GET /status.json HTT")
    try:
        assert sock.recv(4096) == b""
    finally:
        sock.close()
    time.sleep(0.2)
    err = capfd.readouterr().err
    assert "AttributeError" not in err and "Traceback" not in err
