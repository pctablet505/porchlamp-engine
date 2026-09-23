"""The CI workflow files must be valid YAML whose every step runs something.

GitHub cannot run a workflow it cannot parse, and it reports that only as an
instant failure: every push from afcb871 (2026-09-12) to 2026-09-23 "failed"
in 0 s with "a workflow file issue" and no test ever ran, because two step
names held an unquoted ``: `` that YAML reads as a second mapping key. CI
cannot catch its own unparseable file, so the check lives in the suite that
runs locally. A missing PyYAML is an import error here, not a skip: a guard
that silently skips is the failure mode it exists to prevent.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOW_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"
WORKFLOWS = sorted(WORKFLOW_DIR.glob("*.y*ml"))


def test_there_are_workflows_to_check() -> None:
    assert WORKFLOWS, f"no workflow files under {WORKFLOW_DIR}: the parse check would pass vacuously"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_parses_and_every_step_runs_something(path: Path) -> None:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))  # a syntax error raises with its line and column
    assert isinstance(doc, dict), f"{path.name}: not a mapping"
    triggers = doc.get("on", doc.get(True))  # PyYAML (YAML 1.1) reads the bare key `on` as True
    assert triggers, f"{path.name}: no triggers"
    jobs = doc.get("jobs")
    assert isinstance(jobs, dict) and jobs, f"{path.name}: no jobs"
    for job_name, job in jobs.items():
        steps = job.get("steps")
        assert isinstance(steps, list) and steps, f"{path.name}: job {job_name!r} has no steps"
        for index, step in enumerate(steps):
            assert ("uses" in step) != ("run" in step), (
                f"{path.name}: job {job_name!r} step {index} ({step.get('name')!r}) needs exactly one of uses/run"
            )
