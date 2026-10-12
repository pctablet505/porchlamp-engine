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
"selected_by":[...],"stars_at_selection":int,"added_at":iso,
"star_velocity_flag":bool,"star_velocity":{...},"stars_audited":int} (the
last three per D-IN-5 -- see below).
Dedup by (owner,name) lowercased, merging selected_by; cap --max-total
(highest stars kept, carried repos exempt).
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
SEARCH_CRITERIA: dict[str, list[str]] = {
    "pypi": ["language:Python"],
    "npm": ["language:JavaScript", "language:TypeScript"],
    "crates": ["language:Rust"],
    "go": ["language:Go"],
    "maven": ["language:Java"],
    "systems": ["language:C", "language:C++"],
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
    """Top crates.io packages by downloads (User-Agent required)."""
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
                             "stars": int(c.get("downloads") or 0)})
    return hits


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
                lang = c.removeprefix("github-stars:language:")
                by_lang[lang] = by_lang.get(lang, 0) + 1
    new_rows = [r for r in rows if not any(c.startswith("carried:") for c in r["selected_by"])]
    return {"total": len(rows), "per_ecosystem": by_eco, "per_language": by_lang,
            "min_stars_requested": min_stars, "floor_used": floor,
            "min_stars_reached": min((r["stars_at_selection"] for r in new_rows
                                      if any(c.startswith("github-stars:") for c in r["selected_by"])),
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
          prune_ineligible: bool = False) -> int:
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
    (default: ``CANDIDATES``). When ``prune_ineligible=True``, search queries
    exclude archived/fork/mirror/stale repositories and carried candidates
    already verified as ineligible (archived, disabled, stale, unlicensed,
    non_software, no_external_workflow) are pruned before writing.
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
    if src.is_file():
        for line in src.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                prior[(r["owner"].lower(), r["name"].lower())] = r
    found: dict[tuple[str, str], dict[str, Any]] = {k: dict(v, selected_by=list(v["selected_by"]))
                                                    for k, v in prior.items()}
    searched: set[tuple[str, str]] = set()

    def add(owner: str, name: str, eco: str, crit: str, stars: int) -> None:
        key = (owner.lower(), name.lower())
        searched.add(key)
        if key in found:
            if crit not in found[key]["selected_by"]:
                found[key]["selected_by"].append(crit)
            return
        found[key] = {"owner": owner, "name": name, "ecosystem": eco,
                      "selected_by": [crit], "stars_at_selection": stars,
                      "added_at": datetime.now(timezone.utc).isoformat()}

    from datetime import timedelta
    from porchlamp.collect.fingerprint import STALE_PUSH_WINDOW_DAYS

    min_pushed_date = (
        (now - timedelta(days=STALE_PUSH_WINDOW_DAYS)).strftime("%Y-%m-%d")
        if prune_ineligible
        else None
    )
    ecos = [
        (
            eco,
            [
                (
                    q,
                    build_search_query(
                        q, exclude_ineligible=prune_ineligible, min_pushed_date=min_pushed_date
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
                    add(it["owner"], it["name"], "crates", "crates-downloads", it["stars"])
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
    fresh = sorted((r for k, r in found.items() if k not in prior),
                   key=lambda r: -r["stars_at_selection"])[:max(0, max_total - len(carried_rows))]
    rows = sorted(carried_rows + fresh, key=lambda r: -r["stars_at_selection"])
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
                    help="exclude archived, stale, unlicensed, non_software, and "
                         "no_external_workflow repos at universe build time")
    a = ap.parse_args()
    if a.dry_run and a.out is None:
        ap.error("--dry-run needs --out")
    build(a.max_per_criterion, a.max_total, a.only, a.dry_run, min_stars=a.min_stars,
          out=a.out, existing=a.existing, prune_ineligible=a.prune_ineligible)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
