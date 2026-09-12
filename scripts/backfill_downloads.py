"""Throttled pypistats backfill runner for null registry-download envelopes.

Packet W1-8 (downloads coverage; `02` section 4 2.3 + D-MISS-1, F8):
monthly_downloads was 77.8% missing because pypistats rate-limits to about
5 requests/min/IP and its 429 arrives as HTTP 200 + HTML body, which the
gather path records as a null monthly. A null is NOT done: this runner
refetches exactly the null pypi download envelopes, throttled to at most
4 requests/min (15 s minimum interval), resumable from the envelopes on
disk (no separate state), printing an ETA derived from the measured
per-request time.

Ecosystem reachability measured by this packet:
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
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from cvi.collect.envelope import write_envelope

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPISTATS_BASE = "https://pypistats.org"
DEFAULT_MIN_INTERVAL = 15.0  # 4 requests/min; pypistats limit is ~5/min/IP
USER_AGENT = "cvi-research/0.1 (ranking study)"
PUBLIC_DATASET_PROJECT = "bigquery-public-data"

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


def _write_envelope(data_dir: Path, name: str, monthly: int, source: str) -> None:
    """Atomically replace the pypi download envelope with a measured value."""
    path = data_dir / "raw" / "downloads" / f"pypi__{name}.json"
    tmp = path.with_name(path.name + ".backfill-tmp")
    write_envelope(
        tmp,
        source=f"{source.rstrip('/')}/api/packages/{name}/recent",
        data={"monthly": monthly},
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
        monthly = fetch_last_month(base_url, name)
        if monthly is not None:
            _write_envelope(data_dir, name, monthly, base_url)
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
                    default=Path(os.environ.get("CVI_DATA_DIR", REPO_ROOT / "data")))
    ap.add_argument("--base-url", default=PYPISTATS_BASE)
    ap.add_argument("--interval", type=float, default=DEFAULT_MIN_INTERVAL,
                    help="minimum seconds between pypistats requests (default 15 = 4/min)")
    ap.add_argument("--limit", type=int, default=None,
                    help="refetch at most N nulls this session")
    ap.add_argument("--probe-bigquery", action="store_true",
                    help="report BigQuery public-dataset availability, then exit")
    ap.add_argument("--probe-bigquery-live", action="store_true",
                    help="also query the dataset when credentials resolve")
    a = ap.parse_args(argv)
    if a.probe_bigquery or a.probe_bigquery_live:
        probe = probe_bigquery(run_live=a.probe_bigquery_live)
        print(f"BigQuery probe: {probe.label()} - {probe.detail}")
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
