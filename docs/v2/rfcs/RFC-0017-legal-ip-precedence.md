# RFC-0017: legal-ip-precedence

## Motivation

Parameter 3.1 (`legal_ip`, `docs/v2/02-RANKING-DESIGN.md` section 4) classifies a repository's
contributor IP regime into `NONE` (100), `DCO` (95), `FOUNDATION_CLA` (75), or `CORP_CLA` (35).
On the 2026-10-21 published dataset (5,448 repositories: `NONE = 4,038`, `CORP_CLA = 574`,
`DCO = 152`, `FOUNDATION_CLA = 125`, `Unmeasured = 559`), three defects in the `FOUNDATION_CLA` vs
`DCO` vs `CORP_CLA` classification were identified and measured:

1. **`foundation_owner` preceded measured `DCO` evidence even when no CLA check was present.** In
   `porchlamp.collect.governance.classify_legal_ip`, `if foundation_owner: return "FOUNDATION_CLA"`
   ran at step 2 before `dco_check` and `signoff_share`. A repository under a foundation org that
   runs no CLA bot and uses DCO sign-off was forced to `FOUNDATION_CLA` (75) instead of `DCO` (95).
   Across all 75 repositories in the 2026-10-21 corpus that matched `foundation_owner` (63 `apache`,
   6 `kubernetes`, 6 `*-foundation`), a live 60-commit `Signed-off-by` sweep found 4 Apache
   repositories with `signoff_share >= 0.50` and no CLA check (`apache/hbase` 96.7%,
   `apache/beam` 56.7%, `apache/zeppelin` 55.0%, `apache/arrow` 50.0%).
2. **`owner.lower().endswith("-foundation")` in `scripts/derive.py` matched arbitrary non-foundation
   organisations.** Six repositories in the 2026-10-21 corpus matched the `-foundation` suffix
   (`x402-foundation/x402`, `Farama-Foundation/Gymnasium`, `bitsandbytes-foundation/bitsandbytes`,
   `AI4Finance-Foundation/FinRobot`, `Acode-Foundation/Acode`,
   `FULU-Foundation/OrcaSlicer-bambulab`). None of the six runs a CLA check or DCO check, and all
   six have `signoff_share <= 2/60` (`<= 3.3%`), yet all six were classified `FOUNDATION_CLA` (75)
   instead of `NONE` (100).
3. **Foundation projects using generic CLA check contexts (`license/cla`, `cla-signed`,
   `cla-assistant`) were misclassified as `CORP_CLA` (35).** `FOUNDATION_ORGS` lived unhashed inside
   `scripts/derive.py` (`apache`, `kubernetes`, `cncf`, `linuxfoundation`, `eclipse`,
   `openjs-foundation`, `fdiooss`, `openstack`, `llvm`, `chaoss`) and omitted verified multi-vendor
   501(c)(3)/501(c)(6) foundations whose GitHub orgs run generic CLA status contexts:
   - `SeleniumHQ/selenium` runs `license/cla` (Software Freedom Conservancy, verified in
     `CONTRIBUTING.md`) and scored `CORP_CLA` (35).
   - `trinodb/trino` runs `cla-signed` (Trino Software Foundation, verified in CLA check details)
     and scored `CORP_CLA` (35).
   - Eclipse Foundation's status context `eclipsefdn/eca` was not in `FOUNDATION_CHECK_PATTERNS`.

## Method

We probed live GitHub check runs, status contexts, and 60-commit `Signed-off-by` shares across:

- **34 candidate repositories** (foundation projects and `CORP_CLA` repositories from the
  2026-10-21 dataset, 34 GraphQL points):
  - Verified foundation CLA checks with generic names: `SeleniumHQ/selenium` (`license/cla`, SFC),
    `trinodb/trino` (`cla-signed`, Trino Software Foundation), `python/cpython` (`CLA Signing`,
    PSF), `django/django` (`cla-signed`, DSF), `owasp-modsecurity/ModSecurity` (`cla-assistant`,
    OWASP), `composer/composer` (`continuous-integration/cla`, Nils Adermann / Jordi Boggiano).
  - Why `python` is **not** added to `FOUNDATION_ORGS`: `python/typeshed` has no CLA (`NONE` today,
    0 CLA checks, 0/60 signoffs); adding `python` to `FOUNDATION_ORGS` would regress
    `python/typeshed` from `NONE` (100) to `FOUNDATION_CLA` (75) under the `foundation_owner`
    fallback.
  - Why `openssl` is **not** added: `openssl/openssl` runs `cla-check`, and raw repository files do
    not disambiguate the legal beneficiary without external legal metadata.
- **All 75 `foundation_owner` repositories in the 2026-10-21 dataset** (19 GraphQL points):
  - All 6 `kubernetes/*` repos run `EasyCLA` (stay `FOUNDATION_CLA` via `foundation_cla_check`).
  - 59 of 63 `apache/*` repos have `< 50%` commit sign-off and no per-PR CLA bot (Apache enforces
    ICLA at account creation/commit access rather than via a GitHub status check), so keeping
    `foundation_owner` as a fallback after DCO preserves `FOUNDATION_CLA` on all 59 while allowing
    the 4 DCO-active Apache repos (`hbase`, `beam`, `zeppelin`, `arrow`) to classify `DCO`.

## Change

- **Relocate and curate `FOUNDATION_ORGS` in `porchlamp.core.constants`:**
  `FOUNDATION_ORGS` is now a frozen, hashed constant in `porchlamp.core.constants` containing the
  10 existing foundation orgs plus `seleniumhq` and `trinodb` (12 total: `apache`, `chaoss`,
  `cncf`, `eclipse`, `fdiooss`, `kubernetes`, `linuxfoundation`, `llvm`, `openjs-foundation`,
  `openstack`, `seleniumhq`, `trinodb`).
- **Drop `owner.lower().endswith("-foundation")` from the v2 `derive_governance_fields` call in
  `scripts/derive.py`** (preserving the v1 `_legal_ip` heuristic unchanged).
- **Add `r"\beclipsefdn/eca\b"` to `FOUNDATION_CHECK_PATTERNS`** (and `r"eclipsefdn/eca"` to
  `CLA_CHECK_PATTERNS`).
- **Re-order `classify_legal_ip` in `porchlamp.collect.governance`:**
  1. `foundation_cla_check` (`FOUNDATION_CHECK_PATTERNS`) -> `FOUNDATION_CLA`
  2. `foundation_owner_cla_check` (`CORP_CLA_CHECK_PATTERNS` when `foundation_owner=True`) ->
     `FOUNDATION_CLA`
  3. `corp_cla_check` (`CORP_CLA_CHECK_PATTERNS` when `foundation_owner=False`) -> `CORP_CLA`
  4. `dco_check` (`DCO_CHECK_PATTERN`) -> `DCO`
  5. `signoff_share` (`count / total >= SIGNOFF_THRESHOLD`) -> `DCO`
  6. `foundation_owner` (`foundation_owner=True`, no CLA check and no DCO evidence) ->
     `FOUNDATION_CLA`
  7. `no_evidence` -> `NONE`

| Constant | Before | After |
|---|---|---|
| `FOUNDATION_ORGS` | *(unhashed in `scripts/derive.py`, 10 orgs + `-foundation` suffix)* | `("apache", "chaoss", "cncf", "eclipse", "fdiooss", "kubernetes", "linuxfoundation", "llvm", "openjs-foundation", "openstack", "seleniumhq", "trinodb")` |

`CONSTANTS_HASH` moves:
`85df224143be2f7ee4a94f610b104b22f2c6a82f0ad35ddbb47ef9fb78ece50f` ->
`a48306f15d2c9e7603ca00ae25f34e176fbb1421b270a072f9db645f1eb5e1a3`
(pinned in `tests/core/test_frozen.py`, with `_FROZEN_TABLE` rows for `PROMOTION_MIN_OUTSIDE_DAYS`
and `FOUNDATION_ORGS`).

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (no bump, matching the pre-release convention).

## Evidence

- **Fixture (`tests/fixtures/snapshot_50/`):** bundles are pre-derived; scoring with the old and new
  code produces byte-identical records apart from `ScoreRecord.constants_hash` (`records_hash`:
  `27b0ef6f...` -> `72657255...` in `expected.json` and `tests/snapshot/test_store_cli.py`).
- **Sensitivity (D-AGG-3):** not run; no pillar weight or continuous curve constant changed.
- **Measured flips on the 2026-10-21 corpus + live check/DCO probes:**
  - `FOUNDATION_CLA -> DCO` (4 repos): `apache/hbase` (58/60 signoff), `apache/beam` (34/60),
    `apache/zeppelin` (33/60), `apache/arrow` (30/60).
  - `FOUNDATION_CLA -> NONE` (6 repos): `x402-foundation/x402`, `Farama-Foundation/Gymnasium`,
    `bitsandbytes-foundation/bitsandbytes`, `AI4Finance-Foundation/FinRobot`,
    `Acode-Foundation/Acode`, `FULU-Foundation/OrcaSlicer-bambulab`.
  - `CORP_CLA -> FOUNDATION_CLA` (2 repos): `SeleniumHQ/selenium` (`license/cla`),
    `trinodb/trino` (`cla-signed`).
