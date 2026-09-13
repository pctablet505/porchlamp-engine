# I-03 Fixture server — PASS with one Blocker
Probed live on my own port 8912 (`scratch/W4-5a-review/probe.py`, urllib only, zero external requests).
- page_size: 20/50/100/500/1000 -> 200; 30, 999, 1001, 0, -5, 2000 -> 400 REJECTED (not clamped); non-integer -> 400. Default 20 confirmed.
  vs store: `cvi/store/queries.py:143 MAX_LIMIT = 1000` and `_validate_paging` accepts ANY int in 1..1000 — the server is strictly narrower (bridge item).
- page: 0/-1 -> 400; 9999 -> 200 with 0 rows (renders the "empty" shell + the wrong empty copy).
- sort: allow-list enforced; `stars_audited`, `forks`, `downloads`, `owner`, `raw_hash` all -> 400. U-3 holds.
  vs store: `ALLOWED_SORT_FIELDS` uses BARE pillar names (`responsiveness`); this server uses `p_responsiveness`. Both reject the other's spelling (bridge item).
- q: case-insensitive on the query side (RIVERLINE/riverline/RiVeRlInE all -> 1 row).
- eco: case-insensitive match, but echoes the raw input back as `ecosystem` ('PyPI'); the store does an exact match.
- persona: an unknown persona (`bogus_persona`) returns 200 + the balanced list; `queries.list_ranked` raises ValueError (bridge item).
- evidence: unknown repo -> 404 OK. BUT `_parse_evidence_filename` mid-splits on '-': `evidence-ivywood-template-cli-71.json` is served at `ivywood-template/cli-71`, so the real row `ivywood/template-cli-71` 404s (report B-7).
- snapshot, healthz, static, path traversal, unknown /v1/* -> all correct.
- `/v1/cvi/unrated`, `/matrix`, `/reproject` (04 §7) are not served — out of this packet's scope, noted for W4-4.
