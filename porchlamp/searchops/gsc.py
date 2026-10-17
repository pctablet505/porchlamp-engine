"""Google Search Console: request URLs and bodies (pure), and a client that signs as a service account.

A service account proves itself with an RS256-signed JWT, which needs an RSA library. google-auth
signs it (the ``searchops`` extra installs google-auth); it is imported only when a command really
calls Google. The token request itself, like every API call, goes through
:class:`~porchlamp.searchops.transport.Http`, so google-auth needs neither requests nor urllib3.
``sitemaps submit`` asks for the read-write scope; every other call asks for the read-only one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from porchlamp.searchops.credentials import GSC_CREDENTIALS_ENV, CredentialError

if TYPE_CHECKING:
    from porchlamp.searchops.transport import Http

PROPERTY = "sc-domain:porchlamp.org"
WEBMASTERS_API = "https://www.googleapis.com/webmasters/v3"
INSPECTION_API = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"
READONLY_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
WRITE_SCOPE = "https://www.googleapis.com/auth/webmasters"
ROW_LIMIT = 25_000  # Search Analytics rows per request, the API's maximum
WRITE_PERMISSIONS = ("siteOwner", "siteFullUser")


def scopes(write: bool) -> list[str]:
    """The one OAuth scope a command needs: read-write only to submit a sitemap."""
    return [WRITE_SCOPE if write else READONLY_SCOPE]


def sites_url() -> str:
    return f"{WEBMASTERS_API}/sites"


def sitemaps_url(prop: str = PROPERTY) -> str:
    return f"{WEBMASTERS_API}/sites/{quote(prop, safe='')}/sitemaps"


def sitemap_url(feed: str, prop: str = PROPERTY) -> str:
    """The sitemaps.submit URL; both the property and the sitemap URL are path segments."""
    return f"{sitemaps_url(prop)}/{quote(feed, safe='')}"


def search_analytics_url(prop: str = PROPERTY) -> str:
    return f"{WEBMASTERS_API}/sites/{quote(prop, safe='')}/searchAnalytics/query"


def inspection_body(url: str, prop: str = PROPERTY) -> dict[str, str]:
    return {"inspectionUrl": url, "siteUrl": prop}


def query_body(start: date, end: date, start_row: int = 0) -> dict[str, Any]:
    """One page of the Search Analytics query: web search, by query and page."""
    return {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "dimensions": ["query", "page"],
        "type": "web",
        "rowLimit": ROW_LIMIT,
        "startRow": start_row,
    }


def access_problem(sites: Mapping[str, Any], write: bool, prop: str = PROPERTY) -> str | None:
    """Why the credential cannot run the command on ``prop`` (from sites.list), or None."""
    levels = [entry.get("permissionLevel") for entry in sites.get("siteEntry") or [] if entry.get("siteUrl") == prop]
    level = levels[0] if levels else None
    if level is None or level == "siteUnverifiedUser":
        return (f"The service account cannot see {prop}: add its client_email in Search Console, "
                "Settings, Users and permissions.")
    if write and level not in WRITE_PERMISSIONS:
        return f"The service account has {level} on {prop}; submitting a sitemap needs Full or Owner."
    return None


@dataclass
class _AuthResponse:
    """google-auth's transport response: ``status``, ``headers`` and ``data``."""

    status: int
    headers: dict[str, str] = field(default_factory=dict)
    data: bytes = b""


class _AuthTransport:
    """google-auth's transport interface (a callable request) over :class:`Http`."""

    def __init__(self, http: Http) -> None:
        self._http = http

    def __call__(self, url: str, method: str = "GET", body: Any = None, headers: Any = None,
                 timeout: Any = None, **kwargs: Any) -> _AuthResponse:
        content = body.encode("utf-8") if isinstance(body, str) else body
        response = self._http.send(method, url, label="Google token request", content=content, headers=headers)
        return _AuthResponse(response.status_code, dict(response.headers), response.content)


class SearchConsole:
    """Search Console calls as the service account, with a token google-auth refreshes."""

    def __init__(self, info: Mapping[str, Any], write: bool, http: Http) -> None:
        try:
            from google.oauth2 import service_account
        except ImportError:
            service_account = None
        if service_account is None:
            raise CredentialError(
                "google-auth is not installed: install the searchops extra (pip install 'porchlamp[searchops]')."
            )
        try:
            credentials = service_account.Credentials.from_service_account_info(dict(info), scopes=scopes(write))
        except Exception:  # google-auth could not load the key; its message is not repeated
            credentials = None
        if credentials is None:
            raise CredentialError(f"google-auth cannot load the private key in the file named by {GSC_CREDENTIALS_ENV}.")
        self._credentials = credentials
        self._http = http

    def _headers(self) -> dict[str, str]:
        from porchlamp.searchops.transport import ApiError

        if not self._credentials.valid:
            failure = None
            try:
                self._credentials.refresh(_AuthTransport(self._http))
            except ApiError:
                raise
            except Exception as error:  # google.auth.exceptions.RefreshError and kin
                failure = f"Google refused the service-account token: {error}"
            if failure is not None:
                raise ApiError(self._http.redact(failure))
            if not self._credentials.token:
                raise ApiError("Google token request: the answer held no access token")
            self._http.redact.add(self._credentials.token)
        return {"Authorization": f"Bearer {self._credentials.token}"}

    def get(self, url: str, label: str) -> Any:
        return self._http.answer(self._http.send("GET", url, label=label, headers=self._headers()), label)

    def put(self, url: str, label: str) -> Any:
        return self._http.answer(self._http.send("PUT", url, label=label, headers=self._headers()), label)

    def post(self, url: str, body: Mapping[str, Any], label: str) -> Any:
        return self._http.answer(self._http.send("POST", url, label=label, body=body, headers=self._headers()), label)
