"""Promote CLEAN verified bundles into the OperationalStore (SQLite).

Owner rule: ONLY verified data enters SQLite. Reads data/verified/*.json,
keeps report.clean==True, rebuilds RepoMeta + RawPREvent, scores through the
full engine against verified-universe ECDF populations (per ecosystem), and
upserts composites + raw metrics into data/cvi.db.

Two passes: (1) load all clean bundles, build per-ecosystem populations
(log10 dependents/downloads, per-repo TTM p90); (2) score + write.

Scoring is deterministic: the reference time is ``--as-of`` (D-VER-3) and is
threaded into every scoring call. Without ``--as-of`` the wall clock is read
once, here at the script boundary, and that value is used for the whole run --
never inside the scoring loop.

Round 2 (`03` §5, Blocker-3): promotion requires a frozen snapshot manifest
(``python -m cvi.snapshot freeze``) and writes a ``snapshot_meta`` row binding
the database's post-promotion content (:func:`cvi.lakehouse.repositories_dump_hash`)
to that manifest's identity, so ``scripts/serve.py`` can tell a
promoted-then-edited database from one that still matches what was promoted
-- a per-row telemetry check cannot survive a bulk ``UPDATE``, a content hash
of the whole table can.

Usage: .venv/bin/python scripts/promote.py [--db data/cvi.db]
                                         [--data-dir data] [--as-of 2026-09-12T00:00:00Z]
                                         [--snapshot-id <id>]
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from cvi.engine import score_repository
from cvi.deepdive import build_deep_dive
from cvi.ingest import apply_raw_metrics
from cvi.lakehouse import OperationalStore, repositories_dump_hash
from cvi.models import (
    AIPosture,
    AuthorAssociation,
    Ecosystem,
    LegalIPModel,
    OpenSSFSignals,
    PromotionLadder,
    RawPREvent,
    RepoMeta,
)
from cvi.snapshot.manifest import MANIFEST_FILENAME, SNAPSHOTS_DIRNAME

DATA = Path(__file__).resolve().parent.parent / "data"


def _dt(s: Any) -> Optional[datetime]:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _log1p(count: Any) -> float:
    return math.log10(max(0, int(count or 0)) + 1)


def _p90(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = max(0, math.ceil(0.9 * len(s)) - 1)
    return s[idx]


def _repo_meta(m: dict[str, Any]) -> RepoMeta:
    sig = m.get("openssf_signals")
    signals = OpenSSFSignals(**sig) if sig else None
    return RepoMeta(
        owner=m["owner"], name=m["name"], ecosystem=Ecosystem(m["ecosystem"]),
        stars=int(m.get("stars", 0)), forks=int(m.get("forks", 0)),
        monthly_downloads=int(m.get("monthly_downloads", 0)),
        dependents_count=int(m.get("dependents_count", 0)),
        elephant_factor=int(m.get("elephant_factor", 1)),
        legal_ip_model=LegalIPModel(m.get("legal_ip_model", "DCO")),
        ai_posture=AIPosture(m.get("ai_posture", "AD_HOC")),
        scorecard_score=float(m.get("scorecard_score", 0.0)),
        active_nonbot_commits_180d=int(m.get("active_nonbot_commits_180d", 0)),
        off_platform_reviews=bool(m.get("off_platform_reviews", False)),
        hard_ai_ban=bool(m.get("hard_ai_ban", False)),
        promotion_ladder=PromotionLadder[m.get("promotion_ladder", "AD_HOC")],
        mean_inline_comments=float(m.get("mean_inline_comments", 0.0)),
        active_unclaimed_gfis=int(m.get("active_unclaimed_gfis", 0)),
        rr_90=float(m.get("rr_90", 0.0)),
        dx_ci_minutes=float(m.get("dx_ci_minutes", 0.0)),
        openssf_signals=signals,
    )


def _pr_events(rows: list[dict[str, Any]], as_of: datetime) -> list[RawPREvent]:
    out = []
    for e in rows:
        out.append(RawPREvent(
            repo_owner=e["repo_owner"], repo_name=e["repo_name"],
            pr_number=int(e["pr_number"]), created_at=_dt(e["created_at"]) or as_of,
            author_login=e.get("author_login", ""),
            author_association=AuthorAssociation(e.get("author_association", "NONE")),
            author_prior_merged_commits=int(e.get("author_prior_merged_commits", 0)),
            closed_at=_dt(e.get("closed_at")), merged_at=_dt(e.get("merged_at")),
            is_external=bool(e.get("is_external", False)),
            ci_status_green=bool(e.get("ci_status_green", True)),
            first_human_review_at=_dt(e.get("first_human_review_at")),
            response_latency_hours=e.get("response_latency_hours"),
            maintainer_review_comments=int(e.get("maintainer_review_comments", 0)),
        ))
    return out


def _ttm_p90(events: list[RawPREvent]) -> float:
    lat = [
        (e.merged_at - e.created_at).total_seconds() / 3600.0
        for e in events if e.is_external and e.is_merged and e.merged_at
    ]
    return _p90(lat)


def load_clean(data_dir: Path = DATA) -> list[dict[str, Any]]:
    """Return clean verified bundles (list of {"meta","pr_events",...})."""
    out = []
    for p in sorted((data_dir / "verified").glob("*.json")):
        doc = json.loads(p.read_text(encoding="utf-8"))
        if doc.get("report", {}).get("clean"):
            out.append(doc["bundle"])
    return out


def _no_manifest_message(data_dir: Path, detail: str) -> str:
    """One refusal sentence naming the freeze command (Blocker-3, `03` §5)."""
    return (f"{detail}; promotion requires a frozen snapshot -- freeze first with "
            f"`python -m cvi.snapshot freeze --data-dir {data_dir} --as-of <ISO-8601>`")


def resolve_manifest(data_dir: Path, snapshot_id: Optional[str] = None) -> dict[str, Any]:
    """Load the frozen manifest this promotion binds its ``snapshot_meta`` row to.

    An explicit ``snapshot_id`` names ``<data_dir>/snapshots/<id>/manifest.json``
    directly; omitted, the lexically-newest manifest under
    ``<data_dir>/snapshots/`` is used (the same "newest freeze wins" convention
    ``scripts/serve.py``'s round-1 ``snapshot_identity`` used). Raises
    ``SystemExit`` naming the freeze command when none exists: promotion without
    a snapshot has nothing for ``snapshot_meta`` to record, which is exactly the
    round-1 gap (Warning-2) where a normally promoted database could not be
    served.
    """
    snapshots_dir = Path(data_dir) / SNAPSHOTS_DIRNAME
    if snapshot_id:
        path = snapshots_dir / snapshot_id / MANIFEST_FILENAME
        if not path.is_file():
            raise SystemExit(_no_manifest_message(
                data_dir, f"no manifest for snapshot {snapshot_id!r} at {path}"))
    else:
        candidates = sorted(snapshots_dir.glob(f"*/{MANIFEST_FILENAME}"))
        if not candidates:
            raise SystemExit(_no_manifest_message(
                data_dir, f"no frozen manifest under {snapshots_dir}"))
        path = candidates[-1]
    return json.loads(path.read_text(encoding="utf-8"))


def write_snapshot_meta(db_path: str, manifest: dict[str, Any], dump_hash: str) -> None:
    """Bind ``db_path``'s just-promoted content to ``manifest`` (Blocker-3).

    ``dump_hash`` must be computed by the caller AFTER every promoted row is
    written and committed, so it describes exactly the content being vouched
    for -- :func:`cvi.lakehouse.repositories_dump_hash`, never
    :func:`cvi.snapshot.reproduce.canonical_dump_hash` directly (that would
    hash this very table's own row in). ``promoted_at`` mirrors
    ``manifest["as_of"]`` rather than reading a wall clock (D-VER-3): the
    reference time this promotion scored against already is the answer to
    "when". ``scripts/serve.py`` recomputes the same
    :func:`cvi.lakehouse.repositories_dump_hash` at startup and refuses
    unless it still matches a stored row -- editing ``repositories`` after
    promotion (renaming an owner, nudging a counter) changes the recomputed
    hash, so no stored row matches any more.
    """
    store = OperationalStore(db_path)
    try:
        store.connection.execute(
            "INSERT OR REPLACE INTO snapshot_meta "
            "(snapshot_id, as_of, benchmark_version, constants_hash, "
            "manifest_root, canonical_dump_hash, promoted_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (manifest["snapshot_id"], manifest["as_of"], manifest["benchmark_version"],
             manifest["constants_hash"], manifest["merkle_root"], dump_hash,
             manifest["as_of"]),
        )
        store.connection.commit()
    finally:
        store.close()


def promote(
    bundles: list[dict[str, Any]],
    db_path: str,
    as_of: Optional[datetime] = None,
    data_dir: Path = DATA,
) -> int:
    """Score clean bundles against verified populations; write to SQLite.

    ``as_of`` is the snapshot reference time that every scoring call receives.
    When it is omitted the wall clock is read once, here, and reused for every
    repository, so a run is a pure function of ``(bundles, as_of)``.
    ``last_indexed_at`` takes ``as_of`` too: left to the column default the
    store stamps SQLite's ``CURRENT_TIMESTAMP`` and the database stops being
    reproducible (D-VER-3). ``data_dir`` is where the run's ``promoted.json``
    receipt is written.
    """
    if as_of is None:
        as_of = datetime.now(timezone.utc)
    # pass 1: per-ecosystem populations from the verified universe itself
    deps_pop: dict[str, list[float]] = {}
    dl_pop: dict[str, list[float]] = {}
    ttm_pop: dict[str, list[float]] = {}
    metas: dict[str, tuple[RepoMeta, list[RawPREvent], float]] = {}
    for b in bundles:
        m = b["meta"]
        eco = m["ecosystem"]
        meta = _repo_meta(m)
        events = _pr_events(b.get("pr_events", []), as_of)
        p90 = _ttm_p90(events)
        deps_pop.setdefault(eco, []).append(_log1p(meta.dependents_count))
        dl_pop.setdefault(eco, []).append(_log1p(meta.monthly_downloads))
        if p90 > 0:
            ttm_pop.setdefault(eco, []).append(p90)
        metas[f"{meta.owner}/{meta.name}"] = (meta, events, p90)

    store = OperationalStore(db_path)
    n = 0
    try:
        store.ensure_deep_dive_column()
        for _key, (meta, events, _p90v) in metas.items():
            eco = meta.ecosystem.value
            result = score_repository(
                meta, events, as_of,
                ttm_p90_population=ttm_pop.get(eco, []),
                dependents_population=deps_pop.get(eco, []),
                downloads_population=dl_pop.get(eco, []),
            )
            store.upsert_composite(
                result, pillar_scores=result.pillar_scores, last_indexed_at=as_of)
            apply_raw_metrics(
                store, meta.owner, meta.name, stars=meta.stars, forks=meta.forks,
                monthly_downloads=meta.monthly_downloads,
                dependents_count=meta.dependents_count,
                elephant_factor=meta.elephant_factor,
                legal_ip_model=meta.legal_ip_model, ai_posture=meta.ai_posture)
            # Materialize the spec 12.2 deep-dive so the UI's raw-metrics table,
            # survival curve, and maintainer playbook render on real data
            # (without this the repo-detail endpoint degrades to pillars only).
            store.upsert_deep_dive(
                meta.owner, meta.name,
                build_deep_dive(meta, events, as_of,
                                pillar_scores=result.pillar_scores,
                                overall_cvi=result.overall_cvi))
            n += 1
    finally:
        store.close()
    (data_dir / "promoted.json").write_text(json.dumps(
        {"as_of": as_of.isoformat(),
         "promoted_at": datetime.now(timezone.utc).isoformat(),
         "count": n, "repos": sorted(metas)}, indent=2), encoding="utf-8")
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DATA / "cvi.db"))
    ap.add_argument("--data-dir", type=Path, default=DATA,
                    help="directory holding verified/ and receiving promoted.json")
    ap.add_argument("--as-of", metavar="ISO-8601", default=None,
                    help="snapshot reference time for scoring (default: now, read once)")
    ap.add_argument("--snapshot-id", default=None,
                    help="frozen snapshot to bind snapshot_meta to "
                         "(default: newest under <data-dir>/snapshots/)")
    a = ap.parse_args()
    as_of = _dt(a.as_of) if a.as_of else None
    if a.as_of and as_of is None:
        print(f"--as-of is not an ISO-8601 timestamp: {a.as_of!r}")
        return 2
    manifest = resolve_manifest(a.data_dir, a.snapshot_id)
    bundles = load_clean(a.data_dir)
    if not bundles:
        print("no clean verified bundles to promote; run verify.py first")
        return 1
    n = promote(bundles, a.db, as_of, a.data_dir)
    write_snapshot_meta(a.db, manifest, repositories_dump_hash(a.db))
    print(f"promoted {n} verified repos as of {as_of.isoformat() if as_of else 'now'} "
          f"-> {a.db} (snapshot {manifest['snapshot_id']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
