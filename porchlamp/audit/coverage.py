"""Coverage / missingness audit for a Porchlamp data directory.

The tool answers one question for any directory shaped like ``data/`` (or a
copied sample such as
``~/Projects/cvi-scratch/sample-200``): which inputs actually carry
information, and for how many repositories?  It prints the table described in
``docs/v2/01-FINDINGS-2026-09-12.md`` section B: per input, missing rate,
derived rate, distinct values, zero fraction and quantiles; per repository, PR
count, external PR count, latency samples and days spanned; plus the
black-hole gate count with its open-issue denominator histogram.

Definitions (the audit's contract; the report repeats them)
-----------------------------------------------------------
universe
    The repository keys found under ``raw/<collector>/`` (GitHub keyed,
    filename ``{owner}__{repo}.json``) unioned with ``derived/``.  A sample
    manifest (``manifest.json``) may pin the universe and the exact envelope
    per collector; it wins when present.
missing
    A value is missing for a repository when the key is absent, the value is
    ``None``, a list-valued input is empty, the envelope file does not exist,
    or -- for a derived-bundle field -- the bundle's ``confidence`` map
    declares that field ``"missing"``.  Absent and null are the same fact.
present
    ``universe - missing``; ``missing_rate = missing / universe``.
derived
    Among present values of a derived-bundle field, the share whose confidence
    state is ``"derived"`` (computed by the deriver from a null upstream).
    ``derived_rate`` is ``None`` -- printed ``n/a`` -- for raw-envelope
    inputs, which carry no confidence state.
zero_fraction
    Among present values, the share equal to ``0`` (numbers) or ``False``
    (booleans); ``None`` for string inputs.
quantiles
    ``min``, ``q1``, ``median``, ``q3``, ``max`` over present numeric values,
    linear interpolation between close ranks.
black hole
    ``(quality_score missing or quality_score < 40) and (open_issues missing
    or open_issues < 10)``, where ``quality_score`` is the GFI overall score
    when the collector records one, else the OpenSSF Scorecard overall score.
    The denominator histogram buckets ``open_issues`` into
    ``0 / 1-9 / 10-99 / 100+ / missing``.
days_spanned
    Whole days between the oldest and newest PR ``created_at``.
external
    A PR whose derived record sets ``is_external``, else one whose author
    association is not ``OWNER``, ``MEMBER``, ``COLLABORATOR`` or ``APP``.

The library never writes to disk.  :func:`main` is the CLI layer and the only
place that touches stdout.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from porchlamp import __version__ as PORCHLAMP_VERSION

__version__ = "1.0.0"

#: Original v1 collector bucket directories under ``raw/``.
V1_COLLECTORS: Tuple[str, ...] = (
    "actions_runs",
    "commits_180d",
    "contributors",
    "depsdev",
    "downloads",
    "gfi",
    "github_meta",
    "pr_events",
    "pr_reviews",
    "scorecard",
    "tree",
)

#: Additional v2 collector bucket directories under ``raw/``.
V2_COLLECTORS: Tuple[str, ...] = (
    "repo_object",
    "commits_365d",
    "dependents",
    "pr_window",
    "releases",
    "issues_counts",
    "runs",
    "governance",
    "author_history",
    "universe_entry",
)

#: Collector bucket directories under ``raw/``.
COLLECTORS: Tuple[str, ...] = V1_COLLECTORS + V2_COLLECTORS
ALL_COLLECTORS: Tuple[str, ...] = COLLECTORS

#: Collectors keyed by GitHub ``owner__repo`` rather than ``ecosystem__package``.
GITHUB_KEYED_COLLECTORS: Tuple[str, ...] = tuple(name for name in COLLECTORS if name != "downloads")

#: Associations treated as inside the project (01-FINDINGS section B, F5).
INTERNAL_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR", "APP"})

BLACK_HOLE_SCORE = 40.0
BLACK_HOLE_DENOMINATOR = 10

#: PR records the collector keeps per repository: it stores the newest PRs, so a
#: repo at the cap has a window, not a period (01-FINDINGS section B, F4).
RECORDED_PR_CAP = 30

#: Sentinels are per-value: ``None`` is a recorded null, this is no record.
_MISSING = object()


def _read_json(path: Path) -> Optional[Any]:
    """Read a JSON file, returning ``None`` when it is absent or unreadable."""
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp (``Z`` suffix accepted) as UTC."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _dig(payload: Any, dotted: str) -> Any:
    """Walk ``a.b.c`` through dicts, returning :data:`_MISSING` when absent."""
    current = payload
    for part in dotted.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return _MISSING
    return current


def _quantile(sorted_values: Sequence[float], fraction: float) -> float:
    """Quantile with linear interpolation between close ranks."""
    count = len(sorted_values)
    if count == 1:
        return float(sorted_values[0])
    position = fraction * (count - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    span = position - lower
    return float(sorted_values[lower]) + span * (float(sorted_values[upper]) - float(sorted_values[lower]))


class RepoEvidence:
    """One repository's envelopes plus its derived bundle.

    Parameters
    ----------
    repo:
        Repository id in ``owner/repo`` form.
    data_dir:
        Directory containing ``raw/`` and ``derived/``.
    files:
        Optional collector-name to relative-path map from a sample manifest,
        preferred over the on-disk naming convention when supplied.
    """

    def __init__(self, repo: str, data_dir: Path, files: Optional[Dict[str, str]] = None) -> None:
        self.repo = repo
        self.data_dir = data_dir
        self._files = dict(files or {})
        self._cache: Dict[str, Any] = {}
        self.owner, _, self.name = repo.partition("/")
        if not self.name:
            self.name = self.owner
        derived = _read_json(data_dir / "derived" / f"{self.owner}__{self.name}.json")
        if derived is None:
            derived = _read_json(data_dir / "bundles" / f"{self.owner}__{self.name}.json")
        self.derived: Optional[dict] = derived if isinstance(derived, dict) else None
        self.meta: dict = (self.derived or {}).get("meta") or {}
        self.confidence: dict = (self.derived or {}).get("confidence") or {}

    @property
    def ecosystem(self) -> str:
        """Package ecosystem recorded by the deriver, else ``unknown``."""
        value = self.meta.get("ecosystem")
        if not isinstance(value, str) or not value:
            value = ((self.derived or {}).get("repo") or {}).get("ecosystem")
        if not isinstance(value, str) or not value:
            univ = self.envelope("universe_entry")
            if isinstance(univ, dict):
                value = univ.get("ecosystem")
        return value if isinstance(value, str) and value else "unknown"

    def envelope(self, collector: str) -> Optional[Any]:
        """Return a collector envelope's ``data`` payload, or ``None``."""
        if collector in self._cache:
            return self._cache[collector]
        envelope: Any = None
        relative = self._files.get(collector)
        if relative:
            envelope = _read_json(self.data_dir / relative)
        else:
            candidates = []
            if collector == "downloads":
                candidates.append(self.data_dir / "raw" / collector / f"{self.ecosystem}__{self.name}.json")
            candidates.append(self.data_dir / "raw" / collector / f"{self.owner}__{self.name}.json")
            for path in candidates:
                found = _read_json(path)
                if found is not None:
                    envelope = found
                    break
        if isinstance(envelope, dict) and "data" in envelope:
            payload = envelope["data"]
        else:
            payload = envelope
        self._cache[collector] = payload
        return payload

    def meta_value(self, key: str) -> Any:
        """Derived-bundle field, honouring its declared confidence state."""
        if self.confidence.get(key) == "missing":
            return _MISSING
        if not self.derived:
            return _MISSING
        return _dig(self.meta, key)

    def confidence_state(self, key: str) -> Optional[str]:
        """``verified`` / ``derived`` / ``missing`` recorded for a field."""
        state = self.confidence.get(key)
        return state if isinstance(state, str) else None

    def pr_records(self) -> List[dict]:
        """PR records from the derived bundle, falling back to raw ``pr_events`` or v2 ``pr_window``."""
        events = self.derived.get("pr_events") if self.derived else None
        if isinstance(events, list) and events:
            return [event for event in events if isinstance(event, dict)]
        pr_win = self.derived.get("pr_window") if self.derived else None
        if isinstance(pr_win, dict) and isinstance(pr_win.get("events"), list) and pr_win["events"]:
            return [event for event in pr_win["events"] if isinstance(event, dict)]
        raw_events = self.envelope("pr_events")
        if isinstance(raw_events, list):
            return [event for event in raw_events if isinstance(event, dict)]
        raw_pr_win = self.envelope("pr_window")
        if isinstance(raw_pr_win, dict) and isinstance(raw_pr_win.get("events"), list):
            return [event for event in raw_pr_win["events"] if isinstance(event, dict)]
        return []

    def latency_hours(self) -> List[float]:
        """First-response latencies in hours measurable from this repo's PRs."""
        samples: List[float] = []
        for event in self.pr_records():
            created = _parse_timestamp(event.get("created_at"))
            responded = _parse_timestamp(event.get("first_human_review_at") or event.get("first_response_at"))
            if created is None or responded is None:
                continue
            hours = (responded - created).total_seconds() / 3600.0
            if hours >= 0:
                samples.append(hours)
        return samples

    def is_external(self, event: dict) -> bool:
        """Whether a PR record is an outside contribution."""
        flag = event.get("is_external")
        if isinstance(flag, bool):
            return flag
        association = event.get("author_association")
        if isinstance(association, str) and association:
            return association.upper() not in INTERNAL_ASSOCIATIONS
        return False

    def quality_score(self) -> Any:
        """GFI overall score when recorded, else the Scorecard overall score."""
        score = _dig(self.envelope("gfi"), "overall.score")
        if score is _MISSING or score is None:
            score = _dig(self.envelope("scorecard"), "score")
        if score is _MISSING or score is None:
            score = _dig(self.meta, "scorecard_score")
        if score is _MISSING or score is None:
            return _MISSING
        try:
            return float(score)
        except (TypeError, ValueError):
            return _MISSING

    def open_issues(self) -> Any:
        """Open-issue count from the first collector that recorded one."""
        for collector in ("github_meta", "depsdev"):
            value = _dig(self.envelope(collector), "open_issues")
            if value is not _MISSING and value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return _MISSING


@dataclass(frozen=True)
class _Spec:
    """One audited input: display name, human-readable source, extractor."""

    name: str
    source: str
    extract: Callable[[RepoEvidence], Any]
    confidence_key: Optional[str] = None


@dataclass(frozen=True)
class InputStat:
    """Audit statistics for one input across the audited universe."""

    name: str
    source: str
    n: int
    present: int
    missing: int
    missing_rate: float
    derived: int
    derived_rate: Optional[float]
    distinct: int
    zero_fraction: Optional[float]
    constant: bool
    numeric: bool
    min: Optional[float]
    q1: Optional[float]
    median: Optional[float]
    q3: Optional[float]
    max: Optional[float]
    mean: Optional[float]

    def to_dict(self) -> dict:
        """JSON-serialisable form of this row."""
        return dict(self.__dict__)


@dataclass(frozen=True)
class RepoRow:
    """Per-repository evidence counts for the PR-activity table."""

    repo: str
    ecosystem: str
    prs: int
    external: int
    latency_samples: int
    days_spanned: Optional[int]
    open_issues: Optional[float]
    quality_score: Optional[float]
    black_hole: bool
    open_external_prs: int

    def to_dict(self) -> dict:
        """JSON-serialisable form of this row."""
        return dict(self.__dict__)


@dataclass(frozen=True)
class AuditReport:
    """The whole audit: header, input rows, per-repo rows and gate tables."""

    data_dir: str
    n_repos: int
    inputs: Tuple[InputStat, ...]
    repos: Tuple[RepoRow, ...]
    black_hole_count: int
    black_hole_rate: float
    denominator_histogram: Dict[str, int]
    denominator_mean: Optional[float]
    gfi_envelopes: int
    gfi_all_zero: int
    gfi_checks_total: int
    gfi_checks_null: int
    manifest_used: bool
    pr_total: int
    pr_external: int
    pr_with_response: int
    pr_merged: int
    repos_at_recorded_cap: int
    open_external_histogram: Dict[str, int]
    black_holes_open_external_le3: int
    black_holes_open_external_le5: int
    days_spanned_quantiles: Dict[str, float]
    median_latency_samples: Optional[float]

    def row(self, name: str) -> InputStat:
        """Look up an input row by name."""
        for stat in self.inputs:
            if stat.name == name:
                return stat
        raise KeyError(name)

    def to_dict(self) -> dict:
        """JSON-serialisable form of the report."""
        return {
            "tool": f"porchlamp/audit/coverage.py v{__version__} (porchlamp v{PORCHLAMP_VERSION})",
            "data_dir": self.data_dir,
            "n_repos": self.n_repos,
            "manifest_used": self.manifest_used,
            "inputs": [stat.to_dict() for stat in self.inputs],
            "repos": [row.to_dict() for row in self.repos],
            "black_hole": {
                "count": self.black_hole_count,
                "rate": self.black_hole_rate,
                "denominator_histogram": dict(self.denominator_histogram),
                "denominator_mean": self.denominator_mean,
            },
            "gfi": {
                "envelopes": self.gfi_envelopes,
                "all_zero": self.gfi_all_zero,
                "checks_total": self.gfi_checks_total,
                "checks_null": self.gfi_checks_null,
            },
            "prs": {
                "total": self.pr_total,
                "external": self.pr_external,
                "with_response": self.pr_with_response,
                "merged": self.pr_merged,
                "repos_at_recorded_cap": self.repos_at_recorded_cap,
                "recorded_pr_cap": RECORDED_PR_CAP,
                "open_external_histogram": dict(self.open_external_histogram),
                "black_holes_open_external_le3": self.black_holes_open_external_le3,
                "black_holes_open_external_le5": self.black_holes_open_external_le5,
                "days_spanned_quantiles": dict(self.days_spanned_quantiles),
                "median_latency_samples": self.median_latency_samples,
            },
        }


def _meta_spec(key: str) -> _Spec:
    return _Spec(f"meta.{key}", f"derived/*.json meta.{key}", lambda ev, key=key: ev.meta_value(key), confidence_key=key)


def _raw_spec(name: str, collector: str, dotted: str) -> _Spec:
    def extract(ev: RepoEvidence) -> Any:
        value = _dig(ev.envelope(collector), dotted)
        if value is _MISSING or value is None:
            return _MISSING
        return value

    return _Spec(name, f"raw/{collector} {dotted}", extract)


def _count_spec(name: str, collector: str, dotted: str = "") -> _Spec:
    """Count the entries of a list payload, optionally nested under ``dotted``."""

    def extract(ev: RepoEvidence) -> Any:
        payload = ev.envelope(collector) if not dotted else _dig(ev.envelope(collector), dotted)
        if isinstance(payload, list):
            if not payload:
                return _MISSING
            return float(len(payload))
        return _MISSING

    label = f"raw/{collector} len(data{'' if not dotted else ' .' + dotted})"
    return _Spec(name, label, extract)


def _open_issues_spec() -> _Spec:
    return _Spec(
        "repo.open_issues",
        "raw/github_meta open_issues, else raw/depsdev open_issues",
        lambda ev: ev.open_issues(),
    )


def _latency_spec() -> _Spec:
    def extract(ev: RepoEvidence) -> Any:
        samples = ev.latency_hours()
        if not samples:
            return _MISSING
        return _quantile(sorted(samples), 0.5)

    return _Spec(
        "prs.first_response_latency_hours",
        "pr_events[].first_human_review_at minus created_at (median per repo)",
        extract,
    )


def _check_value(check: Any) -> Optional[float]:
    """Score carried by one GFI check record, or ``None`` when unrecorded."""
    if isinstance(check, dict):
        for key in ("score", "value", "points"):
            if check.get(key) is not None:
                value = check[key]
                return float(value) if isinstance(value, (int, float, bool)) else None
        return None
    return float(check) if isinstance(check, (int, float)) else None


def _gfi_checks(payload: Any) -> Optional[List[Any]]:
    """Return the GFI check list when the payload records one, else ``None``."""
    if isinstance(payload, dict) and isinstance(payload.get("checks"), list):
        return payload["checks"]
    return None


def _gfi_spec(name: str, kind: str) -> _Spec:
    def extract(ev: RepoEvidence) -> Any:
        payload = ev.envelope("gfi")
        if kind == "score":
            score = _dig(payload, "overall.score")
            if score is _MISSING or score is None:
                return _MISSING
            try:
                return float(score)
            except (TypeError, ValueError):
                return _MISSING
        checks = _gfi_checks(payload)
        if checks is None:
            return _MISSING
        values = [_check_value(check) for check in checks]
        if kind == "nonnull_checks":
            return float(sum(1 for value in values if value is not None))
        known = [value for value in values if value is not None]
        return all(value == 0 for value in known)

    return _Spec(name, f"raw/gfi {kind}", extract)


def _gfi_issue_spec(name: str, unclaimed: bool = False) -> _Spec:
    def extract(ev: RepoEvidence) -> Any:
        payload = ev.envelope("gfi")
        if not isinstance(payload, list) or not payload:
            return _MISSING
        if unclaimed:
            return float(sum(1 for issue in payload if isinstance(issue, dict) and issue.get("assignee") is None))
        return float(len(payload))

    return _Spec(name, f"raw/gfi list {'unclaimed' if unclaimed else 'issue'} count", extract)


def _tree_spec(name: str, markers: Tuple[str, ...]) -> _Spec:
    def extract(ev: RepoEvidence) -> Any:
        payload = ev.envelope("tree")
        if not isinstance(payload, dict) or not isinstance(payload.get("paths"), list):
            return _MISSING
        for path in payload["paths"]:
            if isinstance(path, str) and any(
                path.rsplit("/", 1)[-1].upper().startswith(marker) for marker in markers
            ):
                return True
        return False

    return _Spec(name, f"raw/tree paths start with {'/'.join(markers)}", extract)


def _pr_spec(name: str, external: bool = False) -> _Spec:
    def extract(ev: RepoEvidence) -> Any:
        records = ev.pr_records()
        if not records:
            return _MISSING
        if external:
            return float(sum(1 for record in records if ev.is_external(record)))
        return float(len(records))

    return _Spec(name, f"pr_events {'external' if external else 'count'} per repo", extract)


#: The audited inputs, in report order.  Rows mirror 01-FINDINGS section B.
SPECS: Tuple[_Spec, ...] = (
    _meta_spec("stars"),
    _raw_spec("github_meta.pushed_at", "github_meta", "pushed_at"),
    _raw_spec("github_meta.closed_count", "github_meta", "closed_count"),
    _open_issues_spec(),
    _raw_spec("github_meta.license_spdx", "github_meta", "license_spdx"),
    _raw_spec("github_meta.archived", "github_meta", "archived"),
    _raw_spec("github_meta.is_mirror", "github_meta", "is_mirror"),
    _meta_spec("forks"),
    _meta_spec("dependents_count"),
    _count_spec("depsdev.dependents", "depsdev", "dependents"),
    _count_spec("contributors.count", "contributors"),
    _meta_spec("monthly_downloads"),
    _raw_spec("downloads.monthly", "downloads", "monthly"),
    _meta_spec("scorecard_score"),
    _raw_spec("scorecard.score", "scorecard", "score"),
    _meta_spec("openssf_signals"),
    _meta_spec("active_nonbot_commits_180d"),
    _count_spec("commits_180d.commit_count", "commits_180d"),
    _count_spec("actions_runs.run_count", "actions_runs"),
    _meta_spec("elephant_factor"),
    _meta_spec("rr_90"),
    _meta_spec("mean_inline_comments"),
    _meta_spec("dx_ci_minutes"),
    _gfi_spec("gfi.score", "score"),
    _gfi_spec("gfi.all_zero", "all_zero"),
    _gfi_spec("gfi.nonnull_checks", "nonnull_checks"),
    _gfi_issue_spec("gfi.issue_count"),
    _gfi_issue_spec("gfi.unclaimed_issue_count", unclaimed=True),
    _latency_spec(),
    _pr_spec("prs.count"),
    _pr_spec("prs.external_count", external=True),
    _meta_spec("legal_ip_model"),
    _meta_spec("ai_posture"),
    _meta_spec("promotion_ladder"),
    _meta_spec("hard_ai_ban"),
    _meta_spec("off_platform_reviews"),
    _meta_spec("active_unclaimed_gfis"),
    _tree_spec("tree.license_present", ("LICENSE", "COPYING")),
    _tree_spec("tree.contributing_present", ("CONTRIBUTING",)),
    _tree_spec("tree.code_of_conduct_present", ("CODE_OF_CONDUCT",)),
    _tree_spec("tree.code_owners_present", ("CODEOWNERS",)),
    _tree_spec("tree.review_policy_present", ("GOVERNANCE", "SUPPORT", "REVIEW")),
    _tree_spec("tree.readme_present", ("README",)),
)


def _load_manifest(data_dir: Path) -> Optional[dict]:
    manifest = _read_json(data_dir / "manifest.json")
    return manifest if isinstance(manifest, dict) and isinstance(manifest.get("repos"), list) else None


def _stem_to_repo(stem: str) -> str:
    """Map an on-disk ``owner__repo`` stem back to ``owner/repo``."""
    owner, separator, name = stem.partition("__")
    return f"{owner}/{name}" if separator else stem


def discover_repos(data_dir: "str | os.PathLike[str]", manifest: Optional[dict] = None) -> Tuple[str, ...]:
    """Return the repository keys of a data dir, sorted.

    Keys come from the sample manifest when one is supplied, otherwise from
    the union of GitHub-keyed collector filenames, auto-discovered ``raw/``
    subdirectories, ``derived/`` filenames and ``bundles/`` filenames.
    """
    root = Path(data_dir)
    if manifest:
        keys = [entry.get("repo") for entry in manifest["repos"] if isinstance(entry, dict)]
        return tuple(sorted({key for key in keys if isinstance(key, str) and key}))
    found = set()
    collector_names = list(GITHUB_KEYED_COLLECTORS)
    raw_root = root / "raw"
    if raw_root.is_dir():
        for child in raw_root.iterdir():
            if child.is_dir() and child.name != "downloads" and child.name not in collector_names:
                collector_names.append(child.name)
    for collector in collector_names:
        directory = raw_root / collector
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if path.suffix == ".json":
                found.add(_stem_to_repo(path.stem))
    for folder_name in ("derived", "bundles"):
        folder = root / folder_name
        if folder.is_dir():
            for path in folder.iterdir():
                if path.suffix == ".json":
                    found.add(_stem_to_repo(path.stem))
    return tuple(sorted(found))


def _files_by_repo(manifest: Optional[dict]) -> Dict[str, Dict[str, str]]:
    """Per-repo collector-to-path map recorded by a sample manifest."""
    if not manifest:
        return {}
    mapping: Dict[str, Dict[str, str]] = {}
    for entry in manifest["repos"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("repo"), str):
            continue
        files = entry.get("files")
        if isinstance(files, dict):
            mapping[entry["repo"]] = {key: value for key, value in files.items() if isinstance(value, str)}
    return mapping


def _summarise(spec: _Spec, evidence: Sequence[RepoEvidence]) -> InputStat:
    """Compute one input row of the audit table."""
    n = len(evidence)
    values: List[Any] = []
    derived = 0
    for repo in evidence:
        value = spec.extract(repo)
        if value is _MISSING or value is None:
            continue
        values.append(value)
        if spec.confidence_key and repo.confidence_state(spec.confidence_key) == "derived":
            derived += 1
    present = len(values)
    numeric = bool(values) and all(isinstance(value, (int, float, bool)) for value in values)
    distinct = len({f"{type(value).__name__}:{value!r}" for value in values})
    zero_fraction: Optional[float] = None
    min_value = q1 = median = q3 = max_value = mean = None
    if numeric:
        numbers = sorted(float(value) for value in values)
        zero_fraction = sum(1 for number in numbers if number == 0.0) / present
        min_value, max_value = numbers[0], numbers[-1]
        q1, median, q3 = (_quantile(numbers, fraction) for fraction in (0.25, 0.5, 0.75))
        mean = statistics.fmean(numbers)
    derived_rate = None
    if spec.confidence_key:
        derived_rate = (derived / present) if present else 0.0
    return InputStat(
        name=spec.name,
        source=spec.source,
        n=n,
        present=present,
        missing=n - present,
        missing_rate=(n - present) / n if n else 0.0,
        derived=derived,
        derived_rate=derived_rate,
        distinct=distinct,
        zero_fraction=zero_fraction,
        constant=present > 0 and distinct == 1,
        numeric=numeric,
        min=min_value,
        q1=q1,
        median=median,
        q3=q3,
        max=max_value,
        mean=mean,
    )


def _repo_row(repo: RepoEvidence) -> RepoRow:
    """Compute the per-repository evidence row."""
    records = repo.pr_records()
    external = sum(1 for record in records if repo.is_external(record))
    stamps = [stamp for stamp in (_parse_timestamp(event.get("created_at")) for event in records) if stamp]
    days: Optional[int] = 0 if stamps else None
    if len(stamps) > 1:
        days = int((max(stamps) - min(stamps)).total_seconds() // 86400)
    issues = repo.open_issues()
    score = repo.quality_score()
    issues_value = None if issues is _MISSING else issues
    score_value = None if score is _MISSING else score
    fires = (score_value is None or score_value < BLACK_HOLE_SCORE) and (
        issues_value is None or issues_value < BLACK_HOLE_DENOMINATOR
    )
    open_external = sum(
        1 for record in records if repo.is_external(record) and not record.get("merged_at") and not record.get("closed_at")
    )
    return RepoRow(
        repo=repo.repo,
        ecosystem=repo.ecosystem,
        prs=len(records),
        external=external,
        latency_samples=len(repo.latency_hours()),
        days_spanned=days,
        open_issues=issues_value,
        quality_score=float(score_value) if score_value is not None else None,
        black_hole=bool(fires),
        open_external_prs=open_external,
    )


EXTERNAL_BUCKETS = ("0", "1-2", "3-5", "6-9", "10+")


def _external_bucket(value: int) -> str:
    """Bucket an open-external-PR denominator the way 01-FINDINGS F6 counts it."""
    if value <= 0:
        return "0"
    if value <= 2:
        return "1-2"
    if value <= 5:
        return "3-5"
    if value <= 9:
        return "6-9"
    return "10+"


def _denominator_bucket(value: Optional[float]) -> str:
    """Bucket an open-issue denominator the way 01-FINDINGS F6 reports it."""
    if value is None:
        return "missing"
    if value <= 0:
        return "0"
    if value < 10:
        return "1-9"
    if value < 100:
        return "10-99"
    return "100+"


def audit_universe(data_dir: "str | os.PathLike[str]", repos: Optional[Sequence[str]] = None) -> AuditReport:
    """Audit every input of every repository in a data dir.

    Parameters
    ----------
    data_dir:
        A directory containing ``raw/<collector>/*.json`` and ``derived/*.json``
        -- the main checkout's ``data/`` or a copied sample.
    repos:
        Restrict the audit to these repository keys.  The default audits
        everything the directory contains, or the sample manifest's list.

    Returns
    -------
    AuditReport
        Input rows, per-repo rows and the black-hole gate table.
    """
    root = Path(data_dir)
    manifest = _load_manifest(root)
    keys = tuple(repos) if repos is not None else discover_repos(root, manifest)
    files = _files_by_repo(manifest)
    evidence = [RepoEvidence(key, root, files.get(key)) for key in keys]
    rows = tuple(_repo_row(repo) for repo in evidence)
    denominators = [row.open_issues for row in rows]
    known = [value for value in denominators if value is not None]
    histogram = {bucket: 0 for bucket in ("0", "1-9", "10-99", "100+", "missing")}
    for value in denominators:
        histogram[_denominator_bucket(value)] += 1
    gfi_envelopes = gfi_all_zero = checks_total = checks_null = 0
    for repo in evidence:
        payload = repo.envelope("gfi")
        checks = _gfi_checks(payload)
        if checks is not None:
            gfi_envelopes += 1
            values = [_check_value(check) for check in checks]
            checks_total += len(values)
            checks_null += sum(1 for value in values if value is None)
            if all(value in (None, 0) for value in values):
                gfi_all_zero += 1
        elif isinstance(payload, list) and payload:
            gfi_envelopes += 1
    black_hole = sum(1 for row in rows if row.black_hole)
    open_external_histogram = {bucket: 0 for bucket in EXTERNAL_BUCKETS}
    for row in rows:
        open_external_histogram[_external_bucket(row.open_external_prs)] += 1
    black_hole_repos = [row for row in rows if row.black_hole]
    spanned = sorted(float(row.days_spanned) for row in rows if row.days_spanned is not None)
    quantiles = (
        {key: _quantile(spanned, fraction) for key, fraction in (("q05", 0.05), ("q25", 0.25), ("q50", 0.5), ("q75", 0.75), ("q95", 0.95))}
        if spanned
        else {}
    )
    latency_counts = sorted(float(row.latency_samples) for row in rows)
    median_latency = _quantile(latency_counts, 0.5) if latency_counts else None
    pr_total = pr_external = pr_response = pr_merged = 0
    for repo in evidence:
        for event in repo.pr_records():
            pr_total += 1
            if repo.is_external(event):
                pr_external += 1
            if event.get("first_human_review_at") or event.get("first_response_at"):
                pr_response += 1
            if event.get("merged_at"):
                pr_merged += 1
    return AuditReport(
        data_dir=str(root),
        n_repos=len(keys),
        inputs=tuple(_summarise(spec, evidence) for spec in SPECS),
        repos=rows,
        black_hole_count=black_hole,
        black_hole_rate=black_hole / len(rows) if rows else 0.0,
        denominator_histogram=histogram,
        denominator_mean=statistics.fmean(known) if known else None,
        gfi_envelopes=gfi_envelopes,
        gfi_all_zero=gfi_all_zero,
        gfi_checks_total=checks_total,
        gfi_checks_null=checks_null,
        pr_total=pr_total,
        pr_external=pr_external,
        pr_with_response=pr_response,
        pr_merged=pr_merged,
        repos_at_recorded_cap=sum(1 for row in rows if row.prs >= RECORDED_PR_CAP),
        open_external_histogram=open_external_histogram,
        black_holes_open_external_le3=sum(1 for row in black_hole_repos if row.open_external_prs <= 3),
        black_holes_open_external_le5=sum(1 for row in black_hole_repos if row.open_external_prs <= 5),
        days_spanned_quantiles=quantiles,
        median_latency_samples=median_latency,
        manifest_used=bool(manifest) and repos is None,
    )


def _fmt(value: Optional[float], width: int, digits: int = 2) -> str:
    if value is None:
        return "n/a".rjust(width)
    return f"{value:.{digits}f}".rjust(width)


def _fmt_rate(value: Optional[float], width: int) -> str:
    if value is None:
        return "n/a".rjust(width)
    return f"{value * 100:.1f}".rjust(width)


def format_report(report: AuditReport) -> str:
    """Render the audit as fixed-width text (the Measure table)."""
    lines: List[str] = []
    lines.append(f"porchlamp/audit/coverage.py v{__version__} (porchlamp v{PORCHLAMP_VERSION}) -- {report.data_dir}")
    lines.append(
        f"repos: {report.n_repos}  |  universe: "
        f"{'sample manifest' if report.manifest_used else 'union of raw/ collectors and derived/'}"
        "  |  missing = key absent, null, empty list, or confidence=missing"
    )
    lines.append("")
    lines.append("PER-INPUT COVERAGE AND MISSINGNESS  (rates in %, quantiles in value units)")
    header = (
        f"{'input':<36} {'n':>5} {'present':>8} {'miss%':>7} {'deriv%':>7} "
        f"{'distinct':>8} {'zero%':>7} {'min':>9} {'q1':>9} {'median':>9} {'q3':>9} {'max':>10} {'mean':>11}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for stat in report.inputs:
        lines.append(
            f"{stat.name:<36} {stat.n:>5} {stat.present:>8} {_fmt_rate(stat.missing_rate, 7)} "
            f"{_fmt_rate(stat.derived_rate, 7)} {stat.distinct:>8} {_fmt_rate(stat.zero_fraction, 7)} "
            f"{_fmt(stat.min, 9)} {_fmt(stat.q1, 9)} {_fmt(stat.median, 9)} {_fmt(stat.q3, 9)} "
            f"{_fmt(stat.max, 10)} {_fmt(stat.mean, 11)}"
        )
    lines.append("")
    lines.append("CONSTANT / NEAR-CONSTANT PARAMETERS (<= 2 distinct values among present)")
    lines.append(f"{'input':<36} {'present':>8} {'distinct':>8} {'miss%':>7}  numeric")
    for stat in report.inputs:
        if stat.present and stat.distinct <= 2:
            lines.append(
                f"{stat.name:<36} {stat.present:>8} {stat.distinct:>8} "
                f"{_fmt_rate(stat.missing_rate, 7)}  {stat.numeric}"
            )
    lines.append("")
    lines.append("PER-REPOSITORY PR EVIDENCE")
    repo_header = (
        f"{'repo':<40} {'eco':<8} {'prs':>4} {'ext':>4} {'opex':>5} {'lat':>4} {'days':>5} "
        f"{'issues':>7} {'score':>6} {'blackhole':>9}"
    )
    lines.append(repo_header)
    lines.append("-" * len(repo_header))
    for row in report.repos:
        lines.append(
            f"{row.repo:<40} {row.ecosystem:<8} {row.prs:>4} {row.external:>4} {row.open_external_prs:>5} "
            f"{row.latency_samples:>4} {_fmt(row.days_spanned, 5, 0)} {_fmt(row.open_issues, 7, 0)} "
            f"{_fmt(row.quality_score, 6, 1)} {'yes' if row.black_hole else 'no':>9}"
        )
    lines.append("")
    lines.append(f"BLACK-HOLE GATE (quality score < {BLACK_HOLE_SCORE:.0f} and open issues < {BLACK_HOLE_DENOMINATOR})")
    lines.append(f"  fires: {report.black_hole_count} / {report.n_repos} repos ({report.black_hole_rate * 100:.1f}%)")
    lines.append(f"  denominator (open issues) mean: {_fmt(report.denominator_mean, 1)}")
    lines.append("  denominator histogram:")
    for bucket in ("0", "1-9", "10-99", "100+", "missing"):
        count = report.denominator_histogram[bucket]
        share = count / report.n_repos * 100 if report.n_repos else 0.0
        lines.append(f"    {bucket:<8} {count:>6} repos  {share:>5.1f}%")
    lines.append("")
    lines.append("PER-PR EVIDENCE (01-FINDINGS section B, F4 and F5 denominators)")
    share = lambda count: count / report.pr_total * 100 if report.pr_total else 0.0  # noqa: E731
    lines.append(
        f"  PR records: {report.pr_total} across {report.n_repos} repos "
        f"({report.pr_total / report.n_repos if report.n_repos else 0:.1f} per repo)"
    )
    lines.append(
        f"  repos at the recorded PR cap of {RECORDED_PR_CAP}: {report.repos_at_recorded_cap} / "
        f"{report.n_repos} ({report.repos_at_recorded_cap / report.n_repos * 100 if report.n_repos else 0:.1f}%)"
    )
    lines.append(f"  external PRs: {report.pr_external} / {report.pr_total} ({share(report.pr_external):.1f}%)")
    lines.append(
        f"  PRs with a first response recorded: {report.pr_with_response} / {report.pr_total} "
        f"({share(report.pr_with_response):.1f}%)"
    )
    lines.append(f"  merged PRs: {report.pr_merged} / {report.pr_total} ({share(report.pr_merged):.1f}%)")
    quantile_text = "  ".join(
        f"{key} {report.days_spanned_quantiles[key]:,.0f}" for key in ("q05", "q25", "q50", "q75", "q95")
        if key in report.days_spanned_quantiles
    )
    lines.append(f"  days spanned by the recorded PRs, per repo: {quantile_text or 'n/a'}")
    lines.append(f"  latency samples per repo, median: {_fmt(report.median_latency_samples, 1)}")
    lines.append("")
    lines.append("OPEN EXTERNAL PR DENOMINATOR (01-FINDINGS section B, F6)")
    for bucket in EXTERNAL_BUCKETS:
        count = report.open_external_histogram[bucket]
        rate = count / report.n_repos * 100 if report.n_repos else 0.0
        lines.append(f"    {bucket:<6} {count:>6} repos  {rate:>5.1f}%")
    lines.append(
        f"  black holes resting on <= 3 open external PRs: {report.black_holes_open_external_le3} / "
        f"{report.black_hole_count} ({report.black_holes_open_external_le3 / report.black_hole_count * 100 if report.black_hole_count else 0:.1f}%)"
    )
    lines.append(
        f"  black holes resting on <= 5 open external PRs: {report.black_holes_open_external_le5} / "
        f"{report.black_hole_count} ({report.black_holes_open_external_le5 / report.black_hole_count * 100 if report.black_hole_count else 0:.1f}%)"
    )
    lines.append("")
    lines.append("GFI SIGNAL")
    lines.append(
        f"  envelopes with evidence: {report.gfi_envelopes} / {report.n_repos}"
        f"  |  all-zero check envelopes: {report.gfi_all_zero}"
    )
    null_share = report.gfi_checks_null / report.gfi_checks_total * 100 if report.gfi_checks_total else 0.0
    lines.append(
        f"  checks: {report.gfi_checks_total} recorded, {report.gfi_checks_null} null ({null_share:.2f}% of checks)"
    )
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point: ``python -m porchlamp.audit.coverage --data-dir DIR``."""
    parser = argparse.ArgumentParser(description="Coverage and missingness audit of a Porchlamp data directory.")
    parser.add_argument("--data-dir", default=os.environ.get("PORCHLAMP_DATA_DIR", "data"), help="data dir or copied sample")
    parser.add_argument("--repos", default=None, help="comma-separated repo keys to audit instead of all")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)
    repos = None
    if args.repos:
        repos = [item.strip() for item in args.repos.split(",") if item.strip()]
    report = audit_universe(args.data_dir, repos=repos)
    sys.stdout.write((json.dumps(report.to_dict(), indent=1) if args.json else format_report(report)) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
