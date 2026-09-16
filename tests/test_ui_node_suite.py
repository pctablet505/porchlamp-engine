"""Pytest wrapper for the Node.js frontend test suite (`web/v2/tests/`).

Executes `node --test` on `unit.test.mjs` and `render.perf.test.mjs` when
Node.js is available in `PATH`, and skips cleanly when Node.js is absent on
the host machine.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
UNIT_TEST_MJS = REPO_ROOT / "web" / "v2" / "tests" / "unit.test.mjs"
PERF_TEST_MJS = REPO_ROOT / "web" / "v2" / "tests" / "render.perf.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js binary not installed in PATH")
def test_node_frontend_unit_and_perf_suite() -> None:
    """Run Node.js unit and render performance suites for `web/v2/`."""
    node_bin = shutil.which("node")
    assert node_bin is not None
    result = subprocess.run(
        [node_bin, "--test", str(UNIT_TEST_MJS), str(PERF_TEST_MJS)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"node --test exited with {result.returncode}:\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
