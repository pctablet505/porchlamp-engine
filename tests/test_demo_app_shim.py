"""Tests for the scripts/demo_app.py deprecation shim (W4-1).

The real logic (26 deterministic fixtures, seeding, ``create_demo_app``,
``main``) moved to ``tests/helpers/demo_app.py``; this file pins the
shim's two behaviours: it still warns, and the underlying app still
boots. ``tests/test_webapp.py`` separately proves the full v1
web-front-end behaviour still works when the shim is loaded by path
(exactly as it did before the fold) -- not duplicated here.
"""

from __future__ import annotations

import importlib
import sys

import pytest


def test_importing_demo_app_shim_emits_deprecation_warning():
    """FAIL-BEFORE: before this shim existed (or if its warnings.warn call
    were removed), importing scripts.demo_app raised no warning at all --
    this test would then fail on pytest.warns' own "did not warn"
    assertion."""
    sys.modules.pop("scripts.demo_app", None)
    with pytest.warns(DeprecationWarning, match="scripts/demo_app.py is deprecated"):
        importlib.import_module("scripts.demo_app")


def test_demo_app_shim_warns_and_still_runs():
    """The one-release compatibility guarantee (per the packet's shim
    test pattern): the shim still exposes a working app after warning."""
    sys.modules.pop("scripts.demo_app", None)
    with pytest.warns(DeprecationWarning):
        import scripts.demo_app as demo_shim
    from tests.helpers import demo_app as demo_helper

    for name in ("DEMO_NOW", "seed_fixture_store", "build_fixture_store",
                 "create_demo_app", "main"):
        assert getattr(demo_shim, name) is getattr(demo_helper, name)

    # The underlying behaviour (app still boots / seeds) is intact.
    app = demo_shim.create_demo_app(count=5)
    assert app is not None
    assert app.state.store is not None
