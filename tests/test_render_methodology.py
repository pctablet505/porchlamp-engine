"""Tests for `scripts/render_methodology.py` and the
committed `docs/METHODOLOGY-v2.md` it produces.

The load-by-path pattern mirrors `tests/test_promote.py`: `scripts/` is not
a package, so its modules are loaded by file location rather than import.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "render_methodology", ROOT / "scripts" / "render_methodology.py"
)
assert _spec is not None and _spec.loader is not None
render_methodology = importlib.util.module_from_spec(_spec)
sys.modules["render_methodology"] = render_methodology
_spec.loader.exec_module(render_methodology)

CHANGELOG = ROOT / "docs" / "v2" / "rfcs" / "CHANGELOG.md"
COMMITTED = ROOT / "docs" / "METHODOLOGY-v2.md"


def test_committed_methodology_doc_equals_a_fresh_render() -> None:
    """`docs/METHODOLOGY-v2.md` must equal `render_markdown(document)` byte
    for byte, so it cannot silently drift from the frozen constants it
    documents. If this fails, run:
    `PYTHONPATH=$PWD .venv/bin/python scripts/render_methodology.py`
    and commit the result."""
    assert COMMITTED.exists(), f"{COMMITTED} is missing; run scripts/render_methodology.py"
    document = render_methodology.methodology_with_history(CHANGELOG)
    fresh = render_methodology.render_markdown(document)
    committed = COMMITTED.read_text(encoding="utf-8")
    assert committed == fresh


def test_render_is_deterministic() -> None:
    document = render_methodology.methodology_with_history(CHANGELOG)
    assert render_methodology.render_markdown(document) == render_methodology.render_markdown(
        document
    )


def test_render_contains_the_reproduce_command_template() -> None:
    document = render_methodology.methodology_with_history(CHANGELOG)
    rendered = render_methodology.render_markdown(document)
    assert render_methodology.REPRODUCE_COMMAND_TEMPLATE in rendered
    assert "porchlamp reproduce <snapshot dir>" in rendered


def test_render_links_the_rfc_procedure() -> None:
    document = render_methodology.methodology_with_history(CHANGELOG)
    rendered = render_methodology.render_markdown(document)
    assert "v2/rfcs/README.md" in rendered


def test_render_covers_every_pillar_and_parameter() -> None:
    from porchlamp.core import constants

    document = render_methodology.methodology_with_history(CHANGELOG)
    rendered = render_methodology.render_markdown(document)
    for name in constants.PILLAR_NAMES:
        assert name in rendered
    for input_id in constants.PARAM_INPUT_IDS:
        assert f"`{input_id}`" in rendered


def test_check_mode_passes_against_the_committed_file(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = render_methodology.main(
        ["--changelog", str(CHANGELOG), "--out", str(COMMITTED), "--check"]
    )
    assert exit_code == 0


def test_check_mode_fails_against_a_stale_file(tmp_path: Path) -> None:
    stale = tmp_path / "METHODOLOGY-v2.md"
    stale.write_text("stale content", encoding="utf-8")
    exit_code = render_methodology.main(
        ["--changelog", str(CHANGELOG), "--out", str(stale), "--check"]
    )
    assert exit_code == 1


def test_main_writes_the_file(tmp_path: Path) -> None:
    out = tmp_path / "METHODOLOGY-v2.md"
    exit_code = render_methodology.main(["--changelog", str(CHANGELOG), "--out", str(out)])
    assert exit_code == 0
    assert out.exists()
    assert out.read_text(encoding="utf-8") == COMMITTED.read_text(encoding="utf-8")


def test_cell_escapes_pipes_and_newlines() -> None:
    assert render_methodology._cell("a | b\nc") == "a \\| b c"
