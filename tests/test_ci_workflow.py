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

import copy
import math
from pathlib import Path
import re
import shlex
from typing import Any, NamedTuple

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
WORKFLOWS = sorted(WORKFLOW_DIR.glob("*.y*ml"))


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """YAML SafeLoader subclass that fails on duplicate mapping keys (including YAML 1.1 bool/string collisions)."""


def _construct_unique_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    seen_scalar_raw: set[str] = set()
    explicit_pairs: list[tuple[yaml.Node, yaml.Node]] = []
    merged: dict[Any, Any] = {}

    for key_node, value_node in node.value:
        if key_node.tag == "tag:yaml.org,2002:merge":
            merge_val = loader.construct_object(value_node, deep=deep)
            if isinstance(merge_val, dict):
                merged.update(merge_val)
            elif isinstance(merge_val, list):
                for item in reversed(merge_val):
                    if isinstance(item, dict):
                        merged.update(item)
            continue
        explicit_pairs.append((key_node, value_node))

    mapping.update(merged)
    seen_explicit_keys: set[Any] = set()
    for key_node, value_node in explicit_pairs:
        key = loader.construct_object(key_node, deep=deep)
        if isinstance(key_node, yaml.ScalarNode):
            raw_norm = str(key_node.value)
            if raw_norm in seen_scalar_raw:
                raise ValueError(f"duplicate YAML key {raw_norm!r} at line {key_node.start_mark.line + 1}")
            seen_scalar_raw.add(raw_norm)
        # Note: in Python True == 1 and False == 0, so key_id distinguishes bool from int
        key_id = (type(key), key) if isinstance(key, (bool, int)) else key
        if key_id in seen_explicit_keys:
            raise ValueError(f"duplicate YAML key {key!r} at line {key_node.start_mark.line + 1}")
        seen_explicit_keys.add(key_id)
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
        assert isinstance(job, dict), f"{path.name}: job {job_name!r} is not a mapping"
        if "uses" in job and "steps" not in job:
            assert isinstance(job["uses"], str) and job["uses"].strip(), (
                f"{path.name}: reusable job {job_name!r} `uses` must be a non-empty string, got {job['uses']!r}"
            )
            continue
        steps = job.get("steps")
        assert isinstance(steps, list) and steps, f"{path.name}: job {job_name!r} has no steps"
        for index, step in enumerate(steps):
            assert isinstance(step, dict), f"{path.name}: job {job_name!r} step {index} is not a mapping"
            assert ("uses" in step) != ("run" in step), (
                f"{path.name}: job {job_name!r} step {index} ({step.get('name')!r}) needs exactly one of uses/run"
            )
            if "uses" in step:
                assert isinstance(step["uses"], str) and step["uses"].strip(), (
                    f"{path.name}: job {job_name!r} step {index} `uses` must be a non-empty string, got {step['uses']!r}"
                )


_SHA_PIN_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+@[0-9a-f]{40}$")
_PINNED_PKG_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9._,-]+\])?==\d+(?:\.\d+)*(?:[A-Za-z0-9.+-]*)?$"
)
_BUILD_PIN_RE = re.compile(r"^build(?:\[[A-Za-z0-9._,-]+\])?==\d+(?:\.\d+)*(?:[A-Za-z0-9.+-]*)?$")
_PYTEST_PIN_RE = re.compile(r"^pytest(?:\[[A-Za-z0-9._,-]+\])?==\d+(?:\.\d+)*(?:[A-Za-z0-9.+-]*)?$")
_PIP_BASENAME_RE = re.compile(
    r"^(?:pip(?:-?\d+(?:\.\d+)*)?(?:\.__main__)?|-[A-Za-z]*mpip(?:-?\d+(?:\.\d+)*)?(?:\.__main__)?)$"
)
_PYTEST_BASENAME_RE = re.compile(
    r"^(?:pytest(?:-?\d+(?:\.\d+)*)?(?:\.__main__)?|py\.test|-[A-Za-z]*mpytest(?:\.__main__)?)$"
)
_LOCAL_SDIST_SPEC_RE = re.compile(r"^\$\{sdists\[0\]\}(?:\[[A-Za-z0-9._,-]+\])?$")
_LOCAL_EDITABLE_SPEC_RE = re.compile(r"^\.(?:\[[A-Za-z0-9._,-]+\])?$")
_RUNNER_TEMP_GUARD_RE = re.compile(r"\$\{RUNNER_TEMP:\?[^}]+\}")
_BARE_RUNNER_TEMP_RE = re.compile(r"\$RUNNER_TEMP(?![A-Za-z0-9_])|\$\{RUNNER_TEMP(?!(?::\?[^}]+))\}")
_RUNNER_TEMP_ANY_RE = re.compile(r"\$RUNNER_TEMP(?![A-Za-z0-9_])|\$\{RUNNER_TEMP(?:[^}]*)\}")
_TMP_PATH_RE = re.compile(r"(?:^|[^A-Za-z0-9_.-])/tmp(?:/|[^A-Za-z0-9_.-]|$)")
_SAFE_CANCEL_EXPR_RE = re.compile(
    r"^\$\{\{\s*github\.event_name\s*==\s*(['\"])pull_request\1\s*\}\}$"
)
_FACTOR_NUM_RE = re.compile(r"^\d+(?:\.\d+)?$")
_PYTEST_VAL_SHORT = frozenset("ckmoprW")
_FD_REDIR_PREFIX_RE = re.compile(r"^[0-9]+$")


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
            assert isinstance(uses, str) and _SHA_PIN_RE.fullmatch(uses), (
                f"step {name!r} must pin action to 40-hex commit SHA, got {uses!r}"
            )
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


def _assert_publish_never_pushes_main(script: str) -> None:
    """Guard against direct pushes to main (including `HEAD:refs/heads/main`)."""
    assert "push origin main" not in script
    assert not re.search(r"refs/heads/main(?![A-Za-z0-9_/-])", script), (
        "publish script must never push directly to refs/heads/main"
    )
    assert not re.search(r"\bpush\b[^\n]*\borigin\b[^\n]*\bmain(?![A-Za-z0-9_/-])", script), (
        "publish script must never push directly to origin main"
    )


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
    _assert_publish_never_pushes_main(script)
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
    raw_tokens: list[str]
    sep_before: str
    sep_after: str
    in_compound: bool = False

    @property
    def is_errexit_guarded(self) -> bool:
        """True iff this command is NOT inside a conditional/compound suppressor or `&&`/`||`/`|`/`|&`/`&` list."""
        return (
            not self.in_compound
            and self.sep_before not in ("&&", "||")
            and self.sep_after not in ("&&", "||", "|", "|&", "&")
        )


def _decode_ansi_c_chunk(raw: str) -> str:
    """Minimal safe ANSI-C `$'...'` unescaper for bash workflow lexing (handles `\\'` / `\\\\` / basic escapes)."""
    out: list[str] = []
    i = 0
    n = len(raw)
    escapes = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", "'": "'", '"': '"', "a": "\a", "b": "\b", "f": "\f", "v": "\v"}
    while i < n:
        ch = raw[i]
        if ch == "\\" and i + 1 < n:
            nx = raw[i + 1]
            if nx in escapes:
                out.append(escapes[nx])
                i += 2
                continue
            out.append("\\" + nx)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _parse_heredoc_delim(src: str, pos: int) -> tuple[str, bool, int]:
    """Parse a heredoc delimiter word after `<<` / `<<-`, returning (delim, is_quoted, end_pos)."""
    n = len(src)
    while pos < n and src[pos] in " \t":
        pos += 1
    if pos >= n or src[pos] in "\n;|&()<>":
        return "", False, pos
    out: list[str] = []
    quoted = False
    i = pos
    while i < n and src[i] not in " \t\n;|&()<>":
        ch = src[i]
        if ch == "'":
            quoted = True
            j = src.find("'", i + 1)
            if j == -1:
                out.append(src[i + 1 :])
                i = n
            else:
                out.append(src[i + 1 : j])
                i = j + 1
            continue
        if ch == '"':
            quoted = True
            i += 1
            while i < n:
                if src[i] == "\\" and i + 1 < n:
                    out.append(src[i + 1])
                    i += 2
                elif src[i] == '"':
                    i += 1
                    break
                else:
                    out.append(src[i])
                    i += 1
            continue
        if ch == "\\" and i + 1 < n:
            quoted = True
            out.append(src[i + 1])
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out), quoted, i


def _skip_heredoc_body(src: str, body_start: int, delim: str, strip_tabs: bool) -> tuple[str, int]:
    """Advance past a heredoc body until an own-line delimiter match (or EOF), returning (body_text, next_pos)."""
    n = len(src)
    pos = body_start
    while pos <= n:
        nl = src.find("\n", pos)
        line_end = n if nl == -1 else nl
        line = src[pos:line_end]
        cmp_line = line.lstrip("\t") if strip_tabs else line
        if cmp_line == delim:
            return src[body_start:pos], (n if nl == -1 else nl + 1)
        if nl == -1:
            break
        pos = nl + 1
    return src[body_start:n], n


def _extract_unquoted_substitutions(text: str, out: list[str]) -> None:
    """Extract `$(...)` and `` `...` `` command substitutions from an unquoted heredoc body."""
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch == "$" and i + 1 < n and text[i + 1] == "(":
            if i + 2 < n and text[i + 2] == "(":
                i += 3
                continue
            inner, i = _slice_cmdsub_balanced(text, i + 2)
            out.append(inner)
            continue
        if ch == "`":
            j = i + 1
            inner_chars: list[str] = []
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    nx = text[j + 1]
                    if nx in ("$", "`", "\\"):
                        inner_chars.append(nx)
                    elif nx != "\n":
                        inner_chars.append("\\" + nx)
                    j += 2
                elif text[j] == "`":
                    j += 1
                    break
                else:
                    inner_chars.append(text[j])
                    j += 1
            out.append("".join(inner_chars))
            i = j
            continue
        i += 1


def _slice_cmdsub_balanced(src: str, start: int) -> tuple[str, int]:
    """Slice inner content of `$(...)` starting right after `$(` respecting quotes, escapes, `#` comments, and nesting."""
    n = len(src)
    i = start
    depth = 1
    in_sq = False
    in_dq = False
    word_start = True
    while i < n and depth > 0:
        ch = src[i]
        if in_sq:
            if ch == "'":
                in_sq = False
            i += 1
            continue
        if in_dq:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == '"':
                in_dq = False
                i += 1
                continue
            if ch == "$" and i + 1 < n and src[i + 1] == "(":
                _, i = _slice_cmdsub_balanced(src, i + 2)
                continue
            if ch == "`":
                i += 1
                while i < n:
                    if src[i] == "\\" and i + 1 < n:
                        i += 2
                    elif src[i] == "`":
                        i += 1
                        break
                    else:
                        i += 1
                continue
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            i += 2
            word_start = False
            continue
        if ch == "#" and word_start:
            nl = src.find("\n", i + 1)
            i = n if nl == -1 else nl + 1
            word_start = True
            continue
        if ch == "$" and i + 1 < n and src[i + 1] == "'":
            i += 2
            while i < n:
                if src[i] == "\\" and i + 1 < n:
                    i += 2
                elif src[i] == "'":
                    i += 1
                    break
                else:
                    i += 1
            word_start = False
            continue
        if ch == "'":
            in_sq = True
            word_start = False
            i += 1
            continue
        if ch == '"':
            in_dq = True
            word_start = False
            i += 1
            continue
        if ch == "`":
            i += 1
            while i < n:
                if src[i] == "\\" and i + 1 < n:
                    i += 2
                elif src[i] == "`":
                    i += 1
                    break
                else:
                    i += 1
            word_start = False
            continue
        if ch == "(":
            depth += 1
            word_start = True
            i += 1
            continue
        if ch == ")":
            depth -= 1
            if depth == 0:
                return src[start:i], i + 1
            word_start = False
            i += 1
            continue
        word_start = ch in " \t\n;|&"
        i += 1
    return src[start:n], n


def _peek_next_operator(src: str, pos: int) -> str:
    """Return the control operator immediately following optional horizontal whitespace at `pos`."""
    n = len(src)
    while pos < n and src[pos] in " \t":
        pos += 1
    if pos >= n:
        return ""
    two = src[pos : pos + 2]
    if two in ("&&", "||", "|&", ";;"):
        return two
    if src[pos] in ";&|\n":
        return src[pos]
    return ""


def _shell_statements(script: str, *, _depth: int = 0) -> list[_ShellStmt]:
    """Explicit linear-time bash lexer returning statements with surrounding control separators preserved.

    Handles POSIX `\\<LF>` line continuations, heredocs (`<<EOF` / `<<'EOF'` / `<<-EOF`), single quotes,
    double quotes, ANSI-C `$'...'` quoting, unquoted `#` comments (preserving newline boundaries), FD
    redirections (`2>&1`, `>&2`, `&>`, `<>`, `>>`, `>`, `<`), array assignments `name=(...)`, unquoted PEP 508
    extras/URLs (`uvicorn[standard]==0.52.4`, `https://...`), nested command substitutions (`$(...)`, `` `...` ``),
    subshells `(...)`, brace groups `{ ... }`, and `if`/`elif`/`while`/`until` conditional scopes.

    Note: does not recursively parse nested shell strings (`bash -c "..."` / `sh -c "..."` / `eval`).
    """
    if _depth > 6:
        return []
    n = len(script)
    stmts: list[_ShellStmt] = []
    cur_vals: list[str] = []
    cur_raws: list[str] = []
    val_buf: list[str] = []
    raw_buf: list[str] = []
    tok_active = False
    skip_redir_target = False
    sep_before = ""
    pending_heredocs: list[tuple[str, bool, bool]] = []
    word_start = True
    in_array_assign = False
    array_paren_depth = 0
    cond_depth = 0
    group_stack: list[tuple[str, int]] = []  # ("("|"{", start_stmt_idx)
    extra_cmdsubs: list[str] = []

    def _flush_tok() -> None:
        nonlocal tok_active, skip_redir_target, cond_depth
        if not tok_active:
            return
        v = "".join(val_buf)
        r = "".join(raw_buf)
        val_buf.clear()
        raw_buf.clear()
        tok_active = False
        if skip_redir_target:
            skip_redir_target = False
            return
        # Track compound condition keywords at command position
        if not cur_vals and r == v:
            if v in ("if", "elif", "while", "until"):
                cond_depth += 1
                return
            if v in ("then", "do"):
                if cond_depth > 0:
                    cond_depth -= 1
                return
            if v in ("else", "fi", "done"):
                return
            if v == "{":
                group_stack.append(("{", len(stmts)))
                return
            if v == "}":
                return
        cur_vals.append(v)
        cur_raws.append(r)

    def _finish_stmt(sep_after: str) -> None:
        nonlocal sep_before
        _flush_tok()
        if cur_vals:
            stmts.append(
                _ShellStmt(
                    tokens=list(cur_vals),
                    raw_tokens=list(cur_raws),
                    sep_before=sep_before,
                    sep_after=sep_after,
                    in_compound=(cond_depth > 0),
                )
            )
            cur_vals.clear()
            cur_raws.clear()
            sep_before = sep_after
        elif sep_after in ("&&", "||", "|", "|&", "&", ";", ";;"):
            sep_before = sep_after
        elif sep_after == "\n" and sep_before not in ("&&", "||", "|", "|&"):
            sep_before = "\n"

    def _mark_group_compound(start_idx: int) -> None:
        for k in range(start_idx, len(stmts)):
            s = stmts[k]
            stmts[k] = _ShellStmt(s.tokens, s.raw_tokens, s.sep_before, s.sep_after, True)

    i = 0
    while i < n:
        ch = script[i]

        # POSIX odd-backslash LF line continuation (never CRLF)
        if ch == "\\" and i + 1 < n and script[i + 1] == "\n":
            i += 2
            continue

        # Comments at word start (preserve trailing '\n'!)
        if ch == "#" and word_start and not tok_active:
            nl = script.find("\n", i + 1)
            i = n if nl == -1 else nl
            continue

        # Horizontal whitespace (plus stray CR)
        if ch in " \t\r":
            _flush_tok()
            word_start = True
            i += 1
            continue

        # Newline: finish statement and drain queued heredocs
        if ch == "\n":
            _finish_stmt("\n")
            i += 1
            if pending_heredocs:
                for delim, strip_tabs, is_quoted in pending_heredocs:
                    body_text, i = _skip_heredoc_body(script, i, delim, strip_tabs)
                    if not is_quoted:
                        _extract_unquoted_substitutions(body_text, extra_cmdsubs)
                pending_heredocs.clear()
            word_start = True
            continue

        # Closing brace as command terminator (`}` followed by whitespace/separator)
        if ch == "}" and word_start and not tok_active and (i + 1 >= n or script[i + 1] in " \t\r\n;|&"):
            _finish_stmt("}")
            if group_stack and group_stack[-1][0] == "{":
                _, start_idx = group_stack.pop()
                nxt = _peek_next_operator(script, i + 1)
                if nxt in ("&&", "||", "|", "|&", "&"):
                    _mark_group_compound(start_idx)
            i += 1
            word_start = True
            continue

        # Backslash escape outside quotes
        if ch == "\\" and i + 1 < n:
            tok_active = True
            val_buf.append(script[i + 1])
            raw_buf.append("\\" + script[i + 1])
            word_start = False
            i += 2
            continue

        # Single quotes
        if ch == "'":
            tok_active = True
            raw_buf.append("'")
            j = script.find("'", i + 1)
            if j == -1:
                val_buf.append(script[i + 1 :])
                raw_buf.append(script[i + 1 :])
                i = n
            else:
                val_buf.append(script[i + 1 : j])
                raw_buf.append(script[i + 1 : j + 1])
                i = j + 1
            word_start = False
            continue

        # ANSI-C quotes $'...'
        if ch == "$" and i + 1 < n and script[i + 1] == "'":
            tok_active = True
            i += 2
            s0 = i
            while i < n:
                if script[i] == "\\" and i + 1 < n:
                    i += 2
                elif script[i] == "'":
                    break
                else:
                    i += 1
            chunk = script[s0:i]
            val_buf.append(_decode_ansi_c_chunk(chunk))
            raw_buf.append("$'" + chunk + ("'" if i < n else ""))
            if i < n:
                i += 1
            word_start = False
            continue

        # Double quotes (preserve multi-line body, extract inner $(...) & `...`)
        if ch == '"':
            tok_active = True
            raw_buf.append('"')
            i += 1
            while i < n:
                c2 = script[i]
                if c2 == "\\" and i + 1 < n:
                    nx = script[i + 1]
                    if nx == "\n":
                        i += 2
                        continue
                    if nx in ('"', "\\", "$", "`"):
                        val_buf.append(nx)
                    else:
                        val_buf.append("\\" + nx)
                    raw_buf.append("\\" + nx)
                    i += 2
                    continue
                if c2 == '"':
                    raw_buf.append('"')
                    i += 1
                    break
                if c2 == "$" and i + 1 < n and script[i + 1] == "(":
                    if i + 2 < n and script[i + 2] == "(":
                        val_buf.append("$((")
                        raw_buf.append("$((")
                        i += 3
                        continue
                    inner, end_pos = _slice_cmdsub_balanced(script, i + 2)
                    extra_cmdsubs.append(inner)
                    frag = script[i:end_pos]
                    val_buf.append(frag)
                    raw_buf.append(frag)
                    i = end_pos
                    continue
                if c2 == "`":
                    j = i + 1
                    inner_chars: list[str] = []
                    while j < n:
                        if script[j] == "\\" and j + 1 < n:
                            nx = script[j + 1]
                            if nx in ("$", "`", "\\", '"'):
                                inner_chars.append(nx)
                            elif nx != "\n":
                                inner_chars.append("\\" + nx)
                            j += 2
                        elif script[j] == "`":
                            j += 1
                            break
                        else:
                            inner_chars.append(script[j])
                            j += 1
                    extra_cmdsubs.append("".join(inner_chars))
                    frag = script[i:j]
                    val_buf.append(frag)
                    raw_buf.append(frag)
                    i = j
                    continue
                val_buf.append(c2)
                raw_buf.append(c2)
                i += 1
            word_start = False
            continue

        # Unquoted backtick substitution
        if ch == "`":
            tok_active = True
            j = i + 1
            inner_chars = []
            while j < n:
                if script[j] == "\\" and j + 1 < n:
                    nx = script[j + 1]
                    if nx in ("$", "`", "\\"):
                        inner_chars.append(nx)
                    elif nx != "\n":
                        inner_chars.append("\\" + nx)
                    j += 2
                elif script[j] == "`":
                    j += 1
                    break
                else:
                    inner_chars.append(script[j])
                    j += 1
            extra_cmdsubs.append("".join(inner_chars))
            frag = script[i:j]
            val_buf.append(frag)
            raw_buf.append(frag)
            i = j
            word_start = False
            continue

        # Unquoted $(...) command substitution
        if ch == "$" and i + 1 < n and script[i + 1] == "(":
            tok_active = True
            if i + 2 < n and script[i + 2] == "(":
                val_buf.append("$((")
                raw_buf.append("$((")
                i += 3
                word_start = False
                continue
            inner, end_pos = _slice_cmdsub_balanced(script, i + 2)
            extra_cmdsubs.append(inner)
            frag = script[i:end_pos]
            val_buf.append(frag)
            raw_buf.append(frag)
            i = end_pos
            word_start = False
            continue

        # Control operators: ;, ;;, |, ||, |&, &, &&
        if ch == ";":
            if i + 1 < n and script[i + 1] == ";":
                _finish_stmt(";;")
                i += 2
            else:
                _finish_stmt(";")
                i += 1
            word_start = True
            continue

        if ch == "|":
            if i + 1 < n and script[i + 1] == "|":
                _finish_stmt("||")
                i += 2
            elif i + 1 < n and script[i + 1] == "&":
                _finish_stmt("|&")
                i += 2
            else:
                _finish_stmt("|")
                i += 1
            word_start = True
            continue

        if ch == "&":
            if i + 1 < n and script[i + 1] == "&":
                _finish_stmt("&&")
                i += 2
                word_start = True
                continue
            if i + 1 < n and script[i + 1] == ">":
                # `&>` or `&>>` redirection (not background `&`)
                _flush_tok()
                i += 3 if (i + 2 < n and script[i + 2] == ">") else 2
                skip_redir_target = True
                word_start = True
                continue
            _finish_stmt("&")
            i += 1
            word_start = True
            continue

        # Parentheses: array assignment `name=(...)` vs subshell `(...)`
        if ch == "(":
            if tok_active and val_buf and val_buf[-1].endswith("="):
                _flush_tok()
                in_array_assign = True
                array_paren_depth += 1
                cur_vals.append("(")
                cur_raws.append("(")
                i += 1
                word_start = True
                continue
            if in_array_assign:
                _flush_tok()
                array_paren_depth += 1
                cur_vals.append("(")
                cur_raws.append("(")
                i += 1
                word_start = True
                continue
            _finish_stmt("(")
            group_stack.append(("(", len(stmts)))
            i += 1
            word_start = True
            continue

        if ch == ")":
            if in_array_assign and array_paren_depth > 0:
                _flush_tok()
                array_paren_depth -= 1
                if array_paren_depth == 0:
                    in_array_assign = False
                cur_vals.append(")")
                cur_raws.append(")")
                i += 1
                word_start = True
                continue
            _finish_stmt(")")
            if group_stack and group_stack[-1][0] == "(":
                _, start_idx = group_stack.pop()
                nxt = _peek_next_operator(script, i + 1)
                if nxt in ("&&", "||", "|", "|&", "&"):
                    _mark_group_compound(start_idx)
            i += 1
            word_start = True
            continue

        # Redirections: `<`, `<<`, `<<-`, `<<<`, `<&`, `<>`, `>`, `>>`, `>&`, `>|`
        if ch in ("<", ">"):
            # Drop leading numeric fd prefix (e.g. `2>&1`, `1>/dev/null`) rather than emitting `2` as an argv token
            if tok_active and raw_buf == val_buf and _FD_REDIR_PREFIX_RE.fullmatch("".join(val_buf)):
                val_buf.clear()
                raw_buf.clear()
                tok_active = False
            else:
                _flush_tok()

            if ch == "<" and script[i : i + 3] == "<<<":
                i += 3
                skip_redir_target = True
                word_start = True
                continue
            if ch == "<" and script[i : i + 2] == "<<":
                strip_tabs = i + 2 < n and script[i + 2] == "-"
                op_end = i + (3 if strip_tabs else 2)
                delim, is_quoted, end_d = _parse_heredoc_delim(script, op_end)
                if delim:
                    pending_heredocs.append((delim, strip_tabs, is_quoted))
                i = end_d
                word_start = True
                continue
            if script[i : i + 2] in (">&", "<&"):
                i += 2
                # FD dup target (`2>&1`, `>&2`, `2>&-`) consumes immediately following word without emitting argv token
                while i < n and script[i] in " \t":
                    i += 1
                while i < n and script[i] not in " \t\r\n;|&()<>":
                    i += 1
                word_start = True
                continue
            if script[i : i + 2] in (">>", ">|", "<>"):
                i += 2
            else:
                i += 1
            skip_redir_target = True
            word_start = True
            continue

        # Ordinary word char (keep `[` `]` `:` `+` `$` `{` `}` `=` intact in unquoted tokens!)
        tok_active = True
        val_buf.append(ch)
        raw_buf.append(ch)
        word_start = False
        i += 1

    _finish_stmt("")
    for sub in extra_cmdsubs:
        stmts.extend(_shell_statements(sub, _depth=_depth + 1))
    return stmts


def _iter_uses_nodes(raw_text: str) -> list[tuple[str, yaml.ScalarNode, yaml.MappingNode]]:
    """Walk the composed YAML AST to yield `(label, uses_scalar_node, container_mapping_node)` for every
    job-level or step-level `uses:` entry. Fails closed on non-scalar `uses:` nodes."""
    root = yaml.compose(raw_text, Loader=yaml.SafeLoader)
    if not isinstance(root, yaml.MappingNode):
        return []
    found: list[tuple[str, yaml.ScalarNode, yaml.MappingNode]] = []
    for k_node, v_node in root.value:
        if k_node.value != "jobs" or not isinstance(v_node, yaml.MappingNode):
            continue
        for job_key, job_val in v_node.value:
            if not isinstance(job_val, yaml.MappingNode):
                continue
            job_name = str(job_key.value)
            for jk, jv in job_val.value:
                if jk.value == "uses":
                    assert isinstance(jv, yaml.ScalarNode), (
                        f"job {job_name!r} `uses` must be a scalar string, got {type(jv).__name__}"
                    )
                    found.append((f"job {job_name!r}", jv, job_val))
                elif jk.value == "steps" and isinstance(jv, yaml.SequenceNode):
                    for idx, step_node in enumerate(jv.value):
                        if not isinstance(step_node, yaml.MappingNode):
                            continue
                        step_name = f"step {idx}"
                        uses_node: yaml.Node | None = None
                        for sk, sv in step_node.value:
                            if sk.value == "name" and isinstance(sv, yaml.ScalarNode):
                                step_name = str(sv.value)
                            elif sk.value == "uses":
                                uses_node = sv
                        if uses_node is not None:
                            assert isinstance(uses_node, yaml.ScalarNode), (
                                f"job {job_name!r} step {step_name!r} `uses` must be a scalar string, "
                                f"got {type(uses_node).__name__}"
                            )
                            found.append((f"job {job_name!r} step {step_name!r}", uses_node, step_node))
    return found


def _extract_uses_version_comment(
    lines: list[str], val_node: yaml.ScalarNode, container_node: yaml.MappingNode
) -> str:
    """Return the genuine YAML `# ...` comment for a `uses:` scalar, ignoring comments hidden inside quoted
    sibling mapping values and rejecting multiline/block-scalar `uses:`."""
    assert val_node.style not in ("|", ">"), (
        f"block scalar `uses:` is forbidden at line {val_node.start_mark.line + 1}"
    )
    assert val_node.start_mark.line == val_node.end_mark.line, (
        f"multiline `uses:` is forbidden at line {val_node.start_mark.line + 1}"
    )
    line_idx = val_node.end_mark.line
    if line_idx >= len(lines):
        return ""
    tail = lines[line_idx][val_node.end_mark.column :]
    if not container_node.flow_style:
        m = re.fullmatch(r"[ \t]*(#.*)", tail)
        return m.group(1) if m else ""
    # Inside a flow mapping `{...}`: accept either a trailing comment right after `uses: <val>,` (nothing else on line)
    # OR a trailing comment strictly after the closing `}` of the enclosing flow mapping!
    m_inline = re.fullmatch(r"[ \t]*,?[ \t]*(#.*)", tail)
    if m_inline:
        return m_inline.group(1)
    end_idx = container_node.end_mark.line
    if end_idx < len(lines):
        after_flow = lines[end_idx][container_node.end_mark.column :]
        m_after = re.fullmatch(r"[ \t]*(#.*)", after_flow)
        if m_after:
            return m_after.group(1)
    return ""


def _find_pip_index(cmd: list[str]) -> int | None:
    """Return index of a `pip`/`pip3`/`pip-3.12`/`pip.__main__`/`.../pip`/`-mpip`/`-umpip` token in `cmd`, or None."""
    for idx, tok in enumerate(cmd):
        if tok in ("(", ")"):
            continue
        base = tok.rsplit("/", 1)[-1]
        if _PIP_BASENAME_RE.fullmatch(base):
            return idx
    return None


def _find_pytest_index(cmd: list[str]) -> int | None:
    """Return index of a `pytest`/`pytest-3`/`pytest.__main__`/`py.test`/`.../pytest`/`-mpytest`/`-umpytest` token."""
    for idx, tok in enumerate(cmd):
        if tok in ("(", ")"):
            continue
        base = tok.rsplit("/", 1)[-1]
        if _PYTEST_BASENAME_RE.fullmatch(base):
            return idx
    return None


def _assert_req_file_pinned(req_path: str, step_name: str, *, base_dir: Path | None = None) -> None:
    """Reject remote URLs in `-r`/`-c` and verify local requirement files exist and exact-pin every package."""
    assert "://" not in req_path, f"step {step_name!r} forbids remote requirement/constraint URL {req_path!r}"
    root = base_dir if base_dir is not None else REPO_ROOT
    candidate = (root / req_path).resolve()
    assert candidate.is_file(), f"step {step_name!r} references missing requirement/constraint file {req_path!r}"
    for lineno, raw_line in enumerate(candidate.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        spec = line.split(";", 1)[0].strip()
        assert "://" not in spec and "*" not in spec and _PINNED_PKG_RE.fullmatch(spec), (
            f"step {step_name!r} requirement file {req_path}:{lineno} must exact-pin package with `==<ver>`, got {spec!r}"
        )


def _assert_pip_install_pinned(
    cmd: list[str], step_name: str, *, allow_local_sdist: bool = False, base_dir: Path | None = None
) -> None:
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
                _assert_req_file_pinned(arg, step_name, base_dir=base_dir)
            i += 2
            continue
        if tok.startswith("-"):
            if tok.startswith("--editable="):
                target = tok.split("=", 1)[1]
                assert _LOCAL_EDITABLE_SPEC_RE.fullmatch(target), (
                    f"step {step_name!r} forbids editable VCS/URL/remote install target {target!r}"
                )
            elif tok.startswith("-e") and len(tok) > 2:
                # In pip optparse, `-e<arg>` passes `<arg>` verbatim (even `-e=foo` passes `=foo`!)
                target = tok[2:]
                assert _LOCAL_EDITABLE_SPEC_RE.fullmatch(target), (
                    f"step {step_name!r} forbids editable VCS/URL/remote install target {target!r}"
                )
            elif tok.startswith(("--requirement=", "--constraint=")):
                _assert_req_file_pinned(tok.split("=", 1)[1], step_name, base_dir=base_dir)
            elif tok.startswith(("-r", "-c")) and len(tok) > 2 and not tok.startswith("--"):
                arg = tok[2:]
                if arg.startswith("="):
                    arg = arg[1:]
                _assert_req_file_pinned(arg, step_name, base_dir=base_dir)
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


def _parse_tx_workers(spec: str, step_name: str) -> int:
    s = spec.strip()
    star = s.find("*")
    if star != -1:
        raw_mult = s[:star].strip()
        assert raw_mult, f"step {step_name!r} has invalid --tx multiplier in {spec!r}"
        try:
            mult = int(raw_mult)
        except ValueError as exc:
            raise AssertionError(f"step {step_name!r} has invalid --tx multiplier in {spec!r}") from exc
        assert mult >= 1, f"step {step_name!r} has non-positive --tx multiplier in {spec!r}"
        return mult
    return 1


def _assert_pytest_worker_cap(cmd: list[str], step_name: str, *, _depth: int = 0) -> None:
    """Assert every `pytest` invocation caps xdist workers at <= 16 (including bundled `-qn`, `-o addopts=...`,
    `--override-ini=addopts=...`, `--tx`, and `python -m pytest.__main__`)."""
    assert _depth <= 4, f"step {step_name!r} nested addopts recursion too deep"
    pytest_idx = _find_pytest_index(cmd)
    if pytest_idx is None:
        return
    args = [t for t in cmd[pytest_idx + 1 :] if t not in ("(", ")")]
    num_vals: list[str] = []
    max_procs: list[str] = []
    tx_total = 0

    def _validate_override_ini(val: str) -> None:
        if "=" not in val:
            return
        k, rhs = val.split("=", 1)
        if k.strip() != "addopts":
            return
        try:
            inner = shlex.split(rhs)
        except ValueError:
            inner = rhs.split()
        _assert_pytest_worker_cap(["pytest", *inner], step_name, _depth=_depth + 1)

    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--":
            break
        if tok in ("-n", "--numprocesses"):
            assert i + 1 < len(args), f"step {step_name!r} has dangling {tok}"
            num_vals.append(args[i + 1])
            i += 2
            continue
        if tok.startswith("--numprocesses="):
            num_vals.append(tok.split("=", 1)[1])
            i += 1
            continue
        if tok == "--maxprocesses":
            assert i + 1 < len(args), f"step {step_name!r} has dangling --maxprocesses"
            max_procs.append(args[i + 1])
            i += 2
            continue
        if tok.startswith("--maxprocesses="):
            max_procs.append(tok.split("=", 1)[1])
            i += 1
            continue
        if tok == "--tx":
            assert i + 1 < len(args), f"step {step_name!r} has dangling --tx"
            tx_total += _parse_tx_workers(args[i + 1], step_name)
            i += 2
            continue
        if tok.startswith("--tx="):
            tx_total += _parse_tx_workers(tok.split("=", 1)[1], step_name)
            i += 1
            continue
        if tok == "--override-ini":
            assert i + 1 < len(args), f"step {step_name!r} has dangling --override-ini"
            _validate_override_ini(args[i + 1])
            i += 2
            continue
        if tok.startswith("--override-ini="):
            _validate_override_ini(tok.split("=", 1)[1])
            i += 1
            continue
        if tok.startswith("-") and not tok.startswith("--") and len(tok) > 1:
            cluster = tok[1:]
            advance = 1
            for cidx, ch in enumerate(cluster):
                if ch == "o":
                    suffix = cluster[cidx + 1 :]
                    if suffix:
                        v = suffix[1:] if suffix.startswith("=") else suffix
                    else:
                        assert i + 1 < len(args), f"step {step_name!r} has dangling -o"
                        v = args[i + 1]
                        advance = 2
                    _validate_override_ini(v)
                    break
                if ch == "n":
                    suffix = cluster[cidx + 1 :]
                    if suffix:
                        nv = suffix[1:] if suffix.startswith("=") else suffix
                    else:
                        assert i + 1 < len(args), f"step {step_name!r} has dangling {tok}"
                        nv = args[i + 1]
                        advance = 2
                    num_vals.append(nv)
                    break
                if ch in _PYTEST_VAL_SHORT:
                    if cidx == len(cluster) - 1:
                        advance = 2
                    break
            i += advance
            continue
        i += 1

    assert tx_total <= 16, f"step {step_name!r} total --tx workers ({tx_total}) exceeds max 16"
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
    """Allow strictly `False` or `${{ github.event_name == 'pull_request' }}` (single- or double-quoted)."""
    assert cip is not None, "ci.yml concurrency.cancel-in-progress must not be null"
    if cip is False:
        return
    assert cip is not True, "ci.yml concurrency.cancel-in-progress must not unconditionally cancel main pushes"
    assert isinstance(cip, str), f"ci.yml concurrency.cancel-in-progress must be False or PR expression, got {cip!r}"
    stripped = cip.strip()
    assert _SAFE_CANCEL_EXPR_RE.fullmatch(stripped), (
        f"ci.yml concurrency.cancel-in-progress must be False or exactly "
        f"`${{{{ github.event_name == 'pull_request' }}}}`, got {cip!r}"
    )


def _assert_safe_shell_override(shell_val: Any, context: str) -> None:
    """Reject custom `shell:` templates that drop `-e` / `errexit` (e.g., `bash {0}`)."""
    if shell_val is None:
        return
    assert isinstance(shell_val, str) and shell_val.strip(), f"{context} shell must be a non-empty string"
    s = shell_val.strip()
    if s in ("bash", "sh"):
        return
    assert "{0}" in s and (
        re.search(r"(?:^|\s)-[a-zA-Z]*e[a-zA-Z]*(?:\s|$)", s) or "-o errexit" in s
    ), f"{context} custom shell override drops errexit (-e): {shell_val!r}"


def _raw_token_has_expanded_runner_temp_guard(raw_tok: str) -> bool:
    """True iff `raw_tok` contains `${RUNNER_TEMP:?...}` OUTSIDE single quotes (i.e. bash will actually expand it)."""
    in_sq = False
    in_dq = False
    i = 0
    n = len(raw_tok)
    unquoted_chunks: list[str] = []
    cur: list[str] = []
    while i < n:
        ch = raw_tok[i]
        if in_sq:
            if ch == "'":
                in_sq = False
            i += 1
            continue
        if in_dq:
            if ch == "\\" and i + 1 < n:
                cur.append(raw_tok[i : i + 2])
                i += 2
                continue
            if ch == '"':
                in_dq = False
                i += 1
                continue
            cur.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            cur.append(raw_tok[i : i + 2])
            i += 2
            continue
        if ch == "'":
            if cur:
                unquoted_chunks.append("".join(cur))
                cur = []
            in_sq = True
            i += 1
            continue
        if ch == '"':
            in_dq = True
            i += 1
            continue
        cur.append(ch)
        i += 1
    if cur:
        unquoted_chunks.append("".join(cur))
    return any(_RUNNER_TEMP_GUARD_RE.search(chunk) for chunk in unquoted_chunks)


def _assert_no_errexit_disabler(stmts: list[_ShellStmt], step_name: str) -> None:
    """Forbid `set +e`, `set +o errexit`, and `shopt -u nullglob` in guarded steps."""
    for s in stmts:
        toks = s.tokens
        if not toks:
            continue
        if toks[0] == "set":
            for idx, t in enumerate(toks[1:], start=1):
                if re.fullmatch(r"\+[a-zA-Z]*e[a-zA-Z]*", t):
                    raise AssertionError(f"step {step_name!r} disables errexit via {toks!r}")
                if t == "+o" and idx + 1 < len(toks) and toks[idx + 1] == "errexit":
                    raise AssertionError(f"step {step_name!r} disables errexit via {toks!r}")
        if toks[0] == "shopt" and "-u" in toks[1:] and "nullglob" in toks[1:]:
            raise AssertionError(f"step {step_name!r} disables nullglob via {toks!r}")


def _assert_workflow_all_guards(path: Path) -> None:
    """Shared implementation for `test_all_workflows_enforce_supply_chain_permissions_and_checkout`."""
    raw_text = path.read_text(encoding="utf-8")
    lines = raw_text.splitlines()
    doc = _load_yaml_strict(raw_text)

    assert doc.get("permissions") in ({"contents": "read"}, {}), (
        f"{path.name} must declare minimal top-level permissions, got {doc.get('permissions')!r}"
    )

    for label, val_node, container_node in _iter_uses_nodes(raw_text):
        uses = str(val_node.value)
        assert _SHA_PIN_RE.fullmatch(uses), (
            f"{path.name} {label} must pin action to 40-hex commit SHA, got {uses!r}"
        )
        comment = _extract_uses_version_comment(lines, val_node, container_node)
        line_idx = val_node.end_mark.line
        preview = lines[line_idx].strip() if line_idx < len(lines) else "<EOF>"
        assert re.search(r"#\s*v\d+", comment), (
            f"{path.name}:{line_idx + 1} pinned action ({label}) must carry `# v<ver>` comment: {preview!r}"
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
                assert isinstance(uses, str) and _SHA_PIN_RE.fullmatch(uses), (
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
                if _find_pytest_index(stmt.tokens) is not None:
                    _assert_pytest_worker_cap(stmt.tokens, f"{path.name}:{name}")


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_all_workflows_enforce_supply_chain_permissions_and_checkout(path: Path) -> None:
    """Every workflow under `.github/workflows/*.y*ml` must pin every action (including job-level reusable
    workflows) to a 40-hex commit SHA with a `# v<ver>` comment, declare minimal top-level and job-level
    permissions (`{}` or `contents: read`), set `persist-credentials: false` strictly on `actions/checkout`,
    exact-pin every `pip install` package, and cap every `pytest` invocation at <= 16 workers."""
    _assert_workflow_all_guards(path)


def _find_node_test_index(cmd: list[str]) -> int | None:
    """Return index of `node` followed by `--test` anywhere in `cmd` (catches wrappers/subshells)."""
    for idx, tok in enumerate(cmd):
        if tok in ("(", ")"):
            continue
        if (tok == "node" or tok.endswith("/node")) and "--test" in cmd[idx + 1 :]:
            return idx
    return None


def _check_ci_workflow_doc(doc: dict[str, Any], *, base_dir: Path | None = None) -> None:
    """Core validator for `.github/workflows/ci.yml` invariants (factored for direct unit testing)."""
    wf_env = doc.get("env") or {}
    assert "PORCHLAMP_PERF_HOST_FACTOR" not in wf_env, (
        "PORCHLAMP_PERF_HOST_FACTOR must not be set at workflow level"
    )
    assert "PYTEST_ADDOPTS" not in wf_env, "PYTEST_ADDOPTS must not be set at workflow level"

    wf_defaults = (doc.get("defaults") or {}).get("run") or {}
    assert wf_defaults.get("shell") == "bash", (
        f"ci.yml must set defaults.run.shell: bash (enables bash -eo pipefail), got {wf_defaults.get('shell')!r}"
    )
    wf_wd = str(wf_defaults.get("working-directory") or "")
    assert not _TMP_PATH_RE.search(wf_wd), f"workflow defaults.run.working-directory must not use /tmp, got {wf_wd!r}"

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
    saw_migrations_check = False
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
        job_defaults = (job.get("defaults") or {}).get("run") or {}
        _assert_safe_shell_override(job_defaults.get("shell"), f"job {job_name!r} defaults.run")
        job_wd = str(job_defaults.get("working-directory") or "")
        assert not _TMP_PATH_RE.search(job_wd), (
            f"job {job_name!r} defaults.run.working-directory must not use /tmp, got {job_wd!r}"
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

            _assert_safe_shell_override(step.get("shell"), f"job {job_name!r} step {name!r}")

            wd = str(step.get("working-directory") or "")
            assert not _TMP_PATH_RE.search(wd), (
                f"step {job_name!r}/{name!r} working-directory must not use /tmp, got {wd!r}"
            )

            run = str(step.get("run") or "")
            assert "PORCHLAMP_PERF_HOST_FACTOR" not in run, (
                f"step {name!r} must not assign or reference PORCHLAMP_PERF_HOST_FACTOR inline in run:"
            )

            stmts = _shell_statements(run)
            for stmt in stmts:
                for tok in stmt.tokens:
                    assert not _TMP_PATH_RE.search(tok), (
                        f"step {job_name!r}/{name!r} must not reference hardcoded /tmp paths, got {tok!r}"
                    )

            step_has_pytest = any(_find_pytest_index(s.tokens) is not None for s in stmts)
            is_primary_pytest = job_name == "test" and name == "Run test suite"
            if is_primary_pytest:
                assert step_has_pytest, "primary `Run test suite` step must invoke pytest"
                assert not any(_find_node_test_index(s.tokens) is not None for s in stmts), (
                    "primary `Run test suite` step must not run standalone `node --test` under host factor"
                )
                saw_primary_pytest = True
                if "PORCHLAMP_PERF_HOST_FACTOR" in env:
                    raw_factor = env["PORCHLAMP_PERF_HOST_FACTOR"]
                    assert not isinstance(raw_factor, bool), (
                        f"PORCHLAMP_PERF_HOST_FACTOR must not be bool, got {raw_factor!r}"
                    )
                    factor_str = str(raw_factor).strip()
                    assert _FACTOR_NUM_RE.fullmatch(factor_str), (
                        f"PORCHLAMP_PERF_HOST_FACTOR must be plain JS/Python decimal digits, got {raw_factor!r}"
                    )
                    val = float(factor_str)
                    assert math.isfinite(val) and 1.0 <= val <= 2.0, (
                        f"PORCHLAMP_PERF_HOST_FACTOR must be finite in [1, 2], got {raw_factor!r}"
                    )
            else:
                assert "PORCHLAMP_PERF_HOST_FACTOR" not in env, (
                    f"non-primary step {job_name!r}/{name!r} must not set PORCHLAMP_PERF_HOST_FACTOR"
                )

            if job_name == "package":
                for stmt in stmts:
                    for raw_tok in stmt.raw_tokens:
                        assert not _BARE_RUNNER_TEMP_RE.search(raw_tok), (
                            f"package step {name!r} must use fail-fast `${{RUNNER_TEMP:?...}}`, got {raw_tok!r}"
                        )
                        if _RUNNER_TEMP_ANY_RE.search(raw_tok):
                            assert _raw_token_has_expanded_runner_temp_guard(raw_tok), (
                                f"package step {name!r} must not single-quote `${{RUNNER_TEMP:?...}}`: {raw_tok!r}"
                            )

            if job_name == "package" and name == "sdist contains the store migrations":
                saw_migrations_check = True
                _assert_no_errexit_disabler(stmts, name)
                tar_stmts = [s for s in stmts if s.tokens and s.tokens[0] == "tar"]
                grep_stmts = [s for s in stmts if s.tokens and s.tokens[0] == "grep"]
                assert tar_stmts and grep_stmts, "migrations step must pipe `tar` into `grep`"
                for gs in grep_stmts:
                    assert gs.is_errexit_guarded, "migrations grep step must not suppress errexit"
                    for t in gs.tokens[1:]:
                        assert t not in ("-q", "--quiet", "--silent"), (
                            f"migrations grep must drain stdin (no `-q`/`--quiet`/`--silent` SIGPIPE risk), got {gs.tokens!r}"
                        )
                        if t.startswith("-") and not t.startswith("--"):
                            assert "q" not in t[1:], (
                                f"migrations grep must not pass `-q` flag ({t!r}) under pipefail"
                            )

            allow_local_sdist = job_name == "package" and "Non-editable install (W4-1:" in name
            for stmt_idx, stmt in enumerate(stmts):
                cmd = stmt.tokens
                for tok in cmd:
                    assert not tok.startswith(("PYTEST_ADDOPTS=", "PYTEST_ADDOPTS+=")), (
                        f"step {name!r} must not set inline {tok!r}"
                    )

                if _find_pytest_index(cmd) is not None:
                    _assert_no_errexit_disabler(stmts, name)
                    assert stmt.is_errexit_guarded, f"pytest step {name!r} must not suppress errexit: {cmd!r}"
                    assert len(cmd) >= 3 and cmd[:3] == ["env", "-u", "PYTEST_ADDOPTS"], (
                        f"pytest step {name!r} must clear PYTEST_ADDOPTS via `env -u PYTEST_ADDOPTS`, got {cmd!r}"
                    )
                    _assert_pytest_worker_cap(cmd, name)

                if _find_pip_index(cmd) is not None:
                    _assert_pip_install_pinned(cmd, name, allow_local_sdist=allow_local_sdist, base_dir=base_dir)
                    if any(_BUILD_PIN_RE.fullmatch(tok) for tok in cmd):
                        saw_build_install = True
                    if any(_PYTEST_PIN_RE.fullmatch(tok) for tok in cmd):
                        saw_deps_install = True

                # Every `node ... --test` invocation must be preceded by ordered nullglob guard + `${suites[@]}`
                if _find_node_test_index(cmd) is not None:
                    saw_node = True
                    _assert_no_errexit_disabler(stmts, name)
                    assert stmt.is_errexit_guarded, f"node step {name!r} must not suppress errexit: {cmd!r}"
                    shopt_idxs = [
                        i
                        for i, s in enumerate(stmts)
                        if s.tokens == ["shopt", "-s", "nullglob"] and s.is_errexit_guarded
                    ]
                    all_suites_assigns = [
                        i
                        for i, s in enumerate(stmts)
                        if s.tokens and s.tokens[0].startswith(("suites=", "suites+="))
                    ]
                    assign_idxs = [
                        i
                        for i, s in enumerate(stmts)
                        if s.tokens == ["suites=", "(", "web/app/tests/*.test.mjs", ")"] and s.is_errexit_guarded
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
                    assert all_suites_assigns == assign_idxs and len(assign_idxs) == 1, (
                        f"node step {name!r} must assign `suites=(web/app/tests/*.test.mjs)` exactly once, "
                        f"got assignments at {all_suites_assigns}"
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
                _assert_no_errexit_disabler(stmts, name)
                shopt_idxs = [
                    i
                    for i, s in enumerate(stmts)
                    if s.tokens == ["shopt", "-s", "nullglob"] and s.is_errexit_guarded
                ]
                all_sdist_assigns = [
                    i
                    for i, s in enumerate(stmts)
                    if s.tokens and s.tokens[0].startswith(("sdists=", "sdists+="))
                ]
                assign_idxs = [
                    i
                    for i, s in enumerate(stmts)
                    if s.tokens == ["sdists=", "(", "dist/*.tar.gz", ")"] and s.is_errexit_guarded
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
                all_venv_idxs = [i for i, s in enumerate(stmts) if "venv" in s.tokens]
                venv_idxs = [
                    i
                    for i, s in enumerate(stmts)
                    if "venv" in s.tokens
                    and s.is_errexit_guarded
                    and any(_raw_token_has_expanded_runner_temp_guard(rt) for rt in s.raw_tokens)
                ]
                all_pip_idxs = [i for i, s in enumerate(stmts) if _find_pip_index(s.tokens) is not None]
                pip_idxs = [
                    i
                    for i, s in enumerate(stmts)
                    if _find_pip_index(s.tokens) is not None
                    and s.is_errexit_guarded
                    and any(_raw_token_has_expanded_runner_temp_guard(rt) for rt in s.raw_tokens)
                    and any(_LOCAL_SDIST_SPEC_RE.fullmatch(t) for t in s.tokens)
                ]
                assert shopt_idxs, "sdist install step must enable standalone `shopt -s nullglob`"
                assert all_sdist_assigns == assign_idxs and len(assign_idxs) == 1, (
                    f"sdist install step must assign `sdists=(dist/*.tar.gz)` exactly once, got {all_sdist_assigns}"
                )
                assert cnt_idxs, (
                    "sdist install step must have standalone (not `&&`/`||`/`&`-chained) `test \"${#sdists[@]}\" -eq 1`"
                )
                assert file_idxs, (
                    "sdist install step must have standalone (not `&&`/`||`/`&`-chained) `test -f \"${sdists[0]}\"`"
                )
                assert venv_idxs and all_venv_idxs == venv_idxs, (
                    f"all venv invocations in sdist install must be guarded and use `${{RUNNER_TEMP:?...}}`: {all_venv_idxs}"
                )
                assert pip_idxs and all_pip_idxs == pip_idxs, (
                    f"all pip invocations in sdist install must be guarded and use `${{RUNNER_TEMP:?...}}`: {all_pip_idxs}"
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
                _assert_no_errexit_disabler(stmts, name)
                assert step.get("working-directory") == "${{ runner.temp }}", (
                    f"sdist verification step must run outside checkout with `working-directory: ${{{{ runner.temp }}}}`, "
                    f"got {step.get('working-directory')!r}"
                )
                py_stmts = [
                    s
                    for s in stmts
                    if s.tokens
                    and (s.tokens[0].endswith("/python") or s.tokens[0].endswith("/python3"))
                    and s.is_errexit_guarded
                    and _raw_token_has_expanded_runner_temp_guard(s.raw_tokens[0])
                ]
                assert py_stmts, (
                    "sdist verification step must invoke Python directly via unquoted/double-quoted "
                    "`${RUNNER_TEMP:?...}` with errexit intact"
                )

    assert (
        saw_primary_pytest
        and saw_node
        and saw_deps_install
        and saw_build_install
        and saw_migrations_check
        and saw_sdist_install
        and saw_sdist_verify
    ), (
        f"missing expected ci.yml steps: pytest={saw_primary_pytest}, node={saw_node}, "
        f"deps={saw_deps_install}, build={saw_build_install}, migrations={saw_migrations_check}, "
        f"sdist_install={saw_sdist_install}, sdist_verify={saw_sdist_verify}"
    )


def test_ci_workflow_enforces_supply_chain_permissions_and_worker_cap() -> None:
    """CI supply-chain and runtime guard: ci.yml must define `defaults.run.shell: bash`, safe concurrency,
    positive job timeouts, cap all pytest xdist invocations at <= 16 workers, exact-pin all pip install
    packages (test deps and `build`), drain `tar` without `grep -q` SIGPIPE risk, run all
    `web/app/tests/*.test.mjs` suites with an ordered standalone nullglob guard, keep
    `PORCHLAMP_PERF_HOST_FACTOR` strictly on the primary `Run test suite` step `env:` (`1 <= factor <= 2`),
    guard `sdists=(dist/*.tar.gz)` with ordered standalone `set -e` checks, and use `${RUNNER_TEMP:?...}`
    (never `/tmp`)."""
    path = WORKFLOW_DIR / "ci.yml"
    doc = _load_yaml_strict(path.read_text(encoding="utf-8"))
    _check_ci_workflow_doc(doc)


def test_workflow_guard_helpers_handle_continuations_comments_and_adversarial_bypasses(tmp_path: Path) -> None:
    """Direct regression checks proving strict YAML parsing, bash lexer, YAML AST walker, `_check_ci_workflow_doc`,
    and pin/worker helpers catch adversarial mutants and avoid false positives on valid syntax."""
    # 1. Strict YAML duplicate-key loader (including YAML 1.1 `on` bool vs `"on"` string collision)
    with pytest.raises(ValueError, match="duplicate YAML key"):
        _load_yaml_strict("name: a\nname: b\n")
    with pytest.raises(ValueError, match="duplicate YAML key"):
        _load_yaml_strict('on: push\n"on": pull_request\n')

    # 2. Backslash continuations, inline `#` comments, FD redirections (`2>&1`), `|&`, subshells, heredocs, ANSI-C
    stmts = _shell_statements('python -m pytest \\\n  tests/ -n auto\npython -m pip install "build==1.6.1" \\\n  requests\n')
    assert len(stmts) == 2
    with pytest.raises(AssertionError):
        _assert_pytest_worker_cap(stmts[0].tokens, "cont-pytest")
    with pytest.raises(AssertionError):
        _assert_pip_install_pinned(stmts[1].tokens, "cont-pip")

    stmts_c = _shell_statements("shopt -s nullglob # prevent literal glob\nsuites=(web/app/tests/*.test.mjs)\n")
    assert [s.tokens for s in stmts_c] == [
        ["shopt", "-s", "nullglob"],
        ["suites=", "(", "web/app/tests/*.test.mjs", ")"],
    ]

    # FD redirection `2>&1` must NOT split a command at `&`
    for redir_cmd in (
        "python -m pip 2>&1 install evil",
        "python -m pytest 2>&1 tests/ -n auto",
        "pytest -n 2>&1 24",
    ):
        rs = _shell_statements(redir_cmd)
        assert len(rs) == 1, f"FD redirect wrongly split {redir_cmd!r} -> {rs!r}"
    with pytest.raises(AssertionError):
        _assert_pip_install_pinned(_shell_statements("python -m pip 2>&1 install evil")[0].tokens, "fd-pip")
    with pytest.raises(AssertionError):
        _assert_pytest_worker_cap(_shell_statements("pytest -n 2>&1 24")[0].tokens, "fd-pytest")

    # Contiguous punctuation `);(` and `|&` must split statements cleanly
    sub_pair = _shell_statements("(pytest tests/a -n auto --maxprocesses=16);(pytest tests/b -n auto)")
    assert len(sub_pair) == 2
    with pytest.raises(AssertionError):
        _assert_pytest_worker_cap(sub_pair[1].tokens, "subshell-split")
    pipe_both = _shell_statements("pytest tests/a -n auto --maxprocesses=16 |& pytest tests/b -n auto")
    assert len(pipe_both) == 2
    with pytest.raises(AssertionError):
        _assert_pytest_worker_cap(pipe_both[1].tokens, "pipe-both")

    # Unquoted PEP 508 extras, URLs, and local versions pass through `_shell_statements` intact
    for good_line in (
        "pip install uvicorn[standard]==0.52.4",
        'pip install --index-url https://pypi.org/simple "build==1.6.1"',
        "pip install pkg==1.0.0+cpu",
        'pip install "build==1.6.1" |& tee log',
        '(pip install "build==1.6.1");echo done',
    ):
        parsed = _shell_statements(good_line)
        for st in parsed:
            _assert_pip_install_pinned(st.tokens, "good-shell")
    assert _shell_statements("pytest -p no:cacheprovider")[0].tokens == ["pytest", "-p", "no:cacheprovider"]

    # Heredocs containing prose apostrophes never crash or hide commands between two heredocs
    hd_script = (
        "cat <<'EOF'\nit's don't fine\nEOF\n"
        "pip install evil-unpinned\n"
        "cat <<'EOF'\nwon't hide\nEOF\n"
    )
    hd_stmts = _shell_statements(hd_script)
    assert any(_find_pip_index(s.tokens) is not None for s in hd_stmts)
    with pytest.raises(AssertionError):
        for s in hd_stmts:
            _assert_pip_install_pinned(s.tokens, "hd-pip")

    # ANSI-C `$'...'` with `\'` never crashes or hides trailing commands
    ansi_stmts = _shell_statements("echo $'a\\'b' && pip install unpinned-evil # '\n")
    assert len(ansi_stmts) == 2
    with pytest.raises(AssertionError):
        _assert_pip_install_pinned(ansi_stmts[1].tokens, "ansi-pip")
    assert len(_shell_statements("echo $'it\\'s ok'\n")) == 1

    # Double-quoted `$(...)` command substitution is recursively extracted
    cmdsub_stmts = _shell_statements('echo "$(pip install evil)"\necho "$(pytest tests/ -n auto)"\n')
    with pytest.raises(AssertionError):
        for s in cmdsub_stmts:
            _assert_pip_install_pinned(s.tokens, "cmdsub-pip")
    with pytest.raises(AssertionError):
        for s in cmdsub_stmts:
            _assert_pytest_worker_cap(s.tokens, "cmdsub-pytest")

    # 3. `is_errexit_guarded` rejects `&&`, `&&\n`, `||`, `|`, `|&`, `&`, subshells/braces before `||`, and `if` conditions
    for suppressed in (
        'test "${#sdists[@]}" -eq 1 && test -f "${sdists[0]}"\n',
        'test "${#sdists[@]}" -eq 1 &&\n  test -f "${sdists[0]}"\n',
        'test "${#sdists[@]}" -eq 1 &\n',
        '(\ntest "${#sdists[@]}" -eq 1\ntest -f "${sdists[0]}"\n) || true\n',
        '{\ntest "${#sdists[@]}" -eq 1\ntest -f "${sdists[0]}"\n} || true\n',
        'if\ntest "${#sdists[@]}" -eq 1\nthen\n:\nfi\n',
    ):
        ss = _shell_statements(suppressed)
        test_ss = [s for s in ss if s.tokens and s.tokens[0] == "test"]
        assert test_ss and all(not s.is_errexit_guarded for s in test_ss), f"expected unguarded: {suppressed!r}"
    separate = _shell_statements('test "${#sdists[@]}" -eq 1\ntest -f "${sdists[0]}"\n')
    assert separate[0].is_errexit_guarded and separate[1].is_errexit_guarded

    # 4. Pip pinning: attached flags, remote requirements, `pip.__main__`, `-umpip`, `pip-3.12`, missing/unpinned `-r`
    pinned_req = tmp_path / "pinned.txt"
    pinned_req.write_text("build==1.6.1\n# comment\n", encoding="utf-8")
    unpinned_req = tmp_path / "unpinned.txt"
    unpinned_req.write_text("requests>=2.0\n", encoding="utf-8")
    _assert_pip_install_pinned(["pip", "install", "-r", "pinned.txt"], "pinned-req", base_dir=tmp_path)

    for bad in (
        ["pip3", "install", "unpinned"],
        ["pip-3.12", "install", "unpinned"],
        ["/usr/bin/pip", "install", "requests"],
        ["/tmp/venv/bin/pip3.12", "install", "evil"],
        ["python", "-mpip", "install", "build"],
        ["python", "-umpip", "install", "build"],
        ["python", "-m", "pip.__main__", "install", "build"],
        ["python", "-m", "pip", "install", "build==1.6.1", "https://evil.example/pkg.tar.gz"],
        ["python", "-m", "pip", "install", "build==1.6.1", "evil-sdists-pkg"],
        ["python", "-m", "pip", "install", "-e", "git+https://evil.example/repo.git"],
        ["python", "-m", "pip", "install", "-egit+https://evil.example/repo.git"],
        ["python", "-m", "pip", "install", "--requirement=https://evil.example/r.txt"],
        ["python", "-m", "pip", "install", "-rhttps://evil.example/r.txt"],
        ["python", "-m", "pip", "install", "--constraint=https://evil.example/c.txt"],
        ["python", "-m", "pip", "install", "-chttps://evil.example/c.txt"],
        ["python", "-m", "pip", "install", "-r", "unpinned.txt"],
        ["python", "-m", "pip", "install", "-r", "missing.txt"],
    ):
        with pytest.raises(AssertionError):
            _assert_pip_install_pinned(bad, "bad-pip", allow_local_sdist=True, base_dir=tmp_path)

    # 5. Pytest worker cap: bundled `-qn`, `-o addopts`, `--override-ini`, `pytest.__main__`, `-umpytest`, `--tx`
    _assert_pytest_worker_cap(["nice", "-n", "20", "pytest", "tests/", "-n", "8"], "nice-ok")
    for bad_py in (
        ["python", "-mpytest", "tests/", "-n", "auto"],
        ["python", "-umpytest", "tests/", "-n", "auto"],
        ["python", "-m", "pytest.__main__", "tests/", "-n", "auto"],
        ["py.test", "tests/", "-n", "24"],
        ["pytest-3", "tests/", "-n", "²"],
        ["pytest", "-qn", "auto", "tests/"],
        ["pytest", "-vn", "24", "tests/"],
        ["pytest", "-o", "addopts=-n auto", "tests/"],
        ["pytest", "--override-ini=addopts=-n auto", "tests/"],
        ["pytest", "-d", "--tx", "24 *popen"],
        ["pytest", "-d", "--tx", "2_4*popen"],
    ):
        with pytest.raises(AssertionError):
            _assert_pytest_worker_cap(bad_py, "bad-pytest")

    # 6. Strict `cancel-in-progress` allowlist rejects inverted polarity, disjunctions, multi-`${{ }}`
    for bad_cip in (
        True,
        None,
        "true",
        "True",
        "${{ true }}",
        "${{ true || github.event_name == 'pull_request' }}",
        "${{ github.event_name != 'pull_request' }}",
        "${{ github.event_name == 'pull_request' || github.event_name == 'push' }}",
        "${{ github.event_name == 'pull_request' || 1 == 1 }}",
        "${{ github.event_name == 'pull_request' || !false }}",
        "${{ github.event_name == 'pull_request' || always() }}",
        "${{ 'github.event_name pull_request' }}",
        "${{ github.event_name == 'pull_request' }} ${{ 1 }}",
    ):
        with pytest.raises(AssertionError):
            _assert_safe_cancel_in_progress(bad_cip)
    _assert_safe_cancel_in_progress(False)
    _assert_safe_cancel_in_progress("${{ github.event_name == 'pull_request' }}")
    _assert_safe_cancel_in_progress('${{ github.event_name == "pull_request" }}')

    # 7. YAML AST walker: valid flow/block, rejects EOF block-scalar (no IndexError!), flow comment spoof, non-scalar
    valid_wf = tmp_path / "valid.yml"
    valid_wf.write_text(
        "name: Demo\non: push\npermissions: {}\njobs:\n"
        f'  reusable:\n    uses: "org/repo/.github/workflows/x.yml@{"a" * 40}" # v1.2.3\n'
        "  build:\n    runs-on: ubuntu-latest\n    steps:\n"
        f'      - uses: "actions/checkout@{"b" * 40}" # v7.0.1\n'
        "        with:\n          persist-credentials: false\n"
        f'      - {{name: FlowOK, uses: actions/checkout@{"c" * 40}, with: {{persist-credentials: false}}}} # v7.0.1\n'
        "      - name: Heredoc\n        run: |\n          cat <<EOF\n          uses: actions/checkout@v4\n          EOF\n",
        encoding="utf-8",
    )
    _assert_workflow_all_guards(valid_wf)

    for bad_name, bad_yaml in (
        (
            "flow_missing.yml",
            "name: F\non: push\npermissions: {}\njobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n"
            f'      - {{name: Checkout, uses: actions/checkout@{"a" * 40}, with: {{persist-credentials: false}}}}\n',
        ),
        (
            "flow_spoof.yml",
            "name: F\non: push\npermissions: {}\njobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n"
            f'      - {{name: "Checkout # v7", uses: actions/checkout@{"a" * 40}, with: {{ref: "# v7", persist-credentials: false}}}}\n',
        ),
        (
            "block_eof.yml",
            "name: F\non: push\npermissions: {}\njobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n"
            f'      - uses: >-\n          actions/checkout@{"a" * 40}\n',
        ),
        (
            "block_nextline_spoof.yml",
            "name: F\non: push\npermissions: {}\njobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n"
            f'      - uses: >-\n          actions/checkout@{"a" * 40}\n        with: # v7\n          persist-credentials: false\n',
        ),
        (
            "nonscalar_job.yml",
            'name: F\non: push\npermissions: {}\njobs:\n  reusable:\n    uses: ["bad/unpinned@v1"]\n',
        ),
    ):
        p = tmp_path / bad_name
        p.write_text(bad_yaml, encoding="utf-8")
        with pytest.raises(AssertionError):
            _assert_workflow_all_guards(p)

    # 8. `_check_ci_workflow_doc` negative mutants & positive `build[virtualenv]==1.6.1` control
    base_doc = _load_yaml_strict((WORKFLOW_DIR / "ci.yml").read_text(encoding="utf-8"))

    # Positive control: PEP 508 extras on `build`
    doc_extras = copy.deepcopy(base_doc)
    for st in doc_extras["jobs"]["package"]["steps"]:
        if st.get("name") == "Install build":
            st["run"] = 'python -m pip install "build[virtualenv]==1.6.1"'
    _check_ci_workflow_doc(doc_extras)

    # Negative mutants against `_check_ci_workflow_doc`
    def _mut(mutator: Any) -> None:
        d = copy.deepcopy(base_doc)
        mutator(d)
        with pytest.raises(AssertionError):
            _check_ci_workflow_doc(d)

    # grep -q regression
    _mut(lambda d: d["jobs"]["package"]["steps"][5].__setitem__("run", "tar tzf dist/*.tar.gz | grep -q 'migrations'"))
    # node --test moved into primary pytest step
    _mut(
        lambda d: d["jobs"]["test"]["steps"][6].__setitem__(
            "run",
            d["jobs"]["test"]["steps"][6]["run"] + '\nshopt -s nullglob\nsuites=(web/app/tests/*.test.mjs)\ntest "${#suites[@]}" -gt 0\nnode --test "${suites[@]}"\n',
        )
    )
    # PYTEST_ADDOPTS+= bypass
    _mut(
        lambda d: d["jobs"]["test"]["steps"][6].__setitem__(
            "run", 'export PYTEST_ADDOPTS+="-n auto"\n' + d["jobs"]["test"]["steps"][6]["run"]
        )
    )
    # Dropping `env -u PYTEST_ADDOPTS`
    _mut(lambda d: d["jobs"]["test"]["steps"][6].__setitem__("run", "python -m pytest tests/ -n auto --maxprocesses=16"))
    # Numeric underscore factor `1_5e-1` (NaN in JS Number())
    _mut(lambda d: d["jobs"]["test"]["steps"][6]["env"].__setitem__("PORCHLAMP_PERF_HOST_FACTOR", "1_5e-1"))
    # `node --test ... || true` suppresses errexit
    _mut(
        lambda d: d["jobs"]["test"]["steps"][5].__setitem__(
            "run", d["jobs"]["test"]["steps"][5]["run"].replace('node --test "${suites[@]}"', 'node --test "${suites[@]}" || true')
        )
    )
    # `shopt -u nullglob` undoes guard
    _mut(
        lambda d: d["jobs"]["test"]["steps"][5].__setitem__(
            "run", d["jobs"]["test"]["steps"][5]["run"].replace("shopt -s nullglob", "shopt -s nullglob\nshopt -u nullglob")
        )
    )
    # Post-guard `suites` clobber
    _mut(
        lambda d: d["jobs"]["test"]["steps"][5].__setitem__(
            "run",
            d["jobs"]["test"]["steps"][5]["run"].replace(
                'node --test "${suites[@]}"',
                'suites=(web/app/tests/unit.test.mjs)\nnode --test "${suites[@]}"',
            ),
        )
    )
    # `set +e` inside sdist install
    _mut(
        lambda d: d["jobs"]["package"]["steps"][6].__setitem__(
            "run", "set +e\n" + d["jobs"]["package"]["steps"][6]["run"]
        )
    )
    # Bare `${RUNNER_TEMP}` inside verify step
    _mut(
        lambda d: d["jobs"]["package"]["steps"][7].__setitem__(
            "run",
            'echo "${RUNNER_TEMP:?ok}"\n"${RUNNER_TEMP}/porchlamp-install-check/bin/python" -c "pass"\n',
        )
    )
    # Single-quoted `'${RUNNER_TEMP:?...}'`
    _mut(
        lambda d: d["jobs"]["package"]["steps"][7].__setitem__(
            "run", "'${RUNNER_TEMP:?ok}/porchlamp-install-check/bin/python' -c 'pass'\n"
        )
    )
    # Missing `working-directory: ${{ runner.temp }}`
    _mut(lambda d: d["jobs"]["package"]["steps"][7].pop("working-directory"))
    # `os.chdir('/tmp')` inside python -c
    _mut(
        lambda d: d["jobs"]["package"]["steps"][7].__setitem__(
            "run",
            d["jobs"]["package"]["steps"][7]["run"].replace("import os", "import os; os.chdir('/tmp')"),
        )
    )
    # Custom shell dropping `-e`
    _mut(lambda d: d["jobs"]["test"]["steps"][5].__setitem__("shell", "bash {0}"))
    # Workflow defaults.run.shell missing
    _mut(lambda d: d.pop("defaults"))

    # 9. `publish_from_drop` main-push regex catches `HEAD:refs/heads/main`
    with pytest.raises(AssertionError):
        _assert_publish_never_pushes_main('git -C porchlamp-site push --force origin "HEAD:refs/heads/main"')
