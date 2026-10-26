#!/usr/bin/env python3
"""Build ``tests/fixtures/snapshot_50``: a frozen v2 fixture snapshot of 50
repositories, derived from the read-only ``~/Projects/cvi-scratch/sample-200``
corpus.

Deterministic and reproducible byte-for-byte: no wall clock, no random
choice, no live network. ``as_of`` is read from the sample's own
``manifest.json`` (``generated_at`` -- the sample-build tool's own frozen
timestamp, never ``datetime.now()``). Repo selection is a pure function of
the sample's manifest content (sorted repo keys plus the D-VER-4 anchor
repos that are present in the sample); scoring is
``porchlamp.core.pipeline.score_snapshot``, itself pure. The only I/O this script
performs is reading the sample corpus and writing the fixture's own output
files -- re-running it against the same sample produces byte-identical
output (``tests/snapshot/test_reproduce_fixture.py`` asserts that).

Gate evidence (``porchlamp.core.gates.GateEvidence``) is not part of the
FeatureBundle v2 contract (04 section 3) and no landed collector wires it
yet. This script therefore derives it directly from the sample's raw
envelopes (``raw/tree``, ``raw/commits_180d``, ``raw/github_meta``) -- a
fixture-only, non-authoritative deriver kept local to this script.
See :func:`gate_evidence_for` for the documented heuristics.

Populations (D-NORM-2/3, stage 4) are frozen from the 50 selected repos
only, via a two-pass bootstrap :func:`porchlamp.snapshot.populations.
build_populations` needs but ``porchlamp.core.pipeline.score_snapshot`` does not
provide (that function consumes an already-frozen ``PopulationSet`` -- see
its own docstring): pass 1 scores every bundle against an *empty*
population set purely to learn each bundle's stage-0/3 gate/rated status
and its raw ECDF-input values (``porchlamp.core.engine``'s own documented,
non-raising ``no_population`` fallback makes this safe); pass 2 builds the
real ``PopulationSet`` from pass 1's rated, gate-eligible repos and scores
every bundle again, for real, through ``score_snapshot``. See
:func:`build_fixture`.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from porchlamp.core import constants
from porchlamp.core.gates import GateEvidence
from porchlamp.snapshot.manifest import merkle_root
from porchlamp.core.models import (
    FeatureBundle,
    Measured,
    canonical_json,
    sha256_hex,
)
from porchlamp.core.pipeline import RepoKey, ScoredSnapshot, repo_key, score_snapshot
from porchlamp.derive.bundle import dump_bundle, load_bundle, write_bundle
from porchlamp.derive.gate_evidence import (  # noqa: F401
    MANIFEST_MAX_DEPTH,
    RECOGNIZED_MANIFEST_BASENAMES,
    RECOGNIZED_ROOT_MANIFEST_BASENAMES,
    _has_license_file,
    _has_off_platform_marker,
    _has_recognized_manifest,
    _tree_paths,
    _vitality_commit_count,
    gate_evidence_for,
)
from porchlamp.snapshot.populations import PopulationMember, PopulationSet, build_populations, write_populations
from porchlamp.snapshot.store_cmd import score_snapshot_with_detectors

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SAMPLE_ROOT = Path.home() / "Projects" / "cvi-scratch" / "sample-200"
DEFAULT_OUT_DIR = REPO_ROOT / "tests" / "fixtures" / "snapshot_50"
FIXTURE_SIZE = 50
SNAPSHOT_ID = "snapshot_50"

#: D-VER-4 anchor repos (``porchlamp/audit/anchors.py``'s ``ANCHOR_RELATIONS``):
#: force-included whenever present in the sample, ahead of the plain
#: alphabetical fill, so the anchor tests can state which anchors this
#: fixture actually carries.
ANCHOR_REPO_KEYS: tuple[RepoKey, ...] = (
    "torvalds/linux",
    "gcc-mirror/gcc",
    "kubernetes/kubernetes",
    "vllm-project/vllm",
    "keras-team/keras-hub",
)

#: The three D-NORM-3 ECDF-normalised input ids (02 section 4, 1.5/2.1/2.3).
ECDF_INPUT_IDS: tuple[str, ...] = ("ttm_p90_hours", "dependents_deduped", "downloads_monthly")


def _basename_for(manifest_repo_entry: Mapping[str, Any]) -> str | None:
    derived = manifest_repo_entry.get("files", {}).get("derived")
    if not isinstance(derived, Mapping):
        return None
    path = derived.get("path")
    if not isinstance(path, str):
        return None
    return Path(path).stem  # "<owner>__<name>"


def _first_awesome_repo(all_keys: Sequence[RepoKey]) -> list[RepoKey]:
    """D-VER-4's ``awesome_non_software`` relation names no single repo
    identity (``porchlamp.audit.anchors._check_awesome`` scans every repo whose
    name starts with "awesome" broadly) -- but it is still one of the
    :data:`~porchlamp.audit.anchors.ANCHOR_RELATIONS` this fixture should give a
    fair chance to pass rather than fail it by omission alone. The
    alphabetically-first ``awesome*`` repo the sample contains (if any) is
    force-included the same way the five identity anchors are."""
    matches = [key for key in all_keys if key.rsplit("/", 1)[-1].lower().startswith("awesome")]
    return matches[:1]


def select_fixture_repo_keys(manifest: Mapping[str, Any], size: int = FIXTURE_SIZE) -> list[RepoKey]:
    """Pick ``size`` repo keys deterministically (sorted lowercase
    ``owner/name``), force-including every :data:`ANCHOR_REPO_KEYS` entry
    present in the sample plus one ``awesome*`` repo (:func:`_first_awesome_repo`)
    ahead of the plain alphabetical fill, so the fixture always exercises
    every D-VER-4 anchor the sample corpus can possibly supply."""
    all_keys = sorted(
        (entry["repo"] for entry in manifest["repos"] if _basename_for(entry) is not None),
        key=str.lower,
    )
    present_anchors = [key for key in ANCHOR_REPO_KEYS if key in all_keys] + _first_awesome_repo(all_keys)
    selected: list[RepoKey] = list(present_anchors)
    seen = set(selected)
    for key in all_keys:
        if len(selected) >= size:
            break
        if key in seen:
            continue
        selected.append(key)
        seen.add(key)
    return sorted(selected, key=str.lower)


def _bundle_raw_hash(bundle: FeatureBundle) -> str:
    """sha256 of the whole bundle document (03 section 5's ``raw_hash``:
    "hash of its bundle" -- distinct from ``FeatureBundle.provenance_hash``,
    which covers only the referenced envelope hashes; ``porchlamp.core.engine.
    score``'s own docstring recommends exactly this)."""
    return sha256_hex(canonical_json(dump_bundle(bundle)))


class _BootstrapPopulationLookup:
    """Bootstrap population lookup for Pass 1 scoring.

    Provides a dummy population and neutral ECDF callable so that measured ECDF
    parameters (ttm_p90_hours, dependents_deduped, downloads_monthly) are not
    falsely marked unmeasured with reason='no_population', which would depress
    Pass 1 composite coverage and exclude eligible repositories from Pass 2
    population membership.
    """

    def population(self, ecosystem: str, param: str) -> object:
        return object()

    def ecdf(self, ecosystem: str, param: str) -> Callable[[float], float]:
        return lambda _val: 50.0


def _empty_populations(snapshot_id: str) -> PopulationSet:
    return PopulationSet(
        snapshot_id=snapshot_id, benchmark_version=constants.BENCHMARK_VERSION,
        constants_hash=constants.CONSTANTS_HASH, populations=(),
    )


def _population_member(bundle: FeatureBundle, pass1_gate, pass1_rated: bool) -> PopulationMember:
    measured: dict[str, float] = {}
    for input_id in ECDF_INPUT_IDS:
        value = bundle.inputs[input_id]
        min_n = constants.PARAM_MIN_N.get(input_id)
        if (
            isinstance(value, Measured)
            and isinstance(value.value, (int, float))
            and not isinstance(value.value, bool)
            and (min_n is None or (value.n is not None and value.n >= min_n))
        ):
            measured[input_id] = float(value.value)
    return PopulationMember(
        key=repo_key(bundle), ecosystem=bundle.repo.ecosystem, gate=pass1_gate, rated=pass1_rated, measured=measured,
    )


@dataclass(frozen=True)
class FixtureBuildResult:
    """Everything :func:`build_fixture` computed, for the caller (``main``
    or a test) to inspect and/or write out."""

    as_of: datetime
    bundles: list[FeatureBundle]
    gate_evidence: dict[RepoKey, GateEvidence]
    raw_hashes: dict[RepoKey, str]
    populations: PopulationSet
    population_fallback_params: list[str]
    snapshot: ScoredSnapshot
    anchors_present: list[RepoKey]
    anchors_absent: list[RepoKey]


def build_fixture(sample_root: Path, *, size: int = FIXTURE_SIZE, snapshot_id: str = SNAPSHOT_ID) -> FixtureBuildResult:
    """Select, load, gate, freeze populations for, and score the fixture --
    everything short of writing files (kept separate so a test can call
    this once and compare two independent runs without touching disk)."""
    manifest = json.loads((sample_root / "manifest.json").read_text(encoding="utf-8"))
    as_of = datetime.fromisoformat(str(manifest["generated_at"]).replace("Z", "+00:00"))

    selected_keys = select_fixture_repo_keys(manifest, size)
    entries_by_key = {entry["repo"]: entry for entry in manifest["repos"]}

    bundles: list[FeatureBundle] = []
    gate_evidence: dict[RepoKey, GateEvidence] = {}
    raw_hashes: dict[RepoKey, str] = {}
    for key in selected_keys:
        entry = entries_by_key[key]
        basename = _basename_for(entry)
        assert basename is not None, key  # select_fixture_repo_keys already filtered these out
        derived_path = sample_root / entry["files"]["derived"]["path"]
        bundle = load_bundle(derived_path, raw_root=sample_root / "raw")
        bundles.append(bundle)
        gate_evidence[key] = gate_evidence_for(sample_root / "raw", basename)
        raw_hashes[key] = _bundle_raw_hash(bundle)

    # Pass 1: population-free scoring, purely to learn gate/rated status and
    # each bundle's raw ECDF-input values (module docstring).
    pass1 = score_snapshot(
        bundles, _BootstrapPopulationLookup(), snapshot_id=snapshot_id, as_of=as_of,
        gate_evidence=gate_evidence, raw_hashes=raw_hashes,
    )
    pass1_by_key = {repo_key_of(record): record for record in pass1.records}
    members = [
        _population_member(bundle, pass1_by_key[repo_key(bundle)].gate, pass1_by_key[repo_key(bundle)].rated)
        for bundle in bundles
    ]
    populations = build_populations(members, snapshot_id)
    fallback_params = sorted({entry.param for entry in populations.populations if entry.fallback})

    # Pass 2: the real, final scoring with frozen populations -- the same
    # score_snapshot_with_detectors() porchlamp.snapshot store's own driver calls
    # (porchlamp.snapshot.store_cmd), so detectors are wired here exactly the
    # same way. previous=None (this script has no previous-snapshot
    # concept of its own): the single-snapshot detectors (closed_unread,
    # label_spam, ...) still fire -- only detect_param_jump needs a real
    # previous record.
    snapshot = score_snapshot_with_detectors(
        bundles, populations, snapshot_id=snapshot_id, as_of=as_of,
        gate_evidence=gate_evidence, raw_hashes=raw_hashes,
    )

    anchors_present = [key for key in ANCHOR_REPO_KEYS if key in selected_keys]
    anchors_absent = [key for key in ANCHOR_REPO_KEYS if key not in selected_keys]

    return FixtureBuildResult(
        as_of=as_of, bundles=bundles, gate_evidence=gate_evidence, raw_hashes=raw_hashes,
        populations=populations, population_fallback_params=fallback_params, snapshot=snapshot,
        anchors_present=anchors_present, anchors_absent=anchors_absent,
    )


def repo_key_of(record: Any) -> RepoKey:
    return f"{record.repo.owner}/{record.repo.name}"


def _tier_or_none(record: Any) -> str | None:
    return record.tier


def expected_document(snapshot: ScoredSnapshot) -> dict[str, Any]:
    """Build ``expected.json``'s content for ``snapshot``.

    Split out of :func:`write_fixture` so the committed fixture's
    expectations can be re-derived from the committed bundles alone (see
    :func:`rewrite_expected`) using the very same construction, without
    needing the read-only sample corpus the rest of the build consumes.
    A second, hand-maintained copy of this shape would be free to drift
    from the one the real build writes, which would quietly weaken the
    reproducibility pin it exists to be.
    """
    return {
        "document": "porchlamp-fixture-snapshot-expected",
        "version": 1,
        "records_hash": snapshot.records_hash,
        "counts": {
            "rated": snapshot.counts.rated,
            "gated": snapshot.counts.gated,
            "unrated": snapshot.counts.unrated,
            "persona_visible": dict(snapshot.counts.persona_visible),
        },
        "rank_order": [f"{r.repo.owner}/{r.repo.name}" for r in snapshot.records],
        "records": [
            {
                "repo": f"{r.repo.owner}/{r.repo.name}",
                "eligible": r.gate.eligible,
                "gate_reason": r.gate.reason,
                "routed": r.gate.routed,
                "rated": r.rated,
                "unrated_reason": r.unrated_reason,
                "composite": r.composite,
                "coverage": round(r.coverage, 6),
                "tier": _tier_or_none(r),
                "rank": r.rank,
                "tie_group": r.tie_group,
            }
            for r in snapshot.records
        ],
    }


def write_expected(snapshot: ScoredSnapshot, path: Path) -> None:
    """Write ``expected.json`` for ``snapshot`` to ``path``."""
    path.write_text(
        json.dumps(expected_document(snapshot), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def rewrite_expected(fixture_dir: Path) -> ScoredSnapshot:
    """Re-score the committed fixture in place and rewrite ``expected.json``.

    For the case where the engine's semantics changed deliberately -- a gate
    corrected, a reason code re-classified -- and the fixture's pinned
    expectations are now stale *by intent*. The bundles, populations and
    manifest are inputs and are left untouched; only the recorded outputs are
    refreshed, from those same inputs, through the real pipeline
    (:func:`porchlamp.snapshot.store_cmd.score_snapshot_with_detectors`, the exact
    entry point ``test_reproduce_matches_expected_hash`` pins against).

    This is deliberately a separate, explicitly-invoked mode rather than
    something any test does on its own: a pin that rewrites itself when it
    fails is not a pin. Every use must come with a reviewed explanation of
    why the previous expectations were wrong.
    """
    from porchlamp.snapshot.store_cmd import score_snapshot_with_detectors

    frozen = load_frozen_fixture(fixture_dir)
    pop_path = fixture_dir / "populations.json"
    if pop_path.is_file():
        pop_doc = json.loads(pop_path.read_text(encoding="utf-8"))
        if pop_doc.get("constants_hash") != constants.CONSTANTS_HASH:
            pop_doc["constants_hash"] = constants.CONSTANTS_HASH
            pop_path.write_text(
                json.dumps(pop_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
    manifest_path = fixture_dir / "manifest.json"
    if manifest_path.is_file():
        manifest_doc = json.loads(manifest_path.read_text(encoding="utf-8"))
        new_merkle = merkle_root(
            sorted([*frozen.raw_hashes.values(), _populations_leaf(fixture_dir)])
        )
        if (
            manifest_doc.get("constants_hash") != constants.CONSTANTS_HASH
            or manifest_doc.get("merkle_root") != new_merkle
        ):
            manifest_doc["constants_hash"] = constants.CONSTANTS_HASH
            manifest_doc["merkle_root"] = new_merkle
            manifest_path.write_text(
                json.dumps(manifest_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
    frozen = load_frozen_fixture(fixture_dir)
    snapshot = score_snapshot_with_detectors(
        frozen.bundles,
        frozen.populations,
        snapshot_id=frozen.snapshot_id,
        as_of=frozen.as_of,
        gate_evidence=frozen.gate_evidence,
        raw_hashes=frozen.raw_hashes,
    )
    write_expected(snapshot, fixture_dir / "expected.json")
    return snapshot


def _populations_leaf(out_dir: Path) -> str:
    """The ``populations.json`` merkle leaf, identical to the one
    ``porchlamp.snapshot.build_cmd.build_snapshot_dir`` uses (E-01).

    Two builders write a v2 snapshot manifest -- that command and this
    fixture script -- and the root must mean the same thing in both, or the
    fixture's integrity claim is weaker than the product's. Populations are
    the ECDF reference distributions: the same bundles scored against
    different populations produce different scores, so a root that omits
    them can be equal for two directories that do not score alike.
    """
    return sha256_hex((out_dir / "populations.json").read_text(encoding="utf-8"))


def write_fixture(result: FixtureBuildResult, out_dir: Path) -> None:
    """Write the fixture's on-disk shape: ``bundles/<owner>__<name>.json``,
    ``populations.json``, ``manifest.json``, ``expected.json``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    bundles_dir = out_dir / "bundles"
    bundles_dir.mkdir(parents=True, exist_ok=True)
    for bundle in result.bundles:
        basename = f"{bundle.repo.owner}__{bundle.repo.name}.json"
        write_bundle(bundle, bundles_dir / basename)

    write_populations(result.populations, out_dir / "populations.json")

    repos_manifest = [
        {
            "owner": bundle.repo.owner,
            "name": bundle.repo.name,
            "ecosystem": bundle.repo.ecosystem,
            "bundle_file": f"bundles/{bundle.repo.owner}__{bundle.repo.name}.json",
            "raw_hash": result.raw_hashes[repo_key(bundle)],
            "gate_evidence": {
                "manifest_found": result.gate_evidence[repo_key(bundle)].manifest_found,
                "is_curated_anchor": result.gate_evidence[repo_key(bundle)].is_curated_anchor,
                "license_file_present": result.gate_evidence[repo_key(bundle)].license_file_present,
                "fork_parent_in_universe": result.gate_evidence[repo_key(bundle)].fork_parent_in_universe,
                "vitality_commits_180d": result.gate_evidence[repo_key(bundle)].vitality_commits_180d,
                "off_platform_marker": result.gate_evidence[repo_key(bundle)].off_platform_marker,
                "review_handoff": result.gate_evidence[repo_key(bundle)].review_handoff,
            },
        }
        for bundle in sorted(result.bundles, key=lambda b: (b.repo.owner.lower(), b.repo.name.lower()))
    ]
    manifest_doc = {
        "document": "porchlamp-fixture-snapshot-manifest",
        "version": 1,
        "snapshot_id": result.snapshot.snapshot_id,
        "as_of": result.as_of.isoformat(),
        "benchmark_version": result.snapshot.benchmark_version,
        "constants_hash": result.snapshot.constants_hash,
        "merkle_root": merkle_root(sorted([*result.raw_hashes.values(), _populations_leaf(out_dir)])),
        "repo_count": len(result.bundles),
        "anchors_present": result.anchors_present,
        "anchors_absent": result.anchors_absent,
        "population_fallback_params": result.population_fallback_params,
        "repos": repos_manifest,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    write_expected(result.snapshot, out_dir / "expected.json")


#: Moved to :mod:`porchlamp.snapshot.fixture_io` so ``porchlamp.snapshot.store_cmd`` --
#: the ``porchlamp.snapshot store`` CLI's driver -- can read a snapshot directory back
#: without importing this script. Re-exported here under their original names.
from porchlamp.snapshot.fixture_io import FrozenFixture, load_frozen_fixture  # noqa: F401


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-root", type=Path, default=DEFAULT_SAMPLE_ROOT)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--size", type=int, default=FIXTURE_SIZE)
    parser.add_argument("--snapshot-id", default=SNAPSHOT_ID)
    parser.add_argument(
        "--rewrite-expected",
        action="store_true",
        help=(
            "Do not rebuild from the sample corpus; re-score the committed fixture in "
            "--out-dir and rewrite only its expected.json. Use when the engine's "
            "semantics changed deliberately and the pinned expectations are stale by "
            "intent -- never to make a failing reproduce test go away."
        ),
    )
    args = parser.parse_args(argv)

    if args.rewrite_expected:
        snapshot = rewrite_expected(args.out_dir)
        print(f"rewrote {args.out_dir / 'expected.json'} from the committed bundles")
        print(f"counts: rated={snapshot.counts.rated} gated={snapshot.counts.gated} "
              f"unrated={snapshot.counts.unrated}")
        print(f"records_hash: {snapshot.records_hash}")
        return 0

    result = build_fixture(args.sample_root, size=args.size, snapshot_id=args.snapshot_id)
    write_fixture(result, args.out_dir)

    print(f"wrote {len(result.bundles)} bundles to {args.out_dir}")
    print(f"as_of: {result.as_of.isoformat()}")
    print(f"anchors present: {result.anchors_present}")
    print(f"anchors absent: {result.anchors_absent}")
    print(f"population_fallback params: {result.population_fallback_params}")
    print(f"counts: rated={result.snapshot.counts.rated} gated={result.snapshot.counts.gated} "
          f"unrated={result.snapshot.counts.unrated}")
    print(f"records_hash: {result.snapshot.records_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
