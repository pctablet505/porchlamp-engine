"""Credential files named by environment variables, and the redaction of every secret they hold.

An environment variable holds a file *path*, never a key: ``PORCHLAMP_GSC_CREDENTIALS`` names the
Search Console service-account JSON file and ``PORCHLAMP_BING_KEY_FILE`` names a file holding the
Bing Webmaster API key. Like an ssh key, each file must be readable by its owner only (``chmod 600``).
A variable's value is never echoed: a key pasted in place of a path must not reach the screen.

Every secret read from those files is registered with the run's :class:`Redactor` before anything
else happens to it. The CLI passes every line it prints, every error message and every report it
writes through the redactor, and :class:`RedactingFilter` rewrites the HTTP client's own log
records: httpx logs each request URL at INFO, and Bing's API key travels in the query string.
"""

from __future__ import annotations

import json
import logging
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus

GSC_CREDENTIALS_ENV = "PORCHLAMP_GSC_CREDENTIALS"
BING_KEY_FILE_ENV = "PORCHLAMP_BING_KEY_FILE"
REDACTED = "[redacted]"
#: Where a Google service-account file sends its signed token request; any other host is refused.
GOOGLE_TOKEN_HOST = "https://oauth2.googleapis.com/"

_WHAT = {
    GSC_CREDENTIALS_ENV: "the Search Console service-account JSON file",
    BING_KEY_FILE_ENV: "the file holding the Bing Webmaster API key",
}
_SERVICE_ACCOUNT_FIELDS = ("client_email", "private_key", "token_uri")


class SearchOpsError(Exception):
    """A failure the CLI reports as one plain sentence; its text is already redacted."""

    exit_code = 1


class CredentialError(SearchOpsError):
    """A credential that is missing, unreadable, exposed to other users or of the wrong shape."""

    exit_code = 2


class Redactor:
    """Replaces every registered secret with ``[redacted]``, in each form it takes in a URL,
    in JSON and in a Python repr."""

    def __init__(self) -> None:
        self._secrets: set[str] = set()

    def add(self, secret: str) -> None:
        """Register ``secret`` (and its quoted, JSON-escaped and repr-escaped forms)."""
        forms = {secret, quote(secret, safe=""), quote_plus(secret), json.dumps(secret)[1:-1], repr(secret)[1:-1]}
        self._secrets.update(form for form in forms if form)

    def __call__(self, text: str) -> str:
        for secret in sorted(self._secrets, key=len, reverse=True):
            text = text.replace(secret, REDACTED)
        return text


class RedactingFilter(logging.Filter):
    """Rewrites each record's message through the redactor before any handler formats it."""

    def __init__(self, redact: Redactor) -> None:
        super().__init__()
        self._redact = redact

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # a malformed record is kept, its parts still redacted
            message = f"{record.msg} {record.args}"
        record.msg = self._redact(message)
        record.args = None
        return True


def credential_path(env_name: str, environ: Mapping[str, str]) -> Path:
    """The file ``env_name`` names; refused unless it is a regular file only its owner can open."""
    value = environ.get(env_name, "").strip()
    if not value:
        raise CredentialError(f"{env_name} is not set: set it to the path of {_WHAT[env_name]}.")
    path = Path(value)
    try:
        path = path.expanduser()
        mode = path.stat().st_mode
    except (OSError, RuntimeError, ValueError):
        mode = 0
    if not stat.S_ISREG(mode):
        raise CredentialError(
            f"{env_name} does not name a file: set it to the path of {_WHAT[env_name]}, not to the key itself."
        )
    if mode & 0o077:
        raise CredentialError(f"{env_name} names a file other users can open: run chmod 600 on that file.")
    return path


def read_bing_key(environ: Mapping[str, str], redact: Redactor) -> str:
    """The Bing Webmaster API key from the file ``PORCHLAMP_BING_KEY_FILE`` names (one line)."""
    path = credential_path(BING_KEY_FILE_ENV, environ)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = ""
    key = text.strip()
    if key:
        redact.add(key)
    if not key or not key.isascii() or not key.isprintable() or any(char.isspace() for char in key):
        raise CredentialError(f"The file named by {BING_KEY_FILE_ENV} does not hold one API key on one line.")
    return key


def read_service_account(environ: Mapping[str, str], redact: Redactor) -> dict[str, Any]:
    """The service-account fields from the JSON file ``PORCHLAMP_GSC_CREDENTIALS`` names.

    The private key (whole, and each line of its PEM body) and its id are registered with
    ``redact`` before the file's shape is checked, so no later message can carry them.
    """
    path = credential_path(GSC_CREDENTIALS_ENV, environ)
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info = None
    if not isinstance(info, dict):
        raise CredentialError(f"The file named by {GSC_CREDENTIALS_ENV} is not a JSON object.")
    for field in ("private_key", "private_key_id"):
        value = info.get(field)
        if isinstance(value, str) and value:
            redact.add(value)
            for line in value.splitlines():
                if len(line.strip()) >= 16 and not line.startswith("-----"):
                    redact.add(line.strip())
    if info.get("type") != "service_account":
        raise CredentialError(
            f"The file named by {GSC_CREDENTIALS_ENV} is not a service-account key (its type is not service_account)."
        )
    missing = [field for field in _SERVICE_ACCOUNT_FIELDS if not isinstance(info.get(field), str) or not info[field]]
    if missing:
        raise CredentialError(f"The file named by {GSC_CREDENTIALS_ENV} lacks {', '.join(missing)}.")
    if not info["token_uri"].startswith(GOOGLE_TOKEN_HOST):
        raise CredentialError(
            f"The file named by {GSC_CREDENTIALS_ENV} sends its token request somewhere other than {GOOGLE_TOKEN_HOST}."
        )
    return info
