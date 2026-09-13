# NB-1 (lead addendum 1) — counts still disagree ACROSS endpoints — OPEN at ac4b46a

Reviewer's reproduction on the REAL fixture store
(`python -m cvi.snapshot store tests/fixtures/snapshot_50 --db real.db`):

```
pipeline/CLI counts      : rated 0  gated 35  unrated 15
GET /v1/cvi/snapshot     : {'rated': 0, 'gated': 35, 'unrated': 15, 'total': 50}
GET /v1/cvi/leaderboard  : {'rated': 0, 'gated': 35, 'unrated': 15, 'total': 50}
GET /v1/cvi/not-ranked   : {'rated': 0, 'gated': 31, 'unrated': 19, 'total': 50}
                            rows g=31 u=19 ph=0

AGREE snapshot vs not-ranked   : False
AGREE leaderboard vs not-ranked: False
```

Delta = exactly 4 repositories: those whose engine gate reason is
`no_external_workflow`, plus the `off_platform_routed` ones — engine GATES
that `06` §3 A2 classifies as UNRATED.

W-1's fix made `/not-ranked` internally consistent (its counts now match its
own three groups) but left `/leaderboard` and `/snapshot` on
`snapshot_summary`'s raw stage-0 triage, so the same screen's header and its
Not-ranked tab now disagree by 4. Round 1 had the opposite failure (one
source, agreeing with each other, disagreeing with the rows).

The lead's ruling is the right shape: ONE classification helper in
`cvi/core/pipeline.py`, used by the pipeline counts, `snapshot_summary` and
the API split, following `06`. Not present at `ac4b46a`
(`grep -rn "def classify" cvi/core/pipeline.py cvi/serve/app.py` → nothing).

Acceptance for the next commit — all three must print the same triple:
```
python -m cvi.snapshot store tests/fixtures/snapshot_50 --db /tmp/real.db   # prints rated/gated/unrated
GET /v1/cvi/snapshot      .counts
GET /v1/cvi/leaderboard   .counts
GET /v1/cvi/not-ranked    .counts   and  len(gated)/len(unrated)+len(persona_hidden)
```
plus a test that fails if any one of the three is computed independently.
