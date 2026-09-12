"""Adversarial tests for ``cvi.lakehouse.OperationalStore`` (spec 11.2, 12).

Bar: a test that is easy to pass is a defect. Every test pins exact
expected values (exact-oracle style) so that column swaps, wrong enum
encoding, off-by-one filter boundaries, missing ON CONFLICT, or lost
defaults fail loudly.

Schema source of truth: ``docs/ARCHITECTURE.md`` section 11.2.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from cvi.lakehouse import OperationalStore
from cvi.models import (
    AIPosture,
    CVICompositeResult,
    Ecosystem,
    Persona,
    PillarScores,
    Quadrant,
    Tier,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def make_result(
    owner: str = "acme",
    name: str = "repo",
    ecosystem: Ecosystem = Ecosystem.PYPI,
    overall_cvi: float = 50.0,
    rank_tier: Tier = Tier.B,
    matrix_quadrant: Quadrant = Quadrant.SWEET_SPOT,
    leverage_score: float = 60.0,
    friction_score: float = 40.0,
    is_stage0_disqualified: bool = False,
    disqualification_reason: str | None = None,
    pillar_scores: PillarScores | None = None,
) -> CVICompositeResult:
    return CVICompositeResult(
        owner=owner,
        name=name,
        ecosystem=ecosystem,
        overall_cvi=overall_cvi,
        rank_tier=rank_tier,
        matrix_quadrant=matrix_quadrant,
        leverage_score=leverage_score,
        friction_score=friction_score,
        is_stage0_disqualified=is_stage0_disqualified,
        disqualification_reason=disqualification_reason,
        pillar_scores=pillar_scores,
    )


def make_pillars(
    responsiveness: float = 10.0,
    criticality: float = 20.0,
    governance: float = 30.0,
    rigor: float = 40.0,
    accessibility: float = 50.0,
) -> PillarScores:
    return PillarScores(
        responsiveness=responsiveness,
        criticality=criticality,
        governance=governance,
        rigor=rigor,
        accessibility=accessibility,
    )


def set_stars(store: OperationalStore, owner: str, name: str, stars: int) -> None:
    """Raw-metric columns are ingestion-layer territory (see lakehouse docstring);
    tests set them via direct SQL to exercise the filter paths."""
    store.connection.execute(
        "UPDATE repositories SET stars = ? WHERE owner = ? AND name = ?",
        (stars, owner, name),
    )
    store.connection.commit()


@pytest.fixture()
def store() -> OperationalStore:
    s = OperationalStore()
    yield s
    s.close()


# --------------------------------------------------------------------------- #
# Schema (spec 11.2)
# --------------------------------------------------------------------------- #
SPEC_COLUMNS = [
    "id",
    "owner",
    "name",
    "ecosystem",
    "domain_category",
    "stars",
    "forks",
    "monthly_downloads",
    "dependents_count",
    "openssf_criticality",
    "elephant_factor",
    "legal_ip_model",
    "ai_posture",
    "score_responsiveness",
    "score_criticality",
    "score_governance",
    "score_rigor_ai",
    "score_accessibility",
    "leverage_score",
    "friction_score",
    "matrix_quadrant",
    "overall_cvi",
    "rank_tier",
    "is_stage0_disqualified",
    "disqualification_reason",
    "last_indexed_at",
]

NOT_NULL_COLUMNS = {
    "owner",
    "name",
    "ecosystem",
    "domain_category",
    "stars",
    "forks",
    "openssf_criticality",
    "elephant_factor",
    "legal_ip_model",
    "ai_posture",
    "score_responsiveness",
    "score_criticality",
    "score_governance",
    "score_rigor_ai",
    "score_accessibility",
    "leverage_score",
    "friction_score",
    "matrix_quadrant",
    "overall_cvi",
    "rank_tier",
}


def test_schema_exact_column_set(store):
    """PRAGMA table_info must yield exactly the spec 11.2 column set, in order."""
    rows = store.connection.execute("PRAGMA table_info(repositories)").fetchall()
    assert [r[1] for r in rows] == SPEC_COLUMNS


def test_schema_not_null_flags(store):
    rows = store.connection.execute("PRAGMA table_info(repositories)").fetchall()
    flags = {r[1]: r[3] for r in rows}
    for col in NOT_NULL_COLUMNS:
        assert flags[col] == 1, f"{col} must be NOT NULL"
    for col in (
        "id",
        "monthly_downloads",
        "dependents_count",
        "is_stage0_disqualified",
        "disqualification_reason",
        "last_indexed_at",
    ):
        assert flags[col] == 0, f"{col} must not be NOT NULL"


def test_schema_defaults(store):
    rows = store.connection.execute("PRAGMA table_info(repositories)").fetchall()
    defaults = {r[1]: r[4] for r in rows}
    assert defaults["monthly_downloads"] == "0"
    assert defaults["dependents_count"] == "0"
    assert defaults["is_stage0_disqualified"] == "FALSE"
    assert defaults["last_indexed_at"] == "CURRENT_TIMESTAMP"


def test_spec_indexes_exist(store):
    names = {
        r[0]
        for r in store.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'repositories'"
        )
    }
    assert "idx_cvi_ecosystem_rank" in names
    assert "idx_cvi_quadrant" in names


def test_unique_owner_name_index(store):
    """UNIQUE(owner, name) must exist as a real unique index on exactly those columns."""
    idx_list = store.connection.execute("PRAGMA index_list(repositories)").fetchall()
    unique_idx = [r for r in idx_list if r[2] == 1]  # (seq, name, unique, origin, partial)
    assert unique_idx, "no unique index on repositories"
    # Find the index whose columns are exactly (owner, name).
    found = False
    for r in idx_list:
        name = r[1]
        cols = [c[2] for c in store.connection.execute(f"PRAGMA index_info('{name}')")]
        if cols == ["owner", "name"]:
            found = True
    assert found, "no unique index on exactly (owner, name)"


# --------------------------------------------------------------------------- #
# Upsert: uniqueness / last-wins
# --------------------------------------------------------------------------- #
def test_unique_owner_name_enforced_update_not_duplicate(store):
    """Second upsert of same owner/name must UPDATE, not duplicate.

    Mutation target: a plain INSERT (no ON CONFLICT) fails here with
    IntegrityError or leaves 2 rows.
    """
    id1 = store.upsert_composite(make_result(overall_cvi=10.0))
    id2 = store.upsert_composite(make_result(overall_cvi=99.0))
    count = store.connection.execute("SELECT COUNT(*) FROM repositories").fetchone()[0]
    assert count == 1
    row = store.get_repo("acme", "repo")
    assert row["overall_cvi"] == 99.0
    assert row["id"] == id1 == id2, "rowid must be stable across upserts"


def test_upsert_last_wins_all_scored_fields(store):
    store.upsert_composite(
        make_result(
            overall_cvi=10.0,
            rank_tier=Tier.D,
            matrix_quadrant=Quadrant.GRAVEYARD,
            leverage_score=5.0,
            friction_score=95.0,
        ),
        pillar_scores=make_pillars(1, 2, 3, 4, 5),
    )
    store.upsert_composite(
        make_result(
            overall_cvi=90.0,
            rank_tier=Tier.S,
            matrix_quadrant=Quadrant.SWEET_SPOT,
            leverage_score=95.0,
            friction_score=5.0,
        ),
        pillar_scores=make_pillars(6, 7, 8, 9, 10),
    )
    row = store.get_repo("acme", "repo")
    assert row["overall_cvi"] == 90.0
    assert row["rank_tier"] == "Tier S"
    assert row["matrix_quadrant"] == "Sweet Spot"
    assert row["leverage_score"] == 95.0
    assert row["friction_score"] == 5.0
    assert row["score_responsiveness"] == 6.0
    assert row["score_accessibility"] == 10.0


def test_upsert_without_pillars_resets_previous_pillars_to_zero(store):
    """Last-write-wins applies to pillar columns too: None -> 0.0, not stale values."""
    store.upsert_composite(make_result(), pillar_scores=make_pillars(50, 60, 70, 80, 90))
    store.upsert_composite(make_result())  # pillar_scores=None
    row = store.get_repo("acme", "repo")
    for col in (
        "score_responsiveness",
        "score_criticality",
        "score_governance",
        "score_rigor_ai",
        "score_accessibility",
    ):
        assert row[col] == 0.0, f"{col} must reset to 0.0, got {row[col]}"


def test_upsert_distinct_repos_no_collision(store):
    store.upsert_composite(make_result(owner="acme", name="a"))
    store.upsert_composite(make_result(owner="acme", name="b"))
    store.upsert_composite(make_result(owner="other", name="a"))
    count = store.connection.execute("SELECT COUNT(*) FROM repositories").fetchone()[0]
    assert count == 3


# --------------------------------------------------------------------------- #
# Upsert: round-trip
# --------------------------------------------------------------------------- #
def test_upsert_returns_int_rowid(store):
    rowid = store.upsert_composite(make_result())
    assert isinstance(rowid, int)
    assert rowid >= 1
    assert store.get_repo("acme", "repo")["id"] == rowid


def test_upsert_round_trip_all_fields(store):
    ts = datetime(2026, 9, 11, 12, 30, 45, tzinfo=timezone.utc)
    store.upsert_composite(
        make_result(
            owner="duckdb",
            name="duckdb",
            ecosystem=Ecosystem.CRATES,
            overall_cvi=87.25,
            rank_tier=Tier.S_PLUS,
            matrix_quadrant=Quadrant.CRUCIBLE,
            leverage_score=91.5,
            friction_score=44.75,
        ),
        pillar_scores=make_pillars(81.5, 88.25, 77.0, 90.125, 64.375),
        last_indexed_at=ts,
    )
    row = store.get_repo("duckdb", "duckdb")
    assert row is not None
    assert row["owner"] == "duckdb"
    assert row["name"] == "duckdb"
    assert row["ecosystem"] == "crates"
    assert row["overall_cvi"] == 87.25
    assert row["rank_tier"] == "Tier S+"
    assert row["matrix_quadrant"] == "Crucible"
    assert row["leverage_score"] == 91.5
    assert row["friction_score"] == 44.75
    assert row["score_responsiveness"] == 81.5
    assert row["score_criticality"] == 88.25
    assert row["score_governance"] == 77.0
    assert row["score_rigor_ai"] == 90.125
    assert row["score_accessibility"] == 64.375
    assert row["is_stage0_disqualified"] == 0
    assert row["disqualification_reason"] is None
    assert row["last_indexed_at"] == ts.isoformat()


def test_upsert_null_disqualification_reason_is_sql_null(store):
    store.upsert_composite(make_result())
    row = store.get_repo("acme", "repo")
    assert row["disqualification_reason"] is None
    raw = store.connection.execute(
        "SELECT disqualification_reason IS NULL FROM repositories"
    ).fetchone()[0]
    assert raw == 1


def test_upsert_disqualified_flag_and_reason(store):
    store.upsert_composite(
        make_result(
            is_stage0_disqualified=True,
            disqualification_reason="ghost_rate_14d=0.72 > 0.50",
        )
    )
    row = store.get_repo("acme", "repo")
    assert row["is_stage0_disqualified"] == 1
    assert row["disqualification_reason"] == "ghost_rate_14d=0.72 > 0.50"


def test_upsert_pillar_scores_none_defaults_zero(store):
    store.upsert_composite(make_result())
    row = store.get_repo("acme", "repo")
    for col in (
        "score_responsiveness",
        "score_criticality",
        "score_governance",
        "score_rigor_ai",
        "score_accessibility",
    ):
        assert row[col] == 0.0


def test_upsert_raw_metric_defaults_documented(store):
    """CVICompositeResult carries no raw metrics; defaults are documented in
    the lakehouse module docstring. Pin them so silent drift is caught."""
    store.upsert_composite(make_result())
    row = store.get_repo("acme", "repo")
    assert row["domain_category"] == ""
    assert row["stars"] == 0
    assert row["forks"] == 0
    assert row["monthly_downloads"] == 0
    assert row["dependents_count"] == 0
    assert row["openssf_criticality"] == 0.0
    assert row["elephant_factor"] == 1
    assert row["legal_ip_model"] == "DCO"
    assert row["ai_posture"] == "AD_HOC"


def test_upsert_last_indexed_at_none_insert_uses_db_default(store):
    store.upsert_composite(make_result())
    row = store.get_repo("acme", "repo")
    assert row["last_indexed_at"] is not None
    # Must be parseable as a timestamp (CURRENT_TIMESTAMP -> 'YYYY-MM-DD HH:MM:SS').
    datetime.fromisoformat(row["last_indexed_at"].replace(" ", "T"))


def test_upsert_last_indexed_at_none_update_preserves_existing(store):
    ts = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    store.upsert_composite(make_result(), last_indexed_at=ts)
    store.upsert_composite(make_result(overall_cvi=42.0))  # no timestamp
    row = store.get_repo("acme", "repo")
    assert row["overall_cvi"] == 42.0
    assert row["last_indexed_at"] == ts.isoformat(), "update must not clobber last_indexed_at"


# --------------------------------------------------------------------------- #
# get_repo
# --------------------------------------------------------------------------- #
def test_get_repo_missing_returns_none(store):
    assert store.get_repo("nope", "nothing") is None


def test_get_repo_returns_full_row_dict(store):
    store.upsert_composite(make_result())
    row = store.get_repo("acme", "repo")
    assert set(row.keys()) == set(SPEC_COLUMNS)


# --------------------------------------------------------------------------- #
# query_matrix (spec 12.1)
# --------------------------------------------------------------------------- #
MATRIX_KEYS = {"owner", "name", "x", "y", "cvi", "tier", "quadrant", "size", "is_gated"}


def _seed_matrix(store: OperationalStore) -> None:
    """5 repos across ecosystems/quadrants with distinct stars and CVI."""
    # (owner, name, ecosystem, quadrant, cvi, friction, leverage, stars, gated)
    seed = [
        ("a", "alpha", Ecosystem.PYPI, Quadrant.SWEET_SPOT, 90.0, 10.0, 90.0, 5000, False),
        ("b", "beta", Ecosystem.PYPI, Quadrant.CRUCIBLE, 80.0, 60.0, 80.0, 1000, False),
        ("c", "gamma", Ecosystem.NPM, Quadrant.SWEET_SPOT, 70.0, 20.0, 70.0, 100, False),
        ("d", "delta", Ecosystem.NPM, Quadrant.GRAVEYARD, 60.0, 90.0, 10.0, 50, True),
        ("e", "epsilon", Ecosystem.GO, Quadrant.NURSERY, 50.0, 30.0, 40.0, 10, False),
    ]
    for owner, name, eco, quad, cvi, fric, lev, stars, gated in seed:
        store.upsert_composite(
            make_result(
                owner=owner,
                name=name,
                ecosystem=eco,
                matrix_quadrant=quad,
                overall_cvi=cvi,
                friction_score=fric,
                leverage_score=lev,
                is_stage0_disqualified=gated,
            )
        )
        set_stars(store, owner, name, stars)


def test_matrix_node_shape(store):
    _seed_matrix(store)
    nodes = store.query_matrix()
    assert len(nodes) == 5
    for node in nodes:
        assert set(node.keys()) == MATRIX_KEYS


def test_matrix_coordinate_mapping_no_swap(store):
    """x MUST be friction, y MUST be leverage, size MUST be stars.

    Mutation target: swapping x/y or mapping size to forks fails here.
    """
    _seed_matrix(store)
    by_name = {n["name"]: n for n in store.query_matrix()}
    alpha = by_name["alpha"]
    assert alpha["x"] == 10.0, "x must be friction_score"
    assert alpha["y"] == 90.0, "y must be leverage_score"
    assert alpha["cvi"] == 90.0
    assert alpha["size"] == 5000, "size must be stars"
    assert alpha["tier"] == "Tier B"
    assert alpha["quadrant"] == "Sweet Spot"
    assert alpha["is_gated"] == 0
    delta = by_name["delta"]
    assert delta["is_gated"] == 1


def test_matrix_ecosystem_filter(store):
    _seed_matrix(store)
    nodes = store.query_matrix(ecosystem="pypi")
    assert {n["name"] for n in nodes} == {"alpha", "beta"}
    assert store.query_matrix(ecosystem="nope") == []


def test_ecosystem_filter_case_insensitive_across_endpoints(store):
    """The UI sends ONE filter value to matrix + leaderboard + quadrant badges;
    all three must agree regardless of the caller's casing (stored values are
    lowercase enum values). Regression: query_matrix/count_by_quadrant used a
    case-sensitive `ecosystem = ?` while leaderboard used LOWER(), so a value
    like "PyPI" showed nodes in the scatter but zero in the badges."""
    _seed_matrix(store)
    for value in ("pypi", "PyPI", "PYPI", "pYpI"):
        nodes = {n["name"] for n in store.query_matrix(ecosystem=value)}
        rows, total = store.leaderboard(ecosystem=value)
        badges = store.count_by_quadrant(value)
        assert nodes == {"alpha", "beta"}, value
        assert total == 2 and {r["name"] for r in rows} == {"alpha", "beta"}, value
        assert badges == {"Sweet Spot": 1, "Crucible": 1,
                          "Graveyard": 0, "Nursery": 0}, value


def test_matrix_quadrant_filter(store):
    _seed_matrix(store)
    nodes = store.query_matrix(quadrant="Sweet Spot")
    assert {n["name"] for n in nodes} == {"alpha", "gamma"}
    assert store.query_matrix(quadrant="Nonexistent") == []


def test_matrix_min_stars_boundary_inclusive(store):
    """stars == min_stars MUST be included (>=, not >)."""
    _seed_matrix(store)
    nodes = store.query_matrix(min_stars=100)
    assert {n["name"] for n in nodes} == {"alpha", "beta", "gamma"}
    # Boundary: exactly 100 stars (gamma) included; 50 (delta) excluded.
    assert "gamma" in {n["name"] for n in nodes}
    assert "delta" not in {n["name"] for n in nodes}
    # Exact boundary at beta's 1000 stars: included at 1000, excluded at 1001.
    assert {n["name"] for n in store.query_matrix(min_stars=1000)} == {"alpha", "beta"}
    assert {n["name"] for n in store.query_matrix(min_stars=1001)} == {"alpha"}


def test_matrix_exclude_black_holes_drops_gated(store):
    _seed_matrix(store)
    all_names = {n["name"] for n in store.query_matrix()}
    assert all_names == {"alpha", "beta", "gamma", "delta", "epsilon"}
    filtered = store.query_matrix(exclude_black_holes=True)
    assert {n["name"] for n in filtered} == {"alpha", "beta", "gamma", "epsilon"}
    assert all(n["is_gated"] == 0 for n in filtered)


def test_matrix_limit_respected_and_top_cvi_first(store):
    _seed_matrix(store)
    nodes = store.query_matrix(limit=3)
    assert len(nodes) == 3
    assert [n["name"] for n in nodes] == ["alpha", "beta", "gamma"]
    assert [n["cvi"] for n in nodes] == [90.0, 80.0, 70.0]
    assert store.query_matrix(limit=0) == []


def test_matrix_default_limit_is_500(store):
    # Gap-closing: pin the query_matrix default limit (documented as
    # ``limit: int = 500`` in cvi.lakehouse). Phase 1 seeds more rows than a
    # 50-row default could return (57); phase 2 grows the store to 501 rows
    # so the default is pinned to exactly 500.
    for i in range(57):
        store.upsert_composite(
            make_result(
                owner="bulk", name=f"repo-{i:02d}", overall_cvi=float(100 - i)
            )
        )
    nodes = store.query_matrix()
    assert len(nodes) == 57
    assert [n["cvi"] for n in nodes][:3] == [100.0, 99.0, 98.0]
    assert [n["name"] for n in nodes][-1] == "repo-56"

    for i in range(57, 501):
        store.upsert_composite(
            make_result(
                owner="bulk", name=f"repo-{i:02d}", overall_cvi=float(500 - i)
            )
        )
    nodes = store.query_matrix()
    assert len(nodes) == 500
    assert nodes[0]["name"] == "repo-57"  # highest cvi (443.0) stays first
    assert nodes[0]["cvi"] == 443.0


def test_matrix_combined_filters(store):
    _seed_matrix(store)
    nodes = store.query_matrix(
        ecosystem="npm", min_stars=100, exclude_black_holes=True
    )
    assert [n["name"] for n in nodes] == ["gamma"]


def test_matrix_empty_store(store):
    assert store.query_matrix() == []


def test_matrix_no_where_path(store):
    _seed_matrix(store)
    nodes = store.query_matrix(min_stars=0, exclude_black_holes=False)
    assert len(nodes) == 5
    assert [n["name"] for n in nodes] == ["alpha", "beta", "gamma", "delta", "epsilon"]


# --------------------------------------------------------------------------- #
# leaderboard
# --------------------------------------------------------------------------- #
def test_leaderboard_desc_ordering_five_distinct_cvi(store):
    for i, (owner, cvi) in enumerate(
        [("r1", 10.0), ("r2", 50.0), ("r3", 30.0), ("r4", 90.0), ("r5", 70.0)]
    ):
        store.upsert_composite(make_result(owner=owner, name=f"repo{i}", overall_cvi=cvi))
    rows, total = store.leaderboard()
    assert total == 5
    assert [r["overall_cvi"] for r in rows] == [90.0, 70.0, 50.0, 30.0, 10.0]
    assert [r["owner"] for r in rows] == ["r4", "r5", "r2", "r3", "r1"]


def test_leaderboard_limit_boundary(store):
    for i in range(5):
        store.upsert_composite(
            make_result(owner=f"o{i}", name=f"n{i}", overall_cvi=float(i * 10))
        )
    rows3, t3 = store.leaderboard(limit=3)
    assert len(rows3) == 3 and t3 == 5
    assert [r["overall_cvi"] for r in rows3] == [40.0, 30.0, 20.0]
    rows5, t5 = store.leaderboard(limit=5)
    assert len(rows5) == 5 and t5 == 5
    rows100, t100 = store.leaderboard(limit=100)
    assert len(rows100) == 5 and t100 == 5
    rows0, t0 = store.leaderboard(limit=0)
    assert rows0 == [] and t0 == 5


def test_leaderboard_offset_pagination(store):
    for i in range(5):
        store.upsert_composite(
            make_result(owner=f"o{i}", name=f"n{i}", overall_cvi=float(i * 10))
        )
    page1, t1 = store.leaderboard(limit=2, offset=0)
    assert [r["overall_cvi"] for r in page1] == [40.0, 30.0]
    assert t1 == 5

    page2, t2 = store.leaderboard(limit=2, offset=2)
    assert [r["overall_cvi"] for r in page2] == [20.0, 10.0]
    assert t2 == 5

    page3, t3 = store.leaderboard(limit=2, offset=4)
    assert [r["overall_cvi"] for r in page3] == [0.0]
    assert t3 == 5


def test_leaderboard_ecosystem_filter(store):
    store.upsert_composite(make_result(owner="p", name="p1", overall_cvi=90.0))
    store.upsert_composite(
        make_result(owner="n", name="n1", ecosystem=Ecosystem.NPM, overall_cvi=99.0)
    )
    rows, total_p = store.leaderboard(ecosystem="pypi")
    assert [r["owner"] for r in rows] == ["p"]
    assert total_p == 1
    rows, total_n = store.leaderboard(ecosystem="npm")
    assert [r["owner"] for r in rows] == ["n"]
    assert total_n == 1


def test_leaderboard_search(store):
    store.upsert_composite(make_result(owner="facebook", name="react", overall_cvi=95.0))
    store.upsert_composite(make_result(owner="pallets", name="flask", overall_cvi=85.0))
    store.upsert_composite(make_result(owner="tiangolo", name="fastapi", overall_cvi=90.0))

    # Match by owner
    rows, t = store.leaderboard(search="face")
    assert [r["name"] for r in rows] == ["react"]
    assert t == 1

    # Match by name
    rows, t = store.leaderboard(search="fast")
    assert [r["name"] for r in rows] == ["fastapi"]
    assert t == 1

    # Match by owner/name slug
    rows, t = store.leaderboard(search="pallets/flask")
    assert [r["name"] for r in rows] == ["flask"]
    assert t == 1

    # Case insensitivity
    rows, t = store.leaderboard(search="FLASK")
    assert [r["name"] for r in rows] == ["flask"]
    assert t == 1

    # Whitespace ignored
    rows, t = store.leaderboard(search="   ")
    assert len(rows) == 3
    assert t == 3


def test_leaderboard_persona_reproject(store):
    # Repo A: high responsiveness (90), low criticality (10)
    # Repo B: low responsiveness (10), high criticality (90)
    p_a = PillarScores(90.0, 10.0, 50.0, 50.0, 50.0)
    p_b = PillarScores(10.0, 90.0, 50.0, 50.0, 50.0)
    store.upsert_composite(make_result(owner="o1", name="a", overall_cvi=50.0), pillar_scores=p_a)
    store.upsert_composite(make_result(owner="o2", name="b", overall_cvi=50.0), pillar_scores=p_b)

    # Under first_timer (resp 0.40, crit 0.10), Repo A scores much higher than Repo B
    rows, t = store.leaderboard(persona="first_timer")
    assert t == 2
    assert rows[0]["name"] == "a"
    assert rows[1]["name"] == "b"
    assert rows[0]["overall_cvi"] > rows[1]["overall_cvi"]


def test_leaderboard_persona_constraints(store):
    from cvi.ingest import apply_raw_metrics
    # Proscribed repo
    store.upsert_composite(make_result(owner="o1", name="gentoo", overall_cvi=80.0), pillar_scores=PillarScores(80, 80, 80, 80, 80))
    apply_raw_metrics(store, "o1", "gentoo", stars=100, forks=20, ai_posture=AIPosture.PROSCRIBED)

    # Low EF repo
    store.upsert_composite(make_result(owner="o2", name="single-firm", overall_cvi=80.0), pillar_scores=PillarScores(80, 80, 80, 80, 80))
    apply_raw_metrics(store, "o2", "single-firm", stars=100, forks=20, elephant_factor=1)

    # Under AI_ASSISTED, gentoo is gated to Tier D / 0.0
    rows_ai, _ = store.leaderboard(persona="ai_assisted")
    gentoo_row = next(r for r in rows_ai if r["name"] == "gentoo")
    assert gentoo_row["overall_cvi"] == 0.0
    assert gentoo_row["rank_tier"] == "Tier D"
    assert gentoo_row["is_stage0_disqualified"] == 1

    # Under SENIOR_SYSTEMS, single-firm (EF=1 < 3) is gated to Tier D / 0.0
    rows_ss, _ = store.leaderboard(persona="senior_systems")
    sf_row = next(r for r in rows_ss if r["name"] == "single-firm")
    assert sf_row["overall_cvi"] == 0.0
    assert sf_row["rank_tier"] == "Tier D"
    assert sf_row["is_stage0_disqualified"] == 1


def test_leaderboard_invalid_persona_raises(store):
    with pytest.raises(ValueError, match="Unknown persona"):
        store.leaderboard(persona="super_hacker")


def test_leaderboard_full_row_keys(store):
    store.upsert_composite(make_result())
    rows, total = store.leaderboard()
    assert total == 1
    assert set(rows[0].keys()) == set(SPEC_COLUMNS)


def test_additional_indexes_exist(store):
    cur = store.connection.execute("PRAGMA index_list('repositories')")
    names = {row[1] for row in cur.fetchall()}
    assert "idx_cvi_overall_rank" in names
    assert "idx_cvi_search" in names
    assert "idx_cvi_ecosystem_rank" in names
    assert "idx_cvi_quadrant" in names


def test_leaderboard_min_stars_filter(store):
    from cvi.ingest import apply_raw_metrics
    store.upsert_composite(make_result(owner="o1", name="small", overall_cvi=80.0))
    apply_raw_metrics(store, "o1", "small", stars=10, forks=2)
    store.upsert_composite(make_result(owner="o2", name="big", overall_cvi=70.0))
    apply_raw_metrics(store, "o2", "big", stars=500, forks=100)

    rows, total = store.leaderboard(min_stars=100)
    assert total == 1
    assert rows[0]["name"] == "big"


def test_leaderboard_persona_enum_and_disqualified(store):
    store.upsert_composite(
        make_result(
            owner="o1",
            name="dq",
            overall_cvi=0.0,
            is_stage0_disqualified=True,
            disqualification_reason="black hole",
        )
    )
    rows, total = store.leaderboard(persona=Persona.FIRST_TIMER)
    assert total == 1
    assert rows[0]["overall_cvi"] == 0.0
    assert rows[0]["rank_tier"] == Tier.D.value
    assert rows[0]["is_stage0_disqualified"] == 1


# --------------------------------------------------------------------------- #
# count_by_quadrant
# --------------------------------------------------------------------------- #
def test_count_by_quadrant_exact_distribution(store):
    """Hand-built: Sweet Spot 3, Crucible 2, Nursery 1, Graveyard 0."""
    for i in range(3):
        store.upsert_composite(
            make_result(owner=f"s{i}", name=f"s{i}", matrix_quadrant=Quadrant.SWEET_SPOT)
        )
    for i in range(2):
        store.upsert_composite(
            make_result(owner=f"c{i}", name=f"c{i}", matrix_quadrant=Quadrant.CRUCIBLE)
        )
    store.upsert_composite(
        make_result(owner="n0", name="n0", matrix_quadrant=Quadrant.NURSERY)
    )
    counts = store.count_by_quadrant()
    assert counts == {
        "Sweet Spot": 3,
        "Crucible": 2,
        "Nursery": 1,
        "Graveyard": 0,
    }


def test_count_by_quadrant_ecosystem_scoping(store):
    # pypi: Sweet Spot x2, Crucible x1. npm: Sweet Spot x1.
    store.upsert_composite(
        make_result(owner="p1", name="p1", matrix_quadrant=Quadrant.SWEET_SPOT)
    )
    store.upsert_composite(
        make_result(owner="p2", name="p2", matrix_quadrant=Quadrant.SWEET_SPOT)
    )
    store.upsert_composite(
        make_result(owner="p3", name="p3", matrix_quadrant=Quadrant.CRUCIBLE)
    )
    store.upsert_composite(
        make_result(
            owner="n1",
            name="n1",
            ecosystem=Ecosystem.NPM,
            matrix_quadrant=Quadrant.SWEET_SPOT,
        )
    )
    assert store.count_by_quadrant("pypi") == {
        "Sweet Spot": 2,
        "Crucible": 1,
        "Nursery": 0,
        "Graveyard": 0,
    }
    assert store.count_by_quadrant("npm") == {
        "Sweet Spot": 1,
        "Crucible": 0,
        "Nursery": 0,
        "Graveyard": 0,
    }
    # Unscoped total.
    assert store.count_by_quadrant()["Sweet Spot"] == 3


def test_count_by_quadrant_empty_store_all_zero(store):
    assert store.count_by_quadrant() == {
        "Sweet Spot": 0,
        "Crucible": 0,
        "Nursery": 0,
        "Graveyard": 0,
    }


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #
def test_close_idempotent_and_blocks_further_use(store):
    store.upsert_composite(make_result())
    store.close()
    store.close()  # idempotent
    with pytest.raises(sqlite3.ProgrammingError):
        store.get_repo("acme", "repo")


def test_file_backed_store_round_trip(tmp_path):
    """Schema + data must survive a close/reopen on a real file."""
    path = str(tmp_path / "cvi.db")
    s1 = OperationalStore(path)
    s1.upsert_composite(make_result(overall_cvi=12.5))
    s1.close()
    s2 = OperationalStore(path)
    row = s2.get_repo("acme", "repo")
    assert row is not None and row["overall_cvi"] == 12.5
    s2.close()


def test_upsert_deep_dive_success_and_absent(store):
    store.upsert_composite(make_result(owner="acme", name="tool"))
    payload = {"test": 123}
    assert store.upsert_deep_dive("acme", "tool", payload) == 1
    row = store.get_repo("acme", "tool")
    assert row is not None
    assert json.loads(row["deep_dive_json"]) == payload

    # Absent repo returns 0 rows updated
    assert store.upsert_deep_dive("nonexistent", "repo", payload) == 0
