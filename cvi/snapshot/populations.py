"""CVI v2 frozen ECDF populations (stage 4, 02 section 6, D-NORM-2/3).

``build_populations`` freezes one population per (ecosystem, param input) at
a snapshot: the raw values of every parameter input over the gate-eligible,
rated repos of that ecosystem for which the input is measured (D-ORDER-1:
stage 4 after stage 3 -- gated or unrated repos must not define the
percentiles, F3). An ecosystem population of fewer than ``MIN_POPULATION``
(30, D-NORM-2) falls back to the global population (all ecosystems combined)
and carries the ``population_fallback`` flag. ``write_populations`` freezes
the set to ``populations.json``; ``build_populations_table`` produces the
plain rows for the store's ``populations`` table -- that schema and its
migration live in ``cvi/store``, which this module never touches.

A member's measured value must be finite: ``build_populations`` raises
``ValueError`` naming the repo key and the param for a NaN or +-infinity
value, rather than freezing it into a population. Such a value would
otherwise be scored at exactly the population median
(``docs/v2/02-RANKING-DESIGN.md`` D-MISS-1: an unmeasurable input is
``Unmeasured``, never a neutral score). Converting an absence to
``Unmeasured`` is the caller's job; this module refuses instead of guessing.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from cvi.core import constants
from cvi.core.models import GateResult, canonical_json, sha256_hex
from cvi.core.normalize import mid_rank_ecdf

#: D-NORM-2: minimum population per ecosystem; below it the population falls
#: back to the global population and carries ``population_fallback``.
#: Re-bound from ``cvi.core.constants.MIN_POPULATION`` under its original
#: name.
MIN_POPULATION: int = constants.MIN_POPULATION

#: Version of the ``populations.json`` document (frozen snapshot artifact).
POPULATIONS_DOCUMENT_VERSION: int = 1


@dataclass(frozen=True)
class PopulationMember:
    """One repo's stage-4 input at the snapshot (D-NORM-2).

    ``key`` is the repo key ``owner/name``; ``gate`` is the stage-0
    ``GateResult`` (``eligible=False`` never enters any population);
    ``rated`` is the stage-3 D-EVID-1 outcome; ``measured`` maps param input
    id to the raw measured value (numeric; the D-NORM-3 ECDF inputs 1.5,
    2.1, 2.3 are what the engine passes)."""

    key: str
    ecosystem: str
    gate: GateResult
    rated: bool
    measured: Mapping[str, float]


@dataclass(frozen=True)
class Population:
    """One frozen (ecosystem, param input) population (D-NORM-2).

    ``values`` maps repo key to raw value over the *effective* population:
    the ecosystem's own values, or the global values when ``fallback`` is
    set; ``n`` is the effective population size (``len(values)``)."""

    ecosystem: str
    param: str
    n: int
    fallback: bool
    values: Mapping[str, float]


@dataclass(frozen=True)
class PopulationSet:
    """All frozen populations of one snapshot, in (ecosystem, param) order."""

    snapshot_id: str
    benchmark_version: str
    constants_hash: str
    populations: tuple[Population, ...]

    def population(self, ecosystem: str, param: str) -> Population | None:
        """The frozen population for one (ecosystem, param input), or ``None``.

        ``None`` means no repo anywhere in the snapshot measured the input,
        so the engine has nothing to normalise for that pair."""
        for entry in self.populations:
            if entry.ecosystem == ecosystem and entry.param == param:
                return entry
        return None

    def ecdf(self, ecosystem: str, param: str) -> Callable[[float], float]:
        """The D-NORM-1 mid-rank ECDF of the effective population, on the 0-100 scale.

        The callback shape the ECDF curves consume. Raises
        ``LookupError`` when the snapshot has no population for the pair."""
        entry = self.population(ecosystem, param)
        if entry is None:
            raise LookupError(f"no population for ecosystem {ecosystem!r}, param {param!r}")
        return mid_rank_ecdf(tuple(entry.values.values()))


def _member_problems(member: PopulationMember) -> list[str]:
    problems: list[str] = []
    if not isinstance(member.key, str) or not member.key:
        problems.append(f"member key must be a non-empty string, got {member.key!r}")
        return problems
    if not isinstance(member.ecosystem, str) or not member.ecosystem:
        problems.append(f"member {member.key!r}: ecosystem must be a non-empty string")
    if not isinstance(member.gate, GateResult):
        problems.append(f"member {member.key!r}: gate must be a GateResult, got {type(member.gate).__name__}")
    if not isinstance(member.rated, bool):
        problems.append(f"member {member.key!r}: rated must be a bool, got {member.rated!r}")
    if not isinstance(member.measured, Mapping):
        problems.append(f"member {member.key!r}: measured must be a mapping, got {type(member.measured).__name__}")
        return problems
    for param, value in member.measured.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            problems.append(
                f"member {member.key!r}: {param!r} measured value must be numeric, got {type(value).__name__}"
            )
        elif not math.isfinite(float(value)):
            problems.append(
                f"member {member.key!r}: {param!r} measured value must be finite, got {value!r}"
            )
    return problems


def build_populations(members: Sequence[PopulationMember], snapshot_id: str) -> PopulationSet:
    """Build the frozen per-ecosystem, per-param populations of a snapshot (D-NORM-2).

    Inclusion: a repo enters a population iff its ``gate`` is eligible, it is
    ``rated``, and the param input is in its ``measured`` mapping. For each
    (ecosystem, param) with at least one measured repo, the effective
    population is the ecosystem's own values when they number at least
    ``MIN_POPULATION`` (30); below that it is the global population (all
    ecosystems combined) with ``fallback=True``. Raises ``ValueError`` for an
    empty ``snapshot_id``, a duplicate repo key, an invalid member, or a
    member with a non-finite (NaN / infinity) measured value (naming the
    repo key and the param)."""
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise ValueError(f"snapshot_id must be a non-empty string, got {snapshot_id!r}")
    problems: list[str] = []
    seen: set[str] = set()
    for member in members:
        problems.extend(_member_problems(member))
        if member.key in seen:
            problems.append(f"duplicate repo key {member.key!r}")
        seen.add(member.key)
    if problems:
        raise ValueError("; ".join(problems))

    eligible = [member for member in members if member.gate.eligible and member.rated]
    global_values: dict[str, dict[str, float]] = {}
    for member in eligible:
        for param, value in member.measured.items():
            global_values.setdefault(param, {})[member.key] = float(value)
    pairs: set[tuple[str, str]] = {
        (member.ecosystem, param) for member in eligible for param in member.measured
    }
    populations: list[Population] = []
    for ecosystem, param in sorted(pairs):
        own = {
            member.key: float(member.measured[param])
            for member in eligible
            if member.ecosystem == ecosystem and param in member.measured
        }
        fallback = len(own) < MIN_POPULATION
        effective = global_values[param] if fallback else own
        populations.append(
            Population(
                ecosystem,
                param,
                len(effective),
                fallback,
                dict(sorted(effective.items())),
            )
        )
    return PopulationSet(
        snapshot_id=snapshot_id,
        benchmark_version=constants.BENCHMARK_VERSION,
        constants_hash=constants.CONSTANTS_HASH,
        populations=tuple(populations),
    )


def write_populations(popset: PopulationSet, path: Path | str) -> Path:
    """Freeze a ``PopulationSet`` to ``populations.json`` (D-NORM-2).

    The document records the ``snapshot_id``, the benchmark version and the
    constants hash in force at freeze time (D-VER-2/3), the minimum
    population, and one entry per (ecosystem, param input) with the
    effective values (repo key to raw value) and the
    ``population_fallback`` flag. Sorted keys, fixed indent, trailing
    newline: the same freeze writes the same bytes, so two files diff as
    text. Parent directories are created and an existing file is
    overwritten; the path is returned."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "document": "cvi-populations",
        "version": POPULATIONS_DOCUMENT_VERSION,
        "snapshot_id": popset.snapshot_id,
        "benchmark_version": popset.benchmark_version,
        "constants_hash": popset.constants_hash,
        "min_population": MIN_POPULATION,
        "populations": [
            {
                "ecosystem": entry.ecosystem,
                "param": entry.param,
                "n": entry.n,
                "fallback": entry.fallback,
                "values": {key: entry.values[key] for key in sorted(entry.values)},
            }
            for entry in popset.populations
        ],
    }
    target.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def build_populations_table(popset: PopulationSet) -> list[tuple[str, str, str, int, bool, str, str]]:
    """Plain rows for the store's ``populations`` table, one per population.

    Row layout: ``(snapshot_id, ecosystem, param, n, fallback,
    values_json, values_hash)``; ``values_json`` is the canonical JSON of
    the effective population (repo key to raw value, sorted by key) and
    ``values_hash`` its SHA-256, so a reader can verify the frozen values
    without the snapshot file. The schema and the migration live in
    ``cvi/store``; this function only builds rows and never touches it."""
    rows: list[tuple[str, str, str, int, bool, str, str]] = []
    for entry in popset.populations:
        values_json = canonical_json({key: entry.values[key] for key in sorted(entry.values)})
        rows.append(
            (
                popset.snapshot_id,
                entry.ecosystem,
                entry.param,
                entry.n,
                entry.fallback,
                values_json,
                sha256_hex(values_json),
            )
        )
    return rows
