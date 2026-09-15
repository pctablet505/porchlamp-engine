"""Verify a derived bundle against independent sources; promote only if clean.

Owner rule: ONLY verified data enters SQLite. This script cross-checks each
data/derived/<key>.json bundle against the raw cache's second sources
(deps.dev stars/forks/license, scorecard vs deps.dev overall), writes
data/verified/<key>.json with a verification report, and --promote upserts
clean repos into the OperationalStore (data/cvi.db) scored against the
verified-universe ECDF populations.

Provenance is part of verification (`03` §5): every field whose confidence
says ``verified`` must name an envelope under ``raw/<collector>/<key>.json``
where ``key`` is the bundle's own ``<owner>__<name>`` (``<ecosystem>__<name>``
for the downloads collector) and, for fields whose collector
``scripts/derive.py`` fixes statically, ``collector`` matches that field.
The envelope's hash must also be recomputed and compared against the sha256
``scripts/derive.py`` records into the provenance entry at derive time; a
missing recorded hash is unverifiable, not merely unusual, so such a bundle
stays flagged until it is re-derived.
A source that is a word (``ecosystem-intelligence``), a URL, an envelope for a
different repository or collector, or one with no recorded hash is not
re-checkable and flags ``unverifiable_provenance``, which blocks promotion.
A bare ``http(s)://`` URL is never accepted (`03` §5): provenance is an
envelope path only; a URL lives inside an envelope's own ``source`` field,
never as the provenance pointer itself.

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

#: field -> the ``raw/<collector>/`` directory name(s) ``scripts/derive.py``
#: statically fixes for that field's ``verified`` confidence (`03` §5).
#: Exhaustive over every ``b.set(field, ..., "verified")`` /
#: ``derive_eligibility`` / ``derive_pr_window_fields`` call in
#: ``scripts/derive.py`` and the modules it calls (``cvi/collect/repo_object.py``,
#: ``cvi/collect/github_graphql.py``). ``archived`` and
#: ``unlicensed`` accept two collectors: ``repo_object`` when the richer
#: envelope exists, ``github_meta`` as the fallback (``primary or
#: "github_meta"`` in ``derive_eligibility``). A field absent from this mapping
#: is not collector-checked (only key-checked): the rule stays additive as new
#: collectors land.
FIELD_COLLECTORS: dict[str, frozenset[str]] = {
    "stars": frozenset({"github_meta"}),
    "forks": frozenset({"github_meta"}),
    "monthly_downloads": frozenset({"downloads"}),
    "dependents_count": frozenset({"depsdev"}),
    "active_nonbot_commits_180d": frozenset({"commits_180d"}),
    "dx_ci_minutes": frozenset({"actions_runs"}),
    "active_unclaimed_gfis": frozenset({"gfi"}),
    "scorecard_score": frozenset({"scorecard"}),
    "archived": frozenset({"repo_object", "github_meta"}),
    "mirror": frozenset({"repo_object"}),
    "fork": frozenset({"repo_object"}),
    "unlicensed": frozenset({"repo_object", "github_meta"}),
    "no_prs_ever": frozenset({"repo_object"}),
    "pr_window_count": frozenset({"pr_window"}),
    "pr_window_truncated": frozenset({"pr_window"}),
    "pr_window_cost_points": frozenset({"pr_window"}),
}

#: Fields keyed by ``<ecosystem>__<name>`` rather than ``<owner>__<name>``:
#: ``scripts/derive.py``'s downloads block reads the registry-keyed envelope
#: ``raw/downloads/<ecosystem>__<name>.json`` (one per package name per
#: ecosystem, not per GitHub owner).
ECOSYSTEM_KEYED_FIELDS = frozenset({"monthly_downloads"})


def _load(p: Path) -> Optional[dict]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _hash_stripped(source: str) -> str:
    """Drop a ``#<sha256>`` suffix: the path part of a provenance string."""
    path, separator, digest = source.rpartition("#")
    if path and separator and len(digest) == SHA256_HEX_LENGTH:
        return path
    return source


def _envelope_path(source: str, data_dir: Path) -> Optional[Path]:
    """Resolve a provenance ``source`` to an envelope under ``<data_dir>/raw``.

    Absolute, data-dir-relative and copied-dir forms all resolve: a bundle keeps
    the absolute path it was collected with, so the part from ``raw`` onward is
    tried against this data dir too. That is the same reason a manifest records
    envelope paths relative to its data dir: a copied sample must
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


def _claimed_hash(source: str, hash_hint: Any) -> Optional[str]:
    """Hash the provenance repeats for ``source``, in either accepted form.

    ``hash_hint`` is the already-extracted ``sha256`` value for this source
    item (a provenance dict's ``sha256`` key, or one element of it for a
    list-valued source — see :func:`_source_items`); a ``<path>#<sha256>``
    suffix on ``source`` itself is the other accepted form
    (:attr:`cvi.collect.envelope.Envelope.provenance`). ``None`` means the
    provenance repeats no hash at all, which counts as unverifiable rather
    than as "nothing to compare".
    """
    if isinstance(hash_hint, str) and len(hash_hint) == SHA256_HEX_LENGTH:
        return hash_hint
    path, separator, digest = source.rpartition("#")
    if separator and path and len(digest) == SHA256_HEX_LENGTH:
        return digest
    return None


def _source_items(entry: Any) -> list[tuple[Any, Any]]:
    """``(source, hash_hint)`` pairs from a provenance entry, one per source item.

    A scalar entry (the common case) yields one pair. A list-valued ``source``
    (``scripts/derive.py``'s ``pr_events``/``mean_inline_comments``, each
    naming two envelopes) is checked element-wise: each source item
    pairs with the same-index element of a list-valued ``sha256``, or with
    ``None`` when ``sha256`` is absent or a different length.
    """
    source = entry.get("source") if isinstance(entry, dict) else entry
    hint = entry.get("sha256") if isinstance(entry, dict) else None
    if isinstance(source, list):
        hints = hint if isinstance(hint, list) and len(hint) == len(source) else [None] * len(source)
        return list(zip(source, hints)) or [(None, None)]
    return [(source, hint)]


def _expected_key(field: str, owner: Any, name: Any, ecosystem: Any) -> str:
    """The ``<...>__<name>`` stem ``field``'s envelope must carry.

    Every field keys on the bundle's own ``owner``/``name`` except the
    downloads collector, which keys on the registry ``ecosystem`` instead
    (:data:`ECOSYSTEM_KEYED_FIELDS`) — ``scripts/derive.py`` fetches downloads
    once per package name per ecosystem, not per GitHub owner.
    """
    prefix = ecosystem if field in ECOSYSTEM_KEYED_FIELDS else owner
    return f"{prefix}__{name}"


def _path_offender(field: str, source: Any, hash_hint: Any, data_dir: Path,
                   owner: Any, name: Any, ecosystem: Any) -> Optional[str]:
    """Why one provenance source item cannot be re-checked, or ``None``.

    Order: is it a non-empty string at all; does it resolve to a file under
    ``raw/``; does that file's key/collector belong to *this* bundle and field;
    is the file a readable, internally-consistent envelope; does a hash exist
    in the provenance to compare, and does it match the envelope's current
    hash. Any failure returns a detail naming the field and
    what was found; passing every gate returns ``None``.
    """
    if not isinstance(source, str) or not source:
        return f"field={field} source={source!r} is not an envelope path"
    path = _envelope_path(source, data_dir)
    if path is None:
        return (f"field={field} source={source!r} is not an envelope under "
                f"{Path(data_dir) / ENVELOPE_DIRNAME}")
    expected_key = _expected_key(field, owner, name, ecosystem)
    if owner is None or name is None or path.stem != expected_key:
        return (f"field={field} envelope={path} key={path.stem!r} does not "
                f"match this bundle's own {expected_key!r}")
    collectors = FIELD_COLLECTORS.get(field)
    if collectors is not None and path.parent.name not in collectors:
        return (f"field={field} envelope={path} collector={path.parent.name!r} "
                f"is not one of {sorted(collectors)} for this field")
    try:
        envelope = read_envelope(path)
    except (json.JSONDecodeError, OSError) as error:
        return f"field={field} envelope={path} is unreadable: {type(error).__name__}"
    if not envelope.hash_matches_data:
        return (f"field={field} envelope={path} records sha256="
                f"{envelope.recorded_sha256} whose data hashes to {envelope.computed_sha256}")
    claimed = _claimed_hash(source, hash_hint)
    if claimed is None:
        return (f"field={field} envelope={path} provenance records no sha256, "
                "so its hash was never compared (re-derive to record one)")
    if claimed != envelope.sha256:
        return (f"field={field} provenance sha256={claimed} but {path} currently "
                f"hashes to {envelope.sha256}")
    return None


def provenance_offender(field: str, entry: Any, data_dir: Path = DATA, *,
                        owner: Any = None, name: Any = None,
                        ecosystem: Any = None) -> Optional[str]:
    """Return why ``field``'s provenance cannot be re-checked, or ``None``.

    Multi-source fields are checked element-wise (:func:`_source_items`); the
    first offending item's detail is returned, so a flagged bundle says which
    field — and which source item — is unverifiable and why.
    """
    for source, hash_hint in _source_items(entry):
        detail = _path_offender(field, source, hash_hint, data_dir, owner, name, ecosystem)
        if detail:
            return detail
    return None


def provenance_checks(bundle: dict, data_dir: Path = DATA) -> list[dict[str, Any]]:
    """One failing check per ``verified`` field whose provenance is not verifiable.

    Only fields that claim ``verified`` are checked: ``derived`` and ``missing``
    fields make no verification claim, so a URL, a bare word or no source at all
    is what they legitimately carry (``ecosystem`` comes from the collector's
    arguments, ``ai_posture`` from a README URL read by hand). ``owner``,
    ``name`` and ``ecosystem`` come from the bundle's own ``meta`` (every real
    ``scripts/derive.py`` bundle sets all three unconditionally) rather than
    from a parameter: the key/collector check needs the bundle's identity.
    """
    provenance = bundle.get("provenance") or {}
    confidence = bundle.get("confidence") or {}
    meta = bundle.get("meta") or {}
    owner, name, ecosystem = meta.get("owner"), meta.get("name"), meta.get("ecosystem")
    checks: list[dict[str, Any]] = []
    for field, level in sorted(confidence.items()):
        if level != "verified":
            continue
        detail = provenance_offender(field, provenance.get(field), data_dir,
                                     owner=owner, name=name, ecosystem=ecosystem)
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
                    help="upsert clean repos into OperationalStore (delegates to scripts/promote.py)")
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
