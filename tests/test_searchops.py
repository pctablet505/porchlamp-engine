"""``porchlamp.searchops``: the pure parts of the search operations tool, and its secret handling.

What is covered: batching, the published tree's key file and sitemaps, the URL Inspection rotation
within the daily quota, request URL and body building, the reports, the redaction of every secret
(a canary planted in fake credential files must never reach stdout, stderr, a log, an exception or a
report) and the exits for a missing or exposed credential.

No test talks to a server, live or stand-in (owner rule: the owner's first real run proves the thin
network layer). A dry run is checked under ``block_live_network``, which fails the test if any
connection is even attempted.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import traceback
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from porchlamp.searchops import bing, cli, gsc
from porchlamp.searchops.credentials import BING_KEY_FILE_ENV, GSC_CREDENTIALS_ENV, Redactor
from porchlamp.searchops.pages import SITE, SITEMAP_URL, SiteError, indexnow_key, read_sitemaps
from porchlamp.searchops.plan import (
    BING_BATCH,
    INDEXNOW_BATCH,
    INSPECTION_DAILY_LIMIT,
    batched,
    inspection_plan,
    is_project,
    priority_list,
    watch_list,
)
from porchlamp.searchops.report import (
    bing_date,
    bing_summary,
    failed_record,
    inspection_record,
    inspection_summary,
    merge_latest,
    queries_summary,
    query_rows,
    sitemap_rows,
)
from porchlamp.searchops.transport import ApiError, Http, failure_text

REPO_ROOT = Path(__file__).resolve().parents[1]
HOST = "porchlamp.org"
KEY = "0123456789abcdef" * 2  # an IndexNow key: public by design, served at the site root
DAY = date(2026, 10, 17)
DAY_ARG = DAY.isoformat()
PAGES = ("", "black-holes/", "methodology/")
READ_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
WRITE_SCOPE = "https://www.googleapis.com/auth/webmasters"

# Planted secrets: none of them may ever reach any output.
BING_CANARY = "canary-bing-api-key-" + "q" * 12
PEM_CANARY = "CANARYPRIVATEKEYBODY" * 3 + "AAAA"
KEY_ID_CANARY = "canary-private-key-id-" + "z" * 18
CANARIES = (BING_CANARY, PEM_CANARY, KEY_ID_CANARY)


def page_urls() -> list[str]:
    return [f"https://{HOST}/{page}" for page in PAGES]


def project_urls(count: int) -> list[str]:
    return [f"https://{HOST}/projects/owner-{number}/name-{number}/" for number in range(count)]


def _urlset(urls: list[str]) -> str:
    rows = "".join(f"<url><loc>{url}</loc><lastmod>2026-10-17</lastmod></url>" for url in urls)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{rows}</urlset>\n'


def _index(urls: list[str]) -> str:
    rows = "".join(f"<sitemap><loc>{url}</loc></sitemap>" for url in urls)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{rows}</sitemapindex>\n'


def make_site(root: Path, projects: int = 5) -> Path:
    """A published tree shaped like the export's: key file, other .txt files, a sitemap index of two."""
    root.mkdir(parents=True)
    (root / f"{KEY}.txt").write_text(f"{KEY}\n")
    (root / "robots.txt").write_text("User-agent: *\nAllow: /\n")
    (root / "llms.txt").write_text("# Porchlamp\n")
    (root / "sitemap.xml").write_text(_index([f"https://{HOST}/sitemap-pages.xml", f"https://{HOST}/sitemap-projects.xml"]))
    (root / "sitemap-pages.xml").write_text(_urlset(page_urls()))
    (root / "sitemap-projects.xml").write_text(_urlset(project_urls(projects)))
    return root


def service_account(**fields: str) -> dict[str, str]:
    """A service-account file's shape, with canaries where the secrets go."""
    info = {
        "type": "service_account",
        "project_id": "porchlamp-search-ops",
        "private_key_id": KEY_ID_CANARY,
        "private_key": f"-----BEGIN PRIVATE KEY-----\n{PEM_CANARY}\n{PEM_CANARY}\n-----END PRIVATE KEY-----\n",
        "client_email": "porchlamp-search-ops@porchlamp-search-ops.iam.gserviceaccount.com",
        "client_id": "100000000000000000001",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    info.update(fields)
    return info


def plant_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variable: str, name: str, text: str,
               mode: int = 0o600) -> Path:
    path = tmp_path / "credentials" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    path.chmod(mode)
    monkeypatch.setenv(variable, str(path))
    return path


def plant_bing_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str = f"{BING_CANARY}\n",
                   mode: int = 0o600) -> Path:
    return plant_file(tmp_path, monkeypatch, BING_KEY_FILE_ENV, "bing-webmaster-api.key", text, mode)


def plant_service_account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str | None = None) -> Path:
    body = json.dumps(service_account()) if text is None else text
    return plant_file(tmp_path, monkeypatch, GSC_CREDENTIALS_ENV, "gsc-service-account.json", body)


def reading(url: str, day: str, verdict: str, coverage: str = "Submitted and indexed") -> dict[str, object]:
    return {"url": url, "date": day, "verdict": verdict, "coverageState": coverage, "canonicalAgrees": True}


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run state under the test's own directory; no credential variable leaks in from the shell."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.delenv(GSC_CREDENTIALS_ENV, raising=False)
    monkeypatch.delenv(BING_KEY_FILE_ENV, raising=False)
    return tmp_path / "xdg-state" / "porchlamp" / "searchops"


# ---------------------------------------------------------------- batches, the site tree, dry runs


def test_batches_hold_at_most_the_limit_and_keep_every_url_once_in_order() -> None:
    urls = project_urls(2 * INDEXNOW_BATCH + 1)
    batches = batched(urls, INDEXNOW_BATCH)
    assert [len(batch) for batch in batches] == [INDEXNOW_BATCH, INDEXNOW_BATCH, 1]
    assert [url for batch in batches for url in batch] == urls
    assert [len(batch) for batch in batched(urls[:1001], BING_BATCH)] == [500, 500, 1]
    assert batched([], INDEXNOW_BATCH) == []
    with pytest.raises(ValueError):
        batched(urls, 0)


def test_the_indexnow_key_is_the_one_32_hex_file_at_the_root_that_holds_its_own_name(tmp_path: Path) -> None:
    site = make_site(tmp_path / "site")
    assert indexnow_key(site) == KEY
    (site / f"{KEY}.txt").write_text("not the key\n")
    with pytest.raises(SiteError, match="does not hold its own name"):
        indexnow_key(site)
    (site / f"{KEY}.txt").write_text(KEY)
    (site / f"{'f' * 32}.txt").write_text("f" * 32)
    with pytest.raises(SiteError, match="exactly one IndexNow key file"):
        indexnow_key(site)


def test_sitemap_urls_come_from_every_child_of_the_index_once_in_order_on_one_host(tmp_path: Path) -> None:
    site = make_site(tmp_path / "site", projects=4)
    maps = read_sitemaps(site)
    assert maps.host == HOST
    assert maps.urls == tuple(page_urls() + project_urls(4))
    assert maps.files == (SITEMAP_URL, f"https://{HOST}/sitemap-pages.xml", f"https://{HOST}/sitemap-projects.xml")
    (site / "sitemap-projects.xml").write_text(_urlset(project_urls(4) + project_urls(2)))
    assert read_sitemaps(site).urls == maps.urls  # a URL listed twice is sent once
    (site / "sitemap-projects.xml").write_text(_urlset(["https://www.porchlamp.org/projects/a/b/"]))
    with pytest.raises(SiteError, match="2 hosts"):
        read_sitemaps(site)


def test_indexnow_dry_run_prints_the_plan_in_batches_and_opens_no_connection(
        tmp_path: Path, isolated_state: Path, capsys: pytest.CaptureFixture[str], block_live_network: None) -> None:
    site = make_site(tmp_path / "site", projects=INDEXNOW_BATCH + 1 - len(PAGES))
    assert cli.main(["indexnow", "--site", str(site), "--dry-run"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert f"urls 10,001 on {HOST} (3 pages, 9,998 projects)" in out
    assert f"key file https://{HOST}/{KEY}.txt (holds its own name)" in out
    assert "batches 2 of at most 10,000 URLs: 10,000, 1" in out
    assert out[-1] == "dry run: nothing sent, nothing written"
    assert not isolated_state.exists()


def test_the_module_runs_from_the_command_line(tmp_path: Path) -> None:
    site = make_site(tmp_path / "site", projects=4)
    result = subprocess.run(
        [sys.executable, "-m", "porchlamp.searchops", "indexnow", "--site", str(site), "--dry-run"],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=120,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    assert result.returncode == 0, result.stderr
    assert f"urls 7 on {HOST} (3 pages, 4 projects)" in result.stdout
    assert "batches 1 of at most 10,000 URLs: 7" in result.stdout


# ---------------------------------------------------------------- URL Inspection: rotation and quota


def test_seven_consecutive_days_inspect_every_project_page_once() -> None:
    projects = project_urls(4_322)
    urls = page_urls() + projects
    plans = [inspection_plan(urls, DAY + timedelta(days=offset)) for offset in range(7)]
    assert {plan.slice_count for plan in plans} == {7}
    by_slice = sorted(plans, key=lambda plan: plan.slice_index)
    assert [plan.slice_index for plan in by_slice] == list(range(7))
    covered = [url for plan in by_slice for url in projects[plan.slice_first - 1:plan.slice_last]]
    assert covered == projects  # every project once, in rank order: no gap, no overlap
    assert [plan.slice_last - plan.slice_first + 1 for plan in by_slice] == [700] * 6 + [122]
    for plan in plans:
        assert set(projects[plan.slice_first - 1:plan.slice_last]) <= set(plan.urls)
        assert len(plan.urls) <= 700 + plan.watch
    assert inspection_plan(urls, DAY + timedelta(days=7)).urls == plans[0].urls  # the cycle repeats


def test_an_inspection_run_stays_inside_the_daily_quota_and_skips_urls_done_that_day() -> None:
    urls = page_urls() + project_urls(4_322)
    full = inspection_plan(urls, DAY)
    assert full.cut == 0 and len(full.urls) == full.wanted
    nearly = inspection_plan(urls, DAY, used=1_995)
    assert nearly.urls == full.urls[:5]  # the watch list goes first
    assert nearly.cut == full.wanted - 5
    assert inspection_plan(urls, DAY, used=INSPECTION_DAILY_LIMIT).urls == ()
    assert inspection_plan(urls, DAY, used=2_600).urls == ()
    wide = inspection_plan(urls, DAY, slice_size=5_000)
    assert len(wide.urls) == INSPECTION_DAILY_LIMIT  # a slice wider than the quota is cut to it
    resumed = inspection_plan(urls, DAY, used=100, done=full.urls[:100])
    assert resumed.urls == full.urls[100:]


def test_the_watch_list_and_the_priority_list_take_every_page_then_projects_by_rank() -> None:
    projects = project_urls(4_322)
    urls = page_urls() + projects
    picks = (0, 500, 999, 1000, 1500, 1999, 2000, 2500, 2999, 3000, 3500, 3999, 4000, 4161, 4321)
    assert watch_list(urls) == page_urls() + [projects[index] for index in picks]
    assert priority_list(urls, 50) == page_urls() + projects[:50]
    assert priority_list(urls, 0) == page_urls()
    assert is_project("https://porchlamp.org/projects/keras-team/keras/")
    assert not is_project("https://porchlamp.org/projects/")
    assert not is_project("https://porchlamp.org/rust/")


# ---------------------------------------------------------------- request URLs, bodies and scopes


def test_search_console_requests_carry_the_property_and_the_sitemap_as_encoded_path_segments(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    api = "https://www.googleapis.com/webmasters/v3/sites/sc-domain%3Aporchlamp.org"
    assert gsc.sites_url() == "https://www.googleapis.com/webmasters/v3/sites"
    assert gsc.sitemaps_url() == f"{api}/sitemaps"
    assert gsc.sitemap_url(SITEMAP_URL) == f"{api}/sitemaps/https%3A%2F%2Fporchlamp.org%2Fsitemap.xml"
    assert gsc.search_analytics_url() == f"{api}/searchAnalytics/query"
    url = "https://porchlamp.org/projects/keras-team/keras/"
    assert gsc.inspection_body(url) == {"inspectionUrl": url, "siteUrl": "sc-domain:porchlamp.org"}
    assert gsc.query_body(date(2026, 10, 14), date(2026, 10, 16), start_row=25_000) == {
        "startDate": "2026-10-14", "endDate": "2026-10-16", "dimensions": ["query", "page"],
        "type": "web", "rowLimit": 25_000, "startRow": 25_000,
    }
    plant_service_account(tmp_path, monkeypatch)
    assert cli.main(["gsc", "queries", "--date", DAY_ARG, "--days", "3", "--dry-run"]) == 0
    assert "range 2026-10-14 to 2026-10-16: web search, by query and page" in capsys.readouterr().out


def test_bing_and_indexnow_requests_carry_the_key_where_each_protocol_puts_it() -> None:
    assert bing.bing_url("GetUrlSubmissionQuota", "k", siteUrl=SITE) == (
        "https://ssl.bing.com/webmaster/api.svc/json/GetUrlSubmissionQuota?siteUrl=https%3A%2F%2Fporchlamp.org%2F&apikey=k")
    assert bing.bing_url("SubmitUrlBatch", "k") == "https://ssl.bing.com/webmaster/api.svc/json/SubmitUrlBatch?apikey=k"
    urls = project_urls(2)
    assert bing.indexnow_body(HOST, KEY, urls) == {
        "host": HOST, "key": KEY, "keyLocation": f"https://{HOST}/{KEY}.txt", "urlList": urls,
    }
    assert bing.indexnow_answer(200) == "received"
    assert bing.indexnow_answer(403).startswith("key not valid")


def test_bing_takes_only_as_many_urls_as_both_its_daily_and_monthly_quota_leave() -> None:
    assert bing.submission_room({"DailyQuota": 5, "MonthlyQuota": 24}) == 5  # Bing's own sample answer
    assert bing.submission_room({"DailyQuota": 100, "MonthlyQuota": 24}) == 24
    assert bing.submission_room({"DailyQuota": "10000", "MonthlyQuota": "300000"}) == 10_000
    assert bing.submission_room({"DailyQuota": 10}) == 0  # a quota it did not state sends nothing
    assert bing.submission_room(None) == 0


def test_an_error_answer_is_reported_in_the_services_own_words() -> None:
    def answer(status: int, **body: object) -> httpx.Response:
        return httpx.Response(status, request=httpx.Request("POST", "https://api.indexnow.org/indexnow"), **body)

    indexnow = answer(403, json={"errorCode": "SiteVerificationNotCompleted", "details": None,
                                 "message": "Site Verification is not completed. Please wait for some time for the "
                                            "verification to complete and try again."})
    assert failure_text("IndexNow", indexnow) == (
        "IndexNow: HTTP 403 Forbidden: SiteVerificationNotCompleted: Site Verification is not completed. "
        "Please wait for some time for the verification to complete and try again.")
    google = answer(403, json={"error": {"code": 403, "status": "PERMISSION_DENIED",
                                         "message": "User does not have sufficient permission for site 'sc-domain:porchlamp.org'."}})
    assert failure_text("Search Console sites.list", google) == (
        "Search Console sites.list: HTTP 403 Forbidden: User does not have sufficient permission for site "
        "'sc-domain:porchlamp.org'.")
    token = answer(400, json={"error": "invalid_grant", "error_description": "Invalid JWT Signature."})
    assert failure_text("Google token request", token) == (
        "Google token request: HTTP 400 Bad Request: invalid_grant: Invalid JWT Signature.")
    page = answer(502, text="<html>\n<body>Bad   gateway</body></html>")
    assert failure_text("Bing GetCrawlStats", page) == "Bing GetCrawlStats: HTTP 502 Bad Gateway: <html> <body>Bad gateway</body></html>"


def test_only_a_sitemap_submission_asks_for_the_read_write_scope(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
        block_live_network: None) -> None:
    assert gsc.scopes(write=True) == [WRITE_SCOPE]
    assert gsc.scopes(write=False) == [READ_SCOPE]
    plant_service_account(tmp_path, monkeypatch)
    site = make_site(tmp_path / "site")
    asked = {}
    for argv in (["gsc", "sitemaps", "--submit"], ["gsc", "sitemaps"],
                 ["gsc", "inspect", "--site", str(site), "--date", DAY_ARG], ["gsc", "queries", "--date", DAY_ARG]):
        assert cli.main([*argv, "--dry-run"]) == 0
        account = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("account "))
        asked[" ".join(argv[:3])] = account.rsplit(" scope ", 1)[1]
    assert asked == {"gsc sitemaps --submit": WRITE_SCOPE, "gsc sitemaps": READ_SCOPE,
                     "gsc inspect --site": READ_SCOPE, "gsc queries --date": READ_SCOPE}
    restricted = {"siteEntry": [{"siteUrl": "sc-domain:porchlamp.org", "permissionLevel": "siteRestrictedUser"}]}
    full = {"siteEntry": [{"siteUrl": "sc-domain:porchlamp.org", "permissionLevel": "siteFullUser"}]}
    assert gsc.access_problem(restricted, write=False) is None
    assert "needs Full or Owner" in str(gsc.access_problem(restricted, write=True))
    assert gsc.access_problem(full, write=True) is None
    assert "cannot see sc-domain:porchlamp.org" in str(gsc.access_problem({}, write=False))


# ---------------------------------------------------------------- reports


def test_an_inspection_record_keeps_the_index_fields_and_whether_the_canonicals_agree() -> None:
    url = "https://porchlamp.org/projects/keras-team/keras/"
    unknown = {"inspectionResult": {"inspectionResultLink": "https://search.google.com/search-console/inspect",
                                    "indexStatusResult": {"verdict": "NEUTRAL", "coverageState": "URL is unknown to Google",
                                                          "robotsTxtState": "ROBOTS_TXT_STATE_UNSPECIFIED",
                                                          "indexingState": "INDEXING_STATE_UNSPECIFIED",
                                                          "pageFetchState": "PAGE_FETCH_STATE_UNSPECIFIED"}}}
    assert inspection_record(url, DAY_ARG, unknown) == {
        "url": url, "date": DAY_ARG, "verdict": "NEUTRAL", "coverageState": "URL is unknown to Google",
        "robotsTxtState": "ROBOTS_TXT_STATE_UNSPECIFIED", "indexingState": "INDEXING_STATE_UNSPECIFIED",
        "pageFetchState": "PAGE_FETCH_STATE_UNSPECIFIED", "lastCrawlTime": None, "crawledAs": None,
        "googleCanonical": None, "userCanonical": None, "canonicalAgrees": None, "sitemaps": [], "referringUrls": 0,
    }
    status = {"verdict": "PASS", "coverageState": "Submitted and indexed", "googleCanonical": url, "userCanonical": url,
              "sitemap": ["https://porchlamp.org/sitemap-projects.xml"], "referringUrls": ["https://porchlamp.org/", "https://porchlamp.org/rust/"],
              "lastCrawlTime": "2026-10-20T08:00:00Z", "crawledAs": "MOBILE"}
    indexed = inspection_record(url, DAY_ARG, {"inspectionResult": {"indexStatusResult": status}})
    assert indexed["canonicalAgrees"] is True
    assert indexed["sitemaps"] == ["https://porchlamp.org/sitemap-projects.xml"] and indexed["referringUrls"] == 2
    moved = {**status, "googleCanonical": "https://porchlamp.org/"}
    assert inspection_record(url, DAY_ARG, {"inspectionResult": {"indexStatusResult": moved}})["canonicalAgrees"] is False


def test_the_latest_reading_per_url_is_the_newest_and_a_failed_call_never_replaces_it() -> None:
    url = project_urls(1)[0]
    latest = merge_latest({}, [reading(url, "2026-10-17", "NEUTRAL")])
    latest = merge_latest(latest, [reading(url, "2026-10-16", "PASS")])
    assert latest[url]["verdict"] == "NEUTRAL"  # an older reading does not win
    latest = merge_latest(latest, [reading(url, "2026-10-18", "PASS")])
    assert latest[url]["verdict"] == "PASS"
    latest = merge_latest(latest, [failed_record(url, "2026-10-19", "URL Inspection: HTTP 500")])
    assert latest[url] == reading(url, "2026-10-18", "PASS")


def test_the_inspection_summary_counts_indexed_unknown_mismatched_and_never_inspected() -> None:
    urls = project_urls(5)
    records = [
        reading(urls[0], DAY_ARG, "PASS"),
        reading(urls[1], DAY_ARG, "NEUTRAL", "URL is unknown to Google"),
        {**reading(urls[2], DAY_ARG, "PASS"), "canonicalAgrees": False},
        failed_record(urls[3], DAY_ARG, "URL Inspection: HTTP 500"),
    ]
    summary = inspection_summary(records, urls)
    assert {key: summary[key] for key in ("inspected", "failed", "indexed", "unknownToGoogle", "canonicalMismatch",
                                          "sitemapUrls", "neverInspected")} == {
        "inspected": 3, "failed": 1, "indexed": 2, "unknownToGoogle": 1, "canonicalMismatch": 1,
        "sitemapUrls": 5, "neverInspected": 2,
    }
    assert summary["verdict"] == {"PASS": 2, "NEUTRAL": 1}


def test_the_sitemaps_table_reads_string_counts_and_never_the_deprecated_indexed_field() -> None:
    answer = {"sitemap": [{"path": SITEMAP_URL, "lastSubmitted": "2026-10-17T10:15:00Z", "isPending": False,
                           "isSitemapsIndex": True, "lastDownloaded": "2026-10-17T10:17:00Z",
                           "warnings": "0", "errors": "1",
                           "contents": [{"type": "web", "submitted": "4337", "indexed": "777"}]}]}
    rows = sitemap_rows(answer)
    assert rows == [{"path": SITEMAP_URL, "lastSubmitted": "2026-10-17T10:15:00Z",
                     "lastDownloaded": "2026-10-17T10:17:00Z", "isPending": False, "isSitemapsIndex": True,
                     "errors": 1, "warnings": 0, "submitted": 4337}]
    assert "777" not in json.dumps(rows)


def test_the_queries_report_totals_ranks_and_finds_black_hole_queries() -> None:
    keras = "https://porchlamp.org/projects/keras-team/keras/"
    answers = [
        {"rows": [
            {"keys": ["keras pull request response time", keras], "clicks": 2, "impressions": 40, "ctr": 0.05, "position": 7.5},
            {"keys": ["porchlamp", "https://porchlamp.org/"], "clicks": 5, "impressions": 30, "ctr": 0.16, "position": 1.0},
            {"keys": ["keras pull request response time", "https://porchlamp.org/"], "clicks": 0, "impressions": 15, "ctr": 0, "position": 20.0},
            {"keys": ["is vllm a black hole", "https://porchlamp.org/projects/vllm-project/vllm/"], "clicks": 0, "impressions": 3, "ctr": 0, "position": 30.0},
        ]},
        {"rows": [{"keys": ["Black-Holes in open source", "https://porchlamp.org/black-holes/"], "clicks": 1, "impressions": 9, "ctr": 0.1, "position": 4.0}]},
    ]
    rows = query_rows(answers)
    assert rows[0] == {"query": "keras pull request response time", "page": keras, "clicks": 2, "impressions": 40,
                       "ctr": 0.05, "position": 7.5}
    summary = queries_summary(rows)
    assert (summary["rows"], summary["clicks"], summary["impressions"]) == (5, 8, 97)
    assert summary["topQueries"][0] == {"query": "keras pull request response time", "impressions": 55, "clicks": 2}
    assert summary["topPages"][0] == {"page": "https://porchlamp.org/", "impressions": 45}
    assert summary["projectPagesWithImpressions"] == 2
    assert summary["blackHoleQueries"] == ["Black-Holes in open source", "is vllm a black hole"]


def test_bing_stats_are_summed_per_query_and_dated_in_their_own_offset() -> None:
    assert bing_date("/Date(1316156400000-0700)/") == "2011-09-16"
    assert bing_date("/Date(1729123200000)/") == "2024-10-17"
    assert bing_date("2011-09-16") is None
    first, second = "/Date(1316156400000-0700)/", "/Date(1316242800000-0700)/"
    stats = {
        "GetRankAndTrafficStats": [{"Date": first, "Clicks": 1, "Impressions": 10}, {"Date": second, "Clicks": 2, "Impressions": 20}],
        "GetQueryStats": [{"Query": "porchlamp", "Impressions": 5, "Clicks": 1, "Date": first},
                          {"Query": "porchlamp", "Impressions": 7, "Clicks": 0, "Date": second},
                          {"Query": "keras", "Impressions": 9, "Clicks": 2, "Date": second}],
        "GetPageStats": [],
        "GetCrawlStats": [{"Date": second, "InIndex": 12, "CrawledPages": 30, "CrawlErrors": 0, "Code2xx": 30, "Code4xx": 0, "Code5xx": 0},
                          {"Date": first, "InIndex": 10, "CrawledPages": 5}],
    }
    summary = bing_summary(stats)
    assert summary["traffic"] == {"days": 2, "from": "2011-09-16", "to": "2011-09-17", "clicks": 3, "impressions": 30}
    assert summary["topQueries"] == [{"key": "porchlamp", "impressions": 12, "clicks": 1},
                                     {"key": "keras", "impressions": 9, "clicks": 2}]
    assert summary["topPages"] == []
    assert summary["crawl"] == {"date": "2011-09-17", "InIndex": 12, "CrawledPages": 30, "CrawlErrors": 0,
                                "Code2xx": 30, "Code4xx": 0, "Code5xx": 0}


def test_an_inspection_run_writes_its_dated_report_its_ledger_and_the_latest_readings(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_state: Path,
        capsys: pytest.CaptureFixture[str]) -> None:
    urls = page_urls() + project_urls(5)
    redact = Redactor()
    redact.add(BING_CANARY)
    earlier = [reading(urls[0], DAY_ARG, "PASS")]
    records = [reading(urls[1], DAY_ARG, "NEUTRAL"), failed_record(urls[2], DAY_ARG, f"URL Inspection: HTTP 500: {BING_CANARY}")]
    latest = cli.save_inspection(isolated_state, DAY_ARG, {"2026-10-16": 700, DAY_ARG: 10}, earlier, records, redact)
    assert json.loads((isolated_state / "inspect-ledger.json").read_text()) == {"2026-10-16": 700, DAY_ARG: 12}
    report_text = (isolated_state / f"inspect-{DAY_ARG}.json").read_text()
    report = json.loads(report_text)
    assert [record["url"] for record in report["records"]] == urls[:3]
    assert (report["summary"]["inspected"], report["summary"]["failed"]) == (2, 1)
    assert BING_CANARY not in report_text and "HTTP 500: [redacted]" in report_text
    assert set(latest) == {urls[1]} == set(json.loads((isolated_state / "inspect-latest.json").read_text()))
    # The next run under the same date reads them back: 12 calls used, two URLs done, the failed one planned again.
    plant_service_account(tmp_path, monkeypatch)
    site = make_site(tmp_path / "site", projects=5)
    assert cli.main(["gsc", "inspect", "--site", str(site), "--date", DAY_ARG, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert f"quota 12 of 2,000 calls already made under {DAY_ARG}; this run 6 of 6 wanted, 0 left out for quota" in out


def test_run_state_lives_under_the_users_state_directory() -> None:
    assert cli.state_dir({"HOME": "/home/owner"}) == Path("/home/owner/.local/state/porchlamp/searchops")
    assert cli.state_dir({"HOME": "/home/owner", "XDG_STATE_HOME": "/srv/state"}) == Path("/srv/state/porchlamp/searchops")
    assert cli.state_dir({"HOME": "/home/owner", "XDG_STATE_HOME": "relative/state"}) == Path(
        "/home/owner/.local/state/porchlamp/searchops")


# ---------------------------------------------------------------- secrets: the canary


def test_a_planted_key_never_reaches_stdout_stderr_or_logs(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
        caplog: pytest.LogCaptureFixture, block_live_network: None) -> None:
    caplog.set_level(logging.DEBUG)
    plant_bing_key(tmp_path, monkeypatch)
    plant_service_account(tmp_path, monkeypatch)
    site = make_site(tmp_path / "site")
    seen = []
    for argv in (["indexnow", "--site", str(site)], ["bing", "submit", "--site", str(site)],
                 ["bing", "stats", "--date", DAY_ARG], ["gsc", "sitemaps", "--submit"],
                 ["gsc", "inspect", "--site", str(site), "--date", DAY_ARG], ["gsc", "queries", "--date", DAY_ARG]):
        assert cli.main([*argv, "--dry-run"]) == 0
        captured = capsys.readouterr()
        seen += [captured.out, captured.err]
    printed = "".join(seen)
    assert printed.count("apikey=[redacted]") == 7  # the Bing URLs were printed: the check is not vacuous
    for canary in CANARIES:
        assert canary not in printed + caplog.text


def test_an_error_answer_never_echoes_the_key_from_its_url_or_body() -> None:
    redact = Redactor()
    redact.add(BING_CANARY)
    url = bing.bing_url("GetCrawlStats", BING_CANARY, siteUrl=SITE)
    response = httpx.Response(400, json={"ErrorCode": 3, "Message": f"InvalidApiKey {BING_CANARY}"},
                              request=httpx.Request("GET", url))
    with Http(redact) as http, pytest.raises(ApiError) as caught:
        http.check(response, "Bing GetCrawlStats")
    error = caught.value
    assert str(error) == "Bing GetCrawlStats: HTTP 400 Bad Request: 3: InvalidApiKey [redacted]"
    assert error.status == 400
    assert BING_CANARY not in str(error) + repr(error) + "".join(traceback.format_exception(error))


def test_a_request_that_gets_no_answer_raises_without_the_httpx_exception_attached() -> None:
    redact = Redactor()
    redact.add(BING_CANARY)
    with Http(redact) as http, pytest.raises(ApiError) as caught:
        # httpx refuses the scheme before any socket opens; its exception holds the request URL with the key
        http.send("GET", f"ftp://ssl.bing.com/webmaster/api.svc/json/GetCrawlStats?apikey={BING_CANARY}",
                  label="Bing GetCrawlStats")
    error = caught.value
    assert str(error).startswith("Bing GetCrawlStats: no answer (UnsupportedProtocol: ")
    assert error.status is None
    assert error.__context__ is None and error.__cause__ is None
    assert BING_CANARY not in "".join(traceback.format_exception(error))


def test_the_http_client_request_log_line_carries_no_key(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="httpx")
    redact = Redactor()
    redact.add(BING_CANARY)
    url = httpx.URL(bing.bing_url("GetCrawlStats", BING_CANARY, siteUrl=SITE))
    with Http(redact):
        # httpx's own request log call, as httpx/_client.py makes it after each answer
        logging.getLogger("httpx").info('HTTP Request: %s %s "%s %d %s"', "GET", url, "HTTP/1.1", 200, "OK")
        # a record whose arguments do not fit its format is kept, not raised into the caller
        logging.getLogger("httpx").info("HTTP Request: %d", str(url))
    assert "GetCrawlStats?siteUrl=https%3A%2F%2Fporchlamp.org%2F&apikey=[redacted]" in caplog.text
    assert "HTTP Request: %d ('https://ssl.bing.com/" in caplog.text
    assert BING_CANARY not in caplog.text


def test_a_real_search_console_run_that_cannot_sign_exits_in_one_sentence_without_the_key(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
        block_live_network: None) -> None:
    plant_service_account(tmp_path, monkeypatch)
    assert cli.main(["gsc", "sitemaps"]) == 2
    captured = capsys.readouterr()
    lines = captured.err.splitlines()
    assert len(lines) == 1 and "google-auth" in lines[0]  # not installed, or cannot load the planted key
    for canary in CANARIES:
        assert canary not in captured.out + captured.err


# ---------------------------------------------------------------- credentials: missing, pasted, exposed


@pytest.mark.parametrize(("argv", "variable", "what"), [
    (["gsc", "sitemaps", "--dry-run"], GSC_CREDENTIALS_ENV, "the Search Console service-account JSON file"),
    (["gsc", "sitemaps", "--submit"], GSC_CREDENTIALS_ENV, "the Search Console service-account JSON file"),
    (["gsc", "inspect", "--site", "no-such-site", "--date", DAY_ARG], GSC_CREDENTIALS_ENV,
     "the Search Console service-account JSON file"),
    (["gsc", "queries", "--date", DAY_ARG], GSC_CREDENTIALS_ENV, "the Search Console service-account JSON file"),
    (["bing", "submit", "--site", "no-such-site"], BING_KEY_FILE_ENV, "the file holding the Bing Webmaster API key"),
    (["bing", "stats", "--date", DAY_ARG, "--dry-run"], BING_KEY_FILE_ENV, "the file holding the Bing Webmaster API key"),
], ids=["gsc-sitemaps-dry-run", "gsc-sitemaps-submit", "gsc-inspect", "gsc-queries", "bing-submit", "bing-stats-dry-run"])
def test_a_missing_credential_exits_with_one_sentence_naming_its_variable(
        argv: list[str], variable: str, what: str, capsys: pytest.CaptureFixture[str], block_live_network: None) -> None:
    assert cli.main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{variable} is not set: set it to the path of {what}.\n"


def test_a_key_pasted_in_place_of_a_path_is_refused_without_echoing_it(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv(BING_KEY_FILE_ENV, BING_CANARY)
    assert cli.main(["bing", "stats", "--date", DAY_ARG, "--dry-run"]) == 2
    captured = capsys.readouterr()
    assert captured.err == (f"{BING_KEY_FILE_ENV} does not name a file: set it to the path of the file holding the "
                            "Bing Webmaster API key, not to the key itself.\n")
    assert BING_CANARY not in captured.out + captured.err


def test_a_credential_file_other_users_can_open_is_refused(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    plant_bing_key(tmp_path, monkeypatch, mode=0o644)
    assert cli.main(["bing", "stats", "--date", DAY_ARG, "--dry-run"]) == 2
    captured = capsys.readouterr()
    assert captured.err == f"{BING_KEY_FILE_ENV} names a file other users can open: run chmod 600 on that file.\n"
    assert BING_CANARY not in captured.out + captured.err


@pytest.mark.parametrize("text", ["", "\n", f"{BING_CANARY}\n{BING_CANARY}\n", f"{BING_CANARY} trailing-word\n"],
                         ids=["empty", "blank-line", "two-lines", "two-words"])
def test_a_bing_key_file_must_hold_one_key_on_one_line(
        text: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    plant_bing_key(tmp_path, monkeypatch, text=text)
    assert cli.main(["bing", "stats", "--date", DAY_ARG, "--dry-run"]) == 2
    captured = capsys.readouterr()
    assert captured.err == f"The file named by {BING_KEY_FILE_ENV} does not hold one API key on one line.\n"
    assert BING_CANARY not in captured.out + captured.err


@pytest.mark.parametrize(("text", "sentence"), [
    ('{"type": "service_account", ', "is not a JSON object."),
    (json.dumps([service_account()]), "is not a JSON object."),
    (json.dumps(service_account(type="authorized_user")), "is not a service-account key (its type is not service_account)."),
    (json.dumps(service_account(private_key="")), "lacks private_key."),
    (json.dumps(service_account(token_uri="https://token.example/oauth")),
     "sends its token request somewhere other than https://oauth2.googleapis.com/."),
], ids=["not-json", "a-list", "wrong-type", "no-private-key", "foreign-token-uri"])
def test_a_service_account_file_of_the_wrong_shape_is_refused_in_one_sentence(
        text: str, sentence: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    plant_service_account(tmp_path, monkeypatch, text=text)
    assert cli.main(["gsc", "sitemaps", "--dry-run"]) == 2
    captured = capsys.readouterr()
    assert captured.err == f"The file named by {GSC_CREDENTIALS_ENV} {sentence}\n"
    for canary in CANARIES:
        assert canary not in captured.out + captured.err


# ---------------------------------------------------------------- the command line


@pytest.mark.parametrize("argv", [[], ["indexnow"], ["gsc"], ["gsc", "sitemaps"], ["gsc", "inspect"],
                                  ["gsc", "queries"], ["bing"], ["bing", "submit"], ["bing", "stats"]])
def test_every_command_prints_its_help(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stop:
        cli.main([*argv, "--help"])
    assert stop.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith(" ".join(["usage: python -m porchlamp.searchops", *argv]))
    if argv in (["indexnow"], ["gsc", "sitemaps"], ["gsc", "inspect"], ["gsc", "queries"], ["bing", "submit"], ["bing", "stats"]):
        assert "--dry-run" in out


@pytest.mark.parametrize("argv", [[], ["gsc"], ["bing"]])
def test_a_command_group_without_a_command_prints_its_usage_and_exits_2(
        argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(argv) == 2
    assert capsys.readouterr().err.startswith(" ".join(["usage: python -m porchlamp.searchops", *argv]))
