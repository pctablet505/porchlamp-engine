# Constants-change log

One line per RFC that lands (`docs/v2/rfcs/README.md` procedure). Newest
first. Not for the initial constants freeze itself (packet W2-8, no RFC
required — this changelog and the RFC procedure are what govern changes
*after* this packet lands).

| Date | RFC | Summary | Hash before -> after |
|---|---|---|---|
| 2026-09-13 | [RFC-0001](RFC-0001-author-unmeasured-max-fraction.md) | Add `AUTHOR_UNMEASURED_MAX_FRACTION = 0.20` (D-MISS-1 collector-failure gate for row 1.2/1.3, W1-4 round-2 review B-2/B-3); relocated out of `cvi.collect.github_graphql`, closing the census escape. No rank movement (no scoring-path consumer yet). | `391d10b91e86a230fa8c16e32f57f2bc5d0449c33dc3bcb6c3f96153ef83fed8` -> `d2702ea93a7ad9eb22696942dd3994d63f066ff2bf2e68e52abfc8235433570e` |
