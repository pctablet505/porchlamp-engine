"""The ten landing pages (six ecosystems, four lenses), their links in, and the home page's structured data (E4, E7).

Built by ``scripts/export_static.py --stats`` (``porchlamp/serve/landing_pages.py``) and proved by
``scripts/verify_static_export.py`` (``verify_landing``, ``verify_home``). The fixture is the report tests' synthetic
snapshot (``tests/test_report_pages.py``); every plant below is a change to a copy of its exported site.
"""
# ruff: noqa: F811  (pytest fixtures are imported by name from the report tests and then used as arguments)
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from porchlamp.core import constants
from porchlamp.serve import landing_pages, project_pages, report_pages
from tests.test_report_pages import (  # noqa: F401  (fixtures)
    SNAPSHOT,
    _evidence_files,
    _inputs,
    _ld,
    _page,
    built,
    external_violations,
    inline_scripts,
    project_index,
    script_srcs,
    verify_static_export,
)

ROOT = Path(__file__).resolve().parent.parent
ECOSYSTEM_SLUGS = ("rust", "python", "javascript", "go", "java", "c-cpp")
LENS_SLUGS = ("first-timers", "career", "senior-systems", "ai-assisted")
SLUGS = ECOSYSTEM_SLUGS + LENS_SLUGS


def _verify(db: Path, site: Path) -> dict:
    return verify_static_export.verify_landing(db, site, log=lambda _line: None)


def _copy_site(site: Path, tmp_path: Path) -> Path:
    target = tmp_path / "site"
    shutil.copytree(site, target)
    return target


def _edit(site: Path, rel: str, old: str, new: str) -> str:
    """Replace the first ``old`` in a page; returns the original text so a test can restore it."""
    text = (site / rel).read_text(encoding="utf-8")
    assert old in text, f"{old!r} not in {rel}"
    (site / rel).write_text(text.replace(old, new, 1), encoding="utf-8")
    return text


# ---- the pages exist, say what the brief says, and are well-formed ---------------------------------------------

def test_the_ten_pages_exist_and_the_slugs_are_the_addresses_in_the_brief():
    assert landing_pages.SLUGS == SLUGS
    assert {p.key: p.slug for p in landing_pages.ECOSYSTEM_PAGES} == {
        "crates": "rust", "pypi": "python", "npm": "javascript", "go": "go", "maven": "java", "systems": "c-cpp"}
    assert {p.key: p.slug for p in landing_pages.LENS_PAGES} == {
        "first_timer": "first-timers", "career_capital": "career", "senior_systems": "senior-systems", "ai_assisted": "ai-assisted"}


def test_the_lists_follow_the_engines_own_keys_and_the_copy_js_names():
    assert {p.key for p in landing_pages.ECOSYSTEM_PAGES} == set(report_pages.ECOSYSTEM_NAMES)
    lenses = set(constants.PERSONA_PRESETS) - {"balanced"}
    assert {p.key for p in landing_pages.LENS_PAGES} == lenses
    assert set(report_pages.LENS_SLUGS) == lenses and set(report_pages.ECOSYSTEM_SLUGS) == set(report_pages.ECOSYSTEM_NAMES)
    names = report_pages.copy_js_map("PERSONA_NAMES")
    assert all(p.key in names for p in landing_pages.LENS_PAGES)


def test_export_writes_ten_pages_each_with_its_own_canonical_and_no_external_load(built):
    site = built[0]
    for slug in SLUGS:
        text = _page(site, f"{slug}/index.html")
        assert re.findall(r'<link rel="canonical" href="([^"]*)"', text) == [f"https://porchlamp.org/{slug}/"]
        assert external_violations(text) == []
        assert script_srcs(text) == list(report_pages.PAGE_SCRIPTS["page"])
        assert inline_scripts(text) == ['<script type="application/ld+json">']
        shown = re.sub(r'<th scope="row">.*?</th>', " ", text)  # a project may be called "null"
        assert not re.search(r"\b(?:null|None|NaN|undefined)\b", re.sub(r"<[^>]+>", " ", shown)), slug
        # The shared chrome's nav (drawn by the prerender) is on the page; the body keeps its own links.
        assert '<nav class="app-primary-nav" id="app-primary-nav" aria-label="Primary">' in text
        nav = re.search(r'<nav class="app-primary-nav".*?</nav>', text, re.S).group(0)
        assert re.findall(r'<a class="primary-tab" href="([^"]*)"', nav) == ["/", "/matrix/", "/compare/", "/weights/", "/methodology/", "/black-holes/"]
        assert 'aria-current' not in nav  # a landing page is none of the nav's pages
        assert 'href="../methodology/"' in text and 'href="../black-holes/"' in text
        assert f'href="../black-holes/{SNAPSHOT}/porchlamp-{SNAPSHOT}-ranked.csv"' in text
        ld = _ld(text)
        assert ld["@type"] == "BreadcrumbList" and ld["itemListElement"][1]["item"] == f"https://porchlamp.org/{slug}/"


def test_every_static_title_is_unique_across_the_whole_site(built):
    site = built[0]
    titles = {}
    pages = [p for p in site.rglob("index.html") if "data" not in p.relative_to(site).parts]
    assert len(pages) > 100
    for path in pages:
        title = re.search(r"<title>(.*?)</title>", path.read_text(encoding="utf-8"), re.S).group(1)
        assert title not in titles, f"{path.relative_to(site)} and {titles[title]} share the title {title!r}"
        titles[title] = path.relative_to(site)
    assert all(f"{slug}/index.html" in {t.as_posix() for t in titles.values()} for slug in SLUGS)


def test_the_ecosystem_copy_follows_the_brief(built):
    site = built[0]
    for slug, name, judged in (("rust", "Rust", "crates.io"), ("python", "Python", "PyPI"), ("go", "Go", "Go"),
                               ("c-cpp", "C, C++ and Zig", "C, C++ and Zig")):
        text = _page(site, f"{slug}/index.html")
        assert f"<title>{name} open-source projects that answer outside pull requests (October 2026) | Porchlamp</title>" in text
        assert f"<h1>{name} projects that answer outside pull requests</h1>" in text
        intro = re.search(r'<p class="answer" id="intro">(.*?)</p>', text, re.S).group(1)
        assert re.fullmatch(
            rf"Porchlamp ranks [\d,]+ {re.escape(name)} projects by what happens to a stranger's pull request\. "
            rf"Among the [\d,]+ judged {re.escape(judged)} projects, \d+\.\d% left most outside pull requests unanswered "
            r"for two weeks, against \d+\.\d% across all six ecosystems\.", intro), intro
        note = re.search(r'<p id="table-note">(.*?)</p>', text).group(1)
        assert re.fullmatch(r"The table lists the top \d+ by the balanced ranking\. Each name opens that project's page\.", note), note


def test_the_lens_copy_follows_the_brief_and_the_weights_are_the_methodologys(built):
    site = built[0]
    methodology = json.loads((site / "data" / SNAPSHOT / "methodology.json").read_text(encoding="utf-8"))
    for slug, lens, phrase in (("first-timers", "first_timer", "first-time contributors"),
                               ("career", "career_capital", "building career capital"),
                               ("senior-systems", "senior_systems", "senior systems engineers"),
                               ("ai-assisted", "ai_assisted", "AI-assisted contributors")):
        text = _page(site, f"{slug}/index.html")
        assert f"<title>Open-source projects for {phrase}, ranked by who answers (October 2026) | Porchlamp</title>" in text
        intro = re.search(r'<p class="answer" id="intro">(.*?)</p>', text, re.S).group(1)
        match = re.fullmatch(rf"This view ranks ([\d,]+) projects for {re.escape(phrase)}\. It weighs (.*)\.", intro)
        assert match, intro
        shown = re.findall(r"([a-z]+) (\d+)%", match.group(2))
        weights = methodology["personas"]["presets"][lens]
        names = {v.lower(): k for k, v in report_pages.copy_js_map("PILLAR_NAMES").items()}
        assert {names[n]: int(p) for n, p in shown} == {k: round(v * 100) for k, v in weights.items() if v}
        assert [int(p) for _n, p in shown] == sorted((int(p) for _n, p in shown), reverse=True), "largest weight first"
        note = re.search(r'<p id="table-note">(.*?)</p>', text).group(1)
        assert re.fullmatch(r"The table lists the top \d+ in this view\. Each name opens that project's page(?:, except .*)?\.", note), note


def test_the_table_has_a_row_per_project_in_rank_order_and_each_name_opens_its_page(built):
    site = built[0]
    for slug in SLUGS:
        text = _page(site, f"{slug}/index.html")
        rows = re.findall(r"<tr><td>([\d,]+)</td><th scope=\"row\">(.*?)</th><td>([\d.]+)</td><td>([^<]+)</td></tr>", text)
        assert 0 < len(rows) <= 50, slug
        ranks = [int(r[0].replace(",", "")) for r in rows]
        assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks), slug
        if slug in ECOSYSTEM_SLUGS:
            assert ranks == list(range(1, len(rows) + 1)), "an ecosystem page ranks within the ecosystem"
        for _rank, name_cell, _score, _tier in rows:
            link = re.fullmatch(r'<a href="\.\./projects/([^"]+)/">([^<]+)<wbr>([^<]+)</a>', name_cell)
            if link:
                assert (site / "projects" / link.group(1) / "index.html").is_file(), name_cell
                assert (link.group(2) + link.group(3)).lower() == link.group(1)
            else:
                assert re.fullmatch(r"[^<]+/<wbr>[^<]+", name_cell), name_cell


def test_a_project_a_lens_ranks_but_the_balanced_ranking_does_not_is_named_without_a_link():
    row = landing_pages.Row(7, "Acme", "Widget", "81.00", "B", False)
    linked = landing_pages.Row(8, "acme", "gadget", "80.00", "A", True)
    assert landing_pages.table_note("in this view", [linked]) == (
        "The table lists the top 1 in this view. Each name opens that project's page.")
    note = landing_pages.table_note("in this view", [linked, row])
    assert note.endswith("Each name opens that project's page, except Acme/Widget, which has no page because it is not in the balanced ranking.")
    assert landing_pages.table_note("in this view", [row, row]).count("have no page because they are not") == 1
    assert landing_pages._name_cell(row, "../") == "Acme/<wbr>Widget"
    assert landing_pages._name_cell(linked, "../") == '<a href="../projects/acme/gadget/">acme/<wbr>gadget</a>'


def test_a_lens_leaves_zero_weights_out_and_an_unknown_lens_stops_the_export(built):
    methodology = json.loads((built[0] / "data" / SNAPSHOT / "methodology.json").read_text(encoding="utf-8"))
    copy = project_pages.Copy.load()
    methodology["personas"]["presets"]["first_timer"] = {"responsiveness": 0.75, "criticality": 0.0, "rigor": 0.25}
    assert landing_pages.lens_weights_text(methodology, "first_timer", copy) == "responsiveness 75% and rigor 25%"
    with pytest.raises(report_pages.ReportError):
        landing_pages.lens_weights_text(methodology, "no_such_lens", copy)


def test_an_ecosystem_with_no_judged_project_stops_the_export_instead_of_printing_a_guess(built, project_index):
    facts = report_pages.build_facts(**_inputs(built))
    copy = project_pages.Copy.load()
    spec = landing_pages.ECOSYSTEM_PAGES[0]
    import dataclasses

    empty = dataclasses.replace(facts, ecosystems=tuple(e for e in facts.ecosystems if e.key != spec.key))
    with pytest.raises(report_pages.ReportError, match="no judged project"):
        landing_pages.ecosystem_landing(empty, spec, project_index, copy)
    slow = dataclasses.replace(facts, deadline_days=21)
    with pytest.raises(report_pages.ReportError, match="two weeks"):
        landing_pages.ecosystem_landing(slow, spec, project_index, copy)


def test_a_script_planted_in_the_landing_template_turns_the_guard_red(built, project_index):
    facts = report_pages.build_facts(**_inputs(built))
    landing = landing_pages.build_landings(facts, _inputs(built)["methodology"], project_index, project_pages.Copy.load())[0]
    assert external_violations(landing_pages.render_landing(facts, landing)) == []
    planted = landing_pages.render_landing(facts, landing).replace("</body>", '<script src="https://cdn.example.com/x.js"></script></body>')
    assert external_violations(planted), "the guard did not see the planted script"


# ---- the verifier proves every number and every row, and fails on a plant ---------------------------------------

def test_verify_landing_proves_every_number_and_row(built):
    site, db, *_ = built
    result = _verify(db, site)
    assert result["errors"] == []
    assert result["pages"] == 10 and result["rows"] > 10 and result["checked"] > 10 * result["rows"] // 4


def _first_row(text: str) -> tuple[str, str]:
    """(the first row's name text, its link target)."""
    match = re.search(r'<th scope="row"><a href="([^"]*)">([^<]*)<wbr>([^<]*)</a></th>', text)
    return match.group(2) + match.group(3), match.group(1)


def test_a_planted_wrong_number_fails_verification_naming_the_page_and_the_number(built, tmp_path):
    site, db, *_ = built
    copy_dir = _copy_site(site, tmp_path)
    intro = re.search(r'<p class="answer" id="intro">Porchlamp ranks ([\d,]+) Rust', (copy_dir / "rust/index.html").read_text(encoding="utf-8"))
    ranked, wrong = intro.group(1), f"{int(intro.group(1).replace(',', '')) + 1:,}"
    original = _edit(copy_dir, "rust/index.html", f'id="intro">Porchlamp ranks {ranked} Rust', f'id="intro">Porchlamp ranks {wrong} Rust')
    errors = _verify(db, copy_dir)["errors"]
    assert any("rust/index.html #intro" in e and ranked in e for e in errors), errors
    (copy_dir / "rust/index.html").write_text(original, encoding="utf-8")
    assert _verify(db, copy_dir)["errors"] == []


def test_a_planted_wrong_weight_on_a_lens_page_fails_verification(built, tmp_path):
    site, db, *_ = built
    copy_dir = _copy_site(site, tmp_path)
    text = (copy_dir / "first-timers/index.html").read_text(encoding="utf-8")
    first = re.search(r"It weighs responsiveness (\d+)%", text).group(1)
    paragraph = re.search(r'<p class="answer" id="intro">.*?</p>', text, re.S).group(0)
    _edit(copy_dir, "first-timers/index.html", paragraph, paragraph.replace(f"responsiveness {first}%", f"responsiveness {int(first) + 1}%"))
    errors = _verify(db, copy_dir)["errors"]
    assert any("first-timers/index.html #intro" in e and f"{first}%" in e for e in errors), errors


def test_a_planted_swap_of_two_names_fails_verification_naming_the_rows(built, tmp_path):
    site, db, *_ = built
    copy_dir = _copy_site(site, tmp_path)
    text = (copy_dir / "python/index.html").read_text(encoding="utf-8")
    cells = re.findall(r'(<th scope="row">(?:<a href="[^"]*">)?[^<]+<wbr>[^<]+(?:</a>)?</th>)', text)
    a, b = cells[0], cells[1]
    (copy_dir / "python/index.html").write_text(text.replace(a, "@@", 1).replace(b, a, 1).replace("@@", b, 1), encoding="utf-8")
    errors = _verify(db, copy_dir)["errors"]
    assert any("python/index.html row 1" in e and "owner/name" in e for e in errors), errors
    assert any("python/index.html row 2" in e and "owner/name" in e for e in errors), errors


def test_a_planted_wrong_score_and_tier_each_fail_verification(built, tmp_path):
    site, db, *_ = built
    copy_dir = _copy_site(site, tmp_path)
    text = (copy_dir / "go/index.html").read_text(encoding="utf-8")
    score = re.search(r"<td>(\d+\.\d\d)</td>", text).group(1)
    _edit(copy_dir, "go/index.html", f"<td>{score}</td>", f"<td>{float(score) + 0.01:.2f}</td>")
    assert any("go/index.html row 1" in e and "score" in e and score in e for e in _verify(db, copy_dir)["errors"])
    tier = re.search(rf"<td>{re.escape(score)}</td><td>([^<]+)</td>", text).group(1)
    (copy_dir / "go/index.html").write_text(text, encoding="utf-8")
    _edit(copy_dir, "go/index.html", f"<td>{score}</td><td>{tier}</td>", f"<td>{score}</td><td>Z</td>")
    assert any("go/index.html row 1" in e and "tier" in e and tier in e for e in _verify(db, copy_dir)["errors"])


def test_a_planted_wrong_link_target_fails_verification_naming_the_row(built, tmp_path):
    site, db, *_ = built
    copy_dir = _copy_site(site, tmp_path)
    name, href = _first_row((copy_dir / "career/index.html").read_text(encoding="utf-8"))
    original = _edit(copy_dir, "career/index.html", f'<a href="{href}">', f'<a href="{href.replace("projects/", "project/")}">')
    errors = _verify(db, copy_dir)["errors"]
    assert any("career/index.html row 1" in e and "link target" in e and href in e for e in errors), errors
    (copy_dir / "career/index.html").write_text(original, encoding="utf-8")
    assert _verify(db, copy_dir)["errors"] == []


def test_a_missing_row_a_missing_page_and_a_missing_method_link_each_fail_verification(built, tmp_path):
    site, db, *_ = built
    copy_dir = _copy_site(site, tmp_path)
    text = (copy_dir / "java/index.html").read_text(encoding="utf-8")
    (copy_dir / "java/index.html").write_text(re.sub(r"<tr><td>1</td>.*?</tr>", "", text, count=1, flags=re.S), encoding="utf-8")
    assert any("java/index.html table row count" in e for e in _verify(db, copy_dir)["errors"])
    (copy_dir / "java/index.html").write_text(text, encoding="utf-8")
    _edit(copy_dir, "java/index.html", 'href="../methodology/">Porchlamp methodology', 'href="../methodolgy/">Porchlamp methodology')
    assert any("java/index.html link to the methodology" in e for e in _verify(db, copy_dir)["errors"])
    (copy_dir / "java/index.html").write_text(text, encoding="utf-8")
    shutil.rmtree(copy_dir / "java")
    assert any("java/index.html is missing" in e for e in _verify(db, copy_dir)["errors"])


def test_a_planted_placeholder_text_a_script_and_a_duplicate_title_fail_verification(built, tmp_path):
    site, db, *_ = built
    copy_dir = _copy_site(site, tmp_path)
    text = (copy_dir / "rust/index.html").read_text(encoding="utf-8")
    _edit(copy_dir, "rust/index.html", "The table lists the top", "NaN The table lists the top")
    assert any("rust/index.html" in e and "NaN" in e for e in _verify(db, copy_dir)["errors"])
    (copy_dir / "rust/index.html").write_text(text, encoding="utf-8")
    _edit(copy_dir, "rust/index.html", "</body>", '<script src="https://cdn.example.com/x.js"></script></body>')
    assert any(e.startswith("rust/index.html scripts:") for e in _verify(db, copy_dir)["errors"])


def test_the_sitemap_and_llms_txt_list_all_ten_and_the_verifier_notices_one_missing(built, tmp_path):
    site, db, *_ = built
    # the export writes the page sitemap; llms.txt is written by hand
    for name, path in (("sitemap-pages.xml", site / "sitemap-pages.xml"), ("llms.txt", ROOT / "web" / "app" / "llms.txt")):
        text = path.read_text(encoding="utf-8")
        for slug in SLUGS:
            assert text.count(f"https://porchlamp.org/{slug}/") == 1, (name, slug)
    copy_dir = _copy_site(site, tmp_path)
    _edit(copy_dir, "sitemap-pages.xml", "<loc>https://porchlamp.org/career/</loc>", "<loc>https://porchlamp.org/careers/</loc>")
    errors = _verify(db, copy_dir)["errors"]
    assert any("sitemap-pages.xml lists https://porchlamp.org/career/" in e for e in errors), errors
    assert any("careers/" in p for p in verify_static_export.check_sitemap(copy_dir))


# ---- the links in -------------------------------------------------------------------------------------------

def test_the_report_names_its_ecosystems_with_links_to_their_pages(built):
    site = built[0]
    for rel, prefix in (("black-holes/index.html", "../"), (f"black-holes/{SNAPSHOT}/index.html", "../../")):
        text = _page(site, rel)
        section = re.search(r'<h2 id="ecosystems">(.*?)<h2 id="how-we-measured">', text, re.S).group(1)
        links = re.findall(r'<th scope="row"><a href="([^"]*)">([^<]*)</a></th>', section)
        assert sorted(href for href, _n in links) == sorted(f"{prefix}{slug}/" for slug in ECOSYSTEM_SLUGS)
        assert {name for _h, name in links} == set(report_pages.ECOSYSTEM_NAMES.values())


def test_a_project_page_links_its_ecosystem_to_the_ecosystem_page(built):
    site = built[0]
    seen = set()
    for path in sorted((site / "projects").rglob("index.html")):
        text = path.read_text(encoding="utf-8")
        match = re.search(r'<li>Ecosystem: <a href="\.\./\.\./\.\./([^"]+)/">([^<]+)</a></li>', text)
        assert match, path
        assert match.group(1) in ECOSYSTEM_SLUGS and (site / match.group(1) / "index.html").is_file()
        seen.add(match.group(1))
    assert seen == set(ECOSYSTEM_SLUGS)


def test_a_planted_wrong_link_in_the_report_and_a_project_page_each_fail_verification(built, tmp_path):
    site, db, stats_path, *_ = built
    copy_dir = _copy_site(site, tmp_path)
    report = f"black-holes/{SNAPSHOT}/index.html"
    text = (copy_dir / report).read_text(encoding="utf-8")
    _edit(copy_dir, report, 'href="../../rust/"', 'href="../../rusty/"')
    errors = verify_static_export.verify_report(db, stats_path, copy_dir, evidence_files=_evidence_files(built), log=lambda _line: None)["errors"]
    assert any(report in e and "links to the ecosystem pages" in e for e in errors), errors
    (copy_dir / report).write_text(text, encoding="utf-8")
    project = sorted((copy_dir / "projects").rglob("index.html"))[0]
    rel = project.relative_to(copy_dir).as_posix()
    body = project.read_text(encoding="utf-8")
    slug = re.search(r'Ecosystem: <a href="\.\./\.\./\.\./([^"]+)/">', body).group(1)
    project.write_text(body.replace(f'../../../{slug}/">', '../../../elsewhere/">', 1), encoding="utf-8")
    errors = verify_static_export.verify_projects(db, copy_dir, log=lambda _line: None)["errors"]
    assert any(rel in e and "ecosystem link" in e for e in errors), errors[:3]


# ---- E7: the home page's structured data --------------------------------------------------------------------

HOME = ROOT / "web" / "app" / "index.html"


def _home_ld(text: str) -> dict:
    return json.loads(re.search(r'<script type="application/ld\+json">\s*(.*?)\s*</script>', text, re.S).group(1))


def _home_site(built, tmp_path: Path) -> Path:
    """A site holding the shipped home page, the fixture's report page, and a stub page at every address the home
    page's ItemList names (the fixture has none of the real top-10 projects)."""
    site = tmp_path / "home-site"
    (site / "black-holes").mkdir(parents=True)
    shutil.copy(HOME, site / "index.html")
    shutil.copy(built[0] / "black-holes" / "index.html", site / "black-holes" / "index.html")
    for item in next(n for n in _home_ld(HOME.read_text(encoding="utf-8"))["@graph"] if n["@type"] == "ItemList")["itemListElement"]:
        address = item["url"].removeprefix("https://porchlamp.org/")
        (site / address).mkdir(parents=True)
        (site / address / "index.html").write_text("<!doctype html>", encoding="utf-8")
    return site


def _verify_home(site: Path) -> list[str]:
    return verify_static_export.verify_home(site, log=lambda _line: None)["errors"]


def test_the_shipped_home_page_has_no_software_application_no_dataset_and_project_page_addresses(built, tmp_path):
    graph = _home_ld(HOME.read_text(encoding="utf-8"))["@graph"]
    assert [n["@type"] for n in graph] == ["Person", "WebSite", "WebPage", "ItemList"]
    report_dataset = next(n for n in _ld(_page(built[0], "black-holes/index.html"))["@graph"] if n["@type"] == "Dataset")["@id"]
    assert next(n for n in graph if n["@type"] == "WebPage")["about"] == {"@id": report_dataset}
    items = next(n for n in graph if n["@type"] == "ItemList")["itemListElement"]
    assert len(items) == 10
    assert all(re.fullmatch(r"https://porchlamp\.org/projects/[a-z0-9._-]+/[a-z0-9._-]+/", i["url"]) for i in items)
    assert _verify_home(_home_site(built, tmp_path)) == []


def _plant(site: Path, change) -> list[str]:
    text = (site / "index.html").read_text(encoding="utf-8")
    ld = _home_ld(text)
    change(ld)
    new = json.dumps(ld, separators=(",", ":"), ensure_ascii=False)
    old = re.search(r'<script type="application/ld\+json">\s*(.*?)\s*</script>', text, re.S).group(1)
    (site / "index.html").write_text(text.replace(old, new, 1), encoding="utf-8")
    return _verify_home(site)


def test_each_e7_rule_fails_on_its_own_plant(built, tmp_path):
    base = _home_site(built, tmp_path)
    assert _verify_home(base) == []

    def fresh(name: str) -> Path:
        target = tmp_path / name
        shutil.copytree(base, target)
        return target

    errors = _plant(fresh("app"), lambda ld: ld["@graph"].append({"@type": "SoftwareApplication", "name": "Porchlamp"}))
    assert any("no SoftwareApplication" in e for e in errors), errors
    errors = _plant(fresh("data"), lambda ld: ld["@graph"].append({"@type": "Dataset", "@id": "https://porchlamp.org/#dataset"}))
    assert any("no Dataset" in e for e in errors), errors

    def about_home(ld):
        next(n for n in ld["@graph"] if n["@type"] == "WebPage")["about"] = {"@id": "https://porchlamp.org/#dataset"}

    assert any("about names the report's Dataset" in e for e in _plant(fresh("about"), about_home))

    def old_address(ld):
        item = next(n for n in ld["@graph"] if n["@type"] == "ItemList")["itemListElement"][0]
        item["url"] = "https://porchlamp.org/?repo=meshery%2Fmeshery"

    assert any("?repo=meshery" in e and "project page address" in e for e in _plant(fresh("old"), old_address))

    def item_path(index: int) -> str:
        """The ``<owner>/<name>/`` of the home list's item ``index``, whichever project ranks there."""
        ld = json.loads(re.search(r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
                                  (base / "index.html").read_text(encoding="utf-8"), re.S).group(1))
        url = next(n for n in ld["@graph"] if n["@type"] == "ItemList")["itemListElement"][index]["url"]
        return url.split("/projects/", 1)[1]

    second = item_path(1)

    def upper_case(ld):
        item = next(n for n in ld["@graph"] if n["@type"] == "ItemList")["itemListElement"][1]
        item["url"] = item["url"].replace(second, second.upper())

    assert second != second.upper()
    errors = _plant(fresh("case"), upper_case)
    assert any(second.upper() in e and "project page address" in e for e in errors), errors

    first = item_path(0)
    gone = fresh("gone")
    shutil.rmtree(gone / "projects" / first.split("/")[0])
    errors = _verify_home(gone)
    assert any(f"projects/{first}" in e and "has a page in the export" in e for e in errors), errors


def test_the_home_article_links_the_six_ecosystem_pages_and_the_verifier_notices_a_missing_line(built, tmp_path):
    base = _home_site(built, tmp_path)
    text = (base / "index.html").read_text(encoding="utf-8")
    assert re.search(r"Browse by ecosystem:\s*<a href=\"rust/\">Rust</a> ·", text)
    (base / "index.html").write_text(text.replace("Browse by ecosystem:", "Browse:"), encoding="utf-8")
    assert any("Browse by ecosystem" in e for e in _verify_home(base))
    (base / "index.html").write_text(text, encoding="utf-8")
    assert _verify_home(base) == []
