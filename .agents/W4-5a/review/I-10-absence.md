# I-10 Absence table — dispatch, the two rework messages (rulings A-H), the pagination instruction
Rulings A-H as recorded in `docs/v2/00-README.md` row 2026-09-13 "W4-5a OWNER UX VERDICT", plus the
owner's pagination instruction (row "OWNER | UX instruction: pagination") and 06 §6.1 U-12..U-19.

| Ruling | Asked for | Present at 026bb32? |
|---|---|---|
| A keyed incremental DOM, focus/scroll/selection preserved, no innerHTML above focus | yes | PARTIAL — table/cards keyed (correct); drawer rewrites innerHTML over its own focused element (B-3); Not-ranked rows are innerHTML (I-04 #3) |
| B one virtualised list, sticky thead, pages of 500 prefetched | superseded by the owner's pagination instruction | N/A — correctly superseded |
| C sort/search/filter instant client-side; persona switch greys + caches | REINTERPRETED — all four now fetch (documented deviation, sound: only the server sees the full set under paging); greying (`.stale-table`), progress bar and per-persona… | PARTIAL — the grey/progress path exists; there is NO per-persona payload cache (only a single-slot NEXT-PAGE cache); a persona switch back and forth refetches |
| D explicit error state for every fetch (404, 5 s timeout) | yes | PARTIAL — leaderboard/not-ranked/evidence yes (with retry); snapshot has none (I-04 #4) |
| E query-string URL = state, pushState/popstate | yes | PARTIAL — pushState everywhere including `q` while typing; replaceState never used (B-5); Not-ranked page/page_size are not in the URL at all |
| F true "rows moved" counts | yes | PRESENT — `countRankChanges` matches by owner/name on rank difference; announced only for non-balanced personas (switching back to balanced announces nothing) |
| G keyboard continuity | yes | ABSENT at this tip — J/K move an internal key and a CSS class but never `document.activeElement` (B-2); Esc focus restore is implemented |
| H Node perf test, 10,000 rows, per-scroll-step patch time | yes | PARTIAL — the file exists but uses 1,000 rows (the max page size, a defensible reinterpretation, declared), is unexecutable as written (I-05), and tests a COPY of the shipped windowing function |
| Owner pagination: default 20, dropdown 20/50/100/500/1000, next/prev + first/last + "Page i of N (M rows)", page & page_size in URL, page scroll, sticky header, virtualised within a page, first visible row kept on a size change, one page per fetch + next-page prefetch | yes | PRESENT — every element implemented and verified in code; the same pager on Not-ranked (client-side, declared deviation) but its page/page_size are not URL-synced |
| Dispatch: contract-first, no I/O in contract.py, fixtures, stdlib server, zero external requests, do not touch v1/store/core/collect | yes | PRESENT — verified: contract imports only typing+pydantic; `git diff --stat acdf885..026bb32` touches no `cvi/core`, `cvi/store`, `cvi/collect`, `scripts/serve.py`, `web/index.html`, `scripts/demo_app.py` |
