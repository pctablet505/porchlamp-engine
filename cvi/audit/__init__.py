"""CVI audit tools: coverage (W0-1), anchors, sensitivity and ties (W0-5), forward (W5-1).

Read-only analysis over a data directory or a ``cvi.db`` database.  The
public names live in the submodules and are re-exported lazily, so
``python -m cvi.audit.<submodule>`` stays free of double-import warnings.

Subcommands (run as modules; each has its own ``main``)
-------------------------------------------------------
coverage
    Missingness/coverage table for a data dir (``python -m cvi.audit.coverage``).
anchors
    D-VER-4 anchor relations on a database (``python -m cvi.audit.anchors``).
sensitivity
    Kendall tau-b under +/-5 pp pillar-weight perturbations, D-AGG-3
    (``python -m cvi.audit.sensitivity``).
ties
    Distinct composite values and tie-group sizes (``python -m cvi.audit.ties``).
"""

from __future__ import annotations

import importlib
from typing import Any

__version__ = "1.0.0"

_LAZY_EXPORTS: dict[str, tuple[str, ...]] = {
    "cvi.audit.anchors": (
        "ANCHOR_RELATIONS",
        "AnchorResult",
        "evaluate_anchors",
        "format_anchor_report",
    ),
    "cvi.audit.sensitivity": (
        "TAU_RELEASE_THRESHOLD",
        "PerturbationResult",
        "SensitivityReport",
        "kendall_tau_b",
        "perturb_pillar_weight",
        "sensitivity_report",
        "format_sensitivity_report",
    ),
    "cvi.audit.ties": (
        "TieGroup",
        "TieReport",
        "ties_report",
        "format_tie_report",
    ),
    "cvi.audit.coverage": (
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
    ),
    "cvi.audit.forward": (
        "GITHUB_API",
        "PER_PAGE",
        "COLLECT_DELAY_DAYS",
        "PR_WINDOW_END_DAYS",
        "DECISION_WINDOW_DAYS",
        "MERGE_WINDOW_DAYS",
        "RESPONSIVENESS_DECISION_RHO_MIN",
        "COMPOSITE_MERGE_RHO_MIN",
        "PILLAR_COLUMNS",
        "COMPOSITE_COLUMN",
        "SCORE_COLUMNS",
        "ForwardError",
        "SnapshotRef",
        "ScoreRow",
        "PRRecord",
        "RepoRates",
        "PairResult",
        "CriterionResult",
        "ForwardReport",
        "resolve_snapshot",
        "load_scores",
        "pr_labels",
        "filter_eligible",
        "first_timers_from_s0",
        "repo_label_rates",
        "spearman_rho",
        "roc_auc",
        "analyze",
        "live_repo_meta",
        "dry_run_report",
        "collect_forward",
        "load_pr_envelopes",
        "main",
    ),
}

_EXPORTS: tuple[str, ...] = tuple(
    name for names in _LAZY_EXPORTS.values() for name in names
)

__all__ = ["__version__", *_EXPORTS]


def __getattr__(name: str) -> Any:
    """Resolve a re-exported name from its submodule on first use."""
    for module_name, names in _LAZY_EXPORTS.items():
        if name in names:
            return getattr(importlib.import_module(module_name), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list:
    """List the module's attributes including the lazy re-exports."""
    return sorted(set(globals()) | set(_EXPORTS))
