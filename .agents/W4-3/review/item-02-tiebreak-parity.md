# Item 2 — Tie-break parity, reviewer's own case (L3) — PASS; R-1 finding

Fixture (`item2_tiebreak.py`, 252 rows through the real writer): a 2-dp tie
group of four; two singletons; two owners differing only by case
(`Zeta/alpha` vs `zeta/beta`, identical through level 7); four NULL-REACHING
pairs identical above the level under test so friction / leverage / ttfhr_p75 /
last_human_commit_at each actually compare NULL against measured; 228 random
rows with NULLs at the responsiveness pillar and the four tail levels; plus
gated and unrated rows.

    rows written: 252
      persona=balanced         n_sql=244 n_py=244 total=244 order_equal=True
      persona=ai_assisted      n_sql=242 n_py=242 total=242 order_equal=True
      persona=career_capital   n_sql=234 n_py=234 total=234 order_equal=True
      persona=first_timer      n_sql=242 n_py=242 total=242 order_equal=True
      persona=senior_systems   n_sql=244 n_py=244 total=244 order_equal=True
    ALL ORDERS EQUAL: True
    negative control (friction NULLS LAST dropped) differs: True
    restored order equals python: True

R-1 (`item2b_tiegroup.py`): membership equal: True; **24 / 234 rows have a
different tie_group id** (SQL `tie_group_p=11` vs `assign_ranks` `1`; SQL ids
run to 219, Python's to 11). `queries.py` mentions 'opaque' 0x, 'equality' 0x,
'across responses' 0x, 'numbering' 0x. -> Warning W-1.
