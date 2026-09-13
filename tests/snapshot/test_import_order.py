"""W4-6f acceptance: the circular import found by the W4-4 reviewer on main
(`3ed230f`'s parent -- reproduced directly: ``python -c "import cvi.store.
writer"`` as the FIRST ``cvi`` import of a fresh process raised
``ImportError: cannot import name 'RepoWrite' from partially initialized
module 'cvi.store.writer' (most likely due to a circular import)``).

Root cause (``cvi/snapshot/__init__.py``'s own module docstring, W4-6f):
``cvi.store.writer`` imports ``cvi.snapshot.populations``, a submodule
import that forces Python to run ``cvi/snapshot/__init__.py`` first if
``cvi.snapshot`` is not already in ``sys.modules``; that ``__init__.py``
used to eagerly import ``cvi.snapshot.store_cmd``, which imports names
FROM ``cvi.store.writer`` -- the very module still mid-execution one frame
up the stack, so those names do not exist on it yet.

The regular test suite never hits this: something always imports
``cvi.snapshot`` (harmlessly, once the cycle can't form) before any test
imports ``cvi.store.writer`` for the first time, and pytest's own
collection/plugin machinery plus every other test module's imports settle
the module cache long before this file's own tests run. A regular
in-process ``import`` assertion is therefore not a real test of this bug
at all (it would pass regardless of the fix, since ``cvi.store.writer`` is
never truly first once the process is already this far into collection)
-- each check below launches a FRESH ``python -c "..."`` subprocess, the
only way to actually control which module is the first ``cvi`` import of
a process.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_fresh_import(statement: str) -> subprocess.CompletedProcess:
    """Run ``statement`` as the entire program of a brand-new Python
    process, with ``PYTHONPATH`` pointed at this checkout (the editable
    install's own convention -- module docstrings across this repo repeat
    it) so it exercises the code in THIS worktree, not whatever is
    importable ambiently."""
    return subprocess.run(
        [sys.executable, "-c", statement],
        cwd=REPO_ROOT,
        env={"PYTHONPATH": str(REPO_ROOT), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_cvi_store_writer_is_importable_as_the_first_cvi_import() -> None:
    """The exact reproduction command from the W4-4 reviewer's report."""
    result = _run_fresh_import("import cvi.store.writer")
    assert result.returncode == 0, (
        f"`python -c \"import cvi.store.writer\"` failed as the first cvi "
        f"import of a fresh process:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "ImportError" not in result.stderr
    assert "circular import" not in result.stderr


def test_cvi_snapshot_store_cmd_is_importable_as_the_first_cvi_import() -> None:
    """The other side of the same cycle: importing ``cvi.snapshot.
    store_cmd`` directly (what ``cvi/snapshot/cli.py`` does) must also
    never trip over its own package's ``__init__.py``."""
    result = _run_fresh_import("import cvi.snapshot.store_cmd")
    assert result.returncode == 0, (
        f"`python -c \"import cvi.snapshot.store_cmd\"` failed as the first "
        f"cvi import of a fresh process:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "ImportError" not in result.stderr
    assert "circular import" not in result.stderr


def test_lazy_names_still_resolve_off_the_package_root() -> None:
    """The fix (``cvi/snapshot/__init__.py``'s ``__getattr__``, PEP 562)
    must not just avoid the crash -- ``cvi.snapshot.<name>`` for each of
    the five names it used to import eagerly must still resolve to the
    real object from ``cvi.snapshot.store_cmd``, from a fresh process,
    with no prior import of ``cvi.snapshot.store_cmd`` warming the cache."""
    statement = (
        "import cvi.snapshot as s; "
        "import cvi.snapshot.store_cmd as sc; "
        "names = ['VerificationError', 'score_snapshot_dir', "
        "'score_snapshot_with_detectors', 'verify_expected', 'write_scored_snapshot']; "
        "assert all(getattr(s, n) is getattr(sc, n) for n in names), "
        "[getattr(s, n) for n in names]; "
        "print('OK')"
    )
    result = _run_fresh_import(statement)
    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "OK" in result.stdout


def test_every_lazy_name_is_declared_in_all() -> None:
    """:data:`cvi.snapshot._LAZY_STORE_CMD_NAMES` (the set ``__getattr__``
    actually resolves) must be exactly a subset of ``__all__`` -- a name
    ``__getattr__`` knows how to resolve but that is missing from
    ``__all__`` would be undiscoverable by ``tests/snapshot/
    test_cli.py``'s own export-completeness check, silently."""
    import cvi.snapshot as snapshot_pkg

    assert snapshot_pkg._LAZY_STORE_CMD_NAMES <= set(snapshot_pkg.__all__)


def test_unknown_attribute_still_raises_attribute_error() -> None:
    """``__getattr__``'s fallback path: a name that is neither a real
    module attribute nor one of the five lazy names raises
    ``AttributeError``, exactly what attribute access on a module without
    a custom ``__getattr__`` would raise -- never silently returning
    ``None`` or swallowing the typo."""
    import cvi.snapshot as snapshot_pkg

    with pytest.raises(AttributeError):
        _ = snapshot_pkg.this_name_does_not_exist_anywhere
