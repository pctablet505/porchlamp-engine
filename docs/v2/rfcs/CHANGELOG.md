# Constants-change log

One line per RFC that lands (`docs/v2/rfcs/README.md` procedure). Newest
first. Not for the initial constants freeze itself (packet W2-8, no RFC
required — this changelog and the RFC procedure are what govern changes
*after* this packet lands).

| Date | RFC | Summary | Hash before -> after |
|---|---|---|---|
| 2026-09-13 | [RFC-0004](RFC-0004-din3-hygiene-constants.md) | Relocate D-IN-3's `MIN_ACCOUNT_AGE_DAYS = 90` / `MIN_PUBLIC_EVENTS = 5` / `AUTHOR_CAP_FRACTION = 0.20` out of `cvi.collect.github_graphql` into `cvi.core.constants_v2` / `cvi.core.hygiene` (W3-1). No rank movement (no scoring-path consumer yet). | `419091af843a5ab994b23a5776c0f0ce9073ed283b9f540b1d26eca7bcd4d7a9` -> `748be6e459b0d9eac279233dac75acd5ede7da9711590c84fd0317a84762cb6b` |
| 2026-09-13 | [RFC-0003](RFC-0003-not-applicable-rules-and-criticality-proxy.md) | Add `NOT_APPLICABLE_RULES` (D-MISS-5's closed not_applicable rule list), `OPENSSF_PROXY_MIN_SIGNALS = 8` (row 2.2's `proxy:no_dependents_signal` minimum), and relocate `WINDOW_STALENESS_DAYS = 14` from `cvi.collect.pike_signals` (W1-7f follow-up). No rank movement on the fixture (0 rated repos; `records_hash` moves only from the new hashed constants and `PillarScore.applicable`'s structural addition). | `446c6a25db4e8c1f9d528f98a332d52bdf5ba570f07774bd674d9c816fdd09d0` -> `419091af843a5ab994b23a5776c0f0ce9073ed283b9f540b1d26eca7bcd4d7a9` |
| 2026-09-13 | [RFC-0002](RFC-0002-starscout-constants-relocation.md) | Relocate the five `STARS_SCOUT_*` universe-entry thresholds (D-IN-5 star-velocity filter, W1-10) from the unhashed `cvi.constants` into `cvi.core.constants_v2`; values unchanged. No rank movement (D-IN-5: stars never enter the composite). | `d2702ea93a7ad9eb22696942dd3994d63f066ff2bf2e68e52abfc8235433570e` -> `446c6a25db4e8c1f9d528f98a332d52bdf5ba570f07774bd674d9c816fdd09d0` |
| 2026-09-13 | [RFC-0001](RFC-0001-author-unmeasured-max-fraction.md) | Add `AUTHOR_UNMEASURED_MAX_FRACTION = 0.20` (D-MISS-1 collector-failure gate for row 1.2/1.3, W1-4 round-2 review B-2/B-3); relocated out of `cvi.collect.github_graphql`, closing the census escape. No rank movement (no scoring-path consumer yet). | `391d10b91e86a230fa8c16e32f57f2bc5d0449c33dc3bcb6c3f96153ef83fed8` -> `d2702ea93a7ad9eb22696942dd3994d63f066ff2bf2e68e52abfc8235433570e` |
