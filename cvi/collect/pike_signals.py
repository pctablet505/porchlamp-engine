"""W1-7: OpenSSF (Pike) criticality signals computed locally; scorecard
subset; review/CI proxy for pipeline defenses (``02`` section 4 2.2, 4.1;
D-MISS-4).

F8: the ten Pike signals were 100% missing because the v1 pipeline awaited
the OpenSSF BigQuery mirror, which CVI never fetches. This module computes
every signal locally from recorded envelopes, three of them (releases,
issue-activity search counts, default-branch CI runs) newly collected here:

  signal                    source envelope   definition
  created_since_months      github_meta       repo age in months
  updated_since_months      commits_365d      months since the last non-bot
                                              commit (W1-5 author-date fact)
  contributor_count         commits_365d      distinct non-bot authors in
                                              the trailing 365 d
  org_count                 commits_365d      distinct orgs (W1-5 org_key)
                                              in the trailing 365 d
  commit_frequency_weekly   commits_365d      non-bot commits / 52 weeks
  recent_releases_count     releases          releases with published_at in
                                              the trailing 365 d (the
                                              envelope's tag fallback when
                                              the repo has no releases)
  closed_issues_count_90d   issues_counts     search is:closed closed:>=90d
  updated_issues_count_90d  issues_counts     search updated:>=now-90d
  comment_frequency         issues_counts     mean comments over the
                                              30-issue sample
  dependents_count          dependents        the W1-2 deduped count

D-MISS-1 applies: a repo-controlled absence is a measured zero (a repo with
no releases records 0), but a missing envelope is unmeasured -- the signal
set is emitted only when all ten sources are present, because the Pike
engine scores a missing signal as 0.0 in the numerator against the full
denominator, which would bias a partial signal set toward 0 instead of the
engine's documented neutral 50 for absent telemetry (``cvi.pillars.
criticality``). The caller (``scripts/derive.py``) leaves ``openssf_signals``
at its ``missing`` placeholder when :func:`derive_pike_signals` returns
None; ``scripts/promote.py`` already reconstructs an ``OpenSSFSignals`` from
a non-null ``meta.openssf_signals`` and ``cvi.pillars.criticality.
score_openssf_criticality`` already feeds it to ``calculate_pike_criticality``
-- this module's job stops at producing the ten-signal set or nothing.

02 section 4 4.1 pipeline defenses, D-MISS-4: when a scorecard envelope
carries at least one of the five named checks, the value is the mean of the
non-null named checks x 10, method ``"primary"``; otherwise the proxy
(method ``"proxy:review_ci"``) is ``0.6 x code-review rate x 100
+ 0.4 x CI pass rate x 100`` where the code-review rate is the share of
merged PRs in the recorded W1-3 window with at least one APPROVED review by
a non-author, non-bot account over all merged PRs, and the CI pass rate is
the recorded default-branch rate (0 when no completed runs are recorded --
the design's "0 if no CI"). Both methods are recorded ``derived`` in v1
confidence terms; the method string follows the v2 vocabulary
(``cvi.core.constants_v2.METHOD_PRIMARY`` / ``PROXY_METHOD_PREFIX``) so a
later packet can lift these values into a v2 ``Measured`` unchanged.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol, Sequence, Union

import json
import subprocess

from cvi.collect import commits as commit_domains
from cvi.collect.envelope import write_envelope
from cvi.collect.github_graphql import is_bot_actor
from cvi.models import OpenSSFSignals

# --------------------------------------------------------------------------- #
# REST transport: gh_rest (W1-3's GraphQL collector sets the pattern --
# cvi.collect.github_graphql.GhCliTransport -- this is its REST equivalent).
# One authenticated request per call; the form fields become query
# parameters via ``gh api -f``; a non-2xx answer raises (``gh api`` exits
# non-zero), so the collector's "envelope exists = done" checkpoint holds.
# ``RestTransport`` is the boundary the unit tests fake.
# --------------------------------------------------------------------------- #

#: Inter-call sleep for core-API transports (matches the sweep's SLEEP_REST).
SLEEP_CORE_SECONDS: float = 0.15
#: Inter-call sleep for search transports (30 req/min authenticated ceiling).
SLEEP_SEARCH_SECONDS: float = 2.1


class RestTransport(Protocol):
    """One authenticated GitHub REST execution; the live one shells to gh."""

    def get(self, path: str, **params: Any) -> Any:
        """GET ``path`` with query parameters and return the parsed JSON."""


class GhRestTransport:
    """Live transport via ``gh api`` (the gh auth token is used).

    Per ``gh api`` semantics, every ``-f`` value is a query parameter
    (stringified); the caller passes ints and strings and the gateway
    handles both. Raises ``RuntimeError`` on any non-zero exit so the
    collector's caller can mark the repo for retry.
    """

    def __init__(self, sleep_seconds: float = SLEEP_CORE_SECONDS) -> None:
        self.sleep_seconds = sleep_seconds

    def get(self, path: str, **params: Any) -> Any:
        """Run one GET; returns the parsed JSON body (a dict or a list).

        ``-X GET`` is explicit: with ``-f`` fields present, ``gh api``
        would otherwise default the method to POST (the form fields become
        a body), which the read endpoints reject.
        """
        cmd = ["gh", "api", "-X", "GET", path]
        for name, value in params.items():
            cmd += ["-f", f"{name}={value}"]
        time.sleep(self.sleep_seconds)
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if out.returncode != 0:
            raise RuntimeError(
                f"gh api {path.split('/')[0]} rc={out.returncode}: {out.stderr[:160]}")
        return json.loads(out.stdout) if out.stdout.strip() else None


# --------------------------------------------------------------------------- #
# Releases collector (02 §4 2.2 signal 6: recent_releases_count)
# --------------------------------------------------------------------------- #

#: 02 §4 2.2: releases are counted over the trailing 365 days.
RELEASES_WINDOW_DAYS: int = 365
#: GitHub REST listing page size (max per_page for releases/tags).
RELEASES_PAGE_SIZE: int = 100
#: Three pages = 300 releases, far above the 26-release signal threshold.
RELEASES_MAX_PAGES: int = 3
#: The tag fallback resolves at most one page of 100 tags.
TAGS_PAGE_SIZE: int = 100


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp; naive values are taken as UTC."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _in_window(value: Optional[str], now: datetime, window_days: int) -> bool:
    dt = parse_iso(value)
    return dt is not None and now - timedelta(days=window_days) <= dt <= now


def releases_in_window(
    releases: Sequence[Mapping[str, Any]], now: datetime,
    window_days: int = RELEASES_WINDOW_DAYS,
) -> int:
    """Releases whose recorded ``published_at`` falls in the trailing window.

    Undated releases (no recorded ``published_at``) never count: absence of
    a date is not evidence of recency.
    """
    return sum(1 for r in releases if _in_window(r.get("published_at"), now, window_days))


def tags_in_window(
    tags: Sequence[Mapping[str, Any]], now: datetime,
    window_days: int = RELEASES_WINDOW_DAYS,
) -> int:
    """Tags whose recorded ``commit_date`` falls in the trailing window."""
    return sum(1 for t in tags if _in_window(t.get("commit_date"), now, window_days))


def _fetch_releases(transport: RestTransport, owner: str, name: str
                    ) -> tuple[list[dict], bool]:
    """All listed releases up to the cap; ``(records, truncated)``."""
    records: list[dict] = []
    for page in range(1, RELEASES_MAX_PAGES + 1):
        batch = transport.get(f"repos/{owner}/{name}/releases",
                              per_page=RELEASES_PAGE_SIZE, page=page) or []
        records.extend({
            "tag_name": r.get("tag_name") or r.get("name"),
            "published_at": r.get("published_at"),
            "prerelease": bool(r.get("prerelease")),
        } for r in batch)
        if len(batch) < RELEASES_PAGE_SIZE:
            return records, False
    return records, True


def _fetch_tags(transport: RestTransport, owner: str, name: str) -> list[dict]:
    """One page of tags with each tag's commit author date resolved."""
    batch = transport.get(f"repos/{owner}/{name}/tags", per_page=TAGS_PAGE_SIZE) or []
    records: list[dict] = []
    for tag in batch:
        sha = ((tag.get("commit") or {}).get("sha")) or ""
        commit_date: Optional[str] = None
        if sha:
            commit = transport.get(f"repos/{owner}/{name}/commits/{sha}") or {}
            git_author = ((commit.get("commit") or {}).get("author") or {})
            commit_date = git_author.get("date")
        records.append({"name": tag.get("name"), "sha": sha,
                        "commit_date": commit_date})
    return records


def collect_releases(raw_dir: Path, owner: str, name: str,
                     now: Optional[datetime] = None,
                     transport: Optional[RestTransport] = None,
                     force: bool = False) -> str:
    """Collect the releases envelope for one repository.

    Writes ``raw_dir/releases/<owner>__<name>.json`` -- one envelope whose
    ``data`` carries the window metadata, the recorded releases (or, when
    the repo has no GitHub releases, the resolved tags), and
    ``recent_count`` computed at the envelope's anchor -- and returns
    ``"ok"``. Returns ``"skip"`` when the envelope already exists and
    ``force`` is False. The deriver recomputes the count from the raw
    records at derive time (W1-3 pattern).
    """
    key = f"{owner}__{name}"
    target = raw_dir / "releases" / f"{key}.json"
    if target.is_file() and not force:
        return "skip"
    now = now or datetime.now(timezone.utc)
    transport = transport or GhRestTransport()
    releases, truncated = _fetch_releases(transport, owner, name)
    if releases:
        tags: Optional[list[dict]] = None
        source = "releases"
        recent = releases_in_window(releases, now)
    else:
        tags = _fetch_tags(transport, owner, name)
        source = "tags"
        recent = tags_in_window(tags, now)
    write_envelope(target, source=f"github-rest:repos/{owner}/{name}/releases",
                   data={
                       "window_days": RELEASES_WINDOW_DAYS,
                       "window_anchor": now.isoformat(),
                       "source": source,
                       "releases": releases,
                       "releases_truncated": truncated,
                       "tags": tags,
                       "recent_count": recent,
                   }, fetched_at=now.isoformat())
    return "ok"


# --------------------------------------------------------------------------- #
# Issue-activity search counts (02 §4 2.2 signals 7, 8, 9)
# --------------------------------------------------------------------------- #

#: 02 §4 2.2: issue counts are taken over the trailing 90 days.
ISSUES_WINDOW_DAYS: int = 90
#: 02 §4 2.2: the comment sample covers 30 issues.
ISSUE_SAMPLE_SIZE: int = 30


def issue_q(owner: str, name: str, qualifier: Optional[str] = None,
            since: Optional[str] = None) -> str:
    """Build the repo-scoped issue search query.

    ``qualifier`` is one of ``None``, ``"closed"``, ``"updated"``; ``since``
    is the ``YYYY-MM-DD`` date for the qualifier's ``>=`` operator.
    ``"closed"`` also adds ``is:closed``: GitHub's ``closed:`` qualifier
    matches on the last recorded close date alone, so a reopened issue that
    was briefly closed inside the window would otherwise still match even
    though it is not, right now, a closed issue -- the signal this module
    reports is "closed issues", the current-state count OpenSSF's
    criticality score names, not "issues closed at some point".
    """
    q = f"repo:{owner}/{name} is:issue"
    if qualifier == "closed":
        assert since is not None
        q += f" is:closed closed:>={since}"
    elif qualifier == "updated":
        assert since is not None
        q += f" updated:>={since}"
    return q


def comments_per_issue(sample: Sequence[Mapping[str, Any]]) -> float:
    """Mean of the recorded ``comments`` counts; 0.0 for an empty sample."""
    if not sample:
        return 0.0
    total = sum(int(item.get("comments") or 0) for item in sample)
    return total / len(sample)


def collect_issue_counts(raw_dir: Path, owner: str, name: str,
                         now: Optional[datetime] = None,
                         transport: Optional[RestTransport] = None,
                         force: bool = False) -> str:
    """Collect the issue-activity search counts and the 30-issue sample.

    Writes ``raw_dir/issues_counts/<owner>__<name>.json`` -- one envelope
    whose ``data`` carries ``closed_90d`` / ``updated_90d`` (search
    ``total_count`` at the envelope's anchor), the recorded sample and
    ``comments_per_issue`` -- and returns ``"ok"``. Returns ``"skip"`` when
    the envelope already exists and ``force`` is False.
    """
    key = f"{owner}__{name}"
    target = raw_dir / "issues_counts" / f"{key}.json"
    if target.is_file() and not force:
        return "skip"
    now = now or datetime.now(timezone.utc)
    transport = transport or GhRestTransport(SLEEP_SEARCH_SECONDS)
    since = (now - timedelta(days=ISSUES_WINDOW_DAYS)).date().isoformat()
    closed = transport.get("search/issues",
                           q=issue_q(owner, name, "closed", since), per_page=1) or {}
    updated = transport.get("search/issues",
                            q=issue_q(owner, name, "updated", since), per_page=1) or {}
    sample_page = transport.get("search/issues",
                                q=issue_q(owner, name),
                                sort="updated", order="desc",
                                per_page=ISSUE_SAMPLE_SIZE) or {}
    items = (sample_page.get("items") or [])
    sample = [{"number": item.get("number"), "comments": item.get("comments"),
               "updated_at": item.get("updated_at")} for item in items]
    write_envelope(target, source="github-rest:search/issues",
                   data={
                       "window_days": ISSUES_WINDOW_DAYS,
                       "window_anchor": now.isoformat(),
                       "closed_90d": int(closed.get("total_count") or 0),
                       "updated_90d": int(updated.get("total_count") or 0),
                       "sample_size": ISSUE_SAMPLE_SIZE,
                       "sample_total": int(sample_page.get("total_count") or 0),
                       "sample": sample,
                       "comments_per_issue": round(comments_per_issue(sample), 4),
                   }, fetched_at=now.isoformat())
    return "ok"


# --------------------------------------------------------------------------- #
# Default-branch CI runs (02 §4 4.1 proxy's CI component)
# --------------------------------------------------------------------------- #

#: 02 §4 4.1: the proxy rates the last 30 completed runs.
RUNS_LIMIT: int = 30


def ci_pass_rate(runs: Sequence[Mapping[str, Any]]) -> Optional[float]:
    """Share of recorded runs whose ``conclusion`` is ``success``.

    ``None`` when no runs are recorded (the caller applies the "0 if no
    CI" rule); never a partial denominator.
    """
    if not runs:
        return None
    passed = sum(1 for r in runs if r.get("conclusion") == "success")
    return passed / len(runs)


def collect_runs(raw_dir: Path, owner: str, name: str, branch: str,
                 now: Optional[datetime] = None,
                 transport: Optional[RestTransport] = None,
                 force: bool = False) -> str:
    """Collect the default-branch CI runs envelope for one repository.

    Writes ``raw_dir/runs/<owner>__<name>.json`` -- one envelope whose
    ``data`` carries the branch, the last 30 completed runs with their
    ``conclusion``, and the recorded ``ci_pass_rate`` -- and returns
    ``"ok"``. Returns ``"skip"`` when the envelope already exists and
    ``force`` is False.
    """
    key = f"{owner}__{name}"
    target = raw_dir / "runs" / f"{key}.json"
    if target.is_file() and not force:
        return "skip"
    now = now or datetime.now(timezone.utc)
    transport = transport or GhRestTransport()
    payload = transport.get(f"repos/{owner}/{name}/actions/runs",
                            branch=branch, status="completed",
                            per_page=RUNS_LIMIT) or {}
    runs = [{"created_at": w.get("created_at"), "conclusion": w.get("conclusion"),
             "event": w.get("event")} for w in (payload.get("workflow_runs") or [])]
    rate = ci_pass_rate(runs)
    write_envelope(target,
                   source=f"github-rest:repos/{owner}/{name}/actions/runs",
                   data={
                       "branch": branch,
                       "limit": RUNS_LIMIT,
                       "runs": runs,
                       "completed": len(runs),
                       "passed": sum(1 for r in runs if r["conclusion"] == "success"),
                       "ci_pass_rate": round(rate, 4) if rate is not None else None,
                   }, fetched_at=now.isoformat())
    return "ok"


# --------------------------------------------------------------------------- #
# 2.2 Pike signal derivation
# --------------------------------------------------------------------------- #

#: Months per day, the 365/12 convention; the single day<->month fact in
#: this module (signal windows are stated in months by the spec).
DAYS_PER_MONTH: float = 365.0 / 12.0
#: Weeks per year for the commit-frequency signal.
WEEKS_PER_YEAR: float = 52.0


def months_between(start: datetime, now: datetime) -> float:
    """Months from ``start`` to ``now`` under the 365/12 convention."""
    return (now - start).total_seconds() / (DAYS_PER_MONTH * 86400.0)


def human_commit_stats(
    rows: Sequence[Mapping[str, Any]], now: datetime,
) -> Optional[dict[str, Union[int, datetime]]]:
    """W1-5-window stats over the non-bot commits of one recorded envelope.

    ``rows`` are ``commits_365d`` envelope rows. Returns
    ``{"contributors", "orgs", "commits", "last_commit_at"}`` over the
    non-bot commits on or after ``now - 365 d`` (undated rows count, as in
    W1-5's own ``elephant_factor``/``bus_factor``); None when no non-bot
    commit carries a recorded date, because then the months-since-last-
    commit signal is unmeasurable (this module cannot import W1-5's
    ``_human_window``: it is module-private, R-10).
    """
    records = commit_domains.records_from_rows(list(rows))
    cutoff = now - timedelta(days=commit_domains.COMMIT_WINDOW_DAYS)
    contributors: set[str] = set()
    orgs: set[str] = set()
    total = 0
    last: Optional[datetime] = None
    for record in records:
        if commit_domains.is_bot(record.actor):
            continue
        if record.date is not None and record.date < cutoff:
            continue
        total += 1
        author = commit_domains.author_key(record.actor)
        if author:
            contributors.add(author)
        org = commit_domains.org_key(record.actor)
        if org:
            orgs.add(org)
        if record.date is not None and (last is None or record.date > last):
            last = record.date
    if last is None:
        return None
    return {"contributors": len(contributors), "orgs": len(orgs),
            "commits": total, "last_commit_at": last}


def derive_pike_signals(
    gh: Optional[Mapping[str, Any]],
    c365: Optional[Sequence[Mapping[str, Any]]],
    rel: Optional[Mapping[str, Any]],
    iss: Optional[Mapping[str, Any]],
    dep: Optional[Mapping[str, Any]],
    now: datetime,
) -> Optional[OpenSSFSignals]:
    """The 10-signal :class:`OpenSSFSignals`, or None when any is unmeasured.

    ``gh`` is the github_meta envelope data (``created_at``), ``c365`` the
    commits_365d rows, ``rel`` the releases envelope data, ``iss`` the
    issues_counts envelope data, ``dep`` the dependents envelope data. Each
    argument maps to exactly one signal group (module docstring table); the
    first one absent is why the whole set comes back None (D-MISS-1: never
    a partial score).
    """
    if not isinstance(gh, Mapping):
        return None
    created_at = parse_iso(gh.get("created_at"))
    if created_at is None:
        return None
    if not c365:
        return None
    stats = human_commit_stats(c365, now)
    if stats is None:
        return None
    if rel is None:
        return None
    if rel.get("source") == "releases":
        recent = releases_in_window(rel.get("releases") or [], now)
    elif rel.get("source") == "tags":
        recent = tags_in_window(rel.get("tags") or [], now)
    else:
        return None
    if iss is None or iss.get("comments_per_issue") is None:
        return None
    if not isinstance(dep, Mapping) or dep.get("deduped_count") is None:
        return None
    return OpenSSFSignals(
        created_since_months=round(months_between(created_at, now), 4),
        updated_since_months=round(
            months_between(stats["last_commit_at"], now), 4),
        contributor_count=stats["contributors"],
        org_count=stats["orgs"],
        commit_frequency_weekly=round(stats["commits"] / WEEKS_PER_YEAR, 4),
        recent_releases_count=recent,
        closed_issues_count_90d=int(iss.get("closed_90d") or 0),
        updated_issues_count_90d=int(iss.get("updated_90d") or 0),
        comment_frequency=float(iss["comments_per_issue"]),
        dependents_count=int(dep["deduped_count"]),
    )


# --------------------------------------------------------------------------- #
# 4.1 Pipeline defenses (scorecard subset, else review/CI proxy)
# --------------------------------------------------------------------------- #

#: 02 §4 4.1: the five named Scorecard checks.
SCORECARD_CHECKS: tuple[str, ...] = (
    "Code-Review", "CI-Tests", "Branch-Protection", "Maintained",
    "Dangerous-Workflow")
#: 04 §3 / D-MISS-4 method vocabulary (``cvi.core.constants_v2``): a
#: primary measurement is exactly ``"primary"``; a proxy is ``"proxy:<name>"``.
METHOD_PRIMARY: str = "primary"
METHOD_PROXY: str = "proxy:review_ci"
#: 02 §4 4.1 proxy weights.
PROXY_REVIEW_WEIGHT: float = 0.6
PROXY_CI_WEIGHT: float = 0.4


def has_non_author_approval(pr: Mapping[str, Any]) -> bool:
    """True when a recorded PR node has an APPROVED review by a non-author.

    The review author's recorded login must be present, differ from the PR
    author's login (02 §4 4.1's "by a non-author") and not be a bot (D-IN-1
    ``is_bot_actor``: an automated approval is not a code review); a review
    whose author no longer resolves (deleted account) cannot be attested
    and does not count.
    """
    author_login = str(((pr.get("author") or {}).get("login") or "")).lower()
    for raw in ((pr.get("reviews") or {}).get("nodes") or []):
        if str(raw.get("state") or "").upper() != "APPROVED":
            continue
        reviewer = raw.get("author") or {}
        login = str(reviewer.get("login") or "").lower()
        if not login or login == author_login:
            continue
        if is_bot_actor(reviewer.get("__typename"), login):
            continue
        return True
    return False


def code_review_rate(prs: Sequence[Mapping[str, Any]]) -> float:
    """02 §4 4.1 proxy component: merged PRs with a non-author approval.

    The denominator is every merged PR in the recorded window; a recorded
    window with no merged PRs is a measured 0.0 (repo-controlled absence).
    """
    merged = [pr for pr in prs if pr.get("mergedAt") is not None]
    if not merged:
        return 0.0
    approved = sum(1 for pr in merged if has_non_author_approval(pr))
    return approved / len(merged)


def scorecard_subset_mean(checks: Optional[Mapping[str, Any]]) -> Optional[float]:
    """Mean of the non-null values among :data:`SCORECARD_CHECKS`.

    None when the checks mapping is absent or every named check is null --
    the caller then falls back to the proxy.
    """
    if not isinstance(checks, Mapping):
        return None
    values = [checks[name] for name in SCORECARD_CHECKS if checks.get(name) is not None]
    if not values:
        return None
    return sum(float(v) for v in values) / len(values)


def pipeline_defenses(
    sc: Optional[Mapping[str, Any]],
    prs: Optional[Sequence[Mapping[str, Any]]],
    runs: Optional[Sequence[Mapping[str, Any]]],
) -> tuple[Optional[float], Optional[str]]:
    """02 §4 4.1: the (value, method) pair, or (None, None) when unmeasured.

    ``sc`` is the recorded scorecard envelope data (``{"score", "checks"}``),
    ``prs`` the recorded W1-3 window nodes, ``runs`` the recorded default-
    branch runs. The scorecard subset wins when usable (method
    :data:`METHOD_PRIMARY`); the proxy (:data:`METHOD_PROXY`) needs both the
    pr_window and the runs envelope (``None`` = envelope absent, ``[]`` =
    recorded absence, which the "0 if no CI" rule scores 0).
    """
    if sc is not None:
        mean = scorecard_subset_mean(sc.get("checks"))
        if mean is not None:
            return round(mean * 10.0, 4), METHOD_PRIMARY
    if prs is not None and runs is not None:
        ci = ci_pass_rate(runs)
        value = (PROXY_REVIEW_WEIGHT * code_review_rate(prs) * 100.0
                 + PROXY_CI_WEIGHT * (0.0 if ci is None else ci) * 100.0)
        return round(value, 4), METHOD_PROXY
    return None, None


# --------------------------------------------------------------------------- #
# scripts/derive.py hook entry point
# --------------------------------------------------------------------------- #

def derive_w17_fields(
    data_dir: Path, key: str, now: datetime,
    gh: Any, gh_at: Optional[str],
    c365: Any, c365_at: Optional[str],
    dep: Any, dep_at: Optional[str],
    rel: Any, rel_at: Optional[str],
    iss: Any, iss_at: Optional[str],
    rns: Any, rns_at: Optional[str],
    sc: Any, sc_at: Optional[str],
    prw: Any, prw_at: Optional[str],
) -> dict[str, tuple[Any, Any, Any, str]]:
    """The W1-7 derive hook: ``{field: (value, source, fetched_at, conf)}``.

    Emits ``openssf_signals`` only when all ten sources are present and
    ``pipeline_defenses`` / ``pipeline_defenses_method`` only when 4.1 is
    measurable; the caller leaves every unemitted field at its missing
    placeholder (D-MISS-1: partial signal sets and absent envelopes are
    unmeasured, not zeros). Source is the list of envelope paths used;
    fetched_at is the newest of the used sources' recorded times.
    """
    out: dict[str, tuple[Any, Any, Any, str]] = {}

    def env_path(col: str) -> str:
        return str(data_dir / "raw" / col / f"{key}.json")

    signals = derive_pike_signals(gh, c365, rel, iss, dep, now)
    if signals is not None:
        sources = [env_path(col) for col in ("github_meta", "commits_365d",
                                             "releases", "issues_counts",
                                             "dependents")]
        ats = [a for a in (gh_at, c365_at, rel_at, iss_at, dep_at) if a]
        out["openssf_signals"] = (signals, sources, max(ats), "derived")

    prs = prw.get("prs") if isinstance(prw, Mapping) else None
    runs = rns.get("runs") if isinstance(rns, Mapping) else None
    value, method = pipeline_defenses(
        sc if isinstance(sc, Mapping) else None, prs, runs)
    if value is not None:
        if method == METHOD_PRIMARY:
            sources, ats = ([env_path("scorecard")], [sc_at])
        else:
            sources, ats = ([env_path("pr_window"), env_path("runs")],
                            [prw_at, rns_at])
        ats = [a for a in ats if a]
        at = max(ats) if ats else None
        out["pipeline_defenses"] = (value, sources, at, "derived")
        out["pipeline_defenses_method"] = (method, sources, at, "derived")
    return out
