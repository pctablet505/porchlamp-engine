"""The static report pages (``scripts/export_static.py --stats``).

Two pages that a crawler or a reader can read without JavaScript:
``/black-holes/`` (the latest snapshot's report) and ``/black-holes/<snapshot_id>/``
(the same report, frozen for citations), plus one CSV of the ranked projects. The
methodology is the app's own view at ``/methodology/`` (owner, 2026-10-17: one
methodology page, the app's). Nothing here is typed by hand:

* the judged / black-hole counts, overall and per ecosystem, come from the
  snapshot database (:func:`decided_counts`, the definition
  :mod:`porchlamp.snapshot.stats` uses);
* the ranked count and the outside-pull-request totals come from the API's
  records, the window dates from their ``black_hole_evidence``;
* the star bands, the rank correlation, the top-100 figures and the corpus
  size come from the stats file that ``python -m porchlamp.snapshot stats``
  (:mod:`porchlamp.snapshot.stats`) computes from the database and the data
  root's ``raw/github_meta`` star counts (the database stores none);
* the method sentences are checked against ``methodology.json`` (the gates'
  own criterion and cutoffs) before they are printed.

Every sentence whose truth depends on a number is guarded (:func:`build_facts`):
a guard that fails raises :class:`ReportError` and the export stops, so a false
sentence is never written. No script, font or library is loaded by any page.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from html import escape
from pathlib import Path
from statistics import NormalDist
from typing import Any, Iterable, Mapping, Sequence

from porchlamp.snapshot.stats import STAR_BUCKETS, bucket_label

SITE = "https://porchlamp.org"
CC_BY_4 = "https://creativecommons.org/licenses/by/4.0/"
GITHUB_REPO = "https://github.com/pctablet505/porchlamp"
CREATOR_ID = f"{SITE}/#creator"
WEB_APP = Path(__file__).resolve().parents[2] / "web" / "app"

REPORT_H1 = "Stars don't review pull requests"
CITE_TITLE = "Stars don't review pull requests: the state of open-source black holes"

#: The star bands the report charts: the stats producer's bands from 1,000 stars up (their keys in the stats file).
STAR_BANDS = tuple(bucket_label(lo, hi) for lo, hi in STAR_BUCKETS if lo >= 1000)
#: Ecosystem key -> display name (the report's wording).
ECOSYSTEM_NAMES = {
    "pypi": "PyPI",
    "npm": "npm",
    "go": "Go",
    "systems": "systems (C, C++ and Zig)",
    "crates": "crates.io (Rust)",
    "maven": "Maven (Java)",
}
#: Ecosystem key -> the address of its landing page (``porchlamp.serve.landing_pages``); lens key -> likewise.
ECOSYSTEM_SLUGS = {"pypi": "python", "npm": "javascript", "go": "go", "systems": "c-cpp", "crates": "rust", "maven": "java"}
LENS_SLUGS = {"first_timer": "first-timers", "career_capital": "career", "senior_systems": "senior-systems", "ai_assisted": "ai-assisted"}
#: Wording guard: PyPI and npm are said to go quiet more often than these four.
GUARD_QUIET = ("pypi", "npm")
GUARD_BUSY = ("crates", "go", "maven", "systems")

CSV_COLUMNS = ("rank", "owner", "name", "ecosystem", "score", "tier", "coverage", "quadrant")

#: ``D-GATE-5`` is a black-hole verdict only when the gate's own cutoffs say what the page says.
EXPECTED_CUTOFFS = {"response_deadline_days": 14, "max_zero_response_share": 0.5}
#: Words the D-GATE-5 criterion must contain for the page's "how we measured" sentences to be true.
CRITERION_MARKERS = (
    "external PRs by non-bot authors",
    "draft PRs are excluded",
    "self-closed",
    "accounts under",
    "95% one-sided",
)

LIMITS = (
    "Not a verdict on the people who maintain these projects. Review time is scarce, and popular projects "
    "receive far more pull requests than anyone can read.",
    "GitHub only. Projects that review on Gerrit or a mailing list are left undecided, not failed.",
    "One six-month window. The numbers will change at the next snapshot.",
)


class ReportError(Exception):
    """A number does not agree with its source, or a guarded sentence would be false."""


# --------------------------------------------------------------------------- #
# Number formatting
# --------------------------------------------------------------------------- #
def fmt_int(value: int) -> str:
    return f"{value:,}"


def _percent(numerator: int, denominator: int, places: str) -> str:
    value = (Decimal(numerator) * 100 / Decimal(denominator)).quantize(Decimal(places), rounding=ROUND_HALF_UP)
    return str(value)


def pct1(numerator: int, denominator: int) -> str:
    """One decimal, half up (26.4 for 1,133 of 4,297)."""
    return _percent(numerator, denominator, "0.1")


def pct0(numerator: int, denominator: int) -> str:
    """A whole number, half up (21 for 467 of 2,190)."""
    return _percent(numerator, denominator, "1")


def rho_text(rho: float) -> str:
    return str(Decimal(str(rho)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def long_date(day: date) -> str:
    return f"{day.day} {day:%B %Y}"


def _band_bounds(key: str) -> tuple[int, int | None]:
    match = re.fullmatch(r"\[(\d+),(\d+|inf)\)", key)
    if not match:
        raise ReportError(f"star band {key!r} is not of the form [low,high)")
    return int(match.group(1)), (None if match.group(2) == "inf" else int(match.group(2)))


def band_range_text(key: str) -> str:
    """``[1000,10000)`` -> ``1,000 to 10,000``; ``[50000,inf)`` -> ``50,000 or more``."""
    low, high = _band_bounds(key)
    return f"{fmt_int(low)} or more" if high is None else f"{fmt_int(low)} to {fmt_int(high)}"


# --------------------------------------------------------------------------- #
# Facts: every number, from its source
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class EcoRow:
    key: str
    name: str
    judged: int
    black_holes: int


@dataclass(frozen=True)
class BandRow:
    key: str
    label: str  # "1,000 to 10,000"
    judged: int
    black_holes: int


@dataclass(frozen=True)
class Facts:
    snapshot_id: str
    snapshot_date: date
    benchmark_version: str
    constants_hash: str
    manifest_root: str
    canonical_dump_hash: str
    code_commit: str
    reproduce_command: str
    judged: int
    black_holes: int
    ecosystems: tuple[EcoRow, ...]  # highest share first
    bands: tuple[BandRow, ...]
    below_bands_judged: int  # judged projects under the lowest charted band (not charted)
    lowest_band_text: str
    ranked: int
    unanswered: int
    counted: int
    window_start: date
    window_end: date
    rho: float
    top100_overlap: int
    top100_black_holes: int
    corpus_prs: int
    corpus_authors: int
    corpus_projects: int
    #: Projects judged out for when their data was fetched: their PR records lack the fields the rules read.
    undecided_rule_inputs: int
    min_n: int
    deadline_days: int
    ci_percent: int

    @property
    def share_text(self) -> str:
        return pct1(self.black_holes, self.judged)

    @property
    def one_in(self) -> int:
        """The ``N`` of "more than 1 in N": the smallest whole number with ``N * black_holes > judged``, so the
        claim is true and no smaller ``N`` would be."""
        return self.judged // self.black_holes + 1

    @property
    def window_text(self) -> str:
        """``16 April`` / ``13 October 2026``, as the measurement bullet prints them."""
        return f"{self.window_start.day} {self.window_start:%B}", long_date(self.window_end)

    @property
    def month_year(self) -> str:
        return f"{self.snapshot_date:%B %Y}"

    @property
    def csv_name(self) -> str:
        return f"porchlamp-{self.snapshot_id}-ranked.csv"


def decided_counts(rows: Iterable[tuple]) -> dict[str, tuple[int, int]]:
    """``{ecosystem: (judged, black_holes)}`` from ``(ecosystem, gate_reason, gate_undecided)`` rows.

    Judged = D-GATE-5 decided: ``gate_reason`` is NULL, ``black_hole`` or
    ``unrated: no_external_workflow``, and ``D-GATE-5`` is not among the
    undecided gates (the definition :func:`porchlamp.snapshot.stats.compute_stats` uses)."""
    out: dict[str, list[int]] = {}
    for ecosystem, gate_reason, gate_undecided in rows:
        if gate_reason not in (None, "black_hole", "unrated: no_external_workflow"):
            continue
        undecided = json.loads(gate_undecided) if gate_undecided else []
        if "D-GATE-5" in undecided:
            continue
        counts = out.setdefault(ecosystem, [0, 0])
        counts[0] += 1
        counts[1] += gate_reason == "black_hole"
    return {eco: (c[0], c[1]) for eco, c in out.items()}


def gate_by_id(methodology: Mapping[str, Any], gate_id: str) -> Mapping[str, Any]:
    for gate in methodology["gates"]:
        if gate["id"] == gate_id:
            return gate
    raise ReportError(f"methodology.json has no gate {gate_id}")


def check_method(methodology: Mapping[str, Any]) -> tuple[int, int, int]:
    """The "how we measured" sentences against the gate's own criterion and cutoffs.

    Returns ``(min_n, deadline_days, confidence_percent)``. Raises when the gate no longer
    says what the page says ("more than half ... within 14 days", "95% confidence", the
    exclusions), so the page is never published over a changed rule."""
    gate = gate_by_id(methodology, "D-GATE-5")
    cutoffs = gate["cutoffs"]
    for key, expected in EXPECTED_CUTOFFS.items():
        if cutoffs.get(key) != expected:
            raise ReportError(
                f"D-GATE-5 cutoff {key} is {cutoffs.get(key)!r}, but the page says {expected!r}; "
                "the plain-English sentences need a ruling"
            )
    missing = [marker for marker in CRITERION_MARKERS if marker not in gate["criterion"]]
    if missing:
        raise ReportError(f"the D-GATE-5 criterion no longer contains {missing}; the page's method sentences need a ruling")
    match = re.search(r"z = ([0-9.]+)", gate["criterion"])
    if not match:
        raise ReportError("the D-GATE-5 criterion names no z value")
    confidence = NormalDist().cdf(float(match.group(1)))
    if abs(confidence - 0.95) > 1e-6:
        raise ReportError(f"D-GATE-5 z = {match.group(1)} is a {confidence:.4f} one-sided bound, not 95%")
    return int(cutoffs["min_n"]), int(cutoffs["response_deadline_days"]), 95


def _iso_date(text: str, what: str) -> date:
    try:
        return datetime.fromisoformat(text).date()
    except (TypeError, ValueError) as error:
        raise ReportError(f"{what} {text!r} is not an ISO date") from error


def check_stats_against_db(
    stats: Mapping[str, Any], snapshot_id: str, db_rows: Iterable[tuple], db_rated: int
) -> dict[str, tuple[int, int]]:
    """The stats file is for this snapshot, and its decided / black-hole / rated counts are the database's.
    Returns the per-ecosystem ``(judged, black_holes)``."""
    if stats.get("snapshot_id") != snapshot_id:
        raise ReportError(f"the stats file is for snapshot {stats.get('snapshot_id')!r}, the database is {snapshot_id!r}")
    per_eco = decided_counts(db_rows)
    judged = sum(j for j, _ in per_eco.values())
    black_holes = sum(b for _, b in per_eco.values())
    for key, db_value in (("gate5_decided", judged), ("black_holes", black_holes), ("rated", db_rated)):
        if stats.get(key) != db_value:
            raise ReportError(f"the stats file says {key} = {stats.get(key)!r}, the database gives {db_value!r}")
    return per_eco


def build_facts(
    *,
    snapshot: Mapping[str, Any],
    methodology: Mapping[str, Any],
    stats: Mapping[str, Any],
    db_rows: Iterable[tuple],
    db_rated: int,
    api_ranked: int,
    api_black_holes: int,
    evidence: Sequence[Mapping[str, Any]],
) -> Facts:
    """Every number the pages print, each from its source, cross-checked, with the wording guards.

    ``db_rows`` are ``(ecosystem, gate_reason, gate_undecided)`` rows of the snapshot database;
    ``evidence`` is the ``black_hole_evidence`` of every black-hole project's API record."""
    snapshot_id = snapshot["snapshot_id"]
    db_rows = list(db_rows)
    per_eco = check_stats_against_db(stats, snapshot_id, db_rows, db_rated)
    judged = sum(j for j, _ in per_eco.values())
    black_holes = sum(b for _, b in per_eco.values())
    if api_ranked != db_rated:
        raise ReportError(f"the API ranks {api_ranked} projects, the database has {db_rated} rated")
    if snapshot["counts"]["rated"] != api_ranked:
        raise ReportError("the snapshot summary's rated count differs from the leaderboard's total")
    if api_black_holes != black_holes:
        raise ReportError(f"the API holds {api_black_holes} black-hole records, the database counts {black_holes}")
    unknown = sorted(set(per_eco) - set(ECOSYSTEM_NAMES))
    if unknown:
        raise ReportError(f"ecosystems without a display name: {unknown}")

    min_n, deadline_days, ci_percent = check_method(methodology)

    # Outside pull requests inside the black holes, and the window they were counted in.
    if len(evidence) != black_holes:
        raise ReportError(
            f"{len(evidence)} of {black_holes} black-hole records carry evidence; pass the export --snapshot-dir and "
            "--data-dir (the stored snapshot and the data root holding raw/pr_window) so the API can serve it"
        )
    unanswered = sum(e["unanswered"] for e in evidence)
    counted = sum(e["population_n"] for e in evidence)
    windows = {(e["window_start"], e["window_end"]) for e in evidence}
    if len(windows) != 1:
        raise ReportError(f"black-hole evidence names {len(windows)} different windows")
    window_start, window_end = (_iso_date(text, "window") for text in next(iter(windows)))

    by_stars = stats["by_stars"]
    missing_bands = [key for key in STAR_BANDS if key not in by_stars]
    if missing_bands:
        raise ReportError(f"the stats file has no star band {missing_bands}")
    if sum(b["n"] for b in by_stars.values()) != judged or sum(b["black_holes"] for b in by_stars.values()) != black_holes:
        raise ReportError("the stats file's star bands do not add up to the decided / black-hole totals")
    bands = []
    for key in STAR_BANDS:
        band = by_stars[key]
        if pct1(band["black_holes"], band["n"]) != _percent_of_share(band["share"]):
            raise ReportError(f"star band {key}: {band['black_holes']}/{band['n']} disagrees with its stored share {band['share']}")
        bands.append(BandRow(key, band_range_text(key), band["n"], band["black_holes"]))
    lowest = min(_band_bounds(key)[0] for key in STAR_BANDS)
    below = sum(b["n"] for key, b in by_stars.items() if _band_bounds(key)[0] < lowest)

    ecosystems = sorted(
        (EcoRow(key, ECOSYSTEM_NAMES[key], j, b) for key, (j, b) in per_eco.items()),
        key=lambda row: (-Decimal(row.black_holes) / Decimal(row.judged), row.key),
    )

    corpus = stats["corpus"]
    if corpus["derived_repos"] != snapshot["counts"]["total"]:
        raise ReportError("the stats file's corpus size differs from the snapshot's project count")
    if not isinstance(stats.get("gate5_undecided_rule_inputs"), int):
        raise ReportError("the stats file has no gate5_undecided_rule_inputs: rebuild it with `porchlamp.snapshot stats`")

    facts = Facts(
        snapshot_id=snapshot_id,
        snapshot_date=_iso_date(snapshot_id, "snapshot id"),
        benchmark_version=snapshot["benchmark_version"],
        constants_hash=snapshot["constants_hash"],
        manifest_root=snapshot["manifest_root"],
        canonical_dump_hash=snapshot["canonical_dump_hash"],
        code_commit=snapshot["code_commit"],
        reproduce_command=snapshot["reproduce_command"],
        judged=judged,
        black_holes=black_holes,
        ecosystems=tuple(ecosystems),
        bands=tuple(bands),
        below_bands_judged=below,
        lowest_band_text=fmt_int(lowest),
        ranked=api_ranked,
        unanswered=unanswered,
        counted=counted,
        window_start=window_start,
        window_end=window_end,
        rho=stats["spearman"]["porchlamp"]["rho"],
        top100_overlap=stats["top100_overlap"],
        top100_black_holes=stats["top100_stars_fate"]["black_hole"],
        corpus_prs=corpus["human_outside_prs"],
        corpus_authors=corpus["distinct_outside_authors"],
        corpus_projects=corpus["derived_repos"],
        undecided_rule_inputs=stats["gate5_undecided_rule_inputs"],
        min_n=min_n,
        deadline_days=deadline_days,
        ci_percent=ci_percent,
    )
    check_wording_guards(facts)
    return facts


def _percent_of_share(share: float) -> str:
    return str((Decimal(str(share)) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def check_wording_guards(facts: Facts) -> None:
    """Sentences that are true only for certain numbers. A guard that fails stops the export."""
    if not facts.black_holes > 0:
        raise ReportError("guard: 'more than 1 in N' needs at least one black hole")
    shares = {row.key: Decimal(row.black_holes) / Decimal(row.judged) for row in facts.ecosystems}
    for quiet in GUARD_QUIET:
        for busy in GUARD_BUSY:
            if quiet not in shares or busy not in shares or not shares[quiet] > shares[busy]:
                raise ReportError(
                    f"guard: 'PyPI and npm go quiet more often than Rust, Go, Java or systems projects' is false: "
                    f"{quiet} {float(shares.get(quiet, 0)):.4f} is not above {busy} {float(shares.get(busy, 0)):.4f}"
                )
    band_shares = [Decimal(b.black_holes) / Decimal(b.judged) for b in facts.bands]
    if not all(a < b for a, b in zip(band_shares, band_shares[1:])):
        raise ReportError(
            "guard: 'the black-hole share climbs at every step up in stars' is false: band shares are "
            + ", ".join(f"{float(s):.4f}" for s in band_shares)
        )
    if not facts.unanswered * 2 > facts.counted:
        raise ReportError(
            f"guard: 'Most of those pull requests waited' needs more than half, got {facts.unanswered} of {facts.counted}"
        )


# --------------------------------------------------------------------------- #
# The CSV: ranked projects only
# --------------------------------------------------------------------------- #
def ranked_csv(boards: Mapping[str, Mapping[str, Any]], lenses: Sequence[str], balanced: str = "balanced") -> str:
    """One row per ranked project of the balanced board, in its order, then each lens's rank (empty when
    the project is not ranked in that lens). Black holes and every other unranked project are not listed."""
    lens_rank = {
        lens: {(r["owner"], r["name"]): r["rank"] for r in boards[lens]["rows"]} for lens in lenses
    }
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(list(CSV_COLUMNS) + [f"rank_{lens}" for lens in lenses])
    for row in boards[balanced]["rows"]:
        key = (row["owner"], row["name"])
        writer.writerow(
            [row["rank"], row["owner"], row["name"], row["ecosystem"], row["porchlamp"], row["tier"] or "",
             row["coverage"], row["quadrant"] or ""]
            + [lens_rank[lens].get(key, "") for lens in lenses]
        )
    return out.getvalue()


# --------------------------------------------------------------------------- #
# The in-app copy, reused: copy.js is the single source of the names
# --------------------------------------------------------------------------- #
def copy_js_map(name: str, web_dir: Path = WEB_APP) -> dict[str, str]:
    """``[export ]const NAME = Object.freeze({ key: "value", ... })`` from ``web/app/copy.js``."""
    text = (web_dir / "copy.js").read_text(encoding="utf-8")
    match = re.search(rf"(?:export )?const {name} = Object\.freeze\(\{{(.*?)\}}\);", text, re.S)
    if not match:
        raise ReportError(f"web/app/copy.js has no `const {name} = Object.freeze({{...}})`")
    pairs = re.findall(r'(\w+):\s*"((?:[^"\\]|\\.)*)"', match.group(1))
    if not pairs:
        raise ReportError(f"copy.js {name} holds no strings")
    return {key: json.loads(f'"{value}"') for key, value in pairs}


def copy_js_string(name: str, web_dir: Path = WEB_APP) -> str:
    """``[export ]const NAME = "text";`` (the string may start on the next line) from ``web/app/copy.js``."""
    text = (web_dir / "copy.js").read_text(encoding="utf-8")
    match = re.search(rf'(?:export )?const {name} =\s*"((?:[^"\\]|\\.)*)";', text)
    if not match:
        raise ReportError(f"web/app/copy.js has no `const {name} = \"...\";`")
    return json.loads(f'"{match.group(1)}"')


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #
def _t(text: str) -> str:
    return escape(text, quote=False)


def _a(text: str) -> str:
    return escape(text, quote=True)


def _ld(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def page(
    *, title: str, description: str, canonical: str, rel: str, body: str, ld: Any, nav_current: str, scripts: str = ""
) -> str:
    """One page of the site. ``rel`` is the path back to the site root ("../", "../../").
    ``scripts`` is markup placed after the page body (the /check/ page's one local script); none by default."""
    current = ' aria-current="page"'
    nav = "".join(
        f'<a href="{_a(rel + href)}"{current if key == nav_current else ""}>{_t(label)}</a>'
        for key, href, label in (
            ("rankings", "", "Porchlamp rankings"),
            ("report", "black-holes/", "Report"),
            ("methodology", "methodology/", "Methodology"),
        )
    )
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n'
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"  <title>{_t(title)}</title>\n"
        f'  <meta name="description" content="{_a(description)}">\n'
        '  <meta name="author" content="Rahul Kumar (pctablet505)">\n'
        '  <meta name="theme-color" content="#0f1419">\n'
        f'  <link rel="icon" href="{rel}favicon.svg" type="image/svg+xml">\n'
        f'  <link rel="canonical" href="{_a(canonical)}">\n'
        '  <meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1">\n'
        '  <meta property="og:type" content="article">\n'
        '  <meta property="og:locale" content="en_US">\n'
        '  <meta property="og:site_name" content="Porchlamp">\n'
        f'  <meta property="og:title" content="{_a(title)}">\n'
        f'  <meta property="og:description" content="{_a(description)}">\n'
        f'  <meta property="og:url" content="{_a(canonical)}">\n'
        f'  <meta property="og:image" content="{SITE}/og.png">\n'
        '  <meta name="twitter:card" content="summary_large_image">\n'
        f'  <meta name="twitter:title" content="{_a(title)}">\n'
        f'  <meta name="twitter:description" content="{_a(description)}">\n'
        f'  <meta name="twitter:image" content="{SITE}/og.png">\n'
        f'  <link rel="stylesheet" href="{rel}styles/tokens.css">\n'
        f'  <link rel="stylesheet" href="{rel}styles/base.css">\n'
        f'  <link rel="stylesheet" href="{rel}styles/report.css">\n'
        f'  <script type="application/ld+json">\n  {_ld(ld)}\n  </script>\n'
        "</head>\n<body>\n"
        '  <a class="skip-link" href="#report-main">Skip to main content</a>\n'
        '  <div class="app-shell">\n'
        f'    <nav class="report-nav" aria-label="Porchlamp"><img src="{rel}favicon.svg" width="28" height="28" alt="">{nav}</nav>\n'
        '    <main class="app-main" id="report-main" tabindex="-1">\n'
        f"{body}\n"
        "    </main>\n"
        "  </div>\n"
        f"{scripts}"
        "</body>\n</html>\n"
    )


def _site_links(rel: str) -> str:
    return (
        '<footer class="report-footer"><p>'
        f'<a href="{rel}">Porchlamp rankings</a> · '
        f'<a href="{rel}methodology/">Methodology</a> · '
        f'<a href="{GITHUB_REPO}">Data on GitHub</a>'
        "</p></footer>"
    )


def _table(
    headers: Sequence[str], rows: Sequence[Sequence[str]], *, caption: str | None = None,
    first_links: Sequence[str] | None = None,
) -> str:
    """``first_links``: one address per row; the row's first cell then links to it."""
    head = "".join(f'<th scope="col">{_t(h)}</th>' for h in headers)

    def first_cell(i: int, text: str) -> str:
        inner = _t(text) if first_links is None else f'<a href="{_a(first_links[i])}">{_t(text)}</a>'
        return f'<th scope="row">{inner}</th>'

    body = "".join(
        f"<tr>{first_cell(i, r[0])}" + "".join(f"<td>{_t(c)}</td>" for c in r[1:]) + "</tr>" for i, r in enumerate(rows)
    )
    cap = f"<caption>{_t(caption)}</caption>" if caption else ""
    return f'<div class="seo-fallback-table-wrap"><table>{cap}<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _bar_chart(rows: Sequence[tuple[str, str, Decimal, str]], summary: str, ident: str) -> str:
    """A horizontal bar chart as inline SVG. ``rows`` are ``(label, detail, share, value_text)``;
    bars start at zero and are scaled to the largest share. Text sits above each bar so it stays
    readable when the chart shrinks to a phone screen (viewBox 360 wide)."""
    width, left, bar_max, row_h = 360, 0, 268, 58
    top = max(share for _, _, share, _ in rows)
    parts = []
    for i, (label, detail, share, value) in enumerate(rows):
        y = i * row_h
        length = max(2, int(bar_max * share / top))
        parts.append(
            f'<text class="chart-label" x="{left}" y="{y + 18}">{_t(label)}</text>'
            f'<text class="chart-n" x="{width}" y="{y + 18}" text-anchor="end">{_t(detail)}</text>'
            f'<rect class="chart-bar" x="{left}" y="{y + 26}" width="{length}" height="20" rx="3"/>'
            f'<text class="chart-value" x="{left + length + 8}" y="{y + 42}">{_t(value)}</text>'
        )
    height = len(rows) * row_h
    return (
        f'<svg class="chart" id="{ident}" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{_a(summary)}"><title>{_t(summary)}</title>{"".join(parts)}</svg>'
    )


def undecided_bullet(f: Facts) -> str:
    """The measurement bullet that says how many projects were left undecided for when their data was fetched
    (with its line break), or nothing when none was."""
    if not f.undecided_rule_inputs:
        return ""
    return (
        f"          <li>Another {fmt_int(f.undecided_rule_inputs)} licensed, maintained projects with at least {f.min_n} "
        "outside pull requests on record are not judged yet: those records were fetched before drafts and closes "
        "were recorded, and the rules need both.</li>\n"
    )


def finding_paragraphs(f: Facts) -> list[tuple[str, str, str]]:
    """``(id, bold lead, rest)`` of the five findings."""
    b1, b2, b3 = f.bands
    return [
        (
            "finding-share",
            f"More than 1 in {f.one_in}.",
            f"In Porchlamp's {f.snapshot_id} snapshot, {fmt_int(f.black_holes)} of {fmt_int(f.judged)} active "
            f"open-source projects ({f.share_text}%) left most outside pull requests with no human reply, review or "
            f"merge within {f.deadline_days} days.",
        ),
        (
            "finding-stars",
            "More stars, more silence.",
            f"The share rises with stars: {pct0(b1.black_holes, b1.judged)}% of projects with {b1.label} stars, "
            f"{pct0(b2.black_holes, b2.judged)}% at {b2.label}, and {pct0(b3.black_holes, b3.judged)}% at {b3.label}.",
        ),
        (
            "finding-top100",
            "Fame is no guarantee.",
            f"{f.top100_black_holes} of the 100 most-starred projects are black holes, and only {f.top100_overlap} "
            "of them reach Porchlamp's top 100.",
        ),
        (
            "finding-correlation",
            "Stars barely predict an answer.",
            f"Across {fmt_int(f.ranked)} ranked projects, the rank correlation between stars and the Porchlamp "
            f"score is {rho_text(f.rho)}.",
        ),
        (
            "finding-prs",
            "Most of those pull requests waited.",
            f"Inside the black-hole projects, {fmt_int(f.unanswered)} of {fmt_int(f.counted)} counted outside pull "
            f"requests got no human reply within {f.deadline_days} days.",
        ),
    ]


def band_chart_summary(f: Facts) -> str:
    parts = [
        f"{pct0(b.black_holes, b.judged)}% of {fmt_int(b.judged)} projects with {b.label} stars" for b in f.bands
    ]
    return "Black-hole share by stars: " + "; ".join(parts) + "."


def eco_chart_summary(f: Facts) -> str:
    parts = [f"{e.name} {pct1(e.black_holes, e.judged)}% of {fmt_int(e.judged)}" for e in f.ecosystems]
    return "Black-hole share by package ecosystem, highest first: " + "; ".join(parts) + "."


def report_title(f: Facts, *, frozen: bool = False) -> str:
    """The latest report names the month; the frozen copy names its snapshot, so the two titles differ."""
    when = f"snapshot {f.snapshot_id}" if frozen else f.month_year
    return f"Open-source black holes: how many projects ignore outside pull requests ({when}) | Porchlamp"


def report_description(f: Facts) -> str:
    return (
        f"{f.share_text}% of active open-source projects left most outside pull requests unanswered for two weeks. "
        f"The share rises with stars. Data on {fmt_int(f.corpus_projects)} GitHub projects."
    )


def report_urls(f: Facts) -> tuple[str, str, str]:
    """``(latest address, frozen address, CSV address)``."""
    latest = f"{SITE}/black-holes/"
    frozen = f"{latest}{f.snapshot_id}/"
    return latest, frozen, f"{frozen}{f.csv_name}"


def report_jsonld(f: Facts, *, canonical: str, exported_on: date) -> dict:
    latest, frozen, csv_url = report_urls(f)
    start, end = f.window_start.isoformat(), f.window_end.isoformat()
    b1, b2, b3 = f.bands
    description = (
        f"{fmt_int(f.black_holes)} of {fmt_int(f.judged)} active open-source projects ({f.share_text}%) left most "
        f"outside pull requests with no human reply, review or merge within {f.deadline_days} days, in Porchlamp's "
        f"{f.snapshot_id} snapshot. The share rises with stars: {pct0(b1.black_holes, b1.judged)}% at {b1.label} "
        f"stars, {pct0(b2.black_holes, b2.judged)}% at {b2.label}, {pct0(b3.black_holes, b3.judged)}% at {b3.label}. "
        f"Includes the {fmt_int(f.ranked)} ranked projects with rank, score, tier, coverage and quadrant."
    )
    dataset_id = f"{canonical}#dataset"
    return {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Person", "@id": CREATOR_ID, "name": "Rahul Kumar", "alternateName": "pctablet505",
                "url": "https://github.com/pctablet505",
            },
            {
                "@type": "Article",
                "@id": f"{canonical}#article",
                "headline": REPORT_H1,
                "description": report_description(f),
                "url": canonical,
                "mainEntityOfPage": canonical,
                "datePublished": exported_on.isoformat(),
                "dateModified": exported_on.isoformat(),
                "inLanguage": "en",
                "author": {"@id": CREATOR_ID},
                "about": {"@id": dataset_id},
            },
            {
                "@type": "Dataset",
                "@id": dataset_id,
                "name": f"Porchlamp open-source black holes: ranked projects (snapshot {f.snapshot_id})",
                "description": description,
                "url": canonical,
                "version": f"{f.snapshot_id} ({f.benchmark_version})",
                "isAccessibleForFree": True,
                "license": CC_BY_4,
                "temporalCoverage": f"{start}/{end}",
                "measurementTechnique": (
                    f"Outside pull requests followed on GitHub; a project is a black hole when, with {f.ci_percent}% "
                    f"confidence, more than half of its counted pull requests got no substantive reply, review or "
                    f"merge within {f.deadline_days} days (methodology at {SITE}/methodology/)."
                ),
                "creator": {"@id": CREATOR_ID},
                "distribution": [
                    {
                        "@type": "DataDownload", "encodingFormat": "text/csv", "contentUrl": csv_url,
                        "name": f.csv_name,
                    }
                ],
            },
        ],
    }


def render_report(f: Facts, *, frozen: bool, exported_on: date, source_db_sha256: str) -> str:
    """The report page: the latest address (``frozen=False``) or the citation copy (``frozen=True``)."""
    latest, frozen_url, _ = report_urls(f)
    canonical = frozen_url if frozen else latest
    rel = "../../" if frozen else "../"
    csv_href = f.csv_name if frozen else f"{f.snapshot_id}/{f.csv_name}"
    start_text, end_text = f.window_text

    findings = "\n".join(
        f'        <p class="finding" id="{ident}"><strong>{_t(lead)}</strong> {_t(rest)} '
        f'<a class="anchor" href="#{ident}" aria-label="Link to this finding">#</a></p>'
        for ident, lead, rest in finding_paragraphs(f)
    )
    band_rows = [
        (f"{b.label} stars", fmt_int(b.judged), fmt_int(b.black_holes), f"{pct0(b.black_holes, b.judged)}%")
        for b in f.bands
    ]
    band_note = ""
    if f.below_bands_judged:
        noun = "project" if f.below_bands_judged == 1 else "projects"
        band_note = (
            f'<p class="note">Not charted: {fmt_int(f.below_bands_judged)} judged {noun} with fewer than '
            f"{f.lowest_band_text} stars.</p>"
        )
    eco_rows = [
        (e.name, fmt_int(e.judged), fmt_int(e.black_holes), f"{pct1(e.black_holes, e.judged)}%") for e in f.ecosystems
    ]
    band_chart = _bar_chart(
        [
            (f"{b.label} stars", f"{fmt_int(b.judged)} projects", Decimal(b.black_holes) / Decimal(b.judged),
             f"{pct0(b.black_holes, b.judged)}%")
            for b in f.bands
        ],
        band_chart_summary(f), "chart-stars",
    )
    eco_chart = _bar_chart(
        [
            (e.name, f"{fmt_int(e.judged)} projects", Decimal(e.black_holes) / Decimal(e.judged),
             f"{pct1(e.black_holes, e.judged)}%")
            for e in f.ecosystems
        ],
        eco_chart_summary(f), "chart-ecosystems",
    )

    body = f"""      <article class="seo-fallback report">
        <h1>{_t(REPORT_H1)}</h1>
        <p class="lede">The state of open-source black holes: how often a stranger's pull request goes unanswered, in the Porchlamp snapshot of {_t(long_date(f.snapshot_date))}.</p>
        <p>You send a small fix to a project you use every day. Two weeks later, nobody has replied. At more than 1 in {f.one_in} active open-source projects, that is the normal experience.</p>
        <h2 id="key-findings">Key findings</h2>
{findings}
        <h2 id="stars">Do popular projects answer fewer pull requests?</h2>
        <p>They answer less often, on average. The black-hole share climbs at every step up in stars.</p>
        {band_chart}
        <details><summary>The numbers behind the chart</summary>
        {_table(("Stars", "Projects judged", "Black holes", "Share"), band_rows)}
        {band_note}
        </details>
        <p>One likely reason: review time stays roughly fixed while attention grows, so the queue grows faster than volunteers can clear it. The data shows the rise, not its cause.</p>
        <h2 id="ecosystems">Which package ecosystems answer outside pull requests?</h2>
        <p>PyPI and npm projects go quiet more often than Rust, Go, Java or systems projects. Each share is computed within its own ecosystem.</p>
        {eco_chart}
        <details><summary>The numbers behind the chart</summary>
        {_table(("Ecosystem", "Projects judged", "Black holes", "Share"), eco_rows, first_links=[f"{rel}{ECOSYSTEM_SLUGS[e.key]}/" for e in f.ecosystems])}
        </details>
        <p>Size is not controlled for. Bigger projects go quiet more often in general, so part of a gap can come from size.</p>
        <h2 id="how-we-measured">How we measured</h2>
        <ul>
          <li>We followed {fmt_int(f.corpus_prs)} pull requests from {fmt_int(f.corpus_authors)} outside contributors to {fmt_int(f.corpus_projects)} GitHub projects, from {_t(start_text)} to {_t(end_text)}.</li>
          <li>A project is judged only when it is licensed, maintained software with at least {f.min_n} counted outside pull requests.</li>
{undecided_bullet(f)}          <li>It is a black hole when, with {f.ci_percent}% confidence, more than half of those pull requests got no substantive reply, review or merge within {f.deadline_days} days.</li>
          <li>Bots, drafts, pull requests their authors closed, brand-new accounts and the project's own members are not counted.</li>
          <li>Every parameter, gate and weight: <a href="{rel}methodology/">Porchlamp methodology</a>.</li>
        </ul>
        <h2 id="what-this-is-not">What this is not</h2>
        <ul>
{"".join(f"          <li>{_t(item)}</li>{chr(10)}" for item in LIMITS)}          <li>This page names no project. Each project's record, with the pull requests behind it, is on <a href="{rel}">the Porchlamp rankings</a>.</li>
        </ul>
        <h2 id="download">Download the data</h2>
        <p><a href="{_a(csv_href)}">{_t(f.csv_name)}</a>: the {fmt_int(f.ranked)} ranked projects with rank, owner, name, ecosystem, score, tier, coverage, quadrant and the rank in each lens. Released under CC BY 4.0.</p>
        <p>The full records, including every project that was not ranked and why, are in the site's JSON data.</p>
        <h2 id="cite">Cite this report</h2>
        <p class="cite">Porchlamp ({f.snapshot_date.year}). {_t(CITE_TITLE)}. Snapshot {_t(f.snapshot_id)}. {_t(frozen_url)}</p>
        <p class="hashes">Data licence: CC BY 4.0. Snapshot database SHA-256: <code>{_t(source_db_sha256)}</code>. Methodology constants hash: <code>{_t(f.constants_hash)}</code>.</p>
        {_site_links(rel)}
      </article>"""
    return page(
        title=report_title(f, frozen=frozen), description=report_description(f), canonical=canonical, rel=rel, body=body,
        ld=report_jsonld(f, canonical=canonical, exported_on=exported_on), nav_current="report",
    )
