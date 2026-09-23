# R2-03 Contract vs store rows, with `balanced_rank`/`rank_delta`

The two new fields are well-formed: `int | None`, defaulting to `None`, both-or-neither, and
`rank_delta == balanced_rank - rank` enforced by a validator.

**BLOCKING SIGN MISMATCH with W4-4.** The contract's `rank` is the row's rank under the ACTIVE lens
(the store's `rank_p`) and `balanced_rank` is the stored balanced `rank`, so the contract requires

    rank_delta = balanced_rank - rank   ==   store.rank - store.rank_p

The coordinator reports W4-4 is projecting `rank_p - rank`, which is the **negation**. Every row with
a non-zero delta would fail `_balanced_rank_and_delta_are_consistent` (a 422, not a silent flip):
fixture row `rank=3, balanced_rank=7` requires `+4`; W4-4's expression yields `-4`. The UI sign
convention agrees with the contract (`table.js:114-117`: positive ⇒ ▲ ⇒ ranks better under the lens),
so **W4-4 must subtract in the other order**, not the contract change its validator.

Rest of the round-1 bridge table is unchanged and still owed by W4-4:
persona projection of `rank_p/porchlamp_p/coverage_p/tier_p/tie_group_p` into the flat fields;
`'unrated: no_external_workflow'` arriving in `gate_reason`; `off_platform_routed` having no producer;
`list_not_ranked`'s flat `reason` needing to be split back into the three fields;
bare-vs-`p_`-prefixed sort field names; page→offset and the page_size membership rule (still absent
from the model — `leaderboard.json` itself validates with `page_size: 73`);
`repo_evidence.pillar` nullable in the store but a non-null Literal here; `matrix` caption inputs;
`raw_hash`'s placement. Persona-gate reason codes are now settled on the engine's vocabulary.
