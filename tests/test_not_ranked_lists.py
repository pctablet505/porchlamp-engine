"""The not-ranked projects as lists by reason (``web/app/routes.js``'s presets of the not-ranked view,
``/not-ranked/<reason>/<n>/``), as the export writes them and ``scripts/verify_static_export.py`` proves them
(``verify_not_ranked_lists``): each reason's projects alphabetical, 200 a page, one sentence each with nothing beside
the name, a black hole's sentence with its own two numbers, and every not-ranked project in exactly one list.
"""
# ruff: noqa: F811  (the pytest fixtures are imported by name from the report tests and then used as arguments)
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from tests.test_report_pages import SNAPSHOT, _evidence_files, built, export_static, verify_static_export  # noqa: F401


@pytest.fixture(scope="module")
def report_values(built):
    """``(record, black-hole numbers)``: what :func:`verify_report` works out, as the verifier's later checks get them."""
    site, db, stats_path, *_ = built
    result = verify_static_export.verify_report(db, stats_path, site, evidence_files=_evidence_files(built), log=lambda _line: None)
    assert result["errors"] == [] and result["black_hole_numbers"], result["errors"][:3]
    return result["record"], result["black_hole_numbers"]


def _check(built, report_values, site: Path) -> dict:
    _site, db, _stats_path, stats, _holes = built
    record, numbers = report_values
    return verify_static_export.verify_not_ranked_lists(db, site, report=record, numbers=numbers, stats=stats, log=lambda _line: None)


def _copy(built, tmp_path: Path) -> Path:
    copy = tmp_path / "site"
    shutil.copytree(built[0], copy)
    return copy


def _first_row(page: Path) -> re.Match:
    found = re.search(r"<li\b[^>]*>(.*?)</li>", page.read_text(encoding="utf-8"), re.S)
    assert found, f"{page} lists no project"
    return found


def _rewrite(page: Path, old: str, new: str) -> None:
    text = page.read_text(encoding="utf-8")
    assert old in text, (page, old)
    page.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_every_not_ranked_project_is_in_one_list_once_in_its_own_sentence(built, report_values):
    result = _check(built, report_values, built[0])
    assert result["errors"] == [], result["errors"][:5]
    projects = verify_static_export.not_ranked_projects(built[1], SNAPSHOT)
    reasons = {route["fixed"]["reason"] for route in export_static.app_routes() if (route.get("fixed") or {}).get("reason")}
    assert {p["reason"] for p in projects} <= reasons  # every reason in the fixture has its list
    assert result["rows"] == len(projects) and result["pages"] >= len(reasons)


def test_a_label_beside_a_name_turns_the_check_red(built, report_values, tmp_path):
    site = _copy(built, tmp_path)
    page = site / "not-ranked" / "unanswered" / "1" / "index.html"
    row = _first_row(page)
    _rewrite(page, row.group(0), row.group(0).replace(row.group(1), '<span class="label">Black hole</span> ' + row.group(1), 1))
    errors = _check(built, report_values, site)["errors"]
    assert any(error.startswith("/not-ranked/unanswered/1/ row 1:") and "Black hole" in error for error in errors), errors[:5]


def test_a_wrong_number_in_a_row_turns_the_check_red(built, report_values, tmp_path):
    site = _copy(built, tmp_path)
    page = site / "not-ranked" / "unanswered" / "1" / "index.html"
    row = _first_row(page)
    assert "11 of 12 counted" in row.group(1), row.group(1)  # the fixture's black holes: 11 of 12 unanswered
    _rewrite(page, row.group(0), row.group(0).replace("11 of 12 counted", "12 of 12 counted", 1))
    errors = _check(built, report_values, site)["errors"]
    assert any(error.startswith("/not-ranked/unanswered/1/ row 1:") and "12 of 12" in error for error in errors), errors[:5]


def test_a_project_left_out_or_listed_twice_turns_the_check_red(built, report_values, tmp_path):
    site = _copy(built, tmp_path)
    page = site / "not-ranked" / "unanswered" / "1" / "index.html"
    row = _first_row(page)
    owner, name = re.search(r'data-owner="([^"]*)" data-name="([^"]*)"', row.group(0)).groups()
    _rewrite(page, row.group(0), "")  # left out
    other = next(p for p in sorted((site / "not-ranked").glob("*/1/index.html")) if p != page and "<li" in p.read_text(encoding="utf-8"))
    twice = _first_row(other)
    _rewrite(other, twice.group(0), twice.group(0) * 2)  # listed twice
    errors = _check(built, report_values, site)["errors"]
    assert any(error.startswith("not-ranked projects in no list") and name in error for error in errors), errors[:5]
    assert any(error.startswith("not-ranked projects listed more than once") for error in errors), errors[:5]


def test_the_static_black_hole_rows_carry_the_numbers_of_their_evidence(built):
    """The site's not-ranked list (what the static api.js answers from) holds each black hole's own numbers: the
    ``unanswered`` and ``population_n`` of the evidence its record carries in the site's repo files."""
    data = built[0] / "data" / SNAPSHOT
    evidence = {}
    for bucket in (data / "repos").glob("*.json"):
        for key, record in json.loads(bucket.read_text(encoding="utf-8")).items():
            found = record["detail"]["black_hole_evidence"]
            if found:
                evidence[tuple(key.split("/", 1))] = (found["unanswered"], found["population_n"])
    rows = [entry["row"] for entry in json.loads((data / "not-ranked-balanced.json").read_text(encoding="utf-8"))["rows"]]
    holes = {(r["owner"], r["name"]): (r["unanswered"], r["counted"]) for r in rows if r["gate_reason"] == "black_hole"}
    assert holes and holes == evidence
    assert all(r["unanswered"] is None and r["counted"] is None for r in rows if r["gate_reason"] != "black_hole")


def test_a_reason_list_has_a_page_for_each_full_two_hundred_rows_one_for_the_rest_and_none_without_projects():
    route = {"path": "/not-ranked/insufficient-evidence/{page}/", "fixed": {"reason": "insufficient_evidence", "persona": "balanced", "page_size": 200}}

    def lists(rows: int) -> dict:
        # the engine's spelling, as the API sends it, and another reason beside it that the list must not count
        entries = [{"group": "unrated", "row": {"gate_reason": None, "unrated_reason": "unrated: insufficient_evidence"}}] * rows
        entries += [{"group": "gated", "row": {"gate_reason": "archived", "unrated_reason": None}}] * 300
        return {"balanced": {"rows": entries}}

    assert [len(export_static.route_instances(route, {}, lists(n))) for n in (0, 1, 200, 201, 400, 401)] == [0, 1, 1, 2, 2, 3]
    assert export_static.route_instances(route, {}, lists(201)) == ["/not-ranked/insufficient-evidence/1/", "/not-ranked/insufficient-evidence/2/"]
