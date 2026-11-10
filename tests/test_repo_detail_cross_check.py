"""Cross-check tests: `GET /v1/porchlamp/repos/{owner}/{name}` has two independent producers
-- the real API (`porchlamp/serve/app.py`) and the UI fixture
stand-in (`scripts/serve_ui_fixtures.py`) -- and nothing
bound them to the same wire contract until this defect reached a served
tree: the lead's final browser pass on `scripts/serve.py --db <the real
fixture store>` found the drawer for ANY repo throwing `TypeError: Cannot
read properties of undefined (reading 'owner')` and staying on "Loading
evidence...". Root cause: the real API returns the flat `RepoDetailResponse`
(`porchlamp/serve/contract.py`), while the fixture server returned
`{"kind": ..., "row": {...}}` and the drawer read `data.row`.

Ruling: the contract is the truth. This module builds BOTH producers (the
real API through `porchlamp.serve.app.create_app` on a synthetic store, via the
SAME `tests/serve/helpers.py` builder `tests/serve/test_api_v2.py` uses;
the fixture server through `scripts/serve_ui_fixtures.py`'s own `serve()`
on the static `tests/fixtures/ui/` files) and asserts, for a comparable
repo in each of the three `kind`s (rated / gated / unrated), that both
responses carry the EXACT SAME top-level key set and both independently
validate as `porchlamp.serve.contract.RepoDetailResponse` -- so a future edit to
either producer that breaks parity with the other fails here first, not
in a browser three packets later.
"""
from __future__ import annotations

import importlib.util
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import httpx
import pydantic
import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests"))  # tests/ on sys.path for `serve.helpers` (matches tests/serve/test_api_v2.py's own trick)

from porchlamp.serve.app import create_app  # noqa: E402
from porchlamp.serve.contract import RepoDetailResponse  # noqa: E402
from serve.helpers import build_snapshot, finalize, fresh_db  # noqa: E402

WEB_DIR = REPO_ROOT / "web" / "app"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "ui"
FIXTURE_SERVER_SCRIPT = REPO_ROOT / "scripts" / "serve_ui_fixtures.py"


def _load_fixture_server_module():
    spec = importlib.util.spec_from_file_location("porchlamp_serve_ui_fixtures_xcheck", FIXTURE_SERVER_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


serve_ui_fixtures = _load_fixture_server_module()


@pytest.fixture(scope="module")
def fixture_server_url() -> Iterator[str]:
    httpd = serve_ui_fixtures.serve(host="127.0.0.1", port=0, web_dir=WEB_DIR, fixtures_dir=FIXTURES_DIR)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def real_api_client(tmp_path_factory) -> TestClient:
    tmp_path = tmp_path_factory.mktemp("repo-detail-xcheck")
    db_path = tmp_path / "db.sqlite"
    conn = fresh_db(db_path)
    build_snapshot(conn, db_path)
    finalize(conn, db_path)
    conn.close()
    web_dir = tmp_path / "web"
    web_dir.mkdir()
    (web_dir / "index.html").write_text("<html></html>")
    app = create_app(str(db_path), web_dir=web_dir)
    return TestClient(app)


# (real owner, real name, fixture owner, fixture name) -- NOT the same
# repo (the two producers serve entirely independent synthetic datasets);
# "comparable" means one well-known example of the SAME `kind` from each
# store, per the module docstring's own definition of what this test binds.
COMPARABLE_REPOS = [
    pytest.param("acme", "flagged-repo", "riverline", "httpkit", id="rated"),
    pytest.param("gated-org", "gated-fork", "gated-org", "repo-mirror-0", id="gated"),
    pytest.param("unrated-org", "unrated-plain-0", "unrated-org", "repo-insufficient_evidence-0", id="unrated"),
]


@pytest.mark.parametrize("real_owner,real_name,fixture_owner,fixture_name", COMPARABLE_REPOS)
def test_repo_detail_key_set_and_contract_match_between_the_two_producers(
    real_api_client: TestClient,
    fixture_server_url: str,
    real_owner: str,
    real_name: str,
    fixture_owner: str,
    fixture_name: str,
) -> None:
    real_resp = real_api_client.get(f"/v1/porchlamp/repos/{real_owner}/{real_name}")
    assert real_resp.status_code == 200, real_resp.text
    real_body = real_resp.json()

    fixture_resp = httpx.get(f"{fixture_server_url}/v1/porchlamp/repos/{fixture_owner}/{fixture_name}")
    assert fixture_resp.status_code == 200, fixture_resp.text
    fixture_body = fixture_resp.json()

    # The real API's own `response_model=RepoDetailResponse` already
    # guarantees `real_body` validates (FastAPI refuses to serialise a
    # response that fails it) -- asserted again here anyway so a reader
    # sees BOTH sides checked against the same model in one place.
    RepoDetailResponse.model_validate(real_body)
    RepoDetailResponse.model_validate(fixture_body)

    assert set(fixture_body.keys()) == set(real_body.keys()), (
        f"key-set mismatch for kind={real_owner}/{real_name} vs {fixture_owner}/{fixture_name}: "
        f"real-only={set(real_body) - set(fixture_body)}, fixture-only={set(fixture_body) - set(real_body)}"
    )


def test_fail_before_the_old_kind_row_shape_fails_this_exact_validation() -> None:
    """N.B. proof, not a live regression check: the shape this test would
    have received from `scripts/serve_ui_fixtures.py` BEFORE this packet's
    fix (`{"kind": "rated", "row": {...}}`) must fail
    `RepoDetailResponse.model_validate` -- both because the two required
    top-level keys (`kind`, `row`) are not fields this model recognises
    (`StrictModel`'s `extra="forbid"`) and because every field the model
    actually requires (`owner`, `snapshot_id`, `raw_hash`, `eligible`, ...)
    is simply absent at the top level. This is the exact defect: nothing
    caught it because nothing had ever run `RepoDetailResponse.model_
    validate` against what the fixture server actually sent."""
    old_shape = {"kind": "rated", "row": {"owner": "riverline", "name": "httpkit", "ecosystem": "pypi"}}
    with pytest.raises(pydantic.ValidationError):
        RepoDetailResponse.model_validate(old_shape)
