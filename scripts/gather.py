"""Gather verified raw data for candidate repos into data/raw/<collector>/.

One envelope per (collector, repo): {"fetched_at","source","data"}.
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

DATA = Path(__file__).resolve().parent.parent / "data" / "raw"
CANDIDATES = DATA / "candidates.jsonl"
GQL = "https://api.github.com/graphql"
SLEEP_REST = 0.15
SLEEP_KEYLESS = 0.35
SLEEP_PYPISTATS = 6.5  # pypistats.org: ~10 req/min per IP
PR_SAMPLE = 30
CONTRIB_PAGES = 2
COMMIT_PAGES = 3


def _env(collector: str, key: str, source: str, data: Any) -> Path:
    p = DATA / collector / f"{key}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": source, "data": data}), encoding="utf-8")
    return p


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


def gather_repo(owner: str, name: str, ecosystem: str, force: bool = False) -> dict[str, str]:
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

    # downloads (registry per ecosystem)
    dl_key = f"{ecosystem}__{name}"
    if not _done("downloads", dl_key, force):
        monthly, src = _downloads(ecosystem, name)
        _env("downloads", dl_key, src, {"monthly": monthly})
        status["downloads"] = "ok" if monthly is not None else "fail"
    else:
        status["downloads"] = "skip"
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


def _downloads(ecosystem: str, name: str) -> tuple[Optional[int], str]:
    if ecosystem == "pypi":
        time.sleep(SLEEP_PYPISTATS)
        d = _get_json(f"https://pypistats.org/api/packages/{name}/recent")
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
