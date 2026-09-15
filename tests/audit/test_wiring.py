"""Wiring tests for the lazy re-export registry in :mod:`cvi.audit`.

``cvi.audit.forward`` must be registered in ``_LAZY_EXPORTS`` with
exactly the module's public top-level names, and every registered name must
resolve through the package ``__getattr__`` / ``importlib`` to the object
defined in its submodule.  ``__getattr__`` hands a name to the first
registered module that lists it, so the shared name ``main`` (already owned
by ``coverage``) still resolves to ``cvi.audit.coverage.main``; every other
forward name resolves to ``cvi.audit.forward``.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
from pathlib import Path


def _public_top_level_names(source: str) -> set[str]:
    """Top-level names defined in *source*, excluding underscore-prefixed ones."""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name):
                names.add(target.id)
    return {name for name in names if not name.startswith("_")}


def test_forward_entry_lists_module_public_names() -> None:
    from cvi import audit

    origin = importlib.util.find_spec("cvi.audit.forward").origin
    expected = _public_top_level_names(Path(origin).read_text(encoding="utf-8"))
    assert "cvi.audit.forward" in audit._LAZY_EXPORTS
    assert set(audit._LAZY_EXPORTS["cvi.audit.forward"]) == expected
    assert not any(
        name.startswith("_") for name in audit._LAZY_EXPORTS["cvi.audit.forward"]
    )


def test_forward_names_resolve_via_getattr() -> None:
    """Each forward name resolves through ``__getattr__`` to its provider module's object."""
    from cvi import audit

    registry = audit._LAZY_EXPORTS
    assert set(registry["cvi.audit.forward"]) <= set(audit.__all__)
    for name in registry["cvi.audit.forward"]:
        provider = next(module for module, names in registry.items() if name in names)
        expected = importlib.import_module(provider)
        assert getattr(audit, name) is getattr(expected, name)


def test_all_registry_entries_resolve() -> None:
    from cvi import audit

    for module_name, names in audit._LAZY_EXPORTS.items():
        module = importlib.import_module(module_name)
        for name in names:
            assert hasattr(module, name)
            provider = next(m for m, n in audit._LAZY_EXPORTS.items() if name in n)
            assert getattr(audit, name) is getattr(importlib.import_module(provider), name)
