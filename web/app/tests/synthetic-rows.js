// synthetic-rows.js — pure synthetic-row generator for the 10,000-row
// performance budget (06 §7). Deterministic (no Math.random) so a perf
// run is reproducible; every row has the same shape as
// `porchlamp/serve/contract.py`'s LeaderboardRow.
//
// Test-only: it lives under tests/, which the static export skips, and
// no production module imports it.

const PILLARS = ["responsiveness", "criticality", "governance", "rigor", "accessibility"];
const WEIGHTS = { responsiveness: 0.25, criticality: 0.25, governance: 0.2, rigor: 0.15, accessibility: 0.15 };
const QUADRANTS = ["Sweet Spot", "Crucible", "Nursery", "Graveyard"];

// A small linear-congruential generator so output is identical on every
// call/engine (Math.random is not reproducible run-to-run).
function makeRng(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 4294967296;
  };
}

/**
 * @param {number} n number of rows to synthesize
 * @returns {object[]} rows shaped like `porchlamp.serve.contract.LeaderboardRow`
 */
export function synthesizeRows(n = 10000) {
  const rng = makeRng(20260913);
  const rows = [];
  for (let i = 0; i < n; i++) {
    const pillarScores = {};
    const pillarCoverage = {};
    for (const p of PILLARS) {
      pillarScores[p] = Math.round((30 + rng() * 65) * 100) / 100;
      pillarCoverage[p] = Math.round((0.6 + rng() * 0.4) * 1000) / 1000;
    }
    const porchlamp = Math.round(
      PILLARS.reduce((sum, p) => sum + WEIGHTS[p] * pillarScores[p], 0) * 100
    ) / 100;
    const coverage = Math.round(
      PILLARS.reduce((sum, p) => sum + WEIGHTS[p] * pillarCoverage[p], 0) * 10000
    ) / 10000;
    const leverage = Math.round(
      (0.5 * pillarScores.criticality + 0.3 * pillarScores.rigor + 0.2 * pillarScores.governance) * 100
    ) / 100;
    const friction = Math.round(
      (0.5 * (100 - pillarScores.responsiveness) +
        0.3 * (100 - pillarScores.governance) +
        0.2 * (100 - pillarScores.accessibility)) *
        100
    ) / 100;
    const quadrant = QUADRANTS[(leverage >= 50 ? 0 : 2) + (friction >= 50 ? 1 : 0)];
    const tierCeiling = coverage >= 0.9 ? null : coverage >= 0.8 ? "A" : "B";
    const tierByScore = porchlamp >= 90 ? "S" : porchlamp >= 80 ? "A" : porchlamp >= 70 ? "B" : porchlamp >= 55 ? "C" : porchlamp >= 40 ? "D" : "F";
    const tierOrder = { S: 0, A: 1, B: 2, C: 3, D: 4, F: 5 };
    const tier = tierCeiling && tierOrder[tierCeiling] > tierOrder[tierByScore] ? tierCeiling : tierByScore;

    const row = {
      rank: i + 1,
      tie_group: i + 1,
      owner: `synthetic-${i % 500}`,
      name: `repo-${i}`,
      ecosystem: ["pypi", "npm", "crates", "go", "maven", "systems"][i % 6],
      porchlamp,
      coverage,
      tier,
      tier_ceiling: tierCeiling,
      quadrant,
      leverage,
      friction,
      leverage_coverage: coverage,
      friction_coverage: coverage,
      persona_gates: {},
      persona_composites: {},
      suspicion: i % 97 === 0 ? ["lockstep_authors"] : [],
      stars_audited: Math.floor(rng() * 20000),
      snapshot_id: "perf-synthetic",
      benchmark_version: "perf-synthetic",
    };
    for (const p of PILLARS) {
      row[`p_${p}`] = pillarScores[p];
      row[`c_${p}`] = pillarCoverage[p];
    }
    rows.push(row);
  }
  return rows;
}
