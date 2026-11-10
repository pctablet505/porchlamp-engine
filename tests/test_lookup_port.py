"""The lookup's rule constants and readings stay the engine's.

`web/app/lookup.js` reads one pull request's answer in the visitor's browser. It can only claim to apply the report's own
rules if what it mirrors still equals the Python it names, so this file parses those constants out of the JS source and
compares them with the engine's. `commentKind` is code rather than data, so it is pinned by a shared case file produced by
the Python function: this file re-checks the Python side against it and `web/app/tests/lookup.test.mjs` checks the JS side.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from porchlamp.core import constants, gates
from porchlamp.snapshot import first_pull_requests as fpr

WEB_APP = Path(__file__).resolve().parent.parent / "web" / "app"
LOOKUP_JS = (WEB_APP / "lookup.js").read_text(encoding="utf-8")
CASES = json.loads((WEB_APP / "tests" / "cases" / "comment_kinds.json").read_text(encoding="utf-8"))


def js_regex(name: str) -> tuple[str, str]:
    match = re.search(rf"export const {name} = /(.*)/([a-z]*);", LOOKUP_JS)
    assert match, f"lookup.js has no regular expression {name}"
    return match.group(1), match.group(2)


def js_number(name: str) -> int:
    match = re.search(rf"export const {name} = (\d+);", LOOKUP_JS)
    assert match, f"lookup.js has no number {name}"
    return int(match.group(1))


def test_the_comment_patterns_are_the_engines_word_for_word() -> None:
    for name, pattern in (("TOOL_COMMENT_RE", fpr.TOOL_COMMENT_RE), ("ASK_COMMENT_RE", fpr.ASK_COMMENT_RE), ("MENTION_RE", fpr.MENTION_RE)):
        source, flags = js_regex(name)
        assert flags == "i" and pattern.flags & re.IGNORECASE, name
        assert source.replace("\\/", "/") == pattern.pattern, name


def test_the_numbers_are_the_engines() -> None:
    assert js_number("SUBSTANTIVE_COMMENT_MIN_CHARS") == constants.SUBSTANTIVE_COMMENT_MIN_CHARS
    assert js_number("SUBSTANTIVE_COMMENT_CJK_WEIGHT") == constants.SUBSTANTIVE_COMMENT_CJK_WEIGHT
    assert js_number("ASK_AFTER_HOURS") == fpr.ASK_AFTER_HOURS


def test_the_maintainer_associations_are_the_engines() -> None:
    match = re.search(r"export const MAINTAINER_ASSOCIATIONS = Object\.freeze\(\[(.*?)\]\);", LOOKUP_JS)
    assert match
    assert set(re.findall(r'"([^"]*)"', match.group(1))) == set(gates.MAINTAINER_ASSOCIATIONS)


def test_the_shared_comment_cases_are_what_the_engine_says() -> None:
    assert len(CASES) >= 15, "the case file must carry every kind several times"
    assert {case["kind"] for case in CASES} == {"tool", "ask", "mention", "other"}
    for case in CASES:
        assert fpr.comment_kind(case["text"]) == case["kind"], case["text"]


def test_the_comment_length_cases_are_what_the_engine_says() -> None:
    lengths = json.loads((WEB_APP / "tests" / "cases" / "comment_lengths.json").read_text(encoding="utf-8"))
    assert len(lengths) >= 5
    for case in lengths:
        assert gates.comment_length(case["text"]) == case["length"], case["text"]
