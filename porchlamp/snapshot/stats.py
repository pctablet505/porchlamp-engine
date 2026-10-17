"""The report's star figures and corpus totals, computed from one snapshot.

``python -m porchlamp.snapshot stats <snapshot.db> --data-dir <data root>`` prints the stats file
``scripts/export_static.py --stats`` reads (``stats-<snapshot>.json``). Its inputs are:

* the snapshot database (``repositories`` and ``snapshots``), opened read-only;
* ``<data root>/raw/github_meta/<owner>__<name>.json``: the GitHub metadata envelopes the snapshot was
  derived from. Their ``data.stars`` is the only star count used: the database stores none
  (``stars_audited`` is NULL), so this is where every star band, the rank correlation and the
  top-100 figures come from;
* ``<data root>/derived/<owner>__<name>.json``: the derived bundles' ``pr_events``, for the
  bots-excluded recomputation and the corpus totals.

The definitions:

``gate5_decided``
    projects that passed gates 1 to 4 and were decided on gate 5: ``gate_reason`` is NULL,
    ``black_hole`` or ``unrated: no_external_workflow``, and ``D-GATE-5`` is not among the
    undecided gates (the same set :func:`porchlamp.serve.report_pages.decided_counts` counts);
``black_holes``
    those of them with ``gate_reason = 'black_hole'``; ``by_stars`` splits both by star band;
``spearman``
    rank correlation between stars and each of ``p_responsiveness``, the score and ``ttfhr_p75``
    over the rated projects that have a star count;
``top100_overlap``
    how many of the 100 most-starred projects are in the top 100 by rank; ``top100_stars_fate``
    is what happened to those 100 (rated, or their gate reason);
``bots_excluded``
    the black-hole test recomputed from the derived ``pr_events`` with bot logins removed
    (``is_external`` alone keeps bots): at least 10 outside human PRs at least 14 days old, and
    more than half of them neither merged nor first reviewed by a human within 14 days;
``corpus``
    derived bundles read, outside human PRs in them, and distinct outside authors.

Nothing here reads the wall clock: PR ages are judged at ``--as-of``, by default the database's
``snapshots.as_of``.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

from porchlamp.constants import BOT_LOGIN_MARKERS

__all__ = ["STAR_BUCKETS", "bucket_label", "compute_stats", "open_snapshot_db", "spearman"]

#: Star bands, ``[lo, hi)``; the last one is open-ended.
STAR_BUCKETS: tuple[tuple[int, int], ...] = ((0, 1000), (1000, 10000), (10000, 50000), (50000, 10**9))
_OPEN_END = 10**9
_FOURTEEN_DAYS = timedelta(days=14)
#: The bots-excluded test needs at least this many outside human PRs old enough to judge.
_MIN_HUMAN_PRS = 10


class StatsError(RuntimeError):
    """The inputs cannot give a trustworthy stats file."""


def _parse(ts: str) -> datetime:
    """An ISO-8601 timestamp; one without an offset is read as UTC."""
    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _is_bot(login: str) -> bool:
    login = login.lower()
    return any(marker in login for marker in BOT_LOGIN_MARKERS)


def _ranks(xs: Sequence[Any]) -> list[float]:
    """1-based ranks, ties sharing their mean rank."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(a: Sequence[Any], b: Sequence[Any]) -> Optional[float]:
    """Spearman's rank correlation (Pearson on mid-ranks); ``None`` when it is undefined (fewer than two
    pairs, or one side constant)."""
    if len(a) < 2:
        return None
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = statistics.mean(ra), statistics.mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else None


def bucket_label(lo: int, hi: int) -> str:
    """``[1000,10000)``; the open-ended band is ``[50000,inf)``."""
    return f"[{lo},{'inf' if hi >= _OPEN_END else hi})"


def open_snapshot_db(db: Path) -> sqlite3.Connection:
    """The snapshot database, read-only and immutable.

    A frozen snapshot is never written again, so it is opened ``immutable``; a non-empty write-ahead
    log would then be ignored, so one is refused instead of read past."""
    db = Path(db)
    if not db.is_file():
        raise StatsError(f"no snapshot database at {db}")
    wal = db.with_name(db.name + "-wal")
    if wal.is_file() and wal.stat().st_size > 0:
        raise StatsError(f"{db} has a non-empty write-ahead log ({wal}); checkpoint it before computing stats")
    return sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro&immutable=1", uri=True)


def _read_stars(meta_dir: Path) -> dict[tuple[str, str], int]:
    stars: dict[tuple[str, str], int] = {}
    for path in meta_dir.glob("*.json"):
        owner, name = path.stem.split("__", 1)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))["data"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("stars"), int):
            stars[(owner.lower(), name.lower())] = data["stars"]
    return stars


def _unanswered(event: dict) -> bool:
    """Neither merged nor first reviewed by a human within 14 days of opening."""
    created = _parse(event["created_at"])
    if event.get("merged_at") and _parse(event["merged_at"]) - created <= _FOURTEEN_DAYS:
        return False
    if not event.get("first_human_review_at"):
        return True
    return _parse(event["first_human_review_at"]) - created > _FOURTEEN_DAYS


def compute_stats(db: Path, data_dir: Path, *, as_of: Optional[str] = None, db_label: Optional[str] = None) -> dict:
    """The stats file for the snapshot in ``db``; see the module docstring for every field.

    ``as_of`` (ISO 8601) overrides the database's ``snapshots.as_of`` as the PR-age reference;
    ``db_label`` is what the ``db`` field records (default: ``str(db)``)."""
    data_dir = Path(data_dir)
    meta_dir, derived = data_dir / "raw" / "github_meta", data_dir / "derived"
    out: dict[str, Any] = {"db": db_label if db_label is not None else str(db)}
    stars = _read_stars(meta_dir)

    con = open_snapshot_db(db)
    try:
        as_of = as_of or con.execute("SELECT as_of FROM snapshots").fetchone()[0]
        now = _parse(as_of)
        out["snapshot_id"] = con.execute("SELECT snapshot_id FROM repositories LIMIT 1").fetchone()[0]
        out["as_of"] = as_of
        # a database built before the rename names the score column `cvi`
        columns = {c[1] for c in con.execute("PRAGMA table_info(repositories)")}
        score = "porchlamp" if "porchlamp" in columns else "cvi"
        out["score_column"] = score
        rows = con.execute(
            "SELECT owner, name, gate_reason, gate_undecided, rated, p_responsiveness, "
            f"{score}, rank, ttfhr_p75 FROM repositories"
        ).fetchall()
        candidates = con.execute(
            "SELECT owner, name FROM repositories WHERE gate_reason IS NULL "
            "OR gate_reason IN ('black_hole','unrated: no_external_workflow')"
        ).fetchall()
    finally:
        con.close()

    def key(r: Sequence[Any]) -> tuple[str, str]:
        return (r[0].lower(), r[1].lower())

    out["repos"] = len(rows)
    out["repos_with_stars"] = sum(key(r) in stars for r in rows)
    out["rated"] = sum(r[4] == 1 for r in rows)

    # black holes among gate-5-decided projects, overall and by stars
    decided = [
        r for r in rows
        if r[2] in (None, "black_hole", "unrated: no_external_workflow") and "D-GATE-5" not in json.loads(r[3] or "[]")
    ]
    bh = sum(r[2] == "black_hole" for r in decided)
    out["gate5_decided"] = len(decided)
    out["black_holes"] = bh
    out["black_hole_share"] = round(bh / len(decided), 4)
    out["by_stars"] = {}
    for lo, hi in STAR_BUCKETS:
        sub = [r for r in decided if lo <= stars.get(key(r), -1) < hi]
        if sub:
            b = sum(r[2] == "black_hole" for r in sub)
            out["by_stars"][bucket_label(lo, hi)] = {"n": len(sub), "black_holes": b, "share": round(b / len(sub), 4)}

    # Spearman among rated projects with stars
    rated = [r for r in rows if r[4] == 1 and key(r) in stars]
    out["rated_with_stars"] = len(rated)
    out["spearman"] = {}
    for col, idx in (("p_responsiveness", 5), ("porchlamp", 6), ("ttfhr_p75", 8)):
        pairs = [(stars[key(r)], r[idx]) for r in rated if r[idx] is not None]
        rho = spearman([p[0] for p in pairs], [p[1] for p in pairs])
        out["spearman"][col] = {"n": len(pairs), "rho": None if rho is None else round(rho, 4)}

    # the 100 most-starred against the top 100 by rank
    by_stars = sorted(rows, key=lambda r: -stars.get(key(r), -1))[:100]
    top100_rank = {key(r) for r in rows if r[7] is not None and r[7] <= 100}
    out["top100_overlap"] = sum(key(r) in top100_rank for r in by_stars)
    out["top100_stars_min"] = stars.get(key(by_stars[-1]), -1)
    fate: dict[str, int] = {}
    for r in by_stars:
        k = r[2] or ("rated" if r[4] else "unrated")
        fate[k] = fate.get(k, 0) + 1
    out["top100_stars_fate"] = dict(sorted(fate.items(), key=lambda x: -x[1]))

    # bots excluded: the black-hole test recomputed from derived pr_events
    acc = {b: [0, 0] for b in STAR_BUCKETS}
    total = [0, 0]
    for owner, name in candidates:
        bundle_path, meta_path = derived / f"{owner}__{name}.json", meta_dir / f"{owner}__{name}.json"
        if not bundle_path.exists() or not meta_path.exists():
            continue
        s = json.loads(meta_path.read_text(encoding="utf-8"))["data"].get("stars")
        events = json.loads(bundle_path.read_text(encoding="utf-8")).get("pr_events") or []
        population = [
            e for e in events
            if e.get("author_login") and e.get("is_external") and not _is_bot(e["author_login"])
            and now - _parse(e["created_at"]) >= _FOURTEEN_DAYS
        ]
        if len(population) < _MIN_HUMAN_PRS or not isinstance(s, int):
            continue
        hole = sum(_unanswered(e) for e in population) / len(population) > 0.5
        total[0] += 1
        total[1] += hole
        for lo, hi in STAR_BUCKETS:
            if lo <= s < hi:
                acc[(lo, hi)][0] += 1
                acc[(lo, hi)][1] += hole
    out["bots_excluded"] = {
        "n": total[0], "black_holes": total[1],
        "share": round(total[1] / total[0], 4) if total[0] else None,
        "by_stars": {bucket_label(*b): {"n": n, "share": round(h / n, 4)} for b, (n, h) in acc.items() if n},
    }

    # the corpus: human outside PRs and distinct outside authors
    n_repo = n_pr = 0
    authors: set[str] = set()
    for path in derived.glob("*.json"):
        try:
            events = json.loads(path.read_text(encoding="utf-8")).get("pr_events") or []
        except (OSError, ValueError):
            continue
        n_repo += 1
        for e in events:
            login = e.get("author_login")
            if login and e.get("is_external") and not _is_bot(login):
                n_pr += 1
                authors.add(login.lower())
    out["corpus"] = {"derived_repos": n_repo, "human_outside_prs": n_pr, "distinct_outside_authors": len(authors)}
    return out
