"""W2-4 acceptance: frozen per-ecosystem ECDF populations (02 section 6, D-NORM-2/3).

``cvi.snapshot.populations`` builds one population per (ecosystem, param input)
over the rated, gate-eligible repos for which the parameter is measured at the
snapshot (D-ORDER-1 stage 4 after stage 3): a population of fewer than 30
repos falls back to the global population and carries the
``population_fallback`` flag. Gated repos never enter any population (their
values must not define the percentiles, F3); unrated repos do not either.
Every expected value below is hand-computed from the D-NORM-1/2 formulas.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from cvi.core import constants_v2
from cvi.core import gates
from cvi.core import models
from cvi.core.models import GateResult
from cvi.snapshot import populations

PARAM = "downloads_monthly"
#: W2-3 registers downloads_monthly as the registry-download input (02 2.3).

#: Child process for TestEmissionOrder.test_written_document_order_is_stable_
#: across_pythonhashseed: builds a fixed PopulationSet spanning six
#: ecosystems and two params -- so the (ecosystem, param) set has enough
#: elements for its unsorted iteration order to be sensitive to string hash
#: randomization -- and writes the frozen document to the path in argv[1].
#: Every measured value is computed from plain indices, never from `hash()`,
#: so PYTHONHASHSEED cannot change the population content, only (absent
#: `sorted(pairs)`) the emission order.
_ORDER_PROBE_SCRIPT = """
import sys

from cvi.core.models import GateResult
from cvi.snapshot import populations

ECOSYSTEMS = ("crates", "go", "maven", "npm", "pypi", "systems")
PARAMS = ("param_a", "param_b")


def _gate():
    return GateResult(eligible=True, reason=None, routed=None, undecided=())


members = []
for e_idx, ecosystem in enumerate(ECOSYSTEMS):
    for i in range(2):
        measured = {
            param: float(e_idx * 100 + p_idx * 10 + i)
            for p_idx, param in enumerate(PARAMS)
        }
        members.append(
            populations.PopulationMember(
                key=f"{ecosystem}/r{i}", ecosystem=ecosystem,
                gate=_gate(), rated=True, measured=measured,
            )
        )

result = populations.build_populations(members, "hash-seed-probe")
path = populations.write_populations(result, sys.argv[1])
sys.stdout.write(path.read_text(encoding="utf-8"))
"""


def _gate(eligible=True, reason=None, routed=None) -> GateResult:
    return GateResult(eligible=eligible, reason=reason, routed=routed, undecided=())


def _member(key: str, ecosystem: str, value: float, eligible=True,
             reason=None, routed=None, rated=True) -> populations.PopulationMember:
    return populations.PopulationMember(
        key=key,
        ecosystem=ecosystem,
        gate=_gate(eligible=eligible, reason=reason, routed=routed),
        rated=rated,
        measured={PARAM: value},
    )


def _pypi_members(count: int, start: int = 1) -> list[populations.PopulationMember]:
    return [_member(f"org/r{start + i}", "pypi", float(start + i)) for i in range(count)]


class TestMinPopulationFallback:
    def test_twenty_nine_below_min_is_flagged(self) -> None:
        result = populations.build_populations(_pypi_members(29), "s0")
        pop = result.population("pypi", PARAM)
        assert pop is not None
        assert pop.fallback is True
        assert pop.n == 29
        assert len(pop.values) == 29

    def test_at_min_is_not_flagged(self) -> None:
        result = populations.build_populations(_pypi_members(30), "s0")
        pop = result.population("pypi", PARAM)
        assert pop is not None
        assert pop.fallback is False
        assert pop.n == 30
        assert set(pop.values) == {f"org/r{i}" for i in range(1, 31)}

    def test_fallback_uses_the_global_population(self) -> None:
        go = [_member(f"go/g{i}", "go", float(100 + i)) for i in range(40)]
        pypi = _pypi_members(29)
        result = populations.build_populations(pypi + go, "s0")
        pypi_pop = result.population("pypi", PARAM)
        go_pop = result.population("go", PARAM)
        assert pypi_pop.fallback is True
        assert pypi_pop.n == 69
        assert set(pypi_pop.values) == {f"org/r{i}" for i in range(1, 30)} | {f"go/g{i}" for i in range(40)}
        assert go_pop.fallback is False
        assert go_pop.n == 40
        assert set(go_pop.values) == {f"go/g{i}" for i in range(40)}
        assert pypi_pop.values["org/r1"] == 1.0


class TestMembership:
    def test_gated_repos_are_excluded_from_populations(self) -> None:
        black_hole = _member(
            "evil/void", "pypi", 999999.0,
            eligible=False, reason=gates.REASON_BLACK_HOLE,
        )
        result = populations.build_populations(_pypi_members(30) + [black_hole], "s0")
        pop = result.population("pypi", PARAM)
        assert pop is not None
        assert "evil/void" not in pop.values
        assert pop.n == 30
        assert pop.fallback is False
        assert pop.values.get("org/r1") == 1.0

    def test_gated_repos_do_not_count_toward_the_boundary(self) -> None:
        gated = [
            _member(f"stale/s{i}", "pypi", float(500 + i),
                    eligible=False, reason=gates.REASON_STALE)
            for i in range(2)
        ]
        result = populations.build_populations(_pypi_members(29) + gated, "s0")
        pop = result.population("pypi", PARAM)
        assert pop.fallback is True
        assert pop.n == 29

    def test_unrated_repos_are_excluded_from_populations(self) -> None:
        unrated = _member("low/evidence", "pypi", 888888.0, rated=False)
        result = populations.build_populations(_pypi_members(30) + [unrated], "s0")
        pop = result.population("pypi", PARAM)
        assert pop is not None
        assert "low/evidence" not in pop.values
        assert pop.n == 30

    def test_routed_off_platform_repo_stays_included(self) -> None:
        routed = _member(
            "list/only", "go", 7.0, routed=gates.ROUTED_OFF_PLATFORM,
        )
        members = [_member(f"go/g{i}", "go", float(i + 1)) for i in range(29)] + [routed]
        result = populations.build_populations(members, "s0")
        pop = result.population("go", PARAM)
        assert pop is not None
        assert "list/only" in pop.values
        assert pop.n == 30
        assert pop.fallback is False


class TestNonFiniteRejection:
    def test_nan_measured_value_raises_naming_key_and_param(self) -> None:
        # Fail-before: build_populations accepted this member silently and
        # froze `nan` into pop.values -- see the module docstring and
        # D-MISS-1 (docs/v2/02-RANKING-DESIGN.md).
        bad = _member("org/nan-repo", "pypi", float("nan"))
        with pytest.raises(ValueError) as excinfo:
            populations.build_populations(_pypi_members(29) + [bad], "s0")
        assert "org/nan-repo" in str(excinfo.value)
        assert PARAM in str(excinfo.value)
        assert "finite" in str(excinfo.value)

    def test_infinite_measured_value_raises_naming_key_and_param(self) -> None:
        bad = _member("org/inf-repo", "pypi", float("inf"))
        with pytest.raises(ValueError) as excinfo:
            populations.build_populations(_pypi_members(29) + [bad], "s0")
        assert "org/inf-repo" in str(excinfo.value)
        assert PARAM in str(excinfo.value)
        assert "finite" in str(excinfo.value)

    def test_finite_members_are_unchanged_by_the_finiteness_check(self) -> None:
        # Over-correction guard: an all-finite membership must build exactly
        # as before -- same effective population, same fallback decision.
        result = populations.build_populations(_pypi_members(30), "s0")
        pop = result.population("pypi", PARAM)
        assert pop is not None
        assert pop.fallback is False
        assert pop.n == 30
        assert pop.values["org/r1"] == 1.0


class TestSetIntegrity:
    def test_duplicate_repo_key_raises(self) -> None:
        members = _pypi_members(2) + [_member("org/r1", "pypi", 99.0)]
        with pytest.raises(ValueError):
            populations.build_populations(members, "s0")

    def test_snapshot_id_must_be_nonempty(self) -> None:
        with pytest.raises(ValueError):
            populations.build_populations(_pypi_members(1), "")

    def test_no_measured_values_means_no_populations(self) -> None:
        empty = populations.PopulationMember(
            key="org/empty", ecosystem="pypi",
            gate=_gate(), rated=True, measured={},
        )
        result = populations.build_populations([empty], "s0")
        assert result.populations == ()
        assert result.population("pypi", PARAM) is None
        with pytest.raises(LookupError):
            result.ecdf("pypi", PARAM)

    def test_ecdf_of_frozen_population_uses_the_effective_population(self) -> None:
        # pypi (2) falls back to the global population of 3 values {1.0, 2.0, 3.0};
        # the pypi ECDF must be the mid-rank ECDF of all 3, not of pypi's 2
        # (whose mid-rank ECDF would give 75.0 at 3.0):
        #   R(3.0) = 100 * (2 + 0.5*1) / 3 = 83 1/3
        pypi = [_member("org/r1", "pypi", 1.0), _member("org/r2", "pypi", 3.0)]
        go = [_member("go/g0", "go", 2.0)]
        result = populations.build_populations(pypi + go, "s0")
        ecdf = result.ecdf("pypi", PARAM)
        assert ecdf(3.0) == 100.0 * 2.5 / 3.0
        assert ecdf(1.0) == 100.0 * 0.5 / 3.0
        assert ecdf(2.0) == 100.0 * 1.5 / 3.0
        with pytest.raises(LookupError):
            result.ecdf("crates", PARAM)

    def test_build_is_deterministic_under_input_ordering(self) -> None:
        import random

        base = _pypi_members(20) + [_member(f"go/g{i}", "go", float(i + 1)) for i in range(12)]
        shuffled = list(base)
        random.Random(20260912).shuffle(shuffled)
        first = populations.build_populations(base, "s0")
        second = populations.build_populations(shuffled, "s0")
        assert populations.build_populations_table(first) == populations.build_populations_table(second)


class TestEmissionOrder:
    _EXPECTED_ORDER = [
        ("crates", "param_a"), ("crates", "param_b"),
        ("go", "param_a"), ("go", "param_b"),
        ("maven", "param_a"), ("maven", "param_b"),
        ("npm", "param_a"), ("npm", "param_b"),
        ("pypi", "param_a"), ("pypi", "param_b"),
        ("systems", "param_a"), ("systems", "param_b"),
    ]

    def test_population_order_is_the_sorted_ecosystem_param_order(self) -> None:
        # Members submitted in reverse ecosystem order, and each member's
        # `measured` mapping keyed in reverse param order too, so neither
        # list order nor dict insertion order could accidentally look sorted.
        ecosystems_reverse = ("systems", "pypi", "npm", "maven", "go", "crates")
        members = [
            populations.PopulationMember(
                key=f"{ecosystem}/r{i}",
                ecosystem=ecosystem,
                gate=_gate(),
                rated=True,
                measured={"param_b": float(i), "param_a": float(i)},
            )
            for ecosystem in ecosystems_reverse
            for i in range(2)
        ]
        result = populations.build_populations(members, "s0")
        assert [(entry.ecosystem, entry.param) for entry in result.populations] == self._EXPECTED_ORDER

    def test_written_document_order_is_stable_across_pythonhashseed(self, tmp_path) -> None:
        # Fail-before (mutant "remove sorted(pairs)"): with the emission
        # order taken from raw `set` iteration, the frozen document's
        # `populations` array order -- and so its bytes -- differs between
        # PYTHONHASHSEED values, even though every one of these three
        # subprocesses runs the identical build.
        raw_by_seed: dict[str, str] = {}
        for seed in ("1", "2", "3"):
            env = dict(os.environ)
            env["PYTHONHASHSEED"] = seed
            target = tmp_path / f"seed-{seed}.json"
            completed = subprocess.run(
                [sys.executable, "-B", "-c", _ORDER_PROBE_SCRIPT, str(target)],
                env=env, capture_output=True, text=True,
            )
            assert completed.returncode == 0, completed.stderr
            raw_by_seed[seed] = completed.stdout

        for seed, raw in raw_by_seed.items():
            document = json.loads(raw)
            order = [(entry["ecosystem"], entry["param"]) for entry in document["populations"]]
            assert order == self._EXPECTED_ORDER, f"seed {seed}: order {order}"

        assert raw_by_seed["1"] == raw_by_seed["2"] == raw_by_seed["3"]


class TestFreeze:
    @pytest.fixture()
    def result(self) -> populations.PopulationSet:
        go = [_member(f"go/g{i}", "go", float(100 + i)) for i in range(40)]
        return populations.build_populations(_pypi_members(29) + go, "snap-7")

    def test_write_populations_document(self, result, tmp_path) -> None:
        path = populations.write_populations(result, tmp_path / "snaps" / "snap-7" / "populations.json")
        assert path.is_file()
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["snapshot_id"] == "snap-7"
        assert document["benchmark_version"] == constants_v2.BENCHMARK_VERSION
        assert document["constants_hash"] == constants_v2.CONSTANTS_HASH
        assert document["min_population"] == populations.MIN_POPULATION == 30
        pypi = next(entry for entry in document["populations"] if entry["ecosystem"] == "pypi")
        assert pypi["param"] == PARAM
        assert pypi["n"] == 69  # the effective population is the global one
        assert pypi["fallback"] is True
        assert len(pypi["values"]) == 69  # the global fallback population
        go = next(entry for entry in document["populations"] if entry["ecosystem"] == "go")
        assert go["n"] == 40
        assert go["fallback"] is False
        assert go["values"] == {f"go/g{i}": float(100 + i) for i in range(40)}
        raw = path.read_text(encoding="utf-8")
        assert raw == json.dumps(document, indent=2, sort_keys=True) + "\n"

    def test_frozen_document_header_fields_are_pinned(self, result, tmp_path) -> None:
        # Fail-before: neither the document tag nor the version number was
        # asserted against a literal anywhere in the suite, so
        # POPULATIONS_DOCUMENT_VERSION 1 -> 2 and "cvi-populations" ->
        # "cvi-populations-v2" both survived every packet test.
        path = populations.write_populations(result, tmp_path / "populations.json")
        document = json.loads(path.read_text(encoding="utf-8"))
        assert set(document) == {
            "document", "version", "snapshot_id", "benchmark_version",
            "constants_hash", "min_population", "populations",
        }
        assert document["document"] == "cvi-populations"
        assert populations.POPULATIONS_DOCUMENT_VERSION == 1
        assert document["version"] == 1
        assert document["min_population"] == 30
        assert document["benchmark_version"] == constants_v2.BENCHMARK_VERSION
        assert document["constants_hash"] == constants_v2.CONSTANTS_HASH

    def test_store_table_rows(self, result) -> None:
        rows = populations.build_populations_table(result)
        by_eco = {row[1]: row for row in rows}
        assert set(by_eco) == {"pypi", "go"}
        for eco in ("pypi", "go"):
            snapshot_id, ecosystem, param, n, fallback, values_json, values_hash = by_eco[eco]
            assert snapshot_id == "snap-7"
            assert ecosystem == eco
            assert param == PARAM
            expected_values = {
                key: result.population(eco, PARAM).values[key]
                for key in sorted(result.population(eco, PARAM).values)
            }
            assert values_json == models.canonical_json(expected_values)
            assert values_hash == models.sha256_hex(values_json)
        assert by_eco["pypi"][3] == 69 and by_eco["pypi"][4] is True
        assert by_eco["go"][3] == 40 and by_eco["go"][4] is False
