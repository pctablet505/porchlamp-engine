"""Tests for scripts/publish_snapshot.py: deterministic README/CITATION rendering, site staging, and pipeline."""

from __future__ import annotations

import importlib.util
import json
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
    "constants_hash": "de2c71c1d7bad01ff749cd81560974ce5199352075feeeda6d805a8d52dd2332",
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
    assert "`data/2026-10-27/`" in readme
    assert "- **Snapshot ID:** `2026-10-27` (measured as of `2026-10-22T22:47:00+00:00`, benchmark `2.0.0-rc1`)" in readme
    assert "`constants_hash: de2c71c1d7ba\u2026`" in readme

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
    (exported / "index.html").write_text("home", encoding="utf-8")

    res = pub_mod.stage_site(exported, target, sid)
    assert res["kept_frozen_reports"] == ["2026-10-25", "2026-10-26"]
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
    assert pub_mod.resolve_as_of(data_dir, None) == "2026-10-28T00:00:00+00:00"
    assert pub_mod.resolve_as_of(data_dir, "2026-10-28T05:30:00Z") == "2026-10-28T05:30:00+00:00"


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
        if step in ("derive bundles", "build snapshot directory", "score and store snapshot database", "compute report stats"):
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
        "export static site",
        "verify static export",
    ]
    # Real SQLite DB and stats JSON were built by the engine!
    assert (work_dir / "cvi-2026-10-28.db").is_file()
    stats_payload = json.loads((work_dir / "stats-2026-10-28.json").read_text(encoding="utf-8"))
    assert stats_payload["snapshot_id"] == "2026-10-28" and stats_payload["repos"] == 3
    # Both export and verify received --previous-site because target/projects existed.
    export_args = steps_seen[4][1]
    verify_args = steps_seen[5][1]
    assert "--previous-site" in export_args and "--previous-site" in verify_args
    assert "--quick" in verify_args and "--no-mutants" in verify_args

    summary = json.loads(status_out.read_text(encoding="utf-8"))
    assert summary["snapshot_id"] == "2026-10-28"
    assert summary["kept_frozen_reports"] == ["2026-10-27"]
    assert summary["kept_previous_data"] == ["2026-10-27"]
    assert (target / "index.html").is_file()
    assert (target / "black-holes" / "2026-10-27" / "index.html").is_file()
    assert (target / "black-holes" / "2026-10-28" / "index.html").is_file()
    assert "Measured on the 2026-10-28 snapshot" in (target / "README.md").read_text(encoding="utf-8")
    assert 'version: "2026-10-28"' in (target / "CITATION.cff").read_text(encoding="utf-8")
    assert (target / "CNAME").read_text(encoding="utf-8") == "porchlamp.org\n"


