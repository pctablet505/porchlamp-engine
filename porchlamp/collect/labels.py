"""Newcomer-label issue collection for parameter 5.1 (D-PARAM 5.1, D-IN-4).

Labels are matched per repo rather than by an AND-label query
(``labels=good-first-issue,help-wanted``), which requires one issue to
carry both labels at once and returns no issues for ~99.9% of repos
(finding F2). This collector:

1. fetches the repo's own label list once (label names are never assumed),
2. keeps the labels matching the 5.1 alias regex,
3. issues one separate ``labels=<one>`` query per selected label,
4. unions the pages by issue number (an issue carrying two matched labels
   is recorded once),
5. keeps open + unassigned issues updated within 90 days, capped at the 200
   most recently updated (D-IN-4; issue queries are sorted by updated
   descending so the cap cannot hide in-window issues), and
6. records ``labels_matched`` in the envelope.

The envelope ``data`` is a list: the first record is the metadata
(``labels_matched``, ``queries``, ``truncated``) and the remaining records
are the unioned issue records, a superset of the legacy fields plus
``state``.
"""
from __future__ import annotations

import itertools
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import quote

from porchlamp.collect.envelope import write_envelope

__all__ = [
    "GfiResult",
    "NEWCOMER_ALIAS_RE",
    "RECENCY_DAYS",
    "ISSUE_CAP",
    "collect_newcomer_issues",
    "count_newcomer_issues",
    "filter_gfi_issues",
    "is_new_gfi_envelope",
    "needs_gather",
    "parse_gfi_envelope",
    "select_newcomer_labels",
    "union_by_number",
    "write_gfi_envelope",
]

# D-PARAM 5.1 alias regex, transcribed verbatim (case-insensitive).
NEWCOMER_ALIAS_RE = re.compile(
    r"good.?first|beginner|easy|newcomer|first.?timers?|help.?wanted|starter",
    re.IGNORECASE,
)
# D-IN-4: issues with newcomer labels are open, updated in the trailing 90
# days, capped at 200.
RECENCY_DAYS = 90
ISSUE_CAP = 200
PAGE_SIZE = 100
MAX_ISSUE_PAGES = 2
MAX_LABEL_PAGES = 10

# gather-style REST transport: fetch(endpoint, *gh api flags) -> parsed JSON.
Transport = Callable[..., Any]


@dataclass
class GfiResult:
    """One per-label newcomer-issue collection.

    ``issues`` is the filtered union (open + unassigned + updated within
    ``RECENCY_DAYS`` of the collection clock, capped at ``ISSUE_CAP`` newest,
    no duplicate issue numbers). ``queries`` records every issue query that
    was actually issued, one entry per page.
    """

    labels_matched: list[str]
    issues: list[dict]
    queries: list[str]
    truncated: bool
    fetched_at: str


def _parse_iso(value: Any) -> Optional[datetime]:
    """Parse a GitHub timestamp; None when absent or malformed."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _issue_label_names(raw: Any) -> list[str]:
    """Label names from a recorded ``labels`` field (string or dict forms)."""
    names: list[str] = []
    for entry in raw or []:
        if isinstance(entry, str):
            names.append(entry)
        elif isinstance(entry, dict) and isinstance(entry.get("name"), str):
            names.append(entry["name"])
    return names


def _shape_issue(record: Any) -> Optional[dict]:
    """One recorded issue record; None for non-issues.

    The /issues endpoint also returns pull requests; 5.1 counts issues, so
    PR records are dropped. ``state`` is recorded so a later re-validation
    can exclude closed issues even though the query is state=open.
    """
    if not isinstance(record, dict) or not isinstance(record.get("number"), int):
        return None
    if record.get("pull_request"):
        return None
    assignee = record.get("assignee")
    return {
        "number": record["number"],
        "state": record.get("state") or "open",
        "assignee": assignee.get("login") if isinstance(assignee, dict) else None,
        "updated_at": record.get("updated_at"),
        "labels": _issue_label_names(record.get("labels")),
    }


def select_newcomer_labels(label_names: Iterable[str]) -> list[str]:
    """Select the newcomer-alias labels from the repo's own label list.

    The input must come from the repo's label endpoint; names are never
    assumed. Order follows the repo's label list; duplicates are dropped.
    """
    seen: set[str] = set()
    selected: list[str] = []
    for name in label_names:
        if not isinstance(name, str) or not name or name in seen:
            continue
        if NEWCOMER_ALIAS_RE.search(name):
            seen.add(name)
            selected.append(name)
    return selected


def union_by_number(per_label: Mapping[str, list[dict]]) -> dict[int, dict]:
    """Merge per-label issue lists by issue number.

    An issue carrying several matched labels is recorded once: the first
    response that carried it wins.
    """
    union: dict[int, dict] = {}
    for issues in per_label.values():
        for issue in issues or []:
            number = issue.get("number") if isinstance(issue, dict) else None
            if isinstance(number, int) and number not in union:
                union[number] = issue
    return union


def filter_gfi_issues(issues: Iterable[dict], labels_matched: Iterable[str],
                      as_of: datetime) -> list[dict]:
    """Keep open, unassigned issues updated within 90 days of ``as_of``.

    The issue must also carry at least one of ``labels_matched``
    (case-insensitive); closed issues are dropped even when recorded.
    """
    matched = {name.lower() for name in labels_matched if isinstance(name, str)}
    cutoff = as_of - timedelta(days=RECENCY_DAYS)
    kept: list[dict] = []
    for issue in issues:
        if not isinstance(issue, dict) or issue.get("state") != "open":
            continue
        if issue.get("assignee") is not None:
            continue
        updated = _parse_iso(issue.get("updated_at"))
        if updated is None or updated < cutoff:
            continue
        labels = {name.lower() for name in _issue_label_names(issue.get("labels"))}
        if not labels & matched:
            continue
        kept.append(issue)
    return kept


def collect_newcomer_issues(fetch: Transport, owner: str, name: str,
                            now: Optional[datetime] = None) -> GfiResult:
    """Collect open, unassigned newcomer-labelled issues (D-IN-4, D-PARAM 5.1).

    ``fetch`` is the gather-style REST transport (``fetch(endpoint, *gh api
    flags)`` returning parsed JSON). The label list comes from the repo's own
    label endpoint; one issue query is issued per matched label (two pages
    each, the D-IN-4 cap) and the pages are unioned by issue number.
    ``now`` is the collection clock (defaults to the current UTC time) and
    anchors the 90-day recency window.
    """
    now = now or datetime.now(timezone.utc)
    base = f"repos/{owner}/{name}"
    cutoff = now - timedelta(days=RECENCY_DAYS)
    label_names: list[str] = []
    for page in range(1, MAX_LABEL_PAGES + 1):
        records = fetch(f"{base}/labels", "-X", "GET",
                        "-f", f"per_page={PAGE_SIZE}", "-f", f"page={page}")
        if not isinstance(records, list):
            break
        label_names.extend(record["name"] for record in records
                           if isinstance(record, dict)
                           and isinstance(record.get("name"), str))
        if len(records) < PAGE_SIZE:
            break
    matched = select_newcomer_labels(label_names)

    per_label: dict[str, list[dict]] = {}
    queries: list[str] = []
    cap_hides_window = False
    for label in matched:
        records_pages: list[list[Any]] = []
        for page in range(1, MAX_ISSUE_PAGES + 1):
            records = fetch(f"{base}/issues", "-X", "GET",
                            "-f", "state=open",
                            "-f", f"per_page={PAGE_SIZE}",
                            "-f", f"labels={label}",
                            "-f", "sort=updated", "-f", "direction=desc",
                            "-f", f"page={page}")
            if not isinstance(records, list):
                break
            records_pages.append(records)
            queries.append(f"{base}/issues?state=open&per_page={PAGE_SIZE}"
                           f"&labels={quote(label, safe='')}"
                           f"&sort=updated&direction=desc&page={page}")
            if len(records) < PAGE_SIZE:
                break
        shaped: list[dict] = []
        for record in itertools.chain(*records_pages):
            issue = _shape_issue(record)
            if issue is not None:
                shaped.append(issue)
        per_label[label] = shaped
        # The 200-cap hides in-window issues only when page 2 was full and
        # its last (oldest-updated) record is still inside the 90-day window.
        last_page = records_pages[-1] if records_pages else []
        if len(last_page) == PAGE_SIZE and len(records_pages) == MAX_ISSUE_PAGES:
            last_record = last_page[-1]
            last_updated = (_parse_iso(last_record.get("updated_at"))
                            if isinstance(last_record, dict) else None)
            cap_hides_window = cap_hides_window or (
                last_updated is not None and last_updated >= cutoff)

    kept = filter_gfi_issues(list(union_by_number(per_label).values()), matched, now)
    kept.sort(key=lambda item: (_parse_iso(item.get("updated_at")), item["number"]),
              reverse=True)
    truncated = cap_hides_window or len(kept) > ISSUE_CAP
    return GfiResult(labels_matched=matched, issues=kept[:ISSUE_CAP],
                     queries=queries, truncated=truncated,
                     fetched_at=now.isoformat())


def write_gfi_envelope(owner: str, name: str, result: GfiResult,
                       path: Path) -> Path:
    """Write the GFI envelope and return the envelope path.

    ``data`` is a list (first record = metadata, rest = issues), the shape
    the derive block reads; the hash over ``data`` is
    recorded by :func:`porchlamp.collect.envelope.write_envelope` (provenance =
    envelope path + hash).
    """
    data: list[dict] = [{
        "gfi_newcomer_v2": True,
        "labels_matched": result.labels_matched,
        "queries": result.queries,
        "truncated": result.truncated,
    }]
    data.extend(result.issues)
    return write_envelope(
        path,
        source=f"github-rest:repos/{owner}/{name}/issues",
        data=data,
        fetched_at=result.fetched_at,
    )


def is_new_gfi_envelope(data: Any) -> bool:
    """True for current-format GFI envelope data (metadata record first), else False."""
    return (isinstance(data, list) and bool(data)
            and isinstance(data[0], dict) and "labels_matched" in data[0])


def parse_gfi_envelope(data: Any) -> Optional[GfiResult]:
    """Read back current-format GFI envelope data; None for absent or legacy data."""
    if not is_new_gfi_envelope(data):
        return None
    meta = data[0]
    return GfiResult(
        labels_matched=list(meta.get("labels_matched") or []),
        issues=[issue for issue in data[1:] if isinstance(issue, dict)],
        queries=list(meta.get("queries") or []),
        truncated=bool(meta.get("truncated")),
        fetched_at="",
    )


def needs_gather(envelope_path: Path, force: bool) -> bool:
    """Whether the GFI envelope must (re)be collected under the per-label logic.

    True when the file is missing, ``force`` is set, or the stored envelope
    predates this collector (a corrupt file or the legacy bare-issue-list
    written by the old AND-label query).
    """
    if force or not envelope_path.is_file():
        return True
    try:
        data = json.loads(envelope_path.read_text(encoding="utf-8")).get("data")
    except (json.JSONDecodeError, OSError):
        return True
    return not is_new_gfi_envelope(data)


def count_newcomer_issues(data: Any, fetched_at: Optional[str],
                          now: datetime) -> Optional[int]:
    """Recount the 5.1 supply from a current-format GFI envelope.

    Re-applies open + unassigned + updated-within-90d to the recorded
    issues, anchored at the envelope's ``fetched_at`` (``now`` when the
    envelope records none), so the count is reproducible from recorded data.
    Returns None for absent or legacy-format data.
    """
    result = parse_gfi_envelope(data)
    if result is None:
        return None
    as_of = _parse_iso(fetched_at) or now
    return len(filter_gfi_issues(result.issues, result.labels_matched, as_of))
