# Item 8 — Wall clock / RETURNING / NULLS LAST / FK / read-only (L3) — PASS, 1 Note

- `grep RETURNING porchlamp/store/queries.py` -> none.
- `grep -E "datetime\.now|time\.time|utcnow|date\.today|CURRENT_TIMESTAMP|datetime\('now'\)"` -> none.
- ORDER BY audit: every D-TIE-1 level carries `NULLS LAST` except the final
  `lower(owner || '/' || name) ASC` (both columns NOT NULL). Two further
  ORDER BYs carry none: `_tie_group_sql`'s inner
  `DENSE_RANK() OVER (ORDER BY ROUND(expr,2) DESC)` and `snapshot_summary`'s
  `ORDER BY je.value`; both operate on provably NULL-free inputs (the persona
  and reproject visible sets exclude NULL composites; json_each values are
  never NULL). No grep-test enforces the blanket rule -> Note N-1.
- FK + transactions: after each of the nine read calls
  `conn.in_transaction=False` and `PRAGMA foreign_keys=1`.
- Strongest read-only proof: every function returns successfully on a
  `sqlite3.connect('file:...?mode=ro', uri=True)` connection.
