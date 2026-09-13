"""``cvi audit detectors``: cross-snapshot detector counts (packet W3-2).

Runs every detector in :mod:`cvi.verify.detectors` over a set of scored
repositories and reports, per detector, how many repositories triggered it
and which ones. All detector *logic* lives in ``cvi.verify.detectors``
(pure functions over ``ScoreRecord``/``DetectorInputs``); this module is
I/O only -- reading the two input files, matching current against previous
by repo key, and rendering the report.

Input format (interim, pending the W4-2 v2 store). No v2 ``cvi.db`` schema
exists yet (``00-README.md`` status table: wave W4 "not started"), so
``--current``/``--previous`` each take a JSON file: a list of "row"
objects, one per repository::

    [
      {
        "key": "owner/name",
        "params": {
          "ttfhr_hours": {"score": 92.0, "measured": true, "n": 40},
          ...
        },
        "newcomer_issue_count": 12,
        "elephant_factor": 3,
        "top_email_domain_share": 0.6,
        "closed_unread_share": 0.1,
        "closed_unread_n": 12,
        "star_lockstep_penalty_applied": false,
        "source_drift_held": false,
        "newcomer_accounts": [
          {"login": "...", "account_created_at": "...", "all_prs_merged": true}
        ]
      },
      ...
    ]

Every field but ``key`` and ``params`` is optional and maps 1:1 onto a
:class:`~cvi.verify.detectors.DetectorInputs` field of the same name
(omitted means ``None``/empty, i.e. "not collected" -- the
unmeasured-never-fires rule). ``params`` need only list the five input ids
the detectors actually read (``ttfhr_hours``, ``dr90_decision_rate``,
``mr_first_rate``, ``newcomer_conversion``, ``rr90_return_rate``); missing
or absent entries are treated as unmeasured. This row shape is this
packet's own interim contract, not a new stored schema -- once W4-2 lands a
real reader of ``ScoreRecord`` rows from ``cvi.db`` should replace
:func:`load_rows`, and the rest of this module (matching, counting,
rendering) is unaffected.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from cvi.core.models import GateResult, ParamScore, RepoIdentity, ScoreRecord
from cvi.verify.detectors import DETECTOR_NAMES, DetectorInputs, NewcomerAccountSample, run_detectors

__version__ = "1.0.0"

#: Parameter input ids a detector row may carry (cvi.verify.detectors'
#: INPUT_* constants, restated here as the JSON row's allowed ``params``
#: keys -- R-2: this is the I/O layer's own list of *which* inputs it
#: reads off disk, not a second definition of what they mean).
ROW_PARAM_IDS: tuple[str, ...] = (
    "ttfhr_hours",
    "dr90_decision_rate",
    "mr_first_rate",
    "newcomer_conversion",
    "rr90_return_rate",
)

_HASH64_PLACEHOLDER = "0" * 64


@dataclass(frozen=True)
class DetectorRow:
    """One repository's detector-relevant facts at one snapshot: a minimal
    ``ScoreRecord`` (only ``params`` populated -- no detector reads
    anything else off a ``ScoreRecord``, see ``cvi.verify.detectors``'s
    module docstring) plus the raw ``DetectorInputs`` for that repo."""

    key: str
    record: ScoreRecord
    inputs: DetectorInputs


def _param_score_from_json(payload: Mapping[str, Any]) -> ParamScore:
    measured = bool(payload.get("measured", False))
    score = payload.get("score")
    return ParamScore(
        score=None if score is None else float(score),
        measured=measured,
        reason=payload.get("reason"),
        n=int(payload.get("n", 0)),
        method=payload.get("method"),
    )


#: Placeholder ``RepoIdentity`` fields the interim row format does not
#: carry (ecosystem/archived/mirror/fork/license/branch): none of the nine
#: detectors read ``ScoreRecord.repo`` (see ``cvi.verify.detectors``'s
#: module docstring -- every predicate reads only ``.params`` and the
#: separate ``DetectorInputs``), so only ``owner``/``name`` need to be
#: real. W2-10 made ``repo`` a required ``ScoreRecord`` field.
_PLACEHOLDER_ECOSYSTEM = "unknown"


def _repo_identity_from_key(key: str) -> RepoIdentity:
    """Build a real ``RepoIdentity`` from a row's ``"owner/name"`` key.

    Raises ``ValueError`` naming the key when it does not contain exactly
    the one ``"/"`` the documented row format requires (R-1: assert
    values, not shapes -- a malformed key must not silently become a
    single-part owner or name)."""
    parts = key.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f'row key must be "owner/name", got {key!r}')
    owner, name = parts
    return RepoIdentity(
        owner=owner,
        name=name,
        ecosystem=_PLACEHOLDER_ECOSYSTEM,
        archived=False,
        mirror_url=None,
        fork=False,
        license_spdx=None,
        default_branch=None,
    )


def _minimal_score_record(repo: RepoIdentity, params: Mapping[str, ParamScore]) -> ScoreRecord:
    """A ``ScoreRecord`` carrying a real ``repo`` and ``params``; every
    other field is a neutral placeholder (see :class:`DetectorRow`)."""
    return ScoreRecord(
        repo=repo,
        snapshot_id="",
        benchmark_version="",
        constants_hash=_HASH64_PLACEHOLDER,
        raw_hash=_HASH64_PLACEHOLDER,
        gate=GateResult(eligible=True, reason=None, routed=None, undecided=()),
        params=dict(params),
        pillars={},
        composite=None,
        coverage=0.0,
        tier=None,
        leverage=None,
        friction=None,
        quadrant=None,
        persona_gates={},
        persona_composites={},
        tie_key=(),
        tie_group=None,
        rank=None,
        suspicion=[],
    )


def _newcomer_accounts_from_json(payload: Sequence[Mapping[str, Any]]) -> tuple[NewcomerAccountSample, ...]:
    return tuple(
        NewcomerAccountSample(
            login=str(entry["login"]),
            account_created_at=str(entry["account_created_at"]),
            all_prs_merged=bool(entry["all_prs_merged"]),
        )
        for entry in payload
    )


def _row_from_json(payload: Mapping[str, Any]) -> DetectorRow:
    key = str(payload["key"])
    raw_params = payload.get("params") or {}
    params = {
        input_id: _param_score_from_json(raw_params[input_id])
        for input_id in ROW_PARAM_IDS
        if input_id in raw_params
    }
    inputs = DetectorInputs(
        newcomer_issue_count=payload.get("newcomer_issue_count"),
        elephant_factor=payload.get("elephant_factor"),
        top_email_domain_share=payload.get("top_email_domain_share"),
        closed_unread_share=payload.get("closed_unread_share"),
        closed_unread_n=payload.get("closed_unread_n"),
        newcomer_accounts=_newcomer_accounts_from_json(payload.get("newcomer_accounts") or ()),
        star_lockstep_penalty_applied=payload.get("star_lockstep_penalty_applied"),
        source_drift_held=payload.get("source_drift_held"),
    )
    return DetectorRow(key=key, record=_minimal_score_record(_repo_identity_from_key(key), params), inputs=inputs)


def load_rows(path: "Path | str") -> dict[str, DetectorRow]:
    """Load the interim JSON row format (see the module docstring) into
    ``key -> DetectorRow``. Raises ``FileNotFoundError`` for a missing
    file, ``json.JSONDecodeError`` for invalid JSON, and ``KeyError`` for a
    row missing its required ``key`` field."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = [_row_from_json(entry) for entry in document]
    return {row.key: row for row in rows}


@dataclass(frozen=True)
class DetectorReport:
    """Per-detector counts and flagged repository keys over one run.

    ``counts`` and ``flagged`` are keyed by every name in
    :data:`~cvi.verify.detectors.DETECTOR_NAMES`, including zero-count
    detectors (so a caller never needs a ``.get(name, 0)``). ``total_repos``
    is the number of repositories in ``current`` the report was computed
    over."""

    total_repos: int
    counts: dict[str, int]
    flagged: dict[str, tuple[str, ...]]


def build_report(
    current: Mapping[str, DetectorRow], previous: Mapping[str, DetectorRow]
) -> DetectorReport:
    """Run every detector over each repository in ``current``, matching
    ``previous`` by key (a repository absent from ``previous`` is scored
    with ``previous=None``: the same-snapshot detectors still run,
    ``param_jump`` cannot fire -- see
    ``cvi.verify.detectors.detect_param_jump``)."""
    flagged: dict[str, list[str]] = {name: [] for name in DETECTOR_NAMES}
    for key in sorted(current):
        row = current[key]
        previous_record = previous[key].record if key in previous else None
        for name in run_detectors(row.record, previous_record, row.inputs):
            flagged[name].append(key)
    return DetectorReport(
        total_repos=len(current),
        counts={name: len(keys) for name, keys in flagged.items()},
        flagged={name: tuple(keys) for name, keys in flagged.items()},
    )


def format_report(report: DetectorReport) -> str:
    """Render the report as plain text: one line per detector, repos
    counted first, ordered by :data:`~cvi.verify.detectors.DETECTOR_NAMES`
    (03 section 6 table order) so the output order never depends on the
    input's key order."""
    lines = [
        "CVI cross-snapshot detector report",
        f"repositories scored: {report.total_repos}",
    ]
    for name in DETECTOR_NAMES:
        count = report.counts[name]
        line = f"{name}: {count}"
        if count:
            line += " (" + ", ".join(report.flagged[name]) + ")"
        lines.append(line)
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run ``cvi audit detectors``; returns the process exit code.

    Exits 0 when the report completes; exits 2 on a missing/malformed
    input file."""
    parser = argparse.ArgumentParser(
        description="Cross-snapshot manipulation-detector counts (03 section 6)."
    )
    parser.add_argument("--current", required=True, help="path to the current-snapshot rows JSON")
    parser.add_argument("--previous", default=None, help="path to the previous-snapshot rows JSON")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)
    try:
        current = load_rows(args.current)
        previous = load_rows(args.previous) if args.previous else {}
    except (OSError, ValueError, KeyError) as error:
        print(f"cannot load detector rows: {error}", file=sys.stderr)
        return 2
    report = build_report(current, previous)
    if args.json:
        print(json.dumps({
            "total_repos": report.total_repos,
            "counts": report.counts,
            "flagged": {name: list(keys) for name, keys in report.flagged.items()},
        }, indent=2))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
