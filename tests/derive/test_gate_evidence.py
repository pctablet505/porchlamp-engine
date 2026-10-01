"""``porchlamp.derive.gate_evidence._vitality_commit_count`` (D-GATE-4) reads a
commits envelope's rows in either recorded shape -- the bare row list every
``commits_180d`` envelope holds today, or the window dict
``collect_commits_365d`` records -- through the same
``porchlamp.core.commit_domains.envelope_rows`` every other commits reader uses."""
from __future__ import annotations

from pathlib import Path

from _fixtures import KEY, write_envelope

from porchlamp.derive.gate_evidence import _vitality_commit_count, gate_evidence_for

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


def test_vitality_count_filters_bot_by_email_and_actor_type_without_login(tmp_path: Path) -> None:
    """Regression: bot commits without author_login are filtered via author_email / author_type,
    while human commits with only author_email are still counted."""
    rows = [
        {"author_login": None, "author_email": "49699333+dependabot[bot]@users.noreply.github.com"},
        {"author_login": "", "author_email": "bot@example.com", "author_type": "Bot"},
        {"author_login": None, "author_email": "human@example.com", "author_type": "User"},
        {"author_login": None, "author_email": None, "author_type": None},
    ]
    write_envelope(tmp_path, "commits_180d", KEY, rows)
    assert _vitality_commit_count(tmp_path, KEY) == 1


def test_gate_evidence_for_tree_absent_corrupt_truncated_and_empty(tmp_path: Path) -> None:
    """Regression: gate_evidence_for distinguishes absent/corrupt tree (None),
    truncated tree without match (None), truncated tree with match (True),
    and complete empty/non-matching tree (False)."""
    # 1. Absent tree file -> None
    ev_absent = gate_evidence_for(tmp_path, KEY)
    assert ev_absent.manifest_found is None
    assert ev_absent.license_file_present is None
    assert ev_absent.off_platform_marker is None

    # 2. Corrupt JSON tree file -> None
    tree_path = tmp_path / "tree" / f"{KEY}.json"
    tree_path.parent.mkdir(parents=True, exist_ok=True)
    tree_path.write_text("{corrupt json", encoding="utf-8")
    ev_corrupt = gate_evidence_for(tmp_path, KEY)
    assert ev_corrupt.manifest_found is None
    assert ev_corrupt.license_file_present is None
    assert ev_corrupt.off_platform_marker is None

    # 3. Complete empty tree (paths=[], truncated=False) -> False (not None)
    write_envelope(tmp_path, "tree", KEY, {"paths": [], "truncated": False})
    ev_empty = gate_evidence_for(tmp_path, KEY)
    assert ev_empty.manifest_found is False
    assert ev_empty.license_file_present is False
    assert ev_empty.off_platform_marker is False

    # 4. Truncated tree without matches -> None (undecided)
    write_envelope(tmp_path, "tree", KEY, {"paths": ["docs/README.md"], "truncated": True})
    ev_trunc_no_match = gate_evidence_for(tmp_path, KEY)
    assert ev_trunc_no_match.manifest_found is None
    assert ev_trunc_no_match.license_file_present is None
    assert ev_trunc_no_match.off_platform_marker is None

    # 5. Truncated tree with a recognized manifest -> True for manifest_found, None for others
    write_envelope(tmp_path, "tree", KEY, {"paths": ["pyproject.toml"], "truncated": True})
    ev_trunc_match = gate_evidence_for(tmp_path, KEY)
    assert ev_trunc_match.manifest_found is True
    assert ev_trunc_match.license_file_present is None
    assert ev_trunc_match.off_platform_marker is None

