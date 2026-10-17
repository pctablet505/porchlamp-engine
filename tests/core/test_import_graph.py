"""Acceptance tests: the import graph.

``core/`` never imports ``collect/``, ``derive/`` or ``store/``; in concrete
terms ``porchlamp.core`` imports nothing outside ``porchlamp.core`` plus
the standard library, and ``porchlamp.derive.bundle`` imports nothing outside
``porchlamp.core`` plus the standard library.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_DIR = REPO_ROOT / "porchlamp" / "core"
DERIVE_DIR = REPO_ROOT / "porchlamp" / "derive"
DERIVE_BUNDLE = DERIVE_DIR / "bundle.py"
STORE_DIR = REPO_ROOT / "porchlamp" / "store"
SERVE_DIR = REPO_ROOT / "porchlamp" / "serve"
RATELIMIT_MODULE = REPO_ROOT / "porchlamp" / "collect" / "ratelimit.py"
PORCHLAMP_DIR = REPO_ROOT / "porchlamp"
SCRIPTS_DIR = REPO_ROOT / "scripts"

#: `porchlamp/serve` is the outermost layer -- it legitimately reads any other
#: `porchlamp.*` package (core, store, audit) -- plus the web framework it is
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
        allowed = top in sys.stdlib_module_names or name.startswith("porchlamp.core")
        assert allowed, f"{path.name} imports {name!r} outside porchlamp.core + stdlib"


def test_core_imports_nothing_outside_core_and_stdlib() -> None:
    assert CORE_DIR.is_dir(), "porchlamp/core package missing"
    for path in sorted(CORE_DIR.glob("*.py")):
        assert_only_core_or_stdlib(path)


def test_derive_bundle_imports_only_core_and_stdlib() -> None:
    assert DERIVE_DIR.is_dir(), "porchlamp/derive package missing"
    for path in sorted(DERIVE_DIR.rglob("*.py")):
        for name in imported_modules(path):
            top = name.split(".")[0]
            allowed = top in sys.stdlib_module_names or name.startswith("porchlamp.core")
            assert allowed, (
                f"{path.relative_to(REPO_ROOT)} imports {name!r}; "
                "it may only import porchlamp.core modules plus stdlib"
            )


def test_store_imports_only_core_snapshot_or_stdlib() -> None:
    """``porchlamp/store`` may import ``porchlamp.core`` (enums, the tie key) and
    ``porchlamp.snapshot`` (the frozen-population table builder, the canonical dump hash)
    in addition to the standard library -- nothing else.

    A module inside ``porchlamp/store`` may also import another module inside
    ``porchlamp/store`` (e.g. ``porchlamp.store.queries``)."""
    assert STORE_DIR.is_dir(), "porchlamp/store package missing"
    for path in sorted(STORE_DIR.rglob("*.py")):
        for name in imported_modules(path):
            top = name.split(".")[0]
            allowed = (
                top in sys.stdlib_module_names
                or name.startswith("porchlamp.core")
                or name.startswith("porchlamp.snapshot")
                or name.startswith("porchlamp.store")
            )
            assert allowed, (
                f"{path.relative_to(REPO_ROOT)} imports {name!r} outside "
                f"porchlamp.core/porchlamp.snapshot/porchlamp.store + stdlib"
            )


def test_serve_imports_only_porchlamp_fastapi_pydantic_or_stdlib() -> None:
    """`porchlamp/serve` may import any `porchlamp.*` package plus the standard library
    and the web-framework packages it is built on (`fastapi`, `starlette`,
    `pydantic`) -- never `scripts.*`."""
    assert SERVE_DIR.is_dir(), "porchlamp/serve package missing"
    for path in sorted(SERVE_DIR.rglob("*.py")):
        for name in imported_modules(path):
            top = name.split(".")[0]
            assert top != "scripts", (
                f"{path.relative_to(REPO_ROOT)} imports {name!r} -- "
                "porchlamp/serve must never import scripts.* (the reverse of the "
                "allowed direction)"
            )
            allowed = (
                top in sys.stdlib_module_names
                or name.startswith("porchlamp.")
                or top in _SERVE_ALLOWED_THIRD_PARTY
            )
            assert allowed, (
                f"{path.relative_to(REPO_ROOT)} imports {name!r} outside "
                f"porchlamp.* + stdlib + {_SERVE_ALLOWED_THIRD_PARTY}"
            )


def test_collect_ratelimit_imports_only_stdlib_or_httpx() -> None:
    """``porchlamp/collect/ratelimit.py`` is a generic HTTP-primitives module --
    token bucket, ETag cache, circuit breaker -- with no Porchlamp-domain knowledge
    at all. It imports nothing but the standard library and ``httpx``.
    """
    assert RATELIMIT_MODULE.is_file(), "porchlamp/collect/ratelimit.py missing"
    for name in imported_modules(RATELIMIT_MODULE):
        top = name.split(".")[0]
        allowed = top in sys.stdlib_module_names or top == "httpx"
        assert allowed, (
            f"porchlamp/collect/ratelimit.py imports {name!r} outside stdlib + httpx"
        )


def test_no_production_import_of_the_synthetic_catalog_fixture() -> None:
    """``tests/fixtures/synthetic_catalog.py`` is never importable from ``porchlamp/``
    or ``scripts/``: a production package importing a test package is prohibited.
    ``porchlamp/`` has no legitimate reason to import anything under ``tests.*``.
    """
    for path in sorted(PORCHLAMP_DIR.rglob("*.py")):
        for name in imported_modules(path):
            assert not name.startswith("tests."), (
                f"{path.relative_to(REPO_ROOT)} imports {name!r} -- "
                "porchlamp/ must never import anything under tests. (no exception: "
                "no porchlamp/ module needs a test helper)"
            )
    for path in sorted(SCRIPTS_DIR.rglob("*.py")):
        for name in imported_modules(path):
            assert not name.startswith("tests.fixtures.synthetic_catalog"), (
                f"{path.relative_to(REPO_ROOT)} imports {name!r} -- "
                "scripts/ must never import the test fixture "
                "tests/fixtures/synthetic_catalog.py"
            )


def test_no_porchlamp_module_resolves_outside_the_repo_root() -> None:
    """Every ``porchlamp/**/*.py`` module that exists on disk today must import
    successfully and resolve inside this repo root.
    """
    on_disk: list[str] = []
    for path in sorted(REPO_ROOT.joinpath("porchlamp").rglob("*.py")):
        parts = path.relative_to(REPO_ROOT).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            on_disk.append(".".join(parts))
    assert on_disk, "no porchlamp/**/*.py modules found to check"

    #: Historical legacy names -- check that none resolve outside the root.
    historical_modules = ["porchlamp.ingest", "porchlamp.benchmark", "porchlamp.benchmark_data"]
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


def test_importing_core_loads_no_other_porchlamp_package() -> None:
    code = (
        "import sys; import porchlamp.core; "
        "bad = sorted(m for m in sys.modules if m.startswith('porchlamp.') and "
        "not m.startswith('porchlamp.core')); "
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
