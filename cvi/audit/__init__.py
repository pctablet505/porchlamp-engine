"""Coverage and missingness audit tools (packet W0-1).

Read-only analysis of a data directory: what each input actually carries, for
how many repositories, and which parameters are constant on real data.  The
public names live in :mod:`cvi.audit.coverage` and are re-exported lazily, so
``python -m cvi.audit.coverage`` stays free of double-import warnings.
"""

from __future__ import annotations

import importlib
from typing import Any

__version__ = "1.0.0"

_EXPORTS = (
    "COLLECTORS",
    "GITHUB_KEYED_COLLECTORS",
    "RECORDED_PR_CAP",
    "AuditReport",
    "InputStat",
    "RepoRow",
    "RepoEvidence",
    "SPECS",
    "audit_universe",
    "discover_repos",
    "format_report",
    "main",
)

__all__ = ["__version__", *_EXPORTS]


def __getattr__(name: str) -> Any:
    """Resolve a re-exported name from :mod:`cvi.audit.coverage` on first use."""
    if name in _EXPORTS:
        return getattr(importlib.import_module("cvi.audit.coverage"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list:
    """List the module's attributes including the lazy re-exports."""
    return sorted(set(globals()) | set(_EXPORTS))
