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
from typing import Any, NamedTuple

import pytest
import yaml

WORKFLOW_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"
WORKFLOWS = sorted(WORKFLOW_DIR.glob("*.y*ml"))


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """YAML SafeLoader subclass that fails on duplicate mapping keys."""


def _construct_unique_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"duplicate YAML key {key!r} at line {key_node.start_mark.line + 1}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _load_yaml_strict(raw_text: str) -> Any:
    return yaml.load(raw_text, Loader=_UniqueKeySafeLoader)


def test_there_are_workflows_to_check() -> None:
    assert WORKFLOWS, f"no workflow files under {WORKFLOW_DIR}: the parse check would pass vacuously"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_parses_and_every_step_runs_something(path: Path) -> None:
    doc = _load_yaml_strict(path.read_text(encoding="utf-8"))  # syntax or duplicate-key error raises with line/column
    assert isinstance(doc, dict), f"{path.name}: not a mapping"
    triggers = doc.get("on", doc.get(True))  # PyYAML (YAML 1.1) reads the bare key `on` as True
    assert triggers, f"{path.name}: no triggers"
    jobs = doc.get("jobs")
    assert isinstance(jobs, dict) and jobs, f"{path.name}: no jobs"
    for job_name, job in jobs.items():
        if "uses" in job and "steps" not in job:
            continue
        steps = job.get("steps")
        assert isinstance(steps, list) and steps, f"{path.name}: job {job_name!r} has no steps"
        for index, step in enumerate(steps):
            assert ("uses" in step) != ("run" in step), (
                f"{path.name}: job {job_name!r} step {index} ({step.get('name')!r}) needs exactly one of uses/run"
            )


_SHA_PIN_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+@[0-9a-f]{40}$")
_PINNED_PKG_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9._,-]+\])?==\d+(?:\.\d+)*(?:[A-Za-z0-9.+-]*)?$"
)
_PIP_BASENAME_RE = re.compile(r"^(?:pip(?:\d+(?:\.\d+)*)?|-mpip(?:\d+(?:\.\d+)*)?)$")
_PYTEST_BASENAME_RE = re.compile(r"^(?:pytest(?:-?\d+(?:\.\d+)*)?|py\.test|-mpytest)$")
_LOCAL_SDIST_SPEC_RE = re.compile(r"^\$\{sdists\[0\]\}(?:\[[A-Za-z0-9._,-]+\])?$")
_LOCAL_EDITABLE_SPEC_RE = re.compile(r"^\.(?:\[[A-Za-z0-9._,-]+\])?$")
_RUNNER_TEMP_GUARD_RE = re.compile(r"\$\{RUNNER_TEMP:\?[^}]+\}")
_TMP_PATH_RE = re.compile(r"(?:^|[^A-Za-z0-9_.-])/tmp(?:/|$)")


def test_publish_from_drop_workflow_enforces_supply_chain_and_secret_isolation() -> None:
    path = WORKFLOW_DIR / "publish_from_drop.yml"
    doc = _load_yaml_strict(path.read_text(encoding="utf-8"))
    assert doc.get("permissions") == {}, "publish_from_drop.yml must set top-level permissions: {}"

    job = doc["jobs"]["publish"]
    assert job.get("permissions") == {"contents": "read"}
    steps = job["steps"]

    for step in steps:
        name = step.get("name", "")
        if "uses" in step:
            uses = step["uses"]
            assert _SHA_PIN_RE.fullmatch(uses), f"step {name!r} must pin action to 40-hex commit SHA, got {uses!r}"
            if uses.lower().startswith("actions/checkout@"):
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
    return _load_yaml_strict((WORKFLOW_DIR / "publish_from_drop.yml").read_text(encoding="utf-8"))


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


class _ShellStmt(NamedTuple):
    tokens: list[str]
    sep_before: str
    sep_after: str

    @property
    def is_errexit_guarded(self) -> bool:
        """True iff this command is NOT part of an `&&`/`||` conditional list or pipeline LHS that suppresses `set -e`."""
        return self.sep_before not in ("&&", "||") and self.sep_after not in ("&&", "||", "|")


def _preprocess_bash(script: str) -> str:
    """Collapse bash `\\<newline>` continuations (outside single quotes) and strip unquoted `#` comments while
    preserving trailing newlines as command separators."""
    s = script.replace("\r\n", "\n").replace("\r", "\n")
    n = len(s)
    out: list[str] = []
    i = 0
    in_sq = False
    in_dq = False
    while i < n:
        ch = s[i]
        if in_sq:
            out.append(ch)
            if ch == "'":
                in_sq = False
            i += 1
            continue
        if in_dq:
            if ch == "\\" and i + 1 < n:
                nx = s[i + 1]
                if nx == "\n":
                    i += 2
                    continue
                out.append(ch)
                out.append(nx)
                i += 2
                continue
            out.append(ch)
            if ch == '"':
                in_dq = False
            i += 1
            continue
        # Unquoted state
        if ch == "\\" and i + 1 < n:
            nx = s[i + 1]
            if nx == "\n":
                i += 2
                continue
            out.append(ch)
            out.append(nx)
            i += 2
            continue
        if ch == "'":
            in_sq = True
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_dq = True
            out.append(ch)
            i += 1
            continue
        if ch == "#" and (not out or out[-1] in " \t\n;|&()"):
            nl = s.find("\n", i + 1)
            if nl == -1:
                break
            # Preserve the newline so adjacent lines never merge into one command
            i = nl
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _shell_statements(script: str) -> list[_ShellStmt]:
    """Tokenize a bash `run:` script into individual commands with surrounding separators preserved.

    Note: does not recursively parse nested shell strings (`bash -c "..."` / `sh -c "..."`).
    """
    cleaned = _preprocess_bash(script)
    lex = shlex.shlex(cleaned, posix=True, punctuation_chars=";&|()")
    lex.whitespace_split = False
    lex.commenters = ""
    lex.whitespace = " \t\r"

    stmts: list[_ShellStmt] = []
    cur: list[str] = []
    sep_before = ""
    for tok in lex:
        if tok in ("\n", ";", "&&", "||", "|", "&"):
            if cur:
                stmts.append(_ShellStmt(tokens=cur, sep_before=sep_before, sep_after=tok))
                cur = []
                sep_before = tok
            elif tok in ("&&", "||", "|", "&"):
                sep_before = tok
        else:
            cur.append(tok)
    if cur:
        stmts.append(_ShellStmt(tokens=cur, sep_before=sep_before, sep_after=""))
    return stmts


def _iter_uses_nodes(raw_text: str) -> list[tuple[str, yaml.ScalarNode]]:
    """Walk the composed YAML AST to yield `(label, scalar_node)` for every job-level or step-level `uses:` mapping key.

    Because this uses PyYAML's AST rather than raw line regexes, it ignores `uses:` text inside `run: |` block
    scalars, supports YAML-quoted values, catches flow-mapping `{name: X, uses: ...}` entries, and covers
    reusable-workflow job-level `uses:`.
    """
    root = yaml.compose(raw_text, Loader=yaml.SafeLoader)
    if not isinstance(root, yaml.MappingNode):
        return []
    found: list[tuple[str, yaml.ScalarNode]] = []
    for k_node, v_node in root.value:
        if k_node.value != "jobs" or not isinstance(v_node, yaml.MappingNode):
            continue
        for job_key, job_val in v_node.value:
            if not isinstance(job_val, yaml.MappingNode):
                continue
            job_name = str(job_key.value)
            for jk, jv in job_val.value:
                if jk.value == "uses" and isinstance(jv, yaml.ScalarNode):
                    found.append((f"job {job_name!r}", jv))
                elif jk.value == "steps" and isinstance(jv, yaml.SequenceNode):
                    for idx, step_node in enumerate(jv.value):
                        if not isinstance(step_node, yaml.MappingNode):
                            continue
                        step_name = f"step {idx}"
                        uses_node: yaml.ScalarNode | None = None
                        for sk, sv in step_node.value:
                            if sk.value == "name" and isinstance(sv, yaml.ScalarNode):
                                step_name = str(sv.value)
                            elif sk.value == "uses" and isinstance(sv, yaml.ScalarNode):
                                uses_node = sv
                        if uses_node is not None:
                            found.append((f"job {job_name!r} step {step_name!r}", uses_node))
    return found


def _find_pip_index(cmd: list[str]) -> int | None:
    """Return index of a `pip`/`pip3`/`pip3.12`/`.../pip`/`-mpip` token in `cmd`, or None."""
    for idx, tok in enumerate(cmd):
        if tok in ("(", ")"):
            continue
        base = tok.rsplit("/", 1)[-1]
        if _PIP_BASENAME_RE.fullmatch(base):
            return idx
    return None


def _find_pytest_index(cmd: list[str]) -> int | None:
    """Return index of a `pytest`/`pytest-3`/`py.test`/`.../pytest`/`-mpytest` token in `cmd`, or None."""
    for idx, tok in enumerate(cmd):
        if tok in ("(", ")"):
            continue
        base = tok.rsplit("/", 1)[-1]
        if _PYTEST_BASENAME_RE.fullmatch(base):
            return idx
    return None


def _assert_pip_install_pinned(cmd: list[str], step_name: str, *, allow_local_sdist: bool = False) -> None:
    """Assert every package spec in a `pip install` command uses an exact `==<ver>` pin."""
    pip_idx = _find_pip_index(cmd)
    if pip_idx is None:
        return
    try:
        idx = cmd.index("install", pip_idx + 1)
    except ValueError:
        return
    value_flags = {
        "-r",
        "--requirement",
        "-c",
        "--constraint",
        "-t",
        "--target",
        "--find-links",
        "-f",
        "--index-url",
        "-i",
        "--extra-index-url",
        "--cache-dir",
        "--timeout",
        "--retries",
        "--prefix",
        "--upgrade-strategy",
        "--python-version",
        "--platform",
        "--implementation",
        "--abi",
        "--config-settings",
        "-C",
        "--root",
    }
    editable_flags = {"-e", "--editable"}
    pkgs: list[str] = []
    i = idx + 1
    while i < len(cmd):
        tok = cmd[i]
        if tok in ("(", ")"):
            i += 1
            continue
        if tok in editable_flags:
            assert i + 1 < len(cmd), f"step {step_name!r} has dangling {tok}"
            target = cmd[i + 1]
            assert _LOCAL_EDITABLE_SPEC_RE.fullmatch(target), (
                f"step {step_name!r} forbids editable VCS/URL/remote install target {target!r}"
            )
            i += 2
            continue
        if tok in value_flags:
            assert i + 1 < len(cmd), f"step {step_name!r} has dangling {tok}"
            arg = cmd[i + 1]
            if tok in ("-r", "--requirement", "-c", "--constraint"):
                assert "://" not in arg, f"step {step_name!r} forbids remote requirement URL {arg!r}"
            i += 2
            continue
        if tok.startswith("-"):
            if tok.startswith(("-e=", "--editable=")):
                target = tok.split("=", 1)[1]
                assert _LOCAL_EDITABLE_SPEC_RE.fullmatch(target), (
                    f"step {step_name!r} forbids editable VCS/URL/remote install target {target!r}"
                )
            i += 1
            continue
        if allow_local_sdist and _LOCAL_SDIST_SPEC_RE.fullmatch(tok):
            i += 1
            continue
        pkgs.append(tok)
        i += 1
    for spec in pkgs:
        assert "://" not in spec and "*" not in spec and _PINNED_PKG_RE.fullmatch(spec), (
            f"step {step_name!r} must exact-pin pip package with `==<ver>`, got {spec!r}"
        )


def _is_ascii_digits(val: str) -> bool:
    return bool(val) and val.isascii() and val.isdigit()


def _assert_pytest_worker_cap(cmd: list[str], step_name: str) -> None:
    """Assert every `pytest` invocation caps xdist workers at <= 16 (and caps `-n auto`/`logical` with `--maxprocesses`)."""
    pytest_idx = _find_pytest_index(cmd)
    if pytest_idx is None:
        return
    args = [t for t in cmd[pytest_idx + 1 :] if t not in ("(", ")")]
    num_vals: list[str] = []
    max_procs: list[str] = []
    i = 0
    while i < len(args):
        tok = args[i]
        if tok in ("-n", "--numprocesses"):
            assert i + 1 < len(args), f"step {step_name!r} has dangling {tok}"
            num_vals.append(args[i + 1])
            i += 2
            continue
        if tok.startswith("-n") and len(tok) > 2:
            num_vals.append(tok[2:].lstrip("="))
        elif tok.startswith("--numprocesses="):
            num_vals.append(tok.split("=", 1)[1])
        elif tok == "--maxprocesses":
            assert i + 1 < len(args), f"step {step_name!r} has dangling --maxprocesses"
            max_procs.append(args[i + 1])
            i += 2
            continue
        elif tok.startswith("--maxprocesses="):
            max_procs.append(tok.split("=", 1)[1])
        i += 1

    for nv in num_vals:
        if nv in ("auto", "logical"):
            assert max_procs, f"step {step_name!r} uses `-n {nv}` without `--maxprocesses <= 16`"
            for mp in max_procs:
                assert _is_ascii_digits(mp) and 1 <= int(mp) <= 16, (
                    f"step {step_name!r} must cap --maxprocesses in 1..16, got {mp!r}"
                )
        else:
            assert _is_ascii_digits(nv) and 0 <= int(nv) <= 16, (
                f"step {step_name!r} must cap pytest -n in 0..16, got {nv!r}"
            )


def _assert_safe_cancel_in_progress(cip: Any) -> None:
    """Reject unconditional cancellation (`True`, `"true"`, `${{ true }}`, `None`, or truthy non-PR expressions)."""
    assert cip is not None, "ci.yml concurrency.cancel-in-progress must not be null"
    if cip is False:
        return
    assert cip is not True, "ci.yml concurrency.cancel-in-progress must not unconditionally cancel main pushes"
    assert isinstance(cip, str), f"ci.yml concurrency.cancel-in-progress must be False or PR expression, got {cip!r}"
    stripped = cip.strip()
    assert stripped.lower() != "true", (
        f"ci.yml concurrency.cancel-in-progress must not be truthy string {cip!r}"
    )
    assert re.fullmatch(r"\$\{\{.+\}\}", stripped), (
        f"ci.yml concurrency.cancel-in-progress string must be a `${{{{ ... }}}}` expression, got {cip!r}"
    )
    assert not re.search(r"\btrue\b", stripped, re.IGNORECASE), (
        f"ci.yml concurrency.cancel-in-progress must not contain literal `true`, got {cip!r}"
    )
    assert "pull_request" in stripped and "github.event_name" in stripped, (
        f"ci.yml concurrency.cancel-in-progress must scope cancellation to pull_request events, got {cip!r}"
    )


def _assert_workflow_all_guards(path: Path) -> None:
    """Shared implementation for `test_all_workflows_enforce_supply_chain_permissions_and_checkout`."""
    raw_text = path.read_text(encoding="utf-8")
    lines = raw_text.splitlines()
    doc = _load_yaml_strict(raw_text)

    assert doc.get("permissions") in ({"contents": "read"}, {}), (
        f"{path.name} must declare minimal top-level permissions, got {doc.get('permissions')!r}"
    )

    for label, val_node in _iter_uses_nodes(raw_text):
        uses = str(val_node.value)
        assert _SHA_PIN_RE.fullmatch(uses), (
            f"{path.name} {label} must pin action to 40-hex commit SHA, got {uses!r}"
        )
        end_line_idx = val_node.end_mark.line
        tail = lines[end_line_idx][val_node.end_mark.column :] if end_line_idx < len(lines) else ""
        assert re.search(r"#\s*v\d+", tail), (
            f"{path.name}:{end_line_idx + 1} pinned action ({label}) must carry `# v<ver>` comment: "
            f"{lines[end_line_idx].strip()!r}"
        )

    for job_name, job in doc["jobs"].items():
        job_perms = job.get("permissions")
        assert job_perms is None or job_perms in ({"contents": "read"}, {}), (
            f"{path.name} job {job_name!r} must not escalate permissions beyond contents: read, got {job_perms!r}"
        )
        for step in job.get("steps") or []:
            name = step.get("name", "")
            if "uses" in step:
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
                continue

            run = str(step.get("run") or "")
            allow_sdist = path.name == "ci.yml" and job_name == "package" and "Non-editable install (W4-1:" in name
            for stmt in _shell_statements(run):
                if _find_pip_index(stmt.tokens) is not None:
                    _assert_pip_install_pinned(stmt.tokens, f"{path.name}:{name}", allow_local_sdist=allow_sdist)


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_all_workflows_enforce_supply_chain_permissions_and_checkout(path: Path) -> None:
    """Every workflow under `.github/workflows/*.y*ml` must pin every action (including job-level reusable
    workflows) to a 40-hex commit SHA with a `# v<ver>` comment, declare minimal top-level and job-level
    permissions (`{}` or `contents: read`), set `persist-credentials: false` strictly on `actions/checkout`,
    and exact-pin every `pip install` package."""
    _assert_workflow_all_guards(path)


def _find_node_test_index(cmd: list[str]) -> int | None:
    """Return index of `node` followed by `--test` anywhere in `cmd` (catches wrappers/subshells)."""
    for idx, tok in enumerate(cmd):
        if tok in ("(", ")"):
            continue
        if (tok == "node" or tok.endswith("/node")) and "--test" in cmd[idx + 1 :]:
            return idx
    return None


def _check_ci_workflow_doc(doc: dict[str, Any]) -> None:
    """Core validator for `.github/workflows/ci.yml` invariants (factored for direct unit testing)."""
    wf_env = doc.get("env") or {}
    assert "PORCHLAMP_PERF_HOST_FACTOR" not in wf_env, (
        "PORCHLAMP_PERF_HOST_FACTOR must not be set at workflow level"
    )
    assert "PYTEST_ADDOPTS" not in wf_env, "PYTEST_ADDOPTS must not be set at workflow level"

    conc = doc.get("concurrency")
    assert isinstance(conc, dict), f"ci.yml must define concurrency mapping, got {conc!r}"
    assert isinstance(conc.get("group"), str) and conc["group"].strip(), (
        f"ci.yml concurrency.group must be a non-empty string, got {conc.get('group')!r}"
    )
    assert "cancel-in-progress" in conc, "ci.yml concurrency must explicitly set cancel-in-progress"
    _assert_safe_cancel_in_progress(conc["cancel-in-progress"])

    saw_primary_pytest = False
    saw_node = False
    saw_deps_install = False
    saw_build_install = False
    saw_sdist_install = False
    saw_sdist_verify = False

    for job_name, job in doc["jobs"].items():
        job_env = job.get("env") or {}
        assert "PORCHLAMP_PERF_HOST_FACTOR" not in job_env, (
            f"job {job_name!r} must not set PORCHLAMP_PERF_HOST_FACTOR at job level"
        )
        assert "PYTEST_ADDOPTS" not in job_env, (
            f"job {job_name!r} must not set PYTEST_ADDOPTS at job level"
        )
        timeout = job.get("timeout-minutes")
        assert type(timeout) is int and 1 <= timeout <= 30, (
            f"job {job_name!r} must declare integer timeout-minutes in 1..30, got {timeout!r}"
        )

        for step in job.get("steps") or []:
            name = step.get("name", "")
            env = step.get("env") or {}

            assert "PYTEST_ADDOPTS" not in env, (
                f"step {job_name!r}/{name!r} must not set PYTEST_ADDOPTS in env:"
            )

            if "uses" in step:
                assert "PORCHLAMP_PERF_HOST_FACTOR" not in env, (
                    f"uses step {name!r} must not set PORCHLAMP_PERF_HOST_FACTOR"
                )
                continue

            run = str(step.get("run") or "")
            assert "PORCHLAMP_PERF_HOST_FACTOR" not in run, (
                f"step {name!r} must not assign or reference PORCHLAMP_PERF_HOST_FACTOR inline in run:"
            )

            stmts = _shell_statements(run)
            step_has_pytest = any(_find_pytest_index(s.tokens) is not None for s in stmts)
            is_primary_pytest = job_name == "test" and name == "Run test suite"
            if is_primary_pytest:
                assert step_has_pytest, "primary `Run test suite` step must invoke pytest"
                saw_primary_pytest = True
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
                wd = str(step.get("working-directory") or "")
                assert not _TMP_PATH_RE.search(wd), (
                    f"package step {name!r} working-directory must not use /tmp, got {wd!r}"
                )
                for stmt in stmts:
                    for tok in stmt.tokens:
                        assert not _TMP_PATH_RE.search(tok), (
                            f"package step {name!r} must not reference hardcoded /tmp paths, got {tok!r}"
                        )

            allow_local_sdist = job_name == "package" and "Non-editable install (W4-1:" in name
            for stmt_idx, stmt in enumerate(stmts):
                cmd = stmt.tokens
                # Reject inline PYTEST_ADDOPTS=<val> assignments (allow `env -u PYTEST_ADDOPTS`)
                for tok in cmd:
                    assert not tok.startswith("PYTEST_ADDOPTS="), (
                        f"step {name!r} must not set inline PYTEST_ADDOPTS={tok!r}"
                    )

                if _find_pytest_index(cmd) is not None:
                    _assert_pytest_worker_cap(cmd, name)

                if _find_pip_index(cmd) is not None:
                    _assert_pip_install_pinned(cmd, name, allow_local_sdist=allow_local_sdist)
                    if any(tok.startswith("build==") for tok in cmd):
                        saw_build_install = True
                    if any(tok.startswith("pytest==") for tok in cmd):
                        saw_deps_install = True

                # Every `node ... --test` invocation must be preceded by ordered nullglob guard + `${suites[@]}`
                if _find_node_test_index(cmd) is not None:
                    saw_node = True
                    shopt_idxs = [
                        i
                        for i, s in enumerate(stmts)
                        if s.tokens == ["shopt", "-s", "nullglob"] and s.is_errexit_guarded
                    ]
                    assign_idxs = [
                        i
                        for i, s in enumerate(stmts)
                        if s.tokens == ["suites=", "(", "web/app/tests/*.test.mjs", ")"]
                    ]
                    test_idxs = [
                        i
                        for i, s in enumerate(stmts)
                        if s.tokens == ["test", "${#suites[@]}", "-gt", "0"] and s.is_errexit_guarded
                    ]
                    assert shopt_idxs and assign_idxs and test_idxs, (
                        f"node step {name!r} must guard `web/app/tests/*.test.mjs` with standalone "
                        "`shopt -s nullglob`, `suites=(web/app/tests/*.test.mjs)`, and standalone "
                        "`test \"${#suites[@]}\" -gt 0`"
                    )
                    assert shopt_idxs[0] < assign_idxs[0] < test_idxs[0] < stmt_idx, (
                        f"node step {name!r} guard ordering broken: shopt={shopt_idxs}, "
                        f"assign={assign_idxs}, test={test_idxs}, node={stmt_idx}"
                    )
                    assert "${suites[@]}" in cmd, (
                        f"node step {name!r} must pass `\"${{suites[@]}}\"` to `node --test`, got {cmd!r}"
                    )

            # Package job RUNNER_TEMP:? & ordered standalone sdist cardinality guards
            if allow_local_sdist:
                saw_sdist_install = True
                shopt_idxs = [
                    i
                    for i, s in enumerate(stmts)
                    if s.tokens == ["shopt", "-s", "nullglob"] and s.is_errexit_guarded
                ]
                assign_idxs = [
                    i
                    for i, s in enumerate(stmts)
                    if s.tokens == ["sdists=", "(", "dist/*.tar.gz", ")"]
                ]
                cnt_idxs = [
                    i
                    for i, s in enumerate(stmts)
                    if s.tokens == ["test", "${#sdists[@]}", "-eq", "1"] and s.is_errexit_guarded
                ]
                file_idxs = [
                    i
                    for i, s in enumerate(stmts)
                    if s.tokens == ["test", "-f", "${sdists[0]}"] and s.is_errexit_guarded
                ]
                venv_idxs = [
                    i
                    for i, s in enumerate(stmts)
                    if "venv" in s.tokens and any(_RUNNER_TEMP_GUARD_RE.search(t) for t in s.tokens)
                ]
                pip_idxs = [
                    i
                    for i, s in enumerate(stmts)
                    if _find_pip_index(s.tokens) is not None
                    and any(_RUNNER_TEMP_GUARD_RE.search(t) for t in s.tokens)
                    and any(_LOCAL_SDIST_SPEC_RE.fullmatch(t) for t in s.tokens)
                ]
                assert shopt_idxs, "sdist install step must enable standalone `shopt -s nullglob`"
                assert assign_idxs, "sdist install step must assign `sdists=(dist/*.tar.gz)`"
                assert cnt_idxs, (
                    "sdist install step must have standalone (not `&&`/`||`-chained) `test \"${#sdists[@]}\" -eq 1`"
                )
                assert file_idxs, (
                    "sdist install step must have standalone (not `&&`/`||`-chained) `test -f \"${sdists[0]}\"`"
                )
                assert venv_idxs, (
                    "sdist install step must create venv under fail-fast `${RUNNER_TEMP:?...}`"
                )
                assert pip_idxs, (
                    "sdist install step must pip-install `${sdists[0]}[...]` using `${RUNNER_TEMP:?...}`"
                )
                assert shopt_idxs[0] < assign_idxs[0] < cnt_idxs[0] < venv_idxs[0] < pip_idxs[0], (
                    f"sdist install ordering invalid: shopt={shopt_idxs}, assign={assign_idxs}, "
                    f"cnt={cnt_idxs}, venv={venv_idxs}, pip={pip_idxs}"
                )
                assert assign_idxs[0] < file_idxs[0] < venv_idxs[0], (
                    f"sdist `test -f` must occur after assignment and before venv: assign={assign_idxs}, "
                    f"file={file_idxs}, venv={venv_idxs}"
                )

            if job_name == "package" and "Non-editable install finds" in name:
                saw_sdist_verify = True
                assert any(
                    any(_RUNNER_TEMP_GUARD_RE.search(t) for t in s.tokens) for s in stmts
                ), "sdist verification step must invoke Python via fail-fast `${RUNNER_TEMP:?...}`"

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


def test_ci_workflow_enforces_supply_chain_permissions_and_worker_cap() -> None:
    """CI supply-chain and runtime guard: ci.yml must define safe concurrency and positive job timeouts,
    cap all pytest xdist invocations at <= 16 workers, exact-pin all pip install packages (test deps and
    `build`), run all `web/app/tests/*.test.mjs` suites with an ordered standalone nullglob guard, keep
    `PORCHLAMP_PERF_HOST_FACTOR` strictly on the primary `Run test suite` step `env:` (`1 <= factor <= 2`),
    guard `sdists=(dist/*.tar.gz)` with ordered standalone `set -e` checks, and use `${RUNNER_TEMP:?...}`
    (never `/tmp`)."""
    path = WORKFLOW_DIR / "ci.yml"
    doc = _load_yaml_strict(path.read_text(encoding="utf-8"))
    _check_ci_workflow_doc(doc)


def test_workflow_guard_helpers_handle_continuations_comments_and_adversarial_bypasses(tmp_path: Path) -> None:
    """Direct regression checks proving the bash tokenizer, YAML AST walker, and pin/worker helpers catch
    bypass mutants and avoid false positives on valid syntax."""
    # 1. Backslash line continuation must NOT split pytest or pip install commands
    stmts = _shell_statements('python -m pytest \\\n  tests/ -n auto\npython -m pip install "build==1.6.1" \\\n  requests\n')
    assert len(stmts) == 2
    with pytest.raises(AssertionError):
        _assert_pytest_worker_cap(stmts[0].tokens, "cont-pytest")
    with pytest.raises(AssertionError):
        _assert_pip_install_pinned(stmts[1].tokens, "cont-pip")

    # 2. Inline `#` comments must preserve newline boundaries (never merge adjacent lines)
    stmts_c = _shell_statements('shopt -s nullglob # prevent literal glob\nsuites=(web/app/tests/*.test.mjs)\n')
    assert [s.tokens for s in stmts_c] == [
        ["shopt", "-s", "nullglob"],
        ["suites=", "(", "web/app/tests/*.test.mjs", ")"],
    ]
    stmts_c2 = _shell_statements('ruff check porchlamp/ # lint\nnode --test web/app/tests/unit.test.mjs\n')
    assert len(stmts_c2) == 2 and _find_node_test_index(stmts_c2[1].tokens) == 0

    # 3. `&&`-chained `test` guard suppresses `set -e` and MUST fail `is_errexit_guarded`
    chained = _shell_statements('test "${#sdists[@]}" -eq 1 && test -f "${sdists[0]}"\n')
    assert not chained[0].is_errexit_guarded and not chained[1].is_errexit_guarded
    separate = _shell_statements('test "${#sdists[@]}" -eq 1\ntest -f "${sdists[0]}"\n')
    assert separate[0].is_errexit_guarded and separate[1].is_errexit_guarded

    # 4. Pip pinning covers pip3, versioned/path-qualified pip, remote URLs, -e VCS URLs, and extras
    for bad in (
        ["pip3", "install", "unpinned"],
        ["/usr/bin/pip", "install", "requests"],
        ["/tmp/venv/bin/pip3.12", "install", "evil"],
        ["python", "-mpip", "install", "build"],
        ["python", "-m", "pip", "install", "build==1.6.1", "https://evil.example/pkg.tar.gz"],
        ["python", "-m", "pip", "install", "build==1.6.1", "evil-sdists-pkg"],
        ["python", "-m", "pip", "install", "-e", "git+https://evil.example/repo.git"],
    ):
        with pytest.raises(AssertionError):
            _assert_pip_install_pinned(bad, "bad-pip", allow_local_sdist=True)

    # Valid extras and value flags pass
    _assert_pip_install_pinned(
        ["pip", "install", "--cache-dir", "/var/cache/pip", "--upgrade-strategy", "eager", "uvicorn[standard]==0.52.4"],
        "good-pip",
    )
    _assert_pip_install_pinned(
        ["${RUNNER_TEMP:?msg}/venv/bin/python", "-m", "pip", "install", "--quiet", "${sdists[0]}[server]"],
        "good-sdist",
        allow_local_sdist=True,
    )

    # 5. Pytest worker cap: slices after pytest token (ignores `nice -n 20`), catches `-mpytest` and rejects superscripts
    _assert_pytest_worker_cap(["nice", "-n", "20", "pytest", "tests/", "-n", "8"], "nice-ok")
    for bad_py in (
        ["python", "-mpytest", "tests/", "-n", "auto"],
        ["py.test", "tests/", "-n", "24"],
        ["pytest-3", "tests/", "-n", "²"],
    ):
        with pytest.raises(AssertionError):
            _assert_pytest_worker_cap(bad_py, "bad-pytest")

    # 6. `cancel-in-progress` rejects `"true"`, `${{ true }}`, `None`, `True`
    for bad_cip in (True, None, "true", "True", "${{ true }}", "${{ true || github.event_name == 'pull_request' }}"):
        with pytest.raises(AssertionError):
            _assert_safe_cancel_in_progress(bad_cip)
    _assert_safe_cancel_in_progress(False)
    _assert_safe_cancel_in_progress("${{ github.event_name == 'pull_request' }}")

    # 7. YAML AST walker: accepts quoted `uses:`, ignores `uses:` inside `run: |`, catches flow mappings & reusable jobs
    valid_wf = tmp_path / "valid.yml"
    valid_wf.write_text(
        'name: Demo\n'
        'on: push\n'
        'permissions: {}\n'
        'jobs:\n'
        '  reusable:\n'
        '    uses: "org/repo/.github/workflows/x.yml@' + "a" * 40 + '" # v1.2.3\n'
        '  build:\n'
        '    runs-on: ubuntu-latest\n'
        '    steps:\n'
        '      - uses: "actions/checkout@' + "b" * 40 + '" # v7.0.1\n'
        '        with:\n'
        '          persist-credentials: false\n'
        '      - name: Heredoc\n'
        '        run: |\n'
        '          cat <<EOF\n'
        '          uses: actions/checkout@v4\n'
        '          EOF\n',
        encoding="utf-8",
    )
    _assert_workflow_all_guards(valid_wf)

    flow_missing_comment = tmp_path / "flow.yml"
    flow_missing_comment.write_text(
        'name: Flow\non: push\npermissions: {}\njobs:\n  b:\n    runs-on: ubuntu-latest\n'
        f'    steps:\n      - {{name: Checkout, uses: actions/checkout@{"a" * 40}, with: {{persist-credentials: false}}}}\n',
        encoding="utf-8",
    )
    with pytest.raises(AssertionError):
        _assert_workflow_all_guards(flow_missing_comment)
