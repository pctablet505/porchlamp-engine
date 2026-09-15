"""Acceptance tests: the import graph.

``core/`` never imports ``collect/``, ``derive/`` or ``store/``; in concrete
terms ``cvi.core`` imports nothing outside ``cvi.core`` plus
the standard library, and ``cvi.derive.bundle`` imports nothing outside
``cvi.core`` plus the standard library.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_DIR = REPO_ROOT / "cvi" / "core"
DERIVE_BUNDLE = REPO_ROOT / "cvi" / "derive" / "bundle.py"
STORE_DIR = REPO_ROOT / "cvi" / "store"
SERVE_DIR = REPO_ROOT / "cvi" / "serve"
RATELIMIT_MODULE = REPO_ROOT / "cvi" / "collect" / "ratelimit.py"
CVI_DIR = REPO_ROOT / "cvi"
SCRIPTS_DIR = REPO_ROOT / "scripts"

#: `cvi/serve` is the outermost layer -- it legitimately reads any other
#: `cvi.*` package (core, store, audit) -- plus the web framework it is
#: built on. What it may NEVER import is `scripts.*`.
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
    """``cvi/store`` may import ``cvi.core`` (enums, the tie key) and
    ``cvi.snapshot`` (the frozen-population table builder, the canonical dump hash)
    in addition to the standard library -- nothing else.

    A module inside ``cvi/store`` may also import another module inside
    ``cvi/store`` (e.g. ``cvi.store.queries``)."""
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
    """`cvi/serve` may import any `cvi.*` package plus the standard library
    and the web-framework packages it is built on (`fastapi`, `starlette`,
    `pydantic`) -- never `scripts.*`."""
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


def test_collect_ratelimit_imports_only_stdlib_or_httpx() -> None:
    """``cvi/collect/ratelimit.py`` is a generic HTTP-primitives module --
    token bucket, ETag cache, circuit breaker -- with no CVI-domain knowledge
    at all. It imports nothing but the standard library and ``httpx``.
    """
    assert RATELIMIT_MODULE.is_file(), "cvi/collect/ratelimit.py missing"
    for name in imported_modules(RATELIMIT_MODULE):
        top = name.split(".")[0]
        allowed = top in sys.stdlib_module_names or top == "httpx"
        assert allowed, (
            f"cvi/collect/ratelimit.py imports {name!r} outside stdlib + httpx"
        )


def test_no_production_import_of_the_synthetic_catalog_fixture() -> None:
    """``tests/fixtures/synthetic_catalog.py`` is never importable from ``cvi/``
    or ``scripts/``: a production package importing a test package is prohibited.
    ``cvi/`` has no legitimate reason to import anything under ``tests.*``.

    ``scripts/demo_app.py`` is the one script allowed to import a
    ``tests.*`` helper (``tests.helpers.demo_app``).
    """
    for path in sorted(CVI_DIR.rglob("*.py")):
        for name in imported_modules(path):
            assert not name.startswith("tests."), (
                f"{path.relative_to(REPO_ROOT)} imports {name!r} -- "
                "cvi/ must never import anything under tests. (no exception: "
                "unlike scripts/demo_app.py, no cvi/ module needs a test helper)"
            )
    for path in sorted(SCRIPTS_DIR.rglob("*.py")):
        for name in imported_modules(path):
            assert not name.startswith("tests.fixtures.synthetic_catalog"), (
                f"{path.relative_to(REPO_ROOT)} imports {name!r} -- "
                "scripts/ must never import the test fixture "
                "tests/fixtures/synthetic_catalog.py (tests.helpers.demo_app "
                "remains legal for the demo_app shim)"
            )


def test_no_cvi_module_resolves_outside_the_repo_root() -> None:
    """Every ``cvi/**/*.py`` module that exists on disk today must import
    successfully and resolve inside this repo root.
    """
    on_disk: list[str] = []
    for path in sorted(REPO_ROOT.joinpath("cvi").rglob("*.py")):
        parts = path.relative_to(REPO_ROOT).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            on_disk.append(".".join(parts))
    assert on_disk, "no cvi/**/*.py modules found to check"

    #: Historical legacy names -- check that none resolve outside the root.
    historical_modules = ["cvi.ingest", "cvi.benchmark", "cvi.benchmark_data"]
    names = sorted(set(on_disk) | set(historical_modules))

    check_script = """
import importlib
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
names = sys.argv[2:]
outside = []
for name in names:
    try:
        mod = importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name == name:
            continue  # genuinely gone -- the correct state for a deletion
        raise  # a real dependency of an existing module is missing -- do not mask it
    file_ = getattr(mod, "__file__", None)
    if file_ is None or not pathlib.Path(file_).resolve().is_relative_to(root):
        outside.append((name, file_))
assert not outside, outside
print("clean:", len(names), "names checked")
"""
    result = subprocess.run(
        [sys.executable, "-c", check_script, str(REPO_ROOT), *names],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "clean:" in result.stdout


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
