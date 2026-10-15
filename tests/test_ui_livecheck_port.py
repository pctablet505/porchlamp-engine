"""The live check's rule constants and readings stay the engine's.

`web/v2/livecheck.js` runs D-GATE-1..4 and D-GATE-6 in the visitor's browser
(06 §3 C, "Repositories outside the snapshot: live check"). It can only claim
to apply the snapshot's own rules if every constant it mirrors still equals
the Python one it names, so this file parses those constants out of the JS
source and compares them with the engine's -- a drift on either side fails
here, not silently in a visitor's browser.

The readings that are code rather than data (the SPDX expression parser, the
bot rule, the manifest/licence-file/marker path rules) are pinned by shared
case files under `web/v2/tests/cases/`, produced by the Python functions:
this file re-checks the Python side against them and
`web/v2/tests/livecheck.test.mjs` checks the JS side against the same files.
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

from porchlamp.collect import github_graphql
from porchlamp.core import constants, gates
from porchlamp.core import methodology as methodology_module
from porchlamp.core.commit_domains import KNOWN_BOT_LOGINS, CommitActor, is_bot
from porchlamp.derive import gate_evidence
from porchlamp.serve.security import SECURITY_HEADERS

WEB_V2 = Path(__file__).resolve().parent.parent / "web" / "v2"
LIVECHECK_JS = (WEB_V2 / "livecheck.js").read_text(encoding="utf-8")
COPY_JS = (WEB_V2 / "copy.js").read_text(encoding="utf-8")
INDEX_HTML = (WEB_V2 / "index.html").read_text(encoding="utf-8")
CASES = WEB_V2 / "tests" / "cases"


def js_string_list(name: str) -> list[str]:
    """`export const NAME = Object.freeze([ "a", "b", ... ]);` -> ["a", "b", ...]."""
    match = re.search(rf"export const {name} = Object\.freeze\(\[(.*?)\]\);", LIVECHECK_JS, re.DOTALL)
    assert match, f"livecheck.js has no frozen string list {name}"
    return re.findall(r'"([^"]*)"', match.group(1))


def js_number(name: str) -> int:
    match = re.search(rf"export const {name} = (\d+);", LIVECHECK_JS)
    assert match, f"livecheck.js has no numeric constant {name}"
    return int(match.group(1))


def js_string(name: str) -> str:
    match = re.search(rf'export const {name} = "([^"]*)";', LIVECHECK_JS)
    assert match, f"livecheck.js has no string constant {name}"
    return match.group(1)


def csp_directive(policy: str, directive: str) -> str:
    for part in policy.split(";"):
        tokens = part.strip().split(None, 1)
        if tokens and tokens[0] == directive:
            return tokens[1] if len(tokens) > 1 else ""
    raise AssertionError(f"no {directive} in {policy!r}")


class TestMirroredConstants:
    def test_manifest_rules(self) -> None:
        assert sorted(js_string_list("MANIFEST_BASENAMES")) == sorted(gate_evidence.RECOGNIZED_MANIFEST_BASENAMES)
        assert js_string_list("MANIFEST_PREFIXES") == list(gate_evidence.RECOGNIZED_MANIFEST_PREFIXES)
        assert js_string_list("MANIFEST_SUFFIXES") == list(gate_evidence.RECOGNIZED_MANIFEST_SUFFIXES)
        assert js_number("MANIFEST_MAX_DEPTH") == gate_evidence.MANIFEST_MAX_DEPTH

    def test_osi_approved_licenses(self) -> None:
        js = js_string_list("OSI_APPROVED_LICENSES")
        assert len(js) == len(set(js)), "duplicate id in the JS list"
        assert set(js) == set(constants.OSI_APPROVED_LICENSES)

    def test_bot_logins_match_both_engine_lists(self) -> None:
        js = set(js_string_list("KNOWN_BOT_LOGINS"))
        assert js == set(KNOWN_BOT_LOGINS)
        assert js == set(github_graphql.BOT_LOGINS)

    def test_external_associations_match_both_engine_lists(self) -> None:
        js = set(js_string_list("EXTERNAL_ASSOCIATIONS"))
        assert js == set(github_graphql.EXTERNAL_ASSOCIATIONS)
        assert js == set(gate_evidence._EXTERNAL_ASSOCIATIONS)

    def test_undetermined_spdx_is_the_engines_minus_none(self) -> None:
        # null stands for None in the port: a repository with no licence object.
        assert set(js_string_list("UNDETERMINED_SPDX")) == {v for v in gates.UNDETERMINED_SPDX if v is not None}
        assert None in gates.UNDETERMINED_SPDX

    def test_windows(self) -> None:
        assert js_number("PR_WINDOW_DAYS") == constants.PR_WINDOW_DAYS
        assert js_number("VITALITY_WINDOW_DAYS") == constants.GATE4_VITALITY_WINDOW_DAYS
        assert js_number("DECISION_WINDOW_DAYS") == constants.GATE6_DECISION_WINDOW_DAYS

    def test_off_platform_marker(self) -> None:
        marker = js_string("OFF_PLATFORM_MARKER")
        assert marker == marker.lower(), "compared against lower-cased paths, so it must be lower case"
        assert f'"{marker}"' in inspect.getsource(gate_evidence._has_off_platform_marker)

    def test_gate_ids_and_names(self) -> None:
        assert js_string_list("LIVE_GATE_IDS") == list(gates.GATE_DECISION_IDS)
        block = re.search(r"export const LIVE_GATE_NAMES = Object\.freeze\(\{(.*?)\}\);", COPY_JS, re.DOTALL)
        assert block, "copy.js has no LIVE_GATE_NAMES"
        js_names = dict(re.findall(r'"(D-GATE-\d)": "([^"]*)"', block.group(1)))
        engine_names = {g["id"]: g["name"] for g in methodology_module._gate_entries() if g["id"] in gates.GATE_DECISION_IDS}
        assert js_names == engine_names


class TestSharedCases:
    def test_spdx_cases_hold_for_the_engine(self) -> None:
        cases = json.loads((CASES / "spdx_osi.json").read_text(encoding="utf-8"))["cases"]
        assert len(cases) >= 30
        for case in cases:
            expression = case["expression"]
            assert gates._is_parseable_spdx_expression(expression) is case["parseable"], expression
            assert gates._is_osi_approved_expression(expression) is case["osi"], expression

    def test_gate_rule_cases_hold_for_the_engine(self) -> None:
        doc = json.loads((CASES / "gate_rules.json").read_text(encoding="utf-8"))
        for case in doc["bots"]:
            actor = CommitActor(login=case["login"], email=case["email"], actor_type=case["type"])
            assert is_bot(actor) is case["expected"], case
        for case in doc["manifests"]:
            assert gate_evidence._has_recognized_manifest(case["paths"]) is case["expected"], case
        for case in doc["license_files"]:
            assert gate_evidence._has_license_file(case["paths"]) is case["expected"], case
        for case in doc["markers"]:
            assert gate_evidence._has_off_platform_marker(case["paths"]) is case["expected"], case
        # Each section exercises both outcomes, or it pins nothing.
        for section in ("bots", "manifests", "license_files", "markers"):
            assert {case["expected"] for case in doc[section]} == {True, False}, section


class TestContentSecurityPolicy:
    """The live check's one external origin is allowed in both places a CSP is set, and nothing else is."""

    def test_server_headers_allow_only_the_github_api(self) -> None:
        policy = SECURITY_HEADERS["Content-Security-Policy"]
        assert csp_directive(policy, "connect-src") == f"'self' {js_string('LIVE_API_BASE')}"

    def test_static_page_meta_allows_only_the_github_api(self) -> None:
        match = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]*)">', INDEX_HTML)
        assert match, "index.html has no CSP meta"
        policy = match.group(1)
        assert csp_directive(policy, "connect-src") == f"'self' {js_string('LIVE_API_BASE')}"
        assert csp_directive(policy, "default-src") == "'self'"
        # No inline script and no eval: index.html has no inline <script> or handlers to need them.
        assert csp_directive(policy, "script-src") == "'self'"
        assert csp_directive(policy, "object-src") == "'none'"
        assert csp_directive(policy, "base-uri") == "'self'"

    def test_static_page_has_no_inline_script_the_meta_would_block(self) -> None:
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", INDEX_HTML), "inline <script> would be blocked by script-src 'self'"
        assert not re.search(r"\son[a-z]+\s*=", INDEX_HTML), "inline event handler would be blocked by script-src 'self'"
