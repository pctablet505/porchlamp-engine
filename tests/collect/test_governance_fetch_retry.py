"""`fetch_file_text` must survive one blackholed CDN address (2026-09-21).

MEASURED. raw.githubusercontent.com resolves to four IPv4 anycast addresses;
on this network 185.199.109.133 accepts no SYN (the other three connect in
~0.05 s, verified with /dev/tcp). Python's urllib takes the address
`getaddrinfo` hands it and blocks for the full timeout -- it has no
happy-eyeballs fallback, unlike curl, which is why `curl` succeeded on the
same host while the collector stalled. Six identical urllib fetches measured
0.05/0.07/0.05/0.08/**30.09**/0.05 s.

`collect_governance` fetches 12 files per repo (GOVERNANCE_FILES +
AI_POLICY_FILES), so P(at least one attempt lands on the dead address) =
1 - 0.75**12 = 97%. Because every non-404 error raises (correctly -- a
transient failure must never be cached as "absent"), one unlucky address
failed the WHOLE repo: L3g run 3 recorded "3882 clean, 1871 with failures"
and throughput fell from 6.00 to 0.50 repos/min.

A bounded retry fixes it without touching host network config: the resolver
rotates addresses, so attempt 2 almost always lands somewhere alive.
"""

import urllib.error

import pytest

from porchlamp.collect import governance as gov


class _Body:
    def __init__(self, text: str) -> None:
        self._text = text.encode()

    def read(self) -> bytes:
        return self._text

    def __enter__(self) -> "_Body":
        return self

    def __exit__(self, *a: object) -> None:
        return None


def _patch(monkeypatch, outcomes):
    """Each call pops one outcome: an Exception to raise, or str to return."""
    calls: list[int] = []

    def fake_urlopen(req, timeout=None):
        calls.append(1)
        out = outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return _Body(out)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


def test_a_blackholed_address_is_retried_and_the_body_returned(monkeypatch):
    """The measured failure: connect stalls, then a retry lands on a live IP."""
    calls = _patch(monkeypatch, [
        urllib.error.URLError(TimeoutError("timed out")),
        "# Contributing\n",
    ])
    assert gov.fetch_file_text("dtolnay", "syn")("CONTRIBUTING.md") == "# Contributing\n"
    assert len(calls) == 2


def test_a_bare_timeout_is_also_retried(monkeypatch):
    """urlopen can raise TimeoutError directly, not only wrapped in URLError."""
    _patch(monkeypatch, [TimeoutError("timed out"), "body"])
    assert gov.fetch_file_text("o", "n")("SECURITY.md") == "body"


# --- over-correction guards ------------------------------------------------ #

def test_404_still_means_absent_and_is_never_retried(monkeypatch):
    """A missing file is an ANSWER, not a transport failure: one call only."""
    calls = _patch(monkeypatch, [
        urllib.error.HTTPError("u", 404, "nf", {}, None),  # type: ignore[arg-type]
    ])
    assert gov.fetch_file_text("o", "n")("GOVERNANCE.md") is None
    assert len(calls) == 1


def test_exhausted_retries_still_raise_so_nothing_is_cached_as_absent(monkeypatch):
    """The contract that protects the data: a transient error must never be
    recorded as "file absent". Retrying must not turn into swallowing."""
    _patch(monkeypatch, [urllib.error.URLError(TimeoutError()) for _ in range(12)])
    with pytest.raises(urllib.error.URLError):
        gov.fetch_file_text("o", "n")("CONTRIBUTING.md")


def test_a_non_404_http_error_still_raises_and_is_not_retried(monkeypatch):
    """A 500 is the server answering; retrying it is not this fix's job."""
    calls = _patch(monkeypatch, [
        urllib.error.HTTPError("u", 500, "err", {}, None),  # type: ignore[arg-type]
    ])
    with pytest.raises(urllib.error.HTTPError):
        gov.fetch_file_text("o", "n")("CONTRIBUTING.md")
    assert len(calls) == 1
