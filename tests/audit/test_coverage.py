"""Unit tests for :mod:`porchlamp.audit.coverage`.

Every assertion is an exact value on a 3-bundle fixture built here, so the
missingness rules, quantile rule, per-repo counts and the black-hole gate are
pinned. Quantiles use linear interpolation between close ranks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import pytest

from porchlamp.audit.coverage import AuditReport, InputStat, audit_universe

REPOS = ("org/alpha", "org/beta", "org/gamma")


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _envelope(source: str, data: Any) -> dict[str, Any]:
    return {"data": data, "fetched_at": "2026-09-12T00:00:00+00:00", "source": source}


def _github_meta(
    stars: int,
    pushed_at: Optional[str],
    archived: bool,
    open_issues: Optional[int],
    closed_count: Optional[int],
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "stars": stars,
        "forks": 0,
        "archived": archived,
        "created_at": "2020-01-01T00:00:00Z",
        "license_spdx": "MIT",
        "default_branch": "main",
        "open_issues": open_issues,
        "closed_count": closed_count,
    }
    if pushed_at is not None:
        data["pushed_at"] = pushed_at
    return _envelope("github-rest:repos/org/x", data)


def _derived_repo(ecosystem: str, name: str, stars: int, dependents: int) -> dict[str, Any]:
    return {
        "repo": f"org/{name}",
        "meta": {
            "owner": "org",
            "name": name,
            "ecosystem": ecosystem,
            "stars": stars,
            "dependents_count": dependents,
            "legal_ip_model": "DCO" if name == "alpha" else "DEFAULT",
            "ai_posture": "AD_HOC",
            "scorecard_score": {"alpha": 30.0, "beta": 90.0, "gamma": None}[name],
        },
        "confidence": {
            "stars": "derived" if name == "beta" else "verified",
            "dependents_count": {"alpha": "missing", "beta": "derived", "gamma": "verified"}[name],
        },
        "provenance": {},
        "pr_events": [],
    }


def _pr(
    number: int,
    created_at: str,
    is_external: bool,
    first_review_at: Optional[str],
    association: str,
    closed_at: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "pr_number": number,
        "created_at": created_at,
        "merged_at": None,
        "closed_at": closed_at,
        "is_external": is_external,
        "author_association": association,
        "first_human_review_at": first_review_at,
    }


def build_fixture(root: Path, extra: bool = False) -> Path:
    """Write a 3-repo data dir shaped like ``data/`` plus the sample layout."""
    raw = root / "raw"
    derived = root / "derived"

    _write(derived / "org__alpha.json", _derived_repo("crates", "alpha", 10, 0))
    _write(derived / "org__beta.json", _derived_repo("npm", "beta", 20, 4))
    _write(derived / "org__gamma.json", _derived_repo("pypi", "gamma", 30, 0))
    derived_join = {
        "org/alpha": [
            _pr(1, "2026-08-01T00:00:00+00:00", True, "2026-08-01T10:00:00+00:00", "NONE"),
            _pr(2, "2026-08-10T00:00:00+00:00", True, "2026-08-11T06:00:00+00:00", "NONE", closed_at="2026-08-12T00:00:00+00:00"),
            _pr(3, "2026-08-20T00:00:00+00:00", False, None, "MEMBER"),
            _pr(4, "2026-09-01T00:00:00+00:00", False, None, "OWNER"),
        ],
        "org/beta": [
            _pr(9, "2026-09-02T00:00:00+00:00", False, None, "MEMBER"),
            _pr(10, "2026-09-02T12:00:00+00:00", False, "2026-09-02T17:00:00+00:00", "MEMBER"),
        ],
    }
    for repo, events in derived_join.items():
        path = derived / f"{repo.replace('/', '__')}.json"
        bundle = json.loads(path.read_text(encoding="utf-8"))
        bundle["pr_events"] = events
        _write(path, bundle)

    _write(
        raw / "github_meta" / "org__alpha.json",
        _github_meta(10, "2026-09-01T00:00:00Z", False, 5, 0),
    )
    _write(
        raw / "github_meta" / "org__beta.json",
        _github_meta(20, "2026-08-01T00:00:00Z", False, None, 0),
    )
    _write(
        raw / "github_meta" / "org__gamma.json",
        _github_meta(30, None, True, None, 5),
    )

    _write(raw / "depsdev" / "org__alpha.json", _envelope("deps.dev", {"dependents": []}))
    _write(
        raw / "depsdev" / "org__beta.json",
        _envelope(
            "deps.dev",
            {"dependents": [{"projectPath": "x"}, {"projectPath": "y"}], "open_issues": 20},
        ),
    )

    _write(
        raw / "contributors" / "org__alpha.json",
        _envelope("github-rest", [{"login": "a", "contributions": 3}, {"login": "b", "contributions": 1}]),
    )
    _write(
        raw / "contributors" / "org__beta.json",
        _envelope("github-rest", [{"login": "c", "contributions": 9}]),
    )
    _write(raw / "contributors" / "org__gamma.json", _envelope("github-rest", []))

    _write(
        raw / "gfi" / "org__alpha.json",
        _envelope(
            "github-graphql",
            {"overall": {"score": 20}, "checks": [{"name": "a", "value": None}, {"name": "b", "value": None}]},
        ),
    )
    _write(
        raw / "gfi" / "org__beta.json",
        _envelope("github-graphql", {"overall": {"score": 95}, "checks": [{"name": "a", "value": 7}]}),
    )
    _write(
        raw / "gfi" / "org__gamma.json",
        _envelope("github-rest:issues", [{"number": 1, "assignee": None, "labels": ["good first issue"]}]),
    )

    _write(raw / "scorecard" / "org__alpha.json", _envelope("securityscorecards", {"score": 30.0}))
    _write(raw / "scorecard" / "org__beta.json", _envelope("securityscorecards", {"score": 90.0}))

    _write(raw / "downloads" / "crates__alpha.json", _envelope("shields", {"monthly": 100}))
    _write(raw / "downloads" / "pypi__gamma.json", _envelope("pypistats", {"monthly": 0}))

    _write(raw / "tree" / "org__alpha.json", _envelope("github-rest:git/trees", {"paths": ["LICENSE", "src/lib.rs"], "truncated": False}))
    _write(raw / "tree" / "org__beta.json", _envelope("github-rest:git/trees", {"paths": ["README.md"], "truncated": False}))

    _write(
        raw / "pr_events" / "org__gamma.json",
        _envelope(
            "github-graphql",
            [
                {"number": 1, "created_at": "2026-01-01T00:00:00Z", "author_association": "NONE", "merged_at": None},
                {"number": 2, "created_at": "2026-01-06T00:00:00Z", "author_association": "CONTRIBUTOR", "merged_at": None, "closed_at": "2026-01-08T00:00:00Z"},
                {"number": 3, "created_at": "2026-01-11T00:00:00Z", "author_association": "OWNER", "merged_at": None},
            ],
        ),
    )
    if extra:
        _write(
            raw / "actions_runs" / "org__alpha.json",
            _envelope("github-rest:actions/runs", [{"id": 1}, {"id": 2}, {"id": 3}]),
        )
    return root


def _row(report: AuditReport, name: str) -> InputStat:
    for stat in report.inputs:
        if stat.name == name:
            return stat
    raise AssertionError(f"no input row named {name!r}; rows: {[s.name for s in report.inputs]}")


@pytest.fixture()
def report(tmp_path: Path) -> AuditReport:
    return audit_universe(build_fixture(tmp_path))


def test_universe_is_the_union_of_raw_and_derived_keys(report: AuditReport) -> None:
    assert [row.repo for row in report.repos] == list(REPOS)
    assert report.n_repos == 3


def test_stars_row_missing_derived_distinct_and_quantiles(report: AuditReport) -> None:
    stars = _row(report, "meta.stars")
    assert (stars.n, stars.present, stars.missing) == (3, 3, 0)
    assert stars.missing_rate == pytest.approx(0.0)
    assert stars.derived == 1
    assert stars.derived_rate == pytest.approx(1.0 / 3.0)
    assert stars.distinct == 3
    assert stars.zero_fraction == pytest.approx(0.0)
    assert stars.constant is False
    assert (stars.min, stars.q1, stars.median, stars.q3, stars.max) == (10.0, 15.0, 20.0, 25.0, 30.0)
    assert stars.mean == pytest.approx(20.0)


def test_dependents_missing_uses_the_confidence_state_not_the_number(report: AuditReport) -> None:
    row = _row(report, "meta.dependents_count")
    assert (row.n, row.present, row.missing) == (3, 2, 1)
    assert row.missing_rate == pytest.approx(1.0 / 3.0)
    assert row.derived == 1
    assert row.derived_rate == pytest.approx(0.5)
    assert (row.min, row.median, row.max) == (0.0, 2.0, 4.0)
    assert row.zero_fraction == pytest.approx(0.5)
    raw = _row(report, "depsdev.dependents")
    assert (raw.present, raw.missing) == (1, 2)
    assert raw.missing_rate == pytest.approx(2.0 / 3.0)
    assert (raw.min, raw.max, raw.mean) == (2.0, 2.0, 2.0)
    assert raw.derived_rate is None


def test_absent_key_and_empty_list_and_none_all_count_as_missing(report: AuditReport) -> None:
    pushed = _row(report, "github_meta.pushed_at")
    assert (pushed.present, pushed.missing) == (2, 1)
    assert pushed.missing_rate == pytest.approx(1.0 / 3.0)
    assert pushed.numeric is False
    assert pushed.min is None and pushed.zero_fraction is None
    contributors = _row(report, "contributors.count")
    assert (contributors.present, contributors.missing) == (2, 1)
    assert (contributors.min, contributors.q1, contributors.median, contributors.max) == (1.0, 1.25, 1.5, 2.0)
    assert contributors.q3 == pytest.approx(1.75)
    assert contributors.zero_fraction == pytest.approx(0.0)
    mirror = _row(report, "github_meta.is_mirror")
    assert (mirror.present, mirror.missing) == (0, 3)
    assert mirror.missing_rate == pytest.approx(1.0)
    assert mirror.distinct == 0 and mirror.constant is False


def test_zero_fraction_and_boolean_rows(report: AuditReport) -> None:
    closed = _row(report, "github_meta.closed_count")
    assert closed.zero_fraction == pytest.approx(2.0 / 3.0)
    assert closed.distinct == 2
    assert (closed.q1, closed.median, closed.q3) == (0.0, 0.0, 2.5)
    assert closed.mean == pytest.approx(5.0 / 3.0)
    archived = _row(report, "github_meta.archived")
    assert archived.distinct == 2 and archived.zero_fraction == pytest.approx(2.0 / 3.0)
    downloads = _row(report, "downloads.monthly")
    assert (downloads.present, downloads.missing) == (2, 1)
    assert downloads.zero_fraction == pytest.approx(0.5)
    assert (downloads.min, downloads.q1, downloads.median, downloads.q3, downloads.max) == (0.0, 25.0, 50.0, 75.0, 100.0)


def test_constant_parameter_is_flagged(report: AuditReport) -> None:
    posture = _row(report, "meta.ai_posture")
    assert posture.distinct == 1 and posture.constant is True
    assert posture.missing_rate == pytest.approx(0.0)
    license_row = _row(report, "github_meta.license_spdx")
    assert license_row.distinct == 1 and license_row.constant is True
    assert license_row.numeric is False


def test_pr_and_latency_input_rows(report: AuditReport) -> None:
    latency = _row(report, "prs.first_response_latency_hours")
    assert (latency.present, latency.missing) == (2, 1)
    assert latency.missing_rate == pytest.approx(1.0 / 3.0)
    assert (latency.min, latency.median, latency.max) == (5.0, 12.5, 20.0)
    gfi_score = _row(report, "gfi.score")
    assert (gfi_score.present, gfi_score.missing) == (2, 1)
    assert (gfi_score.min, gfi_score.median, gfi_score.max) == (20.0, 57.5, 95.0)
    assert _row(report, "gfi.all_zero").present == 2
    assert _row(report, "gfi.all_zero").zero_fraction == pytest.approx(0.5)
    assert _row(report, "gfi.nonnull_checks").max == 1.0
    assert _row(report, "gfi.issue_count").present == 1
    assert _row(report, "scorecard.score").median == pytest.approx(60.0)
    assert _row(report, "tree.license_present").present == 2


def test_per_repo_counts_prs_external_latency_and_days_spanned(report: AuditReport) -> None:
    by_repo = {row.repo: row for row in report.repos}
    alpha = by_repo["org/alpha"]
    assert (alpha.prs, alpha.external, alpha.latency_samples) == (4, 2, 2)
    assert alpha.days_spanned == 31
    assert by_repo["org/beta"].prs == 2
    assert by_repo["org/beta"].external == 0
    assert by_repo["org/beta"].days_spanned == 0
    gamma = by_repo["org/gamma"]
    assert (gamma.prs, gamma.external, gamma.latency_samples) == (3, 2, 0)
    assert gamma.days_spanned == 10


def test_per_pr_evidence_counts_the_f4_and_f5_denominators(report: AuditReport) -> None:
    assert report.pr_total == 9
    assert report.pr_external == 4
    assert report.pr_with_response == 3
    assert report.pr_merged == 0
    assert report.repos_at_recorded_cap == 0


def test_code_owners_row_reads_the_tree_paths(report: AuditReport) -> None:
    owners = _row(report, "tree.code_owners_present")
    assert (owners.present, owners.missing) == (2, 1)
    assert owners.zero_fraction == pytest.approx(1.0)
    assert owners.distinct == 1 and owners.constant is True


def test_open_external_pr_denominator_and_span_quantiles(report: AuditReport) -> None:
    by_repo = {row.repo: row for row in report.repos}
    assert by_repo["org/alpha"].open_external_prs == 1
    assert by_repo["org/beta"].open_external_prs == 0
    assert by_repo["org/gamma"].open_external_prs == 1
    assert report.open_external_histogram == {"0": 1, "1-2": 2, "3-5": 0, "6-9": 0, "10+": 0}
    assert report.black_holes_open_external_le3 == 2
    assert report.black_holes_open_external_le5 == 2
    assert report.days_spanned_quantiles == {
        "q05": pytest.approx(1.0),
        "q25": pytest.approx(5.0),
        "q50": pytest.approx(10.0),
        "q75": pytest.approx(20.5),
        "q95": pytest.approx(28.9),
    }
    assert report.median_latency_samples == pytest.approx(1.0)


def test_black_hole_count_and_open_issue_denominator_histogram(report: AuditReport) -> None:
    assert report.black_hole_count == 2
    assert report.black_hole_rate == pytest.approx(2.0 / 3.0)
    assert report.denominator_histogram == {"0": 0, "1-9": 1, "10-99": 1, "100+": 0, "missing": 1}
    assert report.denominator_mean == pytest.approx(12.5)
    open_issues = _row(report, "repo.open_issues")
    assert (open_issues.present, open_issues.missing) == (2, 1)
    assert (open_issues.min, open_issues.q1, open_issues.median, open_issues.q3, open_issues.max) == (
        5.0,
        8.75,
        12.5,
        16.25,
        20.0,
    )


def test_repo_selection_does_not_change_values(tmp_path: Path, report: AuditReport) -> None:
    single = audit_universe(tmp_path, repos=["org/alpha"])
    assert single.n_repos == 1
    alpha = _row(single, "meta.stars")
    assert (alpha.n, alpha.present, alpha.missing) == (1, 1, 0)
    assert (alpha.min, alpha.max) == (10.0, 10.0)
    assert single.black_hole_count == 1


def test_over_correction_guard_unrelated_envelope_leaves_other_rows_unchanged(
    tmp_path: Path, report: AuditReport
) -> None:
    other = audit_universe(build_fixture(tmp_path / "extra", extra=True))

    keys = ["meta.stars", "meta.dependents_count", "repo.open_issues", "prs.first_response_latency_hours"]
    for name in keys:
        before, after = _row(report, name), _row(other, name)
        assert (before.present, before.missing, before.distinct) == (
            after.present,
            after.missing,
            after.distinct,
        ), name
        assert (before.min, before.median, before.max) == (after.min, after.median, after.max), name
    assert _row(other, "actions_runs.run_count").present == 1
    assert _row(other, "actions_runs.run_count").max == 3.0
