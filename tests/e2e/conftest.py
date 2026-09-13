"""Shared fixtures and configuration for CVI E2E Test Suite (Tiers 1-4).

Follows the opaque-box testing methodology:
- Uses the official FastAPI API layer via TestClient.
- Uses the 1,000-repository benchmark catalog in an in-memory OperationalStore.
- Mounts web/ at / to verify the offline single-page application.
- Guarantees SQLite thread safety via Starlette's portal bridge for TestClient,
  and provides a direct_store fixture created on the test runner thread for
  direct SQL/store assertions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Generator

import pytest
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from cvi.api import create_app
from tests.fixtures.synthetic_catalog import build_benchmark_store
from cvi.lakehouse import OperationalStore

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WEB_DIR = REPO_ROOT / "web"
INDEX_HTML_PATH = WEB_DIR / "index.html"


@pytest.fixture(scope="session")
def web_html() -> str:
    """Read and cache the contents of web/index.html for static/offline UI tests."""
    return INDEX_HTML_PATH.read_text(encoding="utf-8")


@pytest.fixture()
def direct_store() -> OperationalStore:
    """An in-memory OperationalStore seeded with 1,000 repositories on the test thread."""
    return build_benchmark_store(count=1000)


@pytest.fixture()
def store(direct_store: OperationalStore) -> OperationalStore:
    """Alias for direct_store for tests doing direct SQL or store assertions."""
    return direct_store


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    """FastAPI TestClient with web/ mounted at / and 1,000 benchmark repos loaded.

    Uses client.portal.call to initialize the SQLite OperationalStore on the
    TestClient worker thread, eliminating any thread-binding conflicts for HTTP requests.
    """
    app = create_app()
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    test_client = TestClient(app)
    test_client.__enter__()
    # Build and seed 1,000 repositories directly on the event loop portal thread
    app.state.store = test_client.portal.call(lambda: build_benchmark_store(count=1000))
    try:
        yield test_client
    finally:
        test_client.__exit__(None, None, None)
