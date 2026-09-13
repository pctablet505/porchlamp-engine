# Item 5 — isolated TestClient p95 on 10,000 rows (budget 150 ms) — PASS

`cvi-scratch/W4-4-review/probe_perf_tamper.py`, 10,000-row synthetic
snapshot (9,833 rated), 20 calls each after a warm-up, nothing else running:

```
GET  /leaderboard page_size=20       : p95=  35.10 ms  median=  34.37 ms
GET  /leaderboard page_size=1000     : p95=  70.89 ms  median=  57.72 ms
GET  /leaderboard persona ps=20      : p95=  87.05 ms  median=  85.12 ms
GET  /leaderboard persona ps=1000    : p95= 120.58 ms  median= 109.27 ms
POST /reproject   page_size=20       : p95=  59.54 ms  median=  58.35 ms
POST /reproject   page_size=1000     : p95=  95.57 ms  median=  82.28 ms
GET  /not-ranked                     : p95=  34.43 ms  median=  33.62 ms
GET  /matrix                         : p95=  59.83 ms  median=  45.79 ms
GET  /snapshot                       : p95=  31.24 ms  median=  30.53 ms
```

The budget holds in isolation with margin. The handoff's worst case
(reproject @1000, 116–163 ms) did not reproduce: 95.57 ms p95 here. The
slowest path is one the handoff never measured — the **persona** leaderboard
at page_size=1000, 120.58 ms — still inside 150 ms but the real headroom
figure. The single 162.72 ms overshoot is consistent with `-n 4` contention
on this shared host, as the handoff argues.
