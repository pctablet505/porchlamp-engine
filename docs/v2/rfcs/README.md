# Constants-change RFCs

`cvi/core/constants_v2.py` is frozen (D-VER-1/2, `docs/v2/02-RANKING-DESIGN.md`
section 11; `docs/v2/03-ANTI-MANIPULATION.md` section 7). Every value the
ranking computes with — pillar weights, per-parameter weights and minimum
sample sizes, curve-shape constants, coverage/ceiling/gate thresholds, tier
cutoffs and their ordering, the OpenSSF/Pike criticality weights/thresholds/
signal mapping, the OSI-approved-license predicate, persona presets and
their gate thresholds, `MIN_POPULATION`, `COMPOSITE_DECIMALS` — lives in
that one module, and `CONSTANTS_HASH = sha256(canonical JSON of
constants_dict())` is stamped into every stored `ScoreRecord` row and the
snapshot manifest. `tests/core/test_frozen.py` pins the hash, the benchmark
version and a representative-value table to literals; changing any covered
constant moves the hash and fails that test.
`tests/core/test_frozen.py::test_every_module_level_constant_is_hashed_or_declared_not_hashed`
additionally proves the "lives in that one module" claim is exhaustive: a
new module-level constant that is neither a `constants_dict()` key nor
listed in `_NOT_HASHED` fails that test (packet W2-8 round 2, closing the
round-1 review's census — 10 tunables were previously readable from a
scoring module but invisible to the hash; see the round-2 handoff's
completed census table for the full before/after).

This sentence was false between W2-8 round 1 and round 2: `TIER_CUTOFFS`/
`TIER_ORDER`, the OpenSSF weights/thresholds/signal map and
`OSI_APPROVED_LICENSES` were each read by a scoring or gating path but
absent from `constants_dict()` — each one's own drift test moved zero
scored bits while the underlying computation moved measurably (documented,
reproduced numbers in the round-2 handoff). It is accurate as of round 2's
commit.

**A PR that changes a value in `constants_v2.py` without following this
procedure is rejected in review.** The failing hash test in
`tests/core/test_frozen.py` is the enforcement mechanism, not a formality —
it is what makes an unreviewed constants change visible as a red test
instead of a silent ranking shift (with the two documented limits below).

## Procedure

1. **Write an RFC file** at `docs/v2/rfcs/RFC-NNNN-<slug>.md` using the
   template below. `NNNN` is the next unused 4-digit number (zero-padded);
   `<slug>` is a short kebab-case description (e.g. `RFC-0001-legal-ip-none-score`).
2. **Update the pinned hash** in `tests/core/test_frozen.py`
   (`FROZEN_CONSTANTS_HASH`) and, if the changed value is one of the pinned
   representative rows (`_FROZEN_TABLE`), that row too — **in the same
   commit** as the `constants_v2.py` change. Recompute the new hash with:
   ```
   python -c "from cvi.core import constants_v2 as c; print(c.CONSTANTS_HASH)"
   ```
3. **Bump `BENCHMARK_VERSION`** per D-VER-1: a weight or curve-constant
   change is a minor bump; a parameter-set, gate or missingness-policy
   change is major; a collector fix that changes measured values (not a
   `constants_v2.py` edit at all) is a patch with a re-snapshot.
4. **Add a line to `docs/v2/rfcs/CHANGELOG.md`**: date, RFC number, one-line
   summary, the old and new `CONSTANTS_HASH`.
5. Get the RFC reviewed per the hub review protocol before merge (a
   constants change is treated as a behavioural change: `docs/REVIEW_GUIDELINES.md`,
   `03` section 7 — "every ACCEPT is reproduced by a second independent
   agent; mutation proof for behavioural changes").

Until `BENCHMARK_VERSION` reaches the final `"2.0.0"` (flipped at release by
packet W5-2), constants may still change — but only through this same
procedure. There is no "still in development, skip the RFC" exception: the
hash test does not know the difference, and neither should reviewers.

### RFC class: upstream vocabulary refresh

`OSI_APPROVED_LICENSES` is not a weight or curve parameter, but D-GATE-3
*interprets* it as a predicate: SPDX-id membership decides eligible vs
`REASON_UNLICENSED`, so an upstream SPDX/OSI list update changes which
repos are in the benchmark at all — a larger rank movement than most weight
changes. Refreshing it from the upstream sources (see
`cvi/core/constants_v2.py`'s docstring on `OSI_APPROVED_LICENSES` for where
they are) follows the same five-step procedure above; the RFC's "Evidence"
section for this class reports the added/removed id list and the count of
repos whose gate outcome flips, in place of a weight-perturbation
sensitivity report (there is no weight to perturb).

## Process constants (not hashed, changed by RFC all the same)

Not every constant that affects the *pipeline*'s behaviour is a scoring
input, and `CONSTANTS_HASH`'s contract (D-VER-2: it identifies the
methodology that produced a *stored row*) only needs to cover values that
change a stored `ScoreRecord` field. Three constants fail that test on
purpose and are ruled out of `constants_dict()` (W2-8 round 2, lead ruling):

| Constant | Module | What it gates |
|---|---|---|
| `MIN_COVERAGE` | `cvi/audit/anchors.py` | D-VER-4 anchor-check threshold (a release gate, not a per-repo score) |
| `TAU_RELEASE_THRESHOLD` | `cvi/audit/sensitivity.py` | D-AGG-3's tau >= 0.90 release bar |
| `DEFAULT_PERTURBATION_PP` | `cvi/audit/sensitivity.py` | the +-5pp weight-perturbation size the sensitivity report tests at |

None of these three is read anywhere in `cvi.core`'s scoring path
(`engine.score`, `aggregate.*`, `matrix.*`, `params.*`); changing one moves
no `ScoreRecord` field for any repo — it only changes whether a *snapshot*
clears the bar for release. They are still constants-freeze subjects in the
process sense: a change to any of the three **still requires the RFC
procedure above** (an RFC file, a `CHANGELOG.md` row, a named reviewer) —
lowering `TAU_RELEASE_THRESHOLD` to make a weak ranking look releasable is
exactly the kind of unreviewed change this whole directory exists to
prevent — but it does **not** move `CONSTANTS_HASH`, `FROZEN_CONSTANTS_HASH`,
or `_FROZEN_TABLE`, and the RFC's "Evidence" section for this class reports
the release-gate impact (how many past snapshots would newly pass or fail)
rather than a rank-movement/sensitivity report.

## Enforcement limits (documented, not built)

The hash test is necessary, not sufficient. Two known gaps, deliberately
left as documentation rather than a linter (a linter here would be a second,
drifting definition of "what counts as reading a constant" — exactly the
kind of duplication this freeze exists to remove):

- **A call site reverted to a literal of the same value is invisible.** If
  `cvi/core/tiebreak.py`'s `round(composite, constants_v2.COMPOSITE_DECIMALS)`
  were rewritten as `round(composite, 2)`, the behaviour is identical today
  (both round to 2), the hash is untouched (correctly — the *value* did not
  move), and no test distinguishes "reads the registry" from "hardcodes the
  registry's current value" (round-1 review's mutant M12). The next time
  someone changes `COMPOSITE_DECIMALS` in an RFC, that call site would
  silently stop tracking it. There is no general fix for this short of
  reviewing the actual diff at RFC time (checklist item, not a test):
  an RFC touching a constant should `grep` for every consumer of that name
  and confirm each site still reads it symbolically.
- **A constant retyped to an equal-valued but differently-serialised type
  changes nothing under `==` but may change the canonical JSON.** E.g. a
  tuple constant rewritten as a list is not caught by any test in this
  suite (round-1 review's hash-canonicality check found this); reviewers
  should treat a container-type change on a hashed constant as itself an
  RFC-worthy fact, not a refactor.

## RFC template

```markdown
# RFC-NNNN: <slug>

## Motivation

Why this value is wrong or needs to change. Cite the `D-*` decision id and
section of `02-RANKING-DESIGN.md` or `03-ANTI-MANIPULATION.md` it falls
under.

## Change

| Constant | Before | After |
|---|---|---|
| `<NAME>` (`<path.to.key>` if nested) | `<old value>` | `<new value>` |

`BENCHMARK_VERSION`: `<old>` -> `<new>` (D-VER-1 class: minor / major / patch).

## Evidence

- **Sensitivity** (D-AGG-3): Kendall tau between the canonical ranking and
  the ranking under this change, on the fixture snapshot
  (`tests/fixtures/snapshot_50/`, W2-8b). State tau per pillar; release
  requires tau >= 0.90 for a +-5pp weight perturbation — a value below that
  bar means the ranking is too sensitive to ship, not a value to tune until
  it clears.
- **Anchors** (D-VER-4): confirm every anchor relation still holds after the
  change (`torvalds/linux` off-platform; `gcc-mirror/gcc` gated mirror; the
  chosen archived-repo anchor gated archived; an `awesome-*` list
  non_software; `kubernetes/kubernetes` and `vllm-project/vllm` rated with
  C >= 0.90 and tier >= A; the DCO anchor `torvalds/linux` legal = DCO; any
  repo with >= 1 GitHub newcomer label scores 5.1 > 0).
- **Rank movement**: on the fixture snapshot, how many repos change rank,
  and by how much at the median and p95 (paste the numbers, not an
  impression).

## Reviewer

Who reviewed this RFC and the date. A constants-change PR is not merged
without a named reviewer distinct from the author (03 section 7: "every
ACCEPT is reproduced by a second independent agent").
```
