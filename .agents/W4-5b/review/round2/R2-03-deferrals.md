# R2-03 Judgement on the four deferrals

## W-4 (Compare per-keystroke `main.innerHTML`) — **BLOCKS**
Mechanism confirmed in source at `app.js:432-447`:
```js
const html = renderCompareShell(state);
if (currentTab !== "compare" || html !== currentCompareShape) {
  const searchHadFocus = doc.activeElement && doc.activeElement.matches("[data-compare-search]");
  main.innerHTML = html;
  if (searchHadFocus) main.querySelector("[data-compare-search]")?.focus();
```
The search input is rendered INSIDE `main` (`views/compare.js:150`) and its own
`value="${escapeHtml(state.compareQuery)}"` is interpolated into `html`, so every keystroke makes
`html !== currentCompareShape` and destroys the focused node. That is literally U-12's prohibition —
`innerHTML` on an ancestor of the focused element — and matches the coordinator's stated criterion.

Beyond the rule, the consequence is user-visible: a replaced `<input>` cannot carry
`selectionStart`/`selectionEnd`, so the caret jumps to the end of the value on every keystroke —
editing anywhere but the end of the query is impossible — and an IME composition session is
interrupted mid-word. The implementer's own COULD-NOT-VERIFY entry guessed this ("a minor UX
papercut if the browser resets it to the end"); it is not a papercut, it makes mid-string editing
unusable, and it is the one deferral that changes what a user can do.

*Minimal fix:* keep the input out of the diffed string — render the picker's RESULTS list into its
own container and patch only that (it is not an ancestor of the input), or drop `compareQuery` from
the shell key and let the input own its own value. Either is local to `renderCompareShell` plus the
`syncView` branch above.

## W-2 (no-literal detector bypass) — does not block
The detector is load-bearing for the defect it was written against (a bare literal: mutant M9 dies)
and `views/methodology.js` is clean today. The two bypasses I demonstrated (a literal inside a braced
`${…}` expression; a literal inside a quoted string) weaken a guard, they do not ship a defect.
Follow-up.

## W-6 (`postReproject` duplicates `getJson`'s transport) — does not block
~25 duplicated lines, no behavioural difference; both paths were mutation-tested together (M11 kills
5 tests across both). Follow-up.

## W-7 (methodology fixture is pre-generated, never diffed against a live call) — does not block
A real drift risk, but the content is genuinely the function's own output and Screen F renders
whatever it is sent; nothing is wrong today. It becomes moot when W4-4's real `/methodology` endpoint
replaces the fixture server. Follow-up, with a regenerate-and-diff test as the cheap close.
