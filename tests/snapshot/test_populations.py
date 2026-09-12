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

import pytest

from cvi.core import constants_v2
from cvi.core import gates
from cvi.core import models
from cvi.core.models import GateResult
from cvi.snapshot import populations

PARAM = "downloads_monthly"
#: W2-3 registers downloads_monthly as the registry-download input (02 2.3).


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
    def test_nine_below_min_is_flagged(self) -> None:
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
