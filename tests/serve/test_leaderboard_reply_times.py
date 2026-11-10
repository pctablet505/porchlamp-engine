"""The leaderboard's reply measures and the ``first_reply`` sort (`porchlamp/serve/app.py`, `porchlamp/store/queries.py`).

Every leaderboard row carries ``first_reply_hours``/``first_reply_n`` and ``no_reply_14d_share``/``no_reply_14d_n``,
read from the project's own evidence (the ``ttfhr_hours`` and ``gr14_ghost_rate`` rows ``/evidence`` shows): a value
only when it is measured, its sample either way. ``sort=first_reply`` puts the fastest first and ``-first_reply`` the
slowest; a project without a measured median comes last in both directions, and ties keep the ranking's order. The
custom-weight rows (``/reproject``) carry the same values.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # tests/ on sys.path for `store.helpers`

from porchlamp.serve.app import create_app
from porchlamp.store import queries

from serve.helpers import SNAPSHOT_ID, build_snapshot, finalize, fresh_db

#: The evidence planted on the projects ranked 1 to 6: rank -> (ttfhr_hours, gr14_ghost_rate), each
#: ``(value, n, measured)`` or ``None`` for no evidence row. Rank 2 sits on the methodology's minimum sample (10,
#: measured), rank 3 one below it (9, unmeasured, with a raw value as the store keeps for many unmeasured shares);
#: ranks 2 and 4 tie on the median.
PLANTED = {
    1: ((200.0, 40, True), (0.25, 40, True)),
    2: ((3.5, 10, True), (0.0, 10, True)),
    3: ((0.01, 9, False), (0.4, 9, False)),
    4: ((3.5, 25, True), (0.5, 25, True)),
    5: (None, None),
    6: ((0.05, 11, True), (0.1, 11, True)),
}
WEIGHTS = {"weight_responsiveness": 0.4, "weight_criticality": 0.2, "weight_governance": 0.2, "weight_rigor": 0.1,
           "weight_accessibility": 0.1}


def _plant(conn: sqlite3.Connection) -> dict[int, tuple[str, str]]:
    """Writes :data:`PLANTED` over whatever evidence those projects had; returns rank -> (owner, name)."""
    by_rank = {}
    for rank in PLANTED:
        repo_id, owner, name = conn.execute(
            "SELECT id, owner, name FROM repositories WHERE snapshot_id = ? AND rank = ?", (SNAPSHOT_ID, rank)
        ).fetchone()
        by_rank[rank] = (owner, name)
        conn.execute("DELETE FROM params WHERE repo_id = ? AND param_id IN ('ttfhr_hours', 'gr14_ghost_rate')", (repo_id,))
        for param_id, planted in zip(("ttfhr_hours", "gr14_ghost_rate"), PLANTED[rank]):
            if planted is None:
                continue
            value, n, measured = planted
            conn.execute(
                "INSERT INTO params (repo_id, param_id, raw_value, score, measured, reason, n) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (repo_id, param_id, None if value is None else repr(value), 50.0 if measured else None, int(measured),
                 None if measured else "insufficient_n", n),
            )
    conn.commit()
    return by_rank


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("reply")
    db_path = tmp / "db.sqlite"
    conn = fresh_db(db_path)
    build_snapshot(conn, db_path)
    by_rank = _plant(conn)
    finalize(conn, db_path)
    conn.close()
    web_dir = tmp / "web"
    web_dir.mkdir()
    (web_dir / "index.html").write_text("<html><body>shell</body></html>")
    return TestClient(create_app(str(db_path), web_dir=web_dir)), by_rank


def _rows(client: TestClient, **params) -> list[dict]:
    response = client.get("/v1/porchlamp/leaderboard", params={"page_size": 1000, **params})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == len(body["rows"])
    return body["rows"]


def _fields(row: dict) -> tuple:
    return row["first_reply_hours"], row["first_reply_n"], row["no_reply_14d_share"], row["no_reply_14d_n"]


def test_each_row_carries_its_reply_measures_a_value_only_when_measured(api):
    client, by_rank = api
    rows = {(r["owner"], r["name"]): r for r in _rows(client)}
    assert _fields(rows[by_rank[1]]) == (200.0, 40, 0.25, 40)
    assert _fields(rows[by_rank[2]]) == (3.5, 10, 0.0, 10)  # the minimum sample: measured
    assert _fields(rows[by_rank[3]]) == (None, 9, None, 9)  # one below it: its raw values withheld, its sample shown
    assert _fields(rows[by_rank[5]]) == (None, None, None, None)  # no evidence row
    assert _fields(rows[by_rank[6]]) == (0.05, 11, 0.1, 11)


@pytest.mark.parametrize("persona", ["balanced", "first_timer"])
@pytest.mark.parametrize("sort", ["first_reply", "-first_reply"])
def test_first_reply_sorts_the_measured_medians_and_puts_the_rest_last(api, sort, persona):
    client, _by_rank = api
    plain = _rows(client, persona=persona)
    rows = _rows(client, persona=persona, sort=sort)
    assert sorted(_key(r) for r in rows) == sorted(_key(r) for r in plain)  # the same rows, reordered
    measured = [r for r in rows if r["first_reply_hours"] is not None]
    unmeasured = [r for r in rows if r["first_reply_hours"] is None]
    assert measured and unmeasured
    assert rows == measured + unmeasured, "a project without a measured median comes last"
    position = {_key(r): i for i, r in enumerate(plain)}  # the ranking's own order breaks ties
    step = 1 if sort == "first_reply" else -1
    assert [_key(r) for r in measured] == [_key(r) for r in sorted(measured, key=lambda r: (step * r["first_reply_hours"], position[_key(r)]))]
    assert [_key(r) for r in unmeasured] == [_key(r) for r in sorted(unmeasured, key=lambda r: position[_key(r)])]


def _key(row: dict) -> tuple[str, str]:
    return row["owner"], row["name"]


def test_the_fastest_first_order_of_the_planted_medians(api):
    client, by_rank = api
    planted = [_key(r) for r in _rows(client, sort="first_reply") if _key(r) in set(by_rank.values())]
    # 0.05 h, then the 3.5 h tie in rank order (2 before 4), then 200 h; rank 3 (n = 9, unmeasured, though its raw
    # 0.01 h is the smallest) and rank 5 come after every measured median
    assert planted == [by_rank[6], by_rank[2], by_rank[4], by_rank[1], by_rank[3], by_rank[5]]


def test_the_custom_weight_rows_carry_the_same_reply_measures(api):
    client, by_rank = api
    leaderboard = {_key(r): _fields(r) for r in _rows(client)}
    reprojected = client.post("/v1/porchlamp/reproject?page_size=1000", json=WEIGHTS).json()["rows"]
    shared = [r for r in reprojected if _key(r) in leaderboard]
    assert shared and all(_fields(r) == leaderboard[_key(r)] for r in shared)
    assert any(_key(r) == by_rank[2] for r in shared)


def test_a_bad_sort_names_first_reply_after_the_pillars_as_the_export_lists_it(api):
    client, _by_rank = api
    response = client.get("/v1/porchlamp/leaderboard", params={"sort": "stars"})
    assert response.status_code == 422
    detail = response.json()["error"]["detail"]
    assert detail.index("'p_accessibility'") < detail.index("'first_reply'")
    assert queries.ALLOWED_SORT_FIELDS[-1] == "first_reply"
