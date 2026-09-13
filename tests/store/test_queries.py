"""W4-3 acceptance: `cvi/store/queries.py` -- the SQL reprojection and read
paths F-013 (W4-2 review) asked for (`04` sections 5-7; `06` sections 3-4,
7; `02` sections 5, 7-10).

Two big parity requirements get their own tests near the bottom of this
file:

- **Tie-break SQL parity** (`04` section 6's amended ``ORDER BY``): SQL
  order under balanced and under every preset persona equals
  ``cvi.core.tiebreak.assign_ranks`` over a 200-row NULL-bearing synthetic
  snapshot, with a negative control proving the fixture is load-bearing.
- **Reprojection parity**: for a 300-row synthetic snapshot and 20 random
  weight vectors, ``reproject``'s SQL composite/coverage/tier/rank equal a
  Python oracle written INLINE from D-MISS-2/D-MISS-3 + ``assign_ranks`` --
  the oracle deliberately does not import ``cvi.core.aggregate`` or
  ``cvi.core.persona`` (W2-11 is changing those files concurrently; this
  packet stays out of them per its own dispatch)."""
from __future__ import annotations

import hashlib
import random
import sqlite3
import time
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple

import pytest

from cvi.core.gates import REASON_INSUFFICIENT_EVIDENCE
from cvi.core.models import PillarScore
from cvi.core.persona import PERSONA_PRESETS, persona_composite
from cvi.core.tiebreak import TieBreakInput, assign_ranks
from cvi.store import queries, schema
from cvi.store.migrations import apply_migrations
from cvi.store.writer import _normalize_utc_z, write_records, write_snapshot

from .helpers import (
    make_gated_record,
    make_measured,
    make_rated_record,
    make_repo_write,
    make_unrated_record,
)

PILLARS: Tuple[str, ...] = ("responsiveness", "criticality", "governance", "rigor", "accessibility")
BALANCED_WEIGHTS: Dict[str, float] = {
    "responsiveness": 0.25, "criticality": 0.25, "governance": 0.20, "rigor": 0.15, "accessibility": 0.15,
}


def _fresh_db(path: Path, snapshot_id: str = "snap-1") -> sqlite3.Connection:
    conn = schema.connect(path)
    apply_migrations(conn, applied_at="2026-09-13T00:00:00Z")
    write_snapshot(
        conn, id=snapshot_id, as_of="2026-09-13", benchmark_version="2.0.0-dev",
        constants_hash="c" * 64, code_commit="deadbeef", manifest_root="r",
        created_at="2026-09-13T00:00:00Z",
    )
    return conn


def _with_persona_composites(record):
    """Attach a REAL ``persona_composite()`` projection (every preset,
    ``balanced`` included) so persona-facing tests exercise the same
    production formula the engine writes at score time, rather than
    hand-typed JSON. Not used by the reprojection oracle test below (that
    one deliberately stays out of ``cvi.core.persona``/``aggregate``)."""
    composites = {persona: persona_composite(record, persona) for persona in PERSONA_PRESETS}
    return dc_replace(record, persona_composites=composites)


def _group_membership(entries: list) -> Dict[Any, frozenset]:
    """``entries``: ``[(identity, group_id), ...]``. Returns ``{identity:
    frozenset of every identity (including itself) sharing that non-None
    group_id}`` -- ``frozenset()`` when ``group_id`` is ``None``.

    An id-NUMBERING-independent way to compare two tie-grouping schemes:
    SQL's ``DENSE_RANK() OVER (ORDER BY composite DESC)``
    (:func:`cvi.store.queries._tie_group_sql`) assigns a new number to
    EVERY distinct composite value it visits, while
    ``cvi.core.tiebreak.assign_ranks``'s own counter only advances for
    composite values that actually recur -- the two schemes agree on WHICH
    rows share a group but not on the group's numeric label. Both null out
    a singleton (R-D), so a non-``None`` id here is always a real,
    size >= 2 share on both sides."""
    groups: Dict[Any, set] = {}
    for identity, group_id in entries:
        if group_id is not None:
            groups.setdefault(group_id, set()).add(identity)
    return {identity: (frozenset(groups[group_id]) if group_id is not None else frozenset()) for identity, group_id in entries}


def _stamp_tail_columns(conn: sqlite3.Connection, snapshot_id: str, name: str, ttfhr_p75, last_commit) -> None:
    """``ttfhr_p75``/``last_human_commit_at`` have no writer producer yet
    (``cvi/store/writer.py``'s own docstring) -- stamped directly via
    ``UPDATE``, the same pattern ``tests/store/test_reader.py`` uses."""
    conn.execute(
        "UPDATE repositories SET ttfhr_p75=?, last_human_commit_at=? WHERE snapshot_id=? AND name=?",
        (ttfhr_p75, _normalize_utc_z(last_commit), snapshot_id, name),
    )


# =========================================================================== #
# list_ranked -- balanced
# =========================================================================== #

def test_list_ranked_balanced_basic_order_and_total(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    items = [
        make_repo_write(make_rated_record("snap-1", owner="acme", name=f"r{i}", composite=90.0 - i, coverage=0.95))
        for i in range(3)
    ]
    write_records(conn, "snap-1", items)
    rows, total = queries.list_ranked(conn, "snap-1")
    assert total == 3
    assert [row["name"] for row in rows] == ["r0", "r1", "r2"]
    assert rows[0]["cvi"] == 90.0
    assert "cvi_p" not in rows[0]  # balanced: no persona-projection fields at all


def test_list_ranked_balanced_excludes_gated_and_unrated(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    items = [
        make_repo_write(make_rated_record("snap-1", owner="acme", name="rated1")),
        make_repo_write(make_gated_record("snap-1", owner="acme", name="gated1")),
        make_repo_write(make_unrated_record("snap-1", owner="acme", name="unrated1")),
    ]
    write_records(conn, "snap-1", items)
    rows, total = queries.list_ranked(conn, "snap-1")
    assert total == 1
    assert rows[0]["name"] == "rated1"


def test_list_ranked_pagination_is_a_total_deterministic_order(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    items = [
        make_repo_write(make_rated_record("snap-1", owner="acme", name=f"r{i:02d}", composite=float(100 - i), coverage=0.95))
        for i in range(10)
    ]
    write_records(conn, "snap-1", items)
    page1, total = queries.list_ranked(conn, "snap-1", limit=4, offset=0)
    page2, _ = queries.list_ranked(conn, "snap-1", limit=4, offset=4)
    page3, _ = queries.list_ranked(conn, "snap-1", limit=4, offset=8)
    assert total == 10
    names = [row["name"] for row in page1 + page2 + page3]
    assert names == [f"r{i:02d}" for i in range(10)]
    assert len(set(names)) == 10  # offset off-by-one would duplicate or skip a row


def test_list_ranked_ecosystem_filter_is_exact(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    items = [
        make_repo_write(make_rated_record("snap-1", owner="acme", name="py1", ecosystem="python")),
        make_repo_write(make_rated_record("snap-1", owner="acme", name="js1", ecosystem="js")),
    ]
    write_records(conn, "snap-1", items)
    rows, total = queries.list_ranked(conn, "snap-1", ecosystem="python")
    assert total == 1
    assert rows[0]["name"] == "py1"


def test_list_ranked_q_search_is_case_insensitive_substring_on_owner_or_name(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    items = [
        make_repo_write(make_rated_record("snap-1", owner="Acme", name="Flask")),
        make_repo_write(make_rated_record("snap-1", owner="Other", name="Django")),
    ]
    write_records(conn, "snap-1", items)
    rows, total = queries.list_ranked(conn, "snap-1", q="fla")
    assert total == 1 and rows[0]["name"] == "Flask"
    rows2, total2 = queries.list_ranked(conn, "snap-1", q="ACM")
    assert total2 == 1 and rows2[0]["owner"] == "Acme"
    rows3, total3 = queries.list_ranked(conn, "snap-1", q="nomatch")
    assert total3 == 0 and rows3 == []


def test_list_ranked_sort_validation_names_the_allowed_set(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    with pytest.raises(ValueError) as exc:
        queries.list_ranked(conn, "snap-1", sort="stars")
    message = str(exc.value)
    assert "stars" in message
    for field in queries.ALLOWED_SORT_FIELDS:
        assert field in message
    # U-3: vanity fields are never sortable -- not even a spellable input.
    for vanity in ("forks", "downloads"):
        with pytest.raises(ValueError):
            queries.list_ranked(conn, "snap-1", sort=vanity)


def test_list_ranked_sort_by_coverage_overrides_composite_as_primary_key(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    items = [
        make_repo_write(make_rated_record("snap-1", owner="a", name="high-cvi-low-cov", composite=95.0, coverage=0.65)),
        make_repo_write(make_rated_record("snap-1", owner="a", name="low-cvi-high-cov", composite=80.0, coverage=0.99)),
    ]
    write_records(conn, "snap-1", items)
    rows_default, _ = queries.list_ranked(conn, "snap-1")
    assert [row["name"] for row in rows_default] == ["high-cvi-low-cov", "low-cvi-high-cov"]
    rows_cov, _ = queries.list_ranked(conn, "snap-1", sort="coverage")
    assert [row["name"] for row in rows_cov] == ["low-cvi-high-cov", "high-cvi-low-cov"]


def test_list_ranked_sort_by_pillar(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    items = [
        make_repo_write(make_rated_record(
            "snap-1", owner="a", name="hi-resp", composite=80.0, coverage=0.9,
            pillar_scores={"responsiveness": 99.0, "criticality": 50.0, "governance": 50.0, "rigor": 50.0, "accessibility": 50.0},
        )),
        make_repo_write(make_rated_record(
            "snap-1", owner="a", name="lo-resp", composite=85.0, coverage=0.9,
            pillar_scores={"responsiveness": 10.0, "criticality": 90.0, "governance": 90.0, "rigor": 90.0, "accessibility": 90.0},
        )),
    ]
    write_records(conn, "snap-1", items)
    rows, _ = queries.list_ranked(conn, "snap-1", sort="responsiveness")
    assert [row["name"] for row in rows] == ["hi-resp", "lo-resp"]


# =========================================================================== #
# list_ranked -- persona
# =========================================================================== #

def test_list_ranked_persona_reads_stored_composite_never_recomputing(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    records = []
    for i in range(3):
        rec = make_rated_record(
            "snap-1", owner="acme", name=f"r{i}", composite=50.0, coverage=0.9,
            pillar_scores={
                "responsiveness": 90.0 - i * 5, "criticality": 90.0, "governance": 80.0,
                "rigor": 80.0, "accessibility": 80.0,
            },
        )
        records.append(_with_persona_composites(rec))
    write_records(conn, "snap-1", [make_repo_write(r) for r in records])

    rows, total = queries.list_ranked(conn, "snap-1", persona="senior_systems")
    assert total == 3
    expected = sorted(records, key=lambda r: -r.persona_composites["senior_systems"].cvi)
    assert [row["name"] for row in rows] == [r.repo.name for r in expected]
    assert [row["cvi_p"] for row in rows] == [r.persona_composites["senior_systems"].cvi for r in expected]
    assert [row["rank_p"] for row in rows] == [1, 2, 3]
    # The balanced fields on every row are untouched (stored, not recomputed).
    for row in rows:
        assert row["cvi"] == 50.0
        assert row["coverage"] == 0.9


def test_list_ranked_persona_excludes_persona_gated_rows_but_not_the_balanced_view(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    visible = _with_persona_composites(make_rated_record("snap-1", owner="a", name="visible"))
    hidden = _with_persona_composites(
        make_rated_record("snap-1", owner="a", name="hidden", persona_gates={"senior_systems": "elephant_factor_below_3"})
    )
    write_records(conn, "snap-1", [make_repo_write(visible), make_repo_write(hidden)])

    rows, total = queries.list_ranked(conn, "snap-1", persona="senior_systems")
    assert total == 1 and rows[0]["name"] == "visible"

    balanced_rows, balanced_total = queries.list_ranked(conn, "snap-1")
    assert balanced_total == 2  # D-PERS-1: a persona gate never touches the balanced view


def test_list_ranked_persona_excludes_persona_unrated_rows_even_when_balanced_rated(tmp_path) -> None:
    """`04` section 6 ruling: a repo rated under the balanced weights can be
    unrated under a persona's -- craft governance+rigor unmeasured (weight
    0.15+0.35=0.50 of ai_assisted's preset) so ai_assisted-weighted coverage
    falls under 0.60 while the STORED balanced coverage/rated stay high."""
    conn = _fresh_db(tmp_path / "db.sqlite")
    rec = make_rated_record("snap-1", owner="a", name="edge", composite=85.0, coverage=0.95)
    pillars = {
        "responsiveness": PillarScore(score=90.0, coverage=1.0),
        "criticality": PillarScore(score=90.0, coverage=1.0),
        "governance": PillarScore(score=None, coverage=0.0),
        "rigor": PillarScore(score=None, coverage=0.0),
        "accessibility": PillarScore(score=90.0, coverage=1.0),
    }
    rec = dc_replace(rec, pillars=pillars)
    rec = _with_persona_composites(rec)
    assert rec.persona_composites["ai_assisted"].rated is False  # sanity: fixture behaves as intended
    assert rec.persona_composites["ai_assisted"].coverage < 0.60

    write_records(conn, "snap-1", [make_repo_write(rec)])

    balanced_rows, balanced_total = queries.list_ranked(conn, "snap-1")
    assert balanced_total == 1

    persona_rows, persona_total = queries.list_ranked(conn, "snap-1", persona="ai_assisted")
    assert persona_total == 0

    not_ranked_rows, counts = queries.list_not_ranked(conn, "snap-1", persona="ai_assisted")
    assert len(not_ranked_rows) == 1
    assert not_ranked_rows[0]["reason"] == REASON_INSUFFICIENT_EVIDENCE
    assert counts == {REASON_INSUFFICIENT_EVIDENCE: 1}


def test_list_ranked_persona_includes_balanced_unrated_but_persona_rated_rows(tmp_path) -> None:
    """The reverse of the ruling's other half: a stored ``rated=0`` row with
    fully-measured pillars can appear under a persona whose weights clear
    0.60 on those pillars, even though ``list_ranked(persona="balanced")``
    (which reads the stored column verbatim) still excludes it."""
    conn = _fresh_db(tmp_path / "db.sqlite")
    rec = make_unrated_record("snap-1", owner="a", name="reverse-edge", coverage=0.3)
    pillars = {name: PillarScore(score=85.0, coverage=1.0) for name in PILLARS}
    rec = dc_replace(rec, pillars=pillars)
    rec = _with_persona_composites(rec)
    assert rec.persona_composites["senior_systems"].rated is True  # sanity

    write_records(conn, "snap-1", [make_repo_write(rec)])

    balanced_rows, balanced_total = queries.list_ranked(conn, "snap-1")
    assert balanced_total == 0

    persona_rows, persona_total = queries.list_ranked(conn, "snap-1", persona="senior_systems")
    assert persona_total == 1
    assert persona_rows[0]["name"] == "reverse-edge"
    assert persona_rows[0]["rated"] is False  # balanced field, untouched
    assert persona_rows[0]["rated_p"] is True


def test_list_ranked_unknown_persona_raises(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    with pytest.raises(ValueError, match="no-such-persona"):
        queries.list_ranked(conn, "snap-1", persona="no-such-persona")


# =========================================================================== #
# list_not_ranked
# =========================================================================== #

def test_list_not_ranked_balanced_reasons_and_counts_match_list_ranked_complement(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    items = [
        make_repo_write(make_rated_record("snap-1", owner="a", name="rated1")),
        make_repo_write(make_gated_record("snap-1", owner="a", name="gated1", gate_reason="mirror")),
        make_repo_write(make_unrated_record("snap-1", owner="a", name="unrated1")),
    ]
    write_records(conn, "snap-1", items)
    _, ranked_total = queries.list_ranked(conn, "snap-1")
    not_ranked_rows, counts = queries.list_not_ranked(conn, "snap-1")
    assert ranked_total + len(not_ranked_rows) == 3
    assert {row["name"] for row in not_ranked_rows} == {"gated1", "unrated1"}
    assert counts == {"mirror": 1, "unrated: insufficient_evidence": 1}
    assert all(row["rank"] is None and row["tie_group"] is None for row in not_ranked_rows)
    assert [row["name"] for row in not_ranked_rows] == sorted(row["name"] for row in not_ranked_rows)


def test_list_ranked_and_list_not_ranked_partition_every_row_under_every_lens(tmp_path) -> None:
    """The exhaustive complement invariant (`06` section 8 acceptance item
    2: the two tabs' counts must always match) over a mixed 30-row fixture,
    checked for balanced AND every preset persona at once."""
    conn = _fresh_db(tmp_path / "db.sqlite")
    rng = random.Random(7)
    records = []
    for i in range(30):
        eligible = i % 5 != 0
        if eligible:
            rec = make_rated_record("snap-1", owner="a", name=f"row{i:02d}")
            pillars = {name: PillarScore(score=rng.uniform(30, 99), coverage=rng.choice([0.0, 0.5, 1.0])) for name in PILLARS}
            rec = dc_replace(rec, pillars=pillars)
            gates = {"career_capital": "criticality_below_threshold"} if i % 7 == 0 else {}
            rec = dc_replace(rec, persona_gates=gates)
            rec = _with_persona_composites(rec)
        else:
            rec = make_gated_record("snap-1", owner="a", name=f"row{i:02d}")
        records.append(rec)
    write_records(conn, "snap-1", [make_repo_write(r) for r in records])

    all_names = {r.repo.name for r in records}
    for persona in PERSONA_PRESETS:
        ranked_rows, ranked_total = queries.list_ranked(conn, "snap-1", persona=persona)
        not_ranked_rows, _ = queries.list_not_ranked(conn, "snap-1", persona=persona)
        ranked_names = {row["name"] for row in ranked_rows}
        not_ranked_names = {row["name"] for row in not_ranked_rows}
        assert ranked_names.isdisjoint(not_ranked_names), persona
        assert ranked_names | not_ranked_names == all_names, persona
        assert len(ranked_rows) == ranked_total


# =========================================================================== #
# snapshot_summary
# =========================================================================== #

def test_snapshot_summary_counts_and_detector_summary(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    items = [
        make_repo_write(make_rated_record("snap-1", owner="a", name="rated1", suspicion=["param_jump"])),
        make_repo_write(make_rated_record("snap-1", owner="a", name="rated2", suspicion=["param_jump", "star_burst"])),
        make_repo_write(make_gated_record("snap-1", owner="a", name="gated1")),
        make_repo_write(make_unrated_record("snap-1", owner="a", name="unrated1")),
    ]
    write_records(conn, "snap-1", items)
    summary = queries.snapshot_summary(conn, "snap-1")
    assert summary["snapshot_id"] == "snap-1"
    assert summary["counts"] == {"rated": 2, "gated": 1, "unrated": 1, "total": 4}
    assert summary["detector_summary"] == {"param_jump": 2, "star_burst": 1}
    assert summary["reproduce_command"] == "python -m cvi.snapshot reproduce snap-1"
    assert summary["persona_visible_counts"]["balanced"] == 2
    assert set(summary["persona_visible_counts"]) == set(PERSONA_PRESETS)


def test_snapshot_summary_persona_visible_counts_match_list_ranked_totals(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    records = []
    for i in range(6):
        rec = make_rated_record("snap-1", owner="a", name=f"r{i}")
        pillars = {name: PillarScore(score=80.0, coverage=1.0) for name in PILLARS}
        rec = dc_replace(rec, pillars=pillars)
        gates = {"senior_systems": "elephant_factor_below_3"} if i < 2 else {}
        rec = dc_replace(rec, persona_gates=gates)
        records.append(_with_persona_composites(rec))
    write_records(conn, "snap-1", [make_repo_write(r) for r in records])

    summary = queries.snapshot_summary(conn, "snap-1")
    for persona in PERSONA_PRESETS:
        _, total = queries.list_ranked(conn, "snap-1", persona=persona)
        assert summary["persona_visible_counts"][persona] == total, persona


def test_snapshot_summary_missing_snapshot_returns_none(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    assert queries.snapshot_summary(conn, "no-such-snapshot") is None


# =========================================================================== #
# matrix_points
# =========================================================================== #

def test_matrix_points_balanced_basic_and_excluded_and_axis_partial(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    items = [
        make_repo_write(make_rated_record(
            "snap-1", owner="a", name="rated1", leverage=70.0, friction=20.0,
            leverage_coverage=1.0, friction_coverage=1.0,
        )),
        make_repo_write(make_rated_record(
            "snap-1", owner="a", name="rated2", leverage=50.0, friction=40.0,
            leverage_coverage=0.8, friction_coverage=1.0,
        )),
        make_repo_write(make_gated_record("snap-1", owner="a", name="gated1")),
    ]
    write_records(conn, "snap-1", items)
    result = queries.matrix_points(conn, "snap-1")
    assert {p["name"] for p in result["points"]} == {"rated1", "rated2"}
    assert result["excluded_count"] == 1
    assert result["axis_partial_count"] == 1  # rated2's leverage_coverage < 1.0
    assert result["axis_partial_share"] == 0.5


def test_matrix_points_persona_projects_cvi_coverage_tier_only(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    rec = make_rated_record("snap-1", owner="a", name="r1", composite=70.0, coverage=0.95, leverage=60.0, friction=30.0)
    pillars = {name: PillarScore(score=80.0, coverage=1.0) for name in PILLARS}
    rec = dc_replace(rec, pillars=pillars)
    rec = _with_persona_composites(rec)
    write_records(conn, "snap-1", [make_repo_write(rec)])

    balanced = queries.matrix_points(conn, "snap-1")["points"][0]
    persona = queries.matrix_points(conn, "snap-1", persona="career_capital")["points"][0]
    assert persona["leverage"] == balanced["leverage"] == 60.0  # D-MAT-1: persona-invariant
    assert persona["friction"] == balanced["friction"] == 30.0
    assert balanced["cvi"] == 70.0
    assert persona["cvi"] == rec.persona_composites["career_capital"].cvi


def test_matrix_points_unknown_persona_raises(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    with pytest.raises(ValueError):
        queries.matrix_points(conn, "snap-1", persona="nope")


# =========================================================================== #
# repo_evidence
# =========================================================================== #

def test_repo_evidence_shape_and_pillar_lookup(tmp_path) -> None:
    from cvi.core.models import ParamScore

    conn = _fresh_db(tmp_path / "db.sqlite")
    rec = make_rated_record(
        "snap-1", owner="a", name="r1",
        params={
            "ttfhr_hours": ParamScore(
                value=3.5, score=90.0, measured=True, reason=None, n=12,
                window_start="2026-03-05T00:00:00Z", window_end="2026-09-01T00:00:00Z",
                method="primary", source="data/raw/github_rest/a__r1.json", fetched_at="2026-09-01T00:00:00Z",
            ),
            "mr_first_rate": ParamScore(
                value=None, score=None, measured=False, reason="third_party_gap", n=0,
                window_start=None, window_end=None, method=None, source=None, fetched_at=None,
            ),
        },
    )
    item = make_repo_write(
        rec,
        inputs={
            "ttfhr_hours": make_measured(
                3.5, n=12, source="data/raw/github_rest/a__r1.json", source_hash="d" * 64,
                fetched_at="2026-09-01T00:00:00Z",
            ),
        },
    )
    write_records(conn, "snap-1", [item])

    result = queries.repo_evidence(conn, "snap-1", "a", "r1")
    assert result["repo"]["owner"] == "a"
    assert result["repo"]["name"] == "r1"
    by_id = {p["param_id"]: p for p in result["params"]}
    assert by_id["ttfhr_hours"]["pillar"] == "responsiveness"
    assert by_id["ttfhr_hours"]["value"] == 3.5
    assert by_id["ttfhr_hours"]["source"] == "data/raw/github_rest/a__r1.json"
    assert by_id["ttfhr_hours"]["source_sha256"] == "d" * 64
    assert by_id["ttfhr_hours"]["fetched_at"] == "2026-09-01T00:00:00Z"
    assert by_id["ttfhr_hours"]["unmeasured_reason"] is None
    assert by_id["ttfhr_hours"]["measured"] is True
    assert by_id["mr_first_rate"]["pillar"] == "responsiveness"
    assert by_id["mr_first_rate"]["measured"] is False
    assert by_id["mr_first_rate"]["unmeasured_reason"] == "third_party_gap"
    assert by_id["mr_first_rate"]["value"] is None


def test_repo_evidence_missing_repo_returns_none(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    assert queries.repo_evidence(conn, "snap-1", "no", "such") is None


def test_repo_evidence_gated_repo_has_no_params(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    write_records(conn, "snap-1", [make_repo_write(make_gated_record("snap-1", owner="a", name="g1"))])
    result = queries.repo_evidence(conn, "snap-1", "a", "g1")
    assert result["repo"]["eligible"] is False
    assert result["params"] == []


# =========================================================================== #
# reproject
# =========================================================================== #

def test_reproject_validates_weights(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    with pytest.raises(ValueError):
        queries.reproject(conn, "snap-1", {"responsiveness": 1.0})  # missing 4 pillars
    with pytest.raises(ValueError):
        queries.reproject(conn, "snap-1", {**{p: 0.2 for p in PILLARS}, "extra": 0.1})  # unknown key
    with pytest.raises(ValueError):
        queries.reproject(conn, "snap-1", {p: 0.19 for p in PILLARS})  # sums to 0.95


def test_reproject_renormalises_over_measured_pillars_only(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    rec = make_rated_record("snap-1", owner="a", name="r1", composite=1.0, coverage=1.0)
    pillars = {
        "responsiveness": PillarScore(score=80.0, coverage=1.0),
        "criticality": PillarScore(score=60.0, coverage=1.0),
        "governance": PillarScore(score=None, coverage=0.0),  # unmeasured
        "rigor": PillarScore(score=90.0, coverage=1.0),
        "accessibility": PillarScore(score=70.0, coverage=1.0),
    }
    rec = dc_replace(rec, pillars=pillars)
    write_records(conn, "snap-1", [make_repo_write(rec)])

    weights = {"responsiveness": 0.3, "criticality": 0.2, "governance": 0.2, "rigor": 0.2, "accessibility": 0.1}
    rows, total = queries.reproject(conn, "snap-1", weights)
    assert total == 1
    measured_weight = 0.3 + 0.2 + 0.2 + 0.1  # governance (weight 0.2) drops out entirely
    expected_raw = (0.3 * 80.0 + 0.2 * 60.0 + 0.2 * 90.0 + 0.1 * 70.0) / measured_weight
    assert rows[0]["cvi"] == round(expected_raw, 2)
    expected_coverage = 0.3 * 1.0 + 0.2 * 1.0 + 0.2 * 0.0 + 0.2 * 1.0 + 0.1 * 1.0
    assert rows[0]["coverage"] == pytest.approx(expected_coverage)
    assert rows[0]["lens"] == queries.CUSTOM_WEIGHT_LABEL == "custom"
    assert rows[0]["balanced_cvi"] == 1.0
    assert rows[0]["balanced_coverage"] == 1.0


def test_reproject_excludes_a_pillar_whose_coverage_is_zero_even_if_a_score_is_stored(tmp_path) -> None:
    """A pillar with ``c_pillar = 0`` never contributes to the composite,
    even if a (stray, non-production) score happens to be stored under it --
    D-MISS-2's "over pillars with c > 0" is a coverage test, not merely a
    NULL-score test. Real writer output never produces this combination
    (``cvi.core.aggregate.aggregate_pillars`` sets ``coverage=0.0`` exactly
    when ``score is None``); this is a defensive-SQL check, isolating the
    ``c_pillar > 0`` guard from the ``p_pillar IS NOT NULL`` guard next to
    it (mutation target: dropping the coverage guard alone)."""
    conn = _fresh_db(tmp_path / "db.sqlite")
    rec = make_rated_record("snap-1", owner="a", name="r1")
    pillars = {
        "responsiveness": PillarScore(score=80.0, coverage=1.0),
        "criticality": PillarScore(score=60.0, coverage=1.0),
        "governance": PillarScore(score=999.0, coverage=0.0),  # stray score under zero coverage
        "rigor": PillarScore(score=90.0, coverage=1.0),
        "accessibility": PillarScore(score=70.0, coverage=1.0),
    }
    rec = dc_replace(rec, pillars=pillars)
    write_records(conn, "snap-1", [make_repo_write(rec)])

    weights = {"responsiveness": 0.3, "criticality": 0.2, "governance": 0.2, "rigor": 0.2, "accessibility": 0.1}
    rows, _ = queries.reproject(conn, "snap-1", weights)
    measured_weight = 0.3 + 0.2 + 0.2 + 0.1  # governance excluded despite its stray score
    expected_raw = (0.3 * 80.0 + 0.2 * 60.0 + 0.2 * 90.0 + 0.1 * 70.0) / measured_weight
    assert rows[0]["cvi"] == round(expected_raw, 2)


def _uniform_pillars(score: float, coverage: float) -> Dict[str, PillarScore]:
    return {name: PillarScore(score=score, coverage=coverage) for name in PILLARS}


def test_reproject_applies_ceiling_b_band(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    rec = dc_replace(make_rated_record("snap-1", owner="a", name="r1"), pillars=_uniform_pillars(95.0, 0.65))
    write_records(conn, "snap-1", [make_repo_write(rec)])
    rows, _ = queries.reproject(conn, "snap-1", {p: 0.2 for p in PILLARS})
    assert rows[0]["coverage"] == pytest.approx(0.65)
    assert rows[0]["tier"] == "B"  # score alone (95 >= 90) would be S+; ceiling B caps it


def test_reproject_applies_ceiling_a_band(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    rec = dc_replace(make_rated_record("snap-1", owner="a", name="r1"), pillars=_uniform_pillars(95.0, 0.85))
    write_records(conn, "snap-1", [make_repo_write(rec)])
    rows, _ = queries.reproject(conn, "snap-1", {p: 0.2 for p in PILLARS})
    assert rows[0]["tier"] == "A"


def test_reproject_no_ceiling_above_0_95_coverage(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    rec = dc_replace(make_rated_record("snap-1", owner="a", name="r1"), pillars=_uniform_pillars(95.0, 0.97))
    write_records(conn, "snap-1", [make_repo_write(rec)])
    rows, _ = queries.reproject(conn, "snap-1", {p: 0.2 for p in PILLARS})
    assert rows[0]["tier"] == "S+"


def test_reproject_unrated_below_0_60_coverage_is_excluded(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    rec = dc_replace(make_rated_record("snap-1", owner="a", name="r1"), pillars=_uniform_pillars(95.0, 0.55))
    write_records(conn, "snap-1", [make_repo_write(rec)])
    rows, total = queries.reproject(conn, "snap-1", {p: 0.2 for p in PILLARS})
    assert total == 0 and rows == []


def test_reproject_rated_boundary_is_inclusive_at_exactly_0_60(tmp_path) -> None:
    """D-EVID-1/D-MISS-3: rated iff coverage >= 0.60 -- exactly 0.60 is
    RATED (mutation target: ``>`` in place of ``>=`` excludes this row)."""
    conn = _fresh_db(tmp_path / "db.sqlite")
    rec = dc_replace(make_rated_record("snap-1", owner="a", name="r1"), pillars=_uniform_pillars(95.0, 0.60))
    write_records(conn, "snap-1", [make_repo_write(rec)])
    rows, total = queries.reproject(conn, "snap-1", {p: 0.2 for p in PILLARS})
    assert total == 1 and rows[0]["rated"] is True


def test_list_ranked_persona_rated_boundary_is_inclusive_at_exactly_0_60(tmp_path) -> None:
    """Same D-EVID-1 boundary, exercised through the persona projection's
    stored ``coverage_p`` (`04` section 6's ``coverage_p >= 0.60`` filter)."""
    conn = _fresh_db(tmp_path / "db.sqlite")
    from cvi.core.models import PersonaComposite

    rec = make_rated_record(
        "snap-1", owner="a", name="r1",
        persona_composites={"career_capital": PersonaComposite(cvi=70.0, coverage=0.60, tier="B", rated=True)},
    )
    write_records(conn, "snap-1", [make_repo_write(rec)])
    rows, total = queries.list_ranked(conn, "snap-1", persona="career_capital")
    assert total == 1 and rows[0]["coverage_p"] == 0.60


def test_reproject_gated_repo_never_appears_regardless_of_weights(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    write_records(conn, "snap-1", [make_repo_write(make_gated_record("snap-1", owner="a", name="g1"))])
    rows, total = queries.reproject(conn, "snap-1", {p: 0.2 for p in PILLARS})
    assert total == 0


def test_reproject_and_persona_tie_group_nulls_singleton_and_shares_non_singleton(tmp_path) -> None:
    """R-D ruling, exercised through both call sites that use
    :func:`queries._tie_group_sql` (``list_ranked``'s persona branch and
    ``reproject`` -- balanced ``list_ranked`` reads the STORED
    ``tie_group`` column and never calls this function at all)."""
    conn = _fresh_db(tmp_path / "db.sqlite")
    tied = _uniform_pillars(80.0, 0.9)
    single = _uniform_pillars(20.0, 0.9)
    records = [
        make_rated_record("snap-1", owner="a", name="t1", pillar_scores={k: v.score for k, v in tied.items()}),
        make_rated_record("snap-1", owner="a", name="t2", pillar_scores={k: v.score for k, v in tied.items()}),
        make_rated_record("snap-1", owner="a", name="single", pillar_scores={k: v.score for k, v in single.items()}),
    ]
    records = [_with_persona_composites(r) for r in records]
    write_records(conn, "snap-1", [make_repo_write(r) for r in records])

    persona_rows, _ = queries.list_ranked(conn, "snap-1", persona="career_capital", limit=10)
    by_name = {r["name"]: r for r in persona_rows}
    assert by_name["t1"]["tie_group_p"] == by_name["t2"]["tie_group_p"] is not None
    assert by_name["single"]["tie_group_p"] is None

    reproject_rows, _ = queries.reproject(conn, "snap-1", {p: 0.2 for p in PILLARS}, limit=10, offset=0)
    by_name_2 = {r["name"]: r for r in reproject_rows}
    assert by_name_2["t1"]["tie_group"] == by_name_2["t2"]["tie_group"] is not None
    assert by_name_2["single"]["tie_group"] is None


# --------------------------------------------------------------------------- #
# Reprojection parity: 300-row synthetic snapshot, 20 random weight vectors,
# vs. an inline D-MISS-2/D-MISS-3 + tiebreak.assign_ranks oracle.
# --------------------------------------------------------------------------- #

def _reproject_oracle(
    pillar_data: Mapping[str, Tuple[Any, float]], weights: Mapping[str, float]
) -> Tuple[Any, float, Any, bool]:
    """D-MISS-2/D-MISS-3 computed directly from `02` sections 5, 7-8 --
    deliberately NOT via ``cvi.core.aggregate``/``cvi.core.persona`` (W2-11
    is changing those files concurrently; this packet's own dispatch says
    to stay out of them). Returns ``(composite_2dp, coverage, tier,
    rated)``; ``composite``/``tier`` are ``None`` when not rated."""
    numerator = 0.0
    denominator = 0.0
    coverage = 0.0
    for pillar, (score, cov) in pillar_data.items():
        weight = weights[pillar]
        coverage += weight * cov
        if cov > 0 and score is not None:
            numerator += weight * score
            denominator += weight
    composite_raw = numerator / denominator if denominator > 0 else None
    if composite_raw is None or coverage < 0.60:
        return None, coverage, None, False

    tier_cutoffs = ((90.0, "S+"), (80.0, "S"), (70.0, "A"), (55.0, "B"), (40.0, "C"))
    tier_by_score = "D"
    for cutoff, label in tier_cutoffs:
        if composite_raw >= cutoff:
            tier_by_score = label
            break
    if coverage >= 0.95:
        ceiling = None
    elif coverage >= 0.80:
        ceiling = "A"
    else:
        ceiling = "B"
    tier_order = ("D", "C", "B", "A", "S", "S+")
    tier = tier_by_score if ceiling is None else min(tier_by_score, ceiling, key=tier_order.index)
    return round(composite_raw, 2), coverage, tier, True


def test_reproject_parity_with_inline_oracle_over_300_rows_and_20_weight_vectors(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    rng = random.Random(2026)

    fixtures = []
    for i in range(300):
        pillar_data: Dict[str, Tuple[Any, float]] = {}
        for pillar in PILLARS:
            if rng.random() < 0.15:
                pillar_data[pillar] = (None, 0.0)
            else:
                pillar_data[pillar] = (round(rng.uniform(20, 99), 1), round(rng.uniform(0.3, 1.0), 2))
        pillars = {name: PillarScore(score=s, coverage=c) for name, (s, c) in pillar_data.items()}
        friction = round(rng.uniform(0, 100), 1) if rng.random() > 0.1 else None
        leverage = round(rng.uniform(0, 100), 1) if rng.random() > 0.1 else None
        rec = make_rated_record(
            "snap-1", owner="owner", name=f"repo{i:03d}", raw_hash=f"{i:064x}",
            composite=50.0, coverage=0.5,  # balanced values are irrelevant to reproject
            friction=friction, leverage=leverage,
        )
        rec = dc_replace(rec, pillars=pillars)
        ttfhr = round(rng.uniform(1, 300), 1) if rng.random() > 0.1 else None
        commit = f"2026-{1 + i % 9:02d}-01T00:00:00Z" if rng.random() > 0.1 else None
        fixtures.append((rec, pillar_data, ttfhr, commit))

    write_records(conn, "snap-1", [make_repo_write(rec) for rec, _, _, _ in fixtures])
    for rec, _, ttfhr, commit in fixtures:
        _stamp_tail_columns(conn, "snap-1", rec.repo.name, ttfhr, commit)
    conn.commit()

    for _trial in range(20):
        raw_weights = {p: rng.random() + 0.01 for p in PILLARS}
        total_w = sum(raw_weights.values())
        weights = {p: w / total_w for p, w in raw_weights.items()}

        oracle_entries = []
        for rec, pillar_data, ttfhr, commit in fixtures:
            composite, coverage, tier, rated = _reproject_oracle(pillar_data, weights)
            if not rated:
                continue
            oracle_record = dc_replace(rec, composite=composite, coverage=coverage, tier=tier)
            oracle_entries.append(
                TieBreakInput(oracle_record, rec.repo.owner, rec.repo.name, ttfhr_p75_hours=ttfhr, last_non_bot_commit_at=commit)
            )
        oracle_ranked = assign_ranks(oracle_entries)
        oracle_order = [(r.repo.owner, r.repo.name, rank, r.tier) for r, rank, _tg in oracle_ranked]
        oracle_membership = _group_membership(
            [((r.repo.owner, r.repo.name), tg) for r, _rank, tg in oracle_ranked]
        )

        sql_rows, sql_total = queries.reproject(conn, "snap-1", weights, limit=1000, offset=0)
        assert sql_total == len(oracle_order)
        sql_order = [(row["owner"], row["name"], row["rank"], row["tier"]) for row in sql_rows]
        assert sql_order == oracle_order
        # tie_group compared by MEMBERSHIP, not raw id: DENSE_RANK() numbers
        # every distinct composite it visits while assign_ranks' counter
        # only advances for composites that actually recur, so the two
        # schemes agree on WHICH rows share a group, never on its label.
        sql_membership = _group_membership([((row["owner"], row["name"]), row["tie_group"]) for row in sql_rows])
        assert sql_membership == oracle_membership

        sql_cvi = {(row["owner"], row["name"]): row["cvi"] for row in sql_rows}
        for entry in oracle_entries:
            assert sql_cvi[(entry.owner, entry.name)] == round(entry.record.composite, 2)


# =========================================================================== #
# Tie-break SQL parity: balanced + every preset persona, one 200-row
# NULL-bearing snapshot, plus a negative control.
# =========================================================================== #

def _build_null_bearing_dataset(rng: random.Random) -> list[dict]:
    """200 rows. Rows 0-2 share IDENTICAL pillar scores (so their composite
    ties under ANY weight vector -- persona-invariant by construction); row
    3 has a distinctive profile far from any other (a persona-invariant
    singleton). Rows 4-5 ALSO share identical pillar scores (a second,
    persona-invariant 2-way composite tie) but differ in ``friction`` (one
    ``None``) -- forces the sort to actually REACH the friction ASC level
    for a real tie (composite comparisons among the other 194 rows use
    near-continuous random floats, so friction's NULL ordering would
    otherwise never be exercised: `04` section 6's ``NULLS LAST`` fix only
    matters once two rows already tie through the levels ABOVE it). The
    remaining 194 rows get random pillar scores and NULLs at three
    persona/weight-invariant tie levels (friction, leverage, ttfhr_p75)
    plus the commit-date level, on a subset of rows."""
    rows: list[dict] = []
    tie_pillars = {"responsiveness": 72.4, "criticality": 65.0, "governance": 80.0, "rigor": 55.0, "accessibility": 90.0}
    for i in range(3):
        rows.append(dict(name=f"tie{i}", pillars=dict(tie_pillars), friction=30.0, leverage=60.0, ttfhr_p75=20.0, last_commit="2026-01-01T00:00:00Z"))
    rows.append(dict(
        name="single0",
        pillars={"responsiveness": 12.3, "criticality": 88.8, "governance": 44.4, "rigor": 91.1, "accessibility": 3.3},
        friction=10.0, leverage=95.0, ttfhr_p75=5.0, last_commit="2026-06-01T00:00:00Z",
    ))
    friction_null_pillars = {"responsiveness": 40.5, "criticality": 40.5, "governance": 40.5, "rigor": 40.5, "accessibility": 40.5}
    rows.append(dict(name="fric-measured", pillars=dict(friction_null_pillars), friction=15.0, leverage=60.0, ttfhr_p75=20.0, last_commit="2026-01-01T00:00:00Z"))
    rows.append(dict(name="fric-null", pillars=dict(friction_null_pillars), friction=None, leverage=60.0, ttfhr_p75=20.0, last_commit="2026-01-01T00:00:00Z"))
    for i in range(194):
        rows.append(dict(
            name=f"row{i:03d}",
            pillars={p: round(rng.uniform(10, 99), 1) for p in PILLARS},
            friction=None if i % 11 == 0 else round(rng.uniform(0, 100), 1),
            leverage=None if i % 13 == 0 else round(rng.uniform(0, 100), 1),
            ttfhr_p75=None if i % 17 == 0 else round(rng.uniform(1, 300), 1),
            last_commit=None if i % 19 == 0 else f"2026-{1 + i % 9:02d}-01T00:00:00Z",
        ))
    return rows


def test_sql_tie_break_parity_across_balanced_and_every_persona_200_rows_with_nulls(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    rng = random.Random(99)
    dataset = _build_null_bearing_dataset(rng)
    assert len(dataset) == 200

    records = []
    for row in dataset:
        composite = round(sum(BALANCED_WEIGHTS[p] * row["pillars"][p] for p in PILLARS), 2)
        rec = make_rated_record(
            "snap-1", owner="acme", name=row["name"],
            raw_hash=hashlib.sha256(row["name"].encode()).hexdigest(),
            composite=composite, coverage=0.9, pillar_scores=row["pillars"],
            friction=row["friction"], leverage=row["leverage"],
        )
        rec = _with_persona_composites(rec)
        records.append((rec, row))

    # Bake the TRUE balanced rank/tie_group onto each record before writing
    # (`cvi.core.tiebreak.assign_ranks`), exactly as a real pipeline would --
    # `list_ranked(persona="balanced")` reads these stored columns verbatim
    # and never recomputes them (the packet's own instruction), so the
    # balanced-lens comparison below is only meaningful if the STORED
    # columns already carry the true order.
    balanced_entries = [
        TieBreakInput(rec, rec.repo.owner, rec.repo.name, ttfhr_p75_hours=row["ttfhr_p75"], last_non_bot_commit_at=row["last_commit"])
        for rec, row in records
    ]
    balanced_ranked = assign_ranks(balanced_entries)
    ranked_by_name = {r.repo.name: (r, rank, tg) for r, rank, tg in balanced_ranked}
    records = [(dc_replace(rec, rank=ranked_by_name[rec.repo.name][1], tie_group=ranked_by_name[rec.repo.name][2]), row) for rec, row in records]

    write_records(conn, "snap-1", [make_repo_write(rec) for rec, _ in records])
    for _rec, row in records:
        _stamp_tail_columns(conn, "snap-1", row["name"], row["ttfhr_p75"], row["last_commit"])
    conn.commit()

    for lens in PERSONA_PRESETS:
        entries = []
        for rec, row in records:
            if lens == "balanced":
                composite, coverage = rec.composite, rec.coverage
            else:
                projected = rec.persona_composites[lens]
                composite, coverage = projected.cvi, projected.coverage
            lens_record = dc_replace(rec, composite=composite, coverage=coverage)
            entries.append(TieBreakInput(
                lens_record, rec.repo.owner, rec.repo.name,
                ttfhr_p75_hours=row["ttfhr_p75"], last_non_bot_commit_at=row["last_commit"],
            ))
        # Every row here has coverage 0.9 under every weight vector (uniform
        # per-pillar coverage 0.9 x weights summing to 1), so nothing is
        # persona-unrated -- this fixture isolates ORDERING, not exclusion
        # (exclusion has its own tests above).
        python_ranked = assign_ranks(entries)
        python_order = [(r.repo.owner, r.repo.name, rank) for r, rank, _tg in python_ranked]
        python_membership = _group_membership([((r.repo.owner, r.repo.name), tg) for r, _rank, tg in python_ranked])

        sql_rows, sql_total = queries.list_ranked(conn, "snap-1", persona=lens, limit=250, offset=0)
        if lens == "balanced":
            sql_order = [(row["owner"], row["name"], row["rank"]) for row in sql_rows]
            sql_membership = _group_membership([((row["owner"], row["name"]), row["tie_group"]) for row in sql_rows])
        else:
            sql_order = [(row["owner"], row["name"], row["rank_p"]) for row in sql_rows]
            sql_membership = _group_membership([((row["owner"], row["name"]), row["tie_group_p"]) for row in sql_rows])
        assert sql_total == len(python_order) == 200, lens
        assert sql_order == python_order, lens
        # tie_group compared by MEMBERSHIP: the balanced branch's stored
        # `tie_group` was assigned by assign_ranks' own sequential counter
        # (baked in above) while every persona branch's `tie_group_p` is
        # DENSE_RANK-based (`_tie_group_sql`) -- the two label the same
        # groups differently but must agree on WHICH rows share one.
        assert sql_membership == python_membership, lens

    # Concrete, non-vacuous checks (R-1): the 3-way tie shares one
    # tie_group id; the singleton's is NULL (R-D).
    balanced_rows, _ = queries.list_ranked(conn, "snap-1", limit=250)
    by_name = {row["name"]: row for row in balanced_rows}
    tie_ids = {by_name[f"tie{i}"]["tie_group"] for i in range(3)}
    assert len(tie_ids) == 1 and None not in tie_ids
    assert by_name["single0"]["tie_group"] is None

    # Negative control: the pre-ruling clause (one NULLS LAST dropped, at
    # the friction ASC level) gives a DIFFERENT order on this same fixture.
    naive_sql = """
        SELECT owner, name FROM repositories WHERE snapshot_id = 'snap-1' AND rated = 1
        ORDER BY ROUND(cvi, 2) DESC NULLS LAST, coverage DESC NULLS LAST,
                 p_responsiveness DESC NULLS LAST, friction ASC,
                 leverage DESC NULLS LAST, ttfhr_p75 ASC NULLS LAST,
                 last_human_commit_at DESC NULLS LAST, lower(owner || '/' || name) ASC
    """
    naive_order = [(row[0], row[1]) for row in conn.execute(naive_sql).fetchall()]
    balanced_python_order = [
        (r.repo.owner, r.repo.name)
        for r, _rank, _tg in assign_ranks([
            TieBreakInput(rec, rec.repo.owner, rec.repo.name, ttfhr_p75_hours=row["ttfhr_p75"], last_non_bot_commit_at=row["last_commit"])
            for rec, row in records
        ])
    ]
    assert naive_order != balanced_python_order


# =========================================================================== #
# Indexes / EXPLAIN QUERY PLAN
# =========================================================================== #

def test_explain_query_plan_uses_idx_eligible_for_persona_and_reproject_base_filter(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    clause, params = queries._persona_visible_where_sql("career_capital")
    plan = conn.execute(
        f"EXPLAIN QUERY PLAN SELECT COUNT(*) FROM repositories r WHERE r.snapshot_id = ? AND {clause}",
        ["snap-1", *params],
    ).fetchall()
    plan_text = " ".join(str(row[-1]) for row in plan)
    assert "idx_eligible" in plan_text

    reproject_plan = conn.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM repositories r WHERE r.snapshot_id = ? AND r.eligible = 1", ["snap-1"]
    ).fetchall()
    reproject_plan_text = " ".join(str(row[-1]) for row in reproject_plan)
    assert "idx_eligible" in reproject_plan_text


# =========================================================================== #
# Performance: 10,000-row synthetic snapshot, p95 over 20 calls.
# =========================================================================== #

def _p95(samples: list) -> float:
    ordered = sorted(samples)
    index = max(0, int(len(ordered) * 0.95) - 1)
    return ordered[index]


def test_performance_list_ranked_and_reproject_p95_under_100ms_for_10k_rows(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    rng = random.Random(12345)
    items = []
    for i in range(10_000):
        composite = round(rng.uniform(40, 99), 2)
        coverage = round(rng.uniform(0.6, 1.0), 3)
        scores = {p: round(rng.uniform(20, 99), 1) for p in PILLARS}
        rec = make_rated_record(
            "snap-1", owner=f"owner{i}", name=f"repo{i}", ecosystem=rng.choice(["python", "js", "go"]),
            raw_hash=f"{i:064x}", composite=composite, coverage=coverage, pillar_scores=scores,
            friction=round(rng.uniform(0, 100), 1), leverage=round(rng.uniform(0, 100), 1),
        )
        rec = _with_persona_composites(rec)
        items.append(make_repo_write(rec))
    write_records(conn, "snap-1", items)
    conn.commit()

    list_ranked_times = []
    for _ in range(20):
        t0 = time.perf_counter()
        queries.list_ranked(conn, "snap-1", limit=50, offset=0)
        list_ranked_times.append((time.perf_counter() - t0) * 1000)

    weights = {"responsiveness": 0.3, "criticality": 0.3, "governance": 0.15, "rigor": 0.15, "accessibility": 0.1}
    reproject_times = []
    for _ in range(20):
        t0 = time.perf_counter()
        queries.reproject(conn, "snap-1", weights, limit=50, offset=0)
        reproject_times.append((time.perf_counter() - t0) * 1000)

    list_ranked_p95 = _p95(list_ranked_times)
    reproject_p95 = _p95(reproject_times)
    print(f"\n[W4-3 perf] list_ranked p95 over 20 calls, 10,000 rows: {list_ranked_p95:.2f} ms")
    print(f"[W4-3 perf] reproject p95 over 20 calls, 10,000 rows: {reproject_p95:.2f} ms")
    assert list_ranked_p95 < 100.0, f"list_ranked p95 {list_ranked_p95:.2f}ms exceeds the 100ms budget"
    assert reproject_p95 < 100.0, f"reproject p95 {reproject_p95:.2f}ms exceeds the 100ms budget"


def test_performance_list_ranked_persona_p95_under_100ms_for_10k_rows(tmp_path) -> None:
    """The persona branch is the one that cannot use an index for its own
    JSON-path predicates (documented in ``0002_indexes.sql``) -- measured
    separately from the balanced path above since it is the slower one."""
    conn = _fresh_db(tmp_path / "db.sqlite")
    rng = random.Random(54321)
    items = []
    for i in range(10_000):
        composite = round(rng.uniform(40, 99), 2)
        coverage = round(rng.uniform(0.6, 1.0), 3)
        scores = {p: round(rng.uniform(20, 99), 1) for p in PILLARS}
        rec = make_rated_record(
            "snap-1", owner=f"owner{i}", name=f"repo{i}", ecosystem=rng.choice(["python", "js", "go"]),
            raw_hash=f"{i:064x}", composite=composite, coverage=coverage, pillar_scores=scores,
            friction=round(rng.uniform(0, 100), 1), leverage=round(rng.uniform(0, 100), 1),
        )
        rec = _with_persona_composites(rec)
        items.append(make_repo_write(rec))
    write_records(conn, "snap-1", items)
    conn.commit()

    times = []
    for _ in range(20):
        t0 = time.perf_counter()
        queries.list_ranked(conn, "snap-1", persona="career_capital", limit=50, offset=0)
        times.append((time.perf_counter() - t0) * 1000)
    p95 = _p95(times)
    print(f"\n[W4-3 perf] list_ranked(persona) p95 over 20 calls, 10,000 rows: {p95:.2f} ms")
    assert p95 < 100.0, f"list_ranked(persona) p95 {p95:.2f}ms exceeds the 100ms budget"
