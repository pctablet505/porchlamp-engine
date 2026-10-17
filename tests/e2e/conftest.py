"""Shared fixtures and configuration for Porchlamp E2E Test Suite (Tiers 1-2).

Uses the 1,000-repository benchmark catalog in an in-memory OperationalStore,
created on the test runner thread for direct SQL/store assertions.
"""

from __future__ import annotations

import pytest

from tests.fixtures.synthetic_catalog import build_benchmark_store
from porchlamp.lakehouse import OperationalStore


@pytest.fixture()
def direct_store() -> OperationalStore:
    """An in-memory OperationalStore seeded with 1,000 repositories on the test thread."""
    return build_benchmark_store(count=1000)


@pytest.fixture()
def store(direct_store: OperationalStore) -> OperationalStore:
    """Alias for direct_store for tests doing direct SQL or store assertions."""
    return direct_store
