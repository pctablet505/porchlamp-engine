# B-4 — no served-DB guard for a v2 store — FIXED (verified)

```
=== a legitimate finalised v2 store is ACCEPTED ===
  v2_refusal_reasons: []          run_guard -> None (no v1 shape to mount)
  /v1/porchlamp/leaderboard -> 200

=== a TAMPERED finalised v2 store is REFUSED ===
  stored 3440e9517831891d…  recomputed 00fb9313d54bb18c…  differ=True
  create_app -> SystemExit:
    Porchlamp serve refused for '…/tampered.db':
    snapshot 'snap-w44''s current content (hash 00fb…) does not match its recorded
    canonical_dump_hash (3440…) -- the database was modified after promotion
```
Round 1 served this store happily with `porchlamp=100.0` on the tampered row.

Coverage beyond the single repositories edit I asked for:
```
  params row edited        -> refused: True
  repositories row deleted -> refused: True
  fixture clock in `repositories`        -> refused: True
  fixture clock in `legacy_repositories` -> refused: True   (post-04 §10 migration)
```
An UNfinalised snapshot is deliberately not a startup refusal
(`v2_refusal_reasons: []`) — it is `_require_snapshot`'s 503, per W-6.

The v1 guard still behaves identically (message byte-for-byte, `tests/
test_serve.py` 15/15 unmodified), and `porchlamp/serve/app.py` no longer imports
`scripts.*` at all.

Mutants B4 (hash check disabled), M7 (run_guard neutered), R4 (v1
SystemExit swallowed) and RY1 (fixture-clock check disabled, mine this
round) all KILLED, each by a distinct test.
