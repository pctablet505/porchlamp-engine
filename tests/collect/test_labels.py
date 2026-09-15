"""Recorded-response tests for the per-label newcomer-issue collector.

No live calls: a fake transport serves the recorded repo label list and the
recorded per-label issue pages, and records every call so the exact queries
can be asserted (D-PARAM 5.1, D-IN-4).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cvi.collect import labels
from cvi.collect.envelope import envelope_hash, read_envelope

NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)
OWNER, NAME = "acme", "widget"
BASE = f"repos/{OWNER}/{NAME}"


def iso(days_ago: int) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def issue(number: int, days_ago: float, names: list[str],
          state: str = "open", assignee: str | None = None,
          pull_request: bool = False) -> dict:
    """One recorded GitHub issue-API record."""
    record: dict[str, Any] = {
        "number": number,
        "state": state,
        "assignee": {"login": assignee} if assignee else None,
        "updated_at": iso(days_ago),
        "labels": [{"name": n} for n in names],
    }
    if pull_request:
        record["pull_request"] = {"url": f"{BASE}/pulls/{number}"}
    return record


class RecordedTransport:
    """Serves recorded REST responses for one repo and records every call."""

    def __init__(self, label_names: list[str],
                 issues_pages: dict[str, list[list[dict]]]) -> None:
        self.label_names = label_names
        self.issues_pages = issues_pages
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    @staticmethod
    def _flag(flags: tuple[str, ...], key: str) -> str | None:
        for flag in flags:
            if flag.startswith(f"{key}="):
                return flag[len(key) + 1:]
        return None

    def __call__(self, endpoint: str, *flags: str) -> Any:
        self.calls.append((endpoint, flags))
        if endpoint == f"{BASE}/labels":
            return [{"name": n, "color": "00a2ff"} for n in self.label_names]
        if endpoint == f"{BASE}/issues":
            label = self._flag(flags, "labels")
            page = int(self._flag(flags, "page") or 1)
            pages = self.issues_pages.get(label or "", [])
            return pages[page - 1] if page <= len(pages) else []
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    def label_list_calls(self) -> int:
        return sum(1 for c in self.calls if c[0] == f"{BASE}/labels")

    def label_queries(self) -> list[str]:
        return [self._flag(c[1], "labels") for c in self.calls
                if c[0] == f"{BASE}/issues"]


def test_two_labels_two_queries_exact_union_and_matched():
    """good first issue + E-easy -> exactly two label queries, no duplicate
    issue numbers, an issue carrying both labels appears once, exact
    labels_matched, closed/assigned/91d-old issues excluded."""
    both = issue(10, 5, ["good first issue", "E-easy"])
    both_again = issue(10, 5, ["good first issue", "E-easy"])
    transport = RecordedTransport(
        ["good first issue", "E-easy", "bug", "docs"],
        {"good first issue": [
            [both, issue(11, 10, ["good first issue"]),
             issue(12, 1, ["good first issue"], assignee="bob")]],
         "E-easy": [
            [both_again, issue(13, 3, ["E-easy"]),
             issue(14, 91, ["E-easy"])]],
        },
    )
    result = labels.collect_newcomer_issues(transport, OWNER, NAME, now=NOW)

    assert result.labels_matched == ["good first issue", "E-easy"]
    assert transport.label_list_calls() == 1
    assert result.queries == [
        f"{BASE}/issues?state=open&per_page=100&labels=good%20first%20issue"
        f"&sort=updated&direction=desc&page=1",
        f"{BASE}/issues?state=open&per_page=100&labels=E-easy"
        f"&sort=updated&direction=desc&page=1",
    ]
    assert transport.label_queries() == ["good first issue", "E-easy"]
    assert result.truncated is False

    per_label = {q: transport_calls(transport, q)
                 for q in transport.label_queries()}
    union = labels.union_by_number(per_label)
    assert set(union) == {10, 11, 12, 13, 14}          # no duplicate numbers
    assert len(union) == 5                              # issue 10 appears once

    assert [i["number"] for i in result.issues] == [13, 10, 11]  # exact, newest first
    assert result.issues[0]["labels"] == ["E-easy"]


def transport_calls(transport: RecordedTransport, label: str) -> list[dict]:
    return transport.issues_pages[label][0]


def test_select_newcomer_labels_uses_repo_list_order_and_no_assumed_names():
    assert labels.select_newcomer_labels(
        ["bug", "good first issue", "E-easy", "help wanted",
         "first-timers", "beginner-friendly", "starter", "docs"]) == [
        "good first issue", "E-easy", "help wanted", "first-timers",
        "beginner-friendly", "starter"]
    assert labels.select_newcomer_labels(["bug", "docs", "kind/bug"]) == []
    assert labels.select_newcomer_labels([]) == []


def test_no_matching_labels_zero_issue_queries():
    """A repo whose label list has no alias match: zero issue queries, and
    the recorded envelope still documents the (empty) labels_matched."""
    transport = RecordedTransport(["bug", "docs"], {})
    result = labels.collect_newcomer_issues(transport, OWNER, NAME, now=NOW)
    assert result.labels_matched == []
    assert result.issues == []
    assert result.queries == []
    assert transport.label_queries() == []
    assert transport.label_list_calls() == 1


def test_only_help_wanted_label_still_works():
    """Over-correction guard: a repo whose only matching label is
    `help wanted` -> one query, correct count, recorded data."""
    transport = RecordedTransport(["help wanted", "bug"],
                                  {"help wanted": [[issue(21, 2, ["help wanted"])]],
                                   "bug": [[issue(22, 2, ["bug"])]],
                                  })
    result = labels.collect_newcomer_issues(transport, OWNER, NAME, now=NOW)
    assert result.labels_matched == ["help wanted"]
    assert transport.label_queries() == ["help wanted"]
    assert [i["number"] for i in result.issues] == [21]


def test_union_keeps_first_recording_of_a_both_labels_issue():
    """The first response to carry an issue wins; later duplicates are
    dropped by issue number, so the kept record is the exact dict."""
    first = issue(10, 5, ["good first issue", "E-easy"])
    second = issue(10, 6, ["good first issue", "E-easy", "extra"])
    union = labels.union_by_number({"good first issue": [first], "E-easy": [second]})
    assert union == {10: first}


def test_filter_excludes_closed_assigned_and_91d_old():
    """Exact 90-day recency + open + unassigned + matched-label filter on a
    recorded union (the envelope re-validation path)."""
    matched = ["good first issue", "E-easy"]
    kept = labels.filter_gfi_issues(
        [issue(1, 8, ["E-easy"]),
         issue(2, 2, ["good first issue"], state="closed"),
         issue(3, 2, ["E-easy"], assignee="bob"),
         issue(4, 91, ["E-easy"]),
         issue(5, 89, ["E-easy"]),
         issue(6, 8, ["bug"]),
         {"number": 7, "state": "open", "assignee": None}],
        matched, NOW)
    assert [i["number"] for i in kept] == [1, 5]
    assert labels.filter_gfi_issues([issue(8, 90, ["E-easy"])], matched, NOW) \
        == [issue(8, 90, ["E-easy"])]          # exactly-90d boundary is kept


def test_pull_requests_are_not_issues():
    pr = issue(30, 2, ["good first issue"], pull_request=True)
    assert labels.collect_newcomer_issues(
        RecordedTransport(["good first issue"],
                          {"good first issue": [[pr]]}),
        OWNER, NAME, now=NOW).issues == []
    assert labels._shape_issue(pr) is None


def test_issue_cap_200_newest_with_truncated_flag():
    pages: dict[str, list[list[dict]]] = {}
    number = 0
    for label in ("good first issue", "E-easy"):
        pages[label] = []
        for _ in range(2):
            page = []
            for _ in range(100):
                number += 1
                page.append(issue(number, 1 + (number % 80), [label]))
            pages[label].append(page)
    result = labels.collect_newcomer_issues(
        RecordedTransport(["good first issue", "E-easy"], pages),
        OWNER, NAME, now=NOW)
    assert len(result.issues) == 200
    assert len({i["number"] for i in result.issues}) == 200
    assert result.truncated is True
    updated = [i["updated_at"] for i in result.issues]
    assert updated == sorted(updated, reverse=True)


def test_truncated_when_cap_reaches_into_90d_window():
    """Page 2 full and its oldest record still inside the 90-day window:
    more in-window issues exist beyond the cap -> truncated even though
    the filtered count is not above the cap."""
    page1 = [issue(300 - i, 0.5 + i * 0.4, ["good first issue"]) for i in range(100)]
    page2 = [issue(400 - i, 40.0 + i * 0.4, ["good first issue"]) for i in range(100)]
    result = labels.collect_newcomer_issues(
        RecordedTransport(["good first issue"],
                          {"good first issue": [page1, page2]}),
        OWNER, NAME, now=NOW)
    assert len(result.issues) == 200
    assert result.truncated is True


def test_needs_gather_detects_missing_legacy_corrupt_and_force():
    fresh = labels.GfiResult(labels_matched=[], issues=[], queries=[],
                             truncated=False, fetched_at=NOW.isoformat())
    p = Path("/tmp/never/used/gfi.json")
    assert labels.needs_gather(p, force=False) is True
    p.parent.mkdir(parents=True, exist_ok=True)
    labels.write_gfi_envelope(OWNER, NAME, fresh, p)
    assert labels.needs_gather(p, force=False) is False
    assert labels.needs_gather(p, force=True) is True
    p.write_text(json_dumps_legacy(), encoding="utf-8")
    assert labels.needs_gather(p, force=False) is True
    p.write_text("{not json", encoding="utf-8")
    assert labels.needs_gather(p, force=False) is True


def json_dumps_legacy() -> str:
    import json
    return json.dumps({"fetched_at": "2026-09-01T00:00:00Z", "source": "old",
                       "data": [{"number": 1, "assignee": None,
                                 "updated_at": iso(1), "labels": ["good-first-issue"]}]})


def test_envelope_data_roundtrip_and_provenance_hash(tmp_path: Path):
    transport = RecordedTransport(
        ["good first issue", "E-easy"],
        {"good first issue": [[issue(10, 5, ["good first issue", "E-easy"])]],
         "E-easy": [[issue(10, 5, ["good first issue", "E-easy"]),
                     issue(13, 3, ["E-easy"])]],
        })
    result = labels.collect_newcomer_issues(transport, OWNER, NAME, now=NOW)
    path = labels.write_gfi_envelope(OWNER, NAME, result,
                                     tmp_path / "gfi" / f"{OWNER}__{NAME}.json")
    envelope = read_envelope(path)
    assert envelope.hash_matches_data is True
    assert envelope.sha256 == envelope_hash(envelope.data)
    assert envelope.source == f"github-rest:{BASE}/issues"
    assert envelope.fetched_at == NOW.isoformat()

    parsed = labels.parse_gfi_envelope(envelope.data)
    assert parsed is not None
    assert parsed.labels_matched == ["good first issue", "E-easy"]
    assert [i["number"] for i in parsed.issues] == [13, 10]  # newest first
    assert labels.is_new_gfi_envelope(envelope.data) is True
    assert labels.is_new_gfi_envelope([{"number": 1}]) is False
    assert labels.is_new_gfi_envelope(None) is False
    assert labels.is_new_gfi_envelope({"issues": []}) is False


def test_count_newcomer_issues_revalidates_recorded_envelope():
    """Closed and 91-day-old recorded issues are excluded (exact counts),
    anchored at the envelope's fetched_at, not the derive clock."""
    data = [
        {"gfi_newcomer_v2": True,
         "labels_matched": ["good first issue", "E-easy"],
         "queries": [], "truncated": False},
        issue(1, 8, ["E-easy"]),
        issue(2, 2, ["good first issue"], state="closed"),
        issue(3, 2, ["E-easy"], assignee="bob"),
        issue(4, 91, ["E-easy"]),
        issue(5, 89, ["E-easy"]),
    ]
    assert labels.count_newcomer_issues(data, NOW.isoformat(), NOW) == 2
    # fetched 2026-03-01: an issue updated 29d before fetch is in-window even
    # though it is months older than `now`.
    old = [data[0], {"number": 9, "state": "open", "assignee": None,
                     "updated_at": "2026-02-01T00:00:00+00:00",
                     "labels": ["good first issue"]}]
    assert labels.count_newcomer_issues(old, "2026-03-01T00:00:00+00:00", NOW) == 1
    assert labels.count_newcomer_issues([{"number": 1}], NOW.isoformat(), NOW) is None
    assert labels.count_newcomer_issues(None, None, NOW) is None
