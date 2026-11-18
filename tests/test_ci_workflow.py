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
import math
import re
import shlex

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


_SHA_PIN_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+@[0-9a-f]{40}$")
_RAW_USES_LINE_RE = re.compile(r"^\s*(?:-\s*)?uses:\s*([A-Za-z0-9._-]+/[A-Za-z0-9._/-]+@[0-9a-f]{40})\s*(?:#\s*(.+))?$")
_PINNED_PKG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*==\d+(?:\.\d+)*(?:[A-Za-z0-9.+-]*)?$")


def _shell_commands(script: str) -> list[list[str]]:
    """Tokenize a bash `run:` script into individual commands while stripping `#` comments."""
    lex = shlex.shlex(script, posix=True, punctuation_chars=True)
    lex.whitespace_split = False
    lex.commenters = "#"
    # Preserve newline as a command separator
    lex.whitespace = " \t\r"
    cmds: list[list[str]] = []
    cur: list[str] = []
    for tok in lex:
        if tok in ("\n", ";", "&&", "||", "|", "&"):
            if cur:
                cmds.append(cur)
                cur = []
        else:
            cur.append(tok)
    if cur:
        cmds.append(cur)
    return cmds


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_all_workflows_enforce_supply_chain_permissions_and_checkout(path: Path) -> None:
    """Every workflow under `.github/workflows/*.y*ml` must pin every action to a 40-hex commit SHA
    with a version comment (`# v...`), declare minimal top-level and job-level permissions, and set
    `persist-credentials: false` strictly on `actions/checkout` steps."""
    raw_text = path.read_text(encoding="utf-8")
    doc = yaml.safe_load(raw_text)

    assert doc.get("permissions") in ({"contents": "read"}, {}), (
        f"{path.name} must declare minimal top-level permissions, got {doc.get('permissions')!r}"
    )

    for line_no, raw_line in enumerate(raw_text.splitlines(), 1):
        stripped = raw_line.lstrip()
        if stripped.startswith("#"):
            continue
        if re.match(r"^(?:-\s*)?uses:\s*", stripped):
            m = _RAW_USES_LINE_RE.match(raw_line)
            assert m is not None, f"{path.name}:{line_no} `uses:` must be a 40-hex SHA pin: {raw_line.strip()!r}"
            comment = (m.group(2) or "").strip()
            assert re.match(r"^v\d+", comment), (
                f"{path.name}:{line_no} pinned action must carry `# v<ver>` comment: {raw_line.strip()!r}"
            )

    for job_name, job in doc["jobs"].items():
        job_perms = job.get("permissions")
        assert job_perms is None or job_perms in ({"contents": "read"}, {}), (
            f"{path.name} job {job_name!r} must not escalate permissions, got {job_perms!r}"
        )
        for step in job["steps"]:
            name = step.get("name", "")
            if "uses" not in step:
                continue
            uses = step["uses"]
            assert _SHA_PIN_RE.fullmatch(uses), (
                f"{path.name} job {job_name!r} step {name!r} must pin action to 40-hex SHA, got {uses!r}"
            )
            with_cfg = step.get("with") or {}
            if uses.lower().startswith("actions/checkout@"):
                assert with_cfg.get("persist-credentials") is False, (
                    f"{path.name} checkout step {name!r} must set persist-credentials: false"
                )
            else:
                assert "persist-credentials" not in with_cfg, (
                    f"{path.name} non-checkout step {name!r} must not set persist-credentials"
                )


def _assert_pip_install_pinned(cmd: list[str], step_name: str) -> None:
    """Assert every package spec in a `pip install` command (excluding flags & local sdist installs) uses `==`."""
    try:
        idx = cmd.index("install")
    except ValueError:
        return
    skip_next = False
    value_flags = {"-r", "--requirement", "-c", "--constraint", "-e", "--editable", "-t", "--target", "--find-links", "-f", "--index-url", "-i", "--extra-index-url"}
    pkgs: list[str] = []
    for tok in cmd[idx + 1 :]:
        if skip_next:
            skip_next = False
            continue
        if tok in value_flags:
            skip_next = True
            continue
        if tok.startswith("-"):
            continue
        # Skip sdist tarball install (`${sdists[0]}[server]`)
        if "sdists" in tok or tok.endswith(".tar.gz") or ".tar.gz[" in tok:
            continue
        pkgs.append(tok)
    for spec in pkgs:
        assert "*" not in spec and _PINNED_PKG_RE.fullmatch(spec), (
            f"step {step_name!r} must exact-pin pip package with `==<ver>`, got {spec!r}"
        )


def _assert_pytest_worker_cap(cmd: list[str], step_name: str) -> None:
    """Assert every `pytest` invocation caps xdist workers at <= 16 (and caps `-n auto`/`logical` with `--maxprocesses`)."""
    num_vals: list[str] = []
    max_procs: list[str] = []
    i = 0
    while i < len(cmd):
        tok = cmd[i]
        if tok in ("-n", "--numprocesses"):
            assert i + 1 < len(cmd), f"step {step_name!r} has dangling {tok}"
            num_vals.append(cmd[i + 1])
            i += 2
            continue
        if tok.startswith("-n") and len(tok) > 2:
            num_vals.append(tok[2:].lstrip("="))
        elif tok.startswith("--numprocesses="):
            num_vals.append(tok.split("=", 1)[1])
        elif tok == "--maxprocesses":
            assert i + 1 < len(cmd), f"step {step_name!r} has dangling --maxprocesses"
            max_procs.append(cmd[i + 1])
            i += 2
            continue
        elif tok.startswith("--maxprocesses="):
            max_procs.append(tok.split("=", 1)[1])
        i += 1

    for nv in num_vals:
        if nv in ("auto", "logical"):
            assert max_procs, f"step {step_name!r} uses `-n {nv}` without `--maxprocesses <= 16`"
            for mp in max_procs:
                assert mp.isdigit() and 1 <= int(mp) <= 16, (
                    f"step {step_name!r} must cap --maxprocesses in 1..16, got {mp!r}"
                )
        else:
            assert nv.isdigit() and 0 <= int(nv) <= 16, (
                f"step {step_name!r} must cap pytest -n in 0..16, got {nv!r}"
            )


def test_ci_workflow_enforces_supply_chain_permissions_and_worker_cap() -> None:
    """CI supply-chain and runtime guard: ci.yml must define safe concurrency and positive job timeouts,
    cap all pytest xdist invocations at <= 16 workers, exact-pin all pip install packages (test deps and
    `build`), run all `web/app/tests/*.test.mjs` suites with a non-empty glob guard, keep
    `PORCHLAMP_PERF_HOST_FACTOR` strictly on the primary `Run test suite` step `env:` (`1 <= factor <= 2`),
    guard `sdists=(dist/*.tar.gz)` with `nullglob` and file existence, and use `RUNNER_TEMP` (never `/tmp/`)."""
    path = WORKFLOW_DIR / "ci.yml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert "PORCHLAMP_PERF_HOST_FACTOR" not in (doc.get("env") or {}), (
        "PORCHLAMP_PERF_HOST_FACTOR must not be set at workflow level"
    )

    conc = doc.get("concurrency")
    assert isinstance(conc, dict), f"ci.yml must define concurrency mapping, got {conc!r}"
    assert isinstance(conc.get("group"), str) and conc["group"].strip(), (
        f"ci.yml concurrency.group must be a non-empty string, got {conc.get('group')!r}"
    )
    assert "cancel-in-progress" in conc, "ci.yml concurrency must explicitly set cancel-in-progress"
    assert conc["cancel-in-progress"] is not True, (
        "ci.yml concurrency.cancel-in-progress must not unconditionally cancel main pushes"
    )

    saw_primary_pytest = False
    saw_node = False
    saw_deps_install = False
    saw_build_install = False
    saw_sdist_install = False
    saw_sdist_verify = False

    for job_name, job in doc["jobs"].items():
        assert "PORCHLAMP_PERF_HOST_FACTOR" not in (job.get("env") or {}), (
            f"job {job_name!r} must not set PORCHLAMP_PERF_HOST_FACTOR at job level"
        )
        timeout = job.get("timeout-minutes")
        assert type(timeout) is int and 1 <= timeout <= 30, (
            f"job {job_name!r} must declare integer timeout-minutes in 1..30, got {timeout!r}"
        )

        for step in job["steps"]:
            name = step.get("name", "")
            env = step.get("env") or {}

            if "uses" in step:
                assert "PORCHLAMP_PERF_HOST_FACTOR" not in env, (
                    f"uses step {name!r} must not set PORCHLAMP_PERF_HOST_FACTOR"
                )
                continue

            run = str(step.get("run") or "")
            assert "PORCHLAMP_PERF_HOST_FACTOR" not in run, (
                f"step {name!r} must not assign or reference PORCHLAMP_PERF_HOST_FACTOR inline in run:"
            )

            cmds = _shell_commands(run)
            is_primary_pytest = job_name == "test" and name == "Run test suite"
            if is_primary_pytest:
                saw_primary_pytest = True
                assert "PORCHLAMP_PERF_HOST_FACTOR" not in run
                if "PORCHLAMP_PERF_HOST_FACTOR" in env:
                    raw_factor = env["PORCHLAMP_PERF_HOST_FACTOR"]
                    assert not isinstance(raw_factor, bool), (
                        f"PORCHLAMP_PERF_HOST_FACTOR must not be bool, got {raw_factor!r}"
                    )
                    try:
                        val = float(str(raw_factor))
                    except ValueError as exc:
                        raise AssertionError(
                            f"PORCHLAMP_PERF_HOST_FACTOR must be numeric, got {raw_factor!r}"
                        ) from exc
                    assert math.isfinite(val) and 1.0 <= val <= 2.0, (
                        f"PORCHLAMP_PERF_HOST_FACTOR must be finite in [1, 2], got {raw_factor!r}"
                    )
            else:
                assert "PORCHLAMP_PERF_HOST_FACTOR" not in env, (
                    f"non-primary step {job_name!r}/{name!r} must not set PORCHLAMP_PERF_HOST_FACTOR"
                )

            if job_name == "package":
                assert "/tmp/" not in run, f"package step {name!r} must not reference hardcoded /tmp/ paths"

            for cmd in cmds:
                # Check every pytest invocation across every step
                if any(tok == "pytest" or tok.endswith("/pytest") for tok in cmd):
                    _assert_pytest_worker_cap(cmd, name)

                # Check every pip install command across every step
                if "pip" in cmd and "install" in cmd:
                    _assert_pip_install_pinned(cmd, name)
                    if any(tok.startswith("build==") for tok in cmd):
                        saw_build_install = True
                    if any(tok.startswith("pytest==") for tok in cmd):
                        saw_deps_install = True

                # Check Node test suite step
                if cmd and cmd[0] == "node" and "--test" in cmd:
                    saw_node = True
                    # Either expanded via guarded nullglob array or direct glob token
                    has_nullglob_guard = (
                        any(c == ["shopt", "-s", "nullglob"] for c in cmds)
                        and any("web/app/tests/*.test.mjs" in " ".join(c) for c in cmds)
                        and any(c[:1] == ["test"] and "-gt" in c and "0" in c for c in cmds)
                    )
                    assert has_nullglob_guard, (
                        f"node step {name!r} must guard web/app/tests/*.test.mjs with `shopt -s nullglob` and non-empty check"
                    )

            # Package job RUNNER_TEMP & sdist cardinality guards
            if job_name == "package" and "Non-editable install (W4-1:" in name:
                saw_sdist_install = True
                assert any(c == ["shopt", "-s", "nullglob"] for c in cmds), (
                    "sdist install step must enable `shopt -s nullglob` before expanding dist/*.tar.gz"
                )
                assert any("dist/*.tar.gz" in " ".join(c) for c in cmds)
                assert any(c[:1] == ["test"] and "-eq" in c and "1" in c for c in cmds), (
                    "sdist install step must assert exactly 1 tarball (`test ... -eq 1`)"
                )
                assert any(c[:2] == ["test", "-f"] for c in cmds), (
                    "sdist install step must verify tarball exists (`test -f`)"
                )
                assert "RUNNER_TEMP" in run, "sdist install step must use RUNNER_TEMP"
            if job_name == "package" and "Non-editable install finds" in name:
                saw_sdist_verify = True
                assert "RUNNER_TEMP" in run, "sdist verification step must use RUNNER_TEMP"

    assert (
        saw_primary_pytest
        and saw_node
        and saw_deps_install
        and saw_build_install
        and saw_sdist_install
        and saw_sdist_verify
    ), (
        f"missing expected ci.yml steps: pytest={saw_primary_pytest}, node={saw_node}, "
        f"deps={saw_deps_install}, build={saw_build_install}, sdist_install={saw_sdist_install}, "
        f"sdist_verify={saw_sdist_verify}"
    )
