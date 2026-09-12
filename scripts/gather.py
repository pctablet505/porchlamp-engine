"""Gather verified raw data for candidate repos into data/raw/<collector>/.

One envelope per (collector, repo): {"source","fetched_at","http_status",
"etag","sha256","collector_version","data"} (the contract in cvi.collect.envelope).
Existence of the envelope = completed (checkpoint); --force refetches.
GitHub meta+PRs+first-reviews come from ONE aliased GraphQL request/repo;
contributors/commits/GFI/actions/tree via REST; deps.dev, OpenSSF scorecard
and registry download counts are keyless second sources.

Usage: .venv/bin/python scripts/gather.py --limit 5 [--only-collector X] [--force]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from cvi.collect.commits import collect_commits_365d
from cvi.collect.envelope import write_envelope

DATA = Path(__file__).resolve().parent.parent / "data" / "raw"
CANDIDATES = DATA / "candidates.jsonl"
GQL = "https://api.github.com/graphql"
SLEEP_REST = 0.15
SLEEP_KEYLESS = 0.35
SLEEP_PYPISTATS = 15.0  # pypistats.org: ~5 req/min/IP (429 arrives as HTTP 200 + HTML body)
PR_SAMPLE = 30
CONTRIB_PAGES = 2
COMMIT_PAGES = 3


def _env(collector: str, key: str, source: str, data: Any) -> Path:
    """Write one envelope with its hash, status and collector version (`03` section 5).

    ``http_status=200`` records a fact about the transports used here, not a
    guess: ``gh api`` exits non-zero and ``urllib`` raises ``HTTPError`` on a
    non-2xx answer, so a response that reached this function was accepted.
    """
    return write_envelope(
        DATA / collector / f"{key}.json",
        source=source,
        data=data,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        http_status=200,
    )


def _done(collector: str, key: str, force: bool) -> bool:
    return (DATA / collector / f"{key}.json").is_file() and not force


def _gh(*args: str) -> Any:
    out = subprocess.run(["gh", "api", *args], capture_output=True, text=True, timeout=90)
    if out.returncode != 0:
        raise RuntimeError(f"gh api {' '.join(args[:2])} rc={out.returncode}: {out.stderr[:160]}")
    return json.loads(out.stdout) if out.stdout.strip() else None


def _get_json(url: str, headers: Optional[dict] = None) -> Optional[dict]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except Exception:  # review-ok: gate-network — keyless source absent -> None
        return None


def gather_repo(owner: str, name: str, ecosystem: str, force: bool = False,
                skip_pypistats: bool = False) -> dict[str, str]:
    """Fetch every collector for one repo; return {collector: ok|skip|fail}."""
    key = f"{owner}__{name}"
    status: dict[str, str] = {}

    # --- 1 GraphQL point: meta + PR sample + first reviews ---
    if not _done("github_meta", key, force):
        q = (
            '{ r: repository(owner: "%s", name: "%s") {'
            'stargazerCount forkCount pushedAt isArchived createdAt'
            ' licenseInfo { spdxId } defaultBranchRef { name }'
            ' pullRequests(states: [OPEN, MERGED, CLOSED], first: %d,'
            ' orderBy: {field: CREATED_AT, direction: DESC}) {'
            ' nodes { number createdAt closedAt mergedAt'
            ' author { login } authorAssociation'
            ' comments { totalCount } reviews(first: 1) { nodes { createdAt } }'
            ' } } } }' % (owner, name, PR_SAMPLE)
        )
        try:
            out = subprocess.run(["gh", "api", "graphql", "-f", f"query={q}"],
                                 capture_output=True, text=True, timeout=90)
            r = json.loads(out.stdout).get("data", {}).get("r") if out.returncode == 0 else None
        except Exception:  # review-ok: gate-network — transient gh failure, rerun resumes
            r = None
        if r:
            _env("github_meta", key, "github-graphql", {
                "stars": r["stargazerCount"], "forks": r["forkCount"],
                "pushed_at": r["pushedAt"], "archived": r["isArchived"],
                "created_at": r["createdAt"],
                "license_spdx": (r.get("licenseInfo") or {}).get("spdxId") or "NOASSERTION",
                "default_branch": (r.get("defaultBranchRef") or {}).get("name") or "main"})
            nodes = (r.get("pullRequests") or {}).get("nodes") or []
            _env("pr_events", key, "github-graphql", [{
                "number": n["number"], "created_at": n["createdAt"],
                "closed_at": n["closedAt"], "merged_at": n["mergedAt"],
                "author_login": (n.get("author") or {}).get("login") or "",
                "author_association": n.get("authorAssociation") or "NONE",
                "comments_total": (n.get("comments") or {}).get("totalCount", 0),
                "state": "merged" if n["mergedAt"] else
                         ("closed" if n["closedAt"] else "open"),
            } for n in nodes])
            _env("pr_reviews", key, "github-graphql", {
                str(n["number"]): ((n.get("reviews") or {}).get("nodes") or [{}])[0]
                .get("createdAt") for n in nodes})
            status.update(github_meta="ok", pr_events="ok", pr_reviews="ok")
            branch = (r.get("defaultBranchRef") or {}).get("name") or "main"
        else:
            status.update(github_meta="fail", pr_events="fail", pr_reviews="fail")
            branch = "main"
    else:
        try:
            branch = json.loads((DATA / "github_meta" / f"{key}.json")
                                .read_text())["data"].get("default_branch", "main")
        except Exception:  # review-ok: gate-cache — corrupt envelope treated as main
            branch = "main"
        status.update(github_meta="skip", pr_events="skip", pr_reviews="skip")

    rest = [
        ("contributors", f"repos/{owner}/{name}/contributors",
         ["-X", "GET", "-f", "per_page=100", "-f", "anon=1"],
         lambda d: [{"login": c.get("login") or c.get("email") or "?",
                     "contributions": c.get("contributions", 0)} for c in d or []]),
        ("gfi", f"repos/{owner}/{name}/issues",
         ["-X", "GET", "-f", "state=open", "-f", "per_page=100",
          "-f", "labels=good-first-issue,help-wanted"],
         lambda d: [{"number": i["number"],
                     "assignee": (i.get("assignee") or {}).get("login"),
                     "updated_at": i["updated_at"],
                     "labels": [lab["name"] for lab in i.get("labels") or []]}
                    for i in d or []]),
        ("actions_runs", f"repos/{owner}/{name}/actions/runs",
         ["-X", "GET", "-f", "per_page=30", "-f", "status=completed"],
         lambda d: [{"created_at": w["created_at"],
                     "run_duration_seconds": s} for w in
                    (w for w in (d or {}).get("workflow_runs", []))
                    if w.get("created_at") and (s := _run_secs(w)) is not None]),
        ("tree", f"repos/{owner}/{name}/git/trees/{branch}",
         ["-X", "GET", "-f", "recursive=1"],
         lambda d: {"paths": [t["path"] for t in (d or {}).get("tree", [])],
                    "truncated": bool((d or {}).get("truncated"))}),
    ]
    for collector, path, flags, shape in rest:
        if _done(collector, key, force):
            status[collector] = "skip"
            continue
        time.sleep(SLEEP_REST)
        try:
            _env(collector, key, f"github-rest:{path}", shape(_gh(path, *flags)))
            status[collector] = "ok"
        except Exception as e:  # review-ok: gate-network — per-collector retry on rerun
            status[collector] = "fail"
            print(f"  WARN {collector} {owner}/{name}: {e}")

    # commits_180d (paged, capped)
    if not _done("commits_180d", key, force):
        since = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat()
        rows: list[dict] = []
        try:
            for page in range(1, COMMIT_PAGES + 1):
                time.sleep(SLEEP_REST)
                d = _gh(f"repos/{owner}/{name}/commits", "-X", "GET",
                        "-f", "per_page=100", "-f", f"since={since}",
                        "-f", f"page={page}") or []
                rows.extend({"author_login": (c.get("author") or {}).get("login") or "",
                             "date": (c.get("commit") or {}).get("author", {}).get("date")}
                            for c in d)
                if len(d) < 100:
                    break
            _env("commits_180d", key, "github-rest:commits", rows)
            status["commits_180d"] = "ok"
        except Exception as e:  # review-ok: gate-network — retry on rerun
            status["commits_180d"] = "fail"
            print(f"  WARN commits {owner}/{name}: {e}")
    else:
        status["commits_180d"] = "skip"

    # keyless second sources
    for collector, url, hdr, shape in [
        ("depsdev", f"https://api.deps.dev/v3/projects/github.com%2F{owner}%2F{name}",
         {}, _shape_depsdev),
        ("scorecard",
         f"https://api.securityscorecards.dev/projects/github.com/{owner}/{name}",
         {}, lambda d: {"score": d.get("score"),
                        "checks": {c["name"]: c.get("score") for c in d.get("checks", [])}}
         if d else None),
    ]:
        if _done(collector, key, force):
            status[collector] = "skip"
            continue
        time.sleep(SLEEP_KEYLESS)
        body = _get_json(url, hdr)
        shaped = shape(body) if body else None
        _env(collector, key, url, shaped)
        status[collector] = "ok" if shaped else "fail"

    # downloads (registry per ecosystem). A null monthly (rate-limited
    # pypistats etc.) is NOT complete: reruns refetch it.
    dl_key = f"{ecosystem}__{name}"
    dl_p = DATA / "downloads" / f"{dl_key}.json"
    try:
        dl_monthly = (json.loads(dl_p.read_text()).get("data") or {}).get("monthly") \
            if dl_p.is_file() else None
    except (json.JSONDecodeError, OSError):  # review-ok: gate-cache — corrupt -> refetch
        dl_monthly = None
    if not (dl_monthly is not None and not force):
        monthly, src = _downloads(ecosystem, name, skip_pypistats=skip_pypistats)
        _env("downloads", dl_key, src, {"monthly": monthly})
        status["downloads"] = "ok" if monthly is not None else "fail"
    else:
        status["downloads"] = "skip"
    # W1-9 (D-GATE-1/2/3/6): repo-object eligibility envelope
    if _done("repo_object", key, force):
        status["repo_object"] = "skip"
    else:
        from cvi.collect.repo_object import collect_repo_object
        try:
            status["repo_object"] = collect_repo_object(DATA, owner, name, branch)
        except Exception as e:  # review-ok: gate-network — retry on rerun
            status["repo_object"] = "fail"
            print(f"  WARN repo_object {owner}/{name}: {e}")

    # --- W1-1: per-label newcomer-label GFI collection. Supersedes the
    # AND-label entry in `rest` above: when the gfi envelope is missing or
    # legacy, fetch the repo's label list once, query each alias-matching
    # label separately, and rewrite the envelope with labels_matched. ---
    from cvi.collect import labels as cvi_labels

    gfi_path = DATA / "gfi" / f"{key}.json"
    if cvi_labels.needs_gather(gfi_path, force):
        try:
            result = cvi_labels.collect_newcomer_issues(_gh, owner, name)
            cvi_labels.write_gfi_envelope(owner, name, result, gfi_path)
            status["gfi"] = "ok"
        except Exception as e:  # review-ok: gate-network — retry on rerun
            status["gfi"] = "fail"
            print(f"  WARN gfi {owner}/{name}: {e}")
    else:
        status["gfi"] = "skip"

    # --- W1-5: commits_365d (365 d, cap 1000; author email/login/type) ---
    if not _done("commits_365d", key, force):
        def _page(page: int, since: str) -> list[dict]:
            time.sleep(SLEEP_REST)
            return _gh(f"repos/{owner}/{name}/commits", "-X", "GET",
                       "-f", "per_page=100", "-f", f"since={since}",
                       "-f", f"page={page}") or []

        try:
            _env("commits_365d", key, "github-rest:commits",
                 collect_commits_365d(_page))
            status["commits_365d"] = "ok"
        except Exception as e:  # review-ok: gate-network - retry on rerun
            status["commits_365d"] = "fail"
            print(f"  WARN commits_365d {owner}/{name}: {e}")
    else:
        status["commits_365d"] = "skip"
    # W1-2 (F3): package-level dependents — deps.dev primary, ecosyste.ms
    # fallback; "skip" = no registry mapping for this ecosystem (module
    # docstring of cvi.collect.depsdev), no envelope, no fetch.
    if _done("dependents", key, force):
        status["dependents"] = "skip"
    else:
        from cvi.collect import depsdev as depsdev_mod
        try:
            status["dependents"] = depsdev_mod.collect_dependents(DATA, owner, name, ecosystem)
        except Exception as e:  # review-ok: gate-network — retry on rerun
            status["dependents"] = "fail"
            print(f"  WARN dependents {owner}/{name}: {e}")
    # W1-3 (D-IN-1/2/4, 02 §4 1.1/1.4/4.3): windowed PRs, typed actors
    if _done("pr_window", key, force):
        status["pr_window"] = "skip"
    else:
        from cvi.collect import github_graphql as prw_mod
        try:
            status["pr_window"] = prw_mod.collect_pr_window(DATA, owner, name)
        except Exception as e:  # review-ok: gate-network — retry on rerun
            status["pr_window"] = "fail"
            print(f"  WARN pr_window {owner}/{name}: {e}")
    # W1-8 (F8, D-MISS-1): downloads null is retry-able, not a hard failure:
    # pypi/npm/crates -> "pending" (pypi via scripts/backfill_downloads.py at
    # <= 4/min); go (deps.dev dependents only), maven, systems -> "unmeasured".
    if status.get("downloads") == "fail":
        try:
            import backfill_downloads as _bdl
        except ImportError:
            from scripts import backfill_downloads as _bdl
        status["downloads"] = ("pending" if ecosystem in _bdl.RETRYABLE_ECOSYSTEMS
                               else "unmeasured")
    # --- W1-7 (02 §4 2.2 / 4.1): releases, issues search counts, CI runs ---
    from cvi.collect import pike_signals as w17_pike
    for collector, call in (
        ("releases", lambda: w17_pike.collect_releases(DATA, owner, name)),
        ("issues_counts", lambda: w17_pike.collect_issue_counts(DATA, owner, name)),
        ("runs", lambda: w17_pike.collect_runs(DATA, owner, name, branch)),
    ):
        if _done(collector, key, force):
            status[collector] = "skip"
            continue
        try:
            status[collector] = call()
        except Exception as e:  # review-ok: gate-network — retry on rerun
            status[collector] = "fail"
            print(f"  WARN {collector} {owner}/{name}: {e}")
    # --- W1-6 (02 §4 3.1/3.3/4.2): governance evidence envelope — check
    # names on the 20 newest merged PRs, 365 d sign-off commits and the
    # GOVERNANCE/MAINTAINERS/CONTRIBUTING/SECURITY + AI-policy files. ---
    if _done("governance", key, force):
        status["governance"] = "skip"
    else:
        from cvi.collect import governance as gov_mod
        try:
            status["governance"] = gov_mod.collect_governance(DATA, owner, name)
        except Exception as e:  # review-ok: gate-network — retry on rerun
            status["governance"] = "fail"
            print(f"  WARN governance {owner}/{name}: {e}")
    return status


def _run_secs(w: dict) -> Optional[int]:
    """Wall-clock run seconds from run_timing only; the created/updated
    fallback spans queue days and inflates dx_ci_minutes (verified on
    rust-lang/hashbrown: 2884 min vs true ~10 min). None = unusable."""
    rt = w.get("run_timing") or {}
    if rt.get("runDurationMs"):
        return int(rt["runDurationMs"]) // 1000
    return None


def _shape_depsdev(d: Optional[dict]) -> Optional[dict]:
    if not d:
        return None
    sc = ((d.get("scorecard") or {}).get("overallScore"))
    return {"stars": d.get("starsCount"), "forks": d.get("forkCount"),
            "open_issues": d.get("openIssuesCount"),
            "license": d.get("licenseSpdxId"),
            "usedby_count": d.get("usedByCount"),
            "scorecard_overall": sc}


def _downloads(ecosystem: str, name: str,
               skip_pypistats: bool = False) -> tuple[Optional[int], str]:
    if ecosystem == "pypi":
        if skip_pypistats:
            return None, "pypistats-deferred"
        # pypistats 429s arrive as HTTP 200 + HTML body (unparseable -> None).
        # The SLEEP_PYPISTATS pre-call wait is the throttle (4/min < 5/min
        # limit); a transient null is refetched on rerun (nulls are not
        # marked complete), so no inline retry (it would waste 45s/repo).
        time.sleep(SLEEP_PYPISTATS)
        d = _get_json(f"https://pypistats.org/api/packages/{name}/recent",
                      {"User-Agent": "cvi-research/0.1 (ranking study)"})
        return ((d or {}).get("data") or {}).get("last_month"), "pypistats"
    if ecosystem == "npm":
        d = _get_json(f"https://api.npmjs.org/downloads/point/last-month/{name}")
        return (d or {}).get("downloads"), "npmjs"
    if ecosystem == "crates":
        d = _get_json(f"https://crates.io/api/v1/crates/{name}",
                      {"User-Agent": "cvi-research/0.1"})
        rd = (d or {}).get("crate", {}).get("recent_downloads")
        # recent_downloads is a trailing-90-day figure; normalize to monthly.
        return (int(rd) // 3 if rd is not None else None), "crates.io"
    return None, "no-source"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--repo", nargs=2, metavar=("OWNER", "NAME"))
    ap.add_argument("--ecosystem", default="pypi")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.repo:
        repos = [(a.repo[0], a.repo[1], a.ecosystem)]
    else:
        repos = []
        for line in CANDIDATES.read_text().splitlines()[: a.limit]:
            r = json.loads(line)
            if r.get("owner"):
                repos.append((r["owner"], r["name"], r["ecosystem"]))
    ok = fail = 0
    for owner, name, eco in repos:
        st = gather_repo(owner, name, eco, force=a.force)
        bad = [k for k, v in st.items() if v == "fail"]
        ok += not bad
        fail += bool(bad)
        print(f"{owner}/{name} [{eco}]: {'OK' if not bad else 'FAIL ' + ','.join(bad)}")
    print(f"done: {ok} clean, {fail} with failures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
