"""Where the collectors open an outgoing HTTP request: through the phone engine's one gate.

``phone.httpgate`` refuses a host that is not on its allow-list, counts the bytes, and holds the request while the
owner has paused or stopped collection. On a workstation install that does not carry the ``phone`` package the
requests go straight to ``urllib`` as they always did.
"""

from __future__ import annotations

import time
import urllib.request
from collections.abc import Callable
from typing import Any

try:
    from phone import httpgate as _gate
except ImportError:  # a porchlamp install without the phone engine: no gate
    _gate = None


def urlopen(req: urllib.request.Request | str, timeout: float = 30, *, opener: Any = None) -> Any:
    """``urllib.request.urlopen`` (or ``opener.open``) through the gate when there is one."""
    if _gate is not None:
        return _gate.urlopen(req, timeout, opener=opener)
    if opener is not None:
        return opener.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def rate_wait(resource: str, remaining: int, seconds: float,
              sleep: Callable[[float], object] | None = None) -> None:
    """Wait for a GitHub rate-limit reset; with the gate a stop ends the wait within a second."""
    if _gate is not None:
        _gate.rate_wait(resource, remaining, seconds, sleep)
    else:
        (sleep or time.sleep)(seconds)
