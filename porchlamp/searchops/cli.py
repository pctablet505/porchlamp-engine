"""``python -m porchlamp.searchops``: search operations after a publish, by API.

Commands::

    indexnow --site DIR                 every sitemap URL to IndexNow, in POSTs of at most 10,000
    gsc sitemaps [--submit]             the property's sitemaps; --submit sends sitemap.xml first
    gsc inspect --site DIR --date DAY   the day's URL Inspection pass, within the daily quota
    gsc queries --date DAY [--days N]   Search Analytics for the N days before DAY
    bing submit --site DIR              SubmitFeed, the URL submission quota, then the priority URLs
    bing stats --date DAY               Bing traffic, query, page and crawl stats

``--dry-run`` on any command checks its inputs (the site tree, the credential file), prints the
plan, opens no connection and writes nothing. The CLI owns every print and the run state, kept in
``$XDG_STATE_HOME/porchlamp/searchops/`` (by default ``~/.local/state/porchlamp/searchops/``),
never in the repository or a data directory. The package reads no clock: a date comes from
``--date`` (pass ``$(date +%F)``). Credentials: :mod:`porchlamp.searchops.credentials`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

from porchlamp.searchops import bing, gsc
from porchlamp.searchops.credentials import (
    BING_KEY_FILE_ENV,
    GSC_CREDENTIALS_ENV,
    Redactor,
    SearchOpsError,
    read_bing_key,
    read_service_account,
)
from porchlamp.searchops.pages import SITE, SITEMAP_URL, SitemapUrls, indexnow_key, read_sitemaps
from porchlamp.searchops.plan import (
    BING_BATCH,
    INDEXNOW_BATCH,
    INSPECTION_SLICE,
    PRIORITY_PROJECTS,
    InspectionPlan,
    batched,
    inspection_plan,
    is_project,
    priority_list,
)
from porchlamp.searchops.report import (
    bing_summary,
    failed_record,
    inspection_record,
    inspection_summary,
    merge_latest,
    queries_summary,
    query_rows,
    sitemap_rows,
)

PAUSE_SECONDS = 0.1  # between URL Inspection calls: at most 600 a minute, Google's per-minute limit
STOP_STATUSES = (401, 403, 429)  # an inspection run stops at these (and when no answer comes)
QUERY_PAGES = 8  # Search Analytics pages of 25,000 rows read per run, at most
LEDGER = "inspect-ledger.json"
LATEST = "inspect-latest.json"


class Output:
    """Every line the CLI prints passes through the run's redactor."""

    def __init__(self, redact: Redactor) -> None:
        self.redact = redact

    def line(self, text: str = "") -> None:
        print(self.redact(text))

    def error(self, text: str) -> None:
        print(self.redact(text), file=sys.stderr)


def state_dir(environ: Mapping[str, str]) -> Path:
    """``$XDG_STATE_HOME/porchlamp/searchops``, with ``~/.local/state`` when that is unset or relative."""
    base = environ.get("XDG_STATE_HOME", "")
    if base and Path(base).is_absolute():
        return Path(base) / "porchlamp" / "searchops"
    return Path(environ.get("HOME") or Path.home()) / ".local" / "state" / "porchlamp" / "searchops"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except ValueError:
        failure = f"{path} is not valid JSON; move it aside and run again."
    raise SearchOpsError(failure)


def _write_json(path: Path, data: Any, redact: Redactor) -> None:
    """Write ``data`` to ``path`` atomically, through the redactor like every other output."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(redact(json.dumps(data, indent=2, sort_keys=True)) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _print_sitemaps(out: Output, site: Path, maps: SitemapUrls) -> None:
    projects = sum(is_project(url) for url in maps.urls)
    out.line(f"site {site}")
    out.line(f"sitemaps {' '.join(maps.files)}")
    out.line(f"urls {len(maps.urls):,} on {maps.host} ({len(maps.urls) - projects:,} pages, {projects:,} projects)")


def _print_account(out: Output, info: Mapping[str, Any], write: bool) -> None:
    out.line(f"account {info['client_email']} on {gsc.PROPERTY}, scope {gsc.scopes(write)[0]}")


def _dry_run(out: Output) -> int:
    out.line("dry run: nothing sent, nothing written")
    return 0


def _console(info: Mapping[str, Any], write: bool, http: Any) -> gsc.SearchConsole:
    """A Search Console client, after sites.list shows the account may run the command."""
    console = gsc.SearchConsole(info, write, http)
    problem = gsc.access_problem(console.get(gsc.sites_url(), "Search Console sites.list") or {}, write)
    if problem is not None:
        raise SearchOpsError(problem)
    return console


def _indexnow(args: argparse.Namespace, out: Output) -> int:
    """Send every sitemap URL to IndexNow and report each POST's HTTP status."""
    site = Path(args.site).expanduser()
    key = indexnow_key(site)
    maps = read_sitemaps(site)
    batches = batched(maps.urls, INDEXNOW_BATCH)
    _print_sitemaps(out, site, maps)
    out.line(f"key file https://{maps.host}/{key}.txt (holds its own name)")
    out.line(f"batches {len(batches)} of at most {INDEXNOW_BATCH:,} URLs: {', '.join(f'{len(batch):,}' for batch in batches)}")
    out.line(f"send POST {bing.INDEXNOW_ENDPOINT} once per batch")
    if args.dry_run:
        return _dry_run(out)
    from porchlamp.searchops.transport import Http, service_message

    failed = 0
    with Http(out.redact) as http:
        for number, batch in enumerate(batches, start=1):
            body = bing.indexnow_body(maps.host, key, batch)
            response = http.send("POST", bing.INDEXNOW_ENDPOINT, label="IndexNow", body=body)
            status = response.status_code
            line = f"batch {number}/{len(batches)}: {len(batch):,} URLs -> HTTP {status} ({bing.indexnow_answer(status)})"
            if status not in bing.INDEXNOW_SUCCESS:
                failed += 1
                detail = service_message(response)
                line = f"{line}: {detail}" if detail else line
            out.line(line)
            if status == 429:
                break
    return 1 if failed else 0


def _gsc_sitemaps(args: argparse.Namespace, out: Output) -> int:
    """List the property's sitemaps; with ``--submit``, submit the sitemap index first."""
    info = read_service_account(os.environ, out.redact)
    _print_account(out, info, args.submit)
    out.line(f"send GET {gsc.sites_url()} (the account's permission on {gsc.PROPERTY})")
    if args.submit:
        out.line(f"send PUT {gsc.sitemap_url(SITEMAP_URL)} (submit {SITEMAP_URL})")
    out.line(f"send GET {gsc.sitemaps_url()} (the property's sitemaps)")
    if args.dry_run:
        return _dry_run(out)
    from porchlamp.searchops.transport import Http

    with Http(out.redact) as http:
        console = _console(info, args.submit, http)
        if args.submit:
            console.put(gsc.sitemap_url(SITEMAP_URL), "Search Console sitemaps.submit")
            out.line(f"submitted {SITEMAP_URL}")
        rows = sitemap_rows(console.get(gsc.sitemaps_url(), "Search Console sitemaps.list") or {})
    for row in rows:
        out.line(
            f"{row['path']}: {row['submitted']:,} URLs submitted, {row['errors']} errors, {row['warnings']} warnings, "
            f"pending {row['isPending']}, last submitted {row['lastSubmitted']}, last downloaded {row['lastDownloaded']}"
        )
    return 0


def _inspect_all(info: Mapping[str, Any], plan: InspectionPlan, records: list[dict[str, Any]], redact: Redactor) -> Optional[str]:
    """Inspect each planned URL, appending one record per call to ``records``; why it stopped early, or None."""
    from porchlamp.searchops.transport import ApiError, Http

    day = plan.day.isoformat()
    with Http(redact) as http:
        console = _console(info, False, http)
        for url in plan.urls:
            try:
                answer = console.post(gsc.INSPECTION_API, gsc.inspection_body(url), "URL Inspection")
            except ApiError as error:
                records.append(failed_record(url, day, str(error)))
                if error.status is None or error.status in STOP_STATUSES:
                    return str(error)
                continue
            records.append(inspection_record(url, day, answer or {}))
            time.sleep(PAUSE_SECONDS)
    return None


def save_inspection(
    state: Path,
    day: str,
    ledger: Mapping[str, int],
    earlier: Sequence[Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
    redact: Redactor,
) -> dict[str, Any]:
    """Write a run's results: the ledger counts its calls under ``day``, ``inspect-<day>.json``
    holds every record made under ``day``, and the latest reading per URL takes the new readings.
    Returns the latest readings."""
    day_records = [*earlier, *records]
    latest = merge_latest(_read_json(state / LATEST, {}), records)
    _write_json(state / LEDGER, {**ledger, day: int(ledger.get(day, 0)) + len(records)}, redact)
    _write_json(state / f"inspect-{day}.json", {"date": day, "property": gsc.PROPERTY,
                                                 "summary": inspection_summary(day_records),
                                                 "records": day_records}, redact)
    _write_json(state / LATEST, latest, redact)
    return latest


def _summary_line(name: str, summary: Mapping[str, Any]) -> str:
    line = (f"{name}: {summary['inspected']:,} inspected, {summary['indexed']:,} indexed (verdict PASS), "
            f"{summary['unknownToGoogle']:,} unknown to Google, {summary['canonicalMismatch']:,} canonical mismatches, "
            f"{summary['failed']:,} failed")
    if "neverInspected" in summary:
        line += f"; {summary['neverInspected']:,} of {summary['sitemapUrls']:,} sitemap URLs never inspected"
    return line


def _gsc_inspect(args: argparse.Namespace, out: Output) -> int:
    """The day's URL Inspection pass: watch list plus rotation slice, within the daily quota."""
    info = read_service_account(os.environ, out.redact)
    site = Path(args.site).expanduser()
    maps = read_sitemaps(site)
    state = state_dir(os.environ)
    day = args.date.isoformat()
    day_path = state / f"inspect-{day}.json"
    ledger = _read_json(state / LEDGER, {})
    earlier = _read_json(day_path, {}).get("records", [])
    done = {record["url"] for record in earlier if "error" not in record}  # a failed URL is tried again
    plan = inspection_plan(maps.urls, args.date, used=int(ledger.get(day, 0)), done=done, slice_size=args.slice)
    _print_sitemaps(out, site, maps)
    _print_account(out, info, False)
    out.line(f"date {day}: slice {plan.slice_index + 1} of {plan.slice_count} (projects {plan.slice_first:,} to "
             f"{plan.slice_last:,} in sitemap order) and the watch list of {plan.watch}")
    out.line(f"quota {plan.used:,} of {plan.limit:,} calls already made under {day}; this run {len(plan.urls):,} of "
             f"{plan.wanted:,} wanted, {plan.cut:,} left out for quota")
    out.line(f"send POST {gsc.INSPECTION_API} once per URL, one call per {PAUSE_SECONDS} s at most")
    out.line(f"state {state}")
    if args.dry_run:
        return _dry_run(out)
    records: list[dict[str, Any]] = []
    stopped: Optional[str] = None
    try:
        stopped = _inspect_all(info, plan, records, out.redact)
    except KeyboardInterrupt:
        stopped = "interrupted"
    finally:  # the calls made count against the quota even when the run fails
        latest = save_inspection(state, day, ledger, earlier, records, out.redact)
    out.line(f"report {day_path}")
    out.line(_summary_line(f"this run ({len(records):,} calls)", inspection_summary(records)))
    out.line(_summary_line("latest reading per URL", inspection_summary(latest.values(), maps.urls)))
    if stopped is not None:
        out.error(f"stopped early: {stopped}")
        return 1
    return 0


def _gsc_queries(args: argparse.Namespace, out: Output) -> int:
    """The Search Analytics report (web, by query and page) for the ``--days`` days before ``--date``."""
    info = read_service_account(os.environ, out.redact)
    state = state_dir(os.environ)
    start, end = args.date - timedelta(days=args.days), args.date - timedelta(days=1)
    _print_account(out, info, False)
    out.line(f"range {start} to {end}: web search, by query and page")
    out.line(f"send POST {gsc.search_analytics_url()} (pages of {gsc.ROW_LIMIT:,} rows, {QUERY_PAGES} at most)")
    out.line(f"state {state}")
    if args.dry_run:
        return _dry_run(out)
    from porchlamp.searchops.transport import Http

    answers = []
    with Http(out.redact) as http:
        console = _console(info, False, http)
        for page in range(QUERY_PAGES):
            body = gsc.query_body(start, end, start_row=page * gsc.ROW_LIMIT)
            answer = console.post(gsc.search_analytics_url(), body, "Search Analytics query") or {}
            answers.append(answer)
            if len(answer.get("rows") or []) < gsc.ROW_LIMIT:
                break
    rows = query_rows(answers)
    summary = queries_summary(rows)
    path = state / f"queries-{args.date.isoformat()}.json"
    _write_json(path, {"date": args.date.isoformat(), "startDate": start.isoformat(), "endDate": end.isoformat(),
                       "property": gsc.PROPERTY, "summary": summary, "rows": rows}, out.redact)
    out.line(f"report {path}")
    out.line(f"rows {summary['rows']:,}, clicks {summary['clicks']:,}, impressions {summary['impressions']:,}, "
             f"project pages with impressions {summary['projectPagesWithImpressions']:,}")
    for row in summary["topQueries"][:10]:
        out.line(f"query {row['query']!r}: {row['impressions']:,} impressions, {row['clicks']:,} clicks")
    out.line(f"queries naming black holes: {len(summary['blackHoleQueries'])}")
    return 0


def _bing_submit(args: argparse.Namespace, out: Output) -> int:
    """Bing SubmitFeed for the sitemap index, the URL submission quota, then the priority URLs."""
    key = read_bing_key(os.environ, out.redact)
    site = Path(args.site).expanduser()
    maps = read_sitemaps(site)
    urls = priority_list(maps.urls, args.top)
    _print_sitemaps(out, site, maps)
    out.line(f"priority {len(urls):,} URLs: the non-project pages and the first {args.top} projects")
    out.line(f"send POST {bing.bing_url('SubmitFeed', key)} (feed {SITEMAP_URL})")
    out.line(f"send GET {bing.bing_url('GetUrlSubmissionQuota', key, siteUrl=SITE)}")
    out.line(f"send POST {bing.bing_url('SubmitUrlBatch', key)} with the priority URLs cut to the quota left, "
             f"{BING_BATCH} per call at most")
    if args.dry_run:
        return _dry_run(out)
    from porchlamp.searchops.transport import Http

    with Http(out.redact) as http:
        client = bing.BingWebmaster(key, http)
        client.call("SubmitFeed", body={"siteUrl": SITE, "feedUrl": SITEMAP_URL})
        out.line(f"SubmitFeed {SITEMAP_URL}: accepted")
        quota = client.call("GetUrlSubmissionQuota", siteUrl=SITE) or {}
        out.line(f"quota {int(quota.get('DailyQuota') or 0):,} URLs left today, "
                 f"{int(quota.get('MonthlyQuota') or 0):,} this month")
        sent = urls[:bing.submission_room(quota)]
        for batch in batched(sent, BING_BATCH):
            client.call("SubmitUrlBatch", body={"siteUrl": SITE, "urlList": batch})
    out.line(f"SubmitUrlBatch {len(sent):,} of {len(urls):,} priority URLs; {len(urls) - len(sent):,} left out for quota")
    return 0


def _bing_stats(args: argparse.Namespace, out: Output) -> int:
    """Bing traffic, query, page and crawl stats, written as a dated report."""
    key = read_bing_key(os.environ, out.redact)
    state = state_dir(os.environ)
    for method in bing.STATS_METHODS:
        out.line(f"send GET {bing.bing_url(method, key, siteUrl=SITE)}")
    out.line(f"state {state}")
    if args.dry_run:
        return _dry_run(out)
    from porchlamp.searchops.transport import Http

    with Http(out.redact) as http:
        client = bing.BingWebmaster(key, http)
        stats = {method: client.call(method, siteUrl=SITE) or [] for method in bing.STATS_METHODS}
    summary = bing_summary(stats)
    path = state / f"bing-stats-{args.date.isoformat()}.json"
    _write_json(path, {"date": args.date.isoformat(), "site": SITE, "summary": summary, "stats": stats}, out.redact)
    traffic = summary["traffic"]
    out.line(f"report {path}")
    out.line(f"traffic {traffic['from']} to {traffic['to']}: {traffic['clicks']:,} clicks, {traffic['impressions']:,} impressions")
    for row in summary["topQueries"]:
        out.line(f"query {row['key']!r}: {row['impressions']:,} impressions, {row['clicks']:,} clicks")
    crawl = summary["crawl"]
    if crawl is None:
        out.line("crawl: no rows yet")
    else:
        out.line(f"crawl {crawl['date']}: {crawl['InIndex']} in the index, {crawl['CrawledPages']} pages crawled, "
                 f"{crawl['CrawlErrors']} crawl errors")
    return 0


def _day(text: str) -> date:
    """An argparse type: a calendar date, YYYY-MM-DD."""
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a date (YYYY-MM-DD): {text!r}") from None


def _count(least: int) -> Callable[[str], int]:
    """An argparse type: an integer of at least ``least``."""

    def parse(text: str) -> int:
        value = int(text)
        if value < least:
            raise argparse.ArgumentTypeError(f"must be at least {least}, got {value}")
        return value

    return parse


_EPILOG = f"""\
credentials (files only the tool reads; each variable holds a PATH, never a key):
  {GSC_CREDENTIALS_ENV}  the Search Console service-account JSON file (chmod 600)
  {BING_KEY_FILE_ENV}    a file holding the Bing Webmaster API key on one line (chmod 600)
state: $XDG_STATE_HOME/porchlamp/searchops/ (default ~/.local/state/porchlamp/searchops/)
"""


def _parser() -> argparse.ArgumentParser:
    """The ``indexnow`` / ``gsc`` / ``bing`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m porchlamp.searchops",
        description="Search operations after a publish, by API: IndexNow, Google Search Console and Bing "
                    "Webmaster. --dry-run checks the inputs, prints the plan, sends nothing and writes nothing.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.set_defaults(handler=None, parser=parser)
    commands = parser.add_subparsers(dest="command")
    site_help = "the published site tree (the pages repository after an export); its sitemaps give the URLs"
    date_help = "the run's date, YYYY-MM-DD (pass $(date +%%F)); the tool reads no clock"

    indexnow = commands.add_parser("indexnow", help="send every sitemap URL of the published site to IndexNow")
    indexnow.add_argument("--site", required=True, help=site_help)
    indexnow.set_defaults(handler=_indexnow)

    google = commands.add_parser("gsc", help="Google Search Console: sitemaps, URL Inspection, Search Analytics",
                                 epilog=_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    google.set_defaults(parser=google)
    google_commands = google.add_subparsers(dest="gsc_command")
    sitemaps = google_commands.add_parser("sitemaps", help="list the property's sitemaps (submit first with --submit)")
    sitemaps.add_argument("--submit", action="store_true",
                          help=f"submit {SITEMAP_URL} first (the only command that asks for the read-write scope)")
    sitemaps.set_defaults(handler=_gsc_sitemaps)
    inspect = google_commands.add_parser(
        "inspect", help="the day's URL Inspection pass: the watch list and the day's slice, within the daily quota")
    inspect.add_argument("--site", required=True, help=site_help)
    inspect.add_argument("--date", type=_day, required=True, help=f"{date_help}: picks the slice and the quota day")
    inspect.add_argument("--slice", type=_count(1), default=INSPECTION_SLICE,
                         help=f"project pages per day (default {INSPECTION_SLICE}; the quota caps a run at 2,000 calls)")
    inspect.set_defaults(handler=_gsc_inspect)
    queries = google_commands.add_parser("queries", help="the Search Analytics report for the days before --date")
    queries.add_argument("--date", type=_day, required=True, help=date_help)
    queries.add_argument("--days", type=_count(1), default=3, help="how many days before --date (default 3)")
    queries.set_defaults(handler=_gsc_queries)

    microsoft = commands.add_parser("bing", help="Bing Webmaster: submit the sitemap and priority URLs, read stats",
                                    epilog=_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    microsoft.set_defaults(parser=microsoft)
    bing_commands = microsoft.add_subparsers(dest="bing_command")
    submit = bing_commands.add_parser(
        "submit", help="SubmitFeed for the sitemap index, then the priority URLs within the submission quota")
    submit.add_argument("--site", required=True, help=site_help)
    submit.add_argument("--top", type=_count(0), default=PRIORITY_PROJECTS,
                        help=f"projects (in rank order) after the non-project pages (default {PRIORITY_PROJECTS})")
    submit.set_defaults(handler=_bing_submit)
    stats = bing_commands.add_parser("stats", help="traffic, query, page and crawl stats, as a dated report")
    stats.add_argument("--date", type=_day, required=True, help=f"{date_help}: names the report")
    stats.set_defaults(handler=_bing_stats)

    for command in (indexnow, sitemaps, inspect, queries, submit, stats):
        command.add_argument("--dry-run", action="store_true",
                             help="check the inputs and print the plan; send nothing, write nothing")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run ``python -m porchlamp.searchops``; returns the process exit code."""
    args = _parser().parse_args(argv)
    if args.handler is None:
        args.parser.print_usage(sys.stderr)
        return 2
    out = Output(Redactor())
    try:
        return args.handler(args, out)
    except SearchOpsError as error:
        out.error(str(error))
        return error.exit_code
    except KeyboardInterrupt:
        out.error("interrupted.")
        return 130
    except Exception as error:  # a defect: show it, redacted, never raw
        out.error("".join(traceback.format_exception(error)).rstrip())
        return 1
