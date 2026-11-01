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


def test_publish_from_drop_workflow_enforces_supply_chain_and_secret_isolation() -> None:
    import re

    sha_pin_re = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+@[0-9a-f]{40}$")
    path = WORKFLOW_DIR / "publish_from_drop.yml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert doc.get("permissions") == {}, "publish_from_drop.yml must set top-level permissions: {}"

    job = doc["jobs"]["publish"]
    assert job.get("permissions") == {"contents": "read"}
    steps = job["steps"]

    for step in steps:
        name = step.get("name", "")
        if "uses" in step:
            uses = step["uses"]
            assert sha_pin_re.fullmatch(uses), f"step {name!r} must pin action to 40-hex commit SHA, got {uses!r}"
            if uses.startswith("actions/checkout@"):
                with_cfg = step.get("with") or {}
                assert with_cfg.get("persist-credentials") is False, (
                    f"checkout step {name!r} must set persist-credentials: false"
                )
                assert "PORCHLAMP_SITE_PUSH_TOKEN" not in str(with_cfg), (
                    f"checkout step {name!r} must never write PORCHLAMP_SITE_PUSH_TOKEN into .git/config"
                )
        if step.get("id") == "build":
            env_str = str(step.get("env") or {})
            assert "secrets." not in env_str, "build/verify step must run in a zero-secret environment"


def _publish_from_drop() -> dict:
    return yaml.safe_load((WORKFLOW_DIR / "publish_from_drop.yml").read_text(encoding="utf-8"))


def test_publish_from_drop_runs_on_dispatch_only_and_never_pushes_to_the_site_main_branch() -> None:
    """Drop-box decision D1-A/D1-D F6: the workflow is manual-only until merge 2 puts the schedule back (flip the
    first assertion then), and its only publish path is a review pull request; there is no `direct` push to main."""
    doc = _publish_from_drop()
    triggers = doc.get("on", doc.get(True))
    assert "schedule" not in triggers, "merge 1 ships the workflow manual-only; merge 2 restores the schedule"
    assert "workflow_dispatch" in triggers
    assert "publish_mode" not in (triggers["workflow_dispatch"].get("inputs") or {}), (
        "a dispatch must not be able to choose direct publishing"
    )
    steps = doc["jobs"]["publish"]["steps"]
    publish = [s for s in steps if "PORCHLAMP_SITE_PUSH_TOKEN" in str(s.get("env") or {})]
    assert len(publish) == 1, "exactly one step may hold the site push token"
    script = publish[0]["run"]
    assert "push origin main" not in script
    assert "PUBLISH_MODE" not in str(publish[0].get("env") or {}) and "PUBLISH_MODE" not in script
    assert "gh pr create" in script, "the review pull request path must stay"


def test_publish_from_drop_build_step_computes_its_own_as_of() -> None:
    """D2-A: the as_of is the earliest instant every guard accepts, worked out by publish_snapshot.py from the data. The
    workflow must not pass one (the old value was MAIN.done, which the as-of review showed shrinks every window)."""
    doc = _publish_from_drop()
    steps = {s.get("id"): s for s in doc["jobs"]["publish"]["steps"] if s.get("id")}
    build = steps["build"]
    assert "publish_snapshot.py" in build["run"]
    assert "--as-of" not in build["run"]
    assert "AS_OF" not in (build.get("env") or {})
    assert "as_of" not in steps["pull"]["run"].lower(), "the pull step must not read or emit an as_of"
    text = (WORKFLOW_DIR / "publish_from_drop.yml").read_text(encoding="utf-8")
    assert "outputs.as_of" not in text
