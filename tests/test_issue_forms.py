"""The issue forms in ``.github/ISSUE_TEMPLATE/``: what a reporter sees on GitHub's "New issue" page."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / ".github" / "ISSUE_TEMPLATE"


def _forms() -> dict[str, dict]:
    forms = {
        path.name: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in sorted(TEMPLATES.glob("*.yml"))
        if path.name != "config.yml"
    }
    assert forms, "no issue forms found"
    return forms


def test_no_form_makes_a_choice_for_the_reporter():
    """GitHub opens a required dropdown with no default on its first option, so a report sent without touching it
    claims that option. A dropdown is optional (GitHub offers "None") or names its default."""
    dropdowns = 0
    for name, form in _forms().items():
        for element in form["body"]:
            if element["type"] != "dropdown":
                continue
            dropdowns += 1
            required = (element.get("validations") or {}).get("required", False)
            assert not required or "default" in element["attributes"], (
                f"{name}: {element['attributes']['label']!r} opens on its first option"
            )
    assert dropdowns, "the forms' dropdowns are read"


def test_the_chooser_offers_one_way_to_report_a_vulnerability():
    """With private vulnerability reporting on, GitHub adds its own "Report a security vulnerability" entry to the
    chooser, so the config links no second way to the advisories page."""
    config = yaml.safe_load((TEMPLATES / "config.yml").read_text(encoding="utf-8"))
    links = [link["url"] for link in config.get("contact_links") or []]
    assert links, "the config's contact links are read"
    assert [url for url in links if "/security/advisories" in url] == [], links
