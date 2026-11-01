"""The snapshot's reference time (``as_of``) and the pre-flight that checks it (D2-A, D2-E).

``as_of`` is the instant every window of a snapshot is measured against (D-VER-3). Two guards
constrain it, and this module computes the earliest instant that satisfies both, then checks any
given instant against them, before anything is derived or built:

* the **cut guard**: ``pr_window`` records are collected once, and an uncut record covers the
  gate's window only when its own window opens at or before ``as_of`` minus 180 days
  (:func:`porchlamp.derive.gate_evidence.pr_window_coverage_in`). So ``as_of`` must not be earlier
  than any record's ``window_anchor``. A record that is cut anyway is acceptable only when every cut
  record stopped at one recorded cap (the export's rule, ``porchlamp/serve/report_pages.py``);
* the **14-day guard**: a ``releases`` or ``issues_counts`` record whose ``window_anchor`` is more
  than 14 days from ``as_of`` (either side) makes ``openssf_criticality`` a ``collector_failure``
  (:func:`porchlamp.collect.pike_signals.window_anchor_stale`). No publish step refuses that loss.

Let ``A`` be the newest ``window_anchor`` over the ``pr_window`` records of the repos ``derive --all``
reads (the candidates list), and ``R`` and ``I`` the newest ``releases`` and ``issues_counts`` anchors
among files anchored after ``A`` ("this cycle": on the phone, L2 and L3c always run after L1). Then
``as_of = max(A, R - 14 d, I - 14 d)``, rounded UP to the whole minute. Normally that is ``A`` rounded
up, which is the smallest valid value and so loses the least of every 180-day window. It never reads
``author_history`` or the end of collection, so the length of the contributor-history step does not
move it. It has no value when the lower bound is above the oldest this-cycle anchor plus 14 days, or
when a collector has no this-cycle file; the caller refuses to build then.

Everything here is pure: no wall clock, no printing, no writes. The CLI (``porchlamp.snapshot.cli``)
owns the output and the exit codes. The module reads each envelope once and keeps only what the guards
need, because a full data root's ``pr_window`` layer is far larger than memory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Union

from porchlamp.collect.pike_signals import parse_iso, window_anchor_stale
from porchlamp.core.constants import WINDOW_STALENESS_DAYS
from porchlamp.derive.gate_evidence import pr_window_coverage_in

__all__ = [
    "AsOfError",
    "AsOfPlan",
    "GuardResult",
    "RootScan",
    "check_as_of",
    "earliest_valid_as_of",
    "format_report",
    "iso",
    "parse_as_of",
    "passed",
    "plan_as_of",
    "scan_root",
]

PASS, FAIL, WARN, INFO = "PASS", "FAIL", "WARN", "INFO"

#: How many files or records a guard line lists by name.
_LISTED = 10
_STALENESS = timedelta(days=WINDOW_STALENESS_DAYS)
#: The ``pr_window`` fields the cut guard reads (``gates.pr_window_coverage`` and the export's cap rule).
_WINDOW_FIELDS = ("window_anchor", "window_days", "truncated", "cap")


class AsOfError(Exception):
    """The data root cannot be read at all: no directory, or no candidates file."""


@dataclass(frozen=True)
class GuardResult:
    """One guard's verdict. ``status`` is PASS, FAIL, WARN or INFO; ``items`` names up to ten files or
    records; ``caps`` (cut guard) are the distinct caps of the records that do not cover the window,
    ``None`` for a record that is cut but not at a recorded cap; ``counts`` are the numbers behind it."""

    guard: str
    status: str
    summary: str
    items: tuple[str, ...] = ()
    caps: tuple[Optional[int], ...] = ()
    counts: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class _Window:
    key: str
    anchor: Optional[datetime]
    document: Mapping[str, Any]


@dataclass(frozen=True)
class _Dated:
    key: str
    raw: str
    anchor: datetime


@dataclass(frozen=True)
class _History:
    key: str
    anchor: Optional[datetime]
    has_band: bool
    band: Optional[datetime]


@dataclass(frozen=True)
class RootScan:
    """What the guards need from one data root, for the repos its candidates file lists."""

    data_dir: Path
    candidates: int
    skipped_lines: int
    windows: tuple[_Window, ...]
    releases: tuple[_Dated, ...]
    issues_counts: tuple[_Dated, ...]
    histories: tuple[_History, ...]
    unreadable: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class _Cycle:
    """The files of one collector anchored after the newest ``pr_window`` anchor (at least one)."""

    count: int
    oldest: datetime
    newest: datetime


@dataclass(frozen=True)
class AsOfPlan:
    """The earliest valid ``as_of`` and how it was reached. ``earliest`` is ``None`` when there is none,
    and ``problem`` says why."""

    newest_windows: tuple[tuple[str, datetime], ...]
    anchor: Optional[datetime]
    releases: Optional[_Cycle]
    issues_counts: Optional[_Cycle]
    bound: Optional[str]
    lower: Optional[datetime]
    upper: Optional[datetime]
    earliest: Optional[datetime]
    problem: Optional[str]


def iso(moment: datetime) -> str:
    """``moment`` in UTC as ISO-8601 with ``+00:00``."""
    return _utc(moment).isoformat()


def parse_as_of(text: str) -> Optional[datetime]:
    """An ISO-8601 timestamp (``Z`` or an offset; a naive one is UTC), or ``None`` when it is not one."""
    return parse_iso(text)


def _utc(moment: datetime) -> datetime:
    return (
        moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
    ).astimezone(timezone.utc)


def _ceil_minute(moment: datetime) -> datetime:
    """``moment`` rounded up to the whole minute (kept as it is when it already is one)."""
    moment = _utc(moment)
    floor = moment.replace(second=0, microsecond=0)
    return floor if floor == moment else floor + timedelta(minutes=1)


def _repo(key: str) -> str:
    return key.replace("__", "/", 1)


def _candidate_keys(data_dir: Path) -> tuple[list[str], int]:
    """The envelope keys (``owner__name``) of the repos ``derive --all`` reads: ``raw/candidates.jsonl``,
    else ``candidates.jsonl``. Also the number of lines that name no repo (derive refuses those too)."""
    for path in (data_dir / "raw" / "candidates.jsonl", data_dir / "candidates.jsonl"):
        if path.is_file():
            break
    else:
        raise AsOfError(
            f"no candidates file: {data_dir / 'raw' / 'candidates.jsonl'} or {data_dir / 'candidates.jsonl'} "
            "lists the repos whose envelopes the guards read"
        )
    keys: dict[str, None] = {}
    skipped = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            row = None
        owner, name = (row.get("owner"), row.get("name")) if isinstance(row, dict) else (None, None)
        if isinstance(owner, str) and owner and isinstance(name, str) and name:
            keys[f"{owner}__{name}"] = None
        else:
            skipped += 1
    return list(keys), skipped


def _read(path: Path) -> tuple[Optional[Mapping[str, Any]], bool]:
    """``(document, unreadable)``: ``(None, False)`` for an absent file, ``(None, True)`` for one that is not
    a JSON object. Derive reads an unreadable envelope as absent, so the guards do too, and say so."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, False
    except OSError:
        return None, True
    try:
        document = json.loads(text)
    except ValueError:
        return None, True
    return (document, False) if isinstance(document, Mapping) else (None, True)


def _anchor(document: Mapping[str, Any]) -> tuple[Optional[str], Optional[datetime]]:
    """The recorded ``data.window_anchor`` and its parse."""
    data = document.get("data")
    raw = data.get("window_anchor") if isinstance(data, Mapping) else None
    return (raw, parse_iso(raw)) if isinstance(raw, str) else (None, None)


def _reduced_window_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """A ``pr_window`` envelope cut down to what the cut guard reads, so a whole data root fits in memory.

    :func:`pr_window_coverage_in` reads ``fetched_at``, ``truncated``, ``window_anchor``, ``window_days``
    and whether ``prs`` is a list, plus the ``createdAt`` of every PR of a record cut at its cap; the
    export's cap rule reads ``cap``. Nothing else of the record is kept."""
    data = document.get("data")
    reduced: dict[str, Any] = {"fetched_at": document.get("fetched_at"), "data": None}
    if not isinstance(data, Mapping):
        return reduced
    kept: dict[str, Any] = {name: data[name] for name in _WINDOW_FIELDS if name in data}
    prs = data.get("prs")
    if isinstance(prs, list):
        if data.get("truncated") is True:
            kept["prs"] = [
                {"createdAt": pr["createdAt"]}
                for pr in prs
                if isinstance(pr, Mapping) and "createdAt" in pr
            ]
        else:
            kept["prs"] = []
    reduced["data"] = kept
    return reduced


def scan_root(data_dir: Union[Path, str]) -> RootScan:
    """Read the four layers the guards look at, for the candidates only, one full parse per file.

    Raises :class:`AsOfError` when ``data_dir`` is not a directory or has no candidates file."""
    root = Path(data_dir)
    if not root.is_dir():
        raise AsOfError(f"{root} is not a directory")
    keys, skipped = _candidate_keys(root)
    raw = root / "raw"
    windows: list[_Window] = []
    releases: list[_Dated] = []
    issues: list[_Dated] = []
    histories: list[_History] = []
    unreadable: dict[str, list[str]] = {}
    for key in keys:
        for layer in ("pr_window", "releases", "issues_counts", "author_history"):
            document, bad = _read(raw / layer / f"{key}.json")
            if bad:
                unreadable.setdefault(layer, []).append(key)
            if document is None:
                continue
            recorded, anchor = _anchor(document)
            if layer == "pr_window":
                windows.append(_Window(key, anchor, _reduced_window_document(document)))
            elif layer == "author_history":
                data = document.get("data")
                band = data.get("dr90_band_anchor") if isinstance(data, Mapping) else None
                histories.append(
                    _History(
                        key,
                        anchor,
                        band is not None,
                        parse_iso(band) if isinstance(band, str) else None,
                    )
                )
            elif recorded is not None and anchor is not None:
                (releases if layer == "releases" else issues).append(_Dated(key, recorded, anchor))
    return RootScan(
        data_dir=root,
        candidates=len(keys),
        skipped_lines=skipped,
        windows=tuple(windows),
        releases=tuple(releases),
        issues_counts=tuple(issues),
        histories=tuple(histories),
        unreadable={layer: tuple(found) for layer, found in unreadable.items()},
    )


def _scanned(root: Union[Path, str, RootScan]) -> RootScan:
    return root if isinstance(root, RootScan) else scan_root(root)


def _newest_window_anchor(scan: RootScan) -> Optional[datetime]:
    anchors = [window.anchor for window in scan.windows if window.anchor is not None]
    return max(anchors) if anchors else None


def _this_cycle(files: Sequence[_Dated], after: datetime) -> Optional[_Cycle]:
    anchors = [file.anchor for file in files if file.anchor > after]
    return _Cycle(len(anchors), min(anchors), max(anchors)) if anchors else None


def plan_as_of(root: Union[Path, str, RootScan]) -> AsOfPlan:
    """The earliest valid ``as_of`` of ``root`` (see the module docstring) and how it was reached."""
    scan = _scanned(root)
    dated = sorted(
        ((window.anchor, window.key) for window in scan.windows if window.anchor is not None),
        reverse=True,
    )
    newest = tuple((key, anchor) for anchor, key in dated[:3])
    if not dated:
        return AsOfPlan(
            newest,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            "no pr_window record with a readable window_anchor",
        )
    anchor = max(a for a, _ in dated)
    releases = _this_cycle(scan.releases, anchor)
    issues = _this_cycle(scan.issues_counts, anchor)
    if releases is None or issues is None:
        absent = " or ".join(
            name
            for name, cycle in (("releases", releases), ("issues_counts", issues))
            if cycle is None
        )
        problem = (
            f"no this-cycle {absent} file (none anchored after the newest pr_window anchor {iso(anchor)}); "
            "the step that collects it has not run since the pull-request step"
        )
        return AsOfPlan(newest, anchor, releases, issues, None, None, None, None, problem)
    bounds = {
        "pr_window": anchor,
        "releases": releases.newest - _STALENESS,
        "issues_counts": issues.newest - _STALENESS,
    }
    bound = max(bounds, key=lambda name: bounds[name])
    lower = _ceil_minute(bounds[bound])
    oldest_name, oldest = min(
        (("releases", releases.oldest), ("issues_counts", issues.oldest)), key=lambda p: p[1]
    )
    upper = oldest + _STALENESS
    if lower > upper:
        problem = (
            f"the earliest instant every guard accepts, {iso(lower)}, is later than the latest, {iso(upper)} "
            f"(the oldest this-cycle {oldest_name} anchor {iso(oldest)} plus {WINDOW_STALENESS_DAYS} days)"
        )
        return AsOfPlan(newest, anchor, releases, issues, bound, lower, upper, None, problem)
    return AsOfPlan(newest, anchor, releases, issues, bound, lower, upper, lower, None)


def earliest_valid_as_of(root: Union[Path, str, RootScan]) -> Optional[datetime]:
    """The earliest instant every guard accepts, in UTC; ``None`` when there is none."""
    return plan_as_of(root).earliest


def _stale(raw_anchor: str, as_of: datetime) -> bool:
    """The engine's 14-day guard, unchanged (absolute difference, strict ``>``)."""
    return window_anchor_stale(raw_anchor, as_of)


def _cut_guard(scan: RootScan, as_of: datetime) -> GuardResult:
    if not scan.windows:
        return GuardResult("cut", FAIL, "no pr_window record to check")
    cut: list[tuple[str, Optional[datetime], Optional[int]]] = []
    for window in scan.windows:
        if pr_window_coverage_in(window.document, as_of)[0] is not False:
            continue
        data = window.document["data"]
        cap = data.get("cap")
        at_cap = (
            data.get("truncated") is True and isinstance(cap, int) and not isinstance(cap, bool)
        )
        cut.append((window.key, window.anchor, cap if at_cap else None))
    caps = tuple(sorted({cap for _, _, cap in cut}, key=lambda cap: -1 if cap is None else cap))
    items = tuple(
        f"{_repo(key)} anchor {iso(anchor) if anchor is not None else 'unknown'} cap {cap if cap is not None else 'none'}"
        for key, anchor, cap in sorted(cut, key=lambda row: row[0])[:_LISTED]
    )
    counts = {"records": len(scan.windows), "cut": len(cut)}
    shown = ", ".join("none" if cap is None else str(cap) for cap in caps)
    if not caps or (len(caps) == 1 and type(caps[0]) is int and caps[0] > 0):
        if not cut:
            return GuardResult(
                "cut",
                PASS,
                f"all {len(scan.windows)} pr_window records cover the window ending {iso(as_of)}",
                counts=counts,
            )
        return GuardResult(
            "cut",
            PASS,
            f"{len(cut)} of {len(scan.windows)} pr_window records do not cover the window ending {iso(as_of)}; "
            f"every one stopped at the one cap {shown}",
            items,
            caps,
            counts,
        )
    return GuardResult(
        "cut",
        FAIL,
        f"{len(cut)} of {len(scan.windows)} pr_window records do not cover the window ending {iso(as_of)}; caps {shown} "
        "(the export needs every cut record stopped at one recorded cap)",
        items,
        caps,
        counts,
    )


def _staleness_guards(
    layer: str, files: Sequence[_Dated], after: Optional[datetime], as_of: datetime
) -> list[GuardResult]:
    this = [file for file in files if after is not None and file.anchor > after]
    older = [file for file in files if not (after is not None and file.anchor > after)]
    stale_this = [file for file in this if _stale(file.raw, as_of)]
    stale_older = [file for file in older if _stale(file.raw, as_of)]
    results: list[GuardResult] = []
    if not this:
        results.append(
            GuardResult(
                layer,
                FAIL,
                f"no this-cycle {layer} file (none anchored after the newest pr_window anchor)",
                counts={"files": 0, "stale": 0},
            )
        )
    elif stale_this:
        results.append(
            GuardResult(
                layer,
                FAIL,
                f"{len(stale_this)} of {len(this)} this-cycle files are more than {WINDOW_STALENESS_DAYS} days from "
                f"{iso(as_of)}: openssf_criticality is collector_failure for those projects",
                tuple(f"{_repo(file.key)} {file.raw}" for file in stale_this[:_LISTED]),
                counts={"files": len(this), "stale": len(stale_this)},
            )
        )
    else:
        results.append(
            GuardResult(
                layer,
                PASS,
                f"0 of {len(this)} this-cycle files are more than "
                f"{WINDOW_STALENESS_DAYS} days from {iso(as_of)}",
                counts={"files": len(this), "stale": 0},
            )
        )
    if stale_older:
        results.append(
            GuardResult(
                f"{layer} fallback",
                WARN,
                f"{len(stale_older)} of {len(older)} older-cycle files are more than {WINDOW_STALENESS_DAYS} days from "
                f"{iso(as_of)}: those projects stay unmeasured for openssf_criticality (inherent to an overlay root)",
                tuple(f"{_repo(file.key)} {file.raw}" for file in stale_older[:_LISTED]),
                counts={"files": len(older), "stale": len(stale_older)},
            )
        )
    return results


def _history_info(scan: RootScan) -> GuardResult:
    histories = scan.histories
    anchors = [history.anchor for history in histories if history.anchor is not None]
    pr_anchor = {window.key: window.anchor for window in scan.windows}
    banded = [history for history in histories if history.has_band]
    differs = [
        history
        for history in banded
        if pr_anchor.get(history.key) is not None and history.band != pr_anchor[history.key]
    ]
    span = f", anchors {iso(min(anchors))} to {iso(max(anchors))}" if anchors else ""
    return GuardResult(
        "author_history",
        INFO,
        f"{len(histories)} files{span}; {len(banded)} carry a dr90_band_anchor, {len(differs)} of those differ from "
        "their pr_window anchor (1.2 refuses those)",
        tuple(_repo(history.key) for history in differs[:_LISTED]),
        counts={
            "files": len(histories),
            "with_band_anchor": len(banded),
            "band_anchor_differs": len(differs),
        },
    )


def _unreadable_warn(scan: RootScan) -> GuardResult:
    total = sum(len(keys) for keys in scan.unreadable.values())
    items = tuple(
        f"{layer}/{_repo(key)}" for layer, keys in scan.unreadable.items() for key in keys
    )[:_LISTED]
    return GuardResult(
        "unreadable",
        WARN,
        f"{total} envelope(s) are not a readable JSON object; derive reads them as absent",
        items,
        counts={layer: len(keys) for layer, keys in scan.unreadable.items()},
    )


def check_as_of(root: Union[Path, str, RootScan], as_of: datetime) -> tuple[GuardResult, ...]:
    """Every guard's verdict on ``as_of`` for ``root``: the cut guard, the 14-day guard for ``releases`` and
    ``issues_counts`` (a stale this-cycle file FAILs, a stale older-cycle file only WARNs), the
    ``author_history`` figures (INFO) and any unreadable envelope (WARN)."""
    scan = _scanned(root)
    when = _utc(as_of)
    after = _newest_window_anchor(scan)
    results = [_cut_guard(scan, when)]
    results.extend(_staleness_guards("releases", scan.releases, after, when))
    results.extend(_staleness_guards("issues_counts", scan.issues_counts, after, when))
    results.append(_history_info(scan))
    if scan.unreadable:
        results.append(_unreadable_warn(scan))
    return tuple(results)


def passed(results: Sequence[GuardResult]) -> bool:
    """True when no guard FAILs."""
    return not any(result.status == FAIL for result in results)


def _cycle_line(name: str, cycle: Optional[_Cycle]) -> str:
    if cycle is None:
        return f"  this-cycle {name}: none"
    return f"  this-cycle {name}: {cycle.count} files, anchors {iso(cycle.oldest)} to {iso(cycle.newest)}"


def format_report(
    scan: RootScan,
    plan: AsOfPlan,
    results: Sequence[GuardResult],
    as_of: Optional[datetime],
    given: bool,
) -> list[str]:
    """The lines the CLI prints: the as_of, how it was reached, then one line per guard."""
    lines: list[str] = []
    if as_of is not None:
        lines.append(f"as-of {iso(as_of)}" + (" (given)" if given else ""))
    if given:
        lines.append(
            f"earliest valid as-of {iso(plan.earliest)}"
            if plan.earliest is not None
            else f"earliest valid as-of: none ({plan.problem})"
        )
    elif plan.earliest is None:
        lines.append(f"no valid as_of: {plan.problem}")
    lines.append(
        f"  candidates: {scan.candidates} repos"
        + (f" ({scan.skipped_lines} candidate lines name no repo)" if scan.skipped_lines else "")
    )
    if plan.bound is not None and plan.earliest is not None:
        origin = {
            "pr_window": "the newest pr_window anchor",
            "releases": f"the newest this-cycle releases anchor minus {WINDOW_STALENESS_DAYS} days",
            "issues_counts": "the newest this-cycle issues_counts anchor minus "
            f"{WINDOW_STALENESS_DAYS} days",
        }[plan.bound]
        lines.append(f"  set by: {origin}, rounded up to the minute")
    if plan.newest_windows:
        lines.append(
            "  newest pr_window anchors: "
            + ", ".join(f"{_repo(key)} {iso(anchor)}" for key, anchor in plan.newest_windows)
        )
    lines.append(_cycle_line("releases", plan.releases))
    lines.append(_cycle_line("issues_counts", plan.issues_counts))
    if plan.upper is not None:
        lines.append(f"  latest valid as-of: {iso(plan.upper)}")
    for result in results:
        lines.append(f"{result.status} {result.guard}: {result.summary}")
        lines.extend(f"    - {item}" for item in result.items)
    return lines
