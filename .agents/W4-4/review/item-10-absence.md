# Item 10 — absence table vs the dispatch (lead log row "W4-4, W4-5b dispatched")

| Dispatch item | Present | Note |
|---|---|---|
| `porchlamp/serve/app.py` factory with the `/v1/porchlamp` router | yes | `create_app(db_path, *, web_dir)` |
| leaderboard, not-ranked, matrix, repos/{o}/{n}, evidence, snapshot, methodology, reproject | yes (8/8) | all 200 on real fixture data |
| every response through the contract models | yes | `response_model=` on every route; no `**record` passthrough |
| field-by-field bridge from the store rows | yes | 11 of 12 bridge items applied (item 11 deferred, item 12 half — both self-flagged) |
| consistent `{"error": {endpoint, status, detail}}` | yes for 404/422/405/503 | not for 500 (W-7); misclassifies server faults (B-3) |
| page/page_size ↔ limit/offset (max 1000) | yes | both endpoints |
| served-DB guard reused from serve.py | yes literally | no protective effect on a v2 store (B-4) |
| v2 static at `/`, v1 at `/legacy` | yes | `/legacy` only for a v1-shaped DB; SPA fallback over-reaches (B-2) |
| the W1-10 `stars_audited` writer line | yes | round-trips; mutant M9 killed |

Nothing from the dispatch is absent. The three COULD-NOT-VERIFY notes in the
handoff (`-` sort inert, no v2-store CLI, `/not-ranked` unpaged) are all
confirmed accurate and are carried as follow-ups W-3, the item-9 CLI, and N-4.
