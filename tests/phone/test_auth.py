"""phone/auth.py: request signing, the nonce store, reply MACs and the failed-login throttle (no sockets)."""

from __future__ import annotations

import hashlib
import hmac
import re

import pytest

from phone import auth
from phone.auth import (FailureThrottle, NonceStore, check_request, headers_look_signed, request_mac, response_mac,
                        sign_request, verify_request, verify_response)

TOKEN = "k" * 32
NOW = 1_800_000_000
NONCE = "0123456789abcdef0123456789abcdef"
BODY = b'{"owner": "a", "name": "b", "ecosystem": "npm"}\n'


def _signed(method="GET", target="/status.json", body=b"", ts=NOW, nonce=NONCE, token=TOKEN):
    return sign_request(token, method, target, body, ts=ts, nonce=nonce)


def _ok(headers, method="GET", target="/status.json", body=b"", now=NOW, seen=None, token=TOKEN):
    return verify_request(token, method, target, body, headers, now, NonceStore() if seen is None else seen)


def test_mac_matches_the_published_formula_computed_independently():
    body = b"hello body"
    headers = sign_request(TOKEN, "PUT", "/config/candidates.jsonl?x=1", body, ts=NOW, nonce=NONCE)
    message = f"PUT\n/config/candidates.jsonl?x=1\n{NOW}\n{NONCE}\n{hashlib.sha256(body).hexdigest()}"
    expected = hmac.new(TOKEN.encode(), message.encode(), hashlib.sha256).hexdigest()
    assert headers == {"X-Porch-Ts": str(NOW), "X-Porch-Nonce": NONCE, "Authorization": f"Porch-HMAC-SHA256 {expected}"}
    assert request_mac(TOKEN, "PUT", "/config/candidates.jsonl?x=1", body, NOW, NONCE) == expected


def test_default_timestamp_and_nonce_are_integer_seconds_and_32_lower_hex():
    seen = set()
    for _ in range(50):
        headers = sign_request(TOKEN, "GET", "/x", b"")
        assert re.fullmatch(r"[0-9]{10}", headers["X-Porch-Ts"])
        assert re.fullmatch(r"[0-9a-f]{32}", headers["X-Porch-Nonce"])
        assert re.fullmatch(r"Porch-HMAC-SHA256 [0-9a-f]{64}", headers["Authorization"])
        seen.add(headers["X-Porch-Nonce"])
    assert len(seen) == 50


def test_a_genuine_request_verifies_and_the_token_is_not_in_the_headers():
    headers = _signed()
    assert _ok(headers) is True
    assert TOKEN not in "".join(headers.values())


def test_wrong_token_method_target_or_body_is_rejected():
    assert _ok(_signed(token="z" * 32)) is False
    assert _ok(_signed(), method="PUT") is False
    for target in ("/manifest.json", "/status.json?a=1", "/status.json/", "/STATUS.json"):
        assert _ok(_signed(), target=target) is False, target
    assert _ok(_signed("PUT", "/p", BODY), method="PUT", target="/p", body=BODY + b" ") is False
    assert _ok(_signed("PUT", "/p", BODY), method="PUT", target="/p", body=b"") is False
    assert _ok(_signed("PUT", "/p", BODY), method="PUT", target="/p", body=BODY) is True


def test_body_hash_is_part_of_the_mac():
    a = request_mac(TOKEN, "PUT", "/p", b"one", NOW, NONCE)
    b = request_mac(TOKEN, "PUT", "/p", b"two", NOW, NONCE)
    assert a != b


def test_target_is_part_of_the_mac():
    assert request_mac(TOKEN, "GET", "/a", b"", NOW, NONCE) != request_mac(TOKEN, "GET", "/b", b"", NOW, NONCE)


def test_the_old_bearer_header_is_rejected():
    for headers in ({"Authorization": f"Bearer {TOKEN}"},
                    {**_signed(), "Authorization": f"Bearer {TOKEN}"},
                    {"Authorization": f"Bearer {TOKEN}", "X-Porch-Ts": str(NOW), "X-Porch-Nonce": NONCE}):
        assert _ok(headers) is False
    mac = _signed()["Authorization"].split()[1]
    assert _ok({**_signed(), "Authorization": f"Bearer {mac}"}) is False


@pytest.mark.parametrize("skew,accepted", [(0, True), (120, True), (-120, True), (121, False), (-121, False),
                                           (86400, False), (-86400, False), (10**9, False)])
def test_timestamp_window_is_120_seconds_either_way(skew, accepted):
    headers = _signed(ts=NOW + skew)  # a request stamped `skew` seconds from the server's clock
    assert _ok(headers) is accepted
    assert check_request(TOKEN, "GET", "/status.json", b"", _signed(ts=NOW + skew, nonce="a" * 32), NOW,
                         NonceStore()) == ("ok" if accepted else "stale")


def test_replayed_nonce_is_rejected_and_a_fresh_one_is_accepted():
    seen = NonceStore()
    headers = _signed()
    assert _ok(headers, seen=seen) is True
    assert NONCE in seen
    assert _ok(headers, seen=seen) is False
    assert check_request(TOKEN, "GET", "/status.json", b"", headers, NOW, seen) == "replay"
    assert _ok(_signed(nonce="f" * 32), seen=seen) is True


def test_a_nonce_is_recorded_only_after_a_successful_check():
    seen = NonceStore()
    bad = {**_signed(), "Authorization": "Porch-HMAC-SHA256 " + "0" * 64}
    assert _ok(bad, seen=seen) is False
    assert NONCE not in seen and len(seen) == 0
    stale = _signed(ts=NOW - 500)
    assert _ok(stale, seen=seen) is False and len(seen) == 0
    assert _ok(_signed(), seen=seen) is True  # the genuine request still works: the bad one did not burn its nonce


def test_nonce_store_is_bounded_and_fails_closed_when_full():
    seen = NonceStore()
    assert seen.cap == 10_000 and seen.ttl == 300
    for i in range(10_000):
        assert seen.add_if_new(f"{i:032x}", NOW) is True
    assert len(seen) == 10_000
    assert seen.add_if_new("f" * 32, NOW + 1) is False  # full of live entries: refuse, never evict a live nonce
    assert len(seen) == 10_000
    assert seen.add_if_new(f"{0:032x}", NOW + 1) is False  # still remembered, so still a replay
    assert _ok(_signed(nonce="e" * 32), seen=seen) is False


def test_nonce_entries_are_kept_for_300_seconds_then_forgotten():
    seen = NonceStore()
    assert seen.add_if_new(NONCE, NOW)
    assert seen.add_if_new(NONCE, NOW + 299) is False
    assert seen.add_if_new(NONCE, NOW + 301) is True
    full = NonceStore(cap=3)
    for i in range(3):
        assert full.add_if_new(f"{i:032x}", NOW)
    assert full.add_if_new("a" * 32, NOW + 10) is False
    assert full.add_if_new("a" * 32, NOW + 301) is True and len(full) == 1  # old ones aged out


@pytest.mark.parametrize("drop", ["X-Porch-Ts", "X-Porch-Nonce", "Authorization"])
def test_each_missing_header_is_rejected(drop):
    headers = {k: v for k, v in _signed().items() if k != drop}
    assert _ok(headers) is False
    assert check_request(TOKEN, "GET", "/status.json", b"", headers, NOW, NonceStore()) == "missing-headers"
    assert _ok({}) is False


@pytest.mark.parametrize("nonce", ["", "a" * 31, "a" * 33, "A" * 32, "g" * 32, "a" * 31 + "\n", " " + "a" * 31,
                                   "a" * 16 + "-" + "a" * 15, "é" * 32])
def test_malformed_nonce_is_rejected_even_when_signed_over(nonce):
    headers = _signed(nonce=nonce)  # the MAC is genuine for this nonce; the shape alone must fail
    assert _ok(headers) is False
    assert check_request(TOKEN, "GET", "/status.json", b"", headers, NOW, NonceStore()) == "malformed"


@pytest.mark.parametrize("ts", ["", "-5", "1.5", "1e9", "+1800000000", " 1800000000", "1800000000 ", "0x10",
                                "١٨٠٠٠٠٠٠٠٠", "1" * 13])
def test_malformed_timestamp_is_rejected(ts):
    headers = _signed()
    headers["X-Porch-Ts"] = ts
    assert _ok(headers) is False


@pytest.mark.parametrize("value", ["Porch-HMAC-SHA256", "Porch-HMAC-SHA256 ", "Porch-HMAC-SHA256 " + "A" * 64,
                                   "Porch-HMAC-SHA256 " + "a" * 63, "Porch-HMAC-SHA256 " + "a" * 65,
                                   "porch-hmac-sha256 " + "a" * 64, "Basic abc", "Porch-HMAC-SHA256  " + "a" * 64])
def test_malformed_authorization_is_rejected(value):
    headers = _signed()
    headers["Authorization"] = value
    assert _ok(headers) is False
    assert check_request(TOKEN, "GET", "/status.json", b"", headers, NOW, NonceStore()) == "malformed"


def test_header_lookup_is_case_insensitive_on_plain_dicts():
    lowered = {k.lower(): v for k, v in _signed().items()}
    assert _ok(lowered) is True


def test_the_mac_is_compared_in_constant_time(monkeypatch):
    calls = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append((type(a), type(b), len(a), len(b)))
        return real(a, b)

    monkeypatch.setattr(auth.hmac, "compare_digest", spy)
    assert _ok(_signed()) is True
    assert calls and all(c[0] is bytes and c[1] is bytes and c[2] == c[3] == 64 for c in calls)
    calls.clear()
    assert verify_response(TOKEN, NONCE, 200, b"{}", response_mac(TOKEN, NONCE, 200, b"{}")) is True
    assert calls


def test_response_mac_binds_nonce_status_and_body():
    body = b'{"layers": {}}\n'
    expected = hmac.new(TOKEN.encode(), f"{NONCE}\n200\n{hashlib.sha256(body).hexdigest()}".encode(),
                        hashlib.sha256).hexdigest()
    mac = response_mac(TOKEN, NONCE, 200, body)
    assert mac == expected
    assert verify_response(TOKEN, NONCE, 200, body, mac) is True
    assert verify_response(TOKEN, "b" * 32, 200, body, mac) is False
    assert verify_response(TOKEN, NONCE, 206, body, mac) is False
    assert verify_response(TOKEN, NONCE, 200, body + b" ", mac) is False
    assert verify_response("z" * 32, NONCE, 200, body, mac) is False
    for bad in (None, "", mac.upper(), mac[:-1], "0" * 64):
        assert verify_response(TOKEN, NONCE, 200, body, bad) is False


def test_headers_look_signed_is_only_a_shape_and_freshness_check():
    assert headers_look_signed(_signed(), NOW) is True
    assert headers_look_signed(_signed(ts=NOW - 121), NOW) is False
    assert headers_look_signed({}, NOW) is False
    assert headers_look_signed(_signed(nonce="x"), NOW) is False


# ---- the failed-login throttle (injected clock) -------------------------------------------------------------------
class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_ten_failures_in_a_minute_lock_the_ip_for_five_minutes():
    clock = Clock()
    throttle = FailureThrottle(clock=clock)
    for _ in range(9):
        throttle.record_failure("10.0.0.1")
        assert throttle.locked("10.0.0.1") is False
    throttle.record_failure("10.0.0.1")
    assert throttle.locked("10.0.0.1") is True
    assert throttle.locked("10.0.0.2") is False
    clock.t += 299
    assert throttle.locked("10.0.0.1") is True
    clock.t += 2  # 301 s after the tenth failure
    assert throttle.locked("10.0.0.1") is False
    for _ in range(9):  # the count starts over after a lockout
        throttle.record_failure("10.0.0.1")
    assert throttle.locked("10.0.0.1") is False


def test_failures_spread_over_more_than_a_minute_do_not_lock():
    clock = Clock()
    throttle = FailureThrottle(clock=clock)
    for _ in range(40):
        throttle.record_failure("10.0.0.1")
        clock.t += 7  # at most 9 failures fall inside any 60 s window
        assert throttle.locked("10.0.0.1") is False


def test_throttle_table_holds_at_most_1024_ips_oldest_dropped():
    throttle = FailureThrottle(clock=Clock())
    for i in range(1500):
        throttle.record_failure(f"10.1.{i // 250}.{i % 250}")
    assert len(throttle) == 1024
    assert "10.1.0.0" not in throttle._table and "10.1.5.249" in throttle._table
