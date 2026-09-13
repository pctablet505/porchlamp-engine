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
SERVE_DIR = REPO_ROOT / "cvi" / "serve"

#: `cvi/serve` is the outermost layer -- it legitimately reads any other
#: `cvi.*` package (core, store, audit) -- plus the web framework it is
#: built on. What it may NEVER import is `scripts.*` (W4-4 REWORK,
#: Warning W-5): `cvi/serve/app.py` used to do
#: `from scripts.serve import open_or_exit`, a package reaching backwards
#: into a script through a `scripts` namespace package with no
#: `__init__.py` -- invisible to this file before this rule existed,
#: since nothing here policed `cvi/serve` at all.
_SERVE_ALLOWED_THIRD_PARTY = ("fastapi", "starlette", "pydantic")


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
    else. Before this test, that amended rule was prose only.

    W4-3 widens this check one step further: a module INSIDE ``cvi/store``
    may import another module inside ``cvi/store`` (``cvi.store.queries``,
    R-2's "one definition" -- ``cvi/store/reader.py`` reuses
    ``cvi.store.queries.ORDER_BY_SQL``/``row_to_dict`` rather than a second,
    drifting copy of the same SQL/decoding logic). This is not a widening of
    what ``store/`` may reach OUTSIDE itself -- the rule 04 section 3 states
    ("store/ imports core ... and snapshot ... and nothing else") is about
    crossing the package boundary, and an intra-``cvi.store`` import never
    does that; every import a ``cvi/store/*.py`` file names is still exactly
    one of stdlib, ``cvi.core.*``, ``cvi.snapshot.*`` or ``cvi.store.*``."""
    assert STORE_DIR.is_dir(), "cvi/store package missing"
    for path in sorted(STORE_DIR.rglob("*.py")):
        for name in imported_modules(path):
            top = name.split(".")[0]
            allowed = (
                top in sys.stdlib_module_names
                or name.startswith("cvi.core")
                or name.startswith("cvi.snapshot")
                or name.startswith("cvi.store")
            )
            assert allowed, (
                f"{path.relative_to(REPO_ROOT)} imports {name!r} outside "
                f"cvi.core/cvi.snapshot/cvi.store + stdlib"
            )


def test_serve_imports_only_cvi_fastapi_pydantic_or_stdlib() -> None:
    """W4-4 REWORK (Blocker B-4 / Warning W-5): `cvi/serve` may import any
    `cvi.*` package plus the standard library and the web-framework
    packages it is built on (`fastapi`, `starlette`, `pydantic`) -- never
    `scripts.*`. The guard's pure logic (v1 and v2) now lives in
    `cvi/serve/guard.py`; `scripts/serve.py` imports FROM `cvi.serve`, the
    correct direction, and re-exports what `tests/test_serve.py` needs."""
    assert SERVE_DIR.is_dir(), "cvi/serve package missing"
    for path in sorted(SERVE_DIR.rglob("*.py")):
        for name in imported_modules(path):
            top = name.split(".")[0]
            assert top != "scripts", (
                f"{path.relative_to(REPO_ROOT)} imports {name!r} -- "
                "cvi/serve must never import scripts.* (the reverse of the "
                "allowed direction)"
            )
            allowed = (
                top in sys.stdlib_module_names
                or name.startswith("cvi.")
                or top in _SERVE_ALLOWED_THIRD_PARTY
            )
            assert allowed, (
                f"{path.relative_to(REPO_ROOT)} imports {name!r} outside "
                f"cvi.* + stdlib + {_SERVE_ALLOWED_THIRD_PARTY}"
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
