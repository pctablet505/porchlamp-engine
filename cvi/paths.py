"""Single resolution point for the on-disk data root (packet W2-8c).

Every script that reads or writes collected data (``scripts/gather.py``,
``scripts/derive.py``) resolves its root through :func:`data_root` so a
scratch data root can be substituted -- for a live gather/derive run against
a copy, never the main checkout's own ``data/`` (docs/v2/00-README.md
non-negotiables) -- without touching any collector's logic. The collectors
themselves (``cvi/collect/*``) already take a data/raw directory as an
explicit parameter from their caller; only the two scripts' own
module-level constants needed a switch.

Precedence: the ``CVI_DATA_ROOT`` environment variable when set (a scratch
directory such as ``~/Projects/cvi-scratch/W2-8c/data``); otherwise
``<repo root>/data``, unchanged from before this module existed.
"""
from __future__ import annotations

import os
from pathlib import Path

#: The main checkout's own root (``cvi/paths.py`` -> repo root).
REPO_ROOT: Path = Path(__file__).resolve().parent.parent

#: The environment variable that overrides the default data root.
CVI_DATA_ROOT_ENV: str = "CVI_DATA_ROOT"


def data_root() -> Path:
    """Resolve the data root: ``CVI_DATA_ROOT`` if set, else ``<repo>/data``.

    Read fresh on every call (no caching), so a test can set/unset the
    environment variable and observe the change immediately. A relative or
    ``~``-prefixed override is expanded and resolved to an absolute path;
    the default is returned exactly as before this module existed.
    """
    override = os.environ.get(CVI_DATA_ROOT_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return REPO_ROOT / "data"
