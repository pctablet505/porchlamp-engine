"""N1 (round-3 review, 2026-09-14): the B-1 fix to `web/v2/views/
configure.js::normalizeWeights` (locking
four pillars and dragging the fifth DOWN left the sum at 0.87, not 1) was
guarded ONLY by `web/v2/tests/unit.test.mjs`, which cannot execute on this
box (no Node). This file is a FAITHFUL, line-for-line PORT of
`normalizeWeights` and `applyPresetRespectingLocks` into Python — not a
reimplementation from the docstring, a direct translation of the JS
source, kept in a docstring comment above each function pointing at the
exact JS lines it mirrors — so it can be property- and case-tested with
plain pytest.

Ported from `web/v2/views/configure.js` at round-3 dispatch time (post the
B-1 fix, commit `314ce2c`). If either function's JS changes, re-check this
port for drift — the two are independent code, not a shared source of
truth; that's exactly why `tests/cases/normalize_weights.json` exists: it
lets a drift between this port and the real JS show up as ONE of the two
test suites failing its own copy of the same expected numbers (this file's
`TestSharedJsonCases`, and `web/v2/tests/unit.test.mjs`'s equivalent).

Started life as the round-2 reviewer's own property-test script
property test suite ported
into a real pytest module here with more branch-specific cases folded in.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

PILLAR_IDS = ["responsiveness", "criticality", "governance", "rigor", "accessibility"]

BALANCED_WEIGHTS = {
    "responsiveness": 0.25,
    "criticality": 0.25,
    "governance": 0.20,
    "rigor": 0.15,
    "accessibility": 0.15,
}

EPS = 1e-9

CASES_PATH = Path(__file__).resolve().parent.parent / "web" / "v2" / "tests" / "cases" / "normalize_weights.json"


def normalize_weights(
    weights: dict[str, float], locks: dict[str, bool], changed_pillar: str, new_value: float
) -> dict[str, float]:
    """Port of `web/v2/views/configure.js::normalizeWeights` (round-3
    dispatch tip, post B-1 fix):

        const clamped = Math.max(0, Math.min(1, newValue));
        const next = { ...weights, [changedPillar]: clamped };
        const fixed = new Set([changedPillar, ...PILLAR_IDS.filter((p) => locks[p])]);
        const fixedSum = PILLAR_IDS.filter((p) => fixed.has(p)).reduce((s, p) => s + next[p], 0);
        const free = PILLAR_IDS.filter((p) => !fixed.has(p));
        if (free.length === 0) {
          const lockedSum = fixedSum - next[changedPillar];
          next[changedPillar] = Math.max(0, 1 - lockedSum);
          return next;
        }
        let remaining = 1 - fixedSum;
        if (remaining < 0) {
          const lockedSum = fixedSum - next[changedPillar];
          next[changedPillar] = Math.max(0, 1 - lockedSum);
          remaining = 0;
        }
        const freeCurrentSum = free.reduce((s, p) => s + weights[p], 0);
        for (const p of free) {
          next[p] = freeCurrentSum > 0 ? (weights[p] / freeCurrentSum) * remaining : remaining / free.length;
        }
        return next;
    """
    clamped = max(0.0, min(1.0, new_value))
    nxt = dict(weights)
    nxt[changed_pillar] = clamped

    fixed = {changed_pillar} | {p for p in PILLAR_IDS if locks.get(p)}
    fixed_sum = sum(nxt[p] for p in PILLAR_IDS if p in fixed)
    free = [p for p in PILLAR_IDS if p not in fixed]

    if not free:
        # Every OTHER pillar is locked: the dragged one has no freedom at
        # all -- its value is fully determined by the locked ones. This is
        # the exact branch B-1 fixed (it used to only correct on
        # OVERFLOW, leaving a downward drag at the wrong sum).
        locked_sum = fixed_sum - nxt[changed_pillar]
        nxt[changed_pillar] = max(0.0, 1 - locked_sum)
        return nxt

    remaining = 1 - fixed_sum
    if remaining < 0:
        locked_sum = fixed_sum - nxt[changed_pillar]
        nxt[changed_pillar] = max(0.0, 1 - locked_sum)
        remaining = 0.0

    free_current_sum = sum(weights[p] for p in free)
    for p in free:
        nxt[p] = (weights[p] / free_current_sum) * remaining if free_current_sum > 0 else remaining / len(free)
    return nxt


def apply_preset_respecting_locks(
    weights: dict[str, float], locks: dict[str, bool], preset: dict[str, float]
) -> dict[str, float]:
    """Port of `web/v2/views/configure.js::applyPresetRespectingLocks`:

        const lockedSum = PILLAR_IDS.filter((p) => locks[p]).reduce((s, p) => s + weights[p], 0);
        const free = PILLAR_IDS.filter((p) => !locks[p]);
        const remaining = Math.max(0, 1 - lockedSum);
        const presetFreeSum = free.reduce((s, p) => s + preset[p], 0);
        const next = { ...weights };
        for (const p of free) {
          next[p] = presetFreeSum > 0 ? (preset[p] / presetFreeSum) * remaining : remaining / free.length;
        }
        return next;
    """
    locked_sum = sum(weights[p] for p in PILLAR_IDS if locks.get(p))
    free = [p for p in PILLAR_IDS if not locks.get(p)]
    remaining = max(0.0, 1 - locked_sum)
    preset_free_sum = sum(preset[p] for p in free)
    nxt = dict(weights)
    for p in free:
        nxt[p] = (preset[p] / preset_free_sum) * remaining if preset_free_sum > 0 else remaining / len(free)
    return nxt


def _assert_valid_distribution(
    result: dict[str, float], locks: dict[str, bool], before: dict[str, float], label: str
) -> None:
    total = sum(result[p] for p in PILLAR_IDS)
    assert abs(total - 1.0) < 1e-6, f"{label}: sum {total} must be 1 (+/-1e-6)"
    for p in PILLAR_IDS:
        assert result[p] >= -EPS, f"{label}: {p}={result[p]} must not be negative"
        if locks.get(p):
            assert abs(result[p] - before[p]) < EPS, f"{label}: locked pillar {p} moved {before[p]} -> {result[p]}"


class TestNormalizeWeightsProperties:
    """P1 sum==1, P2 locked pillars unchanged, P3 no negative values —
    exercised over every lock subset x a value grid, then a seeded random
    walk, matching the shape the round-2 reviewer's own property test used
    property test suite."""

    def test_every_lock_subset_and_value_grid_keeps_the_invariant(self) -> None:
        base = dict(BALANCED_WEIGHTS)
        failures: list[str] = []
        for mask in range(32):  # every subset of the 5 pillars
            locks = {p: bool(mask & (1 << i)) for i, p in enumerate(PILLAR_IDS)}
            free_pillars = [p for p in PILLAR_IDS if not locks[p]]
            if not free_pillars:
                continue  # a locked slider is never draggable in the UI
            for changed in free_pillars:
                for target in (0.0, 0.01, 0.2, 0.5, 0.8, 1.0):
                    result = normalize_weights(base, locks, changed, target)
                    try:
                        _assert_valid_distribution(result, locks, base, f"mask={mask} changed={changed} target={target}")
                    except AssertionError as exc:  # pragma: no cover - failure path
                        failures.append(str(exc))
        assert not failures, "\n".join(failures[:10])

    def test_randomised_walk_of_locks_and_drags_never_breaks_the_invariant(self) -> None:
        rng = random.Random(20260914)
        weights = dict(BALANCED_WEIGHTS)
        locks = {p: False for p in PILLAR_IDS}
        for step in range(2000):
            if rng.random() < 0.3:
                p = rng.choice(PILLAR_IDS)
                would_lock_all = not locks[p] and all(q == p or locks[q] for q in PILLAR_IDS)
                if not would_lock_all:
                    locks = {**locks, p: not locks[p]}
            free_pillars = [p for p in PILLAR_IDS if not locks[p]]
            if not free_pillars:
                continue
            changed = rng.choice(free_pillars)
            target = rng.random()
            before = weights
            weights = normalize_weights(weights, locks, changed, target)
            _assert_valid_distribution(weights, locks, before, f"step={step}")

    def test_the_b1_repro_four_locked_fifth_dragged_down_snaps_to_the_only_valid_value(self) -> None:
        locks = {"responsiveness": True, "criticality": True, "governance": True, "accessibility": True}
        result = normalize_weights(BALANCED_WEIGHTS, locks, "rigor", 0.02)
        _assert_valid_distribution(result, locks, BALANCED_WEIGHTS, "four-locked repro")
        assert abs(result["rigor"] - 0.15) < EPS

    def test_presets_under_every_lock_subset(self) -> None:
        presets = {
            "first_timer": {"responsiveness": 0.40, "criticality": 0.10, "governance": 0.15, "rigor": 0.10, "accessibility": 0.25},
            "senior_systems": {"responsiveness": 0.15, "criticality": 0.35, "governance": 0.25, "rigor": 0.20, "accessibility": 0.05},
        }
        for mask in range(32):
            locks = {p: bool(mask & (1 << i)) for i, p in enumerate(PILLAR_IDS)}
            for preset in presets.values():
                result = apply_preset_respecting_locks(BALANCED_WEIGHTS, locks, preset)
                _assert_valid_distribution(result, locks, BALANCED_WEIGHTS, f"preset locks={mask}")


def _load_cases() -> dict[str, Any]:
    return json.loads(CASES_PATH.read_text())


class TestSharedJsonCases:
    """The SAME numbers `web/v2/tests/unit.test.mjs` asserts against the
    real JS -- a drift between this Python port and the real
    `normalizeWeights`/`applyPresetRespectingLocks` shows up as one suite
    failing its own copy of these cases."""

    def test_cases_file_exists_and_is_non_trivial(self) -> None:
        assert CASES_PATH.is_file(), f"shared fixture missing: {CASES_PATH}"
        cases = _load_cases()
        assert len(cases["normalize_weights_cases"]) >= 4
        assert len(cases["apply_preset_respecting_locks_cases"]) >= 3
        assert cases["pillar_ids"] == PILLAR_IDS

    def test_normalize_weights_cases_match(self) -> None:
        cases = _load_cases()
        for case in cases["normalize_weights_cases"]:
            result = normalize_weights(case["weights"], case["locks"], case["changed_pillar"], case["new_value"])
            for p in PILLAR_IDS:
                assert abs(result[p] - case["expected"][p]) < EPS, (
                    f"{case['label']}: {p} = {result[p]}, expected {case['expected'][p]}"
                )

    def test_apply_preset_respecting_locks_cases_match(self) -> None:
        cases = _load_cases()
        for case in cases["apply_preset_respecting_locks_cases"]:
            result = apply_preset_respecting_locks(case["weights"], case["locks"], case["preset"])
            for p in PILLAR_IDS:
                assert abs(result[p] - case["expected"][p]) < EPS, (
                    f"{case['label']}: {p} = {result[p]}, expected {case['expected'][p]}"
                )

    def test_detector_itself_a_reverted_b1_fix_fails_the_four_locked_case(self) -> None:
        """Fail-before proof (N1's own "over-correction guard" check, per
        the standing "every fix ships a test" rule): reverting the B-1 fix
        -- the `free.length === 0` branch only correcting an OVERFLOW,
        never a shortfall -- must make THIS test's own case fail, so the
        case set is proven load-bearing rather than vacuous."""
        locks = {"responsiveness": True, "criticality": True, "governance": True, "accessibility": True}

        def pre_b1_normalize_weights(weights, locks, changed_pillar, new_value):
            clamped = max(0.0, min(1.0, new_value))
            nxt = dict(weights)
            nxt[changed_pillar] = clamped
            fixed = {changed_pillar} | {p for p in PILLAR_IDS if locks.get(p)}
            fixed_sum = sum(nxt[p] for p in PILLAR_IDS if p in fixed)
            free = [p for p in PILLAR_IDS if p not in fixed]
            if not free:
                # PRE-B1: only corrected an OVERFLOW (fixed_sum > 1), a
                # downward drag (fixed_sum <= 1) fell through untouched.
                if fixed_sum > 1:
                    locked_sum = fixed_sum - nxt[changed_pillar]
                    nxt[changed_pillar] = max(0.0, 1 - locked_sum)
                return nxt
            return nxt  # unreachable for this specific case's inputs

        result = pre_b1_normalize_weights(BALANCED_WEIGHTS, locks, "rigor", 0.02)
        total = sum(result[p] for p in PILLAR_IDS)
        assert abs(total - 1.0) > 1e-6, (
            f"the pre-B1 implementation's sum ({total}) must NOT already be 1 for this case to prove anything; "
            "if it is, the fixture case no longer exercises the free.length===0 shortfall branch"
        )
