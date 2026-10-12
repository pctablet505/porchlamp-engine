"""test_token: a token must see public data only. A fake GitHub on loopback gives every shape of answer."""

from __future__ import annotations

import json

import pytest

from phone import tokencheck
from phone.control import Controls, Settings
from phone.httpgate import Gate
from tests.helpers.fakes import FakeClock, FakeHTTP, github_responder, loopback_gate_options
from tests.helpers.gate import installed

pytestmark = pytest.mark.usefixtures("clean_env")

TOKEN = "ghp_CANARYtokencheck0123456789abcdefABC"
WIFI = {"battery_pct": 80, "temp_c": 30.0, "network": "wifi", "wifi_ip": "127.0.0.1"}


@pytest.fixture
def gate(tmp_path):
    controls = Controls(Settings(tmp_path / "s.json"), clock=FakeClock(), state_dir=tmp_path)
    controls.update_device(WIFI)
    g = Gate(controls=controls, **loopback_gate_options())
    g.test_controls = controls
    with installed(g):
        yield g


def serve(responder):
    srv = FakeHTTP(responder)
    return srv


def check(srv):
    return tokencheck.check(TOKEN, base=srv.url, timeout=3)


def test_a_token_with_no_scopes_and_no_private_repos_is_accepted_after_exactly_two_questions(gate):
    srv = serve(github_responder(scopes="", private=[]))
    try:
        result = check(srv)
        assert result["state"] == "accepted" and result["detail"] == "GitHub accepted the token."
        assert [r.path for r in srv.seen] == ["/user", "/user/repos?visibility=private&per_page=1"]
        assert all(r.headers["authorization"] == f"Bearer {TOKEN}" for r in srv.seen)
        assert TOKEN not in json.dumps(result)
    finally:
        srv.close()


def test_a_fine_grained_token_with_no_scope_header_is_accepted(gate):
    srv = serve(github_responder(scopes=None, private=[]))
    try:
        assert check(srv)["state"] == "accepted"
    finally:
        srv.close()


@pytest.mark.parametrize("scopes", ["repo", "public_repo", "repo, read:org", "gist", " read:user "])
def test_any_scope_at_all_makes_the_token_too_powerful_and_is_refused(gate, scopes):
    srv = serve(github_responder(scopes=scopes, private=[]))
    try:
        result = check(srv)
        assert result["state"] == "too_powerful" and "refused" in result["detail"]
        assert [r.path for r in srv.seen] == ["/user"]                       # no need to ask further
    finally:
        srv.close()


def test_a_private_repository_in_the_list_makes_it_too_powerful_even_without_a_scope_header(gate):
    srv = serve(github_responder(scopes="", private=[{"full_name": "owner/secret", "private": True}]))
    try:
        result = check(srv)
        assert result["state"] == "too_powerful"
        assert len(srv.seen) == 2 and srv.seen[1].path.startswith("/user/repos?visibility=private")
    finally:
        srv.close()


def test_a_401_from_either_question_is_rejected(gate):
    srv = serve(github_responder(user_status=401))
    try:
        assert check(srv) == {"state": "rejected", "detail": "GitHub rejected the token."}
        assert len(srv.seen) == 1
    finally:
        srv.close()
    ok = github_responder()

    def second_refuses(req):
        return (401, {}, b'{"message": "Bad credentials"}') if req.path.startswith("/user/repos") else ok(req)

    srv = serve(second_refuses)
    try:
        assert check(srv)["state"] == "rejected"
    finally:
        srv.close()


def test_no_answer_is_unreachable(gate):
    srv = serve(github_responder())
    port_url = srv.url
    srv.close()                                                              # nothing listens there any more
    result = tokencheck.check(TOKEN, base=port_url, timeout=2)
    assert result["state"] == "unreachable" and result["detail"] == "Could not reach GitHub to check it."
    srv = serve(lambda req: (503, {}, b"down"))
    try:
        assert check(srv)["state"] == "unreachable"
    finally:
        srv.close()
    srv = serve(lambda req: (200, {"x-oauth-scopes": ""}, b"this is not json"))
    try:
        assert check(srv)["state"] == "unreachable"
    finally:
        srv.close()


def test_a_network_that_cannot_be_used_right_now_is_unreachable_and_nothing_is_sent(gate):
    gate.test_controls.update_device({**WIFI, "network": "none", "wifi_ip": None})
    srv = serve(github_responder())
    try:
        result = check(srv)
        assert result["state"] == "unreachable" and result["blocked"] is True and srv.count == 0
    finally:
        srv.close()


def test_a_redirect_is_never_followed_so_the_token_cannot_follow_it_to_another_host(gate):
    other = serve(github_responder())
    first = serve(lambda req: (302, {"Location": other.url + "/user"}, b""))
    try:
        assert check(first)["state"] == "unreachable"
        assert first.count == 1 and other.count == 0
    finally:
        first.close()
        other.close()


@pytest.mark.parametrize("bad", ["", "short", "has space in it 1234", "line\nbreak123456", "tök" * 5, None, 12345,
                                 "x" * 300])
def test_something_that_is_not_shaped_like_a_token_is_rejected_without_a_request(gate, bad):
    srv = serve(github_responder())
    try:
        result = tokencheck.check(bad, base=srv.url, timeout=2)
        assert result["state"] == "rejected" and srv.count == 0
    finally:
        srv.close()


def test_rate_limit_is_one_free_call_and_reports_the_numbers(gate):
    srv = serve(github_responder(remaining=3410, limit=5000, reset=1_790_003_600))
    try:
        rl = tokencheck.rate_limit(TOKEN, base=srv.url, timeout=3)
        assert rl == {"status": 200, "remaining": 3410, "limit": 5000, "reset": 1_790_003_600.0}
        assert [r.path for r in srv.seen] == ["/rate_limit"]
        srv2 = serve(lambda req: (401, {}, b"{}"))
        try:
            assert tokencheck.rate_limit(TOKEN, base=srv2.url, timeout=3) == {"status": 401}
        finally:
            srv2.close()
    finally:
        srv.close()
    gate.test_controls.update_device({**WIFI, "network": "none", "wifi_ip": None})
    assert tokencheck.rate_limit(TOKEN, base="http://127.0.0.1:9", timeout=1) == {"blocked": True}
