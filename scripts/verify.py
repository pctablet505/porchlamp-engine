"""Verify a derived bundle against independent sources; promote only if clean.

Owner rule: ONLY verified data enters SQLite. This script cross-checks each
data/derived/<key>.json bundle against the raw cache's second sources
(deps.dev stars/forks/license, scorecard vs deps.dev overall), writes
data/verified/<key>.json with a verification report, and --promote upserts
clean repos into the OperationalStore (data/cvi.db) scored against the
verified-universe ECDF populations.

Provenance is part of verification (`03` §5, R-4): every field whose confidence
says ``verified`` must name either an envelope under ``raw/`` whose hash
recomputes with the packet W0-3 helper, or an ``http(s)://`` URL with a
recorded sha256. A source that is a word (``ecosystem-intelligence``), a bare
URL with no hash, or nothing at all is not re-checkable and flags
``unverifiable_provenance``, which blocks promotion.

Usage: .venv/bin/python scripts/verify.py [--all | --repo owner name] [--promote]
                                         [--data-dir data]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from cvi.collect.envelope import read_envelope

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

#: Flag name from `03` §5 for provenance that cannot be re-checked from disk.
UNVERIFIABLE_PROVENANCE = "unverifiable_provenance"

#: Directory under a data dir that holds the envelopes provenance may point at.
ENVELOPE_DIRNAME = "raw"

#: Length of a SHA-256 hex digest, so a ``#`` in a path is not read as a hash.
SHA256_HEX_LENGTH = 64


def _load(p: Path) -> Optional[dict]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _hash_stripped(source: str) -> str:
    """Drop a ``#<sha256>`` suffix: the path part of a W0-3 provenance string."""
    path, separator, digest = source.rpartition("#")
    if path and separator and len(digest) == SHA256_HEX_LENGTH:
        return path
    return source


def _envelope_path(source: str, data_dir: Path) -> Optional[Path]:
    """Resolve a provenance ``source`` to an envelope under ``<data_dir>/raw``.

    Absolute, data-dir-relative and copied-dir forms all resolve: a bundle keeps
    the absolute path it was collected with, so the part from ``raw`` onward is
    tried against this data dir too. That is the same reason a manifest records
    envelope paths relative to its data dir (packet W0-3): a copied sample must
    verify against its own copy, never against the directory it came from.

    Returns ``None`` when the source is not a path under ``raw`` at all — a word
    such as ``ecosystem-intelligence``, an ``https://`` URL, ``None``.
    """
    raw = (Path(data_dir) / ENVELOPE_DIRNAME).resolve()
    recorded = Path(_hash_stripped(source))
    candidates = [recorded] if recorded.is_absolute() else [Path(data_dir) / recorded]
    parts = recorded.parts
    if ENVELOPE_DIRNAME in parts:
        start = len(parts) - 1 - list(reversed(parts)).index(ENVELOPE_DIRNAME)
        candidates.append(raw.joinpath(*parts[start + 1:]))
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file() and resolved.is_relative_to(raw):
            return resolved
    return None


def _claimed_hash(entry: Any, source: str) -> Optional[str]:
    """Hash the provenance repeats for ``source``, in either W0-3 form.

    :attr:`cvi.collect.envelope.Envelope.provenance` writes ``"<path>#<sha256>"``;
    :func:`scripts.derive` writes a ``sha256`` key. Either way the value must be
    the hash of the envelope's ``data`` field; ``None`` means the provenance
    repeats no hash, so the envelope's own (recorded or read-time backfilled)
    hash is the one the chain carries.
    """
    if isinstance(entry, dict):
        recorded = entry.get("sha256")
        if isinstance(recorded, str) and len(recorded) == SHA256_HEX_LENGTH:
            return recorded
    path, separator, digest = source.rpartition("#")
    if separator and path and len(digest) == SHA256_HEX_LENGTH:
        return digest
    return None


def provenance_offender(field: str, entry: Any, data_dir: Path = DATA) -> Optional[str]:
    """Return why ``field``'s provenance cannot be re-checked, or ``None``.

    The detail names the field and what was found, so a flagged bundle says
    which field is unverifiable and where its provenance pointed. `03` §5
    accepts two forms: an envelope path under ``raw/`` whose hash recomputes,
    or an ``http(s)://`` URL with a recorded sha256 (a fetch the pipeline
    hashed at collection time without archiving a full envelope file).
    """
    source = entry.get("source") if isinstance(entry, dict) else entry
    if not isinstance(source, str) or not source:
        return f"field={field} source={source!r} is not an envelope path"
    if source.startswith(("http://", "https://")):
        if _claimed_hash(entry, source):
            return None
        return (f"field={field} source={source!r} is an http(s) URL with no "
                "recorded sha256, so it is not re-checkable")
    path = _envelope_path(source, data_dir)
    if path is None:
        return (f"field={field} source={source!r} is not an envelope under "
                f"{Path(data_dir) / ENVELOPE_DIRNAME}")
    try:
        envelope = read_envelope(path)
    except (json.JSONDecodeError, OSError) as error:
        return f"field={field} envelope={path} is unreadable: {type(error).__name__}"
    if not envelope.hash_matches_data:
        return (f"field={field} envelope={path} records sha256="
                f"{envelope.recorded_sha256} whose data hashes to {envelope.computed_sha256}")
    claimed = _claimed_hash(entry, source)
    if claimed and claimed != envelope.sha256:
        return (f"field={field} provenance sha256={claimed} but {path} hashes to "
                f"{envelope.sha256}")
    return None


def provenance_checks(bundle: dict, data_dir: Path = DATA) -> list[dict[str, Any]]:
    """One failing check per ``verified`` field whose provenance is not verifiable.

    Only fields that claim ``verified`` are checked: ``derived`` and ``missing``
    fields make no verification claim, so a URL, a bare word or no source at all
    is what they legitimately carry (``ecosystem`` comes from the collector's
    arguments, ``ai_posture`` from a README URL read by hand).
    """
    provenance = bundle.get("provenance") or {}
    confidence = bundle.get("confidence") or {}
    checks: list[dict[str, Any]] = []
    for field, level in sorted(confidence.items()):
        if level != "verified":
            continue
        detail = provenance_offender(field, provenance.get(field), data_dir)
        if detail:
            checks.append({"field": UNVERIFIABLE_PROVENANCE, "ok": False, "detail": detail})
    return checks


def verify_bundle(bundle: dict, depsdev: Optional[dict],
                  gh_meta: Optional[dict] = None,
                  data_dir: Path = DATA) -> dict[str, Any]:
    """Return {"checks":[{field,ok,detail}], "flags":[...], "clean":bool}."""
    checks: list[dict[str, Any]] = []
    m = bundle["meta"]
    gh_meta = gh_meta or {}

    def check(field: str, ok: bool, detail: str) -> None:
        checks.append({"field": field, "ok": ok, "detail": detail})

    dd = depsdev or {}
    # Stars/forks are monotonically non-decreasing. GitHub is the live primary;
    # deps.dev is a periodically-refreshed cache, so a cached value BELOW the
    # live one is expected staleness on a growing repo (observed: 13/14
    # mismatches, all viral repos like iptv-org/iptv 45,903 -> 138,350). Only
    # the impossible direction — cache ABOVE live by > tol — means the two
    # sources genuinely disagree (wrong repo mapping / corrupt data).
    for field, dd_key in (("stars", "stars"), ("forks", "forks")):
        a, b = m.get(field), dd.get(dd_key)
        if a and b:
            rel = abs(a - b) / max(a, b)
            ok = rel <= STARS_TOL or b < a
            check(field, ok, f"github={a} depsdev={b} rel={rel:.3f}")
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
    checks.extend(provenance_checks(bundle, data_dir))
    flags = list(dict.fromkeys(c["field"] for c in checks
                               if not c["ok"] and c["field"] not in ADVISORY_CHECKS))
    return {"checks": checks, "flags": flags, "clean": not flags,
            "verified_at": datetime.now(timezone.utc).isoformat()}


def verify_repo(owner: str, name: str, data_dir: Path = DATA) -> dict[str, Any]:
    """Verify one derived bundle; write data/verified/<key>.json; return report."""
    key = f"{owner}__{name}"
    bundle = _load(data_dir / "derived" / f"{key}.json")
    if bundle is None:
        raise SystemExit(f"no derived bundle for {key}; run derive.py first")
    deps = (_load(data_dir / "raw" / "depsdev" / f"{key}.json") or {}).get("data")
    report = verify_bundle(bundle, deps, data_dir=data_dir)
    out = data_dir / "verified" / f"{key}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"bundle": bundle, "report": report},
                              indent=2, sort_keys=True), encoding="utf-8")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", nargs=2, metavar=("OWNER", "NAME"))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--data-dir", type=Path, default=DATA,
                    help="data dir (or copied sample) holding derived/ and raw/")
    ap.add_argument("--promote", action="store_true",
                    help="upsert clean repos into OperationalStore (TODO wave F)")
    a = ap.parse_args()
    keys: list[tuple[str, str]] = []
    if a.repo:
        keys = [(a.repo[0], a.repo[1])]
    elif a.all:
        for p in sorted((a.data_dir / "derived").glob("*.json")):
            o, n = p.stem.split("__", 1)
            keys.append((o, n))
    clean = flagged = 0
    for o, n in keys:
        r = verify_repo(o, n, a.data_dir)
        clean += r["clean"]
        flagged += not r["clean"]
        print(f"{o}/{n}: {'CLEAN' if r['clean'] else 'FLAG ' + ','.join(r['flags'])}")
    print(f"verified: {clean} clean, {flagged} flagged of {len(keys)}")
    return 1 if flagged and a.promote else 0


if __name__ == "__main__":
    raise SystemExit(main())
