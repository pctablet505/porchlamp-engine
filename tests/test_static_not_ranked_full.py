"""The static site serves the whole Not-ranked list, not the API's first ``max_rows``.

MEASURED defect (2026-10-12, live site): ``fetchNotRanked`` copies the API's cut to the first 1,000 rows, the page
filters and pages that list on the client, so 6,679 of 7,679 not-ranked repositories (golang/go, kubernetes/kubernetes,
zitadel, ...) could never be found, and nothing on the page said so. ``fetchNotRanked`` keeps mirroring the API (the
verifier pins that); ``fetchNotRankedFull`` is what the static page calls.

The test builds a ten-row site with ``max_rows`` 3 and runs the real ``web/static/api.js`` under Node.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GROUPS = ("gated", "gated", "unrated", "gated", "unrated", "gated", "gated", "unrated", "gated", "gated")
ECOSYSTEMS = ("npm", "go", "crates", "systems", "npm", "go", "crates", "systems", "npm", "go")


def _verify_module():
    spec = importlib.util.spec_from_file_location("verify_static_export", ROOT / "scripts" / "verify_static_export.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rows() -> list[dict]:
    return [
        {
            "group": group,
            "row": {
                "owner": f"owner{i:02d}",
                "name": f"repo{i:02d}",
                "ecosystem": eco,
                "gate_reason": "archived" if group == "gated" else None,
                "unrated_reason": "no_external_workflow" if group == "unrated" else None,
                "persona_gate_reason": None,
                "coverage": 0.0,
            },
        }
        for i, (group, eco) in enumerate(zip(GROUPS, ECOSYSTEMS))
    ]


@pytest.fixture(scope="module")
def answers(tmp_path_factory):
    verify = _verify_module()
    node = verify.find_node()
    if node is None:
        if os.environ.get("PORCHLAMP_ALLOW_MISSING_NODE") == "1":
            pytest.skip("no Node.js interpreter found (PORCHLAMP_ALLOW_MISSING_NODE=1)")
        pytest.fail("no Node.js interpreter found on PATH, in PORCHLAMP_NODE_BIN, or in ~/.local/opt/node-*")
    site = tmp_path_factory.mktemp("site")
    shutil.copy2(ROOT / "web" / "static" / "api.js", site / "api.js")
    (site / "data-version.js").write_text('export const DATA_VERSION = "t";\n')
    data = site / "data" / "t"
    data.mkdir(parents=True)
    counts = {"rated": 0, "gated": 7, "unrated": 3, "total": 10}
    (data / "snapshot.json").write_text(json.dumps({"counts": counts, "persona_visible_counts": {"balanced": 0}}))
    (data / "not-ranked-balanced.json").write_text(
        json.dumps({"persona": "balanced", "max_rows": 3, "counts": counts, "rows": _rows()})
    )
    requests = [
        {"fn": "fetchNotRanked", "args": [{"persona": "balanced"}]},
        {"fn": "fetchNotRankedFull", "args": [{"persona": "balanced"}]},
        {"fn": "fetchNotRankedFull", "args": [{"persona": "balanced", "q": "REPO09"}]},
        {"fn": "fetchNotRankedFull", "args": [{"persona": "balanced", "eco": "systems"}]},
    ]
    (site / "requests.json").write_text(json.dumps(requests))
    subprocess.run(
        [node, str(ROOT / "scripts" / "static_parity.mjs"), str(site), str(site / "requests.json"), str(site / "out.json")],
        check=True,
    )
    return json.loads((site / "out.json").read_text())


def _rows_of(answer: dict) -> int:
    data = answer["data"]
    return len(data["gated"]) + len(data["unrated"]) + len(data["persona_hidden"])


def test_the_api_mirror_still_cuts_at_max_rows(answers):
    cut = answers[0]
    assert cut["ok"] and _rows_of(cut) == 3 and cut["data"]["truncated"] is True


def test_the_full_list_has_every_row_split_by_group(answers):
    full = answers[1]
    assert full["ok"], full
    assert _rows_of(full) == 10
    assert len(full["data"]["gated"]) == 7 and len(full["data"]["unrated"]) == 3
    assert full["data"]["truncated"] is False
    assert full["data"]["counts"] == {"rated": 0, "gated": 7, "unrated": 3, "total": 10}


def test_a_search_reaches_a_row_beyond_the_cut(answers):
    found = answers[2]
    assert found["ok"], found
    names = [r["name"] for g in ("gated", "unrated") for r in found["data"][g]]
    assert names == ["repo09"]


def test_the_ecosystem_filter_runs_over_the_whole_list(answers):
    systems = answers[3]
    assert systems["ok"], systems
    names = sorted(r["name"] for g in ("gated", "unrated") for r in systems["data"][g])
    assert names == ["repo03", "repo07"]
