# RFC-0005: cla-reciprocity-vocabulary

## Motivation

> **Correction, 2026-09-17, same day.** The first version of this RFC stated
> that row 3.1 was unmeasured because of a key name — that
> `porchlamp/derive/bundle.py` read a bare `legal_ip` while `scripts/derive.py` wrote
> only `legal_ip_model`. **That mechanism was wrong and I published it before
> finishing the trace.** `scripts/derive.py:617-634` *does* write a bare
> `legal_ip` under the correct v2 name, via
> `porchlamp.collect.governance.derive_governance_fields`, whose field names match
> `PARAM_INPUT_IDS` exactly. The measurements below are unchanged and
> reproduced; the cause is not a name, it is a collector that has never run.
> The vocabulary half of this RFC (`A-01`) stands on its own and is what the
> RFC now decides.

Two facts, both measured at `cd8df60`.

**Fact 1 — one collector gates 29 % of the evidence budget.** Row 3.1
(`legal_ip`), row 3.3 (`promotion_ladder`) and row 4.2 (`ai_posture`) all come
from `derive_governance_fields`, which returns `"missing"` for every one of
them when the `governance` envelope is absent:

```
$ python -c "from porchlamp.collect import governance as g; print(g.derive_governance_fields(None, None, None, False))"
  legal_ip          -> value=NONE    conf=missing
  promotion_ladder  -> value=AD_HOC  conf=missing
  ai_posture        -> value=AD_HOC  conf=missing
```

and the envelope is absent for the entire universe:

```
$ ls data/raw/governance | wc -l
0
```

What that costs, computed from the frozen constants:

| Row | Share of its pillar | Pillar weight | Composite coverage |
| --- | --- | --- | --- |
| `legal_ip` + `promotion_ladder` | 0.65 of governance | 0.20 | 0.1300 |
| `ai_posture` | 0.30 of rigor | 0.15 | 0.0450 |
| **total gated on one collector** | | | **0.1750** |

Against `EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE = 0.60`, that is **29.2 % of the
evidence a repository needs to be rated at all**, and it leaves the governance
pillar measured at 35 % (`elephant_factor` alone). A census of all 18 inputs
over 120 real bundles found 10 never measured; `governance` alone explains
three of them, and is the largest single unlock available from data we can
fetch today. `07-BFS-COLLECTION-TASK.md` scheduled `governance` in **L3**,
third of four layers — a planning error, corrected in the same commit as this
RFC: it moves to L1 beside `pr_window`.

**Fact 2 — the vocabulary is defined twice, with different answers.** This is
the audit's `A-01`/`A-02`, and it is what this RFC decides, because it blocks
the collector work: once `governance` runs, row 3.1 starts emitting real
regimes, and v1 and v2 disagree about what they are worth.

| Module | Values | Scores |
|---|---|---|
| `porchlamp/constants.py` (v1, unhashed) | `DCO`, `FOUNDATION_CLA`, `CORP_CLA_RECIPROCAL`, `CORP_CLA_UNILATERAL`, `NONE` | 100 / 75 / **40** / **10** / 85 |
| `porchlamp/core/constants.py` (v2, hashed) | `DCO`, `FOUNDATION_CLA`, `CORP_CLA`, `NONE` | 100 / 75 / **25** / 85 |
| `porchlamp/models.py::LegalIPModel` (the enum the v1 deriver emits) | includes both corporate spellings | — |

`porchlamp/pillars/governance.py`'s docstring publishes the v1 table (40/10) and
`porchlamp/playbook.py:235,251` still branches on the two values to give different
advice, so a row carrying `CORP_CLA_RECIPROCAL` into v2 today is either
rejected by the identity vocab or silently re-scored 40 -> 25, and
`CORP_CLA_UNILATERAL` 10 -> 25.

**A third item, recorded but not decided here.** `scripts/derive.py:486-497`
computes `ai_posture` and `promotion_ladder` from the cached policy-file bodies
by regex, and the governance block at `:634` then overwrites both — including
when it has no evidence at all and writes `"missing"`. Preferring real evidence
over a heuristic is right; discarding the heuristic when there *is* no evidence
is strictly worse than keeping it as a declared `proxy:` method under D-MISS-1.
That is a separate scoring change with its own rank movement and belongs in its
own RFC, not folded into this one.

## Change

**Keep the reciprocity distinction; do not collapse it.** The two regimes are
not the same fact about a project, and the difference is the one
`03-ANTI-MANIPULATION.md` cares about: a *unilateral* CLA assigns the
contributor's work to a single company that may relicense it, which is the
relicensing hazard row 3.1 exists to price; a *reciprocal* CLA does not. A
single `CORP_CLA = 25` prices a Linux-Foundation-style reciprocal CLA and a
company-owned unilateral CLA identically, and it makes the published score
non-monotone in the thing 3.1 measures.

| Constant | Before | After |
|---|---|---|
| `LEGAL_IP_SCORES` | `{"DCO": 100.0, "NONE": 85.0, "FOUNDATION_CLA": 75.0, "CORP_CLA": 25.0}` | `{"DCO": 100.0, "NONE": 85.0, "FOUNDATION_CLA": 75.0, "CORP_CLA_RECIPROCAL": 40.0, "CORP_CLA_UNILATERAL": 10.0}` |
| `LEGAL_IP_VALUES` | `("DCO", "FOUNDATION_CLA", "CORP_CLA", "NONE")` | `("DCO", "FOUNDATION_CLA", "CORP_CLA_RECIPROCAL", "CORP_CLA_UNILATERAL", "NONE")` |

The two scores are not new numbers: 40.0 and 10.0 are the values v1 has
carried since the genesis freeze and that three published documents describe.
This RFC restores them into the hashed table and **deletes `CORP_CLA`**, so
one vocabulary exists rather than two. `porchlamp/pillars/governance.py`'s docstring
becomes accurate rather than contradicted.

Wiring, outside the constants (no RFC needed, listed for completeness):

1. `porchlamp/derive/bundle.py` row 3.1 reads **`legal_ip_model`**, the field that
   is actually written, with an identity vocab over the new
   `LEGAL_IP_VALUES`. The bare `legal_ip` name is retired; a bundle carrying
   only the default-filled `legal_ip` stays `Unmeasured`, as it should.
2. Because the vocab is now an identity over the enum `derive.py` emits, no
   translation table exists to drift. That is the point: one definition per
   fact (R-2).

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (no bump), per the
RFC-0003 precedent — the consolidated bump for the whole amendment set belongs
to W5-2, not to a per-packet change against a still-`rc1` line.

## Evidence

**Rank movement is expected and material**, unlike RFC-0001..0004 — but it
arrives only once the `governance` collector has run. On today's corpus row 3.1
is `Unmeasured` for every repo, so landing this RFC alone moves no score; it
changes what the scores become when L1 completes. Both effects must be measured
and reported separately:

- Coverage, from the `governance` gather (not from this RFC): every repo gains
  up to 0.175 of composite coverage across rows 3.1, 3.3 and 4.2. Repos
  sitting between 0.425 and 0.60 become rated for the first time. This is the
  most plausible single contributor to the standing `rated 0` result.
- Score, from this RFC: only repos whose regime is corporate are affected —
  `CORP_CLA_RECIPROCAL` scores 40.0 rather than 25.0, `CORP_CLA_UNILATERAL`
  10.0 rather than 25.0. The v1 body-regex deriver put 3.5 % of a 200-repo
  sample in `CORP_CLA_UNILATERAL`, which is an indication of the order of
  magnitude, not a prediction: the governance collector classifies from
  different evidence.
- Ceilings: a repo whose only coverage gap was 3.1 can cross
  `CEILING_A_MIN_COVERAGE` (0.80) or `NO_CEILING_MIN_COVERAGE` (0.95).

Required before this lands, and the acceptance criteria for the packet:

1. A fail-before test that a bundle whose governance envelope carries
   `CORP_CLA_RECIPROCAL` resolves row 3.1 as `Measured` with score 40.0 — red
   today, because the value has no v2 vocabulary mapping.
2. A test that `CORP_CLA` is no longer accepted anywhere, so the two
   vocabularies cannot both exist again.
3. `tests/fixtures/snapshot_50` regenerated; the old and new `records_hash`
   and `CONSTANTS_HASH` both printed, and the **rated/gated/unrated counts
   before and after** stated explicitly. A change in the rated count is the
   expected outcome, not a regression — but it must be reported, not absorbed.
4. `tests/core/test_constants_hash.py`'s completeness test still passing, and
   the `CHANGELOG.md` row added with the before -> after hash pair.
5. Anchors and the sensitivity gate re-run on the regenerated fixture
   (`D-VER-4`), with the verdict pasted. Note that the sensitivity CLI returns
   0 unconditionally today — packet `AUD-GATE` fixes that; until it lands,
   read the printed report rather than the exit code.
6. `porchlamp/playbook.py` and `porchlamp/deepdive.py` keep working — they already branch
   on the two values, so they should need no change. Confirm by running their
   tests rather than by reading them.

## Reviewer

Lead (Opus 5), 2026-09-17. Open question deliberately left to the owner: v1's
`NONE` scores 85.0, *above* every CLA regime, on the reasoning that no CLA at
all is safer for a contributor than any corporate CLA. That ordering survives
this RFC unchanged because re-litigating it is a separate design question, but
it is the row most likely to be challenged by a reader of the methodology, and
`A-06` already notes the benchmark spec prints stale gradients elsewhere.
