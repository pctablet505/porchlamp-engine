"""W2-1 acceptance: the import graph (04 section 3 rules).

``core/`` never imports ``collect/``, ``derive/`` or ``store/``; in concrete
terms for this packet ``cvi.core`` imports nothing outside ``cvi.core`` plus
the standard library, and ``cvi.derive.bundle`` imports nothing outside
``cvi.core`` plus the standard library.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_DIR = REPO_ROOT / "cvi" / "core"
DERIVE_BUNDLE = REPO_ROOT / "cvi" / "derive" / "bundle.py"
STORE_DIR = REPO_ROOT / "cvi" / "store"


def imported_modules(path: Path) -> list[str]:
    """Every dotted module name referenced by an import in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module)
    return names


def assert_only_core_or_stdlib(path: Path) -> None:
    for name in imported_modules(path):
        top = name.split(".")[0]
        allowed = top in sys.stdlib_module_names or name.startswith("cvi.core")
        assert allowed, f"{path.name} imports {name!r} outside cvi.core + stdlib"


def test_core_imports_nothing_outside_core_and_stdlib() -> None:
    assert CORE_DIR.is_dir(), "cvi/core package missing"
    for path in sorted(CORE_DIR.glob("*.py")):
        assert_only_core_or_stdlib(path)


def test_derive_bundle_imports_only_core_and_stdlib() -> None:
    for name in imported_modules(DERIVE_BUNDLE):
        top = name.split(".")[0]
        allowed = top in sys.stdlib_module_names or name.startswith("cvi.core")
        assert allowed, f"cvi/derive/bundle.py imports {name!r}; "
        "it may only import cvi.core modules plus stdlib"


def test_store_imports_only_core_snapshot_or_stdlib() -> None:
    """F-009 (W4-2 REWORK round 1): `04` section 3 was amended for this
    packet (R-B) to let ``cvi/store`` import ``cvi.core`` (enums, the tie
    key) and ``cvi.snapshot`` (the frozen-population table builder, the
    canonical dump hash) in addition to the standard library -- nothing
    else. Before this test, that amended rule was prose only."""
    assert STORE_DIR.is_dir(), "cvi/store package missing"
    for path in sorted(STORE_DIR.rglob("*.py")):
        for name in imported_modules(path):
            top = name.split(".")[0]
            allowed = (
                top in sys.stdlib_module_names
                or name.startswith("cvi.core")
                or name.startswith("cvi.snapshot")
            )
            assert allowed, (
                f"{path.relative_to(REPO_ROOT)} imports {name!r} outside "
                f"cvi.core/cvi.snapshot + stdlib"
            )


def test_importing_core_loads_no_other_cvi_package() -> None:
    code = (
        "import sys; import cvi.core; "
        "bad = sorted(m for m in sys.modules if m.startswith('cvi.') and "
        "not m.startswith('cvi.core')); "
        "assert not bad, bad; print('clean')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout
