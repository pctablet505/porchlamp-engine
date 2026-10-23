# RFC-0023: universe-entry-languages

## Motivation

D-UNI-2 (`docs/v2/02-RANKING-DESIGN.md` section 13) makes the universe the union of
"top-N by audited stars per language", registry top-lists and anchors, each
candidate recording its `selected_by` criteria. `scripts/build_universe.py`
searched eight GitHub Linguist languages (Python, JavaScript, TypeScript, Rust,
Go, Java, C, C++). GitHub's `language:` qualifier matches a repository's primary
language only, so a project whose primary language is anything else never
reaches a star criterion, however many stars it has (issue #66, section 3.4).

Checked against the published universe (11,000 repos in both
`porchlamp/data/2026-10-17/` and `.../2026-10-21/`) and one live GraphQL query
(1 point, 2026-10-22) for the six repos #66 names:

| #66 names | In the universe? | Primary language (live) | Why |
|---|---|---|---|
| `facebook/react` | yes, as `react/react` (npm) | JavaScript | **#66 is wrong here**: the repo moved to the `react` org; gated `black_hole` on 2026-10-21 (outside this RFC) |
| `facebook/react-native` | yes, as `react/react-native` (systems) | C++ | same: renamed, gated `black_hole` on 2026-10-21 |
| `apache/spark` | no | Scala | language not searched |
| `neovim/neovim` | no | Vim Script | language not searched |
| `llvm/llvm-project` | no | LLVM (#66 said CMake) | language not searched |
| `ziglang/zig` | no | Zig | language not searched; also last pushed 2025-11-27 (development moved off GitHub), so D-GATE-4 would gate it `stale` |

The same filter leaves out `JetBrains/kotlin` and `square/okhttp` (Kotlin),
`gradle/gradle` (Groovy), `ghostty-org/ghostty` and `tigerbeetle/tigerbeetle`
(Zig) and `vim/vim` (Vim Script): none is in the 2026-10-21 universe.

The fix cannot be "search every language". The repo's ecosystem is fixed at
universe entry from the language that selected it and is a closed vocabulary
(`porchlamp.models.Ecosystem`: npm, pypi, crates, go, maven, systems). It decides
the ECDF population (D-NORM-2) and the D-MISS-5 rules: `systems` means "no
package registry", so 2.1 dependents and 2.3 downloads are `not_applicable`.
Filing a Ruby, PHP or C# project under `systems` would claim that RubyGems,
Packagist or NuGet do not exist.

## Change

1. **Languages, by the ecosystem they belong to.** A language is searched when
   its projects publish to a registry the benchmark models, or have no package
   registry at all:
   - `maven`: Kotlin, Scala and Groovy publish to Maven Central, like Java.
   - `systems`: Zig, Vim Script and LLVM have no package registry, like C and C++;
     D-MISS-5 already scores that class.
   - Left out until their registry is modeled (Phase 2, owner decision below):
     C# (NuGet), Ruby (RubyGems), PHP (Packagist), Dart (pub.dev), Swift (SwiftPM,
     CocoaPods), Lua (LuaRocks), Clojure (Clojars, not Maven Central).
   Every language faces the one star floor (`effective_floor`) and the one
   `--max-total` cap; nothing about scoring changes.
2. **A registry list's number is not a star count** (bug fix, commit `45dd60c`,
   listed here because it changes what the manifest records). `crates_top`
   reported the crate's all-time downloads as `stars`, and `build()` wrote them
   into `stars_at_selection`. A registry-only row now records
   `downloads_at_selection` and `stars_at_selection: null`; under D-UNI-2's union
   it takes its place on its own criterion (most downloads first) and the star
   cap orders only star-search rows. A carried row created that way is repaired
   on the next build.
3. **No language-independent top-N criterion.** Every hit still needs an
   ecosystem: in a searched language its own query already finds it at the same
   floor, and in an unmodeled language it has no honest ecosystem.

| Constant | Before | After |
|---|---|---|
| `SEARCH_CRITERIA["maven"]` (`scripts/build_universe.py`) | `["language:Java"]` | `["language:Java", "language:Kotlin", "language:Scala", "language:Groovy"]` |
| `SEARCH_CRITERIA["systems"]` (`scripts/build_universe.py`) | `["language:C", "language:C++"]` | `["language:C", "language:C++", "language:Zig", 'language:"Vim Script"', "language:LLVM"]` |
| `stars_at_selection` of a `crates-downloads`-only row | the crate's downloads | `null`; the downloads go to `downloads_at_selection` |

Process class (`docs/v2/rfcs/README.md`, "Process constants"): `SEARCH_CRITERIA`
decides which repositories are collected, not how any of them is scored, so it is
not in `constants_dict()`. `CONSTANTS_HASH` is unchanged:
`4a6529ef5ca3c97c443442774440b4947ebc849265132fc16a61e3a60f231a2a` ->
`4a6529ef5ca3c97c443442774440b4947ebc849265132fc16a61e3a60f231a2a`;
`tests/core/test_frozen.py` needs no change.

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (RFC-0001..0013 precedent; no
constant gains a scored consumer).

**Universe size.** The builder carries every current repo and fills only
`--max-total` minus the carried count with new ones, so at the default
`--max-total 11000` an 11,000-repo universe admits nothing new. The new
languages enter only when the run raises `--max-total` by the number they bring
(measured below: at most 220 at the current floor). `docs/v2/10-SCALING-PLAN.md`
(locked 2026-10-14) says "stay at ~10k"; the universe is already 11,000. Raising
the cap is the owner's decision; this RFC does not change the default.

## Evidence

- **Dry run** (live, 2026-10-22, 20 search calls; `total_count` of
  `language:X stars:>=4234 archived:false fork:false mirror:false
  pushed:>=2026-04-25`; 4,234 is the lowest star count among the 5,247 repos the
  11,000-repo build added, commit `b0ba99a`):

  | Language | Ecosystem | Eligible at the floor | Top repos |
  |---|---|---|---|
  | Kotlin | maven | 168 | 2dust/v2rayNG, topjohnwu/Magisk, JetBrains/kotlin |
  | Scala | maven | 22 | apache/spark, lichess-org/lila, scala/scala |
  | Groovy | maven | COULD NOT VERIFY (search answered `incomplete_results`); without the eligibility qualifiers 5, one of them, apache/groovy, already in the universe | gradle/gradle |
  | Zig | systems | 10 | ghostty-org/ghostty, lightpanda-io/browser, tigerbeetle/tigerbeetle |
  | Vim Script | systems | 19 | neovim/neovim, vim/vim, junegunn/vim-plug |
  | LLVM | systems | 1 | llvm/llvm-project |
  | **Phase 1 total** | | **220** (+ up to 4 Groovy) | the top five of every Phase 1 language are absent from the 2026-10-21 universe |
  | C# / Ruby / PHP / Dart / Swift (left out) | none modeled | 297 / 135 / 247 / 61 / 167 | v2rayN, rails/rails, laravel/framework, flutter/flutter, swiftlang/swift |

  Kotlin without the eligibility qualifiers answered 0 against 168 with them, a
  contradiction, so a search answer can be partial without saying so; Groovy and
  Lua with the qualifiers answered `incomplete_results`. Those are COULD NOT
  VERIFY rather than counts. (Clojure, left out, answered 0 with the qualifiers
  and 16 without.) COULD NOT VERIFY how many of the 220
  are already in the universe beyond the top five per language (that needs every
  page of every slice, more calls than this RFC's 20-call budget), so 220 is an
  upper bound on the repos added.
- **Cost of at most 220 more repos**: 2.0 % of the 11,000-repo universe. At the
  per-repo cost `docs/v2/10-SCALING-PLAN.md` section 1 documents (about 14
  GraphQL points and 20 REST calls per changed repo), a full refresh of the added
  repos is at most 3,080 points and 4,400 REST calls: computed from the
  documented cost, not measured. The phone receives the list through
  `scripts/pull_from_phone.py --push-candidates` (8 MiB cap, `CANDIDATES_CAP`);
  COULD NOT VERIFY the size of the live `candidates.jsonl` (not on this machine).
- **Fixture snapshot** (`tests/fixtures/snapshot_50/`): rank movement 0 of 50,
  anchors and sensitivity unchanged, by construction: the fixture's bundles are
  fixed and the universe builder is not part of its pipeline (the fixture step
  passes unchanged, `17 passed, 2 skipped`).
- **Real data**: the added repos join the `maven` and `systems` ECDF populations,
  which moves the percentile of 2.1 and 1.5 for existing repos of those two
  ecosystems. COULD NOT VERIFY the size of that shift: it needs the full-universe
  rebuild, which is not possible on this machine.
- **Tests** (`tests/test_build_universe.py`): the criteria are pinned; spark,
  neovim, llvm-project and JetBrains/kotlin enter a dry run as `maven`/`systems`
  with their `selected_by` criterion and per-language summary; a Kotlin repo
  competes with Java repos under the same floor and cap. The registry-row fix has
  its own six tests (commit `45dd60c`).

## Owner decisions this RFC needs

1. Raise `--max-total` to admit the Phase 1 languages (at most 220 measured, +2.0 %), or
   keep 11,000 and let them wait.
2. Phase 2: model NuGet, RubyGems, Packagist, pub.dev and Swift as ecosystems
   (the `Ecosystem` enum, registry mappings in `porchlamp.collect.depsdev` and
   `.ecosystemes`, downloads sources, D-MISS-5 rules, ECDF populations and the
   site's ecosystem pages). That is what brings flutter/flutter, rails/rails,
   laravel/framework and swiftlang/swift in; 907 repos clear today's floor.
3. The `systems` pages read "C/C++" on the site; with Zig, Vim Script and LLVM
   in that ecosystem the label needs a wider name.

## Reviewer

pending: independent review in the #66 PR.
