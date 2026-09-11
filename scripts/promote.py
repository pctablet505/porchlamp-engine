"""Promote CLEAN verified bundles into the OperationalStore (SQLite).

Owner rule: ONLY verified data enters SQLite. Reads data/verified/*.json,
keeps report.clean==True, rebuilds RepoMeta + RawPREvent, scores through the
full engine against verified-universe ECDF populations (per ecosystem), and
upserts composites + raw metrics into data/cvi.db.

Two passes: (1) load all clean bundles, build per-ecosystem populations
(log10 dependents/downloads, per-repo TTM p90); (2) score + write.

Usage: .venv/bin/python scripts/promote.py [--db data/cvi.db]
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from cvi.engine import score_repository
from cvi.ingest import apply_raw_metrics
from cvi.lakehouse import OperationalStore
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

DATA = Path(__file__).resolve().parent.parent / "data"
PROMOTED = DATA / "promoted.json"


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


def _pr_events(rows: list[dict[str, Any]]) -> list[RawPREvent]:
    out = []
    for e in rows:
        out.append(RawPREvent(
            repo_owner=e["repo_owner"], repo_name=e["repo_name"],
            pr_number=int(e["pr_number"]), created_at=_dt(e["created_at"]) or datetime.now(timezone.utc),
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


def promote(bundles: list[dict[str, Any]], db_path: str) -> int:
    """Score clean bundles against verified populations; write to SQLite."""
    # pass 1: per-ecosystem populations from the verified universe itself
    deps_pop: dict[str, list[float]] = {}
    dl_pop: dict[str, list[float]] = {}
    ttm_pop: dict[str, list[float]] = {}
    metas: dict[str, tuple[RepoMeta, list[RawPREvent], float]] = {}
    for b in bundles:
        m = b["meta"]
        eco = m["ecosystem"]
        meta = _repo_meta(m)
        events = _pr_events(b.get("pr_events", []))
        p90 = _ttm_p90(events)
        deps_pop.setdefault(eco, []).append(_log1p(meta.dependents_count))
        dl_pop.setdefault(eco, []).append(_log1p(meta.monthly_downloads))
        if p90 > 0:
            ttm_pop.setdefault(eco, []).append(p90)
        metas[f"{meta.owner}/{meta.name}"] = (meta, events, p90)

    store = OperationalStore(db_path)
    n = 0
    try:
        for _key, (meta, events, _p90v) in metas.items():
            eco = meta.ecosystem.value
            result = score_repository(
                meta, events, datetime.now(timezone.utc),
                ttm_p90_population=ttm_pop.get(eco, []),
                dependents_population=deps_pop.get(eco, []),
                downloads_population=dl_pop.get(eco, []),
            )
            store.upsert_composite(result, pillar_scores=result.pillar_scores)
            apply_raw_metrics(
                store, meta.owner, meta.name, stars=meta.stars, forks=meta.forks,
                monthly_downloads=meta.monthly_downloads,
                dependents_count=meta.dependents_count,
                elephant_factor=meta.elephant_factor,
                legal_ip_model=meta.legal_ip_model, ai_posture=meta.ai_posture)
            n += 1
    finally:
        store.close()
    PROMOTED.write_text(json.dumps(
        {"promoted_at": datetime.now(timezone.utc).isoformat(),
         "count": n, "repos": sorted(metas)}, indent=2), encoding="utf-8")
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DATA / "cvi.db"))
    a = ap.parse_args()
    bundles = load_clean()
    if not bundles:
        print("no clean verified bundles to promote; run verify.py first")
        return 1
    n = promote(bundles, a.db)
    print(f"promoted {n} verified repos -> {a.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
