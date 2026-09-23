# Item 4 — Paging determinism and sort (L3) — PASS; 1 Blocker; 1 Warning

`item45_paging_notranked.py`: for balanced, persona=first_timer, each with and
without a `q` filter and an ecosystem filter, and for sort in
{coverage, tier, responsiveness, rigor} -- page sizes 1/7/50/100. In all 14
configurations the concatenated pages equal the single full-limit list exactly,
contain no duplicates, and report the same `total`. `ALL PAGING CHECKS PASS: True`.

R-3: every vanity/unspellable field raises naming the allowed set --
stars, stars_audited, forks, downloads, owner, porchlamp_p, rank_p, tie_group.
`ALLOWED_SORT_FIELDS = ('rank','porchlamp','coverage','tier','responsiveness',
'criticality','governance','rigor','accessibility')`.

Blocker B-2: `rank_p` is ROW_NUMBER over the CHOSEN sort, not over D-TIE-1:
    rows whose rank_p CHANGES when sort=coverage: 241 of 242
      bulk/repo-078 rank_p(sort=None)=2  rank_p(sort=coverage)=215
    balanced rank field stable under sort: True

Warning W-5: `limit=-1` -> 244 rows (SQLite: negative LIMIT = unlimited);
`offset=-5` silently behaves as 0; `reproject limit=-1` -> 244 rows.
Sort direction semantics ARE documented in `_build_order_by`'s docstring
(coverage/tier/pillar promoted DESC ahead of the full D-TIE-1 chain).
