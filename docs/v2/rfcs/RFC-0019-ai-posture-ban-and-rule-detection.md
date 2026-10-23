# RFC-0019: ai-posture-ban-and-rule-detection

## Motivation

Parameter 4.2 (`ai_posture`, `docs/v2/02-RANKING-DESIGN.md` section 4) classifies a repository's AI
contribution policy into `REGULATED`, `PROSCRIBED`, or `AD_HOC`, with persona-conditional scores
(`REGULATED = 95/95`, `PROSCRIBED = 85` human / `25` AI, `AD_HOC = 40/40`) and the D-PERS-2
`ai_assisted` exclusion gate (`ai_posture == PROSCRIBED` with non-empty `ai_policy_evidence`).

On the 2026-10-21 snapshot (`5,448` rated repositories; `10,994` measured overall):
- `0` repositories (`0.0%`) were classified as `PROSCRIBED` (`786 REGULATED = 14.4%`,
  `4,656 AD_HOC = 85.6%` among rated repositories; `1,254 REGULATED`, `9,740 AD_HOC`, `0 PROSCRIBED`
  across all `10,994` measured repositories).
- The `ai_assisted` persona gate (`ai_hard_ban`) fired on `0` repositories across the entire
  universe.

Auditing `porchlamp/collect/governance.py` (`classify_ai_posture`) against `256` repositories in
the raw governance cache whose policy documents mention AI/LLM terms revealed five root causes:

1. **Ordering bug (`AI_POLICY_FILES` checked before `AI_BAN_PATTERNS`).** Any repository with
   `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, or `.github/copilot-instructions.md` returned
   `REGULATED` immediately at Step 1, never scanning `CONTRIBUTING.md`, `README.md`, or the AI file
   itself for an explicit ban.
2. **Overly narrow `AI_BAN_PATTERNS`.** The ban regex matched only four rigid phrases (`no ai-generated`,
   `do not submit ai`, `ai contributions are not accepted`, `prohibit use of ai/llms`) and missed
   standard real-world bans (`do not use AI to generate code/PRs`, `AI-generated PRs will be closed`,
   `refrain from using AI tools`, `zero tolerance for AI-generated submissions`, `must be written
   entirely by humans`, `ban on AI-generated content`).
3. **Unqualified ban false positives on conditional/disclosure rules and non-code targets.** When
   ban patterns are widened, sentences with conditional qualifiers (`do not submit AI-generated
   code without disclosure`, `unless you have reviewed and tested every line`) or non-code targets
   (`do not use AI for bug reports / issue descriptions / security reports`, `do not use AI to
   decide what to change`) are regulations (`REGULATED`), not hard code-contribution bans
   (`PROSCRIBED`).
4. **Prose AI policies in `CONTRIBUTING.md` ignored.** Repositories that document detailed AI
   contribution rules in `CONTRIBUTING.md` (`### AI-Assisted Contributions`, `must disclose AI
   usage`, `Co-authored-by:` attribution requirements) without a standalone `CLAUDE.md` or
   `Assisted-by:` trailer were classified as `AD_HOC` (`84` repositories in the 256-repo AI-mentioning
   corpus).
5. **PR templates not collected or scanned.** `.github/PULL_REQUEST_TEMPLATE.md` and
   `.github/pull_request_template.md` were absent from `POLICY_FILES`, missing both PR-template
   AI disclosure checkboxes (`REGULATED`) and PR-template AI bans (`PROSCRIBED`).

## Change

In `porchlamp/collect/governance.py`:

1. **Expand `POLICY_FILES` (`10 -> 14` paths):** add `.github/PULL_REQUEST_TEMPLATE.md`,
   `.github/pull_request_template.md`, `PULL_REQUEST_TEMPLATE.md`, and `pull_request_template.md`
   (`PR_TEMPLATE_FILES`).
2. **Evaluate `PROSCRIBED` first across AI policy files, `CONTRIBUTING.md`, `README.md`, and PR
   templates:**
   - Strip HTML comments (`<!-- ... -->`) and normalize whitespace before sentence splitting.
   - Match explicit ban sentences via `AI_BAN_PATTERNS`, skipping sentences that contain conditional
     or disclosure qualifiers (`AI_BAN_QUALIFIERS`: `without`, `unless`, `except`, `undisclosed`,
     `unreviewed`, `unverified`, `blindly`, `solely`, `purely`) or target non-code artifacts only
     (`_NON_CODE_OBJECT`: `issue`, `bug report`, `vulnerability`, `discussion`, `comment`, `forum`,
     `decide what`) without naming code/PRs/commits/patches.
   - A matched ban sentence returns `("PROSCRIBED", "<path>: '<sentence>'")`, which sets
     `hard_ai_ban = True` and triggers the `ai_assisted` persona gate (`ai_hard_ban`).
3. **Evaluate `REGULATED` second:**
   - Presence of a dedicated AI rule document (`AI_RULE_DOCUMENT_NAMES`: `AGENTS.md`, `CLAUDE.md`,
     `.cursorrules`, `.github/copilot-instructions.md`).
   - Explicit trailer convention (`Assisted-by:`, `AI-generated:`, `Generated-by:`, or
     `Co-authored-by:` paired with an AI/LLM/agent reference), AI policy section heading
     (`AI_POLICY_HEADING`), PR-template AI disclosure checkbox (`AI_CHECKBOX`), or AI rule directive
     sentence (`AI_RULE_DIRECTIVE` paired with `_DIRECTIVE_IMPERATIVE`, excluding negated
     requirements such as `not required to disclose`) in `CONTRIBUTING.md`, `README.md`, AI rule
     documents, or PR templates.
4. **Default:** `("AD_HOC", "")` when at least one governance policy file was fetched and none of
   the `PROSCRIBED` or `REGULATED` rules matched.

No hashed constant in `porchlamp/core/constants.py` is added or modified by this RFC (`POLICY_FILES`
and the classifier regexes live in `porchlamp/collect/governance.py`), so `CONSTANTS_HASH` is
unchanged by RFC-0019 (`3bc4aac040af2937e438e39e3e18ffbbfad6ddf7aeea7a1911f6e6013105226d` ->
`3bc4aac040af2937e438e39e3e18ffbbfad6ddf7aeea7a1911f6e6013105226d`).

## Evidence

- **Labelled sentence corpus (`63` cases across explicit bans, qualified bans, non-code bans,
  prose regulations, PR-template checkboxes, and permissive/incidental mentions):** `63/63` exact
  matches (`0` false positives, `0` false negatives).
- **Live evaluation across all `256` repositories in the governance cache whose policy documents
  mention AI/LLM terms (with live PR templates fetched):**

  | Transition (`old -> new`) | Count |
  |---|---|
  | `AD_HOC -> REGULATED` | `84` |
  | `REGULATED -> REGULATED` | `81` |
  | `AD_HOC -> AD_HOC` | `61` |
  | `AD_HOC -> PROSCRIBED` | `21` |
  | `REGULATED -> PROSCRIBED` | `9` |
  | **Total** | **`256` (`30 PROSCRIBED`, `165 REGULATED`, `61 AD_HOC`)** |

  - The `9` `REGULATED -> PROSCRIBED` repositories are projects that carry an AI rule file or
    PR template alongside an explicit ban on external AI pull requests (which the old ordering bug
    masked as `REGULATED`).
  - `20` of the `256` repositories depend on the newly added `PR_TEMPLATE_FILES` paths for their
    `PROSCRIBED` or `REGULATED` classification.

## Reviewer

Reviewed by independent adversarial reviewer (#66 PR, 2026-10-23).

