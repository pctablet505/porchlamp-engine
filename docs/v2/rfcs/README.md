# Constants-change RFCs

`cvi/core/constants_v2.py` is frozen (D-VER-1/2, `docs/v2/02-RANKING-DESIGN.md`
section 11; `docs/v2/03-ANTI-MANIPULATION.md` section 7). Every value the
ranking computes with — pillar weights, per-parameter weights and minimum
sample sizes, curve-shape constants, coverage/ceiling/gate thresholds,
persona presets and their gate thresholds, `MIN_POPULATION`,
`COMPOSITE_DECIMALS` — lives in that one module, and `CONSTANTS_HASH =
sha256(canonical JSON of constants_dict())` is stamped into every stored
`ScoreRecord` row and the snapshot manifest. `tests/core/test_frozen.py`
pins the hash, the benchmark version and a representative-value table to
literals; changing any covered constant moves the hash and fails that test.

**A PR that changes a value in `constants_v2.py` without following this
procedure is rejected in review.** The failing hash test in
`tests/core/test_frozen.py` is the enforcement mechanism, not a formality —
it is what makes an unreviewed constants change visible as a red test
instead of a silent ranking shift.

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
