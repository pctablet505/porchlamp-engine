# RFC-0020: dx-setup-signals-proxy

## Motivation

Parameter 5.4 (`dx`, `docs/v2/02-RANKING-DESIGN.md` section 4) blends `0.5 * runtime_score`
(median CI wait over the newest 30 pull-request commits with a finished GitHub Actions workflow run,
`min_n = 5`) with `0.5 * setup_signals_score` (`0/1/2/3 -> 0/40/70/100` from repository tree
environment files, `CONTRIBUTING.md` setup headings, and `README.md` install/build headings).

On the 2026-10-21 snapshot (`5,448` rated repositories):
- `4,371` (`80.2%`) are `Measured` (median score `70.00`, mean `67.64`).
- `1,071` (`19.7%`) are `Unmeasured(insufficient_n)` — and every single one of those `1,071`
  repositories has `n = 0` finished GitHub Actions pull-request runs (repositories that run CI on
  Buildkite, CircleCI, Jenkins, GitLab CI, Kokoro, Gerrit, or mailing lists, including
  `torvalds/linux`, `golang/go`, `llvm/llvm-project`, `git/git`, `curl/curl`, and `redis/redis`, or
  header/specification/CLI repositories without GitHub Actions PR workflows).
- Because `scripts/derive.py` returned `dx = None` whenever `actions_runs` had `0` usable PR-commit
  waits (and `porchlamp/derive/bundle.py` rejected `n < 5` as `insufficient_n`), the already
  collected setup-signals half of `5.4 DX` (`tree`, `CONTRIBUTING.md`, `README.md`, `governance`)
  was discarded whenever GitHub Actions PR runtime had fewer than `5` commits, docking `19.7%` of
  rated repositories on Accessibility coverage (`0.15 * 0.15 = 2.25%` composite coverage).

## Change

Follow the D-MISS-4 proxy pattern established by `2.2 openssf_criticality`
(`proxy:no_dependents_signal`) and `4.1 pipeline_defenses` (`proxy:review_ci`):

1. **`porchlamp/core/constants.py`:**
   - Add `DX_PROXY_MIN_N: int = 0`.
   - Add `METHOD_PROXY_DX_SETUP_SIGNALS: str = PROXY_METHOD_PREFIX + "setup_signals_only"`
     (`"proxy:setup_signals_only"`).
2. **`porchlamp/core/params/accessibility.py` & `porchlamp/core/methodology.py`:**
   - Set `proxy_min_n = constants.DX_PROXY_MIN_N` (`0`) on `_DX`.
   - Update `_curve_description("dx")` in `porchlamp/core/methodology.py` to state both the primary
     blend and the setup-signals proxy.
3. **`scripts/derive.py` (`_derive_dx_54`, `derive_repo`) & `porchlamp/derive/bundle.py` (`_resolve_dx`):**
   - When `runs is not None` and `len(waits) >= PARAM_MIN_N["dx"]` (`5`): compute
     `0.5 * runtime_score + 0.5 * setup_score` and emit `dx_method = "primary"`.
   - When `runs is not None` and `len(waits) < PARAM_MIN_N["dx"]` (`0 <= n < 5`) and at least one
     setup-signal source (`tree`, `CONTRIBUTING.md`, `README.md`, or `governance`) is present:
     compute `setup_score` (`0.0`, `40.0`, `70.0`, or `100.0` from the 3 setup signals) and emit
     `dx_method = "proxy:setup_signals_only"` with `dx_run_count = len(waits)`. Do not blend a
     sub-threshold (`1 <= n < 5`) runtime wait into the proxy score.
   - When `runs is None` (the `actions_runs` envelope was never collected) or neither `>= 5` CI
     runs nor any setup-signal source is present: `dx` remains `Unmeasured`.

| Constant | Before | After |
|---|---|---|
| `DX_PROXY_MIN_N` | *(absent)* | `0` |
| `METHOD_PROXY_DX_SETUP_SIGNALS` | *(absent)* | `"proxy:setup_signals_only"` |

`CONSTANTS_HASH` moves:
`3bc4aac040af2937e438e39e3e18ffbbfad6ddf7aeea7a1911f6e6013105226d` ->
`93e44701a8a9862e242e0e1f93e7f082848fbc1f222d68bdcd221600192bac8c`
(pinned in `tests/core/test_frozen.py`, with `_FROZEN_TABLE` rows for `DX_PROXY_MIN_N` and
`METHOD_PROXY_DX_SETUP_SIGNALS`).

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (pre-release convention).

## Evidence

- On the 2026-10-21 snapshot (`5,448` rated repositories), all `1,071` repositories previously
  marked `Unmeasured(insufficient_n)` on `5.4 DX` have collected `tree` and `governance`/`readme`
  envelopes, moving `5.4 DX` coverage among rated repositories from `4,371 / 5,448` (`80.2%`) to
  `5,442 / 5,448` (`99.9%`, with only the `6` `collector_failure` repositories remaining
  unmeasured).
- Each converted repository is explicitly tagged `method = "proxy:setup_signals_only"` with its true
  GitHub Actions PR-commit count `n` (`0..4`) preserved on `Measured.n` and `ParamScore.n`.
