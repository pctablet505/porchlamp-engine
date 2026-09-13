"""W2-1 acceptance: the import graph (04 section 3 rules).

``core/`` never imports ``collect/``, ``derive/`` or ``store/``; in concrete
terms for this packet ``cvi.core`` imports nothing outside ``cvi.core`` plus
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


def test_collect_ratelimit_imports_only_stdlib_or_httpx() -> None:
    """W4-1: ``cvi/collect/ratelimit.py`` (moved from ``cvi/ingest.py``) is a
    generic HTTP-primitives module -- token bucket, ETag cache, circuit
    breaker -- with no CVI-domain knowledge at all, unlike its
    ``cvi.collect`` siblings (which read/write :class:`~cvi.collect.
    envelope.Envelope` and so import ``cvi.core``/``cvi.models``). It
    imports nothing but the standard library and ``httpx``, and this is
    worth enforcing precisely because a future edit could otherwise wire
    ``cvi.models``/``cvi.lakehouse`` into what is supposed to stay a
    reusable, domain-free primitive.
    """
    assert RATELIMIT_MODULE.is_file(), "cvi/collect/ratelimit.py missing"
    for name in imported_modules(RATELIMIT_MODULE):
        top = name.split(".")[0]
        allowed = top in sys.stdlib_module_names or top == "httpx"
        assert allowed, (
            f"cvi/collect/ratelimit.py imports {name!r} outside stdlib + httpx"
        )


def test_no_production_import_of_the_synthetic_catalog_fixture() -> None:
    """W4-1 (`04-ARCHITECTURE.md` section 2): ``tests/fixtures/
    synthetic_catalog.py`` (formerly ``cvi/benchmark_data.py``) is "never
    importable from cvi/" -- and, per `.agents/W4-1/inventory.md` section
    5's reasoning, the same rule extends to ``scripts/``: a production
    script importing a test package is exactly backwards, and this
    fixture's only real property (a stamp nothing but the seeder ever
    writes) is what ``cvi/serve/guard.py``'s independently frozen
    ``_FIXTURE_CLOCK_SENTINEL`` exists to avoid depending on.

    W4-1f (review W-4): the ORIGINAL rule only named
    ``tests.fixtures.synthetic_catalog`` explicitly, so planting
    ``from tests.helpers.demo_app import DEMO_NOW`` in ``cvi/lakehouse.py``
    left this suite green -- a name-specific rule, not a layer rule.
    ``cvi/`` (unlike ``scripts/``) has no legitimate reason to import
    ANYTHING under ``tests.*``, so its arm is now widened to reject every
    ``tests.`` import, not just the one fixture module.

    ``scripts/demo_app.py`` is the one script allowed to import a
    ``tests.*`` name at all -- but only its OWN relocated helper
    (``tests.helpers.demo_app``), never the fixture module directly; the
    fixture import now lives one hop further in, inside
    ``tests/helpers/demo_app.py`` itself, which is under ``tests/`` and
    so is not scanned here. ``scripts/`` therefore keeps the NARROW rule
    (only the fixture module is forbidden), so the shim's own
    ``tests.helpers.demo_app`` import stays legal.
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
    """W4-1f (review W-3, replacing a proposed guard that does not work).

    The naive guard -- ``Path(cvi.__file__).is_relative_to(ROOT)`` --
    does NOT catch a real hazard in this project's shared dev venv: its
    editable install (``__editable___cvi_0_1_0_finder.py``) hardcodes the
    MAIN CHECKOUT's path for any ``cvi.<leaf>`` submodule import (a
    direct child of ``cvi``, e.g. ``cvi.ingest``), regardless of what
    ``cvi.__path__`` actually resolved to -- while the standard
    ``PathFinder``, checked first, already shadows this correctly for
    anything that still exists on disk where ``PYTHONPATH`` points. The
    top-level ``cvi`` package resolved correctly (inside the worktree)
    during the W4-1 review even while ``cvi.ingest``/``cvi.benchmark``/
    ``cvi.benchmark_data`` -- already deleted from that worktree -- each
    "imported successfully" from the sibling main checkout instead of
    raising ``ModuleNotFoundError``, because a module ABSENT from disk
    is exactly the one case the standard ``PathFinder`` cannot shadow.

    This test checks BOTH directions in one pass: every ``cvi/**/*.py``
    module that exists on disk today (walked fresh, so it can't go
    stale) MUST import successfully and resolve inside this repo root;
    PLUS the exact three module names W4-1 deleted, which must EITHER
    raise ``ModuleNotFoundError`` (correct, once truly gone everywhere)
    OR -- if something still manages to import them -- resolve inside
    this root too. A module that raises ``ModuleNotFoundError`` is
    accepted; one that "imports fine" from outside the root is not. This
    passes on main today (every deleted name now raises
    ``ModuleNotFoundError`` everywhere) and would have failed during the
    W4-1 review, when the trap made the three deleted names resolve
    successfully from the sibling main checkout instead.
    """
    on_disk: list[str] = []
    for path in sorted(REPO_ROOT.joinpath("cvi").rglob("*.py")):
        parts = path.relative_to(REPO_ROOT).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            on_disk.append(".".join(parts))
    assert on_disk, "no cvi/**/*.py modules found to check"

    #: W4-1's own deletions -- included explicitly (not just "whatever is
    #: on disk today") so this test would have caught the historical trap
    #: even though none of these three names are on disk to be walked.
    deleted_by_w4_1 = ["cvi.ingest", "cvi.benchmark", "cvi.benchmark_data"]
    names = sorted(set(on_disk) | set(deleted_by_w4_1))

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
