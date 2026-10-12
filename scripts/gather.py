"""Gather verified raw data for candidate repos into data/raw/<collector>/.

One envelope per (collector, repo): {"source","fetched_at","http_status",
"etag","sha256","collector_version","data"} (the contract in porchlamp.collect.envelope).
Existence of the envelope = completed (checkpoint); --force refetches.
GitHub meta+PRs+first-reviews come from ONE aliased GraphQL request/repo;
contributors/commits/GFI/actions/tree via REST; deps.dev, OpenSSF scorecard
and registry download counts are keyless second sources.

Usage: .venv/bin/python scripts/gather.py --limit 5 [--collectors a,b] [--force]
       .venv/bin/python scripts/gather.py --all --collectors pr_window
(the second form is one breadth-first layer: that collector for every candidate)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from porchlamp.collect.commits import collect_commits_365d
from porchlamp.collect.envelope import write_envelope
# SLEEP_REST shares porchlamp.collect.pike_signals' SLEEP_CORE_SECONDS
# (both 0.15, the same GitHub core-API rate limit) -- one definition imported
# under this file's existing name.
from porchlamp.collect.pike_signals import SLEEP_CORE_SECONDS as SLEEP_REST
from porchlamp.collect.user_cache import UserCache
from porchlamp.paths import data_root

DATA = data_root() / "raw"
CANDIDATES = DATA / "candidates.jsonl"
GQL = "https://api.github.com/graphql"
SLEEP_KEYLESS = 0.35
SLEEP_PYPISTATS = 15.0  # pypistats.org: ~5 req/min/IP (429 arrives as HTTP 200 + HTML body)
PR_SAMPLE = 30
CONTRIB_PAGES = 2
COMMIT_PAGES = 3

#: Every envelope directory gather_repo() can write, in fetch order. ``--collectors``
#: takes a comma-separated subset of these. ``github_meta``/``pr_events``/``pr_reviews``
#: come from one GraphQL request, so naming any of the three runs that request.
COLLECTORS: tuple[str, ...] = (
    "github_meta", "pr_events", "pr_reviews", "contributors", "gfi", "actions_runs", "tree",
    "commits_180d", "depsdev", "scorecard", "downloads", "repo_object", "commits_365d",
    "dependents", "pr_window", "releases", "issues_counts", "runs", "governance",
    "author_history",
)
_META_BLOCK: frozenset[str] = frozenset({"github_meta", "pr_events", "pr_reviews"})


def parse_collectors(text: str) -> Optional[frozenset[str]]:
    """``--collectors`` value -> the selected set, or ``None`` for everything.

    An unknown name exits with the valid list: a typo must not quietly turn a
    breadth-first layer into a fetch-everything (depth-first) run.
    """
    names = frozenset(part.strip() for part in text.split(",") if part.strip())
    if not names:
        return None
    unknown = sorted(names - set(COLLECTORS))
    if unknown:
        raise SystemExit(f"unknown collector(s) {unknown}; valid: {', '.join(COLLECTORS)}")
    return names


def _env(collector: str, key: str, source: str, data: Any,
         http_status: int = 200) -> Path:
    """Write one envelope with its hash, status and collector version (`03` section 5).

    ``http_status`` records the status the transport actually saw. The ``200``
    default is for the paths whose transport raises on a non-2xx (``gh api``
    exits non-zero, ``urllib`` raises ``HTTPError``), so a response that reached
    them was accepted; a caller that swallows an error must pass the real status.
    """
    return write_envelope(
        DATA / collector / f"{key}.json",
        source=source,
        data=data,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        http_status=http_status,
    )


def _done(collector: str, key: str, force: bool) -> bool:
    return (DATA / collector / f"{key}.json").is_file() and not force


_USER_CACHE: Optional[UserCache] = None


def _user_cache() -> UserCache:
    """The D-COL-5 user cache (``08``), one per process, at
    ``<data_root>/state/users`` -- derived from ``DATA`` on every call, so
    a caller that points ``DATA`` elsewhere gets a cache beside it."""
    global _USER_CACHE
    root = DATA.parent / "state" / "users"
    if _USER_CACHE is None or _USER_CACHE.root != root:
        _USER_CACHE = UserCache(root)
    return _USER_CACHE


_REPO_SEGMENT_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_ALLOWED_FETCH_HOSTS: frozenset[str] = frozenset({
    "api.deps.dev",
    "api.securityscorecards.dev",
    "pypistats.org",
    "api.npmjs.org",
    "crates.io",
    "raw.githubusercontent.com",
    "ecosyste.ms",
    "packages.ecosyste.ms",
})


def _validate_repo_segment(value: str, label: str = "segment") -> str:
    if not isinstance(value, str) or value in {".", ".."} or not _REPO_SEGMENT_RE.match(value):
        raise ValueError(f"invalid GitHub {label}: {value!r}")
    return value


def _validate_gh_path(path: str) -> str:
    if (
        not isinstance(path, str)
        or not path
        or path.startswith("-")
        or any(ch in path for ch in ("\r", "\n", "\x00"))
        or ".." in path.split("/")
    ):
        raise ValueError(f"invalid gh api path: {path!r}")
    return path


def _validate_fetch_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _ALLOWED_FETCH_HOSTS
        or ".." in parsed.path.split("/")
    ):
        raise ValueError(f"disallowed fetch URL: {url!r}")
    return url


def _gh(*args: str) -> Any:
    if not args:
        raise ValueError("gh api requires a REST endpoint path")
    _validate_gh_path(args[0])
    out = subprocess.run(["gh", "api", *args], capture_output=True, text=True, timeout=90)
    if out.returncode != 0:
        raise RuntimeError(f"gh api {' '.join(args[:2])} rc={out.returncode}: {out.stderr[:160]}")
    return json.loads(out.stdout) if out.stdout.strip() else None


def _fetch_json(url: str, headers: Optional[dict] = None) -> tuple[Any, Optional[int], Optional[Exception]]:
    """Fetch ``url`` and report what happened, so a caller can tell a source
    that answered with nothing from a fetch that could not reach the source.

    Returns ``(body, http_status, error)``:
      * a 2xx answer: the decoded JSON ``body``, the real status, ``None``.
      * an HTTP error such as 404: ``(None, e.code, e)`` -- the source answered,
        so its status is known and a caller may record "not present" with it.
      * a transport failure (DNS, refused, timeout, undecodable body):
        ``(None, None, e)`` -- no status was ever received, so a caller must NOT
        mark the fetch done; the next run should retry.
    """
    _validate_fetch_url(url)
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return (json.load(r), getattr(r, "status", 200), None)
    except urllib.error.HTTPError as e:  # the source answered with a non-2xx status
        return (None, e.code, e)
    except Exception as e:  # review-ok: gate-network — no status => fetch not done, retry next run
        return (None, None, e)


def _get_json(url: str, headers: Optional[dict] = None) -> Optional[dict]:
    """Fetch ``url`` and return its JSON body, or ``None`` on any failure.

    Used by the registry download counters, where any failure means "no number"
    and a null is refetched on rerun, so the failure reason is not needed. The
    keyless metadata path uses :func:`_fetch_json` instead, so it can tell an
    absent source from a failed fetch and never stamp a failed fetch as a
    successful measurement.
    """
    return _fetch_json(url, headers)[0]


def gather_repo(owner: str, name: str, ecosystem: str, force: bool = False,
                skip_pypistats: bool = False,
                collectors: Optional[frozenset[str]] = None,
                *,
                graphql_bulk: bool = False) -> dict[str, str]:
    """Fetch every collector for one repo; return {collector: ok|skip|fail}.

    ``collectors`` restricts the call to those names -- one breadth-first
    layer, swept over every candidate before the next layer starts; every
    other collector is reported ``"skip"`` without touching a transport.
    ``None`` fetches everything, as before.
    """
    _validate_repo_segment(owner, "owner")
    _validate_repo_segment(name, "name")
    key = f"{owner}__{name}"
    status: dict[str, str] = {}

    def selected(collector: str) -> bool:
        return collectors is None or collector in collectors

    def done(collector: str) -> bool:
        return not selected(collector) or _done(collector, key, force)

    # --- 1 GraphQL point: meta + PR sample + first reviews ---
    if (collectors is None or collectors & _META_BLOCK) and not _done("github_meta", key, force):
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
        if done(collector):
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
    if not done("commits_180d"):
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
        if done(collector):
            status[collector] = "skip"
            continue
        time.sleep(SLEEP_KEYLESS)
        body, http_status, _error = _fetch_json(url, hdr)
        if http_status is None or http_status == 429 or http_status >= 500:
            # Could not reach the source (transport error) or got a transient
            # rate-limit / 5xx status: write nothing so the next run retries it
            # -- a failed fetch is not a completed one, and an envelope here
            # would mark the key permanently "done".
            status[collector] = "fail"
            continue
        # The source answered. A 2xx null or a 404 both mean "nothing here", but
        # the envelope carries the real status rather than a blanket 200.
        shaped = shape(body) if body else None
        _env(collector, key, url, shaped, http_status=http_status)
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
    if selected("downloads") and not (dl_monthly is not None and not force):
        monthly, src = _downloads(ecosystem, name, skip_pypistats=skip_pypistats)
        _env("downloads", dl_key, src, {"monthly": monthly})
        status["downloads"] = "ok" if monthly is not None else "fail"
    else:
        status["downloads"] = "skip"
    # Repo-object eligibility envelope (D-GATE-1/2/3/6)
    if done("repo_object"):
        status["repo_object"] = "skip"
    else:
        from porchlamp.collect.repo_object import collect_repo_object
        try:
            status["repo_object"] = collect_repo_object(DATA, owner, name, branch)
        except Exception as e:  # review-ok: gate-network — retry on rerun
            status["repo_object"] = "fail"
            print(f"  WARN repo_object {owner}/{name}: {e}")

    # --- Per-label newcomer-label GFI collection: when the gfi envelope is missing or
    # legacy, fetch the repo's label list once, query each alias-matching
    # label separately, and rewrite the envelope with labels_matched. ---
    from porchlamp.collect import labels as porchlamp_labels

    gfi_path = DATA / "gfi" / f"{key}.json"
    if selected("gfi") and porchlamp_labels.needs_gather(gfi_path, force):
        try:
            result = porchlamp_labels.collect_newcomer_issues(_gh, owner, name)
            porchlamp_labels.write_gfi_envelope(owner, name, result, gfi_path)
            status["gfi"] = "ok"
        except Exception as e:  # review-ok: gate-network — retry on rerun
            status["gfi"] = "fail"
            print(f"  WARN gfi {owner}/{name}: {e}")
    else:
        status["gfi"] = "skip"

    # --- commits_365d (365 d, cap 1000; author email/login/type) ---
    if not done("commits_365d"):
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
    # Package-level dependents — deps.dev primary, ecosyste.ms
    # fallback; "skip" = no registry mapping for this ecosystem (module
    # docstring of porchlamp.collect.depsdev), no envelope, no fetch.
    if done("dependents"):
        status["dependents"] = "skip"
    else:
        from porchlamp.collect import depsdev as depsdev_mod
        try:
            dep_kwargs: dict[str, Any] = {}
            if ecosystem == "maven":
                pom_xml: Optional[str] = None
                try:
                    import inspect as _inspect
                    from porchlamp.collect import repo_object as _ro_mod
                    ro_params = _inspect.signature(_ro_mod._fetch_body).parameters
                    if len(ro_params) >= 3:
                        pom_xml = _ro_mod._fetch_body(owner, name, "pom.xml")
                    else:
                        pom_xml = _ro_mod._fetch_body(
                            f"https://raw.githubusercontent.com/{owner}/{name}/HEAD/pom.xml"
                        )
                except Exception:  # review-ok: gate-network — fallback to _gh
                    pom_xml = None
                if pom_xml is None:
                    try:
                        import base64
                        res = _gh(f"repos/{owner}/{name}/contents/pom.xml")
                        if isinstance(res, dict) and res.get("content"):
                            pom_xml = base64.b64decode(res["content"]).decode("utf-8", "replace")
                        elif isinstance(res, str):
                            pom_xml = res
                    except Exception:  # review-ok: gate-network — unreadable pom.xml
                        pom_xml = None
                coords = depsdev_mod.parse_maven_coordinates(pom_xml) if pom_xml else None
                import inspect as _inspect
                cd_params = _inspect.signature(depsdev_mod.collect_dependents).parameters
                has_var_kw = any(
                    p.kind == _inspect.Parameter.VAR_KEYWORD for p in cd_params.values()
                )
                if "maven_coordinates" in cd_params or has_var_kw:
                    dep_kwargs["maven_coordinates"] = coords
                if "maven_pom_xml" in cd_params and "maven_coordinates" not in cd_params:
                    dep_kwargs["maven_pom_xml"] = pom_xml
            status["dependents"] = depsdev_mod.collect_dependents(
                DATA, owner, name, ecosystem, **dep_kwargs
            )
        except Exception as e:  # review-ok: gate-network — retry on rerun
            status["dependents"] = "fail"
            print(f"  WARN dependents {owner}/{name}: {e}")
    # Windowed PRs, typed actors (D-IN-1/2/4, 02 §4 1.1/1.4/4.3)
    if done("pr_window"):
        status["pr_window"] = "skip"
    else:
        from porchlamp.collect import github_graphql as prw_mod
        try:
            status["pr_window"] = prw_mod.collect_pr_window(DATA, owner, name)
        except Exception as e:  # review-ok: gate-network — retry on rerun
            status["pr_window"] = "fail"
            print(f"  WARN pr_window {owner}/{name}: {e}")
    # Downloads null is retry-able, not a hard failure (D-MISS-1):
    # pypi/npm/crates -> "pending" (pypi via scripts/backfill_downloads.py at
    # <= 4/min); go (deps.dev dependents only), maven, systems -> "unmeasured".
    if status.get("downloads") == "fail":
        try:
            import backfill_downloads as _bdl
        except ImportError:
            from scripts import backfill_downloads as _bdl
        status["downloads"] = ("pending" if ecosystem in _bdl.RETRYABLE_ECOSYSTEMS
                               else "unmeasured")
    # --- Releases, issues search counts, CI runs (02 §4 2.2 / 4.1) ---
    from porchlamp.collect import pike_signals as pike_mod
    for collector, call in (
        ("releases", lambda: pike_mod.collect_releases(DATA, owner, name, force=force)),
        (
            "issues_counts",
            lambda: (
                pike_mod.collect_issue_counts_graphql(DATA, owner, name, force=force)
                if graphql_bulk
                else pike_mod.collect_issue_counts(DATA, owner, name, force=force)
            ),
        ),
        ("runs", lambda: pike_mod.collect_runs(DATA, owner, name, branch, force=force)),
    ):
        if done(collector):
            status[collector] = "skip"
            continue
        try:
            status[collector] = call()
        except Exception as e:  # review-ok: gate-network — retry on rerun
            status[collector] = "fail"
            print(f"  WARN {collector} {owner}/{name}: {e}")
    # --- Governance evidence envelope (02 §4 3.1/3.3/4.2) — check
    # names on the 20 newest merged PRs, 365 d sign-off commits and the
    # GOVERNANCE/MAINTAINERS/CONTRIBUTING/SECURITY + AI-policy files. ---
    if done("governance"):
        status["governance"] = "skip"
    else:
        from porchlamp.collect import governance as gov_mod
        try:
            if graphql_bulk:
                from porchlamp.collect.github_graphql import GhCliTransport

                status["governance"] = gov_mod.collect_governance(
                    DATA, owner, name, force=force, graphql_transport=GhCliTransport()
                )
            else:
                status["governance"] = gov_mod.collect_governance(
                    DATA, owner, name, force=force
                )
        except Exception as e:  # review-ok: gate-network — retry on rerun
            status["governance"] = "fail"
            print(f"  WARN governance {owner}/{name}: {e}")
    # Author history + cohorts (D-IN-3/D-IN-4, 02 §4 1.2/1.3/5.2/5.3)
    if done("author_history"):
        status["author_history"] = "skip"
    else:
        from porchlamp.collect import github_graphql as cohort_mod
        try:
            status["author_history"] = cohort_mod.collect_author_history(
                DATA, owner, name, user_cache=_user_cache(), force=force)
        except Exception as e:  # review-ok: gate-network — retry on rerun
            status["author_history"] = "fail"
            print(f"  WARN author_history {owner}/{name}: {e}")
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
        pkg = re.sub(r"[-_.]+", "-", name).lower()
        d = _get_json(f"https://pypistats.org/api/packages/{pkg}/recent",
                      {"User-Agent": "porchlamp-research/0.1 (ranking study)"})
        return ((d or {}).get("data") or {}).get("last_month"), "pypistats"
    if ecosystem == "npm":
        d = _get_json(f"https://api.npmjs.org/downloads/point/last-month/{name}")
        return (d or {}).get("downloads"), "npmjs"
    if ecosystem == "crates":
        d = _get_json(f"https://crates.io/api/v1/crates/{name}",
                      {"User-Agent": "porchlamp-research/0.1"})
        rd = (d or {}).get("crate", {}).get("recent_downloads")
        # recent_downloads is a trailing-90-day figure; normalize to monthly.
        return (int(rd) // 3 if rd is not None else None), "crates.io"
    return None, "no-source"


def read_priority_keys(path: Path) -> list[str]:
    """``owner__name`` keys, one per line; blank lines and ``#`` comments ignored."""
    return [line.strip() for line in path.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def order_by_priority(repos: list[tuple[str, str, str]], keys: list[str],
                      ) -> tuple[list[tuple[str, str, str]], int]:
    """``repos`` with every repo whose ``owner__name`` is in ``keys`` moved first.

    Both segments keep candidate order, and no repo is dropped or repeated:
    this decides only WHEN a repo is gathered, never WHETHER. Returns the
    reordered list and the length of the priority segment.
    """
    wanted = set(keys)
    first = [r for r in repos if f"{r[0]}__{r[1]}" in wanted]
    rest = [r for r in repos if f"{r[0]}__{r[1]}" not in wanted]
    return first + rest, len(first)


#: Printed once the priority segment has been gathered. Worded so that it can
#: never match the per-run summary the BFS runner greps for
#: (``done: .* clean, .* with failures``): a run killed after this line must
#: not read as a finished run.
PRIORITY_DONE_MARKER = "priority segment finished"


def _fingerprint_state() -> Path:
    """Per-repo fingerprint state of the ``--dirty`` pass (D-COL-1), beside ``DATA``."""
    return DATA.parent / "state" / "fingerprints.jsonl"


def _persist_fingerprint(fp_result: Any, owner: str, name: str, ecosystem: str,
                         status: dict[str, str], started: datetime,
                         refetched: Any) -> None:
    """Save the repo's new fingerprint after its gather, if nothing it needs failed.

    A collector counts as fetched when its envelope was rewritten during this
    gather (``fetched_at >= started``): a source that answered "nothing here"
    (a scorecard 404) writes an envelope but reports ``fail``, and must not hold
    the fingerprint back forever. A collector that failed and wrote nothing
    keeps the repo's old fingerprint, so the next ``--dirty`` run refetches it.
    """
    from porchlamp.collect.fingerprint import (
        append_fingerprints,
        envelope_fetched_at,
        fingerprint_to_persist,
    )

    key = f"{owner}__{name}"
    fp = fp_result.fingerprints.get(key)
    if fp is None:
        return
    fresh: set[str] = set()
    failed: set[str] = set()
    for collector in refetched:
        fetched = envelope_fetched_at(DATA, collector, owner, name, ecosystem)
        if fetched is not None and fetched >= started:
            fresh.add(collector)
        elif status.get(collector) in (None, "fail"):
            failed.add(collector)
    rec = fingerprint_to_persist(
        fp_result.previous.get(key), fp, fp_result.invalidated_by_repo.get(key, frozenset()),
        fresh, failed,
    )
    if rec is not None:
        append_fingerprints(_fingerprint_state(), [rec])


def main(argv: Optional[list[str]] = None) -> int:
    from porchlamp.collect.fingerprint import ALL_GATHER_COLLECTORS

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--repo", nargs=2, metavar=("OWNER", "NAME"))
    ap.add_argument("--ecosystem", default="pypi")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--all", action="store_true",
                    help="every candidate in candidates.jsonl (--limit is ignored)")
    ap.add_argument("--collectors", default="",
                    help="comma-separated subset of: " + ",".join(COLLECTORS)
                         + " -- one breadth-first layer; default: everything")
    ap.add_argument("--priority-keys", type=Path, default=None,
                    help="file of owner__name keys to gather FIRST; every other "
                         "repo still follows (ordering only, nothing is skipped)")
    ap.add_argument("--dirty", action="store_true",
                    help="run the 25-repo/call GraphQL fingerprint pass and fetch only "
                         "the collectors it, a missing envelope or a clock marks dirty "
                         "(D-COL-1/D-COL-3/D-COL-4, W6-2); a repo the pass cannot "
                         "resolve is dirty. A repo's new fingerprint is saved only "
                         "after its collectors succeeded (and only without --collectors)")
    ap.add_argument("--fingerprint-only", action="store_true",
                    help="run the fingerprint pass, report what is dirty, and exit. "
                         "Writes nothing: it must not consume a change that a later "
                         "gather would need")
    ap.add_argument("--graphql-bulk", action="store_true",
                    help="use single-call GraphQL collectors for governance and "
                         "issues_counts instead of multi-call REST / Search API")
    a = ap.parse_args(argv)
    selected = parse_collectors(a.collectors)
    if a.repo:
        repos = [(a.repo[0], a.repo[1], a.ecosystem)]
    else:
        repos = []
        lines = CANDIDATES.read_text().splitlines()
        for line in (lines if a.all else lines[: a.limit]):
            r = json.loads(line)
            if r.get("owner"):
                repos.append((r["owner"], r["name"], r["ecosystem"]))
    n_first = 0
    if a.priority_keys is not None:
        keys = read_priority_keys(a.priority_keys)
        repos, n_first = order_by_priority(repos, keys)
        print(f"priority: {n_first} of {len(repos)} repos first ({a.priority_keys}; "
              f"{len(set(keys)) - n_first} listed keys are not candidates)")

    dirty_map: Optional[dict[str, frozenset[str]]] = None
    fp_result: Any = None
    if a.dirty or a.fingerprint_only:
        from porchlamp.collect.fingerprint import run_fingerprint_pass

        fp_result = run_fingerprint_pass(
            [(owner, name) for owner, name, _eco in repos],
            state_file=_fingerprint_state(),
            raw_dir=DATA,
            ecosystems={f"{owner}__{name}": eco for owner, name, eco in repos},
        )
        dirty_count = sum(1 for inv in fp_result.dirty_by_repo.values() if inv)
        print(
            f"fingerprint: {len(fp_result.fingerprints)} resolved, "
            f"{len(fp_result.unresolved_keys)} UNRESOLVED (dirty), "
            f"{dirty_count} dirty, {len(fp_result.dirty_by_repo) - dirty_count} clean, "
            f"{fp_result.queries_executed} graphql queries ({fp_result.cost_points} pts)"
        )
        for key in fp_result.unresolved_keys[:20]:
            print(f"  UNRESOLVED {key}")
        if a.fingerprint_only:
            return 0
        dirty_map = fp_result.dirty_by_repo

    ok = fail = 0
    print(f"collectors: {'all' if selected is None else ','.join(sorted(selected))}; repos: {len(repos)}")
    for i, (owner, name, eco) in enumerate(repos, 1):
        key = f"{owner}__{name}"
        repo_collectors = selected
        repo_force = a.force
        if dirty_map is not None and not a.force:
            inv = dirty_map.get(key, ALL_GATHER_COLLECTORS)
            if not inv:
                ok += 1
                print(f"{owner}/{name} [{eco}]: SKIP (clean fingerprint)")
                if i == n_first:
                    print(f"{PRIORITY_DONE_MARKER}: {n_first} repos ({fail} failed)", flush=True)
                continue
            repo_collectors = inv if selected is None else (selected & inv)
            if not repo_collectors:
                ok += 1
                print(f"{owner}/{name} [{eco}]: SKIP (selected collectors clean)")
                if i == n_first:
                    print(f"{PRIORITY_DONE_MARKER}: {n_first} repos ({fail} failed)", flush=True)
                continue
            repo_force = True
        started = datetime.now(timezone.utc)
        try:
            if a.graphql_bulk:
                st = gather_repo(
                    owner, name, eco, force=repo_force, collectors=repo_collectors,
                    graphql_bulk=True
                )
            else:
                st = gather_repo(owner, name, eco, force=repo_force, collectors=repo_collectors)
        except Exception as e:  # review-ok: gate-network — isolate unexpected per-repo error during sweep
            fail += 1
            print(f"{owner}/{name} [{eco}]: FAIL {e}")
        else:
            bad = [k for k, v in st.items() if v == "fail"]
            ok += not bad
            fail += bool(bad)
            print(f"{owner}/{name} [{eco}]: {'OK' if not bad else 'FAIL ' + ','.join(bad)}")
            if fp_result is not None and selected is None:
                _persist_fingerprint(fp_result, owner, name, eco, st, started,
                                     refetched=ALL_GATHER_COLLECTORS if a.force
                                     else repo_collectors)
        if i == n_first:
            print(f"{PRIORITY_DONE_MARKER}: {n_first} repos ({fail} failed)", flush=True)
    if _USER_CACHE is not None:
        print(_USER_CACHE.summary())
    print(f"done: {ok} clean, {fail} with failures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
