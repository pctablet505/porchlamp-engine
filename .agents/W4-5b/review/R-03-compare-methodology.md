# R-03 Screens E (Compare) and F (Methodology)

## E. Compare — PASS with 2 notes
- 2–4 repos via chips, `cmp=owner/name,...` in the URL, round-trips.
- **Direction is genuinely payload-driven**: `parameterDirection` reads `value_domain`
  (`higher_is_better` for numeric/ecdf; `values_worst_to_best` / `persona_orders` for enums). I
  grepped the file for hardcoded directions and per-parameter names — the only occurrences of
  `"higher"`/`"lower"` are the two return values of that one function, and no parameter id appears
  anywhere in the file. The fixture backs it: 12 numeric + 3 ecdf carry `higher_is_better`, 3 enums
  carry `values_worst_to_best`.
- `betterRepoKey` declines on a tie and with < 2 measured values. **W-5**: for an enum it ranks by
  `order.indexOf(v)`, so a value absent from `values_worst_to_best` scores −1 — i.e. "worse than the
  worst" rather than "unknown, do not compare". One `indexOf(...) === -1 → treat as unmeasured` line.
- `computeVerdict` counts wins ONLY over parameters measured on every compared repo, collects the
  rest into `notMeasuredEverywhere`, and names them; equal win counts report as tied. Correct.
- Evidence source per cell on hover/focus; states: empty (0), partial-min (1), per-repo loading/error;
  a 404 evidence fetch is tolerated as an empty evidence set.
- **W-4** `COMPARE_SET_QUERY` rebuilds `main.innerHTML` on every keystroke (the implementer's own
  COULD-NOT-VERIFY entry) — U-12 forbids `innerHTML` on an ancestor of the focused element; the
  search input is inside `main`. Focus is explicitly restored afterwards (live-verified), so the
  residual risk is caret position, but the rule is still violated where a keyed patch of the results
  list would avoid it.

## F. Methodology — PASS with 1 warning
- Every rendered number comes from the payload via a `copy.methodology*` formatter; I ran their
  detector and it is genuinely load-bearing on this file (planting `const PLANTED = 0.95;` fails it:
  mutant M9 KILLED).
- The snapshot-section race they fixed: `shellShape` no longer depends on a separate in-flight flag,
  so a late `state.snapshot` cannot leave the section unrendered — read and confirmed.
- Reproduce command in a copyable `<pre>` + clipboard button with the rejected-permission catch.
- Footer "Verify" routes to `goToTab("methodology")`.
- **W-2 the no-literal guard is bypassable two ways, both demonstrated on a copy of the tree:**
  1. a literal inside a `${...}` expression that contains braces —
     `` `<p>${((n) => { const FLOOR = 0.6; return n > FLOOR ? "a" : "b"; })(v)}</p>` `` — because
     `_TEMPLATE_EXPR_RE = \$\{([^{}]*)\}` cannot match a braced expression, so that whole expression
     is dropped unscanned (mutant M10 SURVIVED);
  2. a literal inside a quoted string — `Number("0.95")` — because
     `template_literal_expressions_only` drops every quoted string whole.
  The file is clean today; the guard would not stop the defect class returning. Fix: match `${...}`
  with a brace-balanced scan, and scan quoted strings for digits too.
- **W-7** `methodology.json` is a pre-generated static fixture, not produced at startup by
  `cvi.audit.methodology_history.methodology_with_history` (declared deviation). Post-rebase that
  module IS importable here, and no test compares the fixture against a live call — so the document
  the UI renders can drift from the real methodology silently. A single test that regenerates and
  diffs would close it.
