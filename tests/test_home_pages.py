"""The home page, ``llms.txt`` and ``feed.xml`` are generated at export from the snapshot's own data
(``porchlamp/serve/home_pages.py``), and ``scripts/verify_static_export.py`` ``verify_home`` re-generates each from facts
it works out itself: a wrong number in any of them, or in the data the export read, is named.

The fixture is the report tests' synthetic ``--stats`` export (``tests/test_report_pages.py``); every plant is a change to a
copy of its site.
"""
# ruff: noqa: F811  (the pytest fixture is imported by name from the report tests and then used as an argument)
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from porchlamp.serve import home_pages, report_pages
from tests.test_ecosystem_names import visible_text
from tests.test_report_pages import SNAPSHOT, built, report_record, verify_static_export  # noqa: F401  (fixtures)

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "web" / "app" / "index.html"
LANDING_PATHS = [f"/{slug}/" for slug in (*report_pages.ECOSYSTEM_SLUGS.values(), *report_pages.LENS_SLUGS.values())]


def quiet(_line: str) -> None:
    pass


def _verify(db: Path, site: Path, record: dict) -> dict:
    return verify_static_export.verify_home(db, site, report=record, log=quiet)


def _ld(text: str) -> dict:
    return json.loads(re.search(r'<script type="application/ld\+json">\s*(.*?)\s*</script>', text, re.S).group(1))


def _copy_site(site: Path, tmp_path: Path) -> Path:
    target = tmp_path / "site"
    shutil.copytree(site, target)
    return target


def test_the_generated_home_is_verified_and_reads_without_javascript(built, report_record):
    site, db, *_ = built
    result = _verify(db, site, report_record)
    assert result["errors"] == [] and result["rows"] == home_pages.TOP_ROWS
    text = (site / "index.html").read_text(encoding="utf-8")
    graph = _ld(text)["@graph"]
    assert [n["@type"] for n in graph] == ["Person", "WebSite", "WebPage", "ItemList"]
    website = graph[1]
    assert website["alternateName"] == ["Porchlamp rankings"] and "potentialAction" not in website
    assert all(re.fullmatch(r"https://porchlamp\.org/projects/[a-z0-9._-]+/[a-z0-9._-]+/", i["url"]) for i in graph[3]["itemListElement"])
    # what a reader without JavaScript gets: the finding, the first page of rows, and the ten landing pages by address
    shown = visible_text(text)
    values = report_record["values"]
    assert f"More than 1 in {values['one_in']} active projects" in shown
    assert len(verify_static_export.keyed_items(text, "tr")) == home_pages.TOP_ROWS
    links = re.findall(r'href="([^"]*)"', text)
    assert all(path in links for path in LANDING_PATHS), [p for p in LANDING_PATHS if p not in links]
    assert not any(re.match(r"\.?/?\?(persona|eco|tab|repo)=", href) for href in links)
    # the machine-read files, generated beside it
    llms = (site / "llms.txt").read_text(encoding="utf-8")
    assert "Canonical Views" not in llms and "?persona=" not in llms and "?eco=" not in llms
    assert "Bus Factor" not in llms and "Deduplicated" not in llms
    methodology = json.loads((site / "data" / SNAPSHOT / "methodology.json").read_text(encoding="utf-8"))
    assert f"{len(methodology['parameters'])} parameters" in llms
    feed = (site / "feed.xml").read_text(encoding="utf-8")
    assert "Low Priority" not in feed and "?tab=" not in feed and "?persona=" not in feed


def test_the_generated_texts_state_the_snapshots_own_counts_and_rows(built):
    """The verifier re-generates with the same functions; this pins what they print to the export's data files."""
    site = built[0]
    data = site / "data" / SNAPSHOT
    counts = json.loads((data / "snapshot.json").read_text(encoding="utf-8"))["counts"]
    rows = json.loads((data / "leaderboard-balanced.json").read_text(encoding="utf-8"))["rows"][: home_pages.TOP_ROWS]
    llms = (site / "llms.txt").read_text(encoding="utf-8")
    assert (f"{counts['rated']:,} projects are ranked under the balanced lens; {counts['gated']:,} are gated and "
            f"{counts['unrated']:,} unrated") in llms
    assert re.findall(r"^\d+\. `([^`]+)`", llms, re.M) == [f"{r['owner']}/{r['name']}" for r in rows]
    feed = (site / "feed.xml").read_text(encoding="utf-8")
    assert f"{counts['total']:,} open-source projects measured" in feed
    home = (site / "index.html").read_text(encoding="utf-8")
    assert f'content="Porchlamp rankings: {counts["total"]:,} open-source' in home
    assert [i["url"] for i in _ld(home)["@graph"][3]["itemListElement"]] == [
        f"https://porchlamp.org/projects/{r['owner'].lower()}/{r['name'].lower()}/" for r in rows[: home_pages.TOP_LISTED]]


_DIGIT = re.compile(r"\d")
#: The finding's own form, "1 in {one_in}": its "1" is the wording of the claim, and the value is the slot.
_ONE_IN = re.compile(r"\b1 in (?=\{)")


def number_literals(text: str) -> list[str]:
    """The digits a reader would see in ``text`` (an HTML page's visible text, or a sentence), the "1 in {slot}" form
    of the finding aside."""
    return _DIGIT.findall(_ONE_IN.sub("", text))


def test_no_number_is_typed_into_the_home_template_or_the_finding_lines():
    template = TEMPLATE.read_text(encoding="utf-8")
    assert number_literals(visible_text(template)) == []
    assert "ld+json" not in template and "seo-fallback" not in template
    for name in ("FINDINGS_LINE", "WHY_FINDING_TEMPLATE"):
        assert number_literals(report_pages.copy_js_string(name)) == [], name
    # the check can fail: a planted count in the description, a planted share in the line
    planted = template.replace('content="Porchlamp rankings: open-source', 'content="Porchlamp rankings: 11,000 open-source', 1)
    assert planted != template and number_literals(visible_text(planted)) == ["1", "1", "0", "0", "0"]
    assert number_literals(report_pages.copy_js_string("FINDINGS_LINE").replace("{one_in}", "6", 1)) == ["1", "6"]
    assert number_literals(report_pages.copy_js_string("WHY_FINDING_TEMPLATE").replace("{deadline_days}", "14", 1)) == ["1", "4"]


def _plant(site: Path, rel: str, old: str, new: str) -> None:
    path = site / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, (rel, old)
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


#: Where each generated file states the snapshot's project count.
_COUNTS = {
    "llms.txt": r"(Porchlamp ranks )([\d,]+)( open-source)",
    "feed.xml": r"(: )([\d,]+)( open-source projects measured)",
    "index.html": r'(content="Porchlamp rankings: )([\d,]+)( open-source)',
}


@pytest.mark.parametrize("rel", sorted(_COUNTS))
def test_a_wrong_number_in_a_generated_file_is_named(built, report_record, tmp_path, rel):
    site, db, *_ = built
    copy_dir = _copy_site(site, tmp_path)
    found = re.search(_COUNTS[rel], (copy_dir / rel).read_text(encoding="utf-8"))
    wrong = f"{int(found.group(2).replace(',', '')) + 1:,}"
    _plant(copy_dir, rel, found.group(0), f"{found.group(1)}{wrong}{found.group(3)}")
    errors = _verify(db, copy_dir, report_record)["errors"]
    assert any(rel in e for e in errors), errors[:3]


@pytest.mark.parametrize("rel", ["llms.txt", "feed.xml"])
def test_an_address_in_a_generated_file_that_the_export_lacks_is_named(built, report_record, tmp_path, rel):
    site, db, *_ = built
    copy_dir = _copy_site(site, tmp_path)
    _plant(copy_dir, rel, "https://porchlamp.org/methodology/", "https://porchlamp.org/method/")
    errors = _verify(db, copy_dir, report_record)["errors"]
    assert any(f"{rel} links https://porchlamp.org/method/, a file of the export" in e for e in errors), errors[:3]


def test_a_wrong_finding_or_a_missing_row_on_the_home_is_named(built, report_record, tmp_path):
    site, db, *_ = built
    copy_dir = _copy_site(site, tmp_path)
    one_in = report_record["values"]["one_in"]
    _plant(copy_dir, "index.html", f'<span data-v="one_in">{one_in}</span>', f'<span data-v="one_in">{int(one_in) + 1}</span>')
    errors = _verify(db, copy_dir, report_record)["errors"]
    assert any("home findings line" in e and "'one_in'" in e for e in errors), errors[:3]

    copy_dir = _copy_site(site, tmp_path / "rows")
    text = (copy_dir / "index.html").read_text(encoding="utf-8")
    row = re.search(r'<tr data-owner="[^"]*" data-name="[^"]*".*?</tr>', text, re.S).group(0)
    (copy_dir / "index.html").write_text(text.replace(row, "", 1), encoding="utf-8")
    errors = _verify(db, copy_dir, report_record)["errors"]
    assert any("home table rows are the first" in e for e in errors), errors[:3]

    # a card that is no longer a card, a project's links gone, a landing page reached by a query address again
    def planted(name: str, change) -> list[str]:
        path = _copy_site(site, tmp_path / name) / "index.html"
        before = path.read_text(encoding="utf-8")
        after = change(before)
        assert after != before, name
        path.write_text(after, encoding="utf-8")
        return _verify(db, path.parent, report_record)["errors"]

    not_a_card = planted("card", lambda t: t.replace('class="leaderboard-card"', 'class="leaderboard-tile"', 1))
    assert any("home cards are the same rows" in e for e in not_a_card), not_a_card[:3]
    owner, name = verify_static_export.keyed_items(text, "tr")[0]
    unlinked = planted("link", lambda t: t.replace(f'href="/projects/{owner.lower()}/{name.lower()}/"', 'href="/"'))
    assert any(f"home links {owner}/{name}'s page" in e for e in unlinked), unlinked[:3]
    queried = planted("landing", lambda t: t.replace('href="/rust/"', 'href="/?eco=crates"'))
    assert any("home links the landing page /rust/" in e for e in queried), queried[:3]


def test_a_wrong_value_in_the_data_the_export_read_is_named(built, report_record, tmp_path):
    """The export writes llms.txt from the site's report.json: a report.json with another share gives an llms.txt that the
    verifier, which works the share out itself, refuses."""
    site, db, *_ = built
    copy_dir = _copy_site(site, tmp_path)
    data = copy_dir / "data" / SNAPSHOT
    report = json.loads((data / "report.json").read_text(encoding="utf-8"))
    report["values"]["share"] = f"{float(report['values']['share']) + 0.1:.1f}"
    load = lambda name: json.loads((data / name).read_text(encoding="utf-8"))  # noqa: E731
    facts = home_pages.home_facts(load("snapshot.json"), load("methodology.json"), report, load("leaderboard-balanced.json")["rows"])
    (copy_dir / "llms.txt").write_text(home_pages.llms_txt(facts), encoding="utf-8")
    errors = _verify(db, copy_dir, report_record)["errors"]
    assert any("llms.txt" in e for e in errors), errors[:3]


def test_the_home_json_ld_rules_each_fail_on_their_own_plant(built, report_record, tmp_path):
    site, db, *_ = built

    def planted(name: str, change) -> list[str]:
        copy_dir = _copy_site(site, tmp_path / name)
        text = (copy_dir / "index.html").read_text(encoding="utf-8")
        old = re.search(r'<script type="application/ld\+json">\s*(.*?)\s*</script>', text, re.S).group(1)
        ld = json.loads(old)
        change(ld)
        (copy_dir / "index.html").write_text(text.replace(old, json.dumps(ld), 1), encoding="utf-8")
        return _verify(db, copy_dir, report_record)["errors"]

    assert any("no SoftwareApplication" in e for e in planted("app", lambda ld: ld["@graph"].append({"@type": "SoftwareApplication"})))
    assert any("no search action" in e for e in planted("search", lambda ld: ld["@graph"][1].update(potentialAction={"@type": "SearchAction"})))

    def about_home(ld):
        ld["@graph"][2]["about"] = {"@id": "https://porchlamp.org/#dataset"}

    assert any("about names the report's Dataset" in e for e in planted("about", about_home))

    def old_address(ld):
        ld["@graph"][3]["itemListElement"][0]["url"] = "https://porchlamp.org/?repo=acme%2Fwidget"

    assert any("?repo=acme" in e and "has a page in the export" in e for e in planted("address", old_address))
