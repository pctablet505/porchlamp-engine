"""Unit tests for :mod:`porchlamp.audit.forward`.

All data here is a synthetic fixture built in-memory or under ``tmp_path``:
no repository, no PR and no point cost in this file is real telemetry. The
oracle test asserts exact Spearman values on a synthetic snapshot pair
(now + 90 d) whose label rates are engineered to be strictly ordered against
the composite, so the known correlation is 1.0 / -1.0.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from porchlamp.audit.forward import (
    ForwardError,
    PILLAR_COLUMNS,
    COMPOSITE_COLUMN,
    DECISION_WINDOW_DAYS,
    MERGE_WINDOW_DAYS,
    PRRecord,
    ScoreRow,
    analyze,
    first_timers_from_s0,
    filter_eligible,
    load_pr_envelopes,
    load_scores,
    main,
    pr_labels,
    repo_label_rates,
    resolve_snapshot,
    roc_auc,
    spearman_rho,
)
from porchlamp.collect.envelope import read_envelope, write_envelope

T0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
FETCHED_AT = "2026-09-29T12:00:00Z"  # T0 + 90 d, the pre-registered collect time


def _pr(
    repo: str,
    number: int,
    created: datetime,
    author: str = "alice",
    association: str = "NONE",
    closed: datetime | None = None,
    merged: datetime | None = None,
) -> PRRecord:
    return PRRecord(
        repo=repo,
        number=number,
        author=author,
        association=association,
        created_at=created,
        closed_at=closed,
        merged_at=merged,
    )


# ---------------------------------------------------------------- oracle -- #


def test_spearman_strictly_monotone_is_exactly_one() -> None:
    x = [100.0 - 10.0 * i for i in range(8)]
    y = [80.0 - 10.0 * i for i in range(8)]
    assert spearman_rho(x, y) == 1.0


def test_spearman_strictly_anti_monotone_is_exactly_minus_one() -> None:
    x = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    assert spearman_rho(x, list(reversed(x))) == -1.0


def test_spearman_known_permutation_is_exactly_minus_half() -> None:
    # ranks [1,2,3] vs [2,3,1]: d^2 = 1 + 1 + 4 = 6, rho = 1 - 6*6/(3*8) = -1/2
    assert spearman_rho([1.0, 2.0, 3.0], [2.0, 3.0, 1.0]) == -0.5


def test_spearman_ties_use_mean_ranks() -> None:
    x = [1.0, 1.0, 2.0, 3.0]
    y = [2.0, 1.0, 4.0, 3.0]
    expected = statistics.correlation([1.5, 1.5, 3.0, 4.0], [2.0, 1.0, 4.0, 3.0])
    assert spearman_rho(x, y) == pytest.approx(expected, abs=1e-12)
    # mean-deviation form: 3.5 / sqrt(4.5 * 5.0) = 14 / sqrt(360)
    assert spearman_rho(x, y) == pytest.approx(0.7378647873726218, abs=1e-9)


def test_spearman_undefined_cases() -> None:
    assert spearman_rho([5.0, 5.0, 5.0], [1.0, 2.0, 3.0]) is None
    assert spearman_rho([1.0], [2.0]) is None
    assert spearman_rho([1.0, 2.0], [1.0]) is None


def test_auc_known_values() -> None:
    assert roc_auc([1.0, 2.0, 3.0, 4.0], [0, 0, 1, 1]) == 1.0
    assert roc_auc([4.0, 3.0, 2.0, 1.0], [0, 0, 1, 1]) == 0.0
    assert roc_auc([2.0, 2.0, 2.0, 2.0], [0, 0, 1, 1]) == 0.5
    assert roc_auc([1.0, 1.0, 2.0, 2.0], [0, 1, 0, 1]) == 0.5
    assert roc_auc([1.0, 2.0], [1, 1]) is None
    assert roc_auc([], []) is None
    with pytest.raises(ForwardError, match="equal length"):
        roc_auc([1.0, 2.0], [1])


def test_pr_label_boundaries() -> None:
    created = T0 + timedelta(days=5)
    decided_exact = _pr("org/a", 1, created, closed=created + timedelta(days=30))
    decided_late = _pr("org/a", 2, created, closed=created + timedelta(days=30, seconds=1))
    merged_at_horizon = _pr(
        "org/a", 3, T0 + timedelta(days=60), closed=T0 + timedelta(days=90), merged=T0 + timedelta(days=90)
    )
    merged_after_horizon = _pr(
        "org/a", 4, T0 + timedelta(days=59), closed=T0 + timedelta(days=91), merged=T0 + timedelta(days=91)
    )
    open_pr = _pr("org/a", 5, created)
    assert pr_labels(decided_exact, T0) == (True, False)
    assert pr_labels(decided_late, T0) == (False, False)
    assert pr_labels(merged_at_horizon, T0) == (True, True)
    assert pr_labels(merged_after_horizon, T0) == (False, False)
    assert pr_labels(open_pr, T0) == (False, False)


def test_filter_eligible_counts_by_reason() -> None:
    first_timers = {"org/a": frozenset({"bob", "dave"})}
    kept = []
    for association, author in [
        ("NONE", "bob"),
        ("CONTRIBUTOR", "bob"),
        ("OWNER", "bob"),
        ("MEMBER", "bob"),
        ("COLLABORATOR", "bob"),
        ("APP", "bob"),
        ("NONE", "carol"),
        ("NONE", "dave"),
    ]:
        kept.append(_pr("org/a", 1, T0, author=author, association=association))
    eligible, excluded = filter_eligible(kept, first_timers)
    assert [pr.author for pr in eligible] == ["bob", "bob", "dave"]
    assert excluded == {"internal": 4, "not_first_timer": 1}


def test_repo_label_rates_and_exclusions() -> None:
    prs = [
        _pr("org/a", 1, T0 + timedelta(days=1), closed=T0 + timedelta(days=2)),
        _pr("org/a", 2, T0 + timedelta(days=2)),
    ]
    rates = repo_label_rates(prs, T0)
    assert rates["org/a"].n_prs == 2
    assert rates["org/a"].decision_rate == 0.5
    assert rates["org/a"].merge_rate == 0.0
    assert repo_label_rates([], T0) == {}


# ------------------------------------------------------------ synthetic -- #


def _synthetic_universe() -> tuple[list[ScoreRow], list[PRRecord], dict[str, frozenset[str]]]:
    """Synthetic snapshot pair (fixture, test-only): S0 at T0 and its PRs.

    Composite is strictly descending repo r0..r7; responsiveness strictly
    ascending. Per repo, 16 PRs: merged count 15 - 2*i and decided count
    16 - 2*i, both strictly increasing with the composite rank, so
    Spearman(composite, merge_rate) and Spearman(composite, decision_rate)
    are exactly 1.0, while the anti-ranked responsiveness pillar gives
    exactly -1.0 against the decision label.
    """
    scores: list[ScoreRow] = []
    for i in range(8):
        scores.append(
            ScoreRow(
                repo=f"org/r{i}",
                rated=True,
                scores={
                    "score_responsiveness": 10.0 + 10.0 * i,
                    "score_criticality": 50.0,
                    "score_governance": 100.0 - 5.0 * i,
                    "score_rigor_ai": 5.0 * i,
                    "score_accessibility": 50.0 - 3.0 * i,
                    "overall_porchlamp": 100.0 - 10.0 * i,
                },
            )
        )
    prs: list[PRRecord] = []
    first_timers: dict[str, frozenset[str]] = {}
    for i in range(8):
        repo = f"org/r{i}"
        merged_count = 15 - 2 * i
        decided_count = 16 - 2 * i
        first_timers[repo] = frozenset({f"author-{i}"})
        for j in range(16):
            created = T0 + timedelta(days=2 + j)
            merged = j < merged_count
            decided = j < decided_count
            closed = None
            merged_at = None
            if merged:
                merged_at = created + timedelta(days=2)
                closed = merged_at
            elif decided:
                closed = created + timedelta(days=10)
            prs.append(
                _pr(
                    repo,
                    j + 1,
                    created,
                    author=f"author-{i}",
                    closed=closed,
                    merged=merged_at,
                )
            )
    return scores, prs, first_timers


def test_analyze_known_spearman_and_criteria() -> None:
    scores, prs, first_timers = _synthetic_universe()
    report = analyze(scores, prs, T0, first_timers)
    pairs = {(p.score, p.label): p for p in report.pairs}
    assert pairs[("overall_porchlamp", "merge_rate")].spearman == 1.0
    assert pairs[("overall_porchlamp", "decision_rate")].spearman == 1.0
    assert pairs[("score_responsiveness", "merge_rate")].spearman == -1.0
    assert pairs[("score_responsiveness", "decision_rate")].spearman == -1.0
    assert pairs[("score_criticality", "merge_rate")].spearman is None
    assert pairs[("score_criticality", "merge_rate")].rho_reason == "constant_score"
    assert report.n_repos_in_analysis == 8
    assert report.n_prs_eligible == 128
    assert report.excluded == {"internal": 0, "not_first_timer": 0}

    criteria = {c.name: c for c in report.criteria}
    assert criteria["C1"].value == -1.0
    assert criteria["C1"].verdict == "FAIL"
    assert criteria["C2"].value == 1.0
    assert criteria["C2"].verdict == "PASS"

    # AUC: merged PRs concentrate in high-composite repos, but r0 still holds
    # one unmerged PR and r7 one merged PR, so the value is between 0 and 1:
    # (3220 + 172) / (64 * 64) = 0.828125 exactly.
    auc = pairs[("overall_porchlamp", "merge_rate")].auc
    assert auc is not None
    assert 0.5 < auc < 1.0
    assert auc == 0.828125


def test_analyze_excludes_non_eligible_prs() -> None:
    scores, prs, first_timers = _synthetic_universe()
    prs = prs + [
        _pr("org/r0", 100, T0 + timedelta(days=3), author="stranger", association="NONE"),
        _pr("org/r0", 101, T0 + timedelta(days=3), author="insider", association="MEMBER"),
    ]
    report = analyze(scores, prs, T0, first_timers)
    assert report.excluded == {"internal": 1, "not_first_timer": 1}
    assert report.n_prs_eligible == 128


def test_analyze_insufficient_repos_is_undecided() -> None:
    scores, prs, first_timers = _synthetic_universe()
    kept = [row for row in scores if row.repo == "org/r0"]
    report = analyze(kept, prs, T0, first_timers)
    pairs = {(p.score, p.label): p for p in report.pairs}
    assert pairs[("overall_porchlamp", "merge_rate")].spearman is None
    assert pairs[("overall_porchlamp", "merge_rate")].rho_reason == "insufficient_repos"
    criteria = {c.name: c for c in report.criteria}
    assert criteria["C2"].verdict == "UNDECIDED:insufficient_repos"


def test_window_constants_match_prereg() -> None:
    assert DECISION_WINDOW_DAYS == 30
    assert MERGE_WINDOW_DAYS == 90
    assert PILLAR_COLUMNS == (
        "score_responsiveness",
        "score_criticality",
        "score_governance",
        "score_rigor_ai",
        "score_accessibility",
    )
    assert COMPOSITE_COLUMN == "overall_porchlamp"


def test_last_page_reads_rel_last_not_per_page(monkeypatch: pytest.MonkeyPatch) -> None:
    import porchlamp.audit.forward as forward

    link = (
        '<https://api.github.com/repositories/1/pulls?per_page=100&page=2>; rel="next", '
        '<https://api.github.com/repositories/1/pulls?per_page=100&page=75>; rel="last"'
    )

    class _FakeResponse:
        headers = {"Link": link}

        def __enter__(self) -> "_FakeResponse":
            return self

        def __exit__(self, *args: object) -> bool:
            return False

    monkeypatch.setattr(forward.urllib.request, "urlopen", lambda *a, **k: _FakeResponse())
    assert forward._last_page("https://api.github.com/repositories/1/pulls?per_page=100&page=1", None) == 75

    class _FakeResponseNextOnly:
        headers = {"Link": '<https://api.github.com/repositories/1/pulls?page=2>; rel="next"'}

        def __enter__(self) -> "_FakeResponseNextOnly":
            return self

        def __exit__(self, *args: object) -> bool:
            return False

    monkeypatch.setattr(forward.urllib.request, "urlopen", lambda *a, **k: _FakeResponseNextOnly())
    assert forward._last_page("https://api.github.com/repositories/1/pulls?page=1", None) is None


# ------------------------------------------------------------- snapshot -- #


def _write_manifest(path: Path, as_of: datetime, snapshot_id: str | None = None) -> dict[str, Any]:
    manifest = {
        "manifest_version": 1,
        "snapshot_id": snapshot_id or "20260701T000000Z-abcdef012345",
        "as_of": as_of.isoformat(),
        "benchmark_version": "0.1.0",
        "constants_hash": "c" * 64,
        "code_commit": "d" * 40,
        "canonical_dump_hash": "e" * 64,
        "merkle_root": "f" * 64,
        "envelope_count": 0,
        "envelopes": [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def _write_db(path: Path, scores: list[ScoreRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE repositories (
            owner TEXT NOT NULL, name TEXT NOT NULL,
            is_stage0_disqualified BOOLEAN NOT NULL DEFAULT 0,
            score_responsiveness REAL NOT NULL,
            score_criticality REAL NOT NULL,
            score_governance REAL NOT NULL,
            score_rigor_ai REAL NOT NULL,
            score_accessibility REAL NOT NULL,
            overall_porchlamp REAL NOT NULL,
            UNIQUE(owner, name)
        )
        """
    )
    for row in scores:
        owner, _, name = row.repo.partition("/")
        conn.execute(
            "INSERT INTO repositories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                owner,
                name,
                0 if row.rated else 1,
                row.scores["score_responsiveness"],
                row.scores["score_criticality"],
                row.scores["score_governance"],
                row.scores["score_rigor_ai"],
                row.scores["score_accessibility"],
                row.scores["overall_porchlamp"],
            ),
        )
    conn.commit()
    conn.close()


def test_load_scores_reads_rated_rows(tmp_path: Path) -> None:
    scores, _, _ = _synthetic_universe()
    scores.append(
        ScoreRow(
            repo="org/gated",
            rated=False,
            scores={**{column: 0.0 for column in PILLAR_COLUMNS}, "overall_porchlamp": 0.0},
        )
    )
    db = tmp_path / "porchlamp.db"
    _write_db(db, scores)
    rows = load_scores(db)
    assert len(rows) == 9
    assert next(row for row in rows if row.repo == "org/gated").rated is False
    top = next(row for row in rows if row.repo == "org/r0")
    assert top.scores["overall_porchlamp"] == 100.0
    assert top.scores["score_responsiveness"] == 10.0


def test_resolve_snapshot_from_manifest_path(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path / "snapshots" / "s0" / "manifest.json", T0)
    ref = resolve_snapshot(str(tmp_path / "snapshots" / "s0" / "manifest.json"))
    assert ref.as_of == T0
    assert ref.snapshot_id == manifest["snapshot_id"]
    assert ref.data_dir == tmp_path


def test_resolve_snapshot_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no snapshot"):
        resolve_snapshot("nope", data_dir=tmp_path)


def test_first_timers_from_s0(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    write_envelope(
        data_dir / "raw" / "commits_180d" / "org__a.json",
        source="fixture",
        data=[
            {"author_login": "old", "date": (T0 - timedelta(days=1)).isoformat()},
            {"author_login": "new", "date": (T0 + timedelta(days=1)).isoformat()},
        ],
        fetched_at=T0.isoformat(),
    )
    write_envelope(
        data_dir / "raw" / "pr_events" / "org__a.json",
        source="fixture",
        data=[
            {
                "number": 1,
                "created_at": (T0 - timedelta(days=2)).isoformat(),
                "closed_at": None,
                "merged_at": None,
                "author_login": "old",
                "author_association": "NONE",
                "state": "closed",
            }
        ],
        fetched_at=T0.isoformat(),
    )
    prs = [
        _pr("org/a", 1, T0 + timedelta(days=1), author="old"),
        _pr("org/a", 2, T0 + timedelta(days=1), author="new"),
        _pr("org/a", 3, T0 + timedelta(days=1), author="brand-new"),
    ]
    first_timers = first_timers_from_s0(data_dir, T0, prs)
    assert "old" not in first_timers["org/a"]
    assert "new" in first_timers["org/a"]
    assert "brand-new" in first_timers["org/a"]


# ------------------------------------------------------------------- cli -- #


def test_dry_run_offline_prints_costs_and_paths(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    out = tmp_path / "forward"
    code = main(
        [
            "--collect",
            "--dry-run",
            "--offline",
            "--repos",
            "octo/a,octo/b",
            "--out",
            str(out),
            "--as-of",
            T0.isoformat(),
        ]
    )
    assert code == 0
    text = capsys.readouterr().out
    assert "octo/a" in text
    assert "octo/b" in text
    assert "offline" in text
    assert "raw/fwd_prs/octo__a.json" in text
    assert "forward_receipt.json" in text
    assert not out.exists()


def _repo_meta(archived: bool = False) -> dict[str, Any]:
    return {"full_name": "octo/a", "archived": archived}


def test_dry_run_live_costs_and_writes_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import porchlamp.audit.forward as forward

    calls: list[str] = []

    def fake_get(url: str, token: str) -> Any:
        calls.append(url)
        assert url.endswith("/repos/octo/a") or url.endswith("/repos/octo/b")
        return _repo_meta()

    def fake_last_page(url: str, token: str) -> int:
        return 75 if url.endswith("repos/octo/a/pulls") or "/octo/a/" in url else 2

    monkeypatch.setattr(forward, "_http_get_json", fake_get)
    monkeypatch.setattr(forward, "_last_page", fake_last_page)
    out = tmp_path / "forward"
    code = main(
        [
            "--collect",
            "--dry-run",
            "--repos",
            "octo/a,octo/b",
            "--out",
            str(out),
            "--as-of",
            T0.isoformat(),
        ]
    )
    assert code == 0
    assert len(calls) == 2
    text = capsys.readouterr().out
    assert "75 PR page" in text  # octo/a last page from the Link header
    assert "2 PR page" in text  # octo/b last page
    assert "76 points" in text  # octo/a: 1 meta + 75 pages
    assert "3 points" in text  # octo/b: 1 meta + 2 pages
    assert "79 points" in text  # total bound
    assert not out.exists()


def test_dry_run_live_partial_bound(
    tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    import porchlamp.audit.forward as forward

    def fake_get(url: str, token: str) -> Any:
        if url.endswith("/repos/octo/b"):
            raise forward.ForwardError(f"HTTP 404 for {url}")
        return _repo_meta()

    monkeypatch.setattr(forward, "_http_get_json", fake_get)
    monkeypatch.setattr(forward, "_last_page", lambda url, token: 75)
    code = main(
        [
            "--collect",
            "--dry-run",
            "--repos",
            "octo/a,octo/b",
            "--out",
            str(tmp_path / "forward"),
            "--as-of",
            T0.isoformat(),
        ]
    )
    assert code == 0
    text = capsys.readouterr().out
    assert "repo octo/b: repo not found" in text
    assert "total point cost bound: 76 points (known); 1 repo(s) with unknown bound" in text


def test_collect_writes_envelopes_and_receipt(
    tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    import porchlamp.audit.forward as forward

    out = tmp_path / "forward"
    manifest = _write_manifest(tmp_path / "snapshots" / "s0" / "manifest.json", T0)
    created_in_window = T0 + timedelta(days=10)
    created_late = T0 + timedelta(days=5)

    def fake_get(url: str, token: str) -> Any:
        if url.endswith("/repos/octo/a"):
            return _repo_meta()
        if url.endswith("page=1"):
            return [
                {
                    "number": 3,
                    "created_at": created_in_window.isoformat(),
                    "closed_at": None,
                    "merged_at": None,
                    "user": {"login": "alice"},
                    "author_association": "NONE",
                    "state": "open",
                },
                {
                    "number": 2,
                    "created_at": (T0 + timedelta(days=1)).isoformat(),
                    "closed_at": (T0 + timedelta(days=3)).isoformat(),
                    "merged_at": None,
                    "user": {"login": "bob"},
                    "author_association": "NONE",
                    "state": "closed",
                },
            ]
        if url.endswith("page=2"):
            return [
                {
                    "number": 1,
                    "created_at": created_late.isoformat(),
                    "closed_at": None,
                    "merged_at": None,
                    "user": {"login": "carol"},
                    "author_association": "NONE",
                    "state": "open",
                }
            ]
        if url.endswith("page=3"):
            return []
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(forward, "_http_get_json", fake_get)
    code = main(
        [
            "--collect",
            "--snapshot",
            str(tmp_path / "snapshots" / "s0" / "manifest.json"),
            "--repos",
            "octo/a",
            "--fetched-at",
            FETCHED_AT,
            "--out",
            str(out),
        ]
    )
    assert code == 0
    envelope_path = out / "raw" / "fwd_prs" / "octo__a.json"
    assert envelope_path.is_file()
    envelope = read_envelope(envelope_path)
    assert envelope.hash_origin == "native"
    assert envelope.fetched_at == FETCHED_AT
    assert [item["number"] for item in envelope.data] == [3, 2, 1]
    receipt = json.loads((out / "forward_receipt.json").read_text(encoding="utf-8"))
    assert receipt["snapshot_id"] == manifest["snapshot_id"]
    assert receipt["collected_at"] == FETCHED_AT
    assert receipt["repos"][0]["status"] == "collected"
    assert receipt["repos"][0]["points"] == 4  # 1 meta + 3 pages (end page confirms)
    assert receipt["total_points"] == 4
    prs = load_pr_envelopes(out)
    assert [pr.number for pr in prs] == [3, 2, 1]
    assert prs[2].author == "carol"


def test_collect_without_fetched_at_fails(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    snapshot_dir = tmp_path / "snapshots" / "s0"
    _write_manifest(snapshot_dir / "manifest.json", T0)
    code = main(
        [
            "--collect",
            "--snapshot",
            str(snapshot_dir / "manifest.json"),
            "--repos",
            "octo/a",
            "--out",
            str(tmp_path / "forward"),
        ]
    )
    assert code == 2
    assert "--fetched-at" in capsys.readouterr().err
    assert not (tmp_path / "forward").exists()


def test_collect_rejects_unparseable_fetched_at(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    snapshot_dir = tmp_path / "snapshots" / "s0"
    _write_manifest(snapshot_dir / "manifest.json", T0)
    code = main(
        [
            "--collect",
            "--snapshot",
            str(snapshot_dir / "manifest.json"),
            "--repos",
            "octo/a",
            "--fetched-at",
            "not-a-timestamp",
            "--out",
            str(tmp_path / "forward"),
        ]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "not an ISO-8601 timestamp" in err
    assert "not-a-timestamp" in err


def test_plan_and_dry_run_run_without_fetched_at(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    snapshot_dir = tmp_path / "snapshots" / "s0"
    _write_manifest(snapshot_dir / "manifest.json", T0)
    out = tmp_path / "forward"
    code = main(
        [
            "--snapshot",
            str(snapshot_dir / "manifest.json"),
            "--data-dir",
            str(tmp_path),
            "--out",
            str(out),
        ]
    )
    assert code == 0
    assert "forward plan" in capsys.readouterr().out
    code = main(
        [
            "--collect",
            "--dry-run",
            "--offline",
            "--repos",
            "octo/a",
            "--snapshot",
            str(snapshot_dir / "manifest.json"),
            "--data-dir",
            str(tmp_path),
            "--out",
            str(out),
        ]
    )
    assert code == 0
    assert "dry-run writes nothing" in capsys.readouterr().out
    assert not out.exists()


def test_plan_mode_without_collection(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    snapshot_dir = tmp_path / "snapshots" / "s0"
    _write_manifest(snapshot_dir / "manifest.json", T0)
    out = tmp_path / "forward"
    code = main(
        [
            "--snapshot",
            str(snapshot_dir / "manifest.json"),
            "--data-dir",
            str(tmp_path),
            "--out",
            str(out),
        ]
    )
    assert code == 0
    text = capsys.readouterr().out
    assert "no collection yet" in text
    assert "T + 90" in text
    assert not out.exists()


def test_real_collection_requires_snapshot(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    code = main(
        ["--collect", "--repos", "octo/a", "--out", str(tmp_path / "forward"), "--as-of", T0.isoformat()]
    )
    assert code == 2
    assert "requires --snapshot" in capsys.readouterr().err


def test_load_scores_filters_by_active_snapshot_on_multi_snapshot_v2_db(tmp_path: Path) -> None:
    """Regression: load_scores filters by active snapshot_id on multi-snapshot v2 DBs."""
    from porchlamp.audit.forward import load_scores

    db_path = tmp_path / "multi_v2.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE snapshots (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            promoted_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE repositories (
            snapshot_id TEXT NOT NULL,
            owner TEXT NOT NULL,
            name TEXT NOT NULL,
            rated INTEGER NOT NULL,
            p_responsiveness REAL,
            p_criticality REAL,
            p_governance REAL,
            p_rigor REAL,
            p_accessibility REAL,
            porchlamp REAL
        )
        """
    )
    conn.execute("INSERT INTO snapshots VALUES ('snap-old', '2026-08-01T00:00:00Z', '2026-08-01T01:00:00Z')")
    conn.execute("INSERT INTO snapshots VALUES ('snap-new', '2026-09-01T00:00:00Z', '2026-09-01T01:00:00Z')")
    conn.execute("INSERT INTO repositories VALUES ('snap-old', 'org', 'alpha', 1, 50.0, 50.0, 50.0, 50.0, 50.0, 50.0)")
    conn.execute("INSERT INTO repositories VALUES ('snap-new', 'org', 'alpha', 1, 90.0, 80.0, 85.0, 75.0, 95.0, 86.0)")
    conn.commit()
    conn.close()

    rows = load_scores(db_path)
    assert len(rows) == 1
    assert rows[0].repo == "org/alpha"
    assert rows[0].scores["overall_porchlamp"] == 86.0


def test_first_timers_from_s0_supports_v2_dict_envelopes(tmp_path: Path) -> None:
    """Regression: first_timers_from_s0 supports dict-shaped commits_365d and pr_window envelopes."""
    from porchlamp.audit.forward import first_timers_from_s0

    commits_dir = tmp_path / "raw" / "commits_365d"
    pr_win_dir = tmp_path / "raw" / "pr_window"
    commits_dir.mkdir(parents=True)
    pr_win_dir.mkdir(parents=True)

    (commits_dir / "org__alpha.json").write_text(
        json.dumps(
            {
                "fetched_at": FETCHED_AT,
                "source": "test",
                "data": {"commits": [{"author_login": "veteran_commit", "date": (T0 - timedelta(days=10)).isoformat()}]},
            }
        ),
        encoding="utf-8",
    )
    (pr_win_dir / "org__alpha.json").write_text(
        json.dumps(
            {
                "fetched_at": FETCHED_AT,
                "source": "test",
                "data": {
                    "events": [
                        {"author_login": "veteran_pr", "created_at": (T0 - timedelta(days=5)).isoformat()},
                        {"author": {"login": "newcomer_s0"}, "createdAt": (T0 + timedelta(days=1)).isoformat()},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    prs = [
        _pr("org/alpha", 1, T0 + timedelta(days=2), author="veteran_commit"),
        _pr("org/alpha", 2, T0 + timedelta(days=2), author="veteran_pr"),
        _pr("org/alpha", 3, T0 + timedelta(days=2), author="brand_new"),
    ]
    known = first_timers_from_s0(tmp_path, T0, prs)
    assert known["org/alpha"] == frozenset({"newcomer_s0", "brand_new"})

