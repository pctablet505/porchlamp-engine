# Item 6 — the `-` sort direction is inert — CONFIRMED; required follow-up

Measured: `GET /leaderboard?sort=coverage&page_size=1000` and
`sort=-coverage` return an **identical** 64-row order. `-rank`, `-cvi`,
`-tier`, `-p_rigor` are all accepted (200) and all inert.
`cvi.store.queries._build_order_by` hard-codes `DESC` on every branch.

Minimal `cvi/store/queries.py` change — the promoted level takes a
direction, the D-TIE-1 tail after it stays fixed so the order stays total
and offset paging stays deterministic:

```python
def _build_order_by(sort, composite_expr, coverage_expr, tier_expr, *, descending=True):
    default = _order_by_sql(composite_expr, coverage_expr)      # unchanged tail
    direction = "DESC" if descending else "ASC"
    if sort is None or sort in ("rank", "cvi"):
        return default if descending else f"{composite_expr} ASC NULLS LAST, {default}"
    if sort == "coverage":
        return f"{coverage_expr} {direction} NULLS LAST, {default}"
    if sort == "tier":
        return f"{_tier_index_sql(tier_expr)} {direction} NULLS LAST, {default}"
    if sort in _PILLAR_SORT_FIELDS:
        return f"p_{sort} {direction} NULLS LAST, {default}"
    raise ValueError(...)
```

`NULLS LAST` stays on the ASC branch too (unmeasured always sorts last,
D-TIE-1's rule, independent of direction). `list_ranked` grows
`descending: bool = True`; W4-4's `_translate_sort` returns
`(field, descending)` instead of discarding the stripped `-`.

Parity test (per allowed `sort`): the promoted column's value sequence is
non-increasing under `descending=True` and non-decreasing under
`descending=False`, with NULLs last in **both**; the two orders are not
equal; rows are the same set; the existing offset-paging partition test
re-runs green under `descending=False`. Negative control: dropping
`NULLS LAST` from the ASC branch changes the order on a NULL-bearing
fixture.
