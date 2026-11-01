"""One definition of a legal GitHub owner or repository name for everything that pastes one into a path or a query.

Standard library only (the phone imports this too).
"""

from __future__ import annotations

import re

NAME_RX = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._-]{0,99}")


def valid_name(s: object) -> bool:
    """True iff ``s`` is 1-100 characters from ``A-Za-z0-9._-``, does not start with ``-`` (a command-line flag), and is not ``.`` or ``..``."""
    return isinstance(s, str) and NAME_RX.fullmatch(s) is not None and s not in (".", "..")


def valid_ref(owner: object, name: object) -> bool:
    """True iff both the owner and the repository name are legal."""
    return valid_name(owner) and valid_name(name)


def is_path_segment(s: object) -> bool:
    """True iff ``s`` is a non-empty ``str`` that is one file-name component: not ``.`` or ``..``, and no ``/``, ``\\``, NUL or other control character (below 32, or 127).

    Looser than :func:`valid_name` on purpose: for a name pasted into a path and nothing else, ``valid_name`` refuses a real
    repository (``981011512/--``) that this accepts.
    """
    return (
        isinstance(s, str)
        and s not in ("", ".", "..")
        and not any(c in "/\\" or ord(c) < 32 or ord(c) == 127 for c in s)
    )
