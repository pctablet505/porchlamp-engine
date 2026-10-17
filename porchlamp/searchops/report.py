"""Pure report builders: URL Inspection records and summaries, the sitemaps table, the Search
Analytics report and the Bing stats summary. :mod:`porchlamp.searchops.cli` writes and prints them.

Search Console marks a sitemap's ``contents[].indexed`` "Deprecated; do not use", so the sitemaps
table never reads it; an indexed count comes from URL Inspection only.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from porchlamp.searchops.plan import is_project

INSPECTION_FIELDS = (
    "verdict", "coverageState", "robotsTxtState", "indexingState", "pageFetchState",
    "lastCrawlTime", "crawledAs", "googleCanonical", "userCanonical",
)
UNKNOWN_TO_GOOGLE = "URL is unknown to Google"
SITEMAP_FIELDS = ("path", "lastSubmitted", "lastDownloaded", "isPending", "isSitemapsIndex")
BLACK_HOLE_RE = re.compile(r"black[\s-]*holes?", re.IGNORECASE)
_BING_DATE_RE = re.compile(r"/Date\((-?\d+)(?:([+-])(\d{2})(\d{2}))?\)/")


def inspection_record(url: str, day: str, answer: Mapping[str, Any]) -> dict[str, Any]:
    """One URL's stored reading from a URL Inspection answer."""
    status = (answer.get("inspectionResult") or {}).get("indexStatusResult") or {}
    record: dict[str, Any] = {"url": url, "date": day}
    record.update({field: status.get(field) for field in INSPECTION_FIELDS})
    google, user = status.get("googleCanonical"), status.get("userCanonical")
    record["canonicalAgrees"] = google == user if google and user else None
    record["sitemaps"] = list(status.get("sitemap") or [])
    record["referringUrls"] = len(status.get("referringUrls") or [])
    return record


def failed_record(url: str, day: str, message: str) -> dict[str, Any]:
    """A URL whose inspection call failed; it never replaces an earlier reading."""
    return {"url": url, "date": day, "error": message}


def merge_latest(latest: Mapping[str, Mapping[str, Any]], records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The newest reading per URL: a reading replaces one of the same or an earlier date."""
    merged = dict(latest)
    for record in records:
        if "error" in record:
            continue
        previous = merged.get(record["url"])
        if previous is None or record["date"] >= previous["date"]:
            merged[record["url"]] = dict(record)
    return merged


def inspection_summary(records: Iterable[Mapping[str, Any]], urls: Sequence[str] | None = None) -> dict[str, Any]:
    """Counts over readings: indexed (verdict PASS), unknown to Google, canonical mismatches, and the
    verdict and coverage tallies; with ``urls``, also how many of them were never inspected."""
    records = list(records)
    readings = [record for record in records if "error" not in record]
    summary: dict[str, Any] = {
        "inspected": len(readings),
        "failed": len(records) - len(readings),
        "indexed": sum(record.get("verdict") == "PASS" for record in readings),
        "unknownToGoogle": sum(record.get("coverageState") == UNKNOWN_TO_GOOGLE for record in readings),
        "canonicalMismatch": sum(record.get("canonicalAgrees") is False for record in readings),
        "verdict": dict(Counter(str(record.get("verdict")) for record in readings).most_common()),
        "coverageState": dict(Counter(str(record.get("coverageState")) for record in readings).most_common()),
    }
    if urls is not None:
        seen = {record["url"] for record in readings}
        summary["sitemapUrls"] = len(urls)
        summary["neverInspected"] = sum(url not in seen for url in urls)
    return summary


def sitemap_rows(answer: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The sitemaps.list answer as rows; the API sends its int64 counts as strings."""
    rows = []
    for entry in answer.get("sitemap") or []:
        row: dict[str, Any] = {field: entry.get(field) for field in SITEMAP_FIELDS}
        row["errors"] = int(entry.get("errors") or 0)
        row["warnings"] = int(entry.get("warnings") or 0)
        row["submitted"] = sum(int(content.get("submitted") or 0) for content in entry.get("contents") or [])
        rows.append(row)
    return rows


def query_rows(answers: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Search Analytics answers (dimensions query, page) as flat rows."""
    rows = []
    for answer in answers:
        for row in answer.get("rows") or []:
            keys = list(row.get("keys") or []) + [None, None]
            rows.append({
                "query": keys[0], "page": keys[1],
                "clicks": row.get("clicks", 0), "impressions": row.get("impressions", 0),
                "ctr": row.get("ctr", 0), "position": row.get("position"),
            })
    return rows


def queries_summary(rows: Sequence[Mapping[str, Any]], top: int = 20) -> dict[str, Any]:
    """Totals, the top queries and pages by impressions, the project pages seen, and every query
    that names black holes."""
    impressions: Counter[str] = Counter()
    clicks: Counter[str] = Counter()
    page_impressions: Counter[str] = Counter()
    for row in rows:
        impressions[str(row["query"])] += row["impressions"]
        clicks[str(row["query"])] += row["clicks"]
        page_impressions[str(row["page"])] += row["impressions"]
    return {
        "rows": len(rows),
        "clicks": sum(row["clicks"] for row in rows),
        "impressions": sum(row["impressions"] for row in rows),
        "topQueries": [
            {"query": query, "impressions": count, "clicks": clicks[query]}
            for query, count in impressions.most_common(top)
        ],
        "topPages": [{"page": page, "impressions": count} for page, count in page_impressions.most_common(top)],
        "projectPagesWithImpressions": sum(1 for page in page_impressions if is_project(page)),
        "blackHoleQueries": sorted(query for query in impressions if BLACK_HOLE_RE.search(query)),
    }


def bing_date(value: Any) -> str | None:
    """A WCF JSON date (``/Date(1316156400000-0700)/``) as the ISO date in its own offset."""
    match = _BING_DATE_RE.fullmatch(str(value or ""))
    if match is None:
        return None
    offset = timedelta(0)
    if match.group(2):
        offset = timedelta(hours=int(match.group(3)), minutes=int(match.group(4)))
        offset = -offset if match.group(2) == "-" else offset
    moment = datetime.fromtimestamp(int(match.group(1)) / 1000, tz=timezone(offset))
    return moment.date().isoformat()


def bing_summary(stats: Mapping[str, Any], top: int = 10) -> dict[str, Any]:
    """Bing traffic totals, the top queries and pages by impressions, and the latest crawl row."""
    traffic = list(stats.get("GetRankAndTrafficStats") or [])
    days = sorted(day for day in (bing_date(row.get("Date")) for row in traffic) if day)
    crawl = sorted(stats.get("GetCrawlStats") or [], key=lambda row: bing_date(row.get("Date")) or "")
    latest = crawl[-1] if crawl else None
    return {
        "traffic": {
            "days": len(traffic),
            "from": days[0] if days else None,
            "to": days[-1] if days else None,
            "clicks": sum(int(row.get("Clicks") or 0) for row in traffic),
            "impressions": sum(int(row.get("Impressions") or 0) for row in traffic),
        },
        "topQueries": _bing_top(stats.get("GetQueryStats") or [], top),
        "topPages": _bing_top(stats.get("GetPageStats") or [], top),
        "crawl": None if latest is None else {
            "date": bing_date(latest.get("Date")),
            **{field: latest.get(field) for field in ("InIndex", "CrawledPages", "CrawlErrors", "Code2xx", "Code4xx", "Code5xx")},
        },
    }


def _bing_top(rows: Iterable[Mapping[str, Any]], top: int) -> list[dict[str, Any]]:
    """Bing's query and page rows (the ``Query`` field holds the query or the page) summed per key."""
    impressions: Counter[str] = Counter()
    clicks: Counter[str] = Counter()
    for row in rows:
        impressions[str(row.get("Query"))] += int(row.get("Impressions") or 0)
        clicks[str(row.get("Query"))] += int(row.get("Clicks") or 0)
    return [{"key": key, "impressions": count, "clicks": clicks[key]} for key, count in impressions.most_common(top)]
