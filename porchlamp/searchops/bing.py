"""Bing Webmaster API and IndexNow: request URLs and bodies (pure), and the Bing client.

Bing's API key travels in the query string (``?apikey=``), so every Bing URL built here is itself a
secret: it is only ever printed through the run's redactor, and errors name the method, not the URL.
IndexNow needs no secret: its key is public by design, served at the site root.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

if TYPE_CHECKING:
    from porchlamp.searchops.transport import Http

BING_API = "https://ssl.bing.com/webmaster/api.svc/json/"
INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow"
STATS_METHODS = ("GetRankAndTrafficStats", "GetQueryStats", "GetPageStats", "GetCrawlStats")
#: What each IndexNow status means (the protocol's own table); 200 and 202 are success.
INDEXNOW_ANSWERS = {
    200: "received",
    202: "received; key validation pending",
    400: "bad request (invalid format)",
    403: "key not valid (on a key's first use: SiteVerificationNotCompleted, retry in 5 to 10 minutes)",
    422: "URLs do not belong to the host, or the key does not match the protocol",
    429: "too many requests (potential spam): stop and retry later",
}
INDEXNOW_SUCCESS = (200, 202)


def bing_url(method: str, key: str, **query: str) -> str:
    """A Bing Webmaster JSON endpoint with its query (GET parameters) and the API key last."""
    return f"{BING_API}{method}?{urlencode({**query, 'apikey': key})}"


def indexnow_body(host: str, key: str, urls: Sequence[str]) -> dict[str, Any]:
    """One IndexNow POST: the host, the key, where the key file is, and at most 10,000 URLs."""
    return {"host": host, "key": key, "keyLocation": f"https://{host}/{key}.txt", "urlList": list(urls)}


def indexnow_answer(status: int) -> str:
    return INDEXNOW_ANSWERS.get(status, "unexpected status")


def submission_room(quota: Mapping[str, Any] | None) -> int:
    """How many URLs Bing takes now: the smaller of the daily and the monthly quota left (0 if unknown)."""
    quota = quota or {}
    return max(0, min(int(quota.get("DailyQuota") or 0), int(quota.get("MonthlyQuota") or 0)))


class BingWebmaster:
    """Bing Webmaster JSON calls with the API key; the key is registered with the run's redactor."""

    def __init__(self, key: str, http: Http) -> None:
        self._key = key
        self._http = http
        http.redact.add(key)

    def call(self, method: str, body: dict[str, Any] | None = None, **query: str) -> Any:
        """GET ``method`` with ``query``, or POST ``body`` to it; the answer's ``d``."""
        label = f"Bing {method}"
        verb = "GET" if body is None else "POST"
        response = self._http.send(verb, bing_url(method, self._key, **query), label=label, body=body)
        answer = self._http.answer(response, label)
        return answer.get("d") if isinstance(answer, dict) else answer
