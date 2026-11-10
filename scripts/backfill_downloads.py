"""Throttled pypistats backfill runner for null registry-download envelopes.

Downloads coverage (`02` section 4 2.3 + D-MISS-1):
monthly_downloads was 77.8% missing because pypistats rate-limits to about
5 requests/min/IP and its 429 arrives as HTTP 200 + HTML body, which the
gather path records as a null monthly. A null is NOT done: this runner
refetches exactly the null pypi download envelopes, throttled to at most
4 requests/min (15 s minimum interval), resumable from the envelopes on
disk (no separate state), printing an ETA derived from the measured
per-request time.

Ecosystem reachability:
  pypi   -- pypistats.org (this runner, throttled)
  go     -- dependents via deps.dev only; downloads unmeasured
  maven  -- unmeasured (no registry source)
  crates -- 90-day/3 normalisation kept (crates.io recent_downloads // 3)
  npm    -- as is (npmjs.org point/last-month)
  BigQuery public dataset -- probed without credentials; none on this
             machine => UNDECIDED / U-ENV.

Usage (point --data-dir at a scratch dir; do not run against the main
checkout's data/ while a sweep is writing it):
  .venv/bin/python scripts/backfill_downloads.py --data-dir DIR --probe-bigquery
  .venv/bin/python scripts/backfill_downloads.py --data-dir DIR --limit 5
  .venv/bin/python scripts/backfill_downloads.py --data-dir DIR \
      --base-url http://127.0.0.1:PORT   # fake pypistats (tests)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from porchlamp.collect.envelope import write_envelope

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPISTATS_BASE = "https://pypistats.org"
DEFAULT_MIN_INTERVAL = 15.0  # 4 requests/min; pypistats limit is ~5/min/IP
USER_AGENT = "porchlamp-research/0.1 (ranking study)"
PUBLIC_DATASET_PROJECT = "bigquery-public-data"
BIGQUERY_SOURCE = "bigquery-public-data:pypi.file_downloads"

#: pypistats.org's own definition of a download, from its ETL
#: (psf/pypistats.org ``pypistats/tasks/pypi.py``, read 2026-10-06): installers named here
#: are mirrors and are NOT counted by ``/api/packages/<pkg>/recent`` (it sums the
#: ``without_mirrors`` category), and only downloads with a plausible python version
#: (``^[0-9]\.[0-9]+.*$`` or none) are counted. ``last_month`` is the 30 UTC days ending
#: yesterday. The BigQuery query below uses the same rules so that a value it fills in
#: means the same thing as a value pypistats gave; a count that includes mirrors is a
#: different number, and mixing the two in one snapshot is refused.
PYPISTATS_MIRRORS: tuple[str, ...] = ("bandersnatch", "z3c.pypimirror", "Artifactory", "devpi")
PYPISTATS_PYTHON_REGEX = r"^[0-9]\.[0-9]+.{0,}$"
#: ``file.project`` is the project name as uploaded; pypistats looks packages up by their
#: PEP 503 normalized form, so the query normalizes before matching.
_NORMALIZED_PROJECT = "LOWER(REGEXP_REPLACE(file.project, r'[-_.]+', '-'))"

#: Parameterized BigQuery SQL for trailing 30-day PyPI downloads across an
#: arbitrary package list in a single query, with pypistats' definition (mirrors excluded).
#: Never interpolates package names; the mirror list is the constant above.
BIGQUERY_PYPI_MONTHLY_SQL = f"""
SELECT {_NORMALIZED_PROJECT} AS package, COUNT(*) AS monthly_downloads
FROM `bigquery-public-data.pypi.file_downloads`
WHERE timestamp >= TIMESTAMP(@window_start)
  AND timestamp < TIMESTAMP(@window_end)
  AND {_NORMALIZED_PROJECT} IN UNNEST(@packages)
  AND (REGEXP_CONTAINS(details.python, r'{PYPISTATS_PYTHON_REGEX}') OR details.python IS NULL)
  AND details.installer.name NOT IN ({", ".join(repr(m) for m in PYPISTATS_MIRRORS)})
GROUP BY package
""".strip()

#: Default ceiling on bytes BigQuery may bill for one bulk run (100 GB; override with
#: ``--bigquery-max-gb``). The query scans ``timestamp`` partitions only, but the table is
#: large: the dry run reports the real figure before anything is billed.
DEFAULT_BIGQUERY_MAX_BYTES: int = 100 * 10**9

#: Ecosystems whose null downloads are retry-able (a registry source exists);
#: the rest have none (go: deps.dev dependents only; maven/systems: none).
RETRYABLE_ECOSYSTEMS = frozenset({"pypi", "npm", "crates"})
UNMEASURED_ECOSYSTEMS = frozenset({"go", "maven", "systems"})


def downloads_status(ecosystem: str, measured: bool) -> str:
    """Status of the 2.3 downloads parameter for one repo.

    ``measured``: a non-null monthly value is on disk. ``pending``: null or
    absent but the ecosystem has a registry source, so a rerun (this runner
    for pypi, gather.py for npm/crates) can complete it. ``unmeasured``: no
    registry source (D-MISS-1: unmeasured, not a zero).
    """
    if measured:
        return "measured"
    return "pending" if ecosystem in RETRYABLE_ECOSYSTEMS else "unmeasured"


def pypi_packages(data_dir: Path) -> list[str]:
    """Sorted pypi package names that have a download envelope under
    ``raw/downloads/``; the envelope is the scope unit (gather.py writes one
    per repo, so this is the backfill universe for the dir)."""
    dl = data_dir / "raw" / "downloads"
    if not dl.is_dir():
        return []
    return sorted(p.stem.removeprefix("pypi__") for p in dl.glob("pypi__*.json"))


def envelope_monthly(data_dir: Path, name: str) -> Optional[int]:
    """monthly value from the pypi download envelope; None = not done.

    Missing, corrupt or null monthly all read as not done: a null is not
    complete (it is refetched) and a corrupt envelope is never trusted.
    """
    p = data_dir / "raw" / "downloads" / f"pypi__{name}.json"
    try:
        env = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    monthly = (env.get("data") or {}).get("monthly")
    if isinstance(monthly, bool) or not isinstance(monthly, int):
        return None
    return monthly


def fetch_last_month(base_url: str, name: str, timeout: float = 30.0) -> Optional[int]:
    """One pypistats ``/api/packages/<name>/recent`` fetch; None on any failure.

    Live shape (verified 2026-09-12): ``{"data": {"last_day": ...,
    "last_month": ..., "last_week": ...}, "package": ..., "type":
    "recent_downloads"}``. Failures (404, HTTP 200 + HTML 429 body,
    malformed JSON, transport error) yield None, never a guessed number: the
    envelope keeps its null so the rerun refetches it (R-4: no fabricated
    downloads).
    """
    url = f"{base_url.rstrip('/')}/api/packages/{name}/recent"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # review-ok: gate-network — transport/4xx/HTML-429 -> None
        return None
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    monthly = data.get("last_month")
    if isinstance(monthly, bool) or not isinstance(monthly, int):
        return None
    return monthly


def _stored_data(data_dir: Path, name: str) -> dict[str, Any]:
    """The ``data`` of the stored pypi download envelope, ``{}`` when unreadable.

    ``scripts/gather.py`` records beside ``monthly`` the ``package`` it counted
    (not always the repo name) and the ``repo`` that wrote the file; a backfill
    must fetch that package and keep both fields, or it would undo the
    attribution ``scripts/derive.py`` checks.
    """
    path = data_dir / "raw" / "downloads" / f"pypi__{name}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")).get("data")
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_envelope(data_dir: Path, name: str, monthly: int, source: str,
                    keep: Optional[dict[str, Any]] = None) -> None:
    """Atomically replace the pypi download envelope with a measured value.

    ``keep``: the stored fields to carry over (``package``, ``repo``); the
    pypistats URL then names the counted package.
    """
    path = data_dir / "raw" / "downloads" / f"pypi__{name}.json"
    tmp = path.with_name(path.name + ".backfill-tmp")
    keep = {k: v for k, v in (keep or {}).items() if k in ("package", "repo")}
    counted = keep.get("package") or name
    envelope_source = (
        f"{source.rstrip('/')}/api/packages/{counted}/recent"
        if source.startswith(("http://", "https://"))
        else source
    )
    write_envelope(
        tmp,
        source=envelope_source,
        data={"monthly": monthly, **keep},
        fetched_at=datetime.now(timezone.utc).isoformat(),
        http_status=200,
    )
    os.replace(tmp, path)


@dataclass(frozen=True)
class BackfillReport:
    """Outcome of one backfill session."""

    total: int              # pypi envelopes in scope
    already_done: int       # non-null before the session
    fetched: int            # values written this session
    still_null: int         # null after the session (refetched on next run)
    elapsed_seconds: float
    rate_per_minute: float  # fetched / elapsed; 0.0 when nothing fetched


def backfill(data_dir: Path, *, base_url: str = PYPISTATS_BASE,
             min_interval: float = DEFAULT_MIN_INTERVAL,
             limit: Optional[int] = None) -> BackfillReport:
    """Refetch the null pypi download envelopes, throttled and resumable.

    The envelopes under ``raw/downloads/`` are the only state: a kill
    mid-run leaves the in-flight package null and the next session picks up
    exactly there. Packages are visited in sorted order. ``limit`` caps the
    number of refetches this session. Prints progress with an ETA that is a
    projection of the measured per-request time (at least the throttle
    interval).
    """
    names = pypi_packages(data_dir)
    pending = [n for n in names if envelope_monthly(data_dir, n) is None]
    total = len(names)
    done = total - len(pending)
    if limit is not None:
        pending = pending[:limit]
    if not pending:
        print(f"backfill: 0 pending pypi downloads ({total} done) - nothing to do")
        return BackfillReport(total, total, 0, 0, 0.0, 0.0)
    plan = f"@ {60.0 / min_interval:.1f}/min planned" if min_interval > 0 else ""
    print(f"backfill: {len(pending)} of {total} pypi downloads pending "
          f"(eta {len(pending) * min_interval / 60.0:.1f} min {plan})")
    fetched = nulls = 0
    t0 = time.monotonic()
    last_request_at = 0.0  # monotonic time of the last request (0 -> first is immediate)
    for i, name in enumerate(pending, 1):
        wait = min_interval - (time.monotonic() - last_request_at)
        if wait > 0:
            time.sleep(wait)
        last_request_at = time.monotonic()
        stored = _stored_data(data_dir, name)
        monthly = fetch_last_month(base_url, stored.get("package") or name)
        if monthly is not None:
            _write_envelope(data_dir, name, monthly, base_url, stored)
            fetched += 1
        else:
            nulls += 1
        remaining = len(pending) - i
        elapsed = time.monotonic() - t0
        attempts = i
        per_request = elapsed / attempts
        eta = remaining * max(per_request, min_interval)
        rate = fetched / elapsed * 60.0 if fetched and elapsed > 0 else 0.0
        mark = str(monthly) if monthly is not None else "null (not done)"
        print(f"  [{attempts}/{len(pending)}] {name}: {mark} "
              f"(eta {eta / 60.0:.1f} min, {rate:.1f}/min measured)")
    elapsed = time.monotonic() - t0
    rate = fetched / elapsed * 60.0 if fetched and elapsed > 0 else 0.0
    return BackfillReport(total, done, fetched, nulls, elapsed, rate)


@dataclass(frozen=True)
class BigQueryProbe:
    """Availability verdict for the BigQuery public dataset."""

    status: str                 # "OK" | "UNDECIDED"
    reason_code: Optional[str]  # "U-ENV" (no credentials) | "U-QUERY" | None
    detail: str

    def label(self) -> str:
        """``status`` or ``STATUS / REASON_CODE``, e.g. ``UNDECIDED / U-ENV``."""
        return self.status if self.reason_code is None else f"{self.status} / {self.reason_code}"


@dataclass(frozen=True)
class BigQueryJobConfigFallback:
    """Parameterized query configuration used when ``google-cloud-bigquery`` is absent."""

    query_parameters: tuple[dict[str, Any], ...]
    packages: tuple[str, ...]
    window_start: str
    window_end: str
    dry_run: bool = False
    maximum_bytes_billed: Optional[int] = None


class BigQueryCapExceeded(RuntimeError):
    """The dry run estimates more bytes than the configured ceiling; nothing was run."""


def normalize_pypi_package(name: str) -> str:
    """PEP 503 normalized PyPI package name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _build_bigquery_job_config(
    packages: Sequence[str],
    window_start: str,
    window_end: str,
    *,
    dry_run: bool = False,
    maximum_bytes_billed: Optional[int] = None,
) -> Any:
    pkg_list = list(packages)
    try:
        from google.cloud import bigquery

        return bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("packages", "STRING", pkg_list),
                bigquery.ScalarQueryParameter("window_start", "STRING", window_start),
                bigquery.ScalarQueryParameter("window_end", "STRING", window_end),
            ],
            dry_run=dry_run,
            use_query_cache=not dry_run,
            maximum_bytes_billed=maximum_bytes_billed,
        )
    except ImportError:
        return BigQueryJobConfigFallback(
            query_parameters=(
                {"name": "packages", "type": "ARRAY<STRING>", "value": tuple(pkg_list)},
                {"name": "window_start", "type": "STRING", "value": window_start},
                {"name": "window_end", "type": "STRING", "value": window_end},
            ),
            packages=tuple(pkg_list),
            window_start=window_start,
            window_end=window_end,
            dry_run=dry_run,
            maximum_bytes_billed=maximum_bytes_billed,
        )


def bigquery_window(now: Optional[datetime] = None) -> tuple[str, str]:
    """``(window_start, window_end)`` as UTC dates: the 30 whole UTC days ending yesterday
    (``end`` exclusive), the span pypistats' ``last_month`` covers."""
    anchor = now or datetime.now(timezone.utc)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    end = anchor.astimezone(timezone.utc).date()
    return (end - timedelta(days=30)).isoformat(), end.isoformat()


def estimate_bigquery_bytes(
    packages: Sequence[str],
    *,
    client: Any,
    now: Optional[datetime] = None,
) -> int:
    """Dry run: bytes the monthly-downloads query would scan for ``packages``. Free, and
    nothing is read or billed."""
    window_start, window_end = bigquery_window(now)
    names = sorted({normalize_pypi_package(p) for p in packages if p})
    cfg = _build_bigquery_job_config(names, window_start, window_end, dry_run=True)
    job = client.query(BIGQUERY_PYPI_MONTHLY_SQL, job_config=cfg)
    total = getattr(job, "total_bytes_processed", None)
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise RuntimeError("BigQuery dry run did not report total_bytes_processed")
    return total


def fetch_bigquery_monthly_bulk(
    packages: Sequence[str],
    *,
    client: Any,
    now: Optional[datetime] = None,
    maximum_bytes_billed: Optional[int] = DEFAULT_BIGQUERY_MAX_BYTES,
) -> dict[str, int]:
    """Query trailing 30-day PyPI download counts for ``packages`` in one BigQuery job.

    Uses parameterized query parameters (``@packages``, ``@window_start``,
    ``@window_end``) rather than string interpolation, pypistats' definition (mirrors
    excluded, 30 UTC days ending yesterday) and ``maximum_bytes_billed`` as a hard
    ceiling (BigQuery fails the job instead of billing more). Returns a mapping from
    PEP 503 normalized package name to integer monthly downloads for every row returned.
    """
    if not packages:
        return {}
    window_start, window_end = bigquery_window(now)
    normalized_set = sorted({normalize_pypi_package(p) for p in packages if p})
    job_config = _build_bigquery_job_config(
        normalized_set, window_start, window_end, maximum_bytes_billed=maximum_bytes_billed
    )
    job = client.query(BIGQUERY_PYPI_MONTHLY_SQL, job_config=job_config)
    rows = job.result() if hasattr(job, "result") else job
    counts: dict[str, int] = {}
    for row in rows:
        if isinstance(row, Mapping):
            pkg = row.get("package")
            dl = row.get("monthly_downloads")
        else:
            pkg = getattr(row, "package", None)
            dl = getattr(row, "monthly_downloads", None)
            if pkg is None and isinstance(row, (tuple, list)) and len(row) >= 2:
                pkg, dl = row[0], row[1]
        if not pkg or isinstance(dl, bool) or not isinstance(dl, int) or dl < 0:
            continue
        counts[normalize_pypi_package(str(pkg))] = dl
    return counts


def backfill_bigquery(
    data_dir: Path,
    *,
    client: Optional[Any] = None,
    now: Optional[datetime] = None,
    limit: Optional[int] = None,
    max_bytes: int = DEFAULT_BIGQUERY_MAX_BYTES,
    dry_run_only: bool = False,
) -> BackfillReport:
    """Bulk-backfill null PyPI download envelopes via one BigQuery public-dataset query.

    Replaces 4+ hours of throttled 15s/repo pypistats polling with a single
    parameterized query against ``bigquery-public-data.pypi.file_downloads`` that counts
    downloads the way pypistats does (mirrors excluded; see :data:`PYPISTATS_MIRRORS`),
    so a filled-in value means what the existing ones mean.

    A dry run runs first and prints the bytes the query would scan. Over ``max_bytes``
    it raises :class:`BigQueryCapExceeded` and nothing runs; otherwise the real job is
    capped with ``maximum_bytes_billed=max_bytes``. ``dry_run_only`` stops after the estimate.
    Packages not returned by the query stay null (never fabricated as zero, R-4 / D-MISS-1).
    """
    names = pypi_packages(data_dir)
    pending = [n for n in names if envelope_monthly(data_dir, n) is None]
    total = len(names)
    done = total - len(pending)
    if limit is not None:
        pending = pending[:limit]
    if not pending:
        print(f"backfill-bigquery: 0 pending pypi downloads ({total} done) - nothing to do")
        return BackfillReport(total, total, 0, 0, 0.0, 0.0)
    if client is None:
        try:
            import google.auth
            from google.cloud import bigquery

            credentials, project = google.auth.default()
            client = bigquery.Client(project=project, credentials=credentials)
        except Exception as e:  # review-ok: gate-env — credentials or client library absent
            raise RuntimeError(f"BigQuery client unavailable: {type(e).__name__}: {e}") from e

    estimate = estimate_bigquery_bytes(pending, client=client, now=now)
    print(f"backfill-bigquery dry run: {len(pending)} packages, {estimate} bytes "
          f"({estimate / 1e9:.2f} GB) to scan; ceiling {max_bytes / 1e9:.0f} GB")
    if estimate > max_bytes:
        raise BigQueryCapExceeded(
            f"dry run estimates {estimate} bytes, over the {max_bytes}-byte ceiling; "
            "raise --bigquery-max-gb or use --limit; nothing was run")
    if dry_run_only:
        return BackfillReport(total, done, 0, len(pending), 0.0, 0.0)

    t0 = time.monotonic()
    counts = fetch_bigquery_monthly_bulk(
        pending, client=client, now=now, maximum_bytes_billed=max_bytes)
    fetched = nulls = 0
    for name in pending:
        norm = normalize_pypi_package(name)
        monthly = counts.get(norm)
        if monthly is not None:
            _write_envelope(data_dir, name, monthly, BIGQUERY_SOURCE)
            fetched += 1
        else:
            nulls += 1
    elapsed = time.monotonic() - t0
    rate = fetched / elapsed * 60.0 if fetched and elapsed > 0 else 0.0
    return BackfillReport(total, done, fetched, nulls, elapsed, rate)


def probe_bigquery(run_live: bool = False, client: Optional[Any] = None) -> BigQueryProbe:
    """Probe reachability of the ``bigquery-public-data`` dataset.

    No credentials on this machine => ``UNDECIDED / U-ENV`` (availability is
    undecidable without credentials; nothing is installed or downloaded).
    An injected ``client`` exposing ``list_datasets(project,
    max_results=...)`` answers the availability question directly and is
    the seam the tests use (mock at the external boundary only).
    """
    if client is not None:
        try:
            datasets = list(client.list_datasets(PUBLIC_DATASET_PROJECT, max_results=10))
        except Exception as e:  # review-ok: gate-network — query failure is not availability
            return BigQueryProbe("UNDECIDED", "U-QUERY",
                                 f"dataset listing failed: {type(e).__name__}: {e}")
        return BigQueryProbe("OK", None,
                             f"bigquery-public-data reachable: {len(datasets)} datasets listed")
    try:
        import google.auth
    except ImportError:
        return BigQueryProbe("UNDECIDED", "U-ENV",
                             "google-auth not installed: no credentials available on this machine")
    try:
        resolved = google.auth.default()
    except Exception as e:  # review-ok: gate-env — no default credentials
        return BigQueryProbe("UNDECIDED", "U-ENV",
                             f"default credentials not resolvable: {type(e).__name__}")
    credentials, project = resolved
    if not run_live:
        return BigQueryProbe("UNDECIDED", "U-ENV",
                             "credentials resolvable; rerun with --probe-bigquery-live to query the dataset")
    try:
        from google.cloud import bigquery
        client = bigquery.Client(project=project, credentials=credentials)
    except Exception as e:  # review-ok: gate-env — client library absent
        return BigQueryProbe("UNDECIDED", "U-QUERY",
                             f"bigquery client construction failed: {type(e).__name__}: {e}")
    return probe_bigquery(run_live=True, client=client)


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point (progress printing allowed for scripts, R-8)."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", type=Path,
                    default=Path(os.environ.get("PORCHLAMP_DATA_DIR", REPO_ROOT / "data")))
    ap.add_argument("--base-url", default=PYPISTATS_BASE)
    ap.add_argument("--interval", type=float, default=DEFAULT_MIN_INTERVAL,
                    help="minimum seconds between pypistats requests (default 15 = 4/min)")
    ap.add_argument("--limit", type=int, default=None,
                    help="refetch at most N nulls this session")
    ap.add_argument("--probe-bigquery", action="store_true",
                    help="report BigQuery public-dataset availability, then exit")
    ap.add_argument("--probe-bigquery-live", action="store_true",
                    help="also query the dataset when credentials resolve")
    ap.add_argument("--bigquery-bulk", action="store_true",
                    help="backfill all pending PyPI downloads in one BigQuery query "
                         "(pypistats' definition: mirrors excluded). A dry run reports the "
                         "bytes first; the job is capped by --bigquery-max-gb")
    ap.add_argument("--bigquery-max-gb", type=float, default=DEFAULT_BIGQUERY_MAX_BYTES / 1e9,
                    help="ceiling on bytes BigQuery may scan/bill for --bigquery-bulk "
                         "(default 100 GB); a dry run over it refuses")
    ap.add_argument("--bigquery-dry-run", action="store_true",
                    help="with --bigquery-bulk: report the bytes to scan and stop")
    a = ap.parse_args(argv)
    if a.probe_bigquery or a.probe_bigquery_live:
        probe = probe_bigquery(run_live=a.probe_bigquery_live)
        print(f"BigQuery probe: {probe.label()} - {probe.detail}")
        return 0
    if a.bigquery_bulk:
        probe = probe_bigquery(run_live=True)
        if probe.status != "OK":
            print(f"BigQuery bulk backfill: {probe.label()} - {probe.detail}")
            return 0
        try:
            report = backfill_bigquery(
                a.data_dir, limit=a.limit, max_bytes=int(a.bigquery_max_gb * 1e9),
                dry_run_only=a.bigquery_dry_run)
        except BigQueryCapExceeded as e:
            print(f"BigQuery bulk backfill REFUSED: {e}")
            return 2
        print(f"backfill-bigquery done: {report.fetched} fetched, {report.still_null} still null, "
              f"{report.already_done} already done, {report.total} total, "
              f"{report.elapsed_seconds:.1f}s")
        return 0
    report = backfill(a.data_dir, base_url=a.base_url, min_interval=a.interval,
                      limit=a.limit)
    print(f"backfill done: {report.fetched} fetched, {report.still_null} still null, "
          f"{report.already_done} already done, {report.total} total, "
          f"{report.elapsed_seconds:.1f}s, {report.rate_per_minute:.1f}/min measured")
    if report.still_null:
        print(f"rerun to resume the {report.still_null} nulls (nulls are not done)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
