#!/usr/bin/env python3
"""Build the W0-1 stratified sample of recorded envelopes.

Copies the envelopes of every collector for a stratified sample out of a data
directory (read-only -- nothing under ``data/`` is written) into a scratch
sample directory, and writes ``manifest.json`` plus ``MANIFEST.md``.  The
result is a mini ``data/`` tree, so ``python -m cvi.audit.coverage --data-dir``
runs against it unchanged.

Selection: one stratum per ecosystem, ``--per-ecosystem`` repositories each,
the 01-FINDINGS section F anchors forced in, a fixed seed, and the remainder
topped up to ``--total`` from the largest strata.  The pool defaults to the candidates
that already have at least one recorded envelope, so a sampled repository is
always one that was actually copied out of ``data/raw`` (``--pool candidates``
lifts that restriction).  ``--keys-manifest`` re-copies
an exact key list, which is how a later packet reproduces this sample.

Usage (from the checkout root)::

    python scripts/make_sample.py --data-dir data \
        --out ~/Projects/cvi-scratch/sample-200
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cvi.audit.coverage import COLLECTORS, GITHUB_KEYED_COLLECTORS, discover_repos

__version__ = "1.0.0"

DEFAULT_SEED = 20260912
DEFAULT_PER_ECOSYSTEM = 33
DEFAULT_TOTAL = 200

#: Anchors named in docs/v2/01-FINDINGS-2026-09-12.md section F.
ANCHOR_REPOS: Tuple[str, ...] = (
    "octo-org/.github",
    "openclaw/openclaw",
    "obra/superpowers",
    "anthropics/claude-code",
    "obra/superpowers-marketplace",
    "mattpocock/skills",
    "anthropics/skills",
    "Significant-Gravitas/AutoGPT",
    "qxf-ai/awesome-personal-blockchain",
    "RooCodeInc/Roo-Code",
    "agentmode-dev/registry",
    "agentmode-dev/useagent.dev",
    "MervinPraison/PraisonAI",
    "dhuangdev/agent-publish",
    "dhuangdev/ai-project-governance-framework",
    "open-gsd/gsd-core",
    "gresh-r-d/gresh-cli",
    "gresh-r-d/spectral-mixing",
    "gresh-r-d/ransal-analysis",
    "gresh-r-d/paleo-vqe",
    "gresh-r-d/geom-fingerprint",
    "gresh-r-d/wildtree",
    "gresh-r-d/cnop-optimization",
    "cargo/abandon",
    "pulumi/pulumi",
    "pypi/pytest",
    "pypi/numpy",
    "cargo/anyhow",
    "torvalds/linux",
    "google/leveldb",
    "aio-libs/async-timeout",
)

#: Ecosystem prefixes used by the findings' package keys, mapped to candidate
#: ecosystems.  ``github`` means "the GitHub tier", matched by repo name only.
ECOSYSTEM_ALIASES: Dict[str, Optional[str]] = {
    "cargo": "crates",
    "crates": "crates",
    "npm": "npm",
    "pypi": "pypi",
    "go": "go",
    "maven": "maven",
    "systems": "systems",
    "github": None,
}


def load_universe(data_dir: Path) -> List[dict]:
    """Load ``raw/candidates.jsonl`` -- the universe of selected repositories."""
    path = data_dir / "raw" / "candidates.jsonl"
    entries: List[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if isinstance(record, dict) and record.get("owner") and record.get("name"):
                entries.append(record)
    return entries


def repo_id(entry: dict) -> str:
    """Canonical ``owner/name`` key of a universe entry."""
    return f"{entry['owner']}/{entry['name']}"


def resolve_anchor(token: str, universe: Sequence[dict]) -> Optional[str]:
    """Map a findings anchor onto a universe repository id, or ``None``.

    A token is either a GitHub ``owner/repo`` slug or an ecosystem-qualified
    package key such as ``cargo/abandon``; the latter resolves by ecosystem and
    package name.
    """
    direct = {repo_id(entry) for entry in universe}
    if token in direct:
        return token
    head, _, tail = token.partition("/")
    ecosystem = ECOSYSTEM_ALIASES.get(head)
    if tail and ecosystem is not None:
        for entry in universe:
            if entry.get("ecosystem") == ecosystem and entry.get("name") == tail:
                return repo_id(entry)
    for entry in universe:
        if entry.get("name") == token:
            return repo_id(entry)
    return None


def select_repos(
    universe: Sequence[dict],
    per_ecosystem: int = DEFAULT_PER_ECOSYSTEM,
    total: int = DEFAULT_TOTAL,
    seed: int = DEFAULT_SEED,
    anchors: Sequence[str] = ANCHOR_REPOS,
    pool_note: Optional[dict] = None,
) -> Tuple[List[dict], dict]:
    """Pick the stratified sample deterministically.

    Returns the chosen universe entries (sorted by repository id) and a plan
    describing the strata, the anchor resolution and the top-up.
    """
    rng = random.Random(seed)
    strata: Dict[str, List[dict]] = defaultdict(list)
    for entry in universe:
        strata[str(entry.get("ecosystem"))].append(entry)
    resolved: Dict[str, Optional[str]] = {token: resolve_anchor(token, universe) for token in anchors}
    forced = {key for key in resolved.values() if key}
    chosen: List[dict] = []
    per_stratum: Dict[str, dict] = {}
    for ecosystem, entries in sorted(strata.items()):
        by_id = {repo_id(entry): entry for entry in entries}
        forced_here = sorted(key for key in forced if key in by_id)
        rest = sorted(set(by_id) - set(forced_here))
        quota = max(0, per_ecosystem - len(forced_here))
        picked = forced_here + sorted(rng.sample(rest, min(quota, len(rest))))
        chosen.extend(by_id[key] for key in picked)
        per_stratum[ecosystem] = {
            "universe": len(entries),
            "anchors": len(forced_here),
            "selected": len(picked),
        }
    shortfall = total - len(chosen)
    order = sorted(strata, key=lambda name: (-len(strata[name]), name))
    picked_ids = {repo_id(entry) for entry in chosen}
    top_up: List[str] = []
    index = 0
    while shortfall > 0 and order:
        ecosystem = order[index % len(order)]
        pool = sorted(repo_id(entry) for entry in strata[ecosystem] if repo_id(entry) not in picked_ids)
        index += 1
        if not pool:
            if all(
                repo_id(entry) in picked_ids
                for name in order
                for entry in strata[name]
            ):
                break
            continue
        winner = pool[0]
        entry = next(item for item in strata[ecosystem] if repo_id(item) == winner)
        chosen.append(entry)
        picked_ids.add(winner)
        per_stratum[ecosystem]["selected"] += 1
        top_up.append(f"{ecosystem}:{winner}")
        shortfall -= 1
    plan = {
        "seed": seed,
        "per_ecosystem": per_ecosystem,
        "total": total,
        "universe_size": len(universe),
        "pool": dict(pool_note or {}),
        "strata": per_stratum,
        "anchors_requested": list(anchors),
        "anchors_resolved": resolved,
        "top_up": top_up,
        "selected_total": len(chosen),
    }
    return sorted(chosen, key=repo_id), plan


def envelope_paths(data_dir: Path, entry: dict) -> Dict[str, str]:
    """Map collector name to the relative envelope path that exists on disk."""
    owner, name = entry["owner"], entry["name"]
    ecosystem = str(entry.get("ecosystem", ""))
    files: Dict[str, str] = {}
    for collector in COLLECTORS:
        candidates = []
        if collector == "downloads":
            candidates.append(Path("raw") / collector / f"{ecosystem}__{name}.json")
        candidates.append(Path("raw") / collector / f"{owner}__{name}.json")
        for relative in candidates:
            if (data_dir / relative).is_file():
                files[collector] = relative.as_posix()
                break
    for folder in ("derived", "verified"):
        relative = Path(folder) / f"{owner}__{name}.json"
        if (data_dir / relative).is_file():
            files[folder] = relative.as_posix()
    return files


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_sample(data_dir: Path, out: Path, entries: Sequence[dict]) -> Tuple[List[dict], dict]:
    """Copy every envelope of every entry into ``out``, read-only.

    Returns one manifest record per repository and aggregate counters.
    """
    stats = {
        "copied": 0,
        "reused": 0,
        "bytes": 0,
        "missing_envelope": 0,
        "hash_mismatch": 0,
        "collectors_seen": set(),
    }
    records: List[dict] = []
    for entry in entries:
        files = envelope_paths(data_dir, entry)
        record: Dict[str, Any] = {
            "repo": repo_id(entry),
            "ecosystem": str(entry.get("ecosystem")),
            "selected_by": entry.get("selected_by"),
            "files": {},
            "missing_collectors": [name for name in GITHUB_KEYED_COLLECTORS if name not in files],
        }
        for kind, relative in sorted(files.items()):
            source = data_dir / relative
            destination = out / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            size = source.stat().st_size
            digest = _sha256(source)
            if destination.exists() and destination.stat().st_size == size and _sha256(destination) == digest:
                stats["reused"] += 1
            else:
                shutil.copy2(source, destination)
                os.chmod(destination, 0o444)
                stats["copied"] += 1
                stats["bytes"] += size
            if _sha256(destination) != digest:
                stats["hash_mismatch"] += 1
            stats["collectors_seen"].add(kind)
            record["files"][kind] = {"path": relative, "bytes": size, "sha256": digest}
        stats["missing_envelope"] += len(GITHUB_KEYED_COLLECTORS) - len(
            [name for name in GITHUB_KEYED_COLLECTORS if name in files]
        )
        records.append(record)
    return records, stats


def write_manifest(out: Path, plan: dict, records: Sequence[dict], stats: dict, data_dir: Path) -> None:
    """Write ``manifest.json`` and the human-readable ``MANIFEST.md``."""
    manifest = {
        "tool": f"scripts/make_sample.py v{__version__}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_data_dir": str(data_dir),
        "plan": plan,
        "counters": {
            key: (sorted(value) if isinstance(value, set) else value)
            for key, value in stats.items()
        },
        "repos": list(records),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    lines = [
        "# Sample manifest (W0-1)",
        "",
        f"- source: `{data_dir}`",
        f"- repositories: **{len(records)}** (seed {plan['seed']}, "
        f"{plan['per_ecosystem']} per ecosystem, topped up to {plan['total']})",
        f"- universe: {plan['universe_size']} candidates in raw/candidates.jsonl",
        f"- envelopes: {stats['copied']} copied + {stats['reused']} reused, {stats['bytes']:,} bytes",
        "",
        "| ecosystem | universe | anchors forced in | selected |",
        "|---|---|---|---|",
    ]
    for ecosystem, detail in sorted(plan["strata"].items()):
        lines.append(
            f"| {ecosystem} | {detail['universe']} | {detail['anchors']} | {detail['selected']} |"
        )
    unresolved = sorted(key for key, value in plan["anchors_resolved"].items() if value is None)
    resolved = {value: key for key, value in plan["anchors_resolved"].items() if value}
    lines += ["", f"## Anchors ({len(resolved)} of {len(plan['anchors_requested'])} present in the universe)", ""]
    for token in plan["anchors_requested"]:
        target = plan["anchors_resolved"][token]
        lines.append(f"- `{token}` -> `{target}`" if target else f"- `{token}` -> ABSENT from the universe")
    if unresolved:
        lines += ["", f"Unresolved anchors ({len(unresolved)}): " + ", ".join(f"`{item}`" for item in unresolved)]
    lines += ["", "## Keys", ""]
    for record in records:
        files = record["files"]
        lines.append(
            f"- `{record['repo']}` ({record['ecosystem']}) -- "
            f"{len([key for key in files if not key.startswith(('derived', 'verified'))])} collectors"
            + (", derived+verified" if "derived" in files else "")
            + (f", missing: {', '.join(record['missing_collectors'])}" if record["missing_collectors"] else "")
        )
    (out / "MANIFEST.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def keys_from_manifest(path: Path) -> List[str]:
    """Read the repository keys of a previously written ``manifest.json``."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [entry["repo"] for entry in payload.get("repos", []) if isinstance(entry, dict) and entry.get("repo")]


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--data-dir", default=os.environ.get("CVI_DATA_DIR", "data"), help="source data dir (read-only)")
    parser.add_argument("--out", default="~/Projects/cvi-scratch/sample-200", help="sample output directory")
    parser.add_argument("--per-ecosystem", type=int, default=DEFAULT_PER_ECOSYSTEM)
    parser.add_argument("--total", type=int, default=DEFAULT_TOTAL)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--keys-manifest", default=None, help="manifest.json whose keys to re-copy verbatim")
    parser.add_argument(
        "--pool",
        choices=("collected", "candidates"),
        default="collected",
        help="sample only repositories that have recorded envelopes (default) or the whole candidate universe",
    )
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir).expanduser().resolve()
    out = Path(args.out).expanduser()
    universe = load_universe(data_dir)
    collected = set(discover_repos(data_dir))
    with_envelopes = [entry for entry in universe if repo_id(entry) in collected]
    pool_note = {
        "kind": args.pool,
        "candidates": len(universe),
        "candidates_with_envelopes": len(with_envelopes),
        "keys_on_disk_in_raw_and_derived": len(collected),
    }
    if args.pool == "collected":
        universe = with_envelopes
    if args.keys_manifest:
        wanted = set(keys_from_manifest(Path(args.keys_manifest).expanduser()))
        selection = sorted((entry for entry in universe if repo_id(entry) in wanted), key=repo_id)
        plan = {
            "seed": args.seed,
            "per_ecosystem": args.per_ecosystem,
            "total": args.total,
            "universe_size": len(universe),
            "pool": pool_note,
            "strata": {},
            "anchors_requested": [],
            "anchors_resolved": {},
            "top_up": [],
            "selected_total": len(selection),
            "keys_manifest": str(args.keys_manifest),
        }
    else:
        selection, plan = select_repos(
            universe, args.per_ecosystem, args.total, args.seed, ANCHOR_REPOS, pool_note=pool_note
        )
    out.mkdir(parents=True, exist_ok=True)
    records, stats = copy_sample(data_dir, out, selection)
    write_manifest(out, plan, records, stats, data_dir)

    print(f"source: {data_dir}")
    print(f"out:    {out}")
    print(
        f"universe: {pool_note['candidates']} candidates, {pool_note['candidates_with_envelopes']} with envelopes;"
        f" pool={args.pool}; selected: {len(records)}"
    )
    for ecosystem, detail in sorted(plan["strata"].items()):
        print(
            f"  {ecosystem:<8} universe {detail['universe']:>5}  anchors {detail['anchors']:>3}  "
            f"selected {detail['selected']:>3}"
        )
    resolved = sorted(value for value in plan["anchors_resolved"].values() if value)
    print(f"anchors: {len(resolved)} of {len(plan['anchors_requested'])} present in the universe")
    print(f"envelopes: {stats['copied']} copied, {stats['reused']} reused, {stats['bytes']:,} bytes")
    print(f"manifest: {(out / 'manifest.json').as_posix()}  |  {(out / 'MANIFEST.md').as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
