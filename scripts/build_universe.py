"""Build the criteria-based candidate universe -> data/raw/candidates.jsonl.

Criteria: every GitHub repo per language at or above a star floor
(``--min-stars``, default 1000) up to ``--max-total``, plus registry top-lists
where free. GitHub search returns at most 1,000 results per query, so each
language query is split into star ranges (``language:Go stars:2000..2600``)
whose count is under 1,000, and every range is paged fully. When the floor
would admit more than ``--max-total`` repos, the floor is raised (found by
count probes) until about ``--max-total`` repos qualify. Every repo already in
``candidates.jsonl`` is carried: never dropped by the cap or the floor; one the
new search no longer selects gets a ``carried:<date>`` ``selected_by`` marker.
Output line: {"owner","name","ecosystem",
"selected_by":[...],"stars_at_selection":int|null,"added_at":iso,
"star_velocity_flag":bool,"star_velocity":{...},"stars_audited":int} (the
last three per D-IN-5 -- see below). ``stars_at_selection`` is the GitHub star
count a search hit reported, null for a repo only a registry list selected
(``crates-downloads``, which records ``downloads_at_selection`` instead):
until 2026-10-22 ``crates_top`` wrote the crate's all-time download count into
that field (issue #66), so a carried row whose first criterion is
``crates-downloads`` has its old value moved to ``downloads_at_selection``.
Dedup by (owner,name) lowercased, merging selected_by; cap --max-total
(registry-list repos first, by downloads, then the highest stars; carried
repos exempt).
Checkpoint: data/raw/universe_checkpoint.json (rerun skips completed pages and
replays their stored items; probe counts are stored too).
Search API pacing: at most 30 requests/min (``--sleep`` gap plus a trailing
60 s window), with a backoff retry on a rate-limit error.
``--dry-run --out PATH`` runs the live search, skips the star-velocity audit
and writes only PATH (its checkpoint and summary sit beside it).

Star-velocity audit (D-IN-5, `docs/v2/03-ANTI-MANIPULATION.md` section 6
`star_burst`): every candidate that makes the final `--max-total` cut is
audited through `porchlamp.collect.universe.star_velocity_evidence`
(`porchlamp.starscout`'s StarScout lockstep filter, spec 10.3) before
`candidates.jsonl` is written -- the universe manifest this module writes is
the audited one. `star_velocity_flag`/`star_velocity`/`stars_audited` are
never a silent `False`/`0`/absent: a failed star-history fetch marks the
candidate `{"measured": false, "reason": "collector_failure", ...}`
instead (D-MISS-1). See `porchlamp/collect/universe.py`'s module docstring for
why `STAR_HISTORY_TRANSPORT` (the live fetch) is not yet implemented.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from porchlamp.collect.names import is_path_segment, valid_name
from porchlamp.collect.universe import Transport, default_star_history_transport, star_velocity_evidence

DATA = Path(__file__).resolve().parent.parent / "data" / "raw"
CHECKPOINT = DATA / "universe_checkpoint.json"
CANDIDATES = DATA / "candidates.jsonl"

#: The star-history transport `build()` uses (module attribute, not a
#: default argument, so a test can `monkeypatch.setattr(bu,
#: "STAR_HISTORY_TRANSPORT", fake)` exactly like `gh_search`/`crates_top`
#: above). No live implementation yet (`porchlamp.collect.universe`'s docstring):
#: a real run reports every candidate `Unmeasured(collector_failure)` until
#: this attribute is bound to a real transport.
STAR_HISTORY_TRANSPORT: Transport = default_star_history_transport

# ecosystem -> GitHub search language queries (each is sliced by star range)
#
# A language is searched only when its projects have an ecosystem this
# benchmark models (``porchlamp.models.Ecosystem``; D-UNI-2 as amended by
# RFC-0023, 2026-10-22): the registry it publishes to (Kotlin, Scala and Groovy
# publish to Maven Central like Java, so they are ``maven``), or no package
# registry at all, which D-MISS-5 scores as ``systems`` (Zig, Vim Script and
# LLVM, like C and C++). A language whose registry is not modeled yet (C#
# NuGet, Ruby RubyGems, PHP Packagist, Dart pub.dev, Swift, Lua LuaRocks,
# Clojure Clojars) is left out rather than given an ecosystem it does not have:
# ``systems`` would mark its dependents and downloads not_applicable, which is
# false for those registries. Every language faces the one star floor.
SEARCH_CRITERIA: dict[str, list[str]] = {
    "pypi": ["language:Python"],
    "npm": ["language:JavaScript", "language:TypeScript"],
    "crates": ["language:Rust"],
    "go": ["language:Go"],
    "maven": ["language:Java", "language:Kotlin", "language:Scala", "language:Groovy"],
    "systems": ["language:C", "language:C++", "language:Zig", 'language:"Vim Script"',
                "language:LLVM"],
}

#: GitHub search returns at most this many results per query; a slice whose
#: count reaches it is split.
RESULT_CAP = 1000
PER_PAGE = 100
#: Above any repo's star count (the most-starred repo has about 450k).
STAR_CEILING = 1_000_000
#: Search API limit (requests per minute).
SEARCH_PER_MIN = 30

#: Clock and sleep the pacer uses (module attributes so a test can fake them).
_CLOCK = time.monotonic
_SLEEP = time.sleep


def _gh_api(query: str, page: int, per_page: int, jq: str) -> str:
    cmd = ["gh", "api", "search/repositories",
           "-X", "GET", "-f", f"q={query}", "-f", "sort=stars", "-f", "order=desc",
           "-F", f"page={page}", "-F", f"per_page={per_page}", "--jq", jq]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"gh search failed rc={out.returncode}: {out.stderr[:200]}")
    return out.stdout


def gh_count(query: str) -> int:
    """total_count of a repository search (one API call, no items)."""
    return int(_gh_api(query, 1, 1, ".total_count").strip())


def gh_search(query: str, page: int, per_page: int = PER_PAGE) -> list[dict[str, Any]]:
    """One GitHub search page via `gh api` (authed; 30 req/min limit), stars desc."""
    out = _gh_api(query, page, per_page,
                  '.items[] | {owner: .owner.login, name: .name, stars: .stargazers_count}')
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def crates_top(limit: int) -> list[dict[str, Any]]:
    """Top crates.io packages by downloads (User-Agent required).

    Each hit is ``{"owner", "name", "downloads"}``: ``downloads`` is the
    crate's all-time download count, which says nothing about GitHub stars, so
    it never fills a candidate's ``stars_at_selection``.
    """
    req = urllib.request.Request(
        "https://crates.io/api/v1/crates?sort=downloads&per_page=100",
        headers={"User-Agent": "porchlamp-research/0.1 (ranking study)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    hits = []
    for c in data.get("crates", [])[:limit]:
        repo = c.get("repository") or ""
        if "github.com/" in repo:
            parts = repo.split("github.com/")[1].strip("/").split("/")
            if len(parts) >= 2:
                # crates.io repository URLs sometimes carry a trailing ".git"
                # (e.g. github.com/fizyk20/generic-array.git); the GitHub API
                # 404s on that name, so strip it before it becomes a candidate.
                hits.append({"owner": parts[0], "name": parts[1].removesuffix(".git"),
                             "downloads": int(c.get("downloads") or 0)})
    return hits


#: ``selected_by`` criteria that a registry top-list sets, not a star search.
#: D-UNI-2 makes the universe the union of the criteria, so a repo one of these
#: selected is not cut for its star count, and its ``stars_at_selection`` is
#: whatever a star search reported (null when none did).
REGISTRY_CRITERIA: frozenset[str] = frozenset({"crates-downloads"})


def repair_registry_stars(row: dict[str, Any]) -> dict[str, Any]:
    """``row`` with a registry list's download count moved out of its stars field.

    Before 2026-10-22 :func:`crates_top` handed ``build`` the crate's all-time
    download count as ``stars`` (issue #66), and ``build`` keeps the
    ``stars_at_selection`` of whichever criterion created the row, which is the
    first entry of ``selected_by``. So a row whose first criterion is in
    :data:`REGISTRY_CRITERIA` and that carries an integer
    ``stars_at_selection`` but no ``downloads_at_selection`` holds a download
    count: it is returned as ``downloads_at_selection`` with
    ``stars_at_selection`` null (a later star-search hit fills the stars). Any
    other row is returned unchanged, so the repair is idempotent.
    """
    criteria = row.get("selected_by") or []
    stars = row.get("stars_at_selection")
    if criteria and criteria[0] in REGISTRY_CRITERIA and isinstance(stars, int) \
            and not isinstance(stars, bool) and "downloads_at_selection" not in row:
        return dict(row, stars_at_selection=None, downloads_at_selection=stars)
    return row


def star_order(row: dict[str, Any]) -> tuple[bool, int]:
    """Sort key: most ``stars_at_selection`` first, a row with none (null) last."""
    stars = row.get("stars_at_selection")
    return (stars is None, -(stars or 0))


def _load_ckpt(path: Optional[Path] = None) -> dict[str, Any]:
    path = path or CHECKPOINT
    ck = json.loads(path.read_text()) if path.is_file() else {}
    ck.setdefault("done", {})    # page cid -> item count
    ck.setdefault("items", {})   # page cid -> [[owner, name, stars], ...]
    ck.setdefault("counts", {})  # probe query -> total_count
    return ck


def _save_ckpt(ck: dict[str, Any], path: Optional[Path] = None) -> None:
    path = path or CHECKPOINT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ck, separators=(",", ":")))


class Pacer:
    """Search API pacing: a minimum gap between calls plus at most ``per_min``
    calls in any trailing 60 s. ``gap <= 0`` disables it (tests)."""

    def __init__(self, gap: float, per_min: int = SEARCH_PER_MIN,
                 clock: Any = None, sleep: Any = None) -> None:
        self.gap, self.per_min = gap, per_min
        self.clock, self.sleep = clock or _CLOCK, sleep or _SLEEP
        self.stamps: list[float] = []

    def wait(self) -> None:
        now = self.clock()
        if self.gap > 0:
            target = now
            if self.stamps:
                target = max(target, self.stamps[-1] + self.gap)
            if len(self.stamps) >= self.per_min:
                target = max(target, self.stamps[-self.per_min] + 60.0)
            if target > now:
                self.sleep(target - now)
        self.stamps.append(self.clock())


def _is_rate_limit(err: Exception) -> bool:
    msg = str(err).lower()
    return "rate limit" in msg or "429" in msg or "403" in msg


def plan_slices(query: str, lo: int, hi: int, count: Any,
                limit: int = RESULT_CAP) -> list[tuple[int, int, int]]:
    """Split ``query stars:lo..hi`` into (lo, hi, n) slices each under ``limit``.

    A slice whose count reaches ``limit`` (GitHub returns at most 1,000) is
    split at the geometric mean of its bounds; both ends are inclusive.
    """
    n = count(f"{query} stars:{lo}..{hi}")
    if n == 0:
        return []
    if n < limit or lo >= hi:
        return [(lo, hi, n)]
    mid = min(hi - 1, max(lo, math.isqrt(lo * hi)))
    return plan_slices(query, lo, mid, count, limit) + plan_slices(query, mid + 1, hi, count, limit)


def effective_floor(queries: list[str], min_stars: int, max_total: int, count: Any) -> int:
    """Largest floor F >= min_stars with at least ``max_total`` repos at or above F
    (to within 1%); ``min_stars`` itself when it admits fewer than ``max_total``."""
    def total(f: int) -> int:
        return sum(count(f"{q} stars:>={f}") for q in queries)

    if total(min_stars) < max_total:
        return min_stars
    lo, hi = min_stars, max(1, min_stars * 2)
    while total(hi) >= max_total:
        lo, hi = hi, hi * 2
    while hi - lo > max(1, lo // 100):
        mid = (lo + hi) // 2
        if total(mid) >= max_total:
            lo = mid
        else:
            hi = mid
    return lo


def _summary(rows: list[dict[str, Any]], *, min_stars: int, floor: int, carried: int,
             requalified: int, calls: int, wall_s: float) -> dict[str, Any]:
    by_eco: dict[str, int] = {}
    by_lang: dict[str, int] = {}
    for r in rows:
        by_eco[r["ecosystem"]] = by_eco.get(r["ecosystem"], 0) + 1
        for c in r["selected_by"]:
            if c.startswith("github-stars:language:"):
                lang = c.removeprefix("github-stars:language:").strip('"')
                by_lang[lang] = by_lang.get(lang, 0) + 1
    new_rows = [r for r in rows if not any(c.startswith("carried:") for c in r["selected_by"])]
    return {"total": len(rows), "per_ecosystem": by_eco, "per_language": by_lang,
            "min_stars_requested": min_stars, "floor_used": floor,
            "min_stars_reached": min((r["stars_at_selection"] for r in new_rows
                                      if any(c.startswith("github-stars:") for c in r["selected_by"])
                                      and r["stars_at_selection"] is not None),
                                     default=None),
            "existing_carried_total": carried, "existing_requalified": requalified,
            "existing_carried_marked": carried - requalified,
            "new_repos": len(rows) - carried,
            "search_api_calls": calls, "wall_seconds": round(wall_s, 1)}


def build_search_query(
    query: str,
    *,
    exclude_ineligible: bool = False,
    min_pushed_date: Optional[str] = None,
) -> str:
    """Construct a GitHub repository search query, optionally appending
    pre-collection eligibility filters (``archived:false fork:false mirror:false``
    and ``pushed:>={min_pushed_date}``)."""
    if not exclude_ineligible:
        return query
    qualifiers = ["archived:false", "fork:false", "mirror:false"]
    if min_pushed_date:
        qualifiers.append(f"pushed:>={min_pushed_date}")
    return f"{query} {' '.join(qualifiers)}"


def prune_ineligible_candidates(
    rows: list[dict[str, Any]],
    *,
    raw_dir: Path,
    now: datetime,
    state_file: Optional[Path] = None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Filter out candidate rows whose existing fingerprint or raw envelopes
    classify them as closed for contributions or non-software
    (:func:`porchlamp.collect.fingerprint.classify_prune_reason`: ``archived``,
    ``disabled``, ``stale``, ``unlicensed``, ``non_software``,
    ``no_external_workflow``).

    Returns ``(kept_rows, pruned_reasons_by_key)`` where ``pruned_reasons_by_key``
    maps ``owner__name`` to the prune reason. ``black_hole`` repositories are
    never pruned here — they remain in the candidate universe so they are
    scored and ranked with a ``"black_hole"`` flag.
    """
    from porchlamp.collect.fingerprint import classify_prune_reason, load_latest_fingerprints

    fp_path = state_file or (raw_dir.parent / "state" / "fingerprints.jsonl")
    latest_fps = load_latest_fingerprints(fp_path)
    kept: list[dict[str, Any]] = []
    pruned: dict[str, str] = {}
    for row in rows:
        owner = str(row["owner"])
        name = str(row["name"])
        key = f"{owner}__{name}"
        reason = classify_prune_reason(
            latest_fps.get(key), raw_dir=raw_dir, owner=owner, name=name, now=now
        )
        if reason is not None:
            pruned[key] = reason
        else:
            kept.append(row)
    return kept, pruned


def build(max_per_criterion: int = 1000, max_total: int = 11000,
          only: Optional[str] = None, dry_run: bool = False,
          sleep_s: float = 2.0, now: Optional[datetime] = None,
          min_stars: int = 1000, out: Optional[Path] = None,
          existing: Optional[Path] = None,
          *,
          prune_ineligible: bool = False,
          search_filters: bool = True) -> int:
    """Run the universe build; return candidate count written.

    ``now`` is the D-IN-5 star-velocity audit's reference clock -- an
    explicit parameter (never read from inside the audit itself) so the
    derived `star_velocity_flag`/`star_velocity`/`stars_audited` fields
    stay reproducible from a fixed timestamp, resolved once here (a
    script's own I/O boundary, not a derived-value computation) when the
    caller does not supply one.

    ``max_per_criterion`` now only limits the crates.io download list.
    ``dry_run`` needs ``out``: it searches live, skips the audit and writes
    only ``out`` (checkpoint ``out + .checkpoint.json``, summary
    ``out + .summary.json``). ``existing`` is the current universe to carry
    (default: ``CANDIDATES``). ``search_filters`` (on by default) makes the
    search queries ask only for repositories that are not archived, forks or
    mirrors and were pushed within the stale window, so GitHub never lists
    the dead ones as new candidates. When ``prune_ineligible=True``, carried
    candidates already verified as ineligible (archived, disabled, stale,
    unlicensed, non_software, no_external_workflow) are also pruned before
    writing; without it every carried candidate stays listed.
    """
    if dry_run and out is None:
        raise ValueError("--dry-run needs --out PATH (it writes nothing else)")
    now = now or datetime.now(timezone.utc)
    ck_path = out.with_name(out.name + ".checkpoint.json") if dry_run and out else None
    ck = _load_ckpt(ck_path)
    start = _CLOCK()
    pacer = Pacer(sleep_s)
    calls = 0

    def call(fn: Any, *args: Any) -> Any:
        """One paced Search API call; backs off and retries on a rate limit."""
        nonlocal calls
        for attempt in range(4):
            pacer.wait()
            calls += 1
            try:
                return fn(*args)
            except RuntimeError as e:
                if attempt == 3 or not _is_rate_limit(e):
                    raise
                print(f"rate limited, backing off {60 * (attempt + 1)}s")
                (pacer.sleep)(60 * (attempt + 1))

    def count(q: str) -> int:
        if q not in ck["counts"]:
            ck["counts"][q] = int(call(gh_count, q))
            _save_ckpt(ck, ck_path)
        return ck["counts"][q]

    prior: dict[tuple[str, str], dict[str, Any]] = {}
    src = existing or CANDIDATES
    dropped = 0
    if src.is_file():
        for line in src.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                # owner and name become file names (universe_entry/, star_history/): a carried row is not trusted
                if not (isinstance(r, dict) and is_path_segment(r.get("owner")) and is_path_segment(r.get("name"))
                        and valid_name(r.get("ecosystem"))):
                    dropped += 1
                    continue
                prior[(r["owner"].lower(), r["name"].lower())] = r
    if dropped:
        print(f"WARN dropped {dropped} carried candidates whose owner, name or ecosystem is not a single path segment")
    found: dict[tuple[str, str], dict[str, Any]] = {
        k: repair_registry_stars(dict(v, selected_by=list(v["selected_by"]))) for k, v in prior.items()}
    searched: set[tuple[str, str]] = set()

    def add(owner: str, name: str, eco: str, crit: str, stars: Optional[int],
            downloads: Optional[int] = None) -> None:
        key = (owner.lower(), name.lower())
        searched.add(key)
        if key in found:
            row = found[key]
            if crit not in row["selected_by"]:
                row["selected_by"].append(crit)
            if row.get("stars_at_selection") is None and stars is not None:
                row["stars_at_selection"] = stars
            if downloads is not None and "downloads_at_selection" not in row:
                row["downloads_at_selection"] = downloads
            return
        found[key] = {"owner": owner, "name": name, "ecosystem": eco,
                      "selected_by": [crit], "stars_at_selection": stars,
                      "added_at": datetime.now(timezone.utc).isoformat()}
        if downloads is not None:
            found[key]["downloads_at_selection"] = downloads

    from datetime import timedelta
    from porchlamp.collect.fingerprint import STALE_PUSH_WINDOW_DAYS

    min_pushed_date = (
        (now - timedelta(days=STALE_PUSH_WINDOW_DAYS)).strftime("%Y-%m-%d")
        if search_filters
        else None
    )
    ecos = [
        (
            eco,
            [
                (
                    q,
                    build_search_query(
                        q, exclude_ineligible=search_filters, min_pushed_date=min_pushed_date
                    ),
                )
                for q in qs
            ],
        )
        for eco, qs in SEARCH_CRITERIA.items()
        if not only or eco == only
    ]
    floor = min_stars
    try:
        floor = effective_floor([sq for _, qpairs in ecos for _, sq in qpairs], min_stars, max_total, count)
    except RuntimeError as e:
        print(f"WARN floor probe failed, using --min-stars {min_stars}: {e}")
    print(f"star floor: {floor} (requested {min_stars})")

    for eco, qpairs in ecos:
        for base_query, query in qpairs:
            try:
                slices = plan_slices(query, floor, STAR_CEILING, count)
            except RuntimeError as e:
                print(f"WARN plan {eco}:{query}: {e}")
                continue
            for lo, hi, n in slices:
                sq = f"{query} stars:{lo}..{hi}"
                for page in range(1, min(10, math.ceil(n / PER_PAGE)) + 1):
                    cid = f"search:{eco}:{sq}:{page}"
                    if cid in ck["done"]:
                        items = [{"owner": o, "name": nm, "stars": st}
                                 for o, nm, st in ck["items"].get(cid, [])]
                    else:
                        try:
                            items = call(gh_search, sq, page)
                        except RuntimeError as e:
                            print(f"WARN {cid}: {e}")
                            continue
                        ck["done"][cid] = len(items)
                        ck["items"][cid] = [[it["owner"], it["name"], int(it["stars"])] for it in items]
                        _save_ckpt(ck, ck_path)
                        print(f"{cid}: {len(items)} items")
                    for it in items:
                        if int(it["stars"]) >= floor:
                            add(it["owner"], it["name"], eco, f"github-stars:{base_query}",
                                int(it["stars"]))
        if eco == "crates":
            try:
                for it in crates_top(max_per_criterion):
                    add(it["owner"], it["name"], "crates", "crates-downloads", None, it["downloads"])
            except Exception as e:  # review-ok: gate-network — registry list optional
                print(f"WARN crates_top: {e}")

    # Carry every current repo: it is never cut by the floor or --max-total. One
    # the search did not select this run is marked "carried:<date added>".
    carried_rows = []
    for key, old in prior.items():
        row = found[key]
        if key not in searched:
            marker = f"carried:{str(old.get('added_at', ''))[:10] or 'unknown'}"
            if marker not in row["selected_by"]:
                row["selected_by"].append(marker)
        carried_rows.append(row)
    # D-UNI-2: the universe is the union of the criteria, so a repo a registry
    # list selected takes its place on that criterion (most downloads first), and
    # the star cap orders only the star-search repos.
    new_rows = [r for k, r in found.items() if k not in prior]
    by_registry = sorted((r for r in new_rows if REGISTRY_CRITERIA.intersection(r["selected_by"])),
                         key=lambda r: -(r.get("downloads_at_selection") or 0))
    by_stars = sorted((r for r in new_rows if not REGISTRY_CRITERIA.intersection(r["selected_by"])),
                      key=star_order)
    fresh = (by_registry + by_stars)[:max(0, max_total - len(carried_rows))]
    rows = sorted(carried_rows + fresh, key=star_order)
    if prune_ineligible:
        rows, pruned = prune_ineligible_candidates(rows, raw_dir=DATA, now=now)
        if pruned:
            print(f"pruned {len(pruned)} ineligible candidate(s) prior to write")
    requalified = sum(1 for k in prior if k in searched)
    print(f"carried {len(prior)} current repos ({len(prior) - requalified} marked carried), "
          f"{len(fresh)} new")
    if dry_run:
        assert out is not None
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("".join(json.dumps(r) + "\n" for r in rows))
        summ = _summary(rows, min_stars=min_stars, floor=floor, carried=len(prior),
                        requalified=requalified, calls=calls, wall_s=_CLOCK() - start)
        out.with_name(out.name + ".summary.json").write_text(json.dumps(summ, indent=1) + "\n")
        print(json.dumps(summ, indent=1))
        print(f"dry run: wrote {len(rows)} candidates -> {out}")
    else:
        # D-IN-5: audit every candidate that makes the cut for
        # fraudulent star velocity before it becomes part of the universe
        # manifest -- "universe entry" is exactly this point (a candidate
        # short of the cap never enters the universe, so it is never
        # fetched or charged for). A row already carrying a prior audit
        # (a resumed/merged candidates.jsonl) keeps it: the audit does not
        # depend on selected_by/stars_at_selection, so re-running it would
        # only repeat the same fetch (`star_velocity_evidence`'s own
        # envelope-checkpoint already skips that).
        for row in rows:
            evidence = star_velocity_evidence(
                DATA, row["owner"], row["name"], now, STAR_HISTORY_TRANSPORT,
                now.isoformat(),
            )
            row.update(evidence.to_json())
        CANDIDATES.parent.mkdir(parents=True, exist_ok=True)
        CANDIDATES.write_text("".join(json.dumps(r) + "\n" for r in rows))
        print(f"wrote {len(rows)} candidates -> {CANDIDATES}")
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-per-criterion", type=int, default=1000,
                    help="crates.io download-list size")
    ap.add_argument("--max-total", type=int, default=11000)
    ap.add_argument("--min-stars", type=int, default=1000)
    ap.add_argument("--only", choices=list(SEARCH_CRITERIA))
    ap.add_argument("--dry-run", action="store_true",
                    help="live search, no audit, write only --out")
    ap.add_argument("--out", type=Path, help="candidate list path (required with --dry-run)")
    ap.add_argument("--existing", type=Path,
                    help="current universe to carry (default: data/raw/candidates.jsonl)")
    ap.add_argument("--prune-ineligible", action="store_true",
                    help="also drop carried candidates already verified as archived, disabled, "
                         "stale, unlicensed, non_software or no_external_workflow")
    ap.add_argument("--no-search-filters", dest="search_filters", action="store_false",
                    help="search without the archived/fork/mirror/pushed qualifiers "
                         "(by default only live repositories are listed as new candidates)")
    a = ap.parse_args()
    if a.dry_run and a.out is None:
        ap.error("--dry-run needs --out")
    build(a.max_per_criterion, a.max_total, a.only, a.dry_run, min_stars=a.min_stars,
          out=a.out, existing=a.existing, prune_ineligible=a.prune_ineligible,
          search_filters=a.search_filters)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
