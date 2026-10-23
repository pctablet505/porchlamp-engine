"""OpenSSF (Pike) criticality signals computed locally; scorecard
subset; review/CI proxy for pipeline defenses (``02`` section 4 2.2, 4.1;
D-MISS-4).

Every signal is computed locally from recorded envelopes -- no OpenSSF
BigQuery mirror is fetched -- three of them (releases, issue-activity
search counts, default-branch CI runs) collected by this module:

  signal                    source envelope   definition
  created_since_months      github_meta       repo age in months
  updated_since_months      commits_365d      months since the last non-bot
                                              commit (author-date fact)
  contributor_count         commits_365d      distinct non-bot authors in
                                              the trailing 365 d
  org_count                 commits_365d      distinct orgs (``org_key``)
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
  dependents_count          dependents        the deduped dependents count

The commits_365d record (RFC-0021, ``porchlamp.core.commit_domains.
commit_window``) holds every commit of the year up to
``constants.COMMIT_CAP`` and an evenly spaced sample above it. On a sample,
commit_frequency_weekly is the sample's non-bot share times GitHub's count
of the year's commits (``total``) / 52, and contributor_count / org_count
count the sampled commits' authors and orgs -- at least that many took part
in the year. A record cut at the pre-RFC-0021 newest-1,000 cap leaves the
whole set ``collector_failure``: its count of commits is the cap, not the
year's (it held every busy repository at 19.23 commits a week).

D-MISS-1 applies: a repo-controlled absence is a measured zero (a repo with
no releases records 0), but a missing envelope is unmeasured -- nine of the
ten sources are whole-set-or-nothing, because the Pike engine scores
a missing signal as 0.0 in the numerator against the full denominator,
which would bias a partial signal set toward 0 instead of the engine's
documented neutral 50 for absent telemetry (``porchlamp.pillars.criticality``).
The caller (``scripts/derive.py``) leaves ``openssf_signals`` at its
``missing`` placeholder when :func:`derive_w17_fields` cannot build a
complete ten-signal set; ``scripts/promote.py`` reconstructs an
``OpenSSFSignals`` from a non-null ``meta.openssf_signals`` and
``porchlamp.pillars.criticality.score_openssf_criticality`` feeds it to
``calculate_pike_criticality`` -- this module's ``openssf_signals`` output
is the complete ten-signal set or nothing.

``dependents_count`` is the one exception (D-MISS-5 consequence for 2.2,
``02`` section 5): when it alone is unmeasurable (the same fact 2.1
``dependents_deduped`` reads), :func:`derive_pike_signals` returns the
other nine plus which one is missing, and :func:`derive_w17_fields` records
them under the additive ``openssf_signals_partial`` /
``openssf_signals_partial_missing`` fields (``openssf_signals`` itself stays
at the v1 "missing" placeholder, so v1's scoring is unaffected). Only
``porchlamp.derive.bundle._resolve_openssf_criticality``'s
``proxy:no_dependents_signal`` path reads them, renormalising over the nine
present signals' weights via ``porchlamp.core.openssf.calculate_pike_criticality``'s
``missing`` kwarg instead of biasing toward 0 -- the bias this module's own
whole-set-or-nothing rule exists to avoid, generalised to the one signal a
design-level fact (not a collector outcome) can legitimately explain away.

02 section 4 4.1 pipeline defenses, D-MISS-4: when a scorecard envelope
carries at least one of the five named checks, the value is the mean of the
non-null named checks x 10, method ``"primary"``; otherwise the proxy
(method ``"proxy:review_ci"``) is ``0.6 x code-review rate x 100
+ 0.4 x CI pass rate x 100`` where the code-review rate is the share of
merged PRs in the recorded pr_window with at least one APPROVED review by
a non-author, non-bot account over all merged PRs, and the CI pass rate is
the recorded default-branch rate (0 when no completed runs are recorded --
the design's "0 if no CI"). Both methods are recorded ``derived`` in v1
confidence terms; the method string follows the v2 vocabulary
(``porchlamp.core.constants.METHOD_PRIMARY`` / ``PROXY_METHOD_PREFIX``), so these
values lift into a v2 ``Measured`` unchanged.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol, Sequence, Union

import json

from porchlamp.collect import commits as commit_domains
from porchlamp.collect import ghcli, validate
from porchlamp.collect.envelope import raw_envelope_path, relative_envelope_path, write_envelope
from porchlamp.collect.github_graphql import is_bot_actor, pr_window_cut_short, reply_sampled_prs
from porchlamp.core import constants, gates, landing
from porchlamp.models import OpenSSFSignals

# --------------------------------------------------------------------------- #
# REST transport: gh_rest, the REST equivalent of the GraphQL collector's
# porchlamp.collect.github_graphql.GhCliTransport.
# One authenticated request per call; the form fields become query
# parameters via ``gh api -f``; a non-2xx answer raises (``gh api`` exits
# non-zero), so the collector's "envelope exists = done" checkpoint holds.
# ``RestTransport`` is the boundary the unit tests fake.
# --------------------------------------------------------------------------- #

#: Inter-call sleep for core-API transports. The one definition:
#: ``scripts/gather.py`` imports this as ``SLEEP_REST`` rather than keeping
#: a second ``0.15`` literal of its own.
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
        validate.gh_api_path(path)
        cmd = ["api", "-X", "GET", path]
        for name, value in params.items():
            cmd += ["-f", f"{name}={value}"]
        if self.sleep_seconds > 0:
            time.sleep(self.sleep_seconds)  # review-ok: GitHub API rate-limit pacing (0.15s) to avoid secondary rate limits
        out = ghcli.run(cmd, timeout=90)
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
    records at derive time (rolling-window pattern).
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
        if since is None:
            raise ValueError("since is required when qualifier is 'closed'")
        q += f" is:closed closed:>={since}"
    elif qualifier == "updated":
        if since is None:
            raise ValueError("since is required when qualifier is 'updated'")
        q += f" updated:>={since}"
    return q


def comments_per_issue(sample: Sequence[Mapping[str, Any]]) -> float:
    """Mean of the recorded ``comments`` counts; 0.0 for an empty sample."""
    if not sample:
        return 0.0
    total = sum(int(item.get("comments") or 0) for item in sample)
    return total / len(sample)


#: Single-call GraphQL query for issue counts and the 30-issue comment sample,
#: replacing 3 separate REST ``/search/issues`` requests (which are throttled by
#: GitHub's 30 req/min Search API limit and ``SLEEP_SEARCH_SECONDS = 2.1s``).
#: ``closed_90d`` is the SAME search the REST path runs (``issue_q(..., "closed",
#: since)``: ``is:issue is:closed closed:>=DATE``), read as ``issueCount``. The
#: ``issues(filterBy: {since}, states: [CLOSED])`` connection is NOT equivalent:
#: ``since`` filters on ``updatedAt``, so it counts issues closed AND updated in
#: the window (flask 17 -> 32, golang/go 1,475 -> 2,493 on the 2026-10-06 review).
#: ``updated90d`` (``since`` = updatedAt) and the sample match REST.
ISSUES_COUNTS_GRAPHQL_QUERY = """
query($owner: String!, $name: String!, $since: DateTime!, $sampleSize: Int!, $closedQuery: String!) {
  closed90d: search(type: ISSUE, query: $closedQuery, first: 1) {
    issueCount
  }
  repository(owner: $owner, name: $name) {
    updated90d: issues(filterBy: {since: $since}, first: 1) {
      totalCount
    }
    sample: issues(first: $sampleSize, orderBy: {field: UPDATED_AT, direction: DESC}) {
      totalCount
      nodes {
        number
        updatedAt
        comments {
          totalCount
        }
      }
    }
  }
}
""".strip()


def collect_issue_counts_graphql(
    raw_dir: Path,
    owner: str,
    name: str,
    now: Optional[datetime] = None,
    transport: Optional[Any] = None,
    force: bool = False,
) -> str:
    """Collect ``issues_counts`` in a single GraphQL call instead of 3 Search API calls.

    Writes ``raw_dir/issues_counts/<owner>__<name>.json`` with the exact same
    envelope data schema as :func:`collect_issue_counts` so downstream derivation
    is unchanged, while eliminating the 30 req/min Search API bottleneck.
    """
    key = f"{owner}__{name}"
    target = raw_dir / "issues_counts" / f"{key}.json"
    if target.is_file() and not force:
        return "skip"
    now = now or datetime.now(timezone.utc)
    if transport is None:
        from porchlamp.collect.github_graphql import GhCliTransport

        transport = GhCliTransport()
    since_dt = (now - timedelta(days=ISSUES_WINDOW_DAYS)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    since_iso = since_dt.isoformat().replace("+00:00", "Z")
    payload = transport.execute(
        ISSUES_COUNTS_GRAPHQL_QUERY,
        {
            "owner": owner,
            "name": name,
            "since": since_iso,
            "sampleSize": ISSUE_SAMPLE_SIZE,
            "closedQuery": issue_q(owner, name, "closed", since_dt.date().isoformat()),
        },
    )
    data = payload.get("data") if isinstance(payload, Mapping) else None
    repo = data.get("repository") if isinstance(data, Mapping) else None
    if not isinstance(repo, Mapping):
        errors = payload.get("errors") if isinstance(payload, Mapping) else None
        raise RuntimeError(
            f"graphql issues_counts failed for {owner}/{name}: {errors}"
        )
    closed_conn = data.get("closed90d")
    updated_conn = repo.get("updated90d")
    if (not isinstance(closed_conn, Mapping) or closed_conn.get("issueCount") is None
            or not isinstance(updated_conn, Mapping) or updated_conn.get("totalCount") is None):
        # A count the query did not return is a failed fetch, never a measured 0.
        raise RuntimeError(
            f"graphql issues_counts incomplete for {owner}/{name}: {payload.get('errors')}"
        )
    closed_total = int(closed_conn["issueCount"])
    updated_total = int(updated_conn["totalCount"])
    sample_conn = repo.get("sample") or {}
    sample_total = int(sample_conn.get("totalCount") or 0)
    nodes = sample_conn.get("nodes") or []
    sample = [
        {
            "number": node.get("number"),
            "comments": int(((node.get("comments") or {}).get("totalCount")) or 0),
            "updated_at": node.get("updatedAt"),
        }
        for node in nodes
        if isinstance(node, Mapping)
    ]
    write_envelope(
        target,
        source="github-graphql:issues_counts",
        data={
            "window_days": ISSUES_WINDOW_DAYS,
            "window_anchor": now.isoformat(),
            "closed_90d": closed_total,
            "updated_90d": updated_total,
            "sample_size": ISSUE_SAMPLE_SIZE,
            "sample_total": sample_total,
            "sample": sample,
            "comments_per_issue": round(comments_per_issue(sample), 4),
        },
        fetched_at=now.isoformat(),
    )
    return "ok"


def collect_issue_counts(raw_dir: Path, owner: str, name: str,
                         now: Optional[datetime] = None,
                         transport: Optional[RestTransport] = None,
                         force: bool = False,
                         graphql_transport: Optional[Any] = None) -> str:
    """Collect the issue-activity search counts and the 30-issue sample.

    Writes ``raw_dir/issues_counts/<owner>__<name>.json`` -- one envelope
    whose ``data`` carries ``closed_90d`` / ``updated_90d`` (search
    ``total_count`` at the envelope's anchor), the recorded sample and
    ``comments_per_issue`` -- and returns ``"ok"``. Returns ``"skip"`` when
    the envelope already exists and ``force`` is False.
    When ``graphql_transport`` is supplied, uses :func:`collect_issue_counts_graphql`
    to fetch all three metrics in a single GraphQL request.
    """
    if graphql_transport is not None:
        return collect_issue_counts_graphql(
            raw_dir,
            owner,
            name,
            now=now,
            transport=graphql_transport,
            force=force,
        )
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


#: A fetch-time windowed count's ``window_anchor`` must be
#: within this many days of derive-time ``now`` or the count no longer
#: describes "now" and 2.2 is unmeasured rather than silently stale.
#: Defined in the constants freeze (``porchlamp.core.constants``) and re-bound
#: here under its original name, so call sites resolve
#: ``pike_signals.WINDOW_STALENESS_DAYS``.
WINDOW_STALENESS_DAYS: int = constants.WINDOW_STALENESS_DAYS


def window_anchor_stale(anchor_iso: Optional[str], now: datetime,
                        max_days: int = WINDOW_STALENESS_DAYS) -> bool:
    """True when a recorded ``window_anchor`` is more than ``max_days`` from ``now``.

    A missing/unparseable anchor is not "stale" -- it carries no dating
    information to be stale with, so the caller's ordinary presence checks
    (envelope absent -> unmeasured) apply instead; this only fires when an
    anchor is recorded and it disagrees with the current derive-time clock.
    """
    anchor = parse_iso(anchor_iso)
    if anchor is None:
        return False
    return abs((now - anchor).total_seconds()) > max_days * 86400.0


def human_commit_stats(
    rows: Sequence[Mapping[str, Any]], now: datetime,
) -> Optional[dict[str, Union[int, datetime]]]:
    """Trailing-window stats over the non-bot commits of one recorded envelope.

    ``rows`` are ``commits_365d`` envelope rows. Returns
    ``{"contributors", "orgs", "commits", "last_commit_at"}`` over the
    non-bot commits on or after ``now - 365 d`` (undated rows count, as in
    :mod:`porchlamp.collect.commits`'s ``elephant_factor``/``bus_factor``); None
    when no non-bot commit carries a recorded date, because then the
    months-since-last-commit signal is unmeasurable (the window filter is
    rebuilt here: ``porchlamp.collect.commits._human_window`` is module-private).
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
    *,
    window_total: Optional[int] = None,
) -> Optional[tuple[dict[str, Union[int, float]], frozenset[str]]]:
    """The Pike signals present, plus which of the ten are missing.

    ``gh`` is the github_meta envelope data (``created_at``), ``c365`` the
    commits_365d rows, ``rel`` the releases envelope data, ``iss`` the
    issues_counts envelope data, ``dep`` the dependents envelope data. Each
    of ``gh``/``c365``/``rel``/``iss`` still maps to a hard-required signal
    group (module docstring table): the first one absent is why the whole
    call returns ``None``.

    ``window_total`` is GitHub's count of the year's commits when ``c365``
    is an evenly spaced sample of them (RFC-0021,
    ``porchlamp.core.commit_domains.CommitWindow.total``): the weekly commit
    frequency is then the rows' non-bot in-window share of that count, / 52.
    ``None`` (the default) means ``c365`` is the whole window, counted as is.
    The caller refuses a record cut short of the window before it gets here.

    ``dep``/``dep["deduped_count"]`` is not hard-required (D-MISS-5
    consequence for 2.2) -- when it is absent, ``"dependents_count"``
    is named in the returned ``frozenset`` and omitted from the returned
    dict, letting :func:`porchlamp.core.openssf.calculate_pike_criticality`'s
    ``missing`` kwarg renormalise the score over the other nine
    (``porchlamp.derive.bundle._resolve_openssf_criticality``'s
    ``proxy:no_dependents_signal`` path) instead of the whole set being
    unmeasured. Returns ``(signals, frozenset())`` when all ten are present;
    the signals are a plain dict, not an :class:`OpenSSFSignals` (the
    caller, :func:`derive_w17_fields`,
    is the one place that constructs an ``OpenSSFSignals`` when the
    set is complete, so v1's ``asdict``/``OpenSSFSignals(**sig)`` round trip
    through ``scripts/derive.py``/``scripts/promote.py`` still works).
    """
    if not isinstance(gh, Mapping):
        return None
    created_at = parse_iso(gh.get("created_at"))
    if created_at is None:
        return None
    if c365 is None:
        return None
    if c365:
        stats = human_commit_stats(c365, now)
        if stats is None:
            return None
    else:
        # An empty commits envelope from a SUCCESSFUL fetch
        # (c365 == [], not None) is a measured zero, not
        # unmeasured -- the repo genuinely made zero non-bot commits in the
        # window. contributor_count/org_count/commit_frequency_weekly are
        # then correctly zero; updated_since_months has no commit to date
        # itself from, so it falls back to github_meta.pushed_at (the
        # nearest real "last activity" fact this envelope set carries).
        pushed_at = parse_iso(gh.get("pushed_at"))
        if pushed_at is None:
            return None
        stats = {"contributors": 0, "orgs": 0, "commits": 0, "last_commit_at": pushed_at}
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
    window_commits = float(stats["commits"])
    if window_total is not None and c365:
        window_commits = window_commits * window_total / len(c365)
    signals: dict[str, Union[int, float]] = {
        "created_since_months": round(months_between(created_at, now), 4),
        "updated_since_months": round(months_between(stats["last_commit_at"], now), 4),
        "contributor_count": stats["contributors"],
        "org_count": stats["orgs"],
        "commit_frequency_weekly": round(window_commits / WEEKS_PER_YEAR, 4),
        "recent_releases_count": recent,
        "closed_issues_count_90d": int(iss.get("closed_90d") or 0),
        "updated_issues_count_90d": int(iss.get("updated_90d") or 0),
        "comment_frequency": float(iss["comments_per_issue"]),
    }
    if isinstance(dep, Mapping) and dep.get("deduped_count") is not None:
        signals["dependents_count"] = int(dep["deduped_count"])
        missing: frozenset[str] = frozenset()
    else:
        missing = frozenset({"dependents_count"})
    return signals, missing


# --------------------------------------------------------------------------- #
# 4.1 Pipeline defenses (scorecard subset, else review/CI proxy)
# --------------------------------------------------------------------------- #

#: 02 §4 4.1: the five named Scorecard checks.
SCORECARD_CHECKS: tuple[str, ...] = (
    "Code-Review", "CI-Tests", "Branch-Protection", "Maintained",
    "Dangerous-Workflow")
#: 04 §3 / D-MISS-4 method vocabulary (``porchlamp.core.constants``): a
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

    The denominator is every merged PR in the recorded window (or in the
    1,000-PR reply sample when ``prs`` is a >2,000-PR light+sample window);
    a recorded window with no merged PRs is a measured 0.0 (repo-controlled
    absence). RFC-0015: a PR landed outside GitHub's merge is merged here
    (:func:`porchlamp.core.landing.merged_or_landed_at`). RFC-0022: a merged PR
    whose recorded ``reviews`` list remains cut with no recorded approval is
    withheld rather than counted as unapproved.
    """
    sampled = reply_sampled_prs(prs)
    merged = [
        pr
        for pr in sampled
        if landing.merged_or_landed_at(pr) is not None
        and (has_non_author_approval(pr) or not gates.reply_connection_cut(pr.get("reviews")))
    ]
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
    ``prs`` the recorded pr_window nodes, ``runs`` the recorded default-
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
    """The derive hook: ``{field: (value, source, fetched_at, conf)}``.

    Emits ``openssf_signals`` only when all ten sources are present and
    ``pipeline_defenses`` / ``pipeline_defenses_method`` only when 4.1 is
    measurable; the caller leaves every unemitted field at its missing
    placeholder (D-MISS-1: partial signal sets and absent envelopes are
    unmeasured, not zeros). Source is the list of envelope paths used;
    fetched_at is the newest of the used sources' recorded times.

    ``c365`` is the commits_365d envelope ``data`` in either recorded shape
    (``porchlamp.core.commit_domains.commit_window`` reads it, RFC-0021): a
    record cut short of the window at the pre-RFC-0021 cap records
    ``openssf_signals`` ``collector_failure`` with the reason in
    ``openssf_signals_detail`` (D-MISS-5: a collector failure is never
    renormalised away, a retry measures it); a sample passes GitHub's count
    of the year's commits to :func:`derive_pike_signals`.

    When exactly the dependents signal is missing (D-MISS-5
    consequence for 2.2), ``openssf_signals`` stays unemitted
    but ``openssf_signals_partial`` (the other nine, dict-shaped) and
    ``openssf_signals_partial_missing`` (``["dependents_count"]``) are
    emitted instead, for ``porchlamp.derive.bundle``'s proxy path to read.
    """
    out: dict[str, tuple[Any, Any, Any, str]] = {}

    def env_path(col: str) -> str:
        # The one envelope-path formula, shared with
        # scripts/derive.py's src() closure via porchlamp.collect.envelope.
        #
        # Relative to the data root: these strings become `Measured.source`,
        # and provenance must never be absolute -- an absolute path names the
        # machine that derived the value, so the record is not portable and
        # cannot be re-verified elsewhere (docs/v2/00-README.md). The path is
        # built from `data_dir` immediately above, so it is always inside it.
        return relative_envelope_path(data_dir, raw_envelope_path(data_dir, col, key))

    # A windowed count's own fetch-time anchor more than
    # WINDOW_STALENESS_DAYS from this derive's `now` means the
    # count no longer describes "now" -- the whole ten-signal set is
    # unmeasured (never partial, same D-MISS-1 rule as an absent source),
    # tagged distinctly from a plain absence so the caller can record
    # collector_failure with which window and which two clocks disagreed.
    stale_detail: Optional[str] = None
    if isinstance(rel, Mapping) and window_anchor_stale(rel.get("window_anchor"), now):
        stale_detail = (f"releases window anchored at {rel.get('window_anchor')}, "
                        f"as_of {now.isoformat()}")
    elif isinstance(iss, Mapping) and window_anchor_stale(iss.get("window_anchor"), now):
        stale_detail = (f"issues_counts window anchored at {iss.get('window_anchor')}, "
                        f"as_of {now.isoformat()}")
    # RFC-0021: the same refusal for a commits record cut at the old cap --
    # its commit count is the cap, not the year's.
    commit_window = commit_domains.commit_window(c365)
    if stale_detail is None and commit_window is not None and commit_window.cut:
        stale_detail = commit_domains.COMMIT_WINDOW_CUT_DETAIL

    if stale_detail is not None:
        out["openssf_signals"] = (None, None, None, "collector_failure")
        out["openssf_signals_detail"] = (stale_detail, None, None, "derived")
    else:
        result = derive_pike_signals(
            gh, commit_window.rows if commit_window is not None else None, rel, iss, dep, now,
            window_total=commit_window.total if commit_window is not None and commit_window.sampled else None)
        if result is not None:
            signals, missing = result
            # Guarded like the pipeline_defenses `at` below -- every
            # recorded envelope carries fetched_at, but a hand-built or
            # legacy-shaped one might not, and losing the whole bundle to
            # a ValueError over one optional timestamp is out of
            # proportion to what it costs.
            if not missing:
                # All ten present: an OpenSSFSignals instance under
                # "openssf_signals", the shape v1's
                # asdict()/OpenSSFSignals(**sig) round trip
                # (scripts/derive.py, scripts/promote.py) expects.
                sources = [env_path(col) for col in ("github_meta", "commits_365d",
                                                     "releases", "issues_counts",
                                                     "dependents")]
                ats = [a for a in (gh_at, c365_at, rel_at, iss_at, dep_at) if a]
                out["openssf_signals"] = (OpenSSFSignals(**signals), sources,
                                           max(ats) if ats else None, "derived")
            else:
                # D-MISS-5 consequence for 2.2: only the dependents signal
                # is missing. "openssf_signals" itself is left unemitted
                # (v1 sees its "missing" placeholder); these two fields are
                # additive and read only by
                # porchlamp.derive.bundle._resolve_openssf_criticality's
                # proxy path.
                sources = [env_path(col) for col in ("github_meta", "commits_365d",
                                                     "releases", "issues_counts")]
                ats = [a for a in (gh_at, c365_at, rel_at, iss_at) if a]
                at = max(ats) if ats else None
                out["openssf_signals_partial"] = (signals, sources, at, "derived")
                out["openssf_signals_partial_missing"] = (sorted(missing), None, at, "derived")

    # #51 rule 5: the review/CI proxy is not computed on a record cut short of its window.
    prw_cut = isinstance(prw, Mapping) and pr_window_cut_short(prw)
    prs = (prw.get("prs") if not prw_cut else None) if isinstance(prw, Mapping) else None
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
    elif prw_cut and runs is not None:
        out["pipeline_defenses"] = (None, None, None, "collector_failure")
        out["pipeline_defenses_detail"] = (
            "pr_window envelope is truncated before reaching the 180d window start",
            None,
            None,
            "derived",
        )
    return out
