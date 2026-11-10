"""The not-ranked list's order (``porchlamp/store/queries.py`` ``list_not_ranked``, served by ``/not-ranked`` and kept by
the export and the app's lists by reason): A to Z as a reader expects it, by owner and then by name, each case-insensitive,
digits before letters. An owner comes before a longer owner it begins: ``adobe/zeta`` before ``adobe-fonts/alpha``.
``scripts/verify_static_export.py`` works the same order out on its own (``not_ranked_projects``), to check the lists by.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # tests/ on sys.path for `serve.helpers`

from porchlamp.serve.app import create_app

from serve.helpers import SNAPSHOT_ID, build_snapshot, finalize, fresh_db

_SPEC = importlib.util.spec_from_file_location(
    "verify_static_export_order", Path(__file__).resolve().parents[2] / "scripts" / "verify_static_export.py")
verify_static_export = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(verify_static_export)

#: Projects the ranking leaves out (stale), in the order a reader expects them: digits first; an owner before a longer
#: owner it begins; case ignored; within an owner, a name before a longer name it begins.
EXPECTED = [("9lives", "a"), ("adobe", "zeta"), ("adobe-fonts", "alpha"), ("alpha", "X"), ("alpha", "x-ray"), ("alpha", "x0"),
            ("Beta", "one")]


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("order")
    db_path = tmp / "db.sqlite"
    conn = fresh_db(db_path)
    build_snapshot(conn, db_path)
    for i, (owner, name) in enumerate(reversed(EXPECTED)):
        conn.execute(
            "INSERT INTO repositories (snapshot_id, owner, name, ecosystem, raw_hash, constants_hash, eligible, gate_reason, "
            "rated, coverage, persona_gates, persona_composites, suspicion) VALUES (?, ?, ?, 'go', ?, ?, 0, 'stale', 0, 0.0, '{}', '{}', '[]')",
            (SNAPSHOT_ID, owner, name, f"{i + 1:064x}", "c" * 64),
        )
    conn.commit()
    finalize(conn, db_path)
    conn.close()
    return db_path


@pytest.fixture(scope="module")
def client(db_path):
    web_dir = db_path.parent / "web"
    web_dir.mkdir()
    (web_dir / "index.html").write_text("<html><body>shell</body></html>")
    return TestClient(create_app(str(db_path), web_dir=web_dir))


@pytest.mark.parametrize("persona", ["balanced", "first_timer"])
def test_the_not_ranked_list_runs_a_to_z_by_owner_then_name(client, persona):
    body = client.get("/v1/porchlamp/not-ranked", params={"persona": persona}).json()
    rows = [(r["owner"], r["name"]) for r in body["gated"] + body["unrated"] + body["persona_hidden"]]
    assert [row for row in rows if row in EXPECTED] == EXPECTED
    for group in ("gated", "unrated", "persona_hidden"):  # every group, the fixture's own projects too
        keys = [(r["owner"].lower(), r["name"].lower()) for r in body[group]]
        assert keys == sorted(keys), group


def test_the_verifier_works_out_the_same_order_on_its_own(db_path):
    projects = verify_static_export.not_ranked_projects(db_path, SNAPSHOT_ID)
    keys = [(p["owner"], p["name"]) for p in projects]
    assert [key for key in keys if key in EXPECTED] == EXPECTED
    assert keys == sorted(keys, key=lambda key: (key[0].lower(), key[1].lower()))
