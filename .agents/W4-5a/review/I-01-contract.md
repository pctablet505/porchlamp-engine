# I-01 Contract (`porchlamp/serve/contract.py`) — PASS with gaps
Reviewed at tip 026bb32. 471 lines, no I/O (verified: imports are `typing` + `pydantic` only; no `porchlamp.store`/`porchlamp.core`/`os`/`open`/`requests`).

06 §4 field list vs the model: every field present. `rank, tie_group, owner, name, ecosystem, porchlamp, coverage, tier, tier_ceiling, quadrant, leverage, friction, leverage_coverage, friction_coverage, p_*/c_* (5+5), persona_gates, persona_composites, suspicion, stars_audited, snapshot_id, benchmark_version` — all present; `raw_hash` added (declared as an addition in the module docstring, justified by 06 §3 C item 5).

Eleven W4-3 mismatches — all eleven applied:
1 tie_group `int|None`, equality-only semantics documented (l.123-127) — OK
2 stars_audited `int|None` (l.156-158) — OK
3 counts.total (l.272) with a sum validator — OK
4 persona_gate_reason + "at least one of three" (l.206-216) — OK
5 `'unrated: insufficient_evidence'` accepted (l.81-83) — OK
6 GateReason 'fork' (l.72) — OK
7 unmeasured_reason 'no_population' (l.245) — OK
8 EvidenceRow.measured authoritative (l.234, validator l.249-261) — OK
9 MatrixPoint/MatrixResponse (l.382-410) — OK
10 ReprojectRequest/ReprojectResponse, non-negative weights summing to 1 (l.424-439) — OK
11 page/page_size on LeaderboardResponse (l.335-336) — OK

GAPS (see report Blocker B-6 / bridge table):
- No `rank_delta` / balanced projection fields, so 06 §3 A's persona-delta column and §8 item 4 cannot be satisfied; `extra="forbid"` means an API cannot add them without a contract change.
- `page_size` is bounded by `max(LEADERBOARD_PAGE_SIZES)` but not constrained to membership; `leaderboard.json` itself carries `page_size: 73` and validates.
- `GateReason` omits the engine's stage-6 `'unrated: no_external_workflow'` (`porchlamp/core/gates.py:55`), which the store writes into `gate_reason`.
- `off_platform_routed` has no producer (`porchlamp/core/gates.py:59` writes `routed='off_platform'` on an *eligible* repo).
