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
import subprocess  # noqa: F401  (tests patch this module's subprocess)
import contextlib
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from porchlamp.collect import ci_runs, ghcli, net, validate
from porchlamp.collect.commits import (
    can_derive_180d_from_365d,
    collect_commits_365d,
    commit_total_fetcher,
    derive_commits_180d_rows,
)
from porchlamp.collect.envelope import write_envelope
from porchlamp.collect.names import valid_ref
# SLEEP_REST shares porchlamp.collect.pike_signals' SLEEP_CORE_SECONDS
# (both 0.15, the same GitHub core-API rate limit) -- one definition imported
# under this file's existing name.
from porchlamp.collect.pike_signals import SLEEP_CORE_SECONDS as SLEEP_REST
from porchlamp.collect.user_cache import UserCache
from porchlamp.core.constants import PARAM_MIN_N
from porchlamp.paths import data_root

try:
    from porchlamp.collect import cost
except ImportError:
    cost = None


@contextlib.contextmanager
def _collector_scope(key: str, collector: str):
    if cost is not None and hasattr(cost, "collector_scope"):
        with cost.collector_scope(key, collector):
            yield
    else:
        yield


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
    "github_meta", "pr_events", "pr_reviews", "tree", "contributors", "repo_object",
    "commits_180d", "pr_window", "actions_runs", "depsdev", "scorecard", "downloads",
    "gfi", "commits_365d", "dependents", "releases", "issues_counts", "runs",
    "governance", "author_history",
)
_META_BLOCK: frozenset[str] = frozenset({"github_meta", "pr_events", "pr_reviews"})

#: Said in the help of the two opt-in ledger flags and on stderr whenever either is active.
EXPERIMENTAL = "EXPERIMENTAL: not equivalent to a full fetch; do not use for a published snapshot."


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
         http_status: int = 200, fetched_at: Optional[str] = None) -> Path:
    """Write one envelope with its hash, status and collector version (`03` section 5).

    ``http_status`` records the status the transport actually saw. The ``200``
    default is for the paths whose transport raises on a non-2xx (``gh api``
    exits non-zero, ``urllib`` raises ``HTTPError``), so a response that reached
    them was accepted; a caller that swallows an error must pass the real status.
    ``fetched_at`` defaults to now; a value derived from another envelope passes that one's.
    """
    return write_envelope(
        DATA / collector / f"{key}.json",
        source=source,
        data=data,
        fetched_at=fetched_at or datetime.now(timezone.utc).isoformat(),
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


#: Hosts :func:`_fetch_json` may reach: the keyless sources the collectors read.
FETCH_HOSTS: frozenset[str] = frozenset({
    "api.deps.dev",
    "api.securityscorecards.dev",
    "pypistats.org",
    "api.npmjs.org",
    "crates.io",
    "ecosyste.ms",
    "packages.ecosyste.ms",
}) | validate.RAW_GITHUB_HOSTS


def _gh(*args: str) -> Any:
    if not args:
        raise ValueError("gh api requires a REST endpoint path")
    validate.gh_api_path(args[0])
    out = ghcli.run(["api", *args], timeout=90)
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
    validate.https_url(url, FETCH_HOSTS, "fetch")
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with net.urlopen(req, 30) as r:
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
                graphql_bulk: bool = False,
                skip_ineligible: bool = False,
                incremental_ledgers: bool = False,
                light_author_history: bool = False) -> dict[str, Any]:
    """Fetch every collector for one repo; return {collector: ok|skip|fail}.

    ``collectors`` restricts the call to those names -- one breadth-first
    layer, swept over every candidate before the next layer starts; every
    other collector is reported ``"skip"`` without touching a transport.
    ``None`` fetches everything, as before.
    """
    validate.github_segment(owner, "owner")
    validate.github_segment(name, "name")
    key = f"{owner}__{name}"
    status: dict[str, Any] = {}

    def selected(collector: str) -> bool:
        return collectors is None or collector in collectors

    def done(collector: str) -> bool:
        return not selected(collector) or _done(collector, key, force)

    # =========================================================================
    # Phase 1: Cheap / Gate Collectors
    # =========================================================================

    # --- 1 GraphQL point: meta + PR sample + first reviews ---
    if (collectors is None or collectors & _META_BLOCK) and not _done("github_meta", key, force):
        with _collector_scope(key, "github_meta"):
            q = (
                'query($owner: String!, $name: String!) {'
                ' r: repository(owner: $owner, name: $name) {'
                'stargazerCount forkCount pushedAt isArchived createdAt'
                ' licenseInfo { spdxId } defaultBranchRef { name }'
                ' pullRequests(states: [OPEN, MERGED, CLOSED], first: %d,'
                ' orderBy: {field: CREATED_AT, direction: DESC}) {'
                ' nodes { number createdAt closedAt mergedAt'
                ' author { login } authorAssociation'
                ' comments { totalCount } reviews(first: 1) { nodes { createdAt } }'
                ' } } } }' % (PR_SAMPLE,)
            )
            try:
                out = ghcli.run(["api", "graphql", "-f", f"query={q}", "-f", f"owner={owner}", "-f", f"name={name}"],
                                timeout=90)
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

    # --- tree ---
    if done("tree"):
        status["tree"] = "skip"
    else:
        with _collector_scope(key, "tree"):
            time.sleep(SLEEP_REST)
            try:
                tree_res = _gh(f"repos/{owner}/{name}/git/trees/{branch}", "-X", "GET", "-f", "recursive=1")
                tree_data = {
                    "paths": [t["path"] for t in (tree_res or {}).get("tree", [])],
                    "truncated": bool((tree_res or {}).get("truncated")),
                }
                _env("tree", key, f"github-rest:repos/{owner}/{name}/git/trees/{branch}", tree_data)
                status["tree"] = "ok"
            except Exception as e:  # review-ok: gate-network — per-collector retry on rerun
                status["tree"] = "fail"
                print(f"  WARN tree {owner}/{name}: {e}")

    # --- contributors ---
    if done("contributors"):
        status["contributors"] = "skip"
    else:
        with _collector_scope(key, "contributors"):
            time.sleep(SLEEP_REST)
            try:
                contrib_res = _gh(f"repos/{owner}/{name}/contributors", "-X", "GET", "-f", "per_page=100", "-f", "anon=1")
                contrib_data = [
                    {"login": c.get("login") or c.get("email") or "?", "contributions": c.get("contributions", 0)}
                    for c in contrib_res or []
                ]
                _env("contributors", key, f"github-rest:repos/{owner}/{name}/contributors", contrib_data)
                status["contributors"] = "ok"
            except Exception as e:  # review-ok: gate-network — per-collector retry on rerun
                status["contributors"] = "fail"
                print(f"  WARN contributors {owner}/{name}: {e}")

    # --- Repo-object eligibility envelope (D-GATE-1/2/3/6) ---
    if done("repo_object"):
        status["repo_object"] = "skip"
    else:
        with _collector_scope(key, "repo_object"):
            from porchlamp.collect.repo_object import collect_repo_object
            try:
                status["repo_object"] = collect_repo_object(DATA, owner, name, branch)
            except Exception as e:  # review-ok: gate-network — retry on rerun
                status["repo_object"] = "fail"
                print(f"  WARN repo_object {owner}/{name}: {e}")

    # --- commits_180d (paged, capped) ---
    if not done("commits_180d"):
        with _collector_scope(key, "commits_180d"):
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

    # --- Windowed PRs, typed actors (D-IN-1/2/4, 02 §4 1.1/1.4/4.3) ---
    if done("pr_window"):
        status["pr_window"] = "skip"
    else:
        with _collector_scope(key, "pr_window"):
            from porchlamp.collect import github_graphql as prw_mod
            try:
                if incremental_ledgers:
                    status["pr_window"] = prw_mod.collect_pr_window(
                        DATA, owner, name, force=force, incremental_ledger=True
                    )
                else:
                    status["pr_window"] = prw_mod.collect_pr_window(DATA, owner, name, force=force)
            except Exception as e:  # review-ok: gate-network — retry on rerun
                status["pr_window"] = "fail"
                print(f"  WARN pr_window {owner}/{name}: {e}")

    # =========================================================================
    # Gate Stop: Confirmed Black-Hole Early Return (W6-6)
    # =========================================================================
    if skip_ineligible:
        from porchlamp.collect.fingerprint import is_confirmed_black_hole_in_raw

        if is_confirmed_black_hole_in_raw(DATA, owner, name):
            status["skipped_deep_black_hole"] = True
            return status

    # =========================================================================
    # Phase 2: Deep Collectors
    # =========================================================================

    # actions_runs (paged: the settled pull_request runs behind 5.4's CI wait)
    if done("actions_runs"):
        status["actions_runs"] = "skip"
    else:
        with _collector_scope(key, "actions_runs"):
            time.sleep(SLEEP_REST)
            try:
                _env("actions_runs", key, f"github-rest:repos/{owner}/{name}/actions/runs",
                     _fetch_pr_runs(owner, name))
                status["actions_runs"] = "ok"
            except Exception as e:  # review-ok: gate-network — per-collector retry on rerun
                status["actions_runs"] = "fail"
                print(f"  WARN actions_runs {owner}/{name}: {e}")

    # keyless second sources: depsdev & scorecard
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
        with _collector_scope(key, collector):
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
        dl_data = (json.loads(dl_p.read_text()).get("data") or {}) if dl_p.is_file() else {}
    except (json.JSONDecodeError, OSError):  # review-ok: gate-cache — corrupt -> refetch
        dl_data = {}
    dl_monthly = dl_data.get("monthly") if isinstance(dl_data, dict) else None
    dep_resolution = None
    with _collector_scope(key, "downloads"):
        if selected("downloads") and ecosystem in _DOWNLOAD_ECOSYSTEMS:
            # The count is the repo's only if it is the count of the package deps.dev
            # links to the repo. The file stays keyed <ecosystem>__<repo name>
            # (verify.py pins that key); the package it counted and the repo that
            # wrote it are recorded inside, and derive.py compares them.
            from porchlamp.collect.depsdev import package_key, package_name_for_repo
            verdict, package, dep_resolution = _repo_package(owner, name, ecosystem)
            if verdict == "unsettled":
                status["downloads"] = "fail"
            elif verdict == "none":
                status["downloads"] = "skip"
            else:
                assert package is not None
                counted = dl_data.get("package") or package_name_for_repo(owner, name, ecosystem)
                held_by = dl_data.get("repo")
                if dl_monthly is not None and not force \
                        and package_key(ecosystem, str(counted)) == package_key(ecosystem, package):
                    status["downloads"] = "skip"
                elif dl_monthly is not None and not force and held_by not in (None, f"{owner}/{name}"):
                    # Another repo of the same name already holds this shared file with
                    # its own package's count; a rerun must not flip it back and forth.
                    status["downloads"] = "skip"
                else:
                    monthly, src = _downloads(ecosystem, package, skip_pypistats=skip_pypistats)
                    _env("downloads", dl_key, src,
                         {"monthly": monthly, "package": package, "repo": f"{owner}/{name}"})
                    status["downloads"] = "ok" if monthly is not None else "fail"
        elif selected("downloads") and not (dl_monthly is not None and not force):
            monthly, src = _downloads(ecosystem, name, skip_pypistats=skip_pypistats)
            _env("downloads", dl_key, src, {"monthly": monthly})
            status["downloads"] = "ok" if monthly is not None else "fail"
        else:
            status["downloads"] = "skip"

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

    # --- Per-label newcomer-label GFI collection: when the gfi envelope is missing or
    # legacy, fetch the repo's label list once, query each alias-matching
    # label separately, and rewrite the envelope with labels_matched. ---
    from porchlamp.collect import labels as porchlamp_labels

    gfi_path = DATA / "gfi" / f"{key}.json"
    if selected("gfi") and porchlamp_labels.needs_gather(gfi_path, force):
        with _collector_scope(key, "gfi"):
            try:
                result = porchlamp_labels.collect_newcomer_issues(_gh, owner, name)
                porchlamp_labels.write_gfi_envelope(owner, name, result, gfi_path)
                status["gfi"] = "ok"
            except Exception as e:  # review-ok: gate-network — retry on rerun
                status["gfi"] = "fail"
                print(f"  WARN gfi {owner}/{name}: {e}")
    else:
        status["gfi"] = "skip"

    # --- commits_365d (365 d: every commit up to COMMIT_CAP, else an evenly
    # spaced sample with GitHub's count of the year, RFC-0021; author
    # email/login/type and sha) ---
    if not done("commits_365d"):
        with _collector_scope(key, "commits_365d"):
            def _page(page: int, since: str) -> list[dict]:
                time.sleep(SLEEP_REST)
                return _gh(f"repos/{owner}/{name}/commits", "-X", "GET",
                           "-f", "per_page=100", "-f", f"since={since}",
                           "-f", f"page={page}") or []

            from porchlamp.collect.github_graphql import GhCliTransport
            _total = commit_total_fetcher(GhCliTransport(), owner, name)
            try:
                existing_c365: Optional[list[dict]] = None
                existing_c365_truncated: bool = False
                if incremental_ledgers:
                    c365_p = DATA / "commits_365d" / f"{key}.json"
                    if c365_p.is_file():
                        try:
                            prev_c365 = json.loads(c365_p.read_text(encoding="utf-8")).get("data")
                            # A sampled record is pages spread over the year: new
                            # commits cannot be merged into it (RFC-0021).
                            if isinstance(prev_c365, dict) and prev_c365.get("sampled") is not True:
                                rows_or_commits = prev_c365.get("rows") or prev_c365.get("commits")
                                if isinstance(rows_or_commits, list):
                                    existing_c365 = rows_or_commits
                                    existing_c365_truncated = bool(prev_c365.get("truncated"))
                        except Exception:
                            existing_c365 = None
                if existing_c365 is not None:
                    c365_data = collect_commits_365d(
                        _page,
                        fetch_total=_total,
                        existing_rows=existing_c365,
                        existing_truncated=existing_c365_truncated,
                    )
                else:
                    c365_data = collect_commits_365d(_page, fetch_total=_total)
                c365_at = datetime.now(timezone.utc).isoformat()
                _env("commits_365d", key, "github-rest:commits", c365_data, fetched_at=c365_at)
                status["commits_365d"] = "ok"
                # Materialize commits_180d if missing and derivable, from the commits_365d fetched just now
                c180_path = DATA / "commits_180d" / f"{key}.json"
                if not c180_path.is_file() and can_derive_180d_from_365d(c365_data):
                    _env("commits_180d", key, "derived:commits_365d", derive_commits_180d_rows(c365_data),
                         fetched_at=c365_at)
                    if status.get("commits_180d") in (None, "fail", "skip") and selected("commits_180d"):
                        status["commits_180d"] = "ok"
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
        with _collector_scope(key, "dependents"):
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
                if dep_resolution is not None:
                    dep_kwargs["resolution"] = dep_resolution
                status["dependents"] = depsdev_mod.collect_dependents(
                    DATA, owner, name, ecosystem, **dep_kwargs
                )
            except Exception as e:  # review-ok: gate-network — retry on rerun
                status["dependents"] = "fail"
                print(f"  WARN dependents {owner}/{name}: {e}")

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
        with _collector_scope(key, collector):
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
        with _collector_scope(key, "governance"):
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
        with _collector_scope(key, "author_history"):
            from porchlamp.collect import github_graphql as cohort_mod
            try:
                if light_author_history:
                    status["author_history"] = cohort_mod.collect_author_history(
                        DATA, owner, name, user_cache=_user_cache(), force=force, light_history=True
                    )
                else:
                    status["author_history"] = cohort_mod.collect_author_history(
                        DATA, owner, name, user_cache=_user_cache(), force=force)
            except Exception as e:  # review-ok: gate-network — retry on rerun
                status["author_history"] = "fail"
                print(f"  WARN author_history {owner}/{name}: {e}")
    return status


gather_one = gather_repo


def _fetch_pr_runs(owner: str, name: str, now: Optional[datetime] = None) -> list[dict]:
    """The newest settled ``pull_request`` runs, as ``ci_runs.shape_run``
    rows (D-IN-4 "CI runs"): runs created at least ``ci_runs.SETTLE_HOURS``
    before ``now``. One page of 100 answers most repositories; a further page
    is fetched only while fewer commits than the 5.4 minimum sample have a
    usable CI wait, up to ``ci_runs.MAX_PAGES``."""
    created = ci_runs.created_filter(now or datetime.now(timezone.utc))
    rows: list[dict] = []
    for page in range(1, ci_runs.MAX_PAGES + 1):
        if page > 1:
            time.sleep(SLEEP_REST)
        listing = _gh(f"repos/{owner}/{name}/actions/runs", "-X", "GET",
                      "-f", f"per_page={ci_runs.PAGE_SIZE}", "-f", "event=pull_request",
                      "-f", f"created={created}", "-f", f"page={page}") or {}
        batch = listing.get("workflow_runs", [])
        rows.extend(ci_runs.shape_run(run) for run in batch)
        more_exist = len(batch) == ci_runs.PAGE_SIZE
        complete = ci_runs.complete_commits(rows, more_exist=more_exist)
        if not more_exist or len(ci_runs.commit_waits(complete)) >= PARAM_MIN_N["dx"]:
            return complete
    return complete


def _shape_depsdev(d: Optional[dict]) -> Optional[dict]:
    if not d:
        return None
    sc = ((d.get("scorecard") or {}).get("overallScore"))
    return {"stars": d.get("starsCount"), "forks": d.get("forkCount"),
            "open_issues": d.get("openIssuesCount"),
            "license": d.get("licenseSpdxId"),
            "usedby_count": d.get("usedByCount"),
            "scorecard_overall": sc}


#: Ecosystems whose registry has a downloads source (:func:`_downloads`).
_DOWNLOAD_ECOSYSTEMS: frozenset[str] = frozenset({"pypi", "npm", "crates"})


def _repo_package(owner: str, name: str, ecosystem: str) -> tuple[str, Optional[str], Any]:
    """The registry package whose downloads belong to this repo.

    A download count is a package's, and the repo name is only a guess at the
    package name (npm ``router`` is 291 M downloads a month, credited to three
    repos that are not its owner). Returns ``(verdict, package, resolution)``:

    * ``("package", P, r)``: deps.dev links package ``P`` to the repo. Read from
      the repo's dependents envelope when the attributing resolver wrote it
      (it records ``guess_linked``); otherwise resolved now, and ``r`` is that
      result so the dependents collector does not repeat the lookups.
    * ``("none", None, r)``: the repo has no linked package (or its source-repo
      map was cut and showed none): there are no downloads to fetch.
    * ``("unsettled", None, r)``: deps.dev did not answer; retry later.
    """
    from porchlamp.collect import depsdev as depsdev_mod

    try:
        data = (json.loads((DATA / "dependents" / f"{owner}__{name}.json").read_text())
                .get("data") or {})
    except (OSError, ValueError):
        data = {}
    if isinstance(data, dict) and "guess_linked" in data:
        package = data.get("package")
        if isinstance(package, str) and package:
            return "package", package, None
        return "none", None, None
    resolution = depsdev_mod.resolve_repo_package(owner, name, ecosystem)
    if resolution is None:
        return "none", None, None
    if resolution.verdict == "found" and resolution.package:
        return "package", resolution.package, resolution
    if resolution.verdict in ("no_package", "unresolved"):
        return "none", None, resolution
    return "unsettled", None, resolution


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


def should_skip_ineligible(
    owner: str,
    name: str,
    *,
    raw_dir: Path,
    now: datetime,
    fp: Optional[Any] = None,
) -> Optional[str]:
    """Return a :data:`porchlamp.collect.fingerprint.PRUNED_REASONS` string when
    ``owner/name`` is already known to be closed for contributions or not
    software (``archived``, ``disabled``, ``stale``, ``unlicensed``,
    ``non_software``, ``no_external_workflow``), or ``None`` if collection
    should proceed. ``black_hole`` repositories are never skipped here."""
    from porchlamp.collect.fingerprint import classify_prune_reason

    return classify_prune_reason(fp, raw_dir=raw_dir, owner=owner, name=name, now=now)


def _example(value: Any) -> str:
    """A short, inert rendering of a rejected name: illegal characters become ``?``, at most 20 characters."""
    return re.sub(r"[^A-Za-z0-9._-]", "?", str(value))[:20]


def load_candidates(lines: list[str], limit: Optional[int]) -> list[tuple[str, str, str]]:
    """``(owner, name, ecosystem)`` for the first ``limit`` lines (all when None).

    A line whose owner or name is not a legal GitHub name is skipped, never passed on; the count and at most five
    examples (owner/name only, never the whole line) are printed.
    """
    repos: list[tuple[str, str, str]] = []
    skipped: list[str] = []
    for line in (lines if limit is None else lines[:limit]):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            r = None
        if not isinstance(r, dict):
            skipped.append("<unparseable line>")
            continue
        if not r.get("owner"):
            continue
        if not valid_ref(r.get("owner"), r.get("name")):
            skipped.append(f"{_example(r.get('owner'))}/{_example(r.get('name'))}")
            continue
        repos.append((r["owner"], r["name"], r["ecosystem"]))
    if skipped:
        print(f"skipped {len(skipped)} invalid candidates (e.g. {', '.join(skipped[:5])})")
    return repos


def _repair_replies(owner: str, name: str, eco: str) -> bool:
    """One repo of ``--repair-replies``: read again the PRs of its ``pr_window`` record that the query before RFC-0022
    cut short (:func:`porchlamp.collect.github_graphql.repair_pr_window_replies`). Prints the repo line; False when it
    failed, so the next run reads it again."""
    from porchlamp.collect import github_graphql as prw_mod

    try:
        with _collector_scope(f"{owner}__{name}", "pr_window"):
            repaired = prw_mod.repair_pr_window_replies(DATA, owner, name)
    except Exception as e:  # review-ok: gate-network — isolate unexpected per-repo error during sweep
        print(f"{owner}/{name} [{eco}]: FAIL {e}")
        return False
    print(f"{owner}/{name} [{eco}]: {'OK' if repaired == 'ok' else 'SKIP (no cut record)'}")
    return True


def main(argv: Optional[list[str]] = None) -> int:
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
    ap.add_argument("--graphql-bulk", action="store_true", default=True,
                    help="use single-call GraphQL collectors for governance and "
                         "issues_counts instead of multi-call REST / Search API (default: True)")
    ap.add_argument("--no-graphql-bulk", dest="graphql_bulk", action="store_false",
                    help="disable single-call GraphQL collectors; use REST / Search API")
    ap.add_argument("--skip-ineligible", action="store_true",
                    help="skip archived, disabled, stale, unlicensed, non_software, and "
                         "no_external_workflow repos before running downstream collectors")
    ap.add_argument("--refetch-cut", action="store_true",
                    help="fetch again, and only, the stored records an older collector cut short "
                         "(commits_365d at 1,000, author_history and pr_window at 500): the repos that have "
                         "one, and of each only its cut collectors; --collectors narrows it further. "
                         "A record cut at today's cap is left alone")
    ap.add_argument("--repair-replies", action="store_true",
                    help="read again, and only, the pull requests of each stored pr_window record whose reply lists "
                         "or timeline the query before RFC-0022 stopped at its size (reviews 10, comments 20, "
                         "timeline 5), by PR number, and stamp the record with the current query: about a point per "
                         "25 pull requests, not a refetch of the window. A record of any other query is left alone")
    ap.add_argument("--screen-ineligible", action="store_true",
                    help="before the first layer of a cycle: ask GitHub (the fingerprint pass) about each repo "
                         "whose selected collectors are not all done, and skip the archived, disabled and idle "
                         "ones, and those the slow facts of the last cycle rule out (unlicensed, non_software, "
                         "mirror). Never prunes on last cycle's commits or pull requests; "
                         "--skip-ineligible is for the layers after this one")
    ap.add_argument("--incremental-ledgers", action="store_true",
                    help=EXPERIMENTAL + " Merge delta PRs and commits into existing envelopes (W6-3/W6-4)")
    ap.add_argument("--light-author-history", action="store_true",
                    help=EXPERIMENTAL + " Use repo-wide light PR history pagination instead of per-author Search (RFC-0009)")
    ap.add_argument("--gh-cli", action="store_true",
                    help="use gh CLI subprocesses instead of direct HTTP transport")
    ap.add_argument("--cost-log", type=Path, default=None,
                    help="path to write cost accounting log JSONL")
    a = ap.parse_args(argv)
    if a.refetch_cut and (a.force or a.dirty or a.fingerprint_only or a.screen_ineligible):
        ap.error("--refetch-cut already picks the repos and the collectors: "
                 "not with --force, --dirty, --fingerprint-only or --screen-ineligible")
    if a.repair_replies and (a.force or a.dirty or a.fingerprint_only or a.screen_ineligible or a.skip_ineligible
                             or a.refetch_cut or a.collectors or a.priority_keys):
        ap.error("--repair-replies picks the repos and reads only pr_window: "
                 "not with --force, --dirty, --fingerprint-only, --screen-ineligible, --skip-ineligible, "
                 "--refetch-cut, --collectors or --priority-keys")
    if a.screen_ineligible and (a.skip_ineligible or a.dirty or a.fingerprint_only):
        ap.error("--screen-ineligible is the screen before a cycle's first layer: "
                 "not with --skip-ineligible, --dirty or --fingerprint-only")
    import os as _os
    _prev_gh_env = _os.environ.get("PORCHLAMP_GH")
    if not a.gh_cli and _prev_gh_env is None:
        _os.environ["PORCHLAMP_GH"] = "http"
    try:
        return _run_main(a)
    finally:
        if not a.gh_cli and _prev_gh_env is None:
            _os.environ.pop("PORCHLAMP_GH", None)


def _run_main(a: argparse.Namespace) -> int:
    from porchlamp.collect.fingerprint import ALL_GATHER_COLLECTORS

    if a.incremental_ledgers or a.light_author_history:
        print(EXPERIMENTAL, file=sys.stderr)
    if a.cost_log:
        cost.retain_records()
    selected = parse_collectors(a.collectors)
    if a.repo:
        if not valid_ref(a.repo[0], a.repo[1]):
            print("error: --repo owner or name is not a legal GitHub name", file=sys.stderr)
            return 2
        repos = [(a.repo[0], a.repo[1], a.ecosystem)]
    else:
        repos = load_candidates(CANDIDATES.read_text().splitlines(), None if a.all else a.limit)
    n_first = 0
    if a.priority_keys is not None:
        keys = read_priority_keys(a.priority_keys)
        repos, n_first = order_by_priority(repos, keys)
        print(f"priority: {n_first} of {len(repos)} repos first ({a.priority_keys}; "
              f"{len(set(keys)) - n_first} listed keys are not candidates)")

    dirty_map: Optional[dict[str, frozenset[str]]] = None
    fp_result: Any = None
    screen = a.screen_ineligible and not a.force
    if a.dirty or a.fingerprint_only or screen:
        from porchlamp.collect import fingerprint as _fingerprint

        fp_repos = [(owner, name) for owner, name, _eco in repos]
        if screen:
            fp_repos = [
                (owner, name) for owner, name in fp_repos
                if selected is None or not all(_done(c, f"{owner}__{name}", False) for c in selected)
            ]
        fp_result = _fingerprint.run_fingerprint_pass(
            fp_repos,
            state_file=_fingerprint_state(),
            raw_dir=DATA,
            ecosystems={f"{owner}__{name}": eco for owner, name, eco in repos},
            prune_ineligible=a.skip_ineligible or screen,
            prune_fresh_only=screen,
        )
        dirty_count = sum(1 for inv in fp_result.dirty_by_repo.values() if inv)
        pruned_suffix = (
            f", {len(fp_result.pruned_by_repo)} pruned"
            if fp_result.pruned_by_repo
            else ""
        )
        print(
            f"fingerprint: {len(fp_result.fingerprints)} resolved, "
            f"{len(fp_result.unresolved_keys)} UNRESOLVED (dirty), "
            f"{dirty_count} dirty, {len(fp_result.dirty_by_repo) - dirty_count} clean"
            f"{pruned_suffix}, "
            f"{fp_result.queries_executed} graphql queries ({fp_result.cost_points} pts)"
        )
        for key in fp_result.unresolved_keys[:20]:
            print(f"  UNRESOLVED {key}")
        if a.fingerprint_only:
            return 0
        if not screen:
            dirty_map = fp_result.dirty_by_repo

    if a.refetch_cut:
        from porchlamp.collect.cut_records import legacy_cut_collectors

        dirty_map = {f"{owner}__{name}": legacy_cut_collectors(DATA, owner, name) for owner, name, _eco in repos}
    clean_label = "no cut record" if a.refetch_cut else "clean fingerprint"

    ok = fail = 0
    print(f"collectors: {'all' if selected is None else ','.join(sorted(selected))}; repos: {len(repos)}")
    for i, (owner, name, eco) in enumerate(repos, 1):
        key = f"{owner}__{name}"
        if a.repair_replies:
            repaired_ok = _repair_replies(owner, name, eco)
            ok += repaired_ok
            fail += not repaired_ok
            continue
        if (a.skip_ineligible or screen) and not a.force:
            pruned_reason = (
                fp_result.pruned_by_repo.get(key)
                if fp_result is not None
                else should_skip_ineligible(
                    owner, name, raw_dir=DATA, now=datetime.now(timezone.utc)
                )
            )
            if pruned_reason is not None:
                ok += 1
                print(f"{owner}/{name} [{eco}]: SKIP (ineligible: {pruned_reason})")
                if i == n_first:
                    print(f"{PRIORITY_DONE_MARKER}: {n_first} repos ({fail} failed)", flush=True)
                continue
        repo_collectors = selected
        repo_force = a.force
        if dirty_map is not None and not a.force:
            inv = dirty_map.get(key, ALL_GATHER_COLLECTORS)
            if not inv:
                ok += 1
                print(f"{owner}/{name} [{eco}]: SKIP ({clean_label})")
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
            import inspect as _inspect
            gr_params = _inspect.signature(gather_repo).parameters
            has_var_kw = any(p.kind == _inspect.Parameter.VAR_KEYWORD for p in gr_params.values())
            kwargs: dict[str, Any] = {
                "force": repo_force,
                "collectors": repo_collectors,
            }
            if "graphql_bulk" in gr_params or has_var_kw:
                kwargs["graphql_bulk"] = a.graphql_bulk
            if "skip_ineligible" in gr_params or has_var_kw:
                kwargs["skip_ineligible"] = a.skip_ineligible
            if getattr(a, "incremental_ledgers", False) and ("incremental_ledgers" in gr_params or has_var_kw):
                kwargs["incremental_ledgers"] = True
            if getattr(a, "light_author_history", False) and ("light_author_history" in gr_params or has_var_kw):
                kwargs["light_author_history"] = True
            st = gather_repo(owner, name, eco, **kwargs)
        except Exception as e:  # review-ok: gate-network — isolate unexpected per-repo error during sweep
            fail += 1
            print(f"{owner}/{name} [{eco}]: FAIL {e}")
        else:
            bad = [k for k, v in st.items() if v == "fail"]
            ok += not bad
            fail += bool(bad)
            if st.get("skipped_deep_black_hole") is True and not bad:
                print(f"{owner}/{name} [{eco}]: OK (stopped at black_hole)")
            else:
                print(f"{owner}/{name} [{eco}]: {'OK' if not bad else 'FAIL ' + ','.join(bad)}")
            if fp_result is not None and not screen and selected is None:
                _persist_fingerprint(fp_result, owner, name, eco, st, started,
                                     refetched=ALL_GATHER_COLLECTORS if a.force
                                     else repo_collectors)
        if i == n_first:
            print(f"{PRIORITY_DONE_MARKER}: {n_first} repos ({fail} failed)", flush=True)
    if _USER_CACHE is not None:
        print(_USER_CACHE.summary())
    if a.cost_log and cost is not None and hasattr(cost, "write_cost_log"):
        cost.write_cost_log(a.cost_log)
    print(f"done: {ok} clean, {fail} with failures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
