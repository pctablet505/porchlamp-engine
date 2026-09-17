# RFC-0005: legal-ip-wiring-and-cla-reciprocity

## Motivation

Row 3.1 (`legal_ip`, `PARAM_WEIGHTS["legal_ip"] = 0.35`, the heaviest single
parameter in the governance pillar) is **`Unmeasured` for every repository in
the real corpus**, while the classification it needs is already computed and
sitting in the same file under a different key.

Measured by the lead on 2026-09-17 against `data/derived/` (the live corpus
built by the collection agent) at `96922c1`:

```
$ python -c "... load_bundle(p, raw_root='data/raw') for 60 real bundles ..."
bundles loaded: 60
legal_ip state: {'Unmeasured': 60}
detail: {'reason=collector_failure': 60}
```

and over a 200-file sample of the same corpus:

```
key presence: {'legal_ip': 200, 'legal_ip_model': 200}
values: {('legal_ip', 'NONE'): 200,
         ('legal_ip_model', 'DCO'): 188,
         ('legal_ip_model', 'CORP_CLA_UNILATERAL'): 7,
         ('legal_ip_model', 'FOUNDATION_CLA'): 5}
confidence values: {('legal_ip', 'missing'): 199, ('legal_ip', None): 1,
                    ('legal_ip_model', 'derived'): 200}
```

The cause is a wiring gap, not a collector gap:

- `scripts/derive.py:486` writes the classification to **`legal_ip_model`**
  with confidence `"derived"` (`_legal_ip(contrib_body, license_spdx, owner)`).
- `cvi/derive/bundle.py:1983` maps row 3.1 from a **bare `legal_ip`** field:
  `"legal_ip": _vocab_scalar_resolver("legal_ip", {v: v for v in constants.LEGAL_IP_VALUES})`.
- No code writes a bare `legal_ip`. The `"NONE"` seen above is the v1
  bundle's **default-fill**, stamped confidence `"missing"`, so the resolver
  correctly refuses it — and row 3.1 is `Unmeasured(collector_failure)`
  universally.

Cost, computed from the frozen constants: `legal_ip` is 0.35 of a governance
pillar whose three parameters sum to 1.0, and governance is
`PILLAR_WEIGHTS["governance"] = 0.20` of the composite. So **every repository
loses 0.07 of composite coverage unconditionally**, against
`EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE = 0.60`. That is 11.7 % of the entire
evidence budget a repo needs to be rated at all, thrown away on a key name.

The audit (`AUDIT-2026-09-17-DEEP-REVIEW.md`, `A-01`/`A-02`) found the
neighbouring defect — that the regime vocabulary is defined twice with
different answers — but not this one. Both must be fixed together, because
wiring `legal_ip_model` into row 3.1 requires deciding what its two
corporate-CLA values mean in v2, which is exactly `A-01`.

The vocabularies today:

| Module | Values | Scores |
|---|---|---|
| `cvi/constants.py` (v1, unhashed) | `DCO`, `FOUNDATION_CLA`, `CORP_CLA_RECIPROCAL`, `CORP_CLA_UNILATERAL`, `NONE` | 100 / 75 / **40** / **10** / 85 |
| `cvi/core/constants.py` (v2, hashed) | `DCO`, `FOUNDATION_CLA`, `CORP_CLA`, `NONE` | 100 / 75 / **25** / 85 |
| `cvi/models.py::LegalIPModel` (the enum derive actually emits) | includes `CORP_CLA_RECIPROCAL`, `CORP_CLA_UNILATERAL` | — |

`cvi/pillars/governance.py`'s docstring still publishes the v1 table (40/10),
and `cvi/playbook.py:235,251` still branches on the two values to give
different advice. So a v1 row carrying `CORP_CLA_RECIPROCAL` entering v2 today
is either rejected by the identity vocab (`ValueError`) or, under any naive
mapping, silently re-scored 40 -> 25, and `CORP_CLA_UNILATERAL` 10 -> 25.

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
one vocabulary exists rather than two. `cvi/pillars/governance.py`'s docstring
becomes accurate rather than contradicted.

Wiring, outside the constants (no RFC needed, listed for completeness):

1. `cvi/derive/bundle.py` row 3.1 reads **`legal_ip_model`**, the field that
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

**Rank movement is expected and material**, unlike RFC-0001..0004. This is the
first constants change in this lineage that moves published scores, because it
takes row 3.1 from universally unmeasured to measured for ~100 % of the corpus:

- Coverage: every repo gains 0.07 of composite coverage. Repos previously
  sitting between 0.53 and 0.60 become rated for the first time. This is the
  most plausible single contributor to the standing `rated 0` result on the
  50-repo fixture and on every real build to date.
- Score: on the 200-file sample, 94 % of repos are `DCO` -> 100.0 on row 3.1;
  2.5 % `FOUNDATION_CLA` -> 75.0; 3.5 % `CORP_CLA_UNILATERAL` -> 10.0.
- Ceilings: a repo whose only coverage gap was 3.1 can cross
  `CEILING_A_MIN_COVERAGE` (0.80) or `NO_CEILING_MIN_COVERAGE` (0.95).

Required before this lands, and the acceptance criteria for the packet:

1. A fail-before test that a real bundle from `data/derived/` resolves row 3.1
   as `Measured`, red at `96922c1`.
2. The 60-bundle measurement above re-run and pasted: `Measured` count must be
   60 of 60, with the value distribution matching `legal_ip_model`'s.
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
6. `cvi/playbook.py` and `cvi/deepdive.py` keep working — they already branch
   on the two values, so they should need no change. Confirm by running their
   tests rather than by reading them.

## Reviewer

Lead (Opus 5), 2026-09-17. Open question deliberately left to the owner: v1's
`NONE` scores 85.0, *above* every CLA regime, on the reasoning that no CLA at
all is safer for a contributor than any corporate CLA. That ordering survives
this RFC unchanged because re-litigating it is a separate design question, but
it is the row most likely to be challenged by a reader of the methodology, and
`A-06` already notes the benchmark spec prints stale gradients elsewhere.
