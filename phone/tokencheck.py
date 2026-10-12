"""Check a GitHub token without trusting it: it must see public data only. Standard library only.

``check(token)`` asks GitHub two questions through the HTTP gate (``phone.httpgate``):

1. ``GET /user``: a reply with a non-empty ``X-OAuth-Scopes`` header means the token carries scopes (``too_powerful``);
   401 means GitHub rejected it (``rejected``).
2. ``GET /user/repos?visibility=private&per_page=1``: any private repository in the list means the token can reach
   private code (``too_powerful``). An empty list, with no scope header, is ``accepted``.

No answer, or a network that is not usable right now, is ``unreachable``. The token is only sent to ``API_BASE`` (the
gate also refuses a credential for any other host), redirects are never followed, and nothing about the value is put
in the returned text.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from phone import httpgate

API_BASE = "https://api.github.com"
USER_AGENT = "porchlamp-phone/1"
API_VERSION = "2022-11-28"
TIMEOUT_S = 20

ACCEPTED, REJECTED, TOO_POWERFUL, UNREACHABLE = "accepted", "rejected", "too_powerful", "unreachable"
TEXT = {
    ACCEPTED: "GitHub accepted the token.",
    REJECTED: "GitHub rejected the token.",
    TOO_POWERFUL: "That token can reach more than public data. It was refused.",
    UNREACHABLE: "Could not reach GitHub to check it.",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an error here: urllib would send the Authorization header on to the new address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def shape_ok(token: object) -> bool:
    """A single printable ASCII word: nothing that could split a header line."""
    return isinstance(token, str) and 8 <= len(token) <= 255 and token.isascii() and token.isprintable() \
        and not any(ch.isspace() for ch in token)


def _get(path: str, token: str, base: str, timeout: float) -> tuple[int, Any, bytes]:
    req = urllib.request.Request(base + path, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION, "User-Agent": USER_AGENT})
    try:
        with httpgate.urlopen(req, timeout, opener=_OPENER, scope="token_test", wait=False) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def _result(state: str, **extra: Any) -> dict[str, Any]:
    return {"state": state, "detail": TEXT[state], **extra}


def check(token: str, *, base: str | None = None, timeout: float = TIMEOUT_S) -> dict[str, Any]:
    """``{"state": accepted|rejected|too_powerful|unreachable, "detail": text}`` (plus ``calls_left`` when known)."""
    base = base or API_BASE
    if not shape_ok(token):
        return {"state": REJECTED, "detail": "That does not look like a GitHub token."}
    try:
        status, headers, _body = _get("/user", token, base, timeout)
        if status == 401:
            return _result(REJECTED)
        if status != 200:
            return _result(UNREACHABLE, blocked=False)
        scopes = (headers.get("x-oauth-scopes") or "").strip()
        if scopes:
            return _result(TOO_POWERFUL)
        status, headers, body = _get("/user/repos?visibility=private&per_page=1", token, base, timeout)
        if status == 401:
            return _result(REJECTED)
        if status != 200:
            return _result(UNREACHABLE, blocked=False)
        repos = json.loads(body.decode("utf-8", "replace"))
    except httpgate.WouldBlock:
        return {"state": UNREACHABLE, "detail": "There is no usable network right now. Try again when Wi-Fi returns.",
                "blocked": True}
    except (OSError, ValueError, urllib.error.URLError):
        return _result(UNREACHABLE, blocked=False)
    if not isinstance(repos, list):
        return _result(UNREACHABLE, blocked=False)
    if repos:
        return _result(TOO_POWERFUL)
    left = headers.get("x-ratelimit-remaining")
    return _result(ACCEPTED, calls_left=int(left) if left and left.isdigit() else None)


def rate_limit(token: str, *, base: str | None = None, timeout: float = TIMEOUT_S) -> dict[str, Any]:
    """One free ``GET /rate_limit``: ``{"status": int, "remaining", "limit", "reset"}`` or ``{"blocked": True}``."""
    try:
        status, _headers, body = _get("/rate_limit", token, base or API_BASE, timeout)
    except httpgate.WouldBlock:
        return {"blocked": True}
    except (OSError, ValueError, urllib.error.URLError):
        return {"status": 0}
    out: dict[str, Any] = {"status": status}
    if status == 200:
        try:
            core = json.loads(body.decode("utf-8", "replace"))["resources"]["core"]
            out.update(remaining=int(core["remaining"]), limit=int(core["limit"]), reset=float(core["reset"]))
        except (ValueError, KeyError, TypeError):
            out["status"] = 0
    return out
