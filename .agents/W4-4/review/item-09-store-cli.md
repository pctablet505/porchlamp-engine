# Item 9 — can a scored snapshot be written into a v2 store today? — NO

`cvi.store.writer.write_records` has **zero** non-test callers in `cvi/` or
`scripts/`; `cvi/snapshot/cli.py` exposes only `freeze` and `reproduce`.
Nothing in the shipped tooling ever produces a v2 database.

The chain does work — proven in scratch
(`cvi-scratch/W4-4-review/probe_fixture_to_store.py`):

```
fixture: snapshot_id=snapshot_50 bundles=50
scored: 50 records; counts=SnapshotCounts(rated=0, gated=35, unrated=15, ...)
written: (50, 270, 0)          # repos, params, populations
GET /v1/cvi/leaderboard -> 200   total=0 counts={'rated':0,'gated':35,'unrated':15,'total':50}
  LEADERBOARD IS EMPTY -- the committed fixture scores 0 rated repos
GET /v1/cvi/not-ranked -> 200    gated=31 unrated=19
  gate_reasons=['archived','black_hole','non_software','stale','unlicensed']
  unrated_reasons=['no_external_workflow','off_platform_routed','unrated: insufficient_evidence']
GET /v1/cvi/matrix -> 200 ; /v1/cvi/snapshot -> 200 ; persona leaderboard -> 200
GET evidence -> 200 ; GET detail -> 200
```

Two follow-ups, both required before the lead's browser verification:

1. **`python -m cvi.snapshot store <snapshot dir> --db <path>`** — natural
   home `cvi/snapshot/cli.py`, beside `freeze`/`reproduce`, reusing
   `scripts/build_fixture_snapshot.load_frozen_fixture`'s reader shape. It
   must: apply migrations; `write_snapshot` from the manifest; `write_records`
   with the frozen `PopulationSet`; and **`finalize_snapshot_hash`** — without
   the last step `/leaderboard`, `/not-ranked` and `/snapshot` all 503
   (`"has not been finalised (no canonical_dump_hash yet)"`). Note
   `write_records(..., populations)` wrote 0 population rows for this fixture
   — worth settling in the same change.
2. **A fixture with rated rows.** `snapshot_50` yields 0 rated of 50, so even
   a correct CLI renders an empty leaderboard. The browser verification needs
   a corpus that clears D-EVID-1, or a documented synthetic one.
