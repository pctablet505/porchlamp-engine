"""Tests for scripts/derive.py feature-bundle derivation from the raw cache."""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

from porchlamp.collect.envelope import envelope_hash

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("derive", ROOT / "scripts" / "derive.py")
derive = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(derive)

NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)
OWNER, NAME = "acme", "widget"


def write(tmp: Path, collector: str, key: str, data, at="2026-09-11T00:00:00Z") -> None:
    p = tmp / "raw" / collector / f"{key}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"fetched_at": at, "source": "test", "data": data}),
                 encoding="utf-8")


def body(tmp: Path, fname: str, text: str) -> None:
    p = tmp / "raw" / "bodies" / f"{OWNER}__{NAME}__{fname}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"fetched_at": "2026-09-11T00:00:00Z",
                             "source": "test", "data": {"body": text}}),
                 encoding="utf-8")


def seed_full(tmp: Path) -> None:
    k = f"{OWNER}__{NAME}"
    write(tmp, "github_meta", k, {"stars": 1000, "forks": 50,
                                  "pushed_at": "2026-09-01T00:00:00Z", "archived": False,
                                  "license_spdx": "Apache-2.0", "default_branch": "main"})
    write(tmp, "pr_events", k, [
        {"number": 1, "created_at": "2026-06-01T00:00:00Z", "closed_at": "2026-06-02T00:00:00Z",
         "merged_at": "2026-06-02T00:00:00Z", "author_login": "alice",
         "author_association": "NONE", "comments_total": 4, "state": "closed"},
        {"number": 2, "created_at": "2026-06-03T00:00:00Z", "closed_at": "2026-06-04T00:00:00Z",
         "merged_at": None, "author_login": "bob", "author_association": "MEMBER",
         "comments_total": 2, "state": "closed"},
        {"number": 3, "created_at": "2026-06-05T00:00:00Z", "closed_at": None,
         "merged_at": None, "author_login": "alice", "author_association": "NONE",
         "comments_total": 0, "state": "open"},
        {"number": 4, "created_at": "2026-07-01T00:00:00Z", "closed_at": None,
         "merged_at": None, "author_login": "dana",
         "author_association": "FIRST_TIME_CONTRIBUTOR", "comments_total": 9, "state": "open"},
    ])
    write(tmp, "pr_reviews", k, {"1": "2026-06-01T12:00:00Z", "2": None, "3": None, "4": None})
    write(tmp, "contributors", k, [{"login": "alice", "contributions": 40},
                                   {"login": "bob", "contributions": 40},
                                   {"login": "carol", "contributions": 20}])
    write(tmp, "commits_180d", k, [{"author_login": "alice", "date": "2026-08-01T00:00:00Z"},
                                   {"author_login": "alice", "date": "2026-08-02T00:00:00Z"},
                                   {"author_login": "bob", "date": "2026-08-03T00:00:00Z"},
                                   {"author_login": "dependabot[bot]", "date": "2026-08-04T00:00:00Z"},
                                   {"author_login": "github-actions[bot]", "date": "2026-08-05T00:00:00Z"}])
    write(tmp, "gfi", k, [
        {"number": 10, "assignee": None, "updated_at": "2026-09-07T00:00:00Z",
         "labels": ["good-first-issue"]},
        {"number": 11, "assignee": None, "updated_at": "2026-09-08T00:00:00Z",
         "labels": [{"name": "help-wanted"}]},
        {"number": 12, "assignee": "bob", "updated_at": "2026-09-07T00:00:00Z",
         "labels": ["good-first-issue"]},
        {"number": 13, "assignee": None, "updated_at": "2026-07-01T00:00:00Z",
         "labels": ["good-first-issue"]},
    ])
    write(tmp, "actions_runs", k, [
        {"head_sha": "c2", "status": "completed", "conclusion": "success", "run_duration_seconds": 1200, "created_at": "2026-09-02T00:00:00Z"},
        {"head_sha": "c1", "status": "completed", "conclusion": "success", "run_duration_seconds": 600, "created_at": "2026-09-01T00:00:00Z"},
    ])
    write(tmp, "depsdev", k, {"stars": 990, "forks": 49, "open_issues": 5,
                              "license": "Apache-2.0", "usedby_count": 777,
                              "scorecard_overall": 8.8})
    write(tmp, "scorecard", k, {"score": 9.0, "checks": {"Branch-Protection": 10}})
    write(tmp, "tree", k, {"paths": ["LICENSE", "CONTRIBUTING.md", "README.md"]})
    write(tmp, "downloads", f"pypi__{NAME}", {"monthly": 5000})
    body(tmp, "CONTRIBUTING.md", "Please sign off your commits (DCO). "
         "Contributors may join the triage team and become maintainers.")
    body(tmp, "README.md", "Use an Assisted-by: trailer for AI-generated code.")


def run(tmp: Path):
    return derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp, fetch=False, now=NOW)


def test_full_bundle(tmp_path):
    seed_full(tmp_path)
    out = run(tmp_path)
    m, c = out["meta"], out["confidence"]
    assert m["stars"] == 1000 and c["stars"] == "verified"
    assert m["forks"] == 50
    assert m["monthly_downloads"] == 5000 and c["monthly_downloads"] == "verified"
    assert m["dependents_count"] == 777
    assert m["elephant_factor"] == 2          # 40 < 50%, 40+40 >= 50%
    assert m["active_nonbot_commits_180d"] == 3
    assert m["scorecard_score"] == 9.0 and c["scorecard_score"] == "verified"  # |9.0-8.8| <= 0.5
    assert m["legal_ip_model"] == "DCO" and c["legal_ip_model"] == "derived"
    # ai_posture / promotion_ladder come from evidence-based classification
    # (02 §4 3.1/3.3/4.2), not a regex heuristic over README/CONTRIBUTING
    # body text: this fixture seeds no governance/pr_window/commits_365d
    # envelope, so both are Unmeasured(collector_failure) -- confidence
    # "missing" -- and report the AD_HOC default.
    assert m["ai_posture"] == "AD_HOC" and c["ai_posture"] == "missing"
    assert m["promotion_ladder"] == "AD_HOC" and c["promotion_ladder"] == "missing"
    assert m["hard_ai_ban"] is False and m["off_platform_reviews"] is False
    assert m["dx_ci_minutes"] == 15.0
    assert m["active_unclaimed_gfis"] == 2
    assert m["rr_90"] == 1.0                  # alice: 2 external PRs, 4-day gap
    assert m["mean_inline_comments"] == 4.0   # only PR1 qualifies
    assert out["provenance"]["stars"]["source"].endswith("github_meta/acme__widget.json")
    assert out["provenance"]["stars"]["fetched_at"] == "2026-09-11T00:00:00Z"
    # openssf_signals stays None until all 10 PIKE fields have collectors
    # (partial signals would bias criticality toward 0, not neutral)
    assert m["openssf_signals"] is None


def test_provenance_records_envelope_sha256_for_verified_field(tmp_path):
    # A verified field's provenance must carry the envelope's own hash --
    # computed with the same function porchlamp.snapshot.manifest's freeze uses
    # (native when the envelope carries one, otherwise the backfilled
    # canonical hash of its data) -- so scripts/verify.py has something real
    # to recompute against, not just a path that happens to exist.
    seed_full(tmp_path)
    out = run(tmp_path)
    envelope_data = json.loads(
        (tmp_path / "raw" / "github_meta" / f"{OWNER}__{NAME}.json").read_text(encoding="utf-8")
    )["data"]
    expected = envelope_hash(envelope_data)
    assert out["confidence"]["stars"] == "verified"
    assert out["provenance"]["stars"]["sha256"] == expected
    assert out["confidence"]["forks"] == "verified"
    assert out["provenance"]["forks"]["sha256"] == expected


def test_provenance_omits_sha256_for_non_envelope_source(tmp_path):
    # Over-correction guard: a genuinely non-envelope source (the CLI-supplied
    # "owner"/"name"/"ecosystem" fields) must not gain a fabricated hash --
    # the absence of "sha256" is the honest signal scripts/verify.py's
    # unverifiable_provenance rule reads for a non-path source. Neither
    # "ai_posture" (sourced from the governance envelope) nor
    # "legal_ip_model" (sourced from the local bodies cache, see
    # test_body_cache_provenance_one_cached_one_missing below) is a
    # URL-sourced exemplar of this -- "owner" is the one field with a source
    # no envelope could ever back.
    seed_full(tmp_path)
    out = run(tmp_path)
    assert out["provenance"]["owner"]["source"] == "cli"
    assert "sha256" not in out["provenance"]["owner"]


def test_legal_ip_model_provenance_is_the_local_body_cache_not_a_url(tmp_path):
    # seed_full() caches a real CONTRIBUTING.md body -- legal_ip_model's
    # provenance must point at that cache file (a real, hashable envelope
    # under raw/), never the raw.githubusercontent.com URL, which never
    # resolves on the porchlamp.derive.bundle side. ai_posture/promotion_ladder
    # are not asserted here: the governance block unconditionally supersedes
    # both with its own evidence-based values and provenance. legal_ip_model
    # is the one field the governance block does not touch (it reads bare
    # "legal_ip", not "legal_ip_model"), and it also drives the live v1
    # scoring sweep (porchlamp.collect.governance module docstring).
    seed_full(tmp_path)
    out = run(tmp_path)
    assert out["confidence"]["legal_ip_model"] == "derived"
    assert out["provenance"]["legal_ip_model"]["source"] == (
        f"raw/bodies/{OWNER}__{NAME}__CONTRIBUTING.md.json")
    assert "sha256" in out["provenance"]["legal_ip_model"]


def test_legal_ip_model_provenance_one_cached_one_missing(tmp_path):
    """Both sides of legal_ip_model provenance: CONTRIBUTING.md cached ->
    it resolves to that real, hashable envelope; not cached -> it falls back
    to the tree envelope (still real and hashable, never a raw-URL
    literal)."""
    k = f"{OWNER}__{NAME}"
    base_gh = {"stars": 1, "forks": 0, "pushed_at": "2026-09-01T00:00:00Z", "archived": False,
              "license_spdx": "MIT", "default_branch": "main"}
    contrib_path = f"raw/bodies/{k}__CONTRIBUTING.md.json"

    # Cached side.
    write(tmp_path, "github_meta", k, base_gh)
    write(tmp_path, "tree", k, {"paths": ["CONTRIBUTING.md"]})
    body(tmp_path, "CONTRIBUTING.md", "Please sign off your commits (DCO).")
    out = run(tmp_path)
    assert out["provenance"]["legal_ip_model"]["source"] == contrib_path
    assert "sha256" in out["provenance"]["legal_ip_model"]

    # Missing side: a different repo, no CONTRIBUTING.md ever fetched.
    other = tmp_path / "other"
    write(other, "github_meta", k, base_gh)
    write(other, "tree", k, {"paths": ["CONTRIBUTING.md"]})
    out2 = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, other, fetch=False, now=NOW)
    # Relative to `other`, the data root this derivation read from -- the
    # provenance path is portable, so it is the same string whichever root
    # the envelopes happen to live under.
    assert out2["provenance"]["legal_ip_model"]["source"] == f"raw/tree/{k}.json"
    assert "sha256" in out2["provenance"]["legal_ip_model"]


def test_pr_event_mapping(tmp_path):
    seed_full(tmp_path)
    evs = {e["pr_number"]: e for e in run(tmp_path)["pr_events"]}
    assert evs[1]["response_latency_hours"] == 12.0
    assert evs[1]["is_external"] is True and evs[1]["author_association"] == "NONE"
    assert evs[1]["maintainer_review_comments"] == 4
    assert evs[2]["is_external"] is False and evs[2]["author_association"] == "MEMBER"
    assert evs[2]["response_latency_hours"] is None
    assert evs[3]["maintainer_review_comments"] == 0      # open, 0 comments, no review
    assert evs[4]["author_association"] == "FIRST_TIME"   # FIRST_TIME_CONTRIBUTOR mapped
    assert evs[4]["is_external"] is True
    assert evs[4]["maintainer_review_comments"] == 9      # open but 9 comments -> engaged


def test_missing_files(tmp_path):
    out = run(tmp_path)
    m, c = out["meta"], out["confidence"]
    assert c["stars"] == c["monthly_downloads"] == c["dependents_count"] == "missing"
    assert c["scorecard_score"] == c["rr_90"] == c["dx_ci_minutes"] == "missing"
    assert c["pr_events"] == "missing" and out["pr_events"] == []
    assert m["stars"] == 0 and m["elephant_factor"] == 1 and m["openssf_signals"] is None
    assert m["hard_ai_ban"] is False


def test_bot_filtering(tmp_path):
    k = f"{OWNER}__{NAME}"
    write(tmp_path, "commits_180d", k, [
        {"author_login": "realuser", "date": "2026-08-01T00:00:00Z"},
        {"author_login": "dependabot[bot]", "date": "2026-08-01T00:00:00Z"},
        {"author_login": "actions-user", "date": "2026-08-01T00:00:00Z"},
        {"author_login": "google-cla", "date": "2026-08-01T00:00:00Z"},
        {"author_login": "Stale-Bot", "date": "2026-08-01T00:00:00Z"},
    ])
    assert run(tmp_path)["meta"]["active_nonbot_commits_180d"] == 1


def test_elephant_factor_edges(tmp_path):
    assert derive.elephant_factor(None) == 1
    assert derive.elephant_factor([]) == 1
    assert derive.elephant_factor([{"login": "solo", "contributions": 100}]) == 1
    assert derive.elephant_factor([{"login": "a", "contributions": 60},
                                   {"login": "b", "contributions": 40}]) == 1
    assert derive.elephant_factor([{"login": "a", "contributions": 40},
                                   {"login": "b", "contributions": 40},
                                   {"login": "c", "contributions": 20}]) == 2


def test_scorecard_mismatch_flag(tmp_path):
    k = f"{OWNER}__{NAME}"
    write(tmp_path, "scorecard", k, {"score": 9.0, "checks": {}})
    write(tmp_path, "depsdev", k, {"usedby_count": 1, "scorecard_overall": 6.0})
    out = run(tmp_path)
    assert out["meta"]["scorecard_score"] == 9.0
    assert out["confidence"]["scorecard_score"] == "flag"


def test_downloads_null_is_missing(tmp_path):
    write(tmp_path, "downloads", f"pypi__{NAME}", {"monthly": None})
    out = run(tmp_path)
    assert out["meta"]["monthly_downloads"] == 0
    assert out["confidence"]["monthly_downloads"] == "missing"


def test_body_corrupt_cache_file_and_atomic_write(tmp_path, monkeypatch):
    """Regression: _body treats corrupt cache JSON as a cache miss and writes atomically."""
    import io

    cache_file = tmp_path / "raw" / "bodies" / f"{OWNER}__{NAME}__README.md.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text("{corrupt json", encoding="utf-8")

    # With fetch=False, corrupt cache file is treated as a cache miss (no JSONDecodeError)
    text, at = derive._body(tmp_path, OWNER, NAME, "README.md", fetch=False)
    assert text is None
    assert at is None

    # With fetch=True, _body fetches and atomically replaces the corrupt cache file
    class _FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        derive.urllib.request,
        "urlopen",
        lambda url, timeout=30: _FakeResp(b"Fresh README content"),
    )
    text2, at2 = derive._body(tmp_path, OWNER, NAME, "README.md", fetch=True)
    assert text2 == "Fresh README content"
    assert isinstance(at2, str)
    assert cache_file.is_file()
    assert not cache_file.with_suffix(".json.tmp").exists()
    saved = json.loads(cache_file.read_text(encoding="utf-8"))
    assert saved["data"]["body"] == "Fresh README content"


def test_derive_dx_54_blends_runtime_and_setup_signals(tmp_path):
    """5.4 DX blends 0.5 * runtime_score + 0.5 * setup_score when CI runs exist."""
    k = f"{OWNER}__{NAME}"
    # 5 pull-request commits, each with a quick lint run and a slow test run of 600, 900, 1500, 1800 and 6000 s:
    # a commit's wait is its slowest run -> median 1500 s = 25 min (the mean would be 36 min)
    # -> runtime_score = 100 - 5.0 * (25 - 20) = 75.0
    # Tree has .devcontainer/devcontainer.json (signal 1), CONTRIBUTING.md has
    # a Setup heading (signal 2), and README.md has a Setext Installation heading
    # (signal 3) -> setup_score = 100.0
    # Blended dx = 0.5 * 75.0 + 0.5 * 100.0 = 87.5, dx_run_count = 5
    write(tmp_path, "actions_runs", k, [
        {"head_sha": f"c{i}", "status": "completed", "conclusion": "success", "run_duration_seconds": seconds}
        for i, slow in enumerate((600, 6000, 1500, 900, 1800)) for seconds in (30, slow)
    ])
    write(tmp_path, "tree", k, {"paths": [".devcontainer/devcontainer.json", "README.md", "CONTRIBUTING.md"]})
    body(tmp_path, "CONTRIBUTING.md", "## Development Setup\nRun `pytest` locally.")
    body(tmp_path, "README.md", "Quick Installation\n==================\npip install widget")
    out = run(tmp_path)
    assert out["meta"]["dx"] == 87.5
    assert out["meta"]["dx_run_count"] == 5
    assert out["meta"]["dx_ci_minutes"] == 25.0
    assert out["confidence"]["dx"] == "derived"
    assert out["confidence"]["dx_run_count"] == "derived"

    # Empty runs list -> dx is None (confidence "missing"), dx_run_count is 0 ("derived")
    empty_tmp = tmp_path / "empty_runs"
    write(empty_tmp, "actions_runs", k, [])
    out_empty = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, empty_tmp, fetch=False, now=NOW)
    assert out_empty["meta"]["dx"] is None
    assert out_empty["confidence"]["dx"] == "missing"
    assert out_empty["meta"]["dx_run_count"] == 0
    assert out_empty["confidence"]["dx_run_count"] == "derived"



def test_derive_dx_54_takes_the_median_over_the_newest_thirty_commits_only(tmp_path):
    """Forty commits, newest first: of the newest 30, sixteen waited 25 min
    and fourteen 10 min (median 25 min, runtime 75); the ten older ones waited
    10 min. Counting all forty would make the median 10 min (runtime 100)."""
    k = f"{OWNER}__{NAME}"
    waits = [1500] * 16 + [600] * 14 + [600] * 10
    write(tmp_path, "actions_runs", k, [
        {"head_sha": f"c{i:02d}", "status": "completed", "conclusion": "success", "run_duration_seconds": seconds}
        for i, seconds in enumerate(waits)
    ])
    write(tmp_path, "tree", k, {"paths": ["README.md"]})
    out = run(tmp_path)
    assert out["meta"]["dx_run_count"] == 30
    assert out["meta"]["dx_ci_minutes"] == 25.0
    assert out["meta"]["dx"] == 0.5 * 75.0 + 0.5 * 0.0
