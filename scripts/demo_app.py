"""Deprecated. Thin compatibility wrapper over ``tests/helpers/demo_app.py``.

W4-1 (`.agents/W4-1/inventory.md` sections 3/8, `04-ARCHITECTURE.md`
section 2 "scripts/demo_app.py (folded into a test helper)"): every real
piece of this file -- the 26 deterministic fixtures, the seeding logic,
``create_demo_app``, ``main`` -- moved verbatim to
``tests/helpers/demo_app.py``. Kept here, thin, for one release so
``.venv/bin/python scripts/demo_app.py`` (documented in the project
README's "Demo" section) still runs, and so ``tests/test_webapp.py``
(which loads this file BY PATH via ``importlib.util.
spec_from_file_location`` -- it pins v1 web + v1 demo end to end and is
not touched by this packet, per the inventory's section 6 v1-deletion
checklist, which defers it to a later, lead-verified packet) keeps
resolving ``demo.build_fixture_store`` / ``demo.create_demo_app`` /
``demo.main`` unchanged.

Run:
    .venv/bin/python scripts/demo_app.py              # in-memory, port 8000
    PORT=8765 .venv/bin/python scripts/demo_app.py    # port override
    .venv/bin/python scripts/demo_app.py --db /tmp/cvi-demo.sqlite
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

# `tests` is not installed, so the repo root goes on sys.path here (same
# pattern scripts/promote.py already uses) -- keeps this shim runnable
# standalone without requiring PYTHONPATH=$PWD.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.helpers.demo_app import (
    DEMO_NOW,
    build_fixture_store,
    create_demo_app,
    main,
    seed_fixture_store,
)

__all__ = [
    "DEMO_NOW",
    "seed_fixture_store",
    "build_fixture_store",
    "create_demo_app",
    "main",
]

warnings.warn(
    "scripts/demo_app.py is deprecated and will be removed: its logic "
    "moved to tests/helpers/demo_app.py (W4-1). Run "
    "`.venv/bin/python -m tests.helpers.demo_app` (or keep using this "
    "path for one release).",
    DeprecationWarning,
    stacklevel=2,
)

if __name__ == "__main__":
    main()
