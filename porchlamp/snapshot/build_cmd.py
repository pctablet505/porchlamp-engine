"""``porchlamp snapshot build`` driver: build a v2 snapshot directory from ``data_dir``.

Reads ``data_dir/derived/*.json`` bundles and ``data_dir/raw`` envelopes,
derives :class:`~porchlamp.core.gates.GateEvidence` per repository, computes
bundle raw hashes, runs the two-pass ECDF population bootstrap, and writes
``out_dir/bundles/*.json``, ``out_dir/populations.json``, and
``out_dir/manifest.json`` matching :func:`porchlamp.snapshot.fixture_io.load_frozen_fixture`.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from porchlamp.core import constants
from porchlamp.core.gates import GateEvidence
from porchlamp.core.models import (
    FeatureBundle,
    Measured,
    canonical_json,
    sha256_hex,
)
from porchlamp.core.pipeline import RepoKey, repo_key, score_snapshot
from porchlamp.derive.bundle import dump_bundle, load_bundle, write_bundle
from porchlamp.derive.gate_evidence import gate_evidence_for
from porchlamp.snapshot.fixture_io import FrozenFixture, load_frozen_fixture
from typing import Callable

from porchlamp.snapshot.manifest import code_commit, merkle_root
from porchlamp.snapshot.populations import (
    PopulationMember,
    PopulationSet,
    build_populations,
    write_populations,
)

__all__ = ["ECDF_INPUT_IDS", "build_snapshot_dir"]

_CODE_ROOT = Path(__file__).resolve().parents[2]

#: The three D-NORM-3 ECDF-normalised input ids (02 section 4, 1.5/2.1/2.3).
ECDF_INPUT_IDS: tuple[str, ...] = (
    "ttm_p90_hours",
    "dependents_deduped",
    "downloads_monthly",
)


def _bundle_raw_hash(bundle: FeatureBundle) -> str:
    """sha256 of the canonical v2 bundle document."""
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
        snapshot_id=snapshot_id,
        benchmark_version=constants.BENCHMARK_VERSION,
        constants_hash=constants.CONSTANTS_HASH,
        populations=(),
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
        key=repo_key(bundle),
        ecosystem=bundle.repo.ecosystem,
        gate=pass1_gate,
        rated=pass1_rated,
        measured=measured,
    )


def build_snapshot_dir(
    data_dir: Path,
    out_dir: Path,
    *,
    snapshot_id: str,
    as_of: datetime,
    code_commit_sha: str | None = None,
) -> FrozenFixture:
    """Build a v2 snapshot directory at ``out_dir`` from ``data_dir``.

    1. Loads all ``*.json`` bundles in ``data_dir / "derived"`` using
       ``load_bundle(path, raw_root=data_dir / "raw")``.
    2. Derives ``GateEvidence`` for each repository via
       ``gate_evidence_for(data_dir / "raw", f"{owner}__{name}", as_of=as_of)``
       (D-GATE-4 counts commits in the 180 days ending at ``as_of``).
    3. Computes ``raw_hash = sha256_hex(canonical_json(dump_bundle(bundle)))``.
    4. Pass 1: scores every bundle against an empty ``PopulationSet`` via
       ``score_snapshot`` to determine gate eligibility and rated status.
    5. Pass 2: builds ``PopulationMember`` items from eligible repos for
       ``ECDF_INPUT_IDS``, calls ``build_populations(members, snapshot_id)``,
       and writes ``out_dir / "populations.json"``.
    6. Writes each v2 bundle to ``out_dir / "bundles" / f"{owner}__{name}.json"``.
    7. Writes ``out_dir / "manifest.json"`` and returns ``load_frozen_fixture(out_dir)``.

    The manifest ``merkle_root`` covers every file the build writes: its leaves
    are ``sorted([each bundle raw_hash] + [sha256 of populations.json bytes])``,
    so two builds over the same bundles but different populations differ (E-01).
    """
    derived_dir = data_dir / "derived"
    raw_dir = data_dir / "raw"
    paths = sorted(derived_dir.glob("*.json")) if derived_dir.is_dir() else []

    bundles: list[FeatureBundle] = []
    gate_evidence: dict[RepoKey, GateEvidence] = {}
    raw_hashes: dict[RepoKey, str] = {}

    for path in paths:
        bundle = load_bundle(path, raw_root=raw_dir)
        bundles.append(bundle)
        key = repo_key(bundle)
        basename = f"{bundle.repo.owner}__{bundle.repo.name}"
        gate_evidence[key] = gate_evidence_for(raw_dir, basename, as_of=as_of)
        raw_hashes[key] = _bundle_raw_hash(bundle)

    # Pass 1: score against bootstrap populations to obtain gate/rated status
    pass1 = score_snapshot(
        bundles,
        _BootstrapPopulationLookup(),
        snapshot_id=snapshot_id,
        as_of=as_of,
        gate_evidence=gate_evidence,
        raw_hashes=raw_hashes,
    )
    pass1_by_key = {f"{r.repo.owner}/{r.repo.name}": r for r in pass1.records}

    # Pass 2: build populations from eligible repos
    members = [
        _population_member(
            bundle,
            pass1_by_key[repo_key(bundle)].gate,
            pass1_by_key[repo_key(bundle)].rated,
        )
        for bundle in bundles
        if pass1_by_key[repo_key(bundle)].gate.eligible
    ]
    populations = build_populations(members, snapshot_id)

    out_dir.mkdir(parents=True, exist_ok=True)
    bundles_dir = out_dir / "bundles"
    bundles_dir.mkdir(parents=True, exist_ok=True)

    for bundle in bundles:
        basename = f"{bundle.repo.owner}__{bundle.repo.name}.json"
        write_bundle(bundle, bundles_dir / basename)

    write_populations(populations, out_dir / "populations.json")
    populations_leaf = sha256_hex((out_dir / "populations.json").read_text(encoding="utf-8"))

    commit = code_commit_sha or code_commit(_CODE_ROOT) or "unknown"
    root = merkle_root(sorted([*raw_hashes.values(), populations_leaf]))
    repos_manifest = [
        {
            "owner": bundle.repo.owner,
            "name": bundle.repo.name,
            "ecosystem": bundle.repo.ecosystem,
            "bundle_file": f"bundles/{bundle.repo.owner}__{bundle.repo.name}.json",
            "raw_hash": raw_hashes[repo_key(bundle)],
            "gate_evidence": {
                "manifest_found": gate_evidence[repo_key(bundle)].manifest_found,
                "is_curated_anchor": gate_evidence[repo_key(bundle)].is_curated_anchor,
                "license_file_present": gate_evidence[repo_key(bundle)].license_file_present,
                "fork_parent_in_universe": gate_evidence[repo_key(bundle)].fork_parent_in_universe,
                "vitality_commits_180d": gate_evidence[repo_key(bundle)].vitality_commits_180d,
                "off_platform_marker": gate_evidence[repo_key(bundle)].off_platform_marker,
                "review_handoff": gate_evidence[repo_key(bundle)].review_handoff,
                "pr_rule_inputs_recorded": gate_evidence[repo_key(bundle)].pr_rule_inputs_recorded,
                "decided_external_pr": gate_evidence[repo_key(bundle)].decided_external_pr,
            },
        }
        for bundle in sorted(bundles, key=lambda b: (b.repo.owner.lower(), b.repo.name.lower()))
    ]
    manifest_doc = {
        "snapshot_id": snapshot_id,
        "as_of": as_of.isoformat(),
        "benchmark_version": constants.BENCHMARK_VERSION,
        "constants_hash": constants.CONSTANTS_HASH,
        "code_commit": commit,
        "merkle_root": root,
        "repos": repos_manifest,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    return load_frozen_fixture(out_dir)
