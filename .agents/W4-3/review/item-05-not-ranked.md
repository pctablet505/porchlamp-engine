# Item 5 — Not-ranked (L3) — PASS

`item45_paging_notranked.py`, 252-row snapshot:

    persona=balanced        ranked=244 not_ranked=8  sum=252 all=252 partition=True
       counts={'archived':1,'mirror':1,'insufficient_evidence':6}  counts_sum==len(rows): True
    persona=first_timer     ranked=242 not_ranked=10 sum=252 all=252 partition=True
       counts={'unrated: insufficient_evidence':8,'archived':1,'mirror':1}
    persona=career_capital  ranked=234 not_ranked=18 sum=252 all=252 partition=True
       counts={'insufficient_evidence:2.1':10,'archived':1,'mirror':1,'unrated: insufficient_evidence':6}
       persona-gated rows carry the persona gate reason: 10 rows

Every row carries a non-empty `reason`; `rank`/`tie_group` cleared on every row;
rows ordered by lower(owner/name); filters flow through (ecosystem='systems'
gives 5 rows / counts_sum 5 under career_capital).
Ranked + not-ranked = all rows, disjoint, under balanced and two personas.
Note N-2: no limit/offset on this path.
