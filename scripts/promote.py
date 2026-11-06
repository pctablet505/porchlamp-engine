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

Manifest binding (`03` §5): promotion requires a frozen snapshot manifest
(``python -m porchlamp.snapshot freeze``) and writes a ``snapshot_meta`` row binding
the database's post-promotion content (:func:`porchlamp.lakehouse.repositories_dump_hash`)
to that manifest's identity, so ``scripts/serve.py`` can tell a
promoted-then-edited database from one that still matches what was promoted
-- a per-row telemetry check cannot survive a bulk ``UPDATE``, a content hash
of the whole table can.

Manifest coverage invariants (`03` §5): promotion is bound to the manifest's own
envelope set. :func:`split_by_manifest` keeps only clean bundles whose ``verified``
fields' envelopes are ALL listed in the manifest with a matching sha256
(:func:`_bundle_envelope_refs` reuses ``scripts/verify.py``'s own path/hash
resolution); a manifest covering none of the clean bundles refuses promotion
outright. ``--snapshot-id`` is required (no implicit fallback). ``--as-of`` must
agree with the bound manifest's own ``as_of`` -- one snapshot names one reference
time; :func:`resolved_as_of` refuses a mismatch instead of letting
``snapshot_meta`` record a value nothing scored against.

Atomicity and purity: :func:`promote_atomically` builds into a fresh
``<db>.tmp-<snapshot_id>`` file, re-runs ``scripts/serve.py``'s own refusal
checks against it, and only then ``os.replace``s it onto the served path --
one atomic filesystem rename. On any failure the temp file is discarded and
the served database is untouched, byte-identical to before the call.

Usage: .venv/bin/python scripts/promote.py --db data/cvi.db --data-dir data
                                         --snapshot-id <id> [--as-of 2026-09-12T00:00:00Z]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from porchlamp.engine import score_repository
from porchlamp.deepdive import build_deep_dive
from porchlamp.lakehouse import OperationalStore, apply_raw_metrics, repositories_dump_hash
from porchlamp.models import (
    AIPosture,
    AuthorAssociation,
    Ecosystem,
    LegalIPModel,
    OpenSSFSignals,
    PromotionLadder,
    RawPREvent,
    RepoMeta,
)
from porchlamp.snapshot.manifest import MANIFEST_FILENAME, SNAPSHOTS_DIRNAME
from scripts.serve import refusal_reasons as _serve_refusal_reasons
from scripts.verify import _claimed_hash, _envelope_path, _source_items

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


_PROMOTION_LADDER_MAP: dict[str, PromotionLadder] = {
    "VERIFIED": PromotionLadder.VERIFIED_PROMOTION,
    "VERIFIED_PROMOTION": PromotionLadder.VERIFIED_PROMOTION,
    "DOCUMENTED": PromotionLadder.DOCUMENTED_PATHWAY,
    "DOCUMENTED_PATHWAY": PromotionLadder.DOCUMENTED_PATHWAY,
    "AD_HOC": PromotionLadder.AD_HOC,
    "CLOSED": PromotionLadder.CLOSED_SINGLE_FIRM,
    "CLOSED_SINGLE_FIRM": PromotionLadder.CLOSED_SINGLE_FIRM,
}


def _parse_promotion_ladder(val: Any) -> PromotionLadder:
    if isinstance(val, PromotionLadder):
        return val
    if isinstance(val, str):
        return _PROMOTION_LADDER_MAP.get(val, PromotionLadder.AD_HOC)
    return PromotionLadder.AD_HOC


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
        promotion_ladder=_parse_promotion_ladder(m.get("promotion_ladder")),
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
    """One refusal sentence naming the freeze command (`03` §5)."""
    return (f"{detail}; promotion requires a frozen snapshot -- freeze first with "
            f"`python -m porchlamp.snapshot freeze --data-dir {data_dir} --as-of <ISO-8601>`")


def resolve_manifest(data_dir: Path, snapshot_id: str) -> dict[str, Any]:
    """Load the frozen manifest named by ``snapshot_id`` (`03` §5).

    ``snapshot_id`` is required, with no "pick a manifest for me" fallback:
    defaulting to the lexically-newest ``<data_dir>/snapshots/*`` directory
    name would silently prefer a forward-dated or oddly-named manifest over
    the one a run had just frozen (a directory named ``HOLLOW-SNAPSHOT``
    sorts after every real ``YYYYMMDD...`` name).
    ``scripts/run_all.py`` passes the id its own freeze step reported;
    ``scripts/promote.py``'s CLI requires ``--snapshot-id`` for the same
    reason. Raises ``SystemExit`` naming the freeze command when the id
    names no manifest: promotion without one has nothing for
    ``snapshot_meta`` to record.
    """
    path = Path(data_dir) / SNAPSHOTS_DIRNAME / snapshot_id / MANIFEST_FILENAME
    if not path.is_file():
        raise SystemExit(_no_manifest_message(
            data_dir, f"no manifest for snapshot {snapshot_id!r} at {path}"))
    return json.loads(path.read_text(encoding="utf-8"))


def resolved_as_of(cli_as_of: Optional[str], manifest: dict[str, Any]) -> datetime:
    """The single reference time this promotion scores against (`03` §5).

    A snapshot names one ``as_of``; promotion must score against exactly
    that time, not a second, independently-chosen one. Omitted ``--as-of``
    defaults to the manifest's own ``as_of``. A supplied ``--as-of`` must
    parse and must equal the manifest's ``as_of`` (compared as instants, not
    strings, so ``Z`` and ``+00:00`` agree); a mismatch is refused rather
    than stamped onto rows while ``snapshot_meta`` records the manifest's
    own time.
    """
    manifest_as_of = _dt(manifest.get("as_of"))
    if manifest_as_of is None:
        raise SystemExit(f"manifest {manifest.get('snapshot_id')!r} has an "
                          f"unparseable as_of: {manifest.get('as_of')!r}")
    if not cli_as_of:
        return manifest_as_of
    as_of = _dt(cli_as_of)
    if as_of is None:
        raise SystemExit(f"--as-of is not an ISO-8601 timestamp: {cli_as_of!r}")
    if as_of != manifest_as_of:
        raise SystemExit(
            f"--as-of {as_of.isoformat()} does not match manifest "
            f"{manifest['snapshot_id']!r}'s as_of {manifest_as_of.isoformat()} "
            "-- one snapshot, one reference time")
    return as_of


def _manifest_envelope_index(manifest: dict[str, Any]) -> dict[tuple[str, str], str]:
    """``(collector, key) -> sha256`` for every envelope ``manifest`` froze."""
    return {(e["collector"], e["key"]): e["sha256"] for e in manifest.get("envelopes", [])}


def _bundle_envelope_refs(bundle: dict[str, Any],
                          data_dir: Path) -> list[tuple[str, str, Optional[str]]]:
    """``(collector, key, claimed_sha256)`` for every ``verified`` field's
    envelope reference in ``bundle`` (`03` §5: "promotion reads
    only envelopes in the manifest").

    Restricted to ``verified`` fields -- the same filter
    ``scripts/verify.py``'s ``provenance_checks`` applies -- because those
    are the only claims a CLEAN bundle's ``report.clean`` actually vouches
    for; a list-valued ``derived`` field such as ``pr_events`` carries no
    hash at all (its provenance is written directly, bypassing
    ``Bundle.set``) and would make every bundle fail this check if it were
    included. Resolution reuses ``scripts/verify.py``'s own helpers
    (:func:`scripts.verify._envelope_path`, :func:`scripts.verify._source_items`,
    :func:`scripts.verify._claimed_hash`) so there is one definition of what
    an envelope path is and how its hash is read.
    """
    refs: list[tuple[str, str, Optional[str]]] = []
    provenance = bundle.get("provenance") or {}
    confidence = bundle.get("confidence") or {}
    for field, level in confidence.items():
        if level != "verified":
            continue
        for source, hash_hint in _source_items(provenance.get(field)):
            if not isinstance(source, str) or not source:
                continue
            path = _envelope_path(source, data_dir)
            if path is None:
                continue
            claimed = _claimed_hash(source, hash_hint)
            refs.append((path.parent.name, path.stem, claimed))
    return refs


def _covered_by_manifest(bundle: dict[str, Any], data_dir: Path,
                         manifest_index: dict[tuple[str, str], str]) -> bool:
    """Whether every one of ``bundle``'s envelope references is in ``manifest_index``
    with a matching sha256. A bundle with zero resolvable references fails
    closed (``False``): there is nothing to check it against, which is not
    the same as nothing being wrong."""
    refs = _bundle_envelope_refs(bundle, data_dir)
    return bool(refs) and all(
        claimed is not None and manifest_index.get((collector, key)) == claimed
        for collector, key, claimed in refs
    )


def split_by_manifest(bundles: list[dict[str, Any]], data_dir: Path,
                      manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition CLEAN ``bundles`` into ``(covered, not_in_manifest)`` against
    ``manifest`` (`03` §5): the manifest is the promotion
    boundary, not a label copied into ``snapshot_meta`` for show. A manifest
    with zero envelopes (or one missing/altering an entry a bundle depends
    on) covers zero or fewer bundles; ``main`` refuses outright when
    ``covered`` is empty rather than silently promoting the whole universe.
    """
    index = _manifest_envelope_index(manifest)
    covered: list[dict[str, Any]] = []
    not_in_manifest: list[dict[str, Any]] = []
    for bundle in bundles:
        (covered if _covered_by_manifest(bundle, data_dir, index) else not_in_manifest).append(bundle)
    return covered, not_in_manifest


def write_snapshot_meta(db_path: str, manifest: dict[str, Any], dump_hash: str) -> None:
    """Bind ``db_path``'s just-promoted content to ``manifest``.

    ``dump_hash`` must be computed by the caller AFTER every promoted row is
    written and committed, so it describes exactly the content being vouched
    for -- :func:`porchlamp.lakehouse.repositories_dump_hash`, never
    :func:`porchlamp.snapshot.reproduce.canonical_dump_hash` directly (that would
    hash this very table's own row in). ``promoted_at`` mirrors
    ``manifest["as_of"]``, which is safe to treat as "the reference time this
    promotion scored against" ONLY because :func:`resolved_as_of` (`03` §5)
    already refused any ``--as-of`` that disagreed with the manifest's own.
    ``scripts/serve.py``
    recomputes the same :func:`porchlamp.lakehouse.repositories_dump_hash` at
    startup and refuses unless it still matches a stored row -- editing
    ``repositories`` after promotion (renaming an owner, nudging a counter)
    changes the recomputed hash, so no stored row matches any more.
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

    This function upserts into ``db_path`` as given -- it does not by
    itself guarantee purity against prior content, and it never truncates:
    a promotion that failed after an in-place ``DELETE`` and before
    finishing would leave the live database rewritten and unservable with
    no rollback. Purity is the caller's job -- :func:`promote_atomically`
    builds into a fresh temp file (nothing to truncate: the file did not
    exist) and only swaps it onto the served path once every check passes.
    Call this function directly only against a path you already know is
    disposable (a fresh temp file, a test's own ``tmp_path``).
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
                                overall_porchlamp=result.overall_porchlamp))
            n += 1
    finally:
        store.close()
    (data_dir / "promoted.json").write_text(json.dumps(
        {"as_of": as_of.isoformat(),
         "promoted_at": datetime.now(timezone.utc).isoformat(),
         "count": n, "repos": sorted(metas)}, indent=2), encoding="utf-8")
    return n


def promote_atomically(bundles: list[dict[str, Any]], db_path: str, as_of: datetime,
                       data_dir: Path, manifest: dict[str, Any]) -> int:
    """Promote ``bundles`` into ``db_path`` without ever rewriting the live
    file until every check passes (`03` §5).

    Builds the whole promotion -- scoring, ``snapshot_meta`` -- into
    ``<db_path>.tmp-<snapshot_id>``, a brand-new file next to ``db_path``,
    never the served file itself. Purity against prior content is a property
    of never having opened the old file at all, rather than of an in-place
    ``DELETE``: a promotion that failed after truncating and before finishing
    would leave the live database rewritten and unservable with no rollback.

    Before the swap, re-runs :func:`scripts.serve.refusal_reasons` -- the
    same fixture-row and identity-hash checks ``scripts/serve.py`` runs at
    startup -- against the TEMP file, so a bug that produced an
    inconsistent promotion is caught here, not at the next ``serve.py``
    launch. Only then does ``os.replace`` move the temp file onto
    ``db_path``: one filesystem rename, atomic on POSIX. On any failure
    (a scoring exception, a self-check failure) the temp file is removed
    and ``db_path`` is left exactly as it was -- byte-identical, not
    partially rewritten.
    """
    tmp_path = f"{db_path}.tmp-{manifest['snapshot_id']}"
    Path(tmp_path).unlink(missing_ok=True)
    ready = False
    try:
        n = promote(bundles, tmp_path, as_of, data_dir)
        write_snapshot_meta(tmp_path, manifest, repositories_dump_hash(tmp_path))
        store = OperationalStore(tmp_path)
        try:
            reasons = _serve_refusal_reasons(store, tmp_path)
        finally:
            store.close()
        if reasons:
            raise RuntimeError(
                f"promotion self-check failed for {tmp_path}: " + "; ".join(reasons))
        ready = True
    finally:
        for sidecar in ("-wal", "-shm"):
            Path(f"{tmp_path}{sidecar}").unlink(missing_ok=True)
        if ready:
            for sidecar in ("-wal", "-shm"):
                Path(f"{db_path}{sidecar}").unlink(missing_ok=True)
            os.replace(tmp_path, db_path)
            for sidecar in ("-wal", "-shm"):
                Path(f"{db_path}{sidecar}").unlink(missing_ok=True)
        else:
            Path(tmp_path).unlink(missing_ok=True)
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DATA / "porchlamp.db"))
    ap.add_argument("--data-dir", type=Path, default=DATA,
                    help="directory holding verified/ and receiving promoted.json")
    ap.add_argument("--as-of", metavar="ISO-8601", default=None,
                    help="snapshot reference time for scoring (default: the "
                         "bound manifest's own as_of; must agree with it if given)")
    ap.add_argument("--snapshot-id", required=True,
                    help="frozen snapshot to promote from and bind snapshot_meta "
                         "to (required -- no default)")
    a = ap.parse_args()
    manifest = resolve_manifest(a.data_dir, a.snapshot_id)
    as_of = resolved_as_of(a.as_of, manifest)
    bundles = load_clean(a.data_dir)
    if not bundles:
        print("no clean verified bundles to promote; run verify.py first")
        return 1
    covered, not_in_manifest = split_by_manifest(bundles, a.data_dir, manifest)
    if not covered:
        print(f"manifest {manifest['snapshot_id']} covers 0 of {len(bundles)} clean bundles")
        return 1
    if not_in_manifest:
        print(f"{len(not_in_manifest)} of {len(bundles)} clean bundles not in manifest "
              f"{manifest['snapshot_id']}: not promoted")
    n = promote_atomically(covered, a.db, as_of, a.data_dir, manifest)
    print(f"promoted {n} verified repos as of {as_of.isoformat()} "
          f"-> {a.db} (snapshot {manifest['snapshot_id']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
