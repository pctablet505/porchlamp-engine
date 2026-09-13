# B-3 — server faults reported as client 422s — FIXED (verified)

```
  stored quadrant='Nowhere' -> 500  {"error":{"endpoint":"/v1/cvi/leaderboard","status":500,
                                     "detail":"internal server error"}}
  unknown param_id          -> 500  {"error":{"endpoint":".../evidence","status":500,
                                     "detail":"internal server error"}}
```
Both were 422 with raw pydantic text in round 1; the second now matches the
lead's bridge-item-10 ruling. The detail leaks nothing (W-7 satisfied: a
500 uses the same shape as every other status).

Over-correction guard — genuine caller input is untouched:
```
    bad persona        -> 422  unknown persona 'nonsense'; expected one of [...]
    vanity sort        -> 422  sort must be one of ('rank','cvi','coverage','tier','p_...')
    page_size=0        -> 422  limit must be an integer in 1..1000; got 0
    page_size=1001     -> 422  limit must be an integer in 1..1000; got 1001
    page=0             -> 422  offset must be a non-negative integer; got -20
    weights sum != 1   -> 422  body: Value error, weights must sum to 1.0, got 1.8
    negative weight    -> 422  body: Value error, weights must be non-negative, got -0.5
    unknown repo       -> 404  no repository no/such in snapshot snap-w44
```
Mutant B3 (classification removed) KILLED by
`test_a_stored_out_of_literal_quadrant_is_a_500_not_a_422`.
