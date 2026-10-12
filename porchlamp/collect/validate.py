"""Checks a value passes before it is pasted into a ``gh api`` path, a request URL or a file name.

Each check returns the value or raises ``ValueError`` with a message that names the rule. Standard library only.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Collection

from porchlamp.collect.names import valid_name

#: The one host a repository file body is read from.
RAW_GITHUB_HOSTS: frozenset[str] = frozenset({"raw.githubusercontent.com"})


def _has_control_character(value: str) -> bool:
    return any(ch in value for ch in ("\r", "\n", "\x00"))


def github_segment(value: object, label: str = "segment") -> str:
    """A legal GitHub owner or repository name (see :func:`porchlamp.collect.names.valid_name`)."""
    if not valid_name(value):
        raise ValueError(f"invalid GitHub {label}: {value!r}")
    return value  # type: ignore[return-value]  # valid_name proved it is a str


def gh_api_path(path: object) -> str:
    """A REST path for ``gh api``: not empty, not a flag, no control character, no ``..`` segment."""
    if (
        not isinstance(path, str)
        or not path
        or path.startswith("-")
        or _has_control_character(path)
        or ".." in path.split("/")
    ):
        raise ValueError(f"invalid gh api path: {path!r}")
    return path


def relative_file_path(path: object, what: str) -> str:
    """A file path inside a repository: not empty, not absolute, no control character, no ``..`` segment."""
    if (
        not isinstance(path, str)
        or not path
        or path.startswith("/")
        or _has_control_character(path)
        or ".." in path.split("/")
    ):
        raise ValueError(f"invalid {what}: {path!r}")
    return path


def https_url(url: str, hosts: Collection[str], what: str) -> str:
    """An ``https`` URL on one of ``hosts`` whose path has no ``..`` segment."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in hosts or ".." in parsed.path.split("/"):
        raise ValueError(f"disallowed {what} URL: {url!r}")
    return url
