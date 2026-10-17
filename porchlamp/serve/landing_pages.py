"""Ten landing pages: six ecosystems and four lenses (``scripts/export_static.py --stats``).

``/rust/``, ``/python/``, ``/javascript/``, ``/go/``, ``/java/`` and ``/c-cpp/`` answer "which Rust projects answer
outside pull requests?"; ``/first-timers/``, ``/career/``, ``/senior-systems/`` and ``/ai-assisted/`` are the four
non-default lenses. Each page is a real destination with its own data, not a funnel: a short intro whose numbers are
read from the snapshot, then a static table of the top 50 projects of that cut, each linked to its project page
(:mod:`porchlamp.serve.project_pages`).

Nothing is typed that a record holds. The counts come from the leaderboards (ranked) and the report's facts (judged,
black holes: :func:`report_pages.build_facts`); the lens names come from ``web/app/copy.js`` and the lens weights from
``methodology.json``. The ecosystem names come from ``copy.js`` ``ECOSYSTEM_NAMES`` (``report_pages.ECOSYSTEM_NAMES``), in
plain language: Rust, Python, JavaScript, Go, Java, C and C++, never a registry's name. What is typed here is the one
natural phrase for each lens in a title (:data:`LENS_PAGES`); a test pins both lists to the engine's own keys.
``scripts/verify_static_export.py`` recomputes every number and every table row and compares.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from porchlamp.serve.project_pages import BALANCED, Copy, ProjectIndex, js_fixed, project_path
from porchlamp.serve.report_pages import (
    ECOSYSTEM_NAMES,
    ECOSYSTEM_SLUGS,
    LENS_SLUGS,
    SITE,
    Facts,
    ReportError,
    _a,
    _t,
    fmt_int,
    long_date,
    page,
    pct1,
)

#: Rows in a landing page's table (fewer only when the cut has fewer ranked projects).
TOP = 50
NUMBER_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}


@dataclass(frozen=True)
class EcosystemPage:
    key: str  # the engine's ecosystem key
    slug: str
    name: str  # copy.js's plain-language name, as a searcher says it


@dataclass(frozen=True)
class LensPage:
    key: str  # the engine's lens (persona) key
    slug: str
    phrase: str  # "first-time contributors": the words a title uses after "projects for"


#: The six pages. The name is ``ECOSYSTEM_NAMES[key]`` (copy.js): one list, used everywhere.
ECOSYSTEM_PAGES = tuple(EcosystemPage(key, ECOSYSTEM_SLUGS[key], ECOSYSTEM_NAMES[key]) for key in ("crates", "pypi", "npm", "go", "maven", "systems"))
#: The phrase each lens's title uses, from the wording of ``copy.js`` (``PERSONA_GATE_COPY`` and ``PERSONA_NAMES``).
LENS_PAGES = tuple(
    LensPage(key, LENS_SLUGS[key], phrase)
    for key, phrase in (
        ("first_timer", "first-time contributors"),
        ("career_capital", "building career capital"),
        ("senior_systems", "senior systems engineers"),
        ("ai_assisted", "AI-assisted contributors"),
    )
)
SLUGS = tuple(p.slug for p in ECOSYSTEM_PAGES) + tuple(p.slug for p in LENS_PAGES)


def landing_url(slug: str) -> str:
    return f"{SITE}/{slug}/"


@dataclass(frozen=True)
class Row:
    rank: int
    owner: str
    name: str
    score: str
    tier: str
    linked: bool  # False for a project ranked in this lens but not in the balanced ranking: it has no project page


@dataclass(frozen=True)
class Landing:
    slug: str
    title: str
    h1: str
    description: str
    intro: str  # the answer paragraph
    note: str  # the sentence above the table
    caption: str
    rows: tuple[Row, ...]


def _row(rank: int, record: Mapping[str, Any], index: ProjectIndex, copy: Copy) -> Row:
    owner, name = record["owner"], record["name"]
    linked = (owner, name) in index.rank[BALANCED]
    return Row(rank, owner, name, js_fixed(record["porchlamp"], 2), copy.tier_label(record["tier"], record["tier_ceiling"]), linked)


def table_note(scope: str, rows: Sequence[Row]) -> str:
    """``The table lists the top 50 <scope>. Each name opens that project's page.`` Project pages exist only for the
    balanced ranking, so a project that a lens ranks but the balanced ranking does not is named as having none."""
    note = f"The table lists the top {fmt_int(len(rows))} {scope}."
    unlinked = [f"{r.owner}/{r.name}" for r in rows if not r.linked]
    if not unlinked:
        return f"{note} Each name opens that project's page."
    verb = "has" if len(unlinked) == 1 else "have"
    cause = "it is" if len(unlinked) == 1 else "they are"
    return (
        f"{note} Each name opens that project's page, except {_joined(unlinked)}, which {verb} no page "
        f"because {cause} not in the balanced ranking."
    )


def _percent_text(fraction: float) -> str:
    return f"{round(fraction * 100 + 1e-9)}%"


def _joined(items: Sequence[str]) -> str:
    """``a``, ``a and b``, ``a, b and c``."""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def ecosystem_landing(f: Facts, spec: EcosystemPage, index: ProjectIndex, copy: Copy) -> Landing:
    eco = next((e for e in f.ecosystems if e.key == spec.key), None)
    if eco is None or eco.judged == 0:
        raise ReportError(f"no judged project in the ecosystem {spec.key}; its page has nothing to say")
    if len(f.ecosystems) not in NUMBER_WORDS:
        raise ReportError(f"{len(f.ecosystems)} ecosystems have no number word for 'across all six ecosystems'")
    if f.deadline_days != 14:
        raise ReportError(f"the pages say 'two weeks'; the gate's deadline is {f.deadline_days} days")
    cut = [r for r in index.balanced if r["ecosystem"] == spec.key]
    if not cut:
        raise ReportError(f"no ranked project in the ecosystem {spec.key}")
    rows = tuple(_row(i + 1, r, index, copy) for i, r in enumerate(cut[:TOP]))
    first = f"Porchlamp ranks {fmt_int(len(cut))} {spec.name} projects by what happens to a stranger's pull request."
    second = (
        f"Among the {fmt_int(eco.judged)} judged {spec.name} projects, {pct1(eco.black_holes, eco.judged)}% left most "
        f"outside pull requests unanswered for two weeks, against {f.share_text}% across all {NUMBER_WORDS[len(f.ecosystems)]} ecosystems."
    )
    note = table_note("by the balanced ranking", rows)
    return Landing(
        slug=spec.slug,
        title=f"{spec.name} open-source projects that answer outside pull requests ({f.month_year}) | Porchlamp",
        h1=f"{spec.name} projects that answer outside pull requests",
        description=f"{first} {second}",
        intro=f"{first} {second}",
        note=note,
        caption=f"Top {fmt_int(len(rows))} {spec.name} projects by the balanced ranking",
        rows=rows,
    )


def lens_weights_text(methodology: Mapping[str, Any], lens: str, copy: Copy) -> str:
    """``responsiveness 35%, accessibility 30%, governance 15%, criticality 10% and rigor 10%``: the lens's weights
    from ``methodology.json``, largest first (equal weights keep the methodology's pillar order), zero weights left out."""
    order = [p["name"] for p in methodology["pillars"]]
    weights = methodology["personas"]["presets"].get(lens)
    if weights is None:
        raise ReportError(f"methodology.json has no weights for the lens {lens}")
    ranked = sorted((p for p in order if weights.get(p)), key=lambda p: (-weights[p], order.index(p)))
    if not ranked:
        raise ReportError(f"the lens {lens} weighs no pillar")
    return _joined([f"{copy.pillar_names[p].lower()} {_percent_text(weights[p])}" for p in ranked])


def lens_landing(f: Facts, spec: LensPage, index: ProjectIndex, copy: Copy, methodology: Mapping[str, Any]) -> Landing:
    board = index.rank[spec.key]
    records = index.lens_rows[spec.key]
    if len(records) != index.totals[spec.key] or len(board) != len(records):
        raise ReportError(f"the {spec.key} leaderboard is not whole")
    rows = tuple(_row(r["rank"], r, index, copy) for r in records[:TOP])
    first = (
        f"This view ranks {fmt_int(index.totals[spec.key])} projects for {spec.phrase}. "
        f"It weighs {lens_weights_text(methodology, spec.key, copy)}."
    )
    note = table_note("in this view", rows)
    return Landing(
        slug=spec.slug,
        title=f"Open-source projects for {spec.phrase}, ranked by who answers ({f.month_year}) | Porchlamp",
        h1=f"Open-source projects for {spec.phrase}",
        description=first,
        intro=first,
        note=note,
        caption=f"Top {fmt_int(len(rows))} projects for {spec.phrase}",
        rows=rows,
    )


def build_landings(f: Facts, methodology: Mapping[str, Any], index: ProjectIndex, copy: Copy) -> list[Landing]:
    """The ten pages' content, ecosystems first. Titles must differ from one another."""
    pages = [ecosystem_landing(f, spec, index, copy) for spec in ECOSYSTEM_PAGES]
    pages += [lens_landing(f, spec, index, copy, methodology) for spec in LENS_PAGES]
    titles = [p.title for p in pages]
    if len(set(titles)) != len(titles):
        raise ReportError("two landing pages share a title")
    return pages


def _name_cell(r: Row, rel: str) -> str:
    label = f"{_t(r.owner)}/<wbr>{_t(r.name)}"
    return f'<a href="{_a(rel + project_path(r.owner, r.name))}">{label}</a>' if r.linked else label


def render_landing(f: Facts, landing: Landing) -> str:
    canonical, rel = landing_url(landing.slug), "../"
    csv_href = f"{rel}black-holes/{f.snapshot_id}/{f.csv_name}"
    rows = "".join(
        f'<tr><td>{fmt_int(r.rank)}</td>'
        f'<th scope="row">{_name_cell(r, rel)}</th>'
        f"<td>{_t(r.score)}</td><td>{_t(r.tier)}</td></tr>"
        for r in landing.rows
    )
    body = f"""      <article class="seo-fallback report landing">
        <h1>{_t(landing.h1)}</h1>
        <p class="lede">In the Porchlamp snapshot of {_t(long_date(f.snapshot_date))}.</p>
        <p class="answer" id="intro">{_t(landing.intro)}</p>
        <h2 id="projects">The top {_t(fmt_int(len(landing.rows)))}</h2>
        <p id="table-note">{_t(landing.note)}</p>
        <div class="seo-fallback-table-wrap"><table class="landing-table"><caption>{_t(landing.caption)}</caption><thead><tr><th scope="col">Rank</th><th scope="col">Project</th><th scope="col">Score</th><th scope="col">Tier</th></tr></thead><tbody>{rows}</tbody></table></div>
        <h2 id="method">Method and data</h2>
        <ul class="evidence">
          <li>How every number is measured: <a href="{rel}methodology/">Porchlamp methodology</a>.</li>
          <li>Why stars do not decide it: <a href="{rel}black-holes/">the black-hole report</a>.</li>
          <li>All {_t(fmt_int(f.ranked))} ranked projects as a table: <a href="{_a(csv_href)}">{_t(f.csv_name)}</a>. Released under CC BY 4.0.</li>
        </ul>
      </article>"""
    ld = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Porchlamp", "item": f"{SITE}/"},
            {"@type": "ListItem", "position": 2, "name": landing.h1, "item": canonical},
        ],
    }
    return page(title=landing.title, description=landing.description, canonical=canonical, body=body, ld=ld)
