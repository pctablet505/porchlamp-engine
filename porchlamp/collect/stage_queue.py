"""4-stage collection queue seam and gating funnel.

Organizes repository collection into an ordered pipeline:
- Stage 0: Fingerprint & discovery
- Stage 1: Cheap gates (github_meta, tree, readme, contributing, commits_180d)
- Stage 2: PR window (prs, pr_window)
- Stage 3: Deep signals (governance, commits_365d, author_history, releases, runs, etc.)

Halts downstream collection early when cheap gates fail or when a repository is
confirmed to be a black hole.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence, Set as AbstractSet
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class Stage(str, Enum):
    """Collection stages in order of execution."""

    STAGE_0_FINGERPRINT = "stage0_fingerprint"
    STAGE_1_CHEAP_GATES = "stage1_cheap_gates"
    STAGE_2_PR_WINDOW = "stage2_pr_window"
    STAGE_3_DEEP_SIGNALS = "stage3_deep_signals"


#: Collectors associated with Stage 0 (fingerprint & discovery).
STAGE_0_COLLECTORS: tuple[str, ...] = ("fingerprint",)

#: Collectors associated with Stage 1 (cheap gate facts).
STAGE_1_COLLECTORS: tuple[str, ...] = (
    "github_meta",
    "tree",
    "readme",
    "contributing",
    "commits_180d",
)

#: Collectors associated with Stage 2 (PR responsiveness window).
STAGE_2_COLLECTORS: tuple[str, ...] = (
    "prs",
    "pr_window",
)


@dataclass(frozen=True)
class StageTask:
    """One task to execute within a collection stage."""

    repo_key: str
    owner: str
    name: str
    ecosystem: str
    stage: Stage
    collectors: tuple[str, ...]


def plan_repo_stages(
    owner: str,
    name: str,
    ecosystem: str,
    dirty_collectors: AbstractSet[str],
    *,
    skip_deep_on_black_hole: bool = True,
) -> list[StageTask]:
    """Split dirty collectors into ordered StageTasks, omitting empty stages."""
    repo_key = f"{owner}__{name}"
    tasks: list[StageTask] = []

    s0 = tuple(c for c in STAGE_0_COLLECTORS if c in dirty_collectors)
    if s0:
        tasks.append(
            StageTask(
                repo_key=repo_key,
                owner=owner,
                name=name,
                ecosystem=ecosystem,
                stage=Stage.STAGE_0_FINGERPRINT,
                collectors=s0,
            )
        )

    s1 = tuple(c for c in STAGE_1_COLLECTORS if c in dirty_collectors)
    if s1:
        tasks.append(
            StageTask(
                repo_key=repo_key,
                owner=owner,
                name=name,
                ecosystem=ecosystem,
                stage=Stage.STAGE_1_CHEAP_GATES,
                collectors=s1,
            )
        )

    s2 = tuple(c for c in STAGE_2_COLLECTORS if c in dirty_collectors)
    if s2:
        tasks.append(
            StageTask(
                repo_key=repo_key,
                owner=owner,
                name=name,
                ecosystem=ecosystem,
                stage=Stage.STAGE_2_PR_WINDOW,
                collectors=s2,
            )
        )

    known_prior = set(STAGE_0_COLLECTORS) | set(STAGE_1_COLLECTORS) | set(STAGE_2_COLLECTORS)
    s3 = tuple(sorted(c for c in dirty_collectors if c not in known_prior))
    if s3:
        tasks.append(
            StageTask(
                repo_key=repo_key,
                owner=owner,
                name=name,
                ecosystem=ecosystem,
                stage=Stage.STAGE_3_DEEP_SIGNALS,
                collectors=s3,
            )
        )

    return tasks


def run_stage_funnel(
    tasks_by_repo: Sequence[tuple[str, str, str, AbstractSet[str]]],
    executor_fn: Callable[[StageTask], Mapping[str, Any]],
    *,
    is_ineligible_after_stage1: Optional[Callable[[str, str], Optional[str]]] = None,
    is_black_hole_after_stage2: Optional[Callable[[str, str], bool]] = None,
) -> dict[str, dict[str, Any]]:
    """Execute the multi-stage collection funnel across repositories.

    Executes Stage 1 -> checks is_ineligible_after_stage1 (halting early if gated)
    -> executes Stage 2 (pr_window) -> checks is_black_hole_after_stage2 (halting
    early if confirmed black_hole) -> executes Stage 3 (deep_signals).
    """
    results: dict[str, dict[str, Any]] = {}

    for owner, name, ecosystem, dirty_collectors in tasks_by_repo:
        repo_key = f"{owner}__{name}"
        repo_results: dict[str, Any] = {}
        results[repo_key] = repo_results

        tasks = plan_repo_stages(owner, name, ecosystem, dirty_collectors)

        for task in tasks:
            if task.stage == Stage.STAGE_0_FINGERPRINT:
                res = executor_fn(task)
                if res:
                    repo_results.update(res)

            elif task.stage == Stage.STAGE_1_CHEAP_GATES:
                res = executor_fn(task)
                if res:
                    repo_results.update(res)
                if is_ineligible_after_stage1 is not None:
                    reason = is_ineligible_after_stage1(owner, name)
                    if reason:
                        repo_results["halted_stage"] = Stage.STAGE_1_CHEAP_GATES.value
                        repo_results["halted_reason"] = reason
                        break

            elif task.stage == Stage.STAGE_2_PR_WINDOW:
                res = executor_fn(task)
                if res:
                    repo_results.update(res)
                if is_black_hole_after_stage2 is not None and is_black_hole_after_stage2(owner, name):
                    repo_results["halted_stage"] = Stage.STAGE_2_PR_WINDOW.value
                    repo_results["halted_reason"] = "black_hole"
                    break

            elif task.stage == Stage.STAGE_3_DEEP_SIGNALS:
                res = executor_fn(task)
                if res:
                    repo_results.update(res)

    return results
