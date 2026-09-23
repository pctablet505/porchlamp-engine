"""Extended v2 methodology document: history + cross-package additions
(`06` section 3 F).

``porchlamp.core.methodology.methodology_document`` is pure (``porchlamp.core`` +
stdlib only, `tests/core/test_import_graph.py`) and therefore cannot carry
three facts the Methodology screen and `docs/METHODOLOGY-v2.md` also need:

* the nine cross-snapshot manipulation detectors
  (:mod:`porchlamp.verify.detectors` -- not ``porchlamp.core``);
* the three audit-only "not hashed" release-gate process constants
  (:mod:`porchlamp.audit.anchors` / :mod:`porchlamp.audit.sensitivity`,
  `docs/v2/rfcs/README.md` "Process constants (not hashed, changed by RFC
  all the same)");
* the RFC changelog (a file read -- I/O does not belong in ``porchlamp.core``
  either).

:func:`methodology_with_history` merges the pure document with all three,
reading each straight from its one source module/file (no duplicated
literals: every value here is a plain attribute read or a changelog line,
never retyped).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from porchlamp.audit import anchors as _anchors
from porchlamp.audit import sensitivity as _sensitivity
from porchlamp.core.methodology import MethodologyDocument, methodology_document
from porchlamp.verify import detectors as _detectors

#: One entry per :data:`porchlamp.verify.detectors.DETECTOR_NAMES` row, in that
#: tuple's order (03 section 6 table order). ``thresholds`` keys are read
#: from the named module attribute at call time (never a hand-typed
#: number, mirroring ``porchlamp.core.methodology``'s own rule for its section);
#: ``direction`` is a short label for how the threshold(s) combine --
#: ``"magnitude"`` (an absolute move past one bound), ``"above"``/``"below"``
#: (one bound, one side), ``"compound"`` (two or more bounds combined with
#: AND/OR) or ``"boolean"`` (a pass-through of an upstream fact, no bound
#: at all).
_DETECTOR_BUILDERS: dict[str, Any] = {
    _detectors.DETECTOR_PARAM_JUMP: lambda: {
        "trigger": (
            "any parameter score moves by more than threshold_points between "
            "consecutive snapshots, with n at least min_n on both snapshots"
        ),
        "direction": "magnitude",
        "thresholds": {
            "threshold_points": _detectors.PARAM_JUMP_THRESHOLD_POINTS,
            "min_n": _detectors.PARAM_JUMP_MIN_N,
        },
    },
    _detectors.DETECTOR_SPEED_WITHOUT_DECISIONS: lambda: {
        "trigger": "TTFHR score >= ttfhr_score_min and DR_90 score <= dr90_score_max",
        "direction": "compound",
        "thresholds": {
            "ttfhr_score_min": _detectors.SPEED_TTFHR_SCORE_MIN,
            "dr90_score_max": _detectors.SPEED_DR90_SCORE_MAX,
        },
    },
    _detectors.DETECTOR_MERGES_WITHOUT_RETURN: lambda: {
        "trigger": (
            "MR_first score >= mr_first_score_min and (conversion or return) "
            "score <= conversion_or_return_score_max, with n at least min_n"
        ),
        "direction": "compound",
        "thresholds": {
            "mr_first_score_min": _detectors.MERGES_MR_FIRST_SCORE_MIN,
            "conversion_or_return_score_max": _detectors.MERGES_CONVERSION_OR_RETURN_SCORE_MAX,
            "min_n": _detectors.MERGES_WITHOUT_RETURN_MIN_N,
        },
    },
    _detectors.DETECTOR_CLOSED_UNREAD: lambda: {
        "trigger": (
            "share of external PRs closed unmerged with no substantive "
            "response ever recorded >= share_min"
        ),
        "direction": "above",
        "thresholds": {"share_min": _detectors.CLOSED_UNREAD_SHARE_MIN},
    },
    _detectors.DETECTOR_NEWCOMER_BURST: lambda: {
        "trigger": (
            "at least min_accounts eligible-looking, all-merged first-timer "
            "accounts created within any window_days-day span"
        ),
        "direction": "compound",
        "thresholds": {
            "min_accounts": _detectors.NEWCOMER_BURST_MIN_ACCOUNTS,
            "window_days": _detectors.NEWCOMER_BURST_WINDOW.days,
        },
    },
    _detectors.DETECTOR_STAR_BURST: lambda: {
        "trigger": "the StarScout lockstep penalty was applied to this repo at universe entry",
        "direction": "boolean",
        "thresholds": {},
    },
    _detectors.DETECTOR_LABEL_SPAM: lambda: {
        "trigger": (
            "newcomer-labelled issue count >= issue_count_min and newcomer "
            "conversion score <= conversion_score_max"
        ),
        "direction": "compound",
        "thresholds": {
            "issue_count_min": _detectors.LABEL_SPAM_ISSUE_COUNT_MIN,
            "conversion_score_max": _detectors.LABEL_SPAM_CONVERSION_SCORE_MAX,
        },
    },
    _detectors.DETECTOR_EF_DOMAIN_MISMATCH: lambda: {
        "trigger": "top commit email-domain share >= domain_share_min while elephant factor >= ef_min",
        "direction": "compound",
        "thresholds": {
            "domain_share_min": _detectors.EF_DOMAIN_SHARE_MIN,
            "ef_min": _detectors.EF_DOMAIN_MISMATCH_EF_MIN,
        },
    },
    _detectors.DETECTOR_SOURCE_DRIFT: lambda: {
        "trigger": "the live-sample canary currently holds this repo's row",
        "direction": "boolean",
        "thresholds": {},
    },
}


def _detector_entries() -> list[dict[str, Any]]:
    """One entry per :data:`porchlamp.verify.detectors.DETECTOR_NAMES`, in that
    tuple's order -- never a hand-maintained list of the nine names, so a
    tenth detector added to the real tuple fails this function loudly
    instead of silently missing from the document."""
    entries = []
    for name in _detectors.DETECTOR_NAMES:
        build = _DETECTOR_BUILDERS[name]
        entries.append({"name": name, **build()})
    return entries


#: The three process constants `docs/v2/rfcs/README.md` "Process constants"
#: rules out of ``constants.constants_dict()`` on purpose: release gates,
#: not per-repo scoring inputs. Read straight from their one source module
#: at call time (never retyped) in :func:`_process_constant_entries`.
_PROCESS_CONSTANTS: tuple[tuple[str, Any, str, str], ...] = (
    (
        "MIN_COVERAGE",
        _anchors,
        "porchlamp.audit.anchors",
        "D-VER-4 anchor-check minimum rating coverage (a release gate, not a per-repo score)",
    ),
    (
        "TAU_RELEASE_THRESHOLD",
        _sensitivity,
        "porchlamp.audit.sensitivity",
        "D-AGG-3's Kendall tau-b release bar for a +/-5pp weight perturbation",
    ),
    (
        "DEFAULT_PERTURBATION_PP",
        _sensitivity,
        "porchlamp.audit.sensitivity",
        "the +/-5pp weight-perturbation size the sensitivity report tests at",
    ),
)


def _process_constant_entries() -> list[dict[str, Any]]:
    return [
        {"name": name, "module": module_name, "value": getattr(module, name), "gates": gates}
        for name, module, module_name, gates in _PROCESS_CONSTANTS
    ]


#: One markdown table row of ``docs/v2/rfcs/CHANGELOG.md``:
#: ``| Date | RFC-or-genesis | Summary | hash_before -> hash_after |``.
#: The RFC cell is either a markdown link (``[RFC-0003](...)``\ ), the bare
#: word ``genesis``, or plain text; the hash cell is either two
#: backtick-quoted hex strings joined by ``->`` or a ``*(none)*`` sentinel
#: on the left for the genesis row (there is no "before" for the freeze
#: itself).
_ROW_RE = re.compile(r"^\|(.+)\|\s*$")
_HASH_RE = re.compile(r"`([0-9a-f]+)`")
_RFC_LINK_RE = re.compile(r"\[([^\]]+)\]")


def _parse_changelog_row(line: str) -> dict[str, Any] | None:
    match = _ROW_RE.match(line.strip())
    if match is None:
        return None
    cells = [cell.strip() for cell in match.group(1).split("|")]
    if len(cells) != 4:
        return None
    date, rfc_cell, summary, hash_cell = cells
    if date.lower() == "date" or set(date) <= {"-", ":"}:
        return None  # header or separator row
    link = _RFC_LINK_RE.search(rfc_cell)
    rfc = link.group(1) if link is not None else rfc_cell
    hashes = _HASH_RE.findall(hash_cell)
    if len(hashes) >= 2:
        hash_before, hash_after = hashes[0], hashes[1]
    elif len(hashes) == 1:
        # A one-hash row (the genesis row: "*(none)* -> `<hash>`") has no
        # "before" -- the single captured hash is the "after" side.
        hash_before, hash_after = None, hashes[0]
    else:
        hash_before, hash_after = None, None
    return {
        "date": date,
        "rfc": rfc,
        "summary": summary,
        "hash_before": hash_before,
        "hash_after": hash_after,
    }


def parse_changelog(changelog_path: str | Path) -> list[dict[str, Any]]:
    """Every RFC/genesis row of ``changelog_path`` (``docs/v2/rfcs/CHANGELOG.md``
    by convention), oldest first (the file itself lists newest first)."""
    text = Path(changelog_path).read_text(encoding="utf-8")
    rows = [row for line in text.splitlines() if (row := _parse_changelog_row(line)) is not None]
    return list(reversed(rows))


def methodology_with_history(changelog_path: str | Path) -> MethodologyDocument:
    """The extended v2 methodology document: :func:`methodology_document`
    plus ``detectors``, ``not_hashed_process_constants`` and ``rfc_log``
    (parsed from ``changelog_path``). The only impure/cross-package builder
    in this module -- see the module docstring for why."""
    document: MethodologyDocument = dict(methodology_document())  # type: ignore[assignment]
    document["detectors"] = _detector_entries()
    document["not_hashed_process_constants"] = _process_constant_entries()
    document["rfc_log"] = parse_changelog(changelog_path)
    return document
