# Item 3 — Reprojection parity, reviewer's own oracle (L3) — PASS; 1 Blocker

`item3_reproject.py`: 300 rows (~15% of each pillar unmeasured, 1-in-37 gated)
x 25 weight vectors (balanced; one zeroing accessibility; one all-weight on a
single pillar; 22 random). Oracle written inline with D-MISS-2/D-MISS-3/D-MAT-2
HARD-CODED from `02` §7-8 (cutoffs 90/80/70/55/40, bands 0.60/0.80/0.95) --
no import of aggregate/persona/constants_v2 for the cutoffs.

    vectors checked: 25  failures: 0
    tie_group membership equal: True
    sum != 1: ValueError weights must sum to 1.0 within 1e-06; got 1.5
    unknown pillar: ValueError weights must cover exactly (...)
    missing pillar: ValueError weights must cover exactly (...)
    negative weight: NO RAISE  <-- defect

Blocker B-1 (`item3b_edges.py`):
    sum(w) = 1.0 for {responsiveness:-0.5, criticality:1.5, rest 0}
    edge/neg -> cvi=147.5  coverage=1.0  tier=S+  rank=1

Warning W-2 (same script): SQL ROUND vs Python round
    edge/half-a: returned cvi=82.12  tie_group=2  rank=2
    edge/half-b: returned cvi=82.13  tie_group=2  rank=3
    python round(82.125,2) = 82.12 ; sqlite ROUND(82.125,2) = 82.13
