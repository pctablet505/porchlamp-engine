"""The report's data and its pages' heads (``scripts/export_static.py --stats``).

The report is the app's own view (``web/app/views/report.js``) over ``data/<snapshot_id>/report.json``, which
:func:`report_json` writes from :class:`Facts`: ``/black-holes/`` (the latest snapshot's report, the app's page) and
``/black-holes/<snapshot_id>/`` (the same report, frozen for citations), drawn into their pages at export by
``web/prerender.mjs``, plus one CSV of the ranked projects. Python keeps the numbers, the sentences the numbers decide
(each worded beside the guard that keeps it true), and the pages' heads (:func:`report_page`). Nothing here is typed
by hand:

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
sentence is never written.

Every page of the site is the app's page (``web/app/404.html``: one policy, one head, one frame) with its own head
and text (:func:`app_page`); ``web/prerender.mjs`` then draws the one header, nav and footer into it
(``web/app/components/chrome.js``). The scripts a page may load are :data:`PAGE_SCRIPTS`; no font or library
is loaded by any page.
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
#: The app's page with no text of its own, which every page of the site is built from (:func:`app_page`).
APP_PAGE = WEB_APP / "404.html"
#: Every script a page loads, by kind of page, in order: theme-init.js in ``<head>`` on every one (the reader's
#: theme before the first paint, and the header's toggle), then the app on its own pages, and at most one small
#: script of its own on a page the app does not draw. These pages used to load no script at all (ENG-07).
PAGE_SCRIPTS = {
    "app": ("/theme-init.js", "/app.js"),
    "page": ("/theme-init.js",),
    "project": ("/theme-init.js", "/back-forward.js"),
}

CITE_TITLE = "Stars don't review pull requests: the state of open-source black holes"

#: The star bands the report charts: the stats producer's bands from 1,000 stars up (their keys in the stats file).
STAR_BANDS = tuple(bucket_label(lo, hi) for lo, hi in STAR_BUCKETS if lo >= 1000)
#: Ecosystem key -> the address of its page (an ecosystem preset of ``web/app/routes.js``); lens key -> likewise.
ECOSYSTEM_SLUGS = {"pypi": "python", "npm": "javascript", "go": "go", "systems": "c-cpp", "crates": "rust", "maven": "java"}
LENS_SLUGS = {"first_timer": "first-timers", "career_capital": "career", "senior_systems": "senior-systems", "ai_assisted": "ai-assisted"}
#: Wording guard: Python and JavaScript are said to go quiet more often than these four.
GUARD_QUIET = ("pypi", "npm")
GUARD_BUSY = ("crates", "go", "maven", "systems")
#: Wording guard: "Stars barely predict an answer" holds while the rank correlation between stars and the score is very
#: weak, whichever its sign: under 0.2 (Evans' scale: 0.00-0.19 very weak, 0.20-0.39 weak; lead ruling, 2026-10-17).
RHO_BARELY = Decimal("0.2")

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
    """Two places, half up; a value that rounds to zero is "0.00", never "-0.00"."""
    rounded = Decimal(str(rho)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return str(abs(rounded)) if rounded == 0 else str(rounded)


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
    #: Projects not judged because their PR record stops at the collector's cap before the window starts.
    undecided_cut: int = 0
    #: The cap those records stopped at, as their envelopes record it; None when no record is cut.
    pr_cap: int | None = None
    #: Projects whose PR window holds more PRs than the collector records with every field: replies and reviews
    #: are read on an even random sample of them (RFC-0013).
    sampled: int = 0
    #: The PRs a window may hold with every field recorded, and the sample's size, as those envelopes record them;
    #: None when no record is sampled.
    full_max: int | None = None
    sample_size: int | None = None

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
    if not isinstance(stats.get("gate5_undecided_cut_record"), int):
        raise ReportError("the stats file has no gate5_undecided_cut_record: rebuild it with `porchlamp.snapshot stats`")
    undecided_cut, pr_cap = stats["gate5_undecided_cut_record"], None
    if undecided_cut:
        caps = stats.get("gate5_undecided_cut_record_caps")
        if not (isinstance(caps, list) and len(caps) == 1 and type(caps[0]) is int and caps[0] > 0):
            raise ReportError(
                "guard: 'we hold only the newest N pull requests of each' needs every cut record stopped at one "
                f"recorded cap; the stats file's gate5_undecided_cut_record_caps is {caps!r}"
            )
        pr_cap = caps[0]
    if not isinstance(stats.get("pr_window_sampled"), int):
        raise ReportError("the stats file has no pr_window_sampled: rebuild it with `porchlamp.snapshot stats`")
    sampled, full_max, sample_size = stats["pr_window_sampled"], None, None
    if sampled:
        pairs = stats.get("pr_window_sampled_caps")
        if not (isinstance(pairs, list) and len(pairs) == 1 and isinstance(pairs[0], list) and len(pairs[0]) == 2
                and all(type(n) is int and n > 0 for n in pairs[0])):
            raise ReportError(
                "guard: 'more than N pull requests ... a sample of M of them' needs every sampled record to name one cap "
                f"and one sample size; the stats file's pr_window_sampled_caps is {pairs!r}"
            )
        full_max, sample_size = pairs[0]

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
        undecided_cut=undecided_cut,
        pr_cap=pr_cap,
        sampled=sampled,
        full_max=full_max,
        sample_size=sample_size,
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
                    f"guard: '{ecosystems_answer()}' is false: "
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
    if not abs(Decimal(str(facts.rho))) < RHO_BARELY:
        raise ReportError(
            f"guard: 'Stars barely predict an answer' needs a rank correlation under {RHO_BARELY} either way, got {facts.rho}: "
            "the sentence needs new words, and new words need the owner"
        )
    if not facts.top100_black_holes > 0:
        raise ReportError("guard: 'Fame is no guarantee' needs a black hole among the 100 most-starred projects, got none")
    if len(facts.bands) != 3:
        raise ReportError(f"the stars finding names three star bands; the stats file has {len(facts.bands)}")


def ecosystems_answer() -> str:
    """The ecosystem chart's answer, in copy.js's names: the two that go quiet more often than the other four."""
    busy = [ECOSYSTEM_NAMES[key] for key in GUARD_BUSY]
    return (
        f"{' and '.join(ECOSYSTEM_NAMES[key] for key in GUARD_QUIET)} projects go quiet more often than "
        f"{', '.join(busy[:-1])} or {busy[-1]} projects."
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


def copy_js_list(name: str, web_dir: Path = WEB_APP) -> list[str]:
    """``[export ]const NAME = Object.freeze([ "text", ... ]);`` from ``web/app/copy.js``."""
    text = (web_dir / "copy.js").read_text(encoding="utf-8")
    match = re.search(rf"(?:export )?const {name} = Object\.freeze\(\[(.*?)\]\);", text, re.S)
    if not match:
        raise ReportError(f"web/app/copy.js has no `const {name} = Object.freeze([...])`")
    items = re.findall(r'"((?:[^"\\]|\\.)*)"', match.group(1))
    if not items:
        raise ReportError(f"copy.js {name} holds no strings")
    return [json.loads(f'"{item}"') for item in items]


def copy_js_string(name: str, web_dir: Path = WEB_APP) -> str:
    """``[export ]const NAME = "text";`` (the string may start on the next line) from ``web/app/copy.js``."""
    text = (web_dir / "copy.js").read_text(encoding="utf-8")
    match = re.search(rf'(?:export )?const {name} =\s*"((?:[^"\\]|\\.)*)";', text)
    if not match:
        raise ReportError(f"web/app/copy.js has no `const {name} = \"...\";`")
    return json.loads(f'"{match.group(1)}"')


#: Ecosystem key -> its name, in plain language: copy.js's one list (owner, 2026-10-17), which the app's filter uses too.
ECOSYSTEM_NAMES = copy_js_map("ECOSYSTEM_NAMES")
#: The report's title, as its view prints it (the structured data's headline).
REPORT_H1 = copy_js_string("REPORT_H1")


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


def _once(page: str, old: str, new: str) -> str:
    if page.count(old) != 1:
        raise ReportError(f"web/app/404.html has no single {old.strip()[:60]!r} to build a page from")
    return page.replace(old, new)


def app_page(
    *, title: str, head: str = "", description: str | None = None, main: str | None = None,
    stylesheet: str | None = None, stylesheets: Sequence[str] | None = None, scripts: Sequence[str] | None = None,
    template: Path = APP_PAGE,
) -> str:
    """A page of the site, built from the app's page (``web/app/404.html``: its policy, its head and its frame) with
    this ``title`` and the ``head`` tags (after the description); optionally its own ``description``, its text in
    ``<main>`` (``main``, in place of the app's), a ``stylesheet`` in place of the app views' styles, or
    ``stylesheets`` in place of all the template's, and ``scripts`` (its own module scripts, by address from the site
    root) in place of the app. The template's comments are left out. Its header, nav and footer are drawn into it at
    export (``web/prerender.mjs``)."""
    page = re.sub(r"\n *<!--.*?-->", "", template.read_text(encoding="utf-8"), flags=re.S)
    if len(re.findall(r"<title>[^<]*</title>", page)) != 1:
        raise ReportError("web/app/404.html has no single <title> to build a page from")
    page = re.sub(r"<title>[^<]*</title>", lambda _m: f"<title>{_t(title)}</title>", page)
    found = re.findall(r'<meta name="description" content="[^"]*">', page)
    if len(found) != 1:
        raise ReportError("web/app/404.html has no single description to build a page from")
    tag = found[0] if description is None else f'<meta name="description" content="{_a(description)}">'
    page = _once(page, found[0], tag + head)
    if stylesheet is not None:
        page = _once(page, '<link rel="stylesheet" href="/styles/views.css">', f'<link rel="stylesheet" href="{_a(stylesheet)}">')
    if stylesheets is not None:
        links = re.findall(r'\n *<link rel="stylesheet" href="[^"]*">', page)
        if not links:
            raise ReportError("web/app/404.html has no stylesheet to build a page from")
        own = "".join(f'\n  <link rel="stylesheet" href="{_a(href)}">' for href in stylesheets)
        page = _once(page, links[0], own)
        for link in links[1:]:
            page = _once(page, link, "")
    if main is not None:
        frame = re.search(r'(<main class="app-main" id="app-main" tabindex="-1">)(.*?)(\n *</main>)', page, re.S)
        if not frame:
            raise ReportError("web/app/404.html has no <main> to build a page from")
        page = page[: frame.start(2)] + "\n" + main + page[frame.end(2) :]
    if scripts is not None:
        own = "".join(f'  <script type="module" src="{_a(src)}"></script>\n' for src in scripts)
        page = _once(page, '  <script type="module" src="/app.js"></script>\n', own)
    return page


def _article_head(*, title: str, description: str, canonical: str) -> str:
    """The head tags of a page that a search or a link preview shows as an article: its author, canonical address,
    robots rule and link previews."""
    return (
        '\n  <meta name="author" content="Rahul Kumar (pctablet505)">'
        f'\n  <link rel="canonical" href="{_a(canonical)}">'
        '\n  <meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1">'
        '\n  <meta property="og:type" content="article">'
        '\n  <meta property="og:locale" content="en_US">'
        '\n  <meta property="og:site_name" content="Porchlamp">'
        f'\n  <meta property="og:title" content="{_a(title)}">'
        f'\n  <meta property="og:description" content="{_a(description)}">'
        f'\n  <meta property="og:url" content="{_a(canonical)}">'
        f'\n  <meta property="og:image" content="{SITE}/og.png">'
        '\n  <meta name="twitter:card" content="summary_large_image">'
        f'\n  <meta name="twitter:title" content="{_a(title)}">'
        f'\n  <meta name="twitter:description" content="{_a(description)}">'
        f'\n  <meta name="twitter:image" content="{SITE}/og.png">'
    )


def _with_ld(built: str, ld: Any) -> str:
    return _once(built, "</head>", f'  <script type="application/ld+json">\n  {_ld(ld)}\n  </script>\n</head>')


def report_title(f: Facts, *, frozen: bool = False) -> str:
    """The latest report names the year; the frozen copy names its snapshot, so the two titles differ."""
    when = f"snapshot {f.snapshot_id}" if frozen else f"{f.snapshot_date.year} data"
    return f"Open-source black holes: do projects answer pull requests? ({when}) | Porchlamp"


def report_description(f: Facts) -> str:
    return (
        f"{f.share_text}% of active open-source projects left most outside pull requests with no human reply, review, merge or close within two weeks or since. "
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
        f"outside pull requests with no human reply, review, merge or close within {f.deadline_days} days or since, in Porchlamp's "
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
                    f"confidence, more than half of its counted pull requests got no substantive reply, review, "
                    f"merge or close within {f.deadline_days} days or since (methodology at {SITE}/methodology/)."
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


# --------------------------------------------------------------------------- #
# The report's data (report.json) and its two pages
# --------------------------------------------------------------------------- #
def report_json(f: Facts) -> dict:
    """``data/<snapshot_id>/report.json``, what ``web/app/views/report.js`` draws the report from.

    ``values`` holds every printed number and name as the display string formatted here (half-up rounding stays in
    Python); the chart rows hold their own. Every sentence that carries a number, or a claim the numbers decide, is a
    template with ``{slots}`` worded here, beside the guard that keeps it true (:func:`check_wording_guards`,
    :func:`check_method`); the view fills the slots and never types a number or words a claim. Number-free words are
    copy.js's. The database digest is not here: the view reads it from the export's manifest (``api.fetchManifest``,
    the one source)."""
    _latest, frozen_url, _csv = report_urls(f)
    start_text, end_text = f.window_text
    values = {
        "snapshot_id": f.snapshot_id,
        "snapshot_date": long_date(f.snapshot_date),
        "year": str(f.snapshot_date.year),
        "one_in": fmt_int(f.one_in),
        "black_holes": fmt_int(f.black_holes),
        "judged": fmt_int(f.judged),
        "share": f.share_text,
        "deadline_days": fmt_int(f.deadline_days),
    }
    for i, band in enumerate(f.bands, 1):
        values[f"band{i}_share"] = pct0(band.black_holes, band.judged)
        values[f"band{i}_label"] = band.label
    values.update({
        "top100_black_holes": fmt_int(f.top100_black_holes),
        "top100_overlap": fmt_int(f.top100_overlap),
        "ranked": fmt_int(f.ranked),
        "rho": rho_text(f.rho),
        "unanswered": fmt_int(f.unanswered),
        "counted": fmt_int(f.counted),
        "corpus_prs": fmt_int(f.corpus_prs),
        "corpus_authors": fmt_int(f.corpus_authors),
        "corpus_projects": fmt_int(f.corpus_projects),
        "window_start": start_text,
        "window_end": end_text,
        "min_n": fmt_int(f.min_n),
        "ci_percent": fmt_int(f.ci_percent),
        "undecided_rule_inputs": fmt_int(f.undecided_rule_inputs),
        "below_bands_judged": fmt_int(f.below_bands_judged),
        "lowest_band": f.lowest_band_text,
        "csv_name": f.csv_name,
        "frozen_url": frozen_url,
        "constants_hash": f.constants_hash,
    })
    # only what the page prints: the measurement bullets and the "not charted" note are left out when they have no number
    if not f.undecided_rule_inputs:
        del values["undecided_rule_inputs"]
    if f.undecided_cut:
        values["undecided_cut"] = fmt_int(f.undecided_cut)
        values["pr_cap"] = fmt_int(f.pr_cap)
    if f.sampled:
        values["sampled"] = fmt_int(f.sampled)
        values["full_max"] = fmt_int(f.full_max)
        values["sample_size"] = fmt_int(f.sample_size)
    if not f.below_bands_judged:
        del values["below_bands_judged"], values["lowest_band"]
    method = [
        "We followed {corpus_prs} pull requests from {corpus_authors} outside contributors to {corpus_projects} GitHub "
        "projects, from {window_start} to {window_end}.",
        "A project is judged only when it is licensed, maintained software with at least {min_n} counted outside pull "
        "requests.",
    ]
    if f.undecided_rule_inputs:
        method.append(
            "Another {undecided_rule_inputs} licensed, maintained projects with at least {min_n} outside pull requests on "
            "record are not judged yet: those records were fetched before drafts and closes were recorded, and the "
            "rules need both."
        )
    if f.undecided_cut:
        method.append(
            "We could not judge {undecided_cut} busy projects yet: we hold only the newest {pr_cap} pull requests of "
            "each, and they do not reach back to {window_start}."
        )
    if f.sampled:
        noun = "project" if f.sampled == 1 else "projects"
        method.append(
            f"{{sampled}} {noun} had more than {{full_max}} pull requests in that time. For each, we read replies and "
            "reviews on an even random sample of {sample_size} of them; every pull request counts for decisions and "
            "merges."
        )
    method.append(
        "It is a black hole when, with {ci_percent}% confidence, more than half of those pull requests got no "
        "substantive reply, review, merge or close within {deadline_days} days or since."
    )
    not_charted = None
    if f.below_bands_judged:
        noun = "project" if f.below_bands_judged == 1 else "projects"
        not_charted = f"Not charted: {{below_bands_judged}} judged {noun} with fewer than {{lowest_band}} stars."
    sid = f.snapshot_id
    return {
        "snapshot_id": sid,
        "title": report_title(f),
        "urls": {
            "latest": "/black-holes/",
            "frozen": f"/black-holes/{sid}/",
            "csv": f"/black-holes/{sid}/{f.csv_name}",
        },
        "counts": {"judged": f.judged, "black_holes": f.black_holes},
        "values": values,
        "findings": [
            {
                "id": "finding-share",
                "lead": "More than 1 in {one_in}.",
                "text": "In Porchlamp's {snapshot_id} snapshot, {black_holes} of {judged} active open-source projects "
                "({share}%) left most outside pull requests with no human reply, review, merge or close within {deadline_days} "
                "days or since.",
            },
            {
                "id": "finding-stars",
                "lead": "More stars, more silence.",
                "text": "The share rises with stars: {band1_share}% of projects with {band1_label} stars, {band2_share}% at "
                "{band2_label}, and {band3_share}% at {band3_label}.",
            },
            {
                "id": "finding-top100",
                "lead": "Fame is no guarantee.",
                "text": "{top100_black_holes} of the 100 most-starred projects are black holes, and only {top100_overlap} "
                "of them reach Porchlamp's top 100.",
            },
            {
                "id": "finding-correlation",
                "lead": "Stars barely predict an answer.",
                "text": "Across {ranked} ranked projects, the rank correlation between stars and the Porchlamp score is "
                "{rho}.",
            },
            {
                "id": "finding-prs",
                "lead": "Most of those pull requests waited.",
                "text": "Inside the black-hole projects, {unanswered} of {counted} counted outside pull requests got no "
                "human reply within {deadline_days} days.",
            },
        ],
        "sentences": {
            "lede": "The state of open-source black holes: how often a stranger's pull request goes unanswered, in the "
            "Porchlamp snapshot of {snapshot_date}.",
            "intro": "You send a small fix to a project you use every day. Two weeks later, nobody has replied.",
            "stars_answer": "They answer less often, on average. The black-hole share climbs at every step up in stars.",
            "stars_not_charted": not_charted,
            "ecosystems_answer": ecosystems_answer(),
            "method": method,
            "download": "{csv_name}: the {ranked} ranked projects with rank, owner, name, ecosystem, score, tier, "
            "coverage, quadrant and the rank in each lens. Released under CC BY 4.0.",
            "cite": f"Porchlamp ({{year}}). {CITE_TITLE}. Snapshot {{snapshot_id}}. {{frozen_url}}",
            "hashes": [
                "Data licence: CC BY 4.0.",
                "Snapshot database SHA-256: {source_db_sha256}.",
                "Methodology constants hash: {constants_hash}.",
            ],
            "stars_chart_summary": "Black-hole share by stars: " + "; ".join(
                f"{pct0(b.black_holes, b.judged)}% of {fmt_int(b.judged)} projects with {b.label} stars" for b in f.bands
            ) + ".",
            "ecosystems_chart_summary": "Black-hole share by package ecosystem, highest first: " + "; ".join(
                f"{e.name} {pct1(e.black_holes, e.judged)}% of {fmt_int(e.judged)}" for e in f.ecosystems
            ) + ".",
        },
        "bands": [
            {
                "key": b.key, "label": b.label, "judged": b.judged, "black_holes": b.black_holes,
                "judged_text": fmt_int(b.judged), "black_holes_text": fmt_int(b.black_holes),
                "share_text": pct0(b.black_holes, b.judged),
            }
            for b in f.bands
        ],
        "ecosystems": [
            {
                "key": e.key, "name": e.name, "path": f"/{ECOSYSTEM_SLUGS[e.key]}/", "judged": e.judged,
                "black_holes": e.black_holes, "judged_text": fmt_int(e.judged), "black_holes_text": fmt_int(e.black_holes),
                "share_text": pct1(e.black_holes, e.judged),
            }
            for e in f.ecosystems
        ],
    }


def frozen_stylesheet_href(f: Facts) -> str:
    """The frozen copy's one stylesheet, beside it (ENG-14)."""
    return f"/black-holes/{f.snapshot_id}/styles.css"


def frozen_stylesheet(template: Path = APP_PAGE) -> str:
    """The frozen copy's ``styles.css``: every stylesheet the app's page links, in its order, joined. The copy links
    only this file, so a later restyle of the app cannot alter a page that is never drawn again (ENG-14)."""
    hrefs = re.findall(r'<link rel="stylesheet" href="([^"]*)">', template.read_text(encoding="utf-8"))
    if not hrefs or any(not href.startswith("/styles/") for href in hrefs):
        raise ReportError(f"web/app/404.html links stylesheets this copy cannot carry: {hrefs}")
    return "".join(
        f"/* {href} */\n" + (WEB_APP / href.lstrip("/")).read_text(encoding="utf-8").rstrip("\n") + "\n"
        for href in hrefs
    )


def report_page(f: Facts, *, frozen: bool, exported_on: date) -> str:
    """The report's page, for ``web/prerender.mjs`` to draw the header, nav, footer and the view into: the app's page
    with the report's head (title, description, canonical address, link previews and structured data). The latest
    (``frozen=False``) is the app's page at ``/black-holes/``, which the app takes over. The frozen copy loads no app
    and one stylesheet of its own (:func:`frozen_stylesheet`)."""
    latest, frozen_url, _ = report_urls(f)
    canonical = frozen_url if frozen else latest
    title = report_title(f, frozen=frozen)
    description = report_description(f)
    built = app_page(
        title=title, description=description, head=_article_head(title=title, description=description, canonical=canonical),
        stylesheets=(frozen_stylesheet_href(f),) if frozen else None, scripts=() if frozen else None,
    )
    return _with_ld(built, report_jsonld(f, canonical=canonical, exported_on=exported_on))
