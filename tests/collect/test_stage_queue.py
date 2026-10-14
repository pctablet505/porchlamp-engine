"""Unit tests for porchlamp.collect.stage_queue."""

from __future__ import annotations

from typing import Any

from porchlamp.collect.stage_queue import (
    Stage,
    StageTask,
    plan_repo_stages,
    run_stage_funnel,
)


def test_plan_repo_stages_ordering_and_omission() -> None:
    # All stages present
    dirty = {"github_meta", "tree", "prs", "governance", "releases"}
    tasks = plan_repo_stages("owner", "repo", "pypi", dirty)

    stages = [t.stage for t in tasks]
    assert stages == [
        Stage.STAGE_1_CHEAP_GATES,
        Stage.STAGE_2_PR_WINDOW,
        Stage.STAGE_3_DEEP_SIGNALS,
    ]

    # Verify collectors inside each task
    assert tasks[0].collectors == ("github_meta", "tree")
    assert tasks[1].collectors == ("prs",)
    assert tasks[2].collectors == ("governance", "releases")

    # Omitting empty stages (e.g. only PR window)
    pr_only = plan_repo_stages("owner", "repo", "npm", {"pr_window"})
    assert len(pr_only) == 1
    assert pr_only[0].stage == Stage.STAGE_2_PR_WINDOW
    assert pr_only[0].collectors == ("pr_window",)


def test_stage_funnel_halt_after_stage1_ineligible() -> None:
    executed_stages: list[Stage] = []

    def mock_executor(task: StageTask) -> dict[str, Any]:
        executed_stages.append(task.stage)
        return {f"{task.stage.value}_executed": True}

    def check_ineligible(owner: str, name: str) -> str | None:
        if name == "archived-repo":
            return "archived"
        return None

    dirty = {"github_meta", "prs", "governance"}
    repos = [("owner", "archived-repo", "pypi", dirty)]

    results = run_stage_funnel(
        repos,
        mock_executor,
        is_ineligible_after_stage1=check_ineligible,
    )

    repo_res = results["owner__archived-repo"]
    assert repo_res.get("stage1_cheap_gates_executed") is True
    assert repo_res.get("stage2_pr_window_executed") is None
    assert repo_res.get("stage3_deep_signals_executed") is None
    assert repo_res.get("halted_stage") == Stage.STAGE_1_CHEAP_GATES.value
    assert repo_res.get("halted_reason") == "archived"

    # Only Stage 1 was executed
    assert executed_stages == [Stage.STAGE_1_CHEAP_GATES]


def test_stage_funnel_halt_after_stage2_black_hole() -> None:
    executed_stages: list[Stage] = []

    def mock_executor(task: StageTask) -> dict[str, Any]:
        executed_stages.append(task.stage)
        return {f"{task.stage.value}_executed": True}

    def check_black_hole(owner: str, name: str) -> bool:
        return name == "ghost-repo"

    dirty = {"github_meta", "prs", "governance", "releases"}
    repos = [("owner", "ghost-repo", "cargo", dirty)]

    results = run_stage_funnel(
        repos,
        mock_executor,
        is_black_hole_after_stage2=check_black_hole,
    )

    repo_res = results["owner__ghost-repo"]
    assert repo_res.get("stage1_cheap_gates_executed") is True
    assert repo_res.get("stage2_pr_window_executed") is True
    assert repo_res.get("stage3_deep_signals_executed") is None
    assert repo_res.get("halted_stage") == Stage.STAGE_2_PR_WINDOW.value
    assert repo_res.get("halted_reason") == "black_hole"

    # Stage 1 and Stage 2 ran, but Stage 3 deep signals did NOT run
    assert executed_stages == [Stage.STAGE_1_CHEAP_GATES, Stage.STAGE_2_PR_WINDOW]


def test_stage_funnel_full_success() -> None:
    executed_stages: list[Stage] = []

    def mock_executor(task: StageTask) -> dict[str, Any]:
        executed_stages.append(task.stage)
        return {f"{task.stage.value}_done": True}

    dirty = {"tree", "pr_window", "commits_365d"}
    repos = [("good_org", "healthy_project", "pypi", dirty)]

    results = run_stage_funnel(
        repos,
        mock_executor,
        is_ineligible_after_stage1=lambda o, n: None,
        is_black_hole_after_stage2=lambda o, n: False,
    )

    repo_res = results["good_org__healthy_project"]
    assert repo_res.get("stage1_cheap_gates_done") is True
    assert repo_res.get("stage2_pr_window_done") is True
    assert repo_res.get("stage3_deep_signals_done") is True
    assert "halted_stage" not in repo_res

    assert executed_stages == [
        Stage.STAGE_1_CHEAP_GATES,
        Stage.STAGE_2_PR_WINDOW,
        Stage.STAGE_3_DEEP_SIGNALS,
    ]
