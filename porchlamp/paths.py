"""Single resolution point for the on-disk data root.

Every script that reads or writes collected data (``scripts/gather.py``,
``scripts/derive.py``) resolves its root through :func:`data_root` so a
scratch data root can be substituted -- for a live gather/derive run against
a copy, never the main checkout's own ``data/`` (docs/v2/00-README.md
non-negotiables) -- without touching any collector's logic. The collectors
themselves (``porchlamp/collect/*``) take a data/raw directory as an explicit
parameter from their caller; only the two scripts' own module-level
constants go through the switch.

Precedence: the ``PORCHLAMP_DATA_ROOT`` environment variable when set (a scratch
directory such as ``~/Projects/cvi-scratch/data``); otherwise
``<repo root>/data``.
"""
from __future__ import annotations

import os
from pathlib import Path

#: The main checkout's own root (``porchlamp/paths.py`` -> repo root).
REPO_ROOT: Path = Path(__file__).resolve().parent.parent

#: The environment variable that overrides the default data root.
PORCHLAMP_DATA_ROOT_ENV: str = "PORCHLAMP_DATA_ROOT"


def data_root() -> Path:
    """Resolve the data root: ``PORCHLAMP_DATA_ROOT`` if set, else ``<repo>/data``.

    Read fresh on every call (no caching), so a test can set/unset the
    environment variable and observe the change immediately. A relative or
    ``~``-prefixed override is expanded and resolved to an absolute path.
    """
    override = os.environ.get(PORCHLAMP_DATA_ROOT_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return REPO_ROOT / "data"
