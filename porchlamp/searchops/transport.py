"""The thin network layer: one httpx client whose errors and log lines never carry a secret.

Every error raised here is an :class:`ApiError` whose text went through the run's
:class:`~porchlamp.searchops.credentials.Redactor`. It names the call (``label``), never the URL,
and it is raised outside the ``except`` block, so no httpx exception (whose request URL may carry
Bing's key) rides along as its context. While a client is open, a
:class:`~porchlamp.searchops.credentials.RedactingFilter` sits on the ``httpx`` logger, which logs
each request URL at INFO; httpcore's debug trace names only the method, never the URL.

This layer is not exercised against a live or a stand-in server by the tests (owner rule: the
owner's first real run proves it); the tests cover what it builds and how it reports failures.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

import httpx

from porchlamp.searchops.credentials import RedactingFilter, Redactor, SearchOpsError

TIMEOUT_SECONDS = 60.0
JSON_CONTENT_TYPE = "application/json; charset=utf-8"
_LOGGER = "httpx"


class ApiError(SearchOpsError):
    """A call that failed: no answer (``status`` None) or an answer outside 2xx."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def service_message(response: httpx.Response) -> str:
    """The service's own words from an error answer (Google, Bing and IndexNow shapes), else the
    start of the body; not yet redacted."""
    text = response.text
    try:
        body = json.loads(text)
    except ValueError:
        body = None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        keys = ("errorCode", "ErrorCode", "error", "message", "Message", "error_description")
        parts = [str(body[key]) for key in keys if body.get(key) not in (None, "")]
        if parts:
            return ": ".join(parts)
    return " ".join(text.split())[:300]


def failure_text(label: str, response: httpx.Response) -> str:
    """``<label>: HTTP <status> <reason>[: <the service's message>]``; not yet redacted."""
    head = f"{label}: HTTP {response.status_code} {response.reason_phrase}".rstrip()
    detail = service_message(response)
    return f"{head}: {detail}" if detail else head


class Http:
    """One run's HTTP client. Use it as a context manager: the log filter lives while it is open."""

    def __init__(self, redact: Redactor) -> None:
        self.redact = redact
        self._filter = RedactingFilter(redact)
        logging.getLogger(_LOGGER).addFilter(self._filter)
        self._client = httpx.Client(timeout=TIMEOUT_SECONDS)

    def __enter__(self) -> Http:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()
        logging.getLogger(_LOGGER).removeFilter(self._filter)

    def send(
        self,
        method: str,
        url: str,
        *,
        label: str,
        body: Any = None,
        content: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """One request; ``body`` goes as JSON. A transport failure raises :class:`ApiError`."""
        sent_headers = dict(headers or {})
        payload = content
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            sent_headers.setdefault("Content-Type", JSON_CONTENT_TYPE)
        try:
            return self._client.request(method, url, content=payload, headers=sent_headers)
        except (httpx.HTTPError, httpx.InvalidURL) as error:
            failure = f"{label}: no answer ({type(error).__name__}: {error})"
        raise ApiError(self.redact(failure))

    def check(self, response: httpx.Response, label: str) -> httpx.Response:
        """``response`` if it is a 2xx answer; else an :class:`ApiError` with the service's message."""
        if response.is_success:
            return response
        raise ApiError(self.redact(failure_text(label, response)), status=response.status_code)

    def answer(self, response: httpx.Response, label: str) -> Any:
        """The JSON of a 2xx answer (``None`` for an empty body)."""
        self.check(response, label)
        if not response.content.strip():
            return None
        try:
            return response.json()
        except ValueError:
            failure = f"{label}: HTTP {response.status_code} but the answer is not JSON"
        raise ApiError(self.redact(failure), status=response.status_code)
