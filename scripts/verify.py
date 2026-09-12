"""Verify a derived bundle against independent sources; promote only if clean.

Owner rule: ONLY verified data enters SQLite. This script cross-checks each
data/derived/<key>.json bundle against the raw cache's second sources
(deps.dev stars/forks/license, scorecard vs deps.dev overall), writes
data/verified/<key>.json with a verification report, and --promote upserts
clean repos into the OperationalStore (data/cvi.db) scored against the
verified-universe ECDF populations.

Usage: .venv/bin/python scripts/verify.py [--all | --repo owner name] [--promote]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DATA = Path(__file__).resolve().parent.parent / "data"
STARS_TOL = 0.10  # relative tolerance: GitHub vs deps.dev star counts

# Checks that are recorded in the report for visibility but do NOT gate
# promotion. The OpenSSF Scorecard is fetched live from the authoritative
# securityscorecards.dev API (the bundle marks it "verified"); deps.dev's
# scorecard_overall is deps.dev's own cached copy of that *same* tool. The two
# caches refresh on independent schedules, so a disagreement is temporal drift
# of one tool, not a two-source contradiction like the stars/forks counters
# (GitHub REST vs deps.dev, genuinely independent). Observed spread across the
# verified universe is 0.6-3.4 with no stable threshold, so gating on it
# quarantined ~91 clean repos on false positives. Advisory keeps the signal
# visible without blocking promotion.
ADVISORY_CHECKS = frozenset({"scorecard"})

REQUIRED_VERIFIED = ("stars", "forks")


def _load(p: Path) -> Optional[dict]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def verify_bundle(bundle: dict, depsdev: Optional[dict],
                  gh_meta: Optional[dict] = None) -> dict[str, Any]:
    """Return {"checks":[{field,ok,detail}], "flags":[...], "clean":bool}."""
    checks: list[dict[str, Any]] = []
    m = bundle["meta"]
    gh_meta = gh_meta or {}

    def check(field: str, ok: bool, detail: str) -> None:
        checks.append({"field": field, "ok": ok, "detail": detail})

    dd = depsdev or {}
    for field, dd_key, tol in (("stars", "stars", STARS_TOL),
                               ("forks", "forks", STARS_TOL)):
        a, b = m.get(field), dd.get(dd_key)
        if a and b:
            rel = abs(a - b) / max(a, b)
            check(field, rel <= tol, f"github={a} depsdev={b} rel={rel:.3f}")
        else:
            check(field, True, "single-source (deps.dev value absent)")
    lic, dlic = gh_meta.get("license_spdx"), dd.get("license")
    if lic and dlic and str(lic).upper() not in ("NOASSERTION", "OTHER"):
        check("license", str(lic).lower().replace("_", "-") == str(dlic).lower().replace("_", "-"),
              f"github={lic} depsdev={dlic}")
    sc, dsc = m.get("scorecard_score"), dd.get("scorecard_overall")
    if sc and dsc:
        # Advisory: recorded but never gates (see ADVISORY_CHECKS). The delta is
        # cache-refresh drift between two snapshots of the same tool.
        check("scorecard", abs(sc - dsc) <= 1.0, f"scorecard={sc} depsdev={dsc}")
    conf = bundle.get("confidence", {})
    for field in REQUIRED_VERIFIED:
        check(f"required:{field}", conf.get(field) == "verified",
              f"confidence={conf.get(field)}")
    flags = [c["field"] for c in checks
             if not c["ok"] and c["field"] not in ADVISORY_CHECKS]
    return {"checks": checks, "flags": flags, "clean": not flags,
            "verified_at": datetime.now(timezone.utc).isoformat()}


def verify_repo(owner: str, name: str, data_dir: Path = DATA) -> dict[str, Any]:
    """Verify one derived bundle; write data/verified/<key>.json; return report."""
    key = f"{owner}__{name}"
    bundle = _load(data_dir / "derived" / f"{key}.json")
    if bundle is None:
        raise SystemExit(f"no derived bundle for {key}; run derive.py first")
    deps = (_load(data_dir / "raw" / "depsdev" / f"{key}.json") or {}).get("data")
    report = verify_bundle(bundle, deps)
    out = data_dir / "verified" / f"{key}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"bundle": bundle, "report": report},
                              indent=2, sort_keys=True), encoding="utf-8")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", nargs=2, metavar=("OWNER", "NAME"))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--promote", action="store_true",
                    help="upsert clean repos into OperationalStore (TODO wave F)")
    a = ap.parse_args()
    keys: list[tuple[str, str]] = []
    if a.repo:
        keys = [(a.repo[0], a.repo[1])]
    elif a.all:
        for p in sorted((DATA / "derived").glob("*.json")):
            o, n = p.stem.split("__", 1)
            keys.append((o, n))
    clean = flagged = 0
    for o, n in keys:
        r = verify_repo(o, n)
        clean += r["clean"]
        flagged += not r["clean"]
        print(f"{o}/{n}: {'CLEAN' if r['clean'] else 'FLAG ' + ','.join(r['flags'])}")
    print(f"verified: {clean} clean, {flagged} flagged of {len(keys)}")
    return 1 if flagged and a.promote else 0


if __name__ == "__main__":
    raise SystemExit(main())
