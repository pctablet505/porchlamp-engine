"""Pytest wrapper for the Node.js frontend test suite (`web/app/tests/`).

Executes `node --test` on `unit.test.mjs`, `render.perf.test.mjs` and `livecheck.test.mjs`. An
absent Node.js interpreter is a failure, not a silent skip (H4): the suite
only skips when `PORCHLAMP_ALLOW_MISSING_NODE=1` is set explicitly. `find_node`
looks on `PATH`, then `PORCHLAMP_NODE_BIN`, then the usual install locations, so an
installed-but-unexported Node.js still gates.
"""
from __future__ import annotations

import os
import pwd
import shutil
import subprocess
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
UNIT_TEST_MJS = REPO_ROOT / "web" / "app" / "tests" / "unit.test.mjs"
PERF_TEST_MJS = REPO_ROOT / "web" / "app" / "tests" / "render.perf.test.mjs"
LIVECHECK_TEST_MJS = REPO_ROOT / "web" / "app" / "tests" / "livecheck.test.mjs"
LOOKUP_TEST_MJS = REPO_ROOT / "web" / "app" / "tests" / "lookup.test.mjs"
LOOKUP_PAGE_TEST_MJS = REPO_ROOT / "web" / "app" / "tests" / "lookup_page.test.mjs"
FIRST_PRS_TEST_MJS = REPO_ROOT / "web" / "app" / "tests" / "first_pull_requests.test.mjs"

#: Where to look for a Node.js interpreter, in order, when it is not on
#: ``PATH``. The point of this suite is that the JavaScript tests actually
#: RUN; a Node.js that is installed but unexported is the commonest reason
#: they silently did not (on the development host the binary sat in
#: ``~/.local/opt/node-v22.14.0-linux-x64/bin`` while ``PATH`` had none, so
#: the whole frontend layer skipped for months and two real failures landed
#: on main). These globs are generic install layouts, not one machine's
#: paths; ``PORCHLAMP_NODE_BIN`` is the explicit override.
_NODE_GLOBS: tuple[str, ...] = (
    ".local/opt/node-*/bin/node",
    "tools/node-*/bin/node",
    "oss-contributions/node-*/**/bin/node",
    ".nvm/versions/node/*/bin/node",
    ".volta/tools/image/node/*/bin/node",
    ".fnm/node-versions/*/installation/bin/node",
    ".asdf/installs/nodejs/*/bin/node",
)


def _homes() -> list[Path]:
    """``$HOME``, then the account's own home directory.

    A job under the shared test scheduler runs with a throwaway ``$HOME``, which hides a Node.js installed under
    the real one.
    """
    homes = [Path.home()]
    try:
        real = Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:
        return homes
    if real not in homes:
        homes.append(real)
    return homes


def find_node() -> str | None:
    """A Node.js interpreter: ``PATH``, then ``PORCHLAMP_NODE_BIN``, then a small
    set of generic install locations under ``$HOME``, ``/opt/homebrew``, and ``/usr/local``.

    Returns ``None`` only when no interpreter exists anywhere we look, which
    is the one case that should stop the suite.
    """
    on_path = shutil.which("node")
    if on_path:
        return on_path
    override = os.environ.get("PORCHLAMP_NODE_BIN")
    if override and Path(override).is_file() and os.access(override, os.X_OK):
        return override
    candidates = [path for home in _homes() for pattern in _NODE_GLOBS for path in sorted(home.glob(pattern), reverse=True)]
    candidates.extend([Path("/opt/homebrew/bin/node"), Path("/usr/local/bin/node")])
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def test_find_node_looks_in_the_accounts_real_home_when_home_is_a_throwaway(tmp_path, monkeypatch) -> None:
    """The shared test scheduler runs a job with a scratch ``$HOME``: a Node.js under the real home must still be found.

    MEASURED 2026-10-12: five node-backed tests sat in the known-failing set under the scheduler for this reason.
    """
    real_home = tmp_path / "real"
    stub = real_home / ".local" / "opt" / "node-v0.0.0-test" / "bin" / "node"
    stub.parent.mkdir(parents=True)
    stub.write_text("#!/bin/sh\n")
    stub.chmod(0o755)
    scratch = tmp_path / "scratch-home"
    scratch.mkdir()
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.delenv("PORCHLAMP_NODE_BIN", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: scratch))
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: types.SimpleNamespace(pw_dir=str(real_home)))
    assert find_node() == str(stub)


def test_node_frontend_unit_and_perf_suite() -> None:
    """Run Node.js unit and render performance suites for `web/app/`."""
    node_bin = find_node()
    if node_bin is None:
        if os.environ.get("PORCHLAMP_ALLOW_MISSING_NODE") == "1":
            pytest.skip("no Node.js interpreter found (PORCHLAMP_ALLOW_MISSING_NODE=1)")
        pytest.fail(
            "no Node.js interpreter found on PATH, in PORCHLAMP_NODE_BIN, or in the usual install "
            "locations; the frontend suite is a real gate. Install Node.js, point PORCHLAMP_NODE_BIN "
            "at an interpreter, or set PORCHLAMP_ALLOW_MISSING_NODE=1 to skip it explicitly."
        )
    result = subprocess.run(
        [node_bin, "--test", str(UNIT_TEST_MJS), str(PERF_TEST_MJS), str(LIVECHECK_TEST_MJS), str(LOOKUP_TEST_MJS), str(LOOKUP_PAGE_TEST_MJS), str(FIRST_PRS_TEST_MJS)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"node --test exited with {result.returncode}:\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


def test_find_node_prefers_path_then_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``find_node`` looks on PATH first, then at ``PORCHLAMP_NODE_BIN``."""
    monkeypatch.setattr(shutil, "which", lambda name: "/from/path/node")
    assert find_node() == "/from/path/node"
    monkeypatch.setattr(shutil, "which", lambda name: None)
    fake = tmp_path / "node"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PORCHLAMP_NODE_BIN", str(fake))
    assert find_node() == str(fake)


def test_missing_node_is_a_failure_unless_opted_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """H4: no Node.js interpreter anywhere must fail, not skip, unless
    ``PORCHLAMP_ALLOW_MISSING_NODE=1`` opts out explicitly."""
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr("tests.test_ui_node_suite.find_node", lambda: None)
    monkeypatch.delenv("PORCHLAMP_ALLOW_MISSING_NODE", raising=False)
    with pytest.raises(pytest.fail.Exception):
        test_node_frontend_unit_and_perf_suite()

    monkeypatch.setenv("PORCHLAMP_ALLOW_MISSING_NODE", "1")
    with pytest.raises(pytest.skip.Exception):
        test_node_frontend_unit_and_perf_suite()
