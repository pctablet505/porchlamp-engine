"""serve.py additions: GET /log.txt, and the bytes each connection moves reported to the engine."""

from __future__ import annotations

import socket
import threading
import time

import pytest

from phone import serve
from phone.auth import MAC_HEADER, NONCE_HEADER, sign_request, verify_response
from tests.phone import DUMMY_TOKEN, make_home

LOG_TEXT = "2026-10-11 10:00:00 L1: COMPLETE\n2026-10-11 10:00:01 Idle, next cycle due 2026-11-08\n"


@pytest.fixture
def served(tmp_path):
    home = make_home(tmp_path / "home", layers=("L1",), files_per=40)
    transfers: list[tuple] = []
    server = serve.make_server(home, "127.0.0.1", 0, on_transfer=lambda *a: transfers.append(a),
                               log_text=lambda: LOG_TEXT)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    server.transfers = transfers
    yield server
    server.shutdown()
    server.server_close()


def raw_request(server, method: str, target: str, headers: dict[str, str] | None = None) -> bytes:
    """One request over a bare socket; returns everything the server sent until it closed."""
    lines = [f"{method} {target} HTTP/1.1", "Host: 127.0.0.1", "Connection: close"]
    lines += [f"{k}: {v}" for k, v in (headers or {}).items()]
    request = ("\r\n".join(lines) + "\r\n\r\n").encode()
    with socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=10) as sock:
        sock.sendall(request)
        chunks = []
        while True:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)
    raw_request.last_request_len = len(request)
    return b"".join(chunks)


def wait_for(predicate, seconds: float = 3.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def split(response: bytes) -> tuple[int, dict[str, str], bytes]:
    head, _, body = response.partition(b"\r\n\r\n")
    status_line, *header_lines = head.decode().split("\r\n")
    headers = {k.lower(): v for k, _, v in (ln.partition(": ") for ln in header_lines)}
    return int(status_line.split()[1]), headers, body


def test_log_txt_serves_the_same_text_signed_like_everything_else(served):
    signed = sign_request(DUMMY_TOKEN, "GET", "/log.txt", b"")
    status, headers, body = split(raw_request(served, "GET", "/log.txt", signed))
    assert status == 200 and body.decode() == LOG_TEXT
    assert headers["content-type"].startswith("text/plain")
    assert verify_response(DUMMY_TOKEN, signed[NONCE_HEADER], 200, body, headers[MAC_HEADER.lower()])
    assert not verify_response(DUMMY_TOKEN, signed[NONCE_HEADER], 200, body + b"x", headers[MAC_HEADER.lower()])


def test_log_txt_is_refused_without_a_valid_signature_and_leaks_nothing(served):
    status, _, body = split(raw_request(served, "GET", "/log.txt"))
    assert status == 401 and body == b""
    wrong = sign_request("w" * 32, "GET", "/log.txt", b"")
    assert split(raw_request(served, "GET", "/log.txt", wrong))[0] == 401


def test_log_txt_is_a_404_when_the_engine_gave_no_log(tmp_path):
    home = make_home(tmp_path / "home", layers=("L1",), files_per=1)
    server = serve.make_server(home, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        assert split(raw_request(server, "GET", "/log.txt", sign_request(DUMMY_TOKEN, "GET", "/log.txt", b"")))[0] == 404
    finally:
        server.shutdown()
        server.server_close()


def test_a_transfer_is_reported_with_the_exact_bytes_each_way_and_marked_fetched(served):
    signed = sign_request(DUMMY_TOKEN, "GET", "/archive/L1.tar.gz", b"")
    response = raw_request(served, "GET", "/archive/L1.tar.gz", signed)
    status, _, body = split(response)
    assert status == 200 and len(body) > 100
    assert wait_for(lambda: served.transfers)
    ip, sent, received, fetched = served.transfers[0]
    assert ip == "127.0.0.1" and fetched is True
    assert sent == len(response)                           # every byte the phone wrote, headers and archive
    assert received == raw_request.last_request_len        # every byte of the request it read


def test_a_refused_request_is_still_counted_but_is_not_a_fetch(served):
    response = raw_request(served, "GET", "/archive/L1.tar.gz")
    assert split(response)[0] == 401
    assert wait_for(lambda: served.transfers)
    ip, sent, received, fetched = served.transfers[0]
    assert ip == "127.0.0.1" and fetched is False and sent == len(response) and received > 0


def test_a_connection_that_sends_no_request_is_not_reported(served):
    with socket.create_connection(("127.0.0.1", served.server_address[1]), timeout=5):
        pass
    time.sleep(0.4)
    assert served.transfers == []


def test_the_command_line_server_still_binds_loopback_unless_told_otherwise():
    assert serve.build_parser().parse_args(["--home", "x"]).bind == "127.0.0.1"
