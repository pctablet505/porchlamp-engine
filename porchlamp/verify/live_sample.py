"""Live re-verification canary for rated rows (``verify --live-sample``).

The design is ``docs/v2/03-ANTI-MANIPULATION.md`` section 5
("Live re-verification").

The canary re-fetches, for a sample of rated repositories, the two fact
families the pipeline promotes on: the GitHub metadata counters
(stars / forks) and the PR window head (open-PR count, latest PR number).
The recorded side is the verified bundle under
``data/verified/<owner>__<name>.json`` -- the recorded response whose
envelope hashes the promotion chain vouches for; the live side is one fresh
GitHub GraphQL fetch. A disagreement beyond tolerance is a ``source_drift``
verdict and the row is held from promotion until re-gathered. Verdicts:
``ok`` (no drift; eligible to promote), ``source_drift``,
``live_fetch_failed`` (transport error; retry next night) and
``record_missing`` (no verified bundle to re-verify against).

Sampling: a deterministic random 5% of all rated rows plus the top 50 rows
per ecosystem, unioned and returned in composite rank order (``overall_porchlamp``
desc, then ``owner``/``name``). The random draw is seeded; nightly runs use
the UTC date as the seed, so one nightly run is reproducible while the
sample rotates between nights.

Drift rules (03 section 5):

* ``stars`` / ``forks`` -- monotone: the live value may exceed the recorded
  one (the repository grows between runs) but may not fall below it by more
  than :data:`MONOTONE_REL_TOL`; small counter rollbacks happen when GitHub
  purges deleted accounts, a drop beyond the tolerance does not.
* ``latest_pr_number`` -- monotone with no tolerance: PR numbers are
  assigned sequentially and never reused, so a live maximum below the
  recorded one is impossible.
* ``open_pr_count`` -- free to move both directions; drift when the
  absolute difference exceeds ``max(OPEN_PR_ABS_TOL,
  OPEN_PR_REL_TOL * recorded)``, i.e. the recorded window head no longer
  resembles the source.

"Open-PR count" and "latest PR number" always mean the values over the PR
window head (the :data:`PR_HEAD_SIZE` newest PRs of every state), the same
window shape the sweep records -- not the repository-wide totals.

The module reads no clock (D-VER-3 / 04 section 8): the nightly seed is a
caller argument (``scripts/canary.sh`` passes the UTC date) and the run is
a pure function of ``(rows, transport, data_dir, seed)``. :func:`main` is
the CLI layer (``python -m porchlamp.verify.live_sample``) and the only place
that touches stdout.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Protocol, Sequence

#: Share of the rated population the nightly canary draws at random.
RANDOM_SAMPLE_FRACTION: float = 0.05

#: Top rows re-fetched per ecosystem, in composite rank order.
TOP_N_PER_ECOSYSTEM: int = 50

#: PR window head size: the newest PRs (every state) re-fetched live and
#: compared against the recorded head. Matches the sweep's window head
#: (``PR_SAMPLE`` in ``scripts/gather.py``).
PR_HEAD_SIZE: int = 30

#: Relative tolerance for the impossible direction of a monotone counter:
#: live below recorded by more than this share is ``source_drift``.
#: (Distinct from ``scripts/verify.py``'s STARS_TOL, which compares two
#: independent sources at one point in time; this compares one source at
#: two points in time, where only the decrease is impossible.)
MONOTONE_REL_TOL: float = 0.10

#: Open-PR count tolerances: drift when the absolute difference exceeds
#: ``max(OPEN_PR_ABS_TOL, OPEN_PR_REL_TOL * recorded)``. The absolute floor
#: keeps a recorded 0 open from flagging on one overnight PR; the relative
#: half catches a recorded window head that no longer resembles the source.
OPEN_PR_ABS_TOL: int = 5
OPEN_PR_REL_TOL: float = 0.50

#: Repository keys whose verdict is not ``ok`` in the JSON report.
_VERDICT_OK: str = "ok"
_VERDICT_DRIFT: str = "source_drift"
_VERDICT_FETCH_FAILED: str = "live_fetch_failed"
_VERDICT_RECORD_MISSING: str = "record_missing"

_LIVE_QUERY: str = (
    "{ r: repository(owner: %s, name: %s) {"
    "stargazerCount forkCount"
    " pullRequests(states: [OPEN, MERGED, CLOSED], first: %d,"
    " orderBy: {field: CREATED_AT, direction: DESC}) {"
    " nodes { number closedAt mergedAt } } } }"
)


# --------------------------------------------------------------------------- #
# Data types                                                                  #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RatedRow:
    """One rated row from the store's ``repositories`` table.

    ``overall_porchlamp`` is the composite the sample ranks by; ``stars``/``forks``
    are the row's stored counters, recorded for the report, not compared
    (the recorded side is the verified bundle).
    """

    owner: str
    name: str
    ecosystem: str
    stars: int
    forks: int
    overall_porchlamp: float


@dataclass(frozen=True)
class RecordedFacts:
    """The recorded side: counters and PR window head from one verified bundle.

    ``stars`` / ``forks`` / ``latest_pr_number`` are None when the bundle
    recorded no value for that field (the field then compares as
    single-source ok); ``open_pr_count`` is 0 when the recorded head has no
    open PR.
    """

    stars: Optional[int]
    forks: Optional[int]
    open_pr_count: int
    latest_pr_number: Optional[int]
    bundle_path: str


@dataclass(frozen=True)
class LiveFacts:
    """The live side: one fresh GitHub GraphQL fetch for one repository."""

    stars: int
    forks: int
    open_pr_count: int
    latest_pr_number: Optional[int]


@dataclass(frozen=True)
class FieldCheck:
    """One compared field: name, both sides, the pass/fail and a detail string."""

    field: str
    recorded: Optional[int]
    live: Optional[int]
    ok: bool
    detail: str


@dataclass(frozen=True)
class RepoVerdict:
    """The canary verdict for one sampled repository.

    ``verdict`` is one of ``ok``, ``source_drift``, ``live_fetch_failed``,
    ``record_missing``. ``checks`` is empty for the two non-comparison
    verdicts; ``detail`` carries the transport error text for
    ``live_fetch_failed``.
    """

    owner: str
    name: str
    verdict: str
    checks: tuple[FieldCheck, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class LiveSampleReport:
    """Outcome of one canary run over a ranked sample.

    ``held`` is every row not eligible to promote (any verdict but ``ok``);
    ``promoted`` is the rest. ``live_requests`` counts the live transport
    calls the run made (every verdict except ``record_missing``).
    """

    seed: int
    population: int
    verdicts: tuple[RepoVerdict, ...]

    @property
    def held(self) -> tuple[RepoVerdict, ...]:
        """Rows held from promotion: every verdict except ``ok``."""
        return tuple(v for v in self.verdicts if v.verdict != _VERDICT_OK)

    @property
    def promoted(self) -> tuple[RepoVerdict, ...]:
        """Rows that passed the canary (verdict ``ok``): eligible to promote."""
        return tuple(v for v in self.verdicts if v.verdict == _VERDICT_OK)

    @property
    def drift_count(self) -> int:
        """Number of ``source_drift`` verdicts in this run."""
        return sum(1 for v in self.verdicts if v.verdict == _VERDICT_DRIFT)

    @property
    def live_requests(self) -> int:
        """Live transport calls made: one per verdict except ``record_missing``."""
        return sum(1 for v in self.verdicts if v.verdict != _VERDICT_RECORD_MISSING)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable report (the nightly verdict file's content)."""
        return {
            "seed": self.seed,
            "population": self.population,
            "live_requests": self.live_requests,
            "drift_count": self.drift_count,
            "held": [f"{v.owner}/{v.name}" for v in self.held],
            "promoted": [f"{v.owner}/{v.name}" for v in self.promoted],
            "verdicts": [asdict(v) for v in self.verdicts],
        }


class Transport(Protocol):
    """Boundary the canary re-fetches through; tests inject a fake transport."""

    def fetch_live_facts(self, owner: str, name: str) -> LiveFacts:
        """Re-fetch the live counters and PR window head for one repository."""
        ...


# --------------------------------------------------------------------------- #
# Live transport                                                              #
# --------------------------------------------------------------------------- #


class GitHubTransport:
    """Live GitHub re-fetch: one ``gh api graphql`` call per repository.

    One aliased query returns the counters and the PR window head
    (:data:`PR_HEAD_SIZE` newest PRs of every state, created-desc) -- the
    same request shape the sweep uses for its window head. Transport
    failures and GraphQL error blocks raise :class:`RuntimeError`.
    """

    def __init__(self, timeout: int = 90) -> None:
        self._timeout = timeout

    def fetch_live_facts(self, owner: str, name: str) -> LiveFacts:
        """One live fetch for ``(owner, name)``; raises on any transport error."""
        query = _LIVE_QUERY % (json.dumps(owner), json.dumps(name), PR_HEAD_SIZE)
        out = subprocess.run(["gh", "api", "graphql", "-f", f"query={query}"],
                             capture_output=True, text=True, timeout=self._timeout)
        if out.returncode != 0:
            raise RuntimeError(
                f"gh api graphql rc={out.returncode}: {out.stderr[:160]}")
        return parse_live_payload(json.loads(out.stdout))


def parse_live_payload(payload: dict[str, Any]) -> LiveFacts:
    """Turn one GitHub GraphQL payload into :class:`LiveFacts`.

    ``payload`` is the parsed ``gh api graphql`` body (``{"data": {"r": ...}}``
    for the query in :data:`_LIVE_QUERY`), recorded or live. Raises
    :class:`RuntimeError` on a GraphQL errors block or a repository lookup
    that found nothing; a repository with no PRs yields
    ``open_pr_count=0`` and ``latest_pr_number=None``.
    """
    if payload.get("errors"):
        raise RuntimeError(f"graphql errors: {str(payload['errors'])[:160]}")
    r = (payload.get("data") or {}).get("r")
    if not r:
        raise RuntimeError("github returned no repository object")
    nodes = ((r.get("pullRequests") or {}).get("nodes")) or []
    open_count = sum(
        1 for n in nodes
        if n.get("closedAt") is None and n.get("mergedAt") is None)
    numbers = [int(n["number"]) for n in nodes]
    return LiveFacts(
        stars=int(r.get("stargazerCount", 0)),
        forks=int(r.get("forkCount", 0)),
        open_pr_count=open_count,
        latest_pr_number=max(numbers) if numbers else None,
    )


# --------------------------------------------------------------------------- #
# Sample selection                                                            #
# --------------------------------------------------------------------------- #


def select_sample(rows: Sequence[RatedRow], seed: int) -> tuple[RatedRow, ...]:
    """Random 5% plus top 50 per ecosystem, in composite rank order.

    The random draw is made over the population sorted by ``(owner, name)``
    with :class:`random.Random` seeded by ``seed``, so a ``(seed,
    population)`` pair always yields the same sample; the draw size is
    ``ceil(RANDOM_SAMPLE_FRACTION * n)`` (at least 1). The union with the
    per-ecosystem top :data:`TOP_N_PER_ECOSYSTEM` is returned in rank order:
    ``overall_porchlamp`` desc, then ``owner``, then ``name``.
    """
    if not rows:
        return ()
    population = sorted(rows, key=lambda r: (r.owner, r.name))
    k = max(1, math.ceil(RANDOM_SAMPLE_FRACTION * len(population)))
    random_keys = {(row.owner, row.name)
                   for row in random.Random(seed).sample(population, k)}
    ranked = sorted(rows, key=lambda r: (-r.overall_porchlamp, r.owner, r.name))
    top_keys: set[tuple[str, str]] = set()
    per_ecosystem: dict[str, int] = {}
    for row in ranked:
        seen = per_ecosystem.get(row.ecosystem, 0)
        if seen < TOP_N_PER_ECOSYSTEM:
            top_keys.add((row.owner, row.name))
            per_ecosystem[row.ecosystem] = seen + 1
    return tuple(row for row in ranked
                 if (row.owner, row.name) in random_keys
                 or (row.owner, row.name) in top_keys)


# --------------------------------------------------------------------------- #
# Store and recorded-side loading                                             #
# --------------------------------------------------------------------------- #


def load_rated_rows(db_path: str) -> tuple[RatedRow, ...]:
    """All rated rows from the store's ``repositories`` table, in rank order.

    The store is opened read-only; the table only needs the six columns
    selected here. ``db_path`` pointing at a missing or unreadable file
    raises :class:`sqlite3.OperationalError` for the caller to report.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = conn.execute(
            "SELECT owner, name, ecosystem, stars, forks, overall_porchlamp "
            "FROM repositories")
        rows = [RatedRow(owner=o, name=n, ecosystem=e, stars=int(s),
                         forks=int(f), overall_porchlamp=float(c))
                for o, n, e, s, f, c in cur.fetchall()]
    finally:
        conn.close()
    return tuple(sorted(rows, key=lambda r: (-r.overall_porchlamp, r.owner, r.name)))


def load_verified_bundle(data_dir: Path, owner: str, name: str) -> Optional[Path]:
    """Path of the verified bundle for ``(owner, name)``, or None when absent."""
    path = Path(data_dir) / "verified" / f"{owner}__{name}.json"
    return path if path.is_file() else None


def recorded_facts_from_bundle(doc: dict[str, Any],
                               bundle_path: Path) -> Optional[RecordedFacts]:
    """The recorded counters and PR window head from one verified bundle.

    ``doc`` is the parsed ``data/verified/<owner>__<name>.json`` document
    (``{"bundle": {"meta": ..., "pr_events": ...}, "report": ...}``). The
    open-PR count is the head's open events (``closed_at`` null); the
    latest PR number is the head's maximum ``pr_number``. Returns None when
    the bundle carries neither counters nor a PR head to re-verify.
    """
    bundle = doc.get("bundle") or {}
    meta = bundle.get("meta") or {}
    stars, forks = meta.get("stars"), meta.get("forks")
    events = bundle.get("pr_events") or []
    if stars is None and forks is None and not events:
        return None
    open_count = 0
    latest: Optional[int] = None
    for event in events:
        if not event.get("closed_at") and not event.get("merged_at"):
            open_count += 1
        number = event.get("pr_number")
        if isinstance(number, int) and (latest is None or number > latest):
            latest = number
    return RecordedFacts(
        stars=int(stars) if isinstance(stars, int) else None,
        forks=int(forks) if isinstance(forks, int) else None,
        open_pr_count=open_count,
        latest_pr_number=latest,
        bundle_path=str(bundle_path),
    )


# --------------------------------------------------------------------------- #
# Comparison                                                                  #
# --------------------------------------------------------------------------- #


def _check_monotone(field: str, recorded: Optional[int],
                    live: int) -> FieldCheck:
    if recorded is None:
        return FieldCheck(field, None, live, True, "recorded value absent")
    floor = recorded * (1.0 - MONOTONE_REL_TOL)
    return FieldCheck(
        field, recorded, live, live >= floor,
        f"{field} recorded={recorded} live={live} monotone floor={floor:.0f} "
        f"(drop tolerance {MONOTONE_REL_TOL:.0%})")


def _check_latest_pr(recorded: Optional[int],
                     live: Optional[int]) -> FieldCheck:
    if recorded is None:
        return FieldCheck("latest_pr_number", None, live, True,
                          "recorded value absent")
    ok = live is not None and live >= recorded
    return FieldCheck("latest_pr_number", recorded, live, ok,
                      f"recorded={recorded} live={live} "
                      "(PR numbers never decrease)")


def _check_open_prs(recorded: int, live: int) -> FieldCheck:
    tol = max(OPEN_PR_ABS_TOL, OPEN_PR_REL_TOL * recorded)
    ok = abs(live - recorded) <= tol
    return FieldCheck("open_pr_count", recorded, live, ok,
                      f"recorded={recorded} live={live} tolerance={tol:.1f}")


def compare_fields(recorded: RecordedFacts, live: LiveFacts) -> tuple[FieldCheck, ...]:
    """Compare the four recorded/live facts; one :class:`FieldCheck` each.

    Order is fixed: ``stars``, ``forks``, ``latest_pr_number``,
    ``open_pr_count``. A missing recorded value compares ok as single-source
    (there is no second point in time to disagree with).
    """
    return (
        _check_monotone("stars", recorded.stars, live.stars),
        _check_monotone("forks", recorded.forks, live.forks),
        _check_latest_pr(recorded.latest_pr_number, live.latest_pr_number),
        _check_open_prs(recorded.open_pr_count, live.open_pr_count),
    )


# --------------------------------------------------------------------------- #
# Run                                                                         #
# --------------------------------------------------------------------------- #


def _run_one(row: RatedRow, transport: Transport,
             data_dir: Path) -> RepoVerdict:
    path = load_verified_bundle(data_dir, row.owner, row.name)
    if path is not None:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return RepoVerdict(row.owner, row.name, _VERDICT_RECORD_MISSING,
                               detail=f"unreadable verified bundle: {exc}")
        recorded = recorded_facts_from_bundle(doc, path)
        if recorded is not None:
            try:
                live = transport.fetch_live_facts(row.owner, row.name)
            except Exception as exc:  # review-ok: external network/transport failures convert to RepoVerdict(FETCH_FAILED)
                return RepoVerdict(row.owner, row.name, _VERDICT_FETCH_FAILED,
                                   detail=str(exc))
            checks = compare_fields(recorded, live)
            verdict = (_VERDICT_DRIFT if any(not c.ok for c in checks)
                       else _VERDICT_OK)
            return RepoVerdict(row.owner, row.name, verdict, checks=checks)
        return RepoVerdict(row.owner, row.name, _VERDICT_RECORD_MISSING,
                           detail="bundle has no recorded counters or PR head")
    return RepoVerdict(row.owner, row.name, _VERDICT_RECORD_MISSING)


def run_live_sample(rows: Sequence[RatedRow], transport: Transport,
                    data_dir: Path, seed: int,
                    max_live: Optional[int] = None) -> LiveSampleReport:
    """Re-verify the sampled rows against the live source.

    ``rows`` is the rated population (rank order); the sample is
    :func:`select_sample` truncated to the first ``max_live`` ranked rows
    when ``max_live`` is given (the cap on live repositories per run).
    ``data_dir`` holds ``verified/<owner>__<name>.json`` recorded responses.
    Rows whose verdict is not ``ok`` are held from promotion (the report's
    :attr:`LiveSampleReport.held`); ``ok`` rows are the promoted ones.
    """
    sample = select_sample(rows, seed)
    if max_live is not None:
        sample = sample[:max_live]
    verdicts = tuple(_run_one(row, transport, data_dir) for row in sample)
    return LiveSampleReport(seed=seed, population=len(rows), verdicts=verdicts)


def default_seed(now: datetime) -> int:
    """Seed for a nightly run: the caller-provided time's UTC date as ``YYYYMMDD``.

    Stable within one night, different each night: the sample rotates
    while any single nightly run stays reproducible. The caller owns the
    clock (D-VER-3 / 04 section 8): ``scripts/canary.sh`` passes the UTC
    date; this helper only converts a provided time to a seed.
    """
    return now.year * 10000 + now.month * 100 + now.day


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


_REPO_ROOT: Path = Path(__file__).resolve().parents[2]


def _config_error(ap: argparse.ArgumentParser, message: str) -> int:
    print(f"error: {message}")
    ap.print_usage()
    return 2


def main(argv: Optional[Sequence[str]] = None,
         transport: Optional[Transport] = None) -> int:
    """CLI entry point (``python -m porchlamp.verify.live_sample``).

    Prints one line per sampled row (verdict plus the failed-field details),
    a summary with the held / promoted counts and the live-request count,
    and writes the JSON verdict file (default
    ``<data-dir>/canary/verdicts-<seed>.json``). Exit codes: 0 when no row
    is held, 1 when at least one row is held, 2 on configuration errors.
    """
    ap = argparse.ArgumentParser(
        prog="porchlamp.verify.live_sample",
        description="Live re-verification canary (03-ANTI-MANIPULATION section 5).")
    ap.add_argument("--live-sample", action="store_true",
                    help="run the live sample (explicit; mirrors `verify --live-sample`)")
    ap.add_argument("--db", default=str(_REPO_ROOT / "data" / "porchlamp.db"),
                    help="store with the rated rows (default: data/cvi.db)")
    ap.add_argument("--data-dir", type=Path, default=_REPO_ROOT / "data",
                    help="directory holding verified/ (default: data/)")
    ap.add_argument("--seed", type=int, default=None,
                    help="sample seed, required: the caller owns the clock "
                         "(canary.sh passes the UTC date, stable per night)")
    ap.add_argument("--max-live", type=int, default=None, metavar="N",
                    help="cap the ranked sample to the top N rows before any live fetch")
    ap.add_argument("--out", type=Path, default=None,
                    help="verdict file path (default: <data-dir>/canary/verdicts-<seed>.json)")
    a = ap.parse_args(list(argv) if argv is not None else None)
    if not a.live_sample:
        return _config_error(ap, "pass --live-sample to run the live sample")
    if a.seed is None:
        return _config_error(ap, "--seed is required: the caller owns the clock "
                                 "(canary.sh passes the UTC date)")
    seed = a.seed
    try:
        rows = load_rated_rows(a.db)
    except sqlite3.Error as exc:
        return _config_error(ap, f"no such store or no repositories table: {a.db} ({exc})")
    if not rows:
        return _config_error(ap, f"no rated rows in {a.db}; nothing to re-verify")
    live_transport: Transport = transport if transport is not None else GitHubTransport()
    report = run_live_sample(rows, live_transport, a.data_dir, seed,
                             max_live=a.max_live)
    out_path = (a.out if a.out is not None
                else a.data_dir / "canary" / f"verdicts-{seed}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True),
                        encoding="utf-8")

    print(f"Live re-verification canary: seed={seed} population={report.population} "
          f"sample={len(report.verdicts)}")
    for v in report.verdicts:
        failed = [f"{c.field}: {c.detail}" for c in v.checks if not c.ok]
        extra = ("; " + " | ".join(failed)) if failed else ""
        note = f" ({v.detail})" if v.detail and v.verdict != _VERDICT_DRIFT else ""
        print(f"{v.owner}/{v.name}: {v.verdict}{extra}{note}")
    print(f"held from promotion: {len(report.held)} "
          f"(drift {report.drift_count}, "
          f"fetch_failed {sum(1 for v in report.verdicts if v.verdict == _VERDICT_FETCH_FAILED)}, "
          f"record_missing {sum(1 for v in report.verdicts if v.verdict == _VERDICT_RECORD_MISSING)}); "
          f"promoted: {len(report.promoted)}")
    print(f"live requests: {report.live_requests}")
    print(f"verdicts -> {out_path}")
    return 1 if report.held else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
