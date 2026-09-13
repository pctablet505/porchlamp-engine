"""Run the full verified-data pipeline over the candidate universe.

Chains, per candidate repo: gather (raw cache) -> derive (feature bundle) ->
verify (cross-source). Then freezes a snapshot manifest and promotes all CLEAN
bundles into SQLite once. Checkpointed via the raw cache (existing envelopes
are skipped), so reruns resume. --skip-downloads runs a fast pass (GitHub +
deps.dev + scorecard + npm/crates) and defers pypistats (4/min) to a later
backfill.

Round 2 (`03` §5, Blocker-3, Warning-2): promotion now requires a frozen
snapshot (``scripts/promote.py --snapshot-id``), so the documented pipeline
gains a freeze step between verify and promote -- gather -> derive -> verify
-> freeze -> promote -> serve -- rather than leaving that step to be
discovered only from ``scripts/serve.py``'s own refusal message.

Round 3 (Warning-1, `03` §5): :func:`freeze_and_promote` passes the id ITS
OWN freeze call reported straight into ``--snapshot-id``, rather than having
``promote.resolve_manifest`` re-discover "the newest manifest" by directory
name -- a forward-dated or stray manifest already under ``snapshots/`` used
to win that lexical sort over the run's own freeze, silently.

Usage:
  .venv/bin/python scripts/run_all.py --limit 50 --skip-downloads
  .venv/bin/python scripts/run_all.py --all --db data/cvi.db
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from cvi.snapshot import cli as snapshot_cli

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_script(mod_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(mod_name, ROOT / "scripts" / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gather = _load_script("gather", "gather.py")
derive = _load_script("derive", "derive.py")
verify = _load_script("verify", "verify.py")
promote = _load_script("promote", "promote.py")


def _candidates(limit: int | None) -> list[tuple[str, str, str]]:
    out = []
    for line in gather.CANDIDATES.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("owner"):
            out.append((r["owner"], r["name"], r["ecosystem"]))
        if limit and len(out) >= limit:
            break
    return out


def run_one(owner: str, name: str, eco: str, skip_pypistats: bool) -> str:
    """gather->derive->verify one repo; return CLEAN|FLAG|FAIL."""
    gather.gather_repo(owner, name, eco, skip_pypistats=skip_pypistats)
    bundle = derive.derive_repo(owner, name, derive.Ecosystem(eco))
    out = derive.DATA_DIR / "derived" / f"{owner}__{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle, indent=2, sort_keys=True), encoding="utf-8")
    report = verify.verify_repo(owner, name)
    return "CLEAN" if report["clean"] else "FLAG"


def freeze_and_promote(db: str, data_dir: Path, as_of: datetime) -> int:
    """Freeze ``data_dir`` at ``as_of``, then promote CLEAN bundles under the
    id THAT freeze just reported (round 3, Warning-1, `03` §5) -- never by
    re-discovering "the newest manifest" (round 2's bug: a forward-dated or
    stray manifest already under ``snapshots/`` wins that lexical sort over
    the run's own freeze). ``as_of`` is read once by the caller and reused
    for both the freeze and the scoring, so :func:`promote.resolved_as_of`'s
    match-the-manifest check always passes here by construction. Returns the
    process exit code (0 on success).
    """
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        freeze_rc = snapshot_cli.main(
            ["freeze", "--data-dir", str(data_dir), "--as-of", as_of.isoformat()])
    output = captured.getvalue()
    print(output, end="")
    if freeze_rc != 0:
        print(f"freeze failed (exit {freeze_rc}); not promoting")
        return freeze_rc
    snapshot_line = next((line for line in output.splitlines()
                         if line.startswith("snapshot ")), None)
    if snapshot_line is None:
        print("freeze produced no snapshot id; not promoting")
        return 1
    snapshot_id = snapshot_line.split(" ", 1)[1]
    manifest = promote.resolve_manifest(data_dir, snapshot_id)
    bundles = promote.load_clean(data_dir)
    if not bundles:
        print("no clean verified bundles to promote; run verify.py first")
        return 1
    covered, not_in_manifest = promote.split_by_manifest(bundles, data_dir, manifest)
    if not covered:
        print(f"manifest {manifest['snapshot_id']} covers 0 of {len(bundles)} clean bundles")
        return 1
    if not_in_manifest:
        print(f"{len(not_in_manifest)} of {len(bundles)} clean bundles not in manifest "
              f"{manifest['snapshot_id']}: not promoted")
    n = promote.promote_atomically(covered, db, as_of, data_dir, manifest)
    print(f"promoted {n} verified repos -> {db} (snapshot {manifest['snapshot_id']})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--skip-downloads", action="store_true",
                    help="defer pypistats (4/min) to a backfill pass")
    ap.add_argument("--db", default=str(gather.DATA.parent / "cvi.db"))
    a = ap.parse_args()
    repos = _candidates(None if a.all else a.limit)
    tally = {"CLEAN": 0, "FLAG": 0, "FAIL": 0}
    for i, (owner, name, eco) in enumerate(repos, 1):
        try:
            result = run_one(owner, name, eco, a.skip_downloads)
        except Exception as e:  # review-ok: gate-network — one repo must not abort the sweep
            result = "FAIL"
            print(f"  [{i}] {owner}/{name}: {type(e).__name__}: {str(e)[:120]}")
        tally[result] += 1
        if i % 25 == 0 or result != "CLEAN":
            print(f"[{i}/{len(repos)}] {owner}/{name} {eco}: {result} | {tally}")
    print(f"sweep done: {tally}")
    if not a.skip_downloads:
        return freeze_and_promote(a.db, gather.DATA.parent, datetime.now(timezone.utc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
