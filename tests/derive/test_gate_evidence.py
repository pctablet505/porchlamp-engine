"""``porchlamp.derive.gate_evidence._vitality_commit_count`` (D-GATE-4) reads a
commits envelope's rows in either recorded shape -- the bare row list every
``commits_180d`` envelope holds today, or the window dict
``collect_commits_365d`` records -- through the same
``porchlamp.core.commit_domains.envelope_rows`` every other commits reader uses."""
from __future__ import annotations

from pathlib import Path

from _fixtures import KEY, write_envelope

from porchlamp.derive.gate_evidence import _vitality_commit_count

ROWS = [{"author_login": "alice", "date": "2026-08-01T00:00:00Z"},
        {"author_login": "bob", "date": "2026-08-02T00:00:00Z"},
        {"author_login": "dependabot[bot]", "date": "2026-08-03T00:00:00Z"}]


def test_vitality_count_from_a_row_list(tmp_path: Path) -> None:
    write_envelope(tmp_path, "commits_180d", KEY, ROWS)
    assert _vitality_commit_count(tmp_path, KEY) == 2  # the bot is not counted


def test_vitality_count_from_a_window_dict(tmp_path: Path) -> None:
    write_envelope(tmp_path, "commits_180d", KEY,
                   {"count": 3, "cap": 1000, "truncated": False, "rows": ROWS})
    assert _vitality_commit_count(tmp_path, KEY) == 2


def test_vitality_count_absent_or_rowless_envelope_is_undecided(tmp_path: Path) -> None:
    assert _vitality_commit_count(tmp_path, KEY) is None
    write_envelope(tmp_path, "commits_180d", KEY, {"count": 3})
    assert _vitality_commit_count(tmp_path, KEY) is None
