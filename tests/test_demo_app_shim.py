"""Tests for the scripts/demo_app.py deprecation shim.

The real logic (26 deterministic fixtures, seeding, ``create_demo_app``,
``main``) lives in ``tests/helpers/demo_app.py``; this file pins the
shim's two behaviours: it still warns, and the underlying app still
boots. ``tests/test_webapp.py`` separately proves the full v1
web-front-end behaviour works when the shim is loaded by path -- not
duplicated here.
"""

from __future__ import annotations

import importlib
import sys

import pytest


def test_importing_demo_app_shim_emits_deprecation_warning():
    """Importing scripts.demo_app emits a DeprecationWarning naming the
    deprecated path."""
    sys.modules.pop("scripts.demo_app", None)
    with pytest.warns(DeprecationWarning, match="scripts/demo_app.py is deprecated"):
        importlib.import_module("scripts.demo_app")


def test_demo_app_shim_warns_and_still_runs():
    """The one-release compatibility guarantee: the shim still exposes a
    working app after warning, re-exporting the helper's own objects."""
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
