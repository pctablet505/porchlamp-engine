# R2-04 Are W-3 and W-4 landing-blockers?

**W-3 (not-ranked list rendered with `container.innerHTML`) — NOT a landing-blocker.**
U-12's rule is "never `innerHTML` on an ancestor of the focused element". The not-ranked rows ARE
focusable (`tabindex="0" role="button"`), so the container CAN be an ancestor of focus, but
`patchNotRankedBody` rewrites only when the computed html differs (`notranked.js:143`), and I could
not construct a transition that changes that html while a row holds focus:
- pager/page-size changes put focus on a pager control, outside the container;
- a persona switch puts focus on the header select;
- the rows do not exist yet while the payload is loading;
- `closeDrawer()` does focus a not-ranked row, but the re-render that follows produces identical html,
  so no rewrite happens.
The one real path — the list content changing while a row is focused — needs a persona change made
without touching the persona control, which no current interaction offers. Rule: leave it, and fix it
when W4-4 makes this list server-paged (the handoff already flags that), or when any keyboard action
can change the list while a row is focused. Note `]`/`[` on the Not-ranked tab currently page the
LEADERBOARD, which is its own small defect (N-2).

**W-4 (the snapshot fetch has no error state) — YES, it blocks.**
U-15 is unconditional ("Every request has an error state… 'Loading…' is never a resting state") and
the snapshot fetch is one of the four. `SNAPSHOT_ERROR` is reduced into state and then read by
nothing: `syncFooter` (`app.js:139-156`) and the header chip branch only on `!snap`, so a 404/5xx/
timeout on `/v1/porchlamp/snapshot` leaves the literal string "Loading…" in the header chip and the footer
**forever**. That is precisely the fabricated state U-7 forbids, and it lands on the one strip that
carries 06 §6 U-6's reproducibility claim (snapshot id, version, constants hash, manifest root —
§8 item 6). It is also the failure mode most likely to appear first against W4-4's real API.
Cost to fix: render `state.snapshotError` through the existing `copy.errorBannerText` in both places
(~6 lines + one test asserting the footer does not say "Loading…" when `snapshotError` is set).
