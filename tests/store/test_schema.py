"""Database schema and DDL tests for ``porchlamp.store``."""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

from porchlamp.store import schema

STORE_DIR = Path(__file__).resolve().parents[2] / "porchlamp" / "store"


def test_create_schema_creates_exactly_the_five_tables() -> None:
    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    assert tables == {"schema_migrations", "snapshots", "repositories", "params", "populations"}


def test_repositories_row_contract_columns_present() -> None:
    """Verify repositories table columns are present: ``persona_composites``,
    ``leverage_coverage``, ``friction_coverage``, and ``gate_undecided``."""
    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    columns = {row[1] for row in conn.execute('PRAGMA table_info("repositories")').fetchall()}
    for expected in (
        "persona_composites", "leverage_coverage", "friction_coverage",
        "stars_audited", "tier_ceiling", "unrated_reason", "persona_gates", "suspicion",
        "gate_undecided",
    ):
        assert expected in columns, f"repositories missing {expected!r}"


def test_populations_row_contract_matches_build_populations_table() -> None:
    """Verify ``populations`` table columns match
    ``porchlamp.snapshot.populations.build_populations_table`` output columns."""
    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    columns = [row[1] for row in conn.execute('PRAGMA table_info("populations")').fetchall()]
    assert set(columns) == {"snapshot_id", "ecosystem", "param_id", "n", "fallback", "values_json", "values_hash"}


def test_snapshots_is_a_superset_of_snapshot_meta() -> None:
    """Verify ``snapshots`` table contains metadata columns:
    ``as_of, benchmark_version, constants_hash, manifest_root,
    canonical_dump_hash, promoted_at``, with ``id`` as PK."""
    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    columns = {row[1] for row in conn.execute('PRAGMA table_info("snapshots")').fetchall()}
    snapshot_meta_columns = {
        "as_of", "benchmark_version", "constants_hash", "manifest_root", "canonical_dump_hash", "promoted_at",
    }
    assert snapshot_meta_columns <= columns
    assert "id" in columns  # plays snapshot_meta.snapshot_id's PK role


def test_indexes_match_expected() -> None:
    """Verify required indexes are present on the schema, including
    ``idx_eligible`` for persona projection queries."""
    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    assert names == {"idx_rank", "idx_eco", "idx_name", "idx_eligible"}


def test_introspect_schema_catches_a_missing_column() -> None:
    """Over-correction guard for :func:`schema.introspect_schema` itself:
    a schema missing one column must NOT compare equal to the real one."""
    good = sqlite3.connect(":memory:")
    schema.create_schema(good)
    bad = sqlite3.connect(":memory:")
    bad.execute("CREATE TABLE repositories (id INTEGER PRIMARY KEY, owner TEXT)")
    assert schema.introspect_schema(good) != schema.introspect_schema(bad)


def test_introspect_schema_is_insensitive_to_index_creation_order() -> None:
    """Positive control for the parity test's normalisation: the three
    ``CREATE INDEX`` statements have no ordering dependency on each other
    (unlike the ``CREATE TABLE`` statements, which must respect their
    ``REFERENCES`` order) -- built in reverse, they still introspect equal
    (index identity by column-set, not by the name SQLite happens to
    assign in creation order)."""
    table_statements = [s for s in schema.SCHEMA_STATEMENTS if "CREATE TABLE" in s]
    index_statements = [s for s in schema.SCHEMA_STATEMENTS if "CREATE INDEX" in s]
    assert len(table_statements) + len(index_statements) == len(schema.SCHEMA_STATEMENTS)

    forward = sqlite3.connect(":memory:")
    schema.create_schema(forward)

    reordered_conn = sqlite3.connect(":memory:")
    for statement in table_statements:
        reordered_conn.execute(statement)
    for statement in reversed(index_statements):
        reordered_conn.execute(statement)

    assert schema.introspect_schema(forward) == schema.introspect_schema(reordered_conn)


def _non_docstring_strings(py_path: Path) -> list[str]:
    """Every string literal in ``py_path`` that is NOT a module/function/class
    docstring -- so a docstring that explains "no RETURNING clause" in prose
    is not itself a false positive for the check below."""
    tree = ast.parse(py_path.read_text(encoding="utf-8"))
    docstring_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstring_nodes.add(id(body[0].value))
    strings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstring_nodes:
            strings.append(node.value)
    return strings


def _iter_source_files(root: Path):
    yield from root.rglob("*.py")
    yield from root.rglob("*.sql")


def _returning_offenders(root: Path) -> list[str]:
    """`04` section 5's "no RETURNING clause" rule, checked over actual SQL
    text: ``.sql`` files verbatim, and every non-docstring string literal in
    ``.py`` files (so the SQL constants in ``writer.py`` are covered without
    a docstring explaining the rule in English tripping the same check)."""
    offenders = []
    for path in _iter_source_files(root):
        if path.suffix == ".sql":
            haystacks = [path.read_text(encoding="utf-8")]
        else:
            haystacks = _non_docstring_strings(path)
        if any("RETURNING" in text.upper() for text in haystacks):
            offenders.append(str(path))
    return offenders


def test_no_returning_clause_anywhere_in_store() -> None:
    """`04` section 5: "``RETURNING`` is avoided so SQLite >= 3.35 is not
    required." A grep-style test over real SQL text, with a positive-control
    companion below proving the check actually catches a violation (R-1:
    assert values, not shapes -- the check's own behaviour is asserted, not
    merely run)."""
    assert _returning_offenders(STORE_DIR) == []


def test_no_returning_check_catches_a_planted_violation(tmp_path) -> None:
    (tmp_path / "planted.sql").write_text("INSERT INTO x (a) VALUES (1) RETURNING id;\n", encoding="utf-8")
    assert _returning_offenders(tmp_path) != []
