# W4-5d handoff — gated drawer: reason sentence first, sections collapsed below (not nothing)

Base: `main` tip `a45b4aa`. Branch `W4-5d`. Worktree `~/Projects/cvi-wt-W4-5d`
(created fresh for this packet — did not previously exist).

## The defect (from the lead's own final browser pass, 2026-09-14)

`docs/v2/00-README.md` row 205: opening the drawer for a gated repo (a-h/
templ, dtolnay/anyhow) showed "the gate sentence and nothing else — 06 §3 C
wanted 'nothing else ABOVE THE FOLD', i.e. the evidence and integrity
sections collapsed below". `views/drawer.js::renderDrawer`'s `kind ===
"gated"` branch returned only the identity header + gate banner — no
`<details>` at all, so a gated repo's real facts (measured/unmeasured
evidence, its own raw_hash/snapshot_id) were never reachable, even by
clicking.

## The fix (`web/v2/views/drawer.js`)

For `kind === "gated"`: identity + gate banner render first (unchanged),
then three `<details>` sections, all collapsed (no `open` attribute) so
the fold still shows only the reason sentence:

1. **"Pillars and evidence"** — the real evidence table (whatever was
   measured; unmeasured parameters with their reasons), fetched exactly as
   for any other kind. New shared helper `renderEvidenceOnlyPillarSection
   (evidenceRows, evidenceError, open)` factors out the body both
   "unrated" (unchanged, still `open`) and "gated" (new, `open=false`) use
   — neither kind has a `p_<pillar>`/`c_<pillar>` score to summarise
   (`RepoDetailResponse` returns `None` for both), so this never calls
   `renderPillarSummary` the way the "rated" branch's own
   `renderPillarSection` does.
2. **"Persona view"** — new `renderPersonaSectionAllGated(row)`: every
   known persona (`copy.PERSONA_NAMES`) listed against the repo's own
   `gate_reason` sentence. A gated repo's `persona_gates`/
   `persona_composites` are both `{}` on the wire (disqualification
   happens before any persona lens is ever applied), so reusing the
   existing `renderPersonaSection` (which iterates `persona_composites`)
   would have rendered zero rows — the dispatch's own wording, "all
   personas gated with the gate reason", needed the full persona list
   iterated from `copy.js`, not from the row.
3. **"Integrity"** — reused `renderIntegritySection(row)` unchanged; a
   gated row's `raw_hash` (synthetic, `scripts/serve_ui_fixtures.py::
   _synthetic_raw_hash`) and `snapshot_id` are both real fields already on
   the wire.

**Composite** (section 2) is **omitted** for gated — no CVI/tier/quadrant
was ever computed. Documented per the dispatch's "pick one, document in
copy.js" instruction: a comment above `copy.DRAWER_SECTION_COMPOSITE`
explains the choice (omit, not a "no composite" placeholder sentence).
**Playbook** (section 6) is also omitted for gated (not part of the
dispatch's three named sections) — an action-recommendation stub has
nothing to recommend for a repository disqualified before scoring.

`findRow`/`classifyKind` unchanged. `app.js::openRepo` was checked (per
the dispatch instruction) and already calls `loadEvidence` unconditionally
for every kind — never skipped for gated; no fix needed there, confirmed
both by reading the code and live on the wire (see below).

## Tests

**`web/v2/tests/unit.test.mjs`** (Node still absent on this box — hand-
traced, AND additionally executed against the real, unmodified module in
a real V8 engine via `import('/views/drawer.js')` in a live Chromium tab,
see below):

- Rewrote the now-stale `"a GATED repo renders ONLY the identity/gate-
  status section"` test (it asserted the exact behaviour this packet
  fixes) into `"a GATED repo renders the gate sentence first, then
  Pillars/Persona/Integrity collapsed, with Composite omitted"`: gate
  sentence appears before the first `<details>`; exactly 3 `<details>`,
  none carrying `open`; Composite and Playbook section headings absent;
  every persona name appears in the Persona view; the gate sentence
  appears exactly `1 + persona_count` times (once in the banner, once per
  persona row); raw_hash/snapshot_id render in Integrity.
- New: `"a GATED repo's Pillars section renders its real evidence rows
  once loaded"` — a state with `evidenceByRepo` populated with the exact
  shape of `tests/fixtures/ui/evidence-gated-org__repo-archived-1.json`'s
  one row (governance/archived_flag/not_applicable) renders the pillar
  heading and the "Not shown: not_applicable" text.
- Strengthened the existing unrated test with an explicit regression
  guard for the `renderEvidenceOnlyPillarSection` extraction: exactly one
  `<details>`, still `open`, no Persona/Integrity/Composite sections —
  proving the shared-helper refactor left "unrated" byte-for-byte
  unchanged.

**`tests/test_serve_ui_fixtures.py`** (new `TestGatedRepoDrawerSections`):
`test_gated_repo_with_a_real_evidence_fixture_is_fully_fetchable` hits
both `GET /v1/cvi/repos/gated-org/repo-archived-1` (validates
`eligible=False`, `gate_reason="archived"`) and its `/evidence` (200,
non-empty, `pillar="governance"`, `unmeasured_reason="not_applicable"`) —
the fixture-server half of "evidence is fetched for gated repos exactly as
for unrated" (`gated-org/repo-archived-1` is 1 of only 3 repos with a real
`evidence-*.json` fixture, deliberately chosen over the other 6 gated
fixture repos so this test exercises real rows, not a 404-tolerated empty
set). `test_every_gated_fixture_repo_validates_with_no_composite_facts`
checks every gated fixture repo (not just this one) has `cvi`/`tier`/
`quadrant`/`persona_composites`/`persona_gates` all null/empty — the
contract-level reason Composite is omitted for `kind === "gated"`.

**Live-browser verification** (Chromium via the Claude Browser tool,
`scripts/serve_ui_fixtures.py --port 8930` — not 8899/8902/8793, all
reserved/in use by other sessions):

- `?repo=gated-org%2Frepo-archived-1`: `#app-drawer-root` has exactly 3
  `<details>`, all `open === false`; summaries exactly `["Pillars and
  evidence", "Persona view", "Integrity"]`; gate banner text "Archived on
  GitHub — no active development to evaluate."; network log shows BOTH
  `GET /v1/cvi/repos/gated-org/repo-archived-1` (200) AND its `/evidence`
  (200) fired by `openRepo` — confirms evidence is not skipped for gated.
  Expanding the Pillars `<details>` (`.open = true`) renders exactly the
  fixture's evidence row: `<h3>Governance</h3>` … `archived_flag` … "Not
  shown: not_applicable". Persona view lists all 5 persona names each
  against the same gate sentence. Integrity shows the real raw_hash
  (truncated to 16 chars + "…") and snapshot_id. No `Composite` summary
  present anywhere in the drawer.
- `?repo=unrated-org%2Frepo-insufficient_evidence-0`: unchanged —
  exactly 1 `<details>` (`open === true`), summary "Pillars and
  evidence" only.
- `?repo=riverline%2Fhttpkit` (rated, regression check): unchanged —
  exactly 5 `<details>` (`open === [true, true, false, false, false]`),
  summaries `["Composite", "Pillars and evidence", "Persona view",
  "Integrity", "Playbook"]`.
- Every one of the new/rewritten `unit.test.mjs` assertions above was
  additionally re-run as literal JS against the real, unmodified
  `views/drawer.js`/`copy.js` via dynamic `import()` in that same live
  tab (Node itself still absent) — all pass, matching the technique
  W4-5b's round 3 documented ("this does not make `unit.test.mjs` itself
  executable… it's a verification technique for THIS session").

## Suite, ruff

```
env -u PYTEST_ADDOPTS PYTHONPATH=$PWD /home/pctablet505/Projects/contributor-value-index/.venv/bin/python -B -m pytest tests -p no:cacheprovider -n 4
```
→ **2611 passed, 1 skipped** (main's own count was 2609 passed, 1 skipped
— +2 net from the new `TestGatedRepoDrawerSections` class; the two
rewritten/added `unit.test.mjs` cases don't add to this count, Node
absent). `ruff check .` — clean, whole repo.

## Rules followed

`PYTHONPATH=$PWD` for every Python command. Never `git stash`/`git
checkout --`. No network, no CDN. Live-verification server ran on port
8930 (not 8899/8902/8793 — all three seen occupied by other sessions at
dispatch time, confirmed via `ss -ltnp` before choosing a port), stopped
after verification. Did not touch `main`'s working tree. Did not add a
lead-log row to `docs/v2/00-README.md` — every existing row there is
committed by a dedicated "docs(v2): lead log" commit (`git log --
docs/v2/00-README.md` confirms this is exclusively the lead's own commit
pattern, never a packet implementer's), so that row is the lead's to add
on review, not mine.

## Not touched / out of scope

- "Unrated" branch: dispatch says "an unrated repo unchanged" — its
  content and rendering are byte-identical (proven by the new regression
  test above); only the shared helper it now calls through
  (`renderEvidenceOnlyPillarSection`) was extracted, with the extraction
  verified to produce the exact same concatenated string as the old
  inline code.
- `findRow`, `classifyKind`, `app.js::openRepo`/`loadEvidence`/
  `loadRepoDetail` — read and confirmed correct as-is, no changes needed.
- No new colour tokens, no `tests/test_ui_contrast.py` changes needed —
  every new/reused element uses existing classes (`evidence-table`,
  `cell-missing`, `cell-numeric`, `gate-banner`).

## Rebase / final state

Base equals `main` tip `a45b4aa` exactly (packet dispatched at that base;
no other commits landed on `main` during this session, so no rebase was
needed before committing).
