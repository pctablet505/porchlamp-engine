# I-04 Front-end architecture — FAIL (4 rule violations at 026bb32)
All line numbers are `git show 026bb32:<path>`.

patchRows (`components/patch.js`) — CORRECT. Hand-traced add / remove / move / no-op and the
spacer-bracketed window: the cursor walk with `insertBefore(node, cursor)` yields the right order in
every case ([A,B]<-[B,A], [A,B,C]<-[C,B,A], remove+add mid-list); survivors are moved, never
recreated; leftovers removed. Only `createItem` builds markup. Two latent hazards: a duplicate key
(two rows with the same owner/name) silently accumulates duplicate nodes forever (Map keeps the last
binding, the orphan is never in `existingByKey` again); a non-element `firstChild` (stray text node)
would be treated as the cursor.
Header/footer/table skeleton: built once (`buildHeaderOnce`/`buildFooterOnce`, `shellShape`) — OK.
Scroll/resize: `patchLeaderboardBody` only; the container is never rebuilt on scroll — OK.
Cell-level update: `updateRowElement` rewrites only `<td>`s whose `dataset.rendered` differs — OK.
Virtualisation within a page: spacer rows + `computeVisibleRange` (buffer 8) — OK.

VIOLATIONS
1. U-12 "never innerHTML on an ancestor of the focused element" — `app.js:398` rewrites
   `drawerRoot.innerHTML` on every drawer content change, including evidence arriving while focus is
   on the close button inside it. (In-flight fix confirms: "opening via click leaves focus on body".)
2. U-16 "replaceState for q while typing" — NOT implemented. `app.js:288 replaceUrl` is defined and
   never called (`grep -rn replaceUrl web/v2` -> one hit, the definition); the search handler
   (`app.js:445`) goes through `changeParamsAndFetch` -> `pushUrl` (`app.js:425`), so each 80 ms
   debounce window pushes a history entry. The handoff claims the opposite.
3. U-12 on the Not-ranked list — `views/notranked.js:131` sets `container.innerHTML` for the whole
   row list instead of a keyed patch; a focused not-ranked row is destroyed by any re-render.
4. U-15 "Loading… is never a resting state" — the snapshot fetch has no error path:
   `SNAPSHOT_ERROR` is stored but `syncFooter` (`app.js:139-150`) and the header chip only test
   `!snap`, so a failed `/v1/cvi/snapshot` leaves "Loading…" in the footer and the snapshot chip
   permanently. (Leaderboard/not-ranked/evidence error states are all correct, with a retry.)

COPY RULE (06 §6 "every user-facing sentence in copy.js") — violated at:
`views/drawer.js:64` (a full sentence), `:111` and `:158` (11 table headers),
`components/table.js:156` and `:268`, `views/notranked.js:42` (aria-label "Open …").
`views/drawer.js:196` reuses `emptyLeaderboardText(0, 0)` ("No rated repositories match. 0 gated and
0 unrated…") as the drawer's repo-not-found message.
