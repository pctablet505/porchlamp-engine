"""Forward-validation experiment for the v2 ranking (D-VAL-1).

The pre-registered contract is `docs/v2/validation/PREREG.md` (v1.0, frozen
at its self-hash): T is the S0 snapshot's `as_of` (D-VER-3), collection runs
at T + 90 d over GitHub PRs opened in (T, T + 60 d] on rated repos
(`is_stage0_disqualified = FALSE` in the S0 database), with labels
decision_30d (closed at or before created_at + 30 d) and merge_90d (merged
at or before T + 90 d), per-repo rates over eligible PRs (external and
first-timer as recorded in the S0 envelopes), and the pre-stated criteria
C1 Spearman(responsiveness, decision_rate) >= 0.40, C2 Spearman(overall_porchlamp,
merge_rate) >= 0.30. Point cost per repo: 1 meta call + 1 per PR
page; the dry-run bound reads the last page from the pagination Link header.

CLI (``python -m porchlamp.audit.forward`` until `porchlamp/audit/__init__.py` wires the
``forward`` subcommand): plan ``--snapshot S0``; dry run
``--snapshot S0 --collect --dry-run`` (``--offline`` skips live meta calls,
``--repos`` targets explicitly, ``--as-of`` fixes T without a snapshot);
collect ``--snapshot S0 --collect --fetched-at <ISO-8601 UTC>``
(``--fetched-at`` is required for real collection and unused for plan and
dry run; D-VER-3: the module owns no wall-clock default). The library
never writes outside ``--out``; :func:`main` is the only place that
touches stdout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AbstractSet, Any, Mapping, Optional, Sequence

from porchlamp.audit.coverage import INTERNAL_ASSOCIATIONS
from porchlamp.collect.envelope import read_envelope, write_envelope
from porchlamp.provenance import utc_now_iso
from porchlamp.snapshot.manifest import load_manifest

__version__ = "1.0.0"

GITHUB_API = "https://api.github.com"
PER_PAGE = 100
COLLECT_DELAY_DAYS = 90
PR_WINDOW_END_DAYS = 60
DECISION_WINDOW_DAYS = 30
MERGE_WINDOW_DAYS = 90
RESPONSIVENESS_DECISION_RHO_MIN = 0.40
COMPOSITE_MERGE_RHO_MIN = 0.30
PILLAR_COLUMNS: tuple[str, ...] = (
    "score_responsiveness",
    "score_criticality",
    "score_governance",
    "score_rigor_ai",
    "score_accessibility",
)
COMPOSITE_COLUMN = "overall_porchlamp"
SCORE_COLUMNS = PILLAR_COLUMNS + (COMPOSITE_COLUMN,)
_LABEL_INDEX = {"decision_rate": 0, "merge_rate": 1}
_PREREG = Path(__file__).resolve().parents[2] / "docs" / "v2" / "validation" / "PREREG.md"


class ForwardError(RuntimeError):
    """The forward experiment cannot proceed, with the reason inside."""


@dataclass(frozen=True)
class SnapshotRef:
    """A resolved S0 snapshot: identity, T, data dir and snapshot dir."""

    snapshot_id: str
    as_of: datetime
    data_dir: Path
    snap_dir: Path


@dataclass(frozen=True)
class ScoreRow:
    """One S0 database row: the repo, its gate status and its scores."""

    repo: str
    rated: bool
    scores: dict[str, float]


@dataclass(frozen=True)
class PRRecord:
    """One collected PR, as observed at collection time."""

    repo: str
    number: int
    author: str
    association: str
    created_at: datetime
    closed_at: Optional[datetime]
    merged_at: Optional[datetime]


@dataclass(frozen=True)
class RepoRates:
    """Label rates for one repo over its eligible PRs."""

    n_prs: int
    decision_rate: float
    merge_rate: float


@dataclass(frozen=True)
class PairResult:
    """Spearman and AUC of one score against one label, with reasons."""

    score: str
    label: str
    n_repos: int
    n_prs: int
    spearman: Optional[float]
    auc: Optional[float]
    rho_reason: Optional[str] = None
    auc_reason: Optional[str] = None


@dataclass(frozen=True)
class CriterionResult:
    """The verdict of one pre-stated criterion (D-VAL-1)."""

    name: str
    threshold: float
    value: Optional[float]
    verdict: str


@dataclass(frozen=True)
class ForwardReport:
    """The pre-registered analysis over one collection."""

    as_of: datetime
    n_repos_rated: int
    n_repos_in_analysis: int
    n_prs_eligible: int
    excluded: dict[str, int]
    pairs: tuple[PairResult, ...]
    criteria: tuple[CriterionResult, ...]

    def to_dict(self) -> dict[str, Any]:
        """JSON-able form of the report (the as_of datetime as ISO-8601)."""
        payload = asdict(self)
        payload["as_of"] = self.as_of.isoformat()
        return payload


def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp (``Z`` suffix accepted) as UTC, else None."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def resolve_snapshot(snapshot: str, data_dir: Optional[os.PathLike[str]] = None) -> SnapshotRef:
    """Resolve ``--snapshot`` to a :class:`SnapshotRef`.

    Accepts a path to a ``manifest.json`` (the data dir is inferred from the
    ``<data_dir>/snapshots/<id>/manifest.json`` layout unless given) or a
    snapshot id looked up under ``data_dir`` (default: ``data``).
    """
    path = Path(snapshot)
    if path.is_file():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        snap_dir = path.parent
        root = Path(data_dir) if data_dir is not None else path.parent.parent.parent
    else:
        root = Path(data_dir) if data_dir is not None else Path("data")
        manifest = load_manifest(root, snapshot)
        snap_dir = root / "snapshots" / snapshot
    as_of = _parse_ts(manifest.get("as_of"))
    if as_of is None:
        raise ForwardError(f"manifest has no parseable as_of: {path}")
    return SnapshotRef(str(manifest.get("snapshot_id", snap_dir.name)), as_of, root, snap_dir)


def _served_snapshot_id(conn: sqlite3.Connection, cols: set[str]) -> Optional[str]:
    """Return the active/latest snapshot ID for a v2 database, if any."""
    has_snapshots = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='snapshots'"
        ).fetchone()
        is not None
    )
    if has_snapshots:
        row = conn.execute(
            "SELECT id FROM snapshots WHERE promoted_at IS NOT NULL ORDER BY promoted_at DESC, id DESC LIMIT 1"
        ).fetchone()
        if row is not None:
            return str(row[0])
        row = conn.execute(
            "SELECT id FROM snapshots ORDER BY created_at DESC, id DESC LIMIT 1"
        ).fetchone()
        if row is not None:
            return str(row[0])
    if "snapshot_id" in cols:
        row = conn.execute(
            "SELECT snapshot_id FROM repositories ORDER BY snapshot_id DESC LIMIT 1"
        ).fetchone()
        if row is not None:
            return str(row[0])
    return None


def load_scores(db_path: os.PathLike[str]) -> list[ScoreRow]:
    """Read every ``repositories`` row as a :class:`ScoreRow`, sorted by repo."""
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    try:
        cols = {row[1] for row in conn.execute('PRAGMA table_info("repositories")').fetchall()}
        if "p_responsiveness" in cols and "rated" in cols:
            comp_col = "COALESCE(porchlamp, composite)" if ("porchlamp" in cols and "composite" in cols) else ("porchlamp" if "porchlamp" in cols else "composite")
            v2_cols = [
                "p_responsiveness",
                "p_criticality",
                "p_governance",
                "p_rigor",
                "p_accessibility",
                comp_col,
            ]
            snap_id = _served_snapshot_id(conn, cols)
            if snap_id is not None and "snapshot_id" in cols:
                cursor = conn.execute(
                    "SELECT owner, name, rated, " + ", ".join(v2_cols) + " FROM repositories WHERE snapshot_id = ?",
                    (snap_id,),
                )
            else:
                cursor = conn.execute("SELECT owner, name, rated, " + ", ".join(v2_cols) + " FROM repositories")
            rows = [
                ScoreRow(
                    f"{owner}/{name}",
                    bool(rated),
                    {
                        canon: (float(val) if val is not None else 0.0)
                        for canon, val in zip(SCORE_COLUMNS, values)
                    },
                )
                for owner, name, rated, *values in cursor
            ]
        else:
            cursor = conn.execute("SELECT owner, name, is_stage0_disqualified, "
                                  + ", ".join(SCORE_COLUMNS) + " FROM repositories")
            rows = [
                ScoreRow(
                    f"{owner}/{name}",
                    not bool(disqualified),
                    {
                        column: (float(value) if value is not None else 0.0)
                        for column, value in zip(SCORE_COLUMNS, values)
                    },
                )
                for owner, name, disqualified, *values in cursor
            ]
    finally:
        conn.close()
    return sorted(rows, key=lambda row: row.repo)


def pr_labels(pr: PRRecord, as_of: datetime) -> tuple[bool, bool]:
    """Return ``(decision_30d, merge_90d)`` for one PR against snapshot T.

    Both are fully observed at the collection time T + 90 d for every PR in
    the window (PREREG section 3).
    """
    decided = pr.closed_at is not None and pr.closed_at <= pr.created_at + timedelta(days=DECISION_WINDOW_DAYS)
    merged = pr.merged_at is not None and pr.merged_at <= as_of + timedelta(days=MERGE_WINDOW_DAYS)
    return decided, merged


def filter_eligible(
    prs: Sequence[PRRecord], first_timers: Mapping[str, AbstractSet[str]]
) -> tuple[list[PRRecord], dict[str, int]]:
    """Split PRs into eligible and excluded, counting exclusions by reason.

    A PR is eligible when it is external and its author is in
    ``first_timers[repo]``. A PR failing both reasons counts once, as internal.
    """
    kept: list[PRRecord] = []
    excluded = {"internal": 0, "not_first_timer": 0}
    for pr in prs:
        if pr.association in INTERNAL_ASSOCIATIONS:
            excluded["internal"] += 1
        elif pr.author not in first_timers.get(pr.repo, frozenset()):
            excluded["not_first_timer"] += 1
        else:
            kept.append(pr)
    return kept, excluded


def _envelope_items(data: Any) -> Sequence[Mapping[str, Any]]:
    """Extract row mappings from a list-shaped or dict-shaped envelope payload."""
    if isinstance(data, list):
        return [item for item in data if isinstance(item, Mapping)]
    if isinstance(data, Mapping):
        for key in ("commits", "rows", "events", "prs"):
            items = data.get(key)
            if isinstance(items, list):
                return [item for item in items if isinstance(item, Mapping)]
    return ()


def _logins_in_envelope(path: Path, as_of: datetime, ts_field: str) -> tuple[set[str], set[str]]:
    """Logins recorded in one S0 envelope, split by (seen, active before T)."""
    seen, prior = set(), set()
    if not path.is_file():
        return seen, prior
    for item in _envelope_items(read_envelope(path).data):
        login = item.get("author_login")
        if not isinstance(login, str) and isinstance(item.get("author"), Mapping):
            login = item["author"].get("login")
        if not isinstance(login, str) or not login:
            continue
        seen.add(login)
        raw_ts = item.get(ts_field)
        if raw_ts is None and ts_field == "created_at":
            raw_ts = item.get("createdAt")
        stamp = _parse_ts(raw_ts)
        if stamp is not None and stamp < as_of:
            prior.add(login)
    return seen, prior


def first_timers_from_s0(
    data_dir: os.PathLike[str], as_of: datetime, prs: Sequence[PRRecord]
) -> dict[str, frozenset[str]]:
    """First-timers as recorded in the S0 envelopes (PREREG section 2).

    A login is a first-timer for a repo when the S0 ``commits_180d`` (or
    ``commits_365d``) and ``pr_events`` (or ``pr_window``) envelopes record no
    commit or PR authored by the login before ``as_of``. Logins only seen in
    the new PRs are first-timers: nothing before T is recorded for them.
    Missing envelopes count as empty.
    """
    root = Path(data_dir)
    result: dict[str, frozenset[str]] = {}
    for repo in sorted({pr.repo for pr in prs}):
        owner, _, name = repo.partition("/")
        key = f"{owner}__{name}"
        seen, prior = set(), set()
        envelope_specs = (
            (root / "raw" / "commits_180d" / f"{key}.json", "date"),
            (root / "raw" / "commits_365d" / f"{key}.json", "date"),
            (root / "raw" / "pr_events" / f"{key}.json", "created_at"),
            (root / "raw" / "pr_window" / f"{key}.json", "created_at"),
        )
        for env_path, ts_field in envelope_specs:
            logins, earlier = _logins_in_envelope(env_path, as_of, ts_field)
            seen |= logins
            prior |= earlier
        seen |= {pr.author for pr in prs if pr.repo == repo}
        result[repo] = frozenset(seen - prior)
    return result


def repo_label_rates(prs: Sequence[PRRecord], as_of: datetime) -> dict[str, RepoRates]:
    """Per-repo decision/merge rates over the given (already eligible) PRs."""
    per_repo: dict[str, list[tuple[bool, bool]]] = {}
    for pr in prs:
        per_repo.setdefault(pr.repo, []).append(pr_labels(pr, as_of))
    return {repo: RepoRates(len(labels),
                            sum(decided for decided, _ in labels) / len(labels),
                            sum(merged for _, merged in labels) / len(labels))
            for repo, labels in sorted(per_repo.items())}


def _average_ranks(values: Sequence[float]) -> list[float]:
    """1-based average ranks; tied values take the mean of their positions."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        stop = start
        while stop + 1 < len(order) and values[order[stop + 1]] == values[order[start]]:
            stop += 1
        for index in range(start, stop + 1):
            ranks[order[index]] = (start + stop) / 2 + 1
        start = stop + 1
    return ranks


def spearman_rho(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    """Spearman correlation (Pearson on average ranks); None when undefined.

    Undefined below two values or when either side has zero variance.
    """
    if len(x) != len(y) or len(x) < 2:
        return None
    rx, ry = _average_ranks(list(x)), _average_ranks(list(y))
    mean_x, mean_y = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mean_x) * (b - mean_y) for a, b in zip(rx, ry))
    den_x = sum((a - mean_x) ** 2 for a in rx)
    den_y = sum((b - mean_y) ** 2 for b in ry)
    if den_x == 0.0 or den_y == 0.0:
        return None
    return num / math.sqrt(den_x * den_y)


def roc_auc(scores: Sequence[float], labels: Sequence[int]) -> Optional[float]:
    """Mann-Whitney AUC with average ranks for ties; None when one class is empty."""
    if len(scores) != len(labels):
        raise ForwardError("scores and labels must have equal length")
    positives = sum(labels)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return None
    ranks = _average_ranks([score for score, _ in sorted(zip(scores, labels))])
    rank_sum = sum(rank for rank, (_, label) in zip(ranks, sorted(zip(scores, labels))) if label)
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def analyze(
    scores: Sequence[ScoreRow],
    prs: Sequence[PRRecord],
    as_of: datetime,
    first_timers: Mapping[str, AbstractSet[str]],
) -> ForwardReport:
    """Run the pre-registered analysis (PREREG section 5) and build the report.

    Every score column against both label rates: Spearman across rated repos
    with at least one eligible PR, AUC over PR-level labels. C1 and C2 apply
    the pre-stated thresholds; undefined values are UNDECIDED with the reason
    code, never a guess.
    """
    eligible, excluded = filter_eligible(prs, first_timers)
    rates = repo_label_rates(eligible, as_of)
    population = [row for row in scores if row.rated and row.repo in rates]
    pop_repos = {row.repo for row in population}
    pop_prs = [pr for pr in eligible if pr.repo in pop_repos]
    n_repos = len(population)
    labels_by_label = {label: [pr_labels(pr, as_of)[_LABEL_INDEX[label]] for pr in pop_prs]
                       for label in _LABEL_INDEX}
    pairs: list[PairResult] = []
    for column in SCORE_COLUMNS:
        score_by_repo = {row.repo: row.scores[column] for row in population}
        values = [row.scores[column] for row in population]
        for label in ("decision_rate", "merge_rate"):
            target = [getattr(rates[row.repo], label) for row in population]
            if n_repos < 2:
                rho, rho_reason = None, "insufficient_repos"
            else:
                rho = spearman_rho(values, target)
                rho_reason = "constant_score" if rho is None else None
            auc = roc_auc([score_by_repo[pr.repo] for pr in pop_prs], labels_by_label[label])
            pairs.append(PairResult(column, label, n_repos, len(pop_prs), rho, auc, rho_reason,
                                    "one_class" if auc is None else None))
    by_pair = {(p.score, p.label): p for p in pairs}
    criteria = []
    for name, column, label, threshold in (
        ("C1", PILLAR_COLUMNS[0], "decision_rate", RESPONSIVENESS_DECISION_RHO_MIN),
        ("C2", COMPOSITE_COLUMN, "merge_rate", COMPOSITE_MERGE_RHO_MIN),
    ):
        pair = by_pair[(column, label)]
        if pair.spearman is None:
            verdict = f"UNDECIDED:{pair.rho_reason}"
        elif pair.spearman >= threshold:
            verdict = "PASS"
        else:
            verdict = "FAIL"
        criteria.append(CriterionResult(name, threshold, pair.spearman, verdict))
    return ForwardReport(as_of, sum(1 for row in scores if row.rated), n_repos, len(pop_prs),
                         excluded, tuple(pairs), tuple(criteria))


def _http_get_json(url: str, token: Optional[str]) -> Any:
    """One authenticated GitHub REST GET; the module's only network call."""
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": "porchlamp-forward-audit"}
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise ForwardError(f"HTTP {error.code} for {url}") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as error:
        raise ForwardError(f"fetch failed for {url}: {error}") from error


def live_repo_meta(repo: str, token: Optional[str]) -> dict[str, Any]:
    """Live repo meta for cost estimation; 404 and errors come back structured."""
    try:
        meta = _http_get_json(f"{GITHUB_API}/repos/{repo}", token)
        return {"exists": True, "archived": bool(meta.get("archived")), "error": None}
    except ForwardError as error:
        note = "repo not found" if "HTTP 404" in str(error) else str(error)
        return {"exists": False, "archived": None, "error": note}


def _last_page(url: str, token: Optional[str]) -> Optional[int]:
    """The last page number from a PR-listing Link header, or None."""
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": "porchlamp-forward-audit"}
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            link = response.headers.get("Link") or ""
    except (urllib.error.URLError, TimeoutError, urllib.error.HTTPError, OSError) as error:
        raise ForwardError(f"Link header fetch failed for {url}: {error}") from error
    for segment in link.split(","):
        parts = [part.strip() for part in segment.split(";")]
        if len(parts) == 2 and parts[1] == 'rel="last"':
            match = re.search(r"[?&]page=(\d+)", parts[0])
            if match:
                return int(match.group(1))
    return None


def dry_run_report(
    repos: Sequence[str], as_of: datetime, out_dir: Path, live: bool, token: Optional[str]
) -> dict[str, Any]:
    """The would-be collection: per-repo point cost and the exact paths.

    With ``live`` each repo gets one meta call (1 point) and the PR page count
    is bounded by ``ceil(total_pulls / per_page)``; without, the bound is
    unknown and only the meta point is counted. No file is written.
    """
    rows: list[dict[str, Any]] = []
    total = 0
    complete = True
    for repo in repos:
        if not live:
            complete = False
            rows.append({"repo": repo, "cost": None,
                         "note": "offline: 1 meta point + PR pages (bound unknown without live meta)"})
            continue
        meta = live_repo_meta(repo, token)
        if meta["error"] is not None:
            complete = False
            rows.append({"repo": repo, "cost": None, "note": meta["error"]})
            continue
        if meta["archived"]:
            total += 1
            rows.append({"repo": repo, "cost": 1, "note": "live: archived repo, no new PRs, 1 point"})
            continue
        pulls_url = (f"{GITHUB_API}/repos/{repo}/pulls"
                     f"?state=all&sort=created&direction=desc&per_page={PER_PAGE}&page=1")
        try:
            pages = _last_page(pulls_url, token)
        except ForwardError as error:
            complete = False
            rows.append({"repo": repo, "cost": None, "note": f"live: {error}"})
            continue
        if pages is None:
            complete = False
            rows.append({"repo": repo, "cost": None,
                         "note": "live: no pagination Link header, page bound unknown"})
            continue
        cost = 1 + pages
        total += cost
        rows.append({"repo": repo, "cost": cost,
                     "note": f"live: {pages} PR page(s) total, bound {cost} points (1 meta + {pages} pages)"})
    would_write = [f"raw/fwd_prs/{repo.partition('/')[0]}__{repo.partition('/')[2]}.json" for repo in repos]
    would_write.append("forward_receipt.json")
    return {"t": as_of.isoformat(),
            "collect_at": (as_of + timedelta(days=COLLECT_DELAY_DAYS)).isoformat(),
            "window_end": (as_of + timedelta(days=PR_WINDOW_END_DAYS)).isoformat(),
            "out_dir": str(out_dir), "repos": rows,
            "total_cost_bound": total, "cost_complete": complete, "would_write": would_write}


def _fetch_pull_pages(
    repo: str, as_of: datetime, token: Optional[str]
) -> tuple[list[dict[str, Any]], int]:
    """Walk the PR list down to T; return (in-window PR items, pages fetched)."""
    window_end = as_of + timedelta(days=PR_WINDOW_END_DAYS)
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        url = (f"{GITHUB_API}/repos/{repo}/pulls"
               f"?state=all&sort=created&direction=desc&per_page={PER_PAGE}&page={page}")
        payload = _http_get_json(url, token)
        page += 1
        if not payload:
            break
        created_last = _parse_ts(payload[-1].get("created_at"))
        for item in payload:
            created = _parse_ts(item.get("created_at"))
            if created is not None and as_of < created <= window_end:
                items.append(item)
        # A short page only confirms the walk end on the next (empty) fetch.
        if created_last is not None and created_last <= as_of:
            break
    return items, page - 1


def collect_forward(
    repos: Sequence[str],
    as_of: datetime,
    out_dir: Path,
    token: Optional[str],
    snapshot_id: Optional[str],
    prereg_hash: Optional[str],
    fetched_at: datetime,
) -> dict[str, Any]:
    """Collect PRs for every repo, write envelopes and the receipt.

    One envelope per repo under ``<out>/raw/fwd_prs/`` holding the in-window
    PR items exactly as fetched, plus ``<out>/forward_receipt.json``. The
    stamp is the explicit ``fetched_at`` time (D-VER-3): the caller passes
    the time, the module owns no clock.
    """
    collected_at = utc_now_iso(fetched_at)
    rows: list[dict[str, Any]] = []
    total = 0
    for repo in repos:
        meta = live_repo_meta(repo, token)
        if meta["error"] is not None:
            status = "gone" if meta["error"] == "repo not found" else f"error: {meta['error']}"
            rows.append({"repo": repo, "status": status, "prs": 0, "pages": 0, "points": 1})
            total += 1
            continue
        if meta["archived"]:
            rows.append({"repo": repo, "status": "archived", "prs": 0, "pages": 0, "points": 1})
            total += 1
            continue
        items, pages = _fetch_pull_pages(repo, as_of, token)
        owner, _, name = repo.partition("/")
        write_envelope(out_dir / "raw" / "fwd_prs" / f"{owner}__{name}.json",
                       source=f"forward:github-rest:repos/{repo}/pulls", data=items,
                       fetched_at=collected_at)
        total += 1 + pages
        rows.append({"repo": repo, "status": "collected", "prs": len(items), "pages": pages,
                     "points": 1 + pages})
    receipt = {"snapshot_id": snapshot_id, "as_of": as_of.isoformat(), "collected_at": collected_at,
               "prereg_sha256": prereg_hash, "total_points": total, "repos": rows}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "forward_receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                                                  encoding="utf-8")
    return receipt


def load_pr_envelopes(out_dir: os.PathLike[str]) -> list[PRRecord]:
    """Read every collected envelope under ``<out>/raw/fwd_prs/`` as PRs."""
    prs: list[PRRecord] = []
    folder = Path(out_dir) / "raw" / "fwd_prs"
    if not folder.is_dir():
        return prs
    for path in sorted(folder.glob("*.json")):
        owner, _, name = path.stem.partition("__")
        for item in read_envelope(path).data or []:
            created = _parse_ts(item.get("created_at"))
            if created is None:
                raise ForwardError(f"envelope {path} has a PR without created_at")
            user = item.get("user") or {}
            prs.append(PRRecord(f"{owner}/{name}", int(item["number"]),
                                str(user.get("login") or "unknown"),
                                str(item.get("author_association") or "UNKNOWN"),
                                created, _parse_ts(item.get("closed_at")),
                                _parse_ts(item.get("merged_at"))))
    return prs


def _prereg_sha256(path: Optional[str]) -> Optional[str]:
    """sha256 of the pre-registration document, or None when it is absent."""
    target = Path(path) if path is not None else _PREREG
    if not target.is_file():
        return None
    return hashlib.sha256(target.read_bytes()).hexdigest()


def _fail(message: str) -> int:
    print(f"forward: {message}", file=sys.stderr)
    return 2


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run ``porchlamp audit forward``; returns the process exit code."""
    parser = argparse.ArgumentParser(prog="porchlamp audit forward",
                                     description="Forward-validation experiment (D-VAL-1; "
                                                 "contract: docs/v2/validation/PREREG.md).")
    parser.add_argument("--snapshot", default=None, help="S0 snapshot id or path to manifest.json")
    parser.add_argument("--data-dir", default=None, help="data dir with raw/ and snapshots/ (default: data)")
    parser.add_argument("--as-of", default=None, help="ISO-8601 T when no snapshot is given")
    parser.add_argument("--repos", default=None, help="comma-separated owner/repo targets")
    parser.add_argument("--collect", action="store_true", help="run the collection phase")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --collect: print costs and paths, write nothing")
    parser.add_argument("--offline", action="store_true", help="dry-run without live meta calls")
    parser.add_argument("--out", default=None, help="output dir (default: <data-dir>/forward)")
    parser.add_argument("--prereg", default=None, help="path to PREREG.md (default: docs/v2/validation/PREREG.md)")
    parser.add_argument("--fetched-at", default=None,
                        help="ISO-8601 UTC fetch stamp; required for --collect, unused for "
                             "plan and --collect --dry-run (no wall-clock default)")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.dry_run and not args.collect:
        return _fail("--dry-run requires --collect")
    try:
        ref = resolve_snapshot(args.snapshot, args.data_dir) if args.snapshot else None
    except (ForwardError, FileNotFoundError, json.JSONDecodeError) as error:
        return _fail(str(error))
    if args.collect and not args.dry_run and ref is None:
        return _fail("real collection requires --snapshot: a frozen T defines the window and the rated set")
    as_of = ref.as_of if ref is not None else _parse_ts(args.as_of) if args.as_of else None
    if as_of is None:
        return _fail("no T: pass --snapshot, or --as-of with --repos in dry-run mode")
    if ref is not None and args.as_of is not None:
        print("note: --as-of ignored, the snapshot manifest defines T", file=sys.stderr)
    fetched_at: Optional[datetime] = None
    if args.collect and not args.dry_run:
        if args.fetched_at is None:
            return _fail("--collect requires --fetched-at <ISO-8601 UTC>: the envelope and "
                        "receipt stamps are explicit (D-VER-3), never a wall-clock default")
        fetched_at = _parse_ts(args.fetched_at)
        if fetched_at is None:
            return _fail(f"--fetched-at is not an ISO-8601 timestamp: {args.fetched_at!r}")
    scores: Optional[list[ScoreRow]] = None
    if ref is not None and (snap_db := sorted((ref.snap_dir / "build").glob("*.db"))):
        try:
            scores = load_scores(snap_db[0])
        except sqlite3.Error as error:
            return _fail(f"cannot read the snapshot database {snap_db[0]}: {error}")
    if args.repos:
        repos = tuple(repo.strip() for repo in args.repos.split(",") if repo.strip())
        if not repos or not all(repo.count("/") == 1 for repo in repos):
            return _fail("--repos entries must be owner/repo")
    elif scores is not None:
        repos = tuple(row.repo for row in scores if row.rated)
    else:
        repos = None
    if args.collect and not repos:
        return _fail("cannot determine the target repo set: pass --repos, or provide the S0 database")
    out_dir = Path(args.out) if args.out else (ref.data_dir / "forward" if ref is not None
                                               else Path("data") / "forward")
    prereg_hash = _prereg_sha256(args.prereg)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if args.collect and args.dry_run:
        report = dry_run_report(repos or (), as_of, out_dir, live=not args.offline, token=token)
        source = "the S0 manifest" if ref is not None else f"--as-of {as_of.isoformat()}"
        print(f"forward dry-run ({'live' if not args.offline else 'offline'}) — T={as_of.isoformat()} ({source})")
        print(f"collect at T + {COLLECT_DELAY_DAYS} d = {report['collect_at']}; "
              f"PR window (T, T + {PR_WINDOW_END_DAYS} d], ends {report['window_end']}")
        for row in report["repos"]:
            print(f"repo {row['repo']}: {row['note']}")
        if not args.offline and not report["cost_complete"]:
            unknown = sum(1 for row in report["repos"] if row["cost"] is None)
            print(f"total point cost bound: {report['total_cost_bound']} points (known); "
                  f"{unknown} repo(s) with unknown bound")
        elif args.offline:
            print("total point cost bound: unknown offline")
        else:
            print(f"total point cost bound: {report['total_cost_bound']} points")
        print(f"would write (under {out_dir}):")
        for name in report["would_write"]:
            print(f"  {out_dir / name}")
        print("dry-run writes nothing")
        return 0
    if args.collect:
        receipt = collect_forward(repos, as_of, out_dir, token,
                                  ref.snapshot_id if ref is not None else None,
                                  prereg_hash, fetched_at)
        for row in receipt["repos"]:
            print(f"repo {row['repo']}: {row['status']} ({row['prs']} PRs, {row['points']} points)")
        collected = sum(1 for row in receipt["repos"] if row["status"] == "collected")
        print(f"collected {collected} of {len(receipt['repos'])} repos; total {receipt['total_points']} points; "
              f"receipt {out_dir / 'forward_receipt.json'}")
    else:
        source = f"S0 snapshot {ref.snapshot_id}" if ref is not None else f"--as-of {as_of.isoformat()}"
        print(f"forward plan — T={as_of.isoformat()} ({source})")
        print(f"collect at T + {COLLECT_DELAY_DAYS} d; PR window (T, T + {PR_WINDOW_END_DAYS} d]; "
              "labels: decision_30d, merge_90d")
        target = f"{len(repos)}" if repos else "unresolved (no S0 database under the snapshot build dir)"
        print(f"out dir: {out_dir}; target repos: {target}")
        print(f"criteria: C1 rho(score_responsiveness, decision_rate) >= {RESPONSIVENESS_DECISION_RHO_MIN}; "
              f"C2 rho(overall_porchlamp, merge_rate) >= {COMPOSITE_MERGE_RHO_MIN}")
        print(f"prereg: {prereg_hash if prereg_hash else 'ABSENT'}")
        if not load_pr_envelopes(out_dir):
            print("no collection yet: run --collect at T + 90 d")
    prs = load_pr_envelopes(out_dir)
    if not prs:
        return 0
    if scores is None:
        print("analysis UNDECIDED:no_scores: no S0 database under the snapshot build dir")
        return 0
    if ref is None:
        print("analysis UNDECIDED:s0_envelopes_unavailable")
        return 0
    report = analyze(scores, prs, as_of, first_timers_from_s0(ref.data_dir, as_of, prs))
    (out_dir / "forward_report.json").write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for criterion in report.criteria:
        value = f"{criterion.value:.4f}" if criterion.value is not None else "n/a"
        print(f"{criterion.name}: {value} (threshold {criterion.threshold}) -> {criterion.verdict}")
    for pair in report.pairs:
        rho = f"{pair.spearman:.4f}" if pair.spearman is not None else f"UNDECIDED:{pair.rho_reason}"
        auc = f"{pair.auc:.4f}" if pair.auc is not None else f"UNDECIDED:{pair.auc_reason}"
        print(f"{pair.score} x {pair.label}: rho={rho} auc={auc} (n={pair.n_repos} repos, {pair.n_prs} PRs)")
    print(f"excluded PRs: internal={report.excluded['internal']}, "
          f"not_first_timer={report.excluded['not_first_timer']}; "
          f"forward_report.json covers {report.n_prs_eligible} eligible PRs")
    if any(c.verdict == "FAIL" for c in report.criteria):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
