"""One definition of a legal GitHub owner or repository name for everything that pastes one into a path or a query.

Standard library only (the phone imports this too).
"""

from __future__ import annotations

import re

NAME_RX = re.compile(r"[A-Za-z0-9._-]{1,100}")


def valid_name(s: object) -> bool:
    """True iff ``s`` is a string of 1-100 characters from ``A-Za-z0-9._-`` and is not ``.`` or ``..``."""
    return isinstance(s, str) and NAME_RX.fullmatch(s) is not None and s not in (".", "..")


def valid_ref(owner: object, name: object) -> bool:
    """True iff both the owner and the repository name are legal."""
    return valid_name(owner) and valid_name(name)
