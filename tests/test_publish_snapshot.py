"""Tests for scripts/publish_snapshot.py: deterministic README/CITATION rendering, site staging, and pipeline."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from porchlamp.collect.envelope import write_envelope

ROOT = Path(__file__).resolve().parent.parent


def _load_publish_snapshot():
    spec = importlib.util.spec_from_file_location("publish_snapshot", ROOT / "scripts" / "publish_snapshot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pub_mod = _load_publish_snapshot()


SAMPLE_SNAPSHOT = {
    "snapshot_id": "2026-10-27",
    "as_of": "2026-10-22T22:47:00+00:00",
    "benchmark_version": "2.0.0-rc1",
    "constants_hash": "093cefd6c2f45824a756d1a2fa95b2fe1aa857ad505357ad5cdfcbaff81a4182",
    "manifest_root": "8425233e71eb0000000000000000000000000000000000000000000000000000",
    "canonical_dump_hash": "7142a0d012d40000000000000000000000000000000000000000000000000000",
    "counts": {"rated": 3028, "gated": 5092, "unrated": 2880, "total": 11000},
    "persona_visible_counts": {
        "ai_assisted": 3543,
        "balanced": 3028,
        "career_capital": 554,
        "first_timer": 1636,
        "senior_systems": 1101,
    },
}

SAMPLE_REPORT = {
    "snapshot_id": "2026-10-27",
    "values": {
        "corpus_projects": "11,000",
        "one_in": "8",
        "judged": "4,169",
        "black_holes": "567",
        "share": "13.6",
        "undecided_cut": "184",
        "pr_cap": "500",
        "window_start": "25 April",
        "band1_share": "11",
        "band2_share": "17",
        "band3_share": "17",
        "ranked": "3,028",
        "rho": "0.00",
        "top100_overlap": "1",
        "corpus_prs": "1,442,863",
        "corpus_authors": "227,260",
    },
}


def test_render_readme_and_citation_cff_are_deterministic_and_exact():
    readme = pub_mod.render_readme(SAMPLE_SNAPSHOT, SAMPLE_REPORT)
    assert "Measured on the 2026-10-27 snapshot of 11,000 projects:" in readme
    assert "567 (13.6%) left most of them with no human reply" in readme
    assert "We could not judge 184 busy projects yet" in readme
    one = {**SAMPLE_REPORT, "values": {**SAMPLE_REPORT["values"], "undecided_cut": "1"}}
    assert "We could not judge 1 busy project yet: we hold only\n  its newest" in pub_mod.render_readme(SAMPLE_SNAPSHOT, one)
    assert "`data/2026-10-27/`" in readme
    assert "- **Snapshot ID:** `2026-10-27` (measured as of `2026-10-22T22:47:00+00:00`, benchmark `2.0.0-rc1`)" in readme
    assert "`constants_hash: 093cefd6c2f4\u2026`" in readme

    assert "and 1 of the\n  100 most-starred projects makes the top 100." in readme
    ten = {**SAMPLE_REPORT, "values": {**SAMPLE_REPORT["values"], "top100_overlap": "10"}}
    assert "and 10 of the\n  100 most-starred projects make the top 100." in pub_mod.render_readme(SAMPLE_SNAPSHOT, ten)
    assert "private" not in readme
    assert (
        "The engine that collects the data is open source under the Apache License 2.0: "
        "https://github.com/pctablet505/porchlamp-engine ." in readme
    )
    help_at, license_at = readme.find("\n## Help improve it\n"), readme.find("\n## License and citation\n")
    assert -1 < help_at < license_at, "the way to help sits before the licence"
    words = " ".join(readme[help_at:license_at].split())
    assert "every issue and pull request gets a reply from a person within 14 days" in words
    assert "Each project page ends with a link that opens a report with the project filled in." in words
    assert "https://github.com/pctablet505/porchlamp-engine/blob/main/CONTRIBUTING.md" in words
    assert "rebuilt at every publish, so a change made here would be overwritten" in words

    cff = pub_mod.render_citation_cff(SAMPLE_SNAPSHOT, SAMPLE_REPORT)
    assert 'version: "2026-10-27"' in cff
    assert "date-released: 2026-10-27" in cff
    assert "1,442,863 pull requests from 227,260 outside contributors to 11,000 GitHub projects" in cff


def test_stage_site_preserves_operational_files_frozen_reports_and_one_prior_data_dir(tmp_path):
    target = tmp_path / "porchlamp-site"
    (target / ".git").mkdir(parents=True)
    (target / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (target / "CNAME").write_text("porchlamp.org\n", encoding="utf-8")
    (target / "LICENSE").write_text("CC-BY-4.0\n", encoding="utf-8")
    (target / "googled8fac072696a3851.html").write_text("google-site-verification: googled8fac072696a3851.html\n", encoding="utf-8")
    (target / "7c9d4a2e8b1f6a3c5d8e9f0a1b2c3d4e.txt").write_text("7c9d4a2e8b1f6a3c5d8e9f0a1b2c3d4e\n", encoding="utf-8")
    (target / "obsolete.html").write_text("old", encoding="utf-8")

    # Seed 2 historical frozen reports and 2 old data dirs (only newest 1 old data dir should be kept).
    for old_id in ("2026-10-25", "2026-10-26"):
        (target / "black-holes" / old_id).mkdir(parents=True)
        (target / "black-holes" / old_id / "index.html").write_text(f"frozen {old_id}", encoding="utf-8")
        (target / "first-pull-requests" / old_id).mkdir(parents=True)
        (target / "first-pull-requests" / old_id / "index.html").write_text(f"frozen first PRs {old_id}", encoding="utf-8")
        (target / "data" / old_id).mkdir(parents=True)
        (target / "data" / old_id / "snapshot.json").write_text("{}", encoding="utf-8")

    exported = tmp_path / "exported"
    sid = "2026-10-28"
    (exported / "data" / sid).mkdir(parents=True)
    snap = {**SAMPLE_SNAPSHOT, "snapshot_id": sid}
    rep = {**SAMPLE_REPORT, "snapshot_id": sid}
    (exported / "data" / sid / "snapshot.json").write_text(json.dumps(snap), encoding="utf-8")
    (exported / "data" / sid / "report.json").write_text(json.dumps(rep), encoding="utf-8")
    (exported / "black-holes" / sid).mkdir(parents=True)
    (exported / "black-holes" / sid / "index.html").write_text(f"frozen {sid}", encoding="utf-8")
    (exported / "black-holes" / "index.html").write_text(f"latest {sid}", encoding="utf-8")
    (exported / "first-pull-requests" / sid).mkdir(parents=True)
    (exported / "first-pull-requests" / sid / "index.html").write_text(f"frozen first PRs {sid}", encoding="utf-8")
    (exported / "first-pull-requests" / "index.html").write_text(f"latest first PRs {sid}", encoding="utf-8")
    (exported / "index.html").write_text("home", encoding="utf-8")

    res = pub_mod.stage_site(exported, target, sid)
    assert res["kept_frozen_reports"] == [
        "black-holes/2026-10-25", "black-holes/2026-10-26", "first-pull-requests/2026-10-25", "first-pull-requests/2026-10-26",
    ]
    assert res["kept_previous_data"] == ["2026-10-26"]

    # Operational files untouched.
    assert (target / ".git" / "HEAD").is_file()
    assert (target / "CNAME").read_text(encoding="utf-8") == "porchlamp.org\n"
    assert (target / "LICENSE").read_text(encoding="utf-8") == "CC-BY-4.0\n"
    assert (target / "googled8fac072696a3851.html").is_file()
    assert (target / "7c9d4a2e8b1f6a3c5d8e9f0a1b2c3d4e.txt").is_file()
    assert not (target / "obsolete.html").exists()

    # All historical black-hole reports kept; data/ keeps only 2026-10-26 and 2026-10-28.
    assert sorted(p.name for p in (target / "black-holes").iterdir() if p.is_dir()) == [
        "2026-10-25",
        "2026-10-26",
        "2026-10-28",
    ]
    # Three cycles of the first pull requests report too: both earlier frozen copies and this one, beside the latest.
    assert sorted(p.name for p in (target / "first-pull-requests").iterdir() if p.is_dir()) == ["2026-10-25", "2026-10-26", "2026-10-28"]
    assert (target / "first-pull-requests" / "2026-10-25" / "index.html").read_text(encoding="utf-8") == "frozen first PRs 2026-10-25"
    assert (target / "first-pull-requests" / "index.html").read_text(encoding="utf-8") == f"latest first PRs {sid}"
    assert sorted(p.name for p in (target / "data").iterdir() if p.is_dir()) == ["2026-10-26", "2026-10-28"]
    assert f"Measured on the {sid} snapshot" in (target / "README.md").read_text(encoding="utf-8")
    assert f'version: "{sid}"' in (target / "CITATION.cff").read_text(encoding="utf-8")


def test_stage_site_refuses_any_github_directory(tmp_path):
    exported = tmp_path / "exported"
    (exported / "data" / "2026-10-28").mkdir(parents=True)
    (exported / "data" / "2026-10-28" / "snapshot.json").write_text(json.dumps(SAMPLE_SNAPSHOT), encoding="utf-8")
    (exported / ".github" / "workflows").mkdir(parents=True)
    target = tmp_path / "target"
    with pytest.raises(pub_mod.PublishError, match=r"\.github"):
        pub_mod.stage_site(exported, target, "2026-10-28")


def _seed_publish_fixture_data(data_dir: Path) -> None:
    """Seed a small multi-band corpus so derive -> build -> store -> stats -> export -> verify all run cleanly."""
    at = "2026-10-27T00:00:00Z"
    specs = [
        ("alpha", "one", "pypi", 2500),
        ("beta", "two", "npm", 15000),
        ("gamma", "three", "crates", 60000),
    ]
    cand_lines = []
    for owner, name, eco, stars in specs:
        key = f"{owner}__{name}"
        cand_lines.append(json.dumps({"owner": owner, "name": name, "ecosystem": eco}))

        def write(collector: str, data: object, k: str = key) -> None:
            write_envelope(data_dir / "raw" / collector / f"{k}.json", f"test:{collector}", data, at)

        write("github_meta", {
            "stars": stars,
            "forks": 100,
            "pushed_at": "2026-10-20T00:00:00Z",
            "archived": False,
            "fork": False,
            "mirror_url": None,
            "license_spdx": "MIT",
            "default_branch": "main",
        })
        prs = []
        reviews = {}
        window_items = []
        for idx in range(1, 16):
            created = f"2026-08-{idx:02d}T00:00:00Z"
            replied = f"2026-08-{idx:02d}T06:00:00Z"
            merged = f"2026-08-{idx:02d}T12:00:00Z"
            prs.append({
                "number": idx,
                "created_at": created,
                "closed_at": merged,
                "merged_at": merged,
                "author_login": f"extuser{idx}",
                "author_association": "NONE",
                "comments_total": 2,
                "state": "closed",
            })
            reviews[str(idx)] = replied
            window_items.append({
                "number": idx,
                "isDraft": False,
                "createdAt": created,
                "closedAt": merged,
                "mergedAt": merged,
                "author": {"__typename": "User", "login": f"extuser{idx}"},
                "authorAssociation": "NONE",
                "comments": {
                    "nodes": [
                        {
                            "author": {"__typename": "User", "login": "maint"},
                            "authorAssociation": "MEMBER",
                            "createdAt": replied,
                            "body": "Thanks for the fix, reviewed the edge cases carefully!",
                        }
                    ]
                },
                "reviews": {
                    "nodes": [
                        {
                            "author": {"__typename": "User", "login": "maint"},
                            "authorAssociation": "MEMBER",
                            "submittedAt": replied,
                            "state": "APPROVED",
                            "body": "Looks good to me after testing locally.",
                        }
                    ]
                },
                "timelineItems": {"nodes": []},
            })
        write("pr_events", prs)
        write("pr_reviews", reviews)
        write("pr_window", {
            "owner": owner,
            "name": name,
            "window_days": 180,
            "window_anchor": at,
            "window_start": "2026-04-01T00:00:00Z",
            "truncated": False,
            "count": len(window_items),
            "cost_points": 1,
            "prs": window_items,
        })
        write("contributors", [
            {"login": "maint", "contributions": 50},
            {"login": "maint2", "contributions": 35},
            {"login": "maint3", "contributions": 25},
        ])
        write("commits_180d", [
            {"author_login": f"maint{(idx % 3) + 1}", "date": f"2026-09-{idx:02d}T00:00:00Z"}
            for idx in range(1, 10)
        ])
        write("gfi", [
            {"number": 100, "assignee": None, "updated_at": "2026-10-15T00:00:00Z", "labels": ["good-first-issue"]},
            {"number": 101, "assignee": None, "updated_at": "2026-10-16T00:00:00Z", "labels": ["help-wanted"]},
        ])
        write("actions_runs", [
            {
                "head_sha": "c1",
                "status": "completed",
                "conclusion": "success",
                "run_duration_seconds": 300,
                "created_at": "2026-10-15T00:00:00Z",
            }
        ])
        write("depsdev", {
            "stars": stars,
            "forks": 100,
            "open_issues": 10,
            "license": "MIT",
            "usedby_count": 500,
            "scorecard_overall": 8.5,
        })
        write("scorecard", {"score": 8.5, "checks": {"Branch-Protection": 10}})
        # The as_of is computed from the data (D2-A): a this-cycle releases and issues_counts file, each anchored after the
        # newest pr_window anchor `at` and within 14 days of it, make `at` itself the earliest valid as_of.
        for layer, anchor, body in (
            ("releases", "2026-10-27T01:00:00Z", {"window_days": 365, "source": "releases", "releases": [], "releases_truncated": False, "tags": None, "recent_count": 0}),
            ("issues_counts", "2026-10-27T02:00:00Z", {"window_days": 90, "closed_90d": 1, "updated_90d": 1, "sample_size": 1, "sample_total": 1, "sample": [], "comments_per_issue": 1.0}),
        ):
            write_envelope(data_dir / "raw" / layer / f"{key}.json", f"test:{layer}", {**body, "window_anchor": anchor}, anchor)
        write("tree", {"paths": ["LICENSE", "CONTRIBUTING.md", "README.md", "pyproject.toml", "src/main.py"]})
        write("bodies", {"body": "Please sign off commits (DCO). Contributors may join the maintainer team."}, k=f"{key}__CONTRIBUTING.md")

    (data_dir / "raw").mkdir(parents=True, exist_ok=True)
    (data_dir / "raw" / "candidates.jsonl").write_text("\n".join(cand_lines) + "\n", encoding="utf-8")
    (data_dir / "state" / "refresh").mkdir(parents=True, exist_ok=True)
    (data_dir / "state" / "refresh" / "MAIN.done").write_text("2026-10-28T00:00:00Z\n", encoding="utf-8")


def test_resolve_as_of_and_snapshot_id_validation(tmp_path):
    assert pub_mod.validate_snapshot_id(" 2026-10-28 ") == "2026-10-28"
    with pytest.raises(pub_mod.PublishError):
        pub_mod.validate_snapshot_id("../2026-10-28")

    data_dir = tmp_path / "data"
    _seed_publish_fixture_data(data_dir)
    # MAIN.done (2026-10-28T00:00:00Z) is not the as_of: the earliest valid value is the newest pr_window anchor.
    assert pub_mod.resolve_as_of(data_dir, None) == "2026-10-27T00:00:00+00:00"
    assert pub_mod.resolve_as_of(data_dir, "2026-10-28T05:30:00Z") == "2026-10-28T05:30:00+00:00"
    with pytest.raises(pub_mod.PublishError, match="invalid ISO-8601"):
        pub_mod.resolve_as_of(data_dir, "not-a-time")


A = datetime(2026, 10, 24, 18, 9, 24, tzinfo=timezone.utc)  # the newest pr_window anchor of the 2026-10-31 build, to the second
DAY = timedelta(days=1)


def _put(root: Path, layer: str, key: str, data: dict, fetched_at: datetime) -> None:
    write_envelope(root / "raw" / layer / f"{key}.json", f"test:{layer}", data, fetched_at.isoformat())


def _pr_window_data(anchor: datetime | None) -> dict:
    data = {"window_days": 180, "cap": 2000, "count": 1, "truncated": False, "prs": [{"number": 1, "createdAt": "2026-10-01T00:00:00+00:00", "state": "MERGED"}]}
    return data if anchor is None else {**data, "window_anchor": anchor.isoformat()}


def _asof_root(tmp_path: Path, *, extra_pr_window_fetched: datetime | None = None) -> Path:
    """Two repos of one cycle (pull requests up to ``A``, releases and issues after it) and a MAIN.done four days later.
    ``extra_pr_window_fetched`` adds a third repo whose pr_window record has no window_anchor, only that fetched_at."""
    root = tmp_path / "asof-root"
    repos = {"acme__alpha": (A, A + 2 * DAY, A + 4 * DAY), "acme__beta": (A - 2 * DAY, A + 2 * DAY, A + 4 * DAY)}
    for key, (pr, releases, issues) in repos.items():
        _put(root, "pr_window", key, _pr_window_data(pr), pr)
        _put(root, "releases", key, {"window_anchor": releases.isoformat(), "window_days": 365, "releases": []}, releases)
        _put(root, "issues_counts", key, {"window_anchor": issues.isoformat(), "window_days": 90}, issues)
    keys = list(repos)
    if extra_pr_window_fetched is not None:
        _put(root, "pr_window", "acme__gamma", _pr_window_data(None), extra_pr_window_fetched)
        keys.append("acme__gamma")
    (root / "raw").mkdir(parents=True, exist_ok=True)
    (root / "raw" / "candidates.jsonl").write_text(
        "".join(json.dumps({"owner": k.split("__")[0], "name": k.split("__")[1], "ecosystem": "pypi"}) + "\n" for k in keys),
        encoding="utf-8",
    )
    (root / "state" / "refresh").mkdir(parents=True, exist_ok=True)
    (root / "state" / "refresh" / "MAIN.done").write_text("2026-10-28T00:00:00Z\n", encoding="utf-8")
    return root


def test_resolve_as_of_is_the_earliest_valid_value_never_main_done(tmp_path):
    """D2-A: MAIN.done marks the end of the last collection step (here four days after the newest pull-request anchor);
    using it as the as_of would shrink every window. The earliest instant every guard accepts is the newest pr_window
    anchor rounded up to the minute."""
    root = _asof_root(tmp_path)
    assert (root / "state" / "refresh" / "MAIN.done").is_file()
    assert pub_mod.resolve_as_of(root, None) == "2026-10-24T18:10:00+00:00"


def test_resolve_as_of_refuses_a_given_value_that_fails_a_guard_and_accepts_one_that_passes(tmp_path):
    root = _asof_root(tmp_path)
    assert pub_mod.resolve_as_of(root, "2026-10-24T18:10:00Z") == "2026-10-24T18:10:00+00:00"
    one_minute_early = (A - timedelta(minutes=1)).isoformat()
    with pytest.raises(pub_mod.PublishError, match="cut"):
        pub_mod.resolve_as_of(root, one_minute_early)


def test_resolve_as_of_checks_the_value_it_computes(tmp_path):
    """RB1-G: a pr_window record with no window_anchor and a later fetched_at cannot cover the earliest value, which
    ignores it. Returning that value unchecked would hand the export a root it refuses; the check stops the run here."""
    ok = _asof_root(tmp_path)
    assert pub_mod.resolve_as_of(ok, None) == "2026-10-24T18:10:00+00:00"
    bad = _asof_root(tmp_path / "bad", extra_pr_window_fetched=A + 5 * DAY)
    with pytest.raises(pub_mod.PublishError, match="cut"):
        pub_mod.resolve_as_of(bad, None)


def test_resolve_as_of_raises_publish_error_when_no_valid_value_exists_or_the_root_is_unreadable(tmp_path):
    root = _asof_root(tmp_path)
    for layer in ("releases", "issues_counts"):
        for path in (root / "raw" / layer).glob("*.json"):
            path.unlink()
    with pytest.raises(pub_mod.PublishError, match="this-cycle"):
        pub_mod.resolve_as_of(root, None)
    with pytest.raises(pub_mod.PublishError):
        pub_mod.resolve_as_of(tmp_path / "no-such-root", None)


def test_the_default_snapshot_id_is_the_run_date_not_the_as_of_date(tmp_path, monkeypatch):
    """D2-A: the as_of is one to three weeks older than the run, so the snapshot id (the publication date) comes from the clock."""
    root = _asof_root(tmp_path)
    seen: dict = {}
    monkeypatch.setattr(pub_mod, "run_pipeline", lambda **kwargs: seen.update(kwargs) or {"snapshot_id": kwargs["snapshot_id"]})
    before = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rc = pub_mod.main(["--data-dir", str(root), "--work-dir", str(tmp_path / "work"), "--target-site", str(tmp_path / "site")])
    after = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert rc == 0
    assert seen["snapshot_id"] in {before, after}
    assert seen["snapshot_id"] != "2026-10-24"


def test_run_pipeline_executes_real_snapshot_stages_and_full_orchestration(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _seed_publish_fixture_data(data_dir)
    work_dir = tmp_path / "work"
    target = tmp_path / "porchlamp-site"
    (target / ".git").mkdir(parents=True)
    (target / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (target / "CNAME").write_text("porchlamp.org\n", encoding="utf-8")
    (target / "LICENSE").write_text("CC-BY-4.0\n", encoding="utf-8")
    # Seed an existing projects/ directory and prior black-holes/data so --previous-site and preservation are exercised.
    (target / "projects").mkdir(parents=True)
    (target / "black-holes" / "2026-10-27").mkdir(parents=True)
    (target / "black-holes" / "2026-10-27" / "index.html").write_text("old report", encoding="utf-8")
    (target / "data" / "2026-10-27").mkdir(parents=True)
    (target / "data" / "2026-10-27" / "snapshot.json").write_text("{}", encoding="utf-8")
    status_out = tmp_path / "summary.json"

    real_run_cmd = pub_mod._run_cmd
    steps_seen: list[tuple[str, list[str]]] = []

    def intercept_run_cmd(cmd: list[str], *, step: str, stdout_file: Path | None = None) -> None:
        steps_seen.append((step, list(cmd)))
        if step in (
            "derive bundles",
            "build snapshot directory",
            "score and store snapshot database",
            "compute report stats",
            "compute first-pull-requests stats",
        ):
            # Run the real engine stages!
            real_run_cmd(cmd, step=step, stdout_file=stdout_file)
            return
        if step == "export static site":
            # Full Black Holes prose guards require the 150-repo multi-ecosystem corpus (tested in test_report_pages.py);
            # materialize the exported directory structure here so stage_site runs end-to-end.
            out_idx = cmd.index("--out") + 1
            out_dir = Path(cmd[out_idx])
            (out_dir / "data" / "2026-10-28").mkdir(parents=True, exist_ok=True)
            snap = {**SAMPLE_SNAPSHOT, "snapshot_id": "2026-10-28"}
            rep = {**SAMPLE_REPORT, "snapshot_id": "2026-10-28"}
            (out_dir / "data" / "2026-10-28" / "snapshot.json").write_text(json.dumps(snap), encoding="utf-8")
            (out_dir / "data" / "2026-10-28" / "report.json").write_text(json.dumps(rep), encoding="utf-8")
            (out_dir / "black-holes" / "2026-10-28").mkdir(parents=True, exist_ok=True)
            (out_dir / "black-holes" / "2026-10-28" / "index.html").write_text("new frozen", encoding="utf-8")
            (out_dir / "projects").mkdir(parents=True, exist_ok=True)
            (out_dir / "index.html").write_text("home", encoding="utf-8")
            return
        if step == "verify static export":
            return
        raise AssertionError(f"unexpected step: {step}")

    monkeypatch.setattr(pub_mod, "_run_cmd", intercept_run_cmd)

    rc = pub_mod.main([
        "--data-dir", str(data_dir),
        "--work-dir", str(work_dir),
        "--target-site", str(target),
        "--snapshot-id", "2026-10-28",
        "--workers", "2",
        "--quick-verify",
        "--no-mutants",
        "--status-out", str(status_out),
    ])
    assert rc == 0
    assert [s for s, _ in steps_seen] == [
        "derive bundles",
        "build snapshot directory",
        "score and store snapshot database",
        "compute report stats",
        "compute first-pull-requests stats",
        "export static site",
        "verify static export",
    ]
    # Real SQLite DB and stats JSON were built by the engine!
    assert (work_dir / "cvi-2026-10-28.db").is_file()
    stats_payload = json.loads((work_dir / "stats-2026-10-28.json").read_text(encoding="utf-8"))
    assert stats_payload["snapshot_id"] == "2026-10-28" and stats_payload["repos"] == 3
    # The first-pull-requests stats came from the engine too, on the same as-of, and both later stages read that file.
    first_prs_path = work_dir / "first-pull-requests-2026-10-28.json"
    first_prs_payload = json.loads(first_prs_path.read_text(encoding="utf-8"))
    assert first_prs_payload["as_of"] == stats_payload["as_of"]
    assert first_prs_payload["population"]["projects"] == 3
    # Both export and verify received --previous-site because target/projects existed.
    export_args = steps_seen[5][1]
    verify_args = steps_seen[6][1]
    assert "--previous-site" in export_args and "--previous-site" in verify_args
    assert export_args[export_args.index("--first-pull-requests") + 1] == str(first_prs_path)
    assert verify_args[verify_args.index("--first-pull-requests") + 1] == str(first_prs_path)
    assert "--quick" in verify_args and "--no-mutants" in verify_args

    summary = json.loads(status_out.read_text(encoding="utf-8"))
    assert summary["snapshot_id"] == "2026-10-28"
    assert summary["kept_frozen_reports"] == ["black-holes/2026-10-27"]
    assert summary["kept_previous_data"] == ["2026-10-27"]
    assert (target / "index.html").is_file()
    assert (target / "black-holes" / "2026-10-27" / "index.html").is_file()
    assert (target / "black-holes" / "2026-10-28" / "index.html").is_file()
    assert "Measured on the 2026-10-28 snapshot" in (target / "README.md").read_text(encoding="utf-8")
    assert 'version: "2026-10-28"' in (target / "CITATION.cff").read_text(encoding="utf-8")
    assert (target / "CNAME").read_text(encoding="utf-8") == "porchlamp.org\n"


def test_shrinkage_circuit_breaker_blocks_poisoned_miniature_snapshot_until_overridden(tmp_path):
    target = tmp_path / "site"
    (target / "data" / "2026-10-27").mkdir(parents=True)
    prev_snap = {
        **SAMPLE_SNAPSHOT,
        "snapshot_id": "2026-10-27",
        "counts": {"total": 11000, "rated": 7000, "gated": 3000, "unrated": 1000},
    }
    (target / "data" / "2026-10-27" / "snapshot.json").write_text(json.dumps(prev_snap), encoding="utf-8")
    (target / "index.html").write_text("live-home-untouched", encoding="utf-8")

    exported = tmp_path / "exported"
    (exported / "data" / "2026-10-28").mkdir(parents=True)
    tiny_snap = {
        **SAMPLE_SNAPSHOT,
        "snapshot_id": "2026-10-28",
        "counts": {"total": 50, "rated": 10, "gated": 30, "unrated": 10},
    }
    (exported / "data" / "2026-10-28" / "snapshot.json").write_text(json.dumps(tiny_snap), encoding="utf-8")
    (exported / "index.html").write_text("tiny-poisoned-home", encoding="utf-8")

    with pytest.raises(pub_mod.PublishError, match="shrinkage guard tripped"):
        pub_mod.stage_site(exported, target, "2026-10-28")
    # Live site remains completely untouched!
    assert (target / "index.html").read_text(encoding="utf-8") == "live-home-untouched"
    assert not (target / "data" / "2026-10-28").exists()

    # Intentional override (--allow-shrink) permits staging.
    res = pub_mod.stage_site(exported, target, "2026-10-28", allow_shrink=True)
    assert res["snapshot_id"] == "2026-10-28"
    assert (target / "index.html").read_text(encoding="utf-8") == "tiny-poisoned-home"


def test_stage_site_audits_symlinks_and_hidden_files_and_run_cmd_scrubs_secrets(tmp_path, monkeypatch):
    exported = tmp_path / "exported"
    (exported / "data" / "2026-10-28").mkdir(parents=True)
    (exported / "data" / "2026-10-28" / "snapshot.json").write_text(json.dumps(SAMPLE_SNAPSHOT), encoding="utf-8")
    (exported / "escape.html").symlink_to("/etc/hosts")

    target = tmp_path / "target"
    with pytest.raises(pub_mod.PublishError, match="symlink"):
        pub_mod.stage_site(exported, target, "2026-10-28")
    (exported / "escape.html").unlink()

    (exported / ".env").write_text("SECRET=1\n", encoding="utf-8")
    with pytest.raises(pub_mod.PublishError, match="hidden file"):
        pub_mod.stage_site(exported, target, "2026-10-28")
    (exported / ".env").unlink()

    # Subprocess execution scrubs all GitHub/drop/push credentials from child environment.
    monkeypatch.setenv("GH_TOKEN", "ghp_secret1")
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_secret2")
    monkeypatch.setenv("PORCHLAMP_DROP_READ_TOKEN", "github_pat_secret3")
    monkeypatch.setenv("PORCHLAMP_SITE_PUSH_TOKEN", "github_pat_secret4")
    env_dump = tmp_path / "child_env.json"
    pub_mod._run_cmd(
        [
            pub_mod.sys.executable,
            "-c",
            "import json, os; print(json.dumps({k: v for k, v in os.environ.items() if 'TOKEN' in k}))",
        ],
        step="check child env",
        stdout_file=env_dump,
    )
    leaked = json.loads(env_dump.read_text(encoding="utf-8"))
    for forbidden in ("GH_TOKEN", "GITHUB_TOKEN", "PORCHLAMP_DROP_READ_TOKEN", "PORCHLAMP_SITE_PUSH_TOKEN"):
        assert forbidden not in leaked


