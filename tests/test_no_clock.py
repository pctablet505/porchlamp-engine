"""D-VER-3 / 04 §8: the scoring core owns no clock; callers pass the time in.

Scanned twice: the wall-clock regex over the non-exempt modules, and a
SQL-clock scan (``CURRENT_TIMESTAMP``, ``datetime('now')``) over every
``cvi/`` module, because a DDL default reads the database's clock on the
module's behalf (W0-2b).

Fail-before proof for packet W0-2. Before the change ``cvi/playbook.py``
defined its own ``BENCHMARK_NOW`` and defaulted ``now`` to it, so a scoring
call that omitted the reference time silently scored against a frozen date
instead of the snapshot's ``as_of``.

A module-owned reference time is the same defect as a ``datetime.now()`` call:
both make a score depend on a clock the scoring module itself chooses (R-2).
That is why ``BENCHMARK_NOW`` is in the scanned pattern set next to the three
wall-clock readers.

The over-correction guard is
:func:`test_explicit_reference_time_path_is_unchanged`: with ``now`` passed
explicitly, the pre-change numbers are reproduced exactly.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from cvi import playbook
from cvi.deepdive import build_deep_dive
from cvi.engine import score_repository
from cvi.models import (
    AIPosture,
    AuthorAssociation,
    Ecosystem,
    LegalIPModel,
    RawPREvent,
    RepoMeta,
)
from cvi.playbook import generate_maintainer_playbook

CVI_PKG = Path(__file__).resolve().parent.parent / "cvi"

CLOCK_RE = re.compile(
    r"datetime\.now|utcnow|time\.time|BENCHMARK_NOW|datetime\.today|date\.today"
)

# 04 §8 exempts the collect/serve paths: fetching and rate limiting own the
# clock. ``cvi/collect/`` is the real target-layout directory (W4-1 moved
# the rate limiter's ``time.monotonic`` defaults there from ``cvi/
# ingest.py``, which no longer needs an exemption at all -- it has zero
# clock tokens of its own left, see ``_sources()``'s directory filter).
EXCLUDED_DIRS = {"collect", "serve"}
EXCLUDED_MODULES = {
    "benchmark_data.py": "single definition of the fixture clock (R-2)",
}

NOW = datetime(2026, 9, 11, 12, 0, 0)


def _sources() -> list[Path]:
    """Every ``cvi/`` module except the collect/serve path."""
    return [
        path
        for path in sorted(CVI_PKG.rglob("*.py"))
        if not EXCLUDED_DIRS.intersection(path.parts)
        and path.name not in EXCLUDED_MODULES
    ]


def _offenders(paths: list[Path]) -> list[str]:
    """``path:line`` for each line that reads a time the module itself owns."""
    return [
        f"{path.relative_to(CVI_PKG.parent)}:{number}"
        for path in paths
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        )
        if CLOCK_RE.search(line)
    ]


# W0-2b gap 2: a clock the database reads on the module's behalf (a DDL
# default or a SQL clock function) is invisible to the wall-clock regex
# above, so it is scanned separately. The scan covers every file in the
# package: no store-side clock exists that the collect/serve exemption
# could legitimately cover.
SQL_CLOCK_RE = re.compile(
    r"CURRENT_TIMESTAMP|datetime\(\s*['\"]now['\"]\s*\)", re.IGNORECASE
)


def _sql_offenders() -> list[str]:
    """``path:line`` for each line that reads a clock inside SQL."""
    return [
        f"{path.relative_to(CVI_PKG.parent)}:{number}"
        for path in sorted(CVI_PKG.rglob("*.py"))
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        )
        if SQL_CLOCK_RE.search(line)
    ]


def test_cvi_modules_own_no_sql_clock() -> None:
    """No SQL-side clock anywhere in ``cvi/``: no DDL timestamp default,
    no SQL NOW() reader (D-VER-3: the database is a pure function of the
    times its callers pass in)."""
    assert _sql_offenders() == []


def test_sql_clock_pattern_sees_both_clock_forms() -> None:
    """Anti-vacuity for the SQL scan: the pattern catches both forms."""
    assert SQL_CLOCK_RE.search("last_indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,")
    assert SQL_CLOCK_RE.search("SELECT datetime('now') FROM sqlite_master LIMIT 1;")


def test_clock_pattern_sees_the_dot_today_forms() -> None:
    """Anti-vacuity for the residual gap the INT-W0-3f review found open
    (Consider 4): ``datetime.today()``/``date.today()`` are wall-clock reads
    the pre-W0-3g pattern missed entirely; both stayed green on a module with
    one added. Mutation-proved live (fail-before/restore, W0-3g handoff):
    injecting ``datetime.today()`` into a scanned module passed
    ``test_cvi_scoring_modules_own_no_clock`` before this pattern change and
    fails it after."""
    assert CLOCK_RE.search("return datetime.today().timestamp()")
    assert CLOCK_RE.search("return date.today().isoformat()")


def test_cvi_scoring_modules_own_no_clock() -> None:
    """No wall-clock read and no module-owned reference time in the core."""
    assert _offenders(_sources()) == []


def test_exempt_modules_actually_own_a_clock() -> None:
    """Anti-vacuity for the exemptions: the scanner does find their clocks.

    If a module below stops owning a clock this test goes red, which forces a
    re-read of the exemption instead of letting it rot into a blind spot.
    """
    paths = [
        CVI_PKG / name for name in sorted(EXCLUDED_MODULES) if (CVI_PKG / name).is_file()
    ]
    assert {hit.rsplit(":", 1)[0] for hit in _offenders(paths)} == {
        "cvi/benchmark_data.py",
    }


@pytest.mark.parametrize(
    ("entry_point", "call"),
    [
        (
            "generate_maintainer_playbook",
            lambda: generate_maintainer_playbook(_meta(), _pr_events()),
        ),
        (
            "score_repository",
            lambda: score_repository(_meta(), _pr_events()),
        ),
        (
            "build_deep_dive",
            lambda: build_deep_dive(_meta(), _pr_events()),
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_scoring_entry_points_require_an_explicit_reference_time(
    entry_point: str, call
) -> None:
    """Omitting the reference time is a call error, never a module-owned default."""
    with pytest.raises(TypeError, match="argument"):
        call()


def test_playbook_no_longer_defines_benchmark_now() -> None:
    """One definition per fact: the fixture clock lives in ``benchmark_data``."""
    assert not hasattr(playbook, "BENCHMARK_NOW")


def test_explicit_reference_time_path_is_unchanged() -> None:
    """Over-correction guard: the values produced when ``now`` is passed in."""
    pb = generate_maintainer_playbook(_meta(), _pr_events(), now=NOW)
    assert pb["current_cvi"] == 76.46
    assert pb["potential_cvi"] == 80.84
    assert pb["total_point_gain"] == 4.38
    assert pb["current_tier"] == "Tier A"
    assert pb["projected_tier"] == "Tier S"
    assert pb["actions"] == [
        {
            "priority": 1,
            "bottleneck": "Review Latency Friction (TTFHR > 24h)",
            "recommendation": (
                "Accelerate initial maintainer review turnaround to under 12 "
                "hours (target TTFHR <= 12h) to reduce contributor drop-off."
            ),
            "delta_param_score": 50.0,
            "delta_cvi": 4.38,
            "delta_friction": -8.75,
        }
    ]


def _meta() -> RepoMeta:
    return RepoMeta(
        owner="test-org",
        name="test-repo",
        ecosystem=Ecosystem.PYPI,
        stars=10000,
        forks=1500,
        monthly_downloads=1000000,
        dependents_count=5000,
        elephant_factor=4,
        legal_ip_model=LegalIPModel.DCO,
        ai_posture=AIPosture.REGULATED,
        scorecard_score=9.5,
        active_nonbot_commits_180d=200,
        off_platform_reviews=False,
        hard_ai_ban=False,
        active_unclaimed_gfis=15,
        mean_inline_comments=3.5,
        rr_90=0.6,
        dx_ci_minutes=15.0,
    )


def _pr_events() -> list[RawPREvent]:
    """Three external PRs with 48h review latency: one bottleneck, no gate."""
    out: list[RawPREvent] = []
    for number, days in ((1, 2.0), (2, 4.0), (3, 20.0)):
        created = NOW - timedelta(days=days)
        out.append(
            RawPREvent(
                repo_owner="test-org",
                repo_name="test-repo",
                pr_number=number,
                created_at=created,
                author_login="contrib",
                author_association=AuthorAssociation.FIRST_TIME,
                author_prior_merged_commits=0,
                closed_at=created + timedelta(hours=2),
                merged_at=created + timedelta(hours=2),
                is_external=True,
                response_latency_hours=48.0,
                maintainer_review_comments=3,
            )
        )
    return out


def test_ratelimit_directory_exemption_covers_only_the_monotonic_defaults() -> None:
    """W0-gate P-01 (W4-1: re-pinned to ``cvi/collect/ratelimit.py``, where
    the rate limiter's ``time.monotonic`` defaults now live -- they moved
    out of ``cvi/ingest.py``, which no longer needs any exemption at all).

    (1) No line of the module today matches the wall-clock scanner, so the
    directory exemption currently removes nothing THIS module needs from
    the scan.
    (2) The two ``time.monotonic`` lines are the only clock tokens in the
    module: the exemption's actual scope.
    (3) The scanner's own predicate (``CLOCK_RE`` over the module lines, the
    same operation ``_offenders`` performs on every scanned file) fails on a
    copy of the module with one ``datetime.now()`` added: any wall-clock call
    in this exempt module is caught by the guard, so the ``collect``
    directory exemption cannot silently rot into a blind spot for it.
    """
    source = (CVI_PKG / "collect" / "ratelimit.py").read_text(encoding="utf-8")
    wall_clock_lines = [line for line in source.splitlines() if CLOCK_RE.search(line)]
    assert wall_clock_lines == []
    monotonic_lines = [
        line for line in source.splitlines() if "time.monotonic" in line
    ]
    assert len(monotonic_lines) == 2
    assert all("clock if clock is not None else time.monotonic" in line
               for line in monotonic_lines)

    probe = source + (
        "\n"
        "\n"
        "def _wall_clock_probe() -> float:\n"
        "    return datetime.now().timestamp()\n"
    )
    probe_lines = [line for line in probe.splitlines() if CLOCK_RE.search(line)]
    assert probe_lines == ["    return datetime.now().timestamp()"]


def test_ingest_module_is_gone_and_needs_no_exemption() -> None:
    """W4-1: ``cvi/ingest.py`` is fully deleted (its content moved to
    ``cvi.collect.ratelimit``/``cvi.lakehouse`` or removed as dead code;
    the one-release deprecation shim itself was then deleted, per the
    inventory's own instruction, once the grep sweep showed no in-repo
    caller remained). It never needs a per-module entry in
    ``EXCLUDED_MODULES`` again -- pinned here so a stale exemption is
    never silently re-added for a module that does not exist."""
    assert "ingest.py" not in EXCLUDED_MODULES
    assert not (CVI_PKG / "ingest.py").exists()
