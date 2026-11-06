"""Governance evidence — legal IP, promotion ladder, AI posture.

Collects, per repository, the raw evidence for the three governance
parameters of ``docs/v2/02-RANKING-DESIGN.md`` section 4 (3.1, 3.3, 4.2)
and derives them with ``NONE`` / ``AD_HOC`` / ``AD_HOC`` defaults:

* **3.1 Legal IP** — check-run names and commit-status contexts on the
  :data:`MERGED_PR_CAP` (20) most recent merged PRs (DCO, EasyCLA,
  cla-assistant, google-cla, ...). The DCO alternative evidence is the
  ``Signed-off-by`` share over the trailing 365 d commits: the
  ``commits_365d`` envelope (:mod:`porchlamp.collect.commits`) records a
  per-commit ``signed_off`` boolean, so this module computes the share
  from that envelope with **no second commit fetch** and no
  message text stored anywhere (``03`` section 3: documents/text are not
  evidence for 3.1; only checks and the sign-off fact are).
* **3.3 Promotion ladder** — action-based promotions read from the
  recorded 180 d ``pr_window`` envelope (RFC-0016): an account that first
  had D-IN-3 age-eligible (``>= MIN_ACCOUNT_AGE_DAYS``) PRs merged by
  another non-bot human, had no recorded merge or close action before
  that merge, and at least :data:`PROMOTION_MIN_OUTSIDE_DAYS` (30 d)
  later merged or closed another author's PR (D-IN-2 maintainer action)
  is a verified promotion (``VERIFIED``). Because ``VERIFIED`` and
  ``CLOSED`` both rest on the absence of earlier actions in the 180 d
  window, a ``pr_window`` cut short of 180 d refuses every rung. The
  ``CLOSED`` rung needs the merged PR authors attributable to one
  email-domain org via the ``commits_365d`` envelope. ``DOCUMENTED``
  reads the collected GOVERNANCE / MAINTAINERS / CONTRIBUTING bodies
  (resolved across ``.github/``, root, and ``docs/`` with case and
  extension variants, plus ``<owner>/.github`` org-level defaults for
  CONTRIBUTING and SECURITY).
* **4.2 AI policy posture** (RFC-0019) — the :data:`AI_POLICY_FILES`
  family, and the AI rules a contributing guide or a pull-request template
  states (:data:`AI_RULE_DOCUMENT_NAMES`, wherever the collector recorded
  them; :data:`PR_TEMPLATE_FILES` are fetched for it). **PROSCRIBED** needs
  an explicit ban sentence that binds the ban to AI-made work, as its object
  or its subject (:data:`AI_BAN_PATTERNS`: "We do not accept AI-generated
  contributions", "AI-generated pull requests are not accepted", "Do not use
  AI tools to write code"), with no condition that narrows it to some AI use
  (:data:`AI_BAN_QUALIFIERS`: "undisclosed", "without review", "fully",
  "low-quality") and not aimed only at issues, comments or reports. Bare
  co-occurrence of a negation and the word "AI" is never a ban: "Do not
  worry if you used AI" and "We do not ban AI-generated code" match no
  pattern, because every pattern names what is banned. A ban narrowed by a
  condition, or aimed only at issues or comments, still regulates AI use and
  reads REGULATED, as do an AI-policy file, an AI-policy heading or an AI
  checkbox in those documents, a sentence requiring that AI-made work be
  disclosed or reviewed, and a named ``Assisted-by:`` trailer convention;
  AD_HOC otherwise. :data:`porchlamp.core.constants.AI_POSTURE_VALUES` keeps
  the three spellings (REGULATED/PROSCRIBED/AD_HOC). The commit-trailer
  convention path of ``02`` section 4 4.2 ("Assisted-by"/"AI-generated")
  needs commit message text, which this collector never reads, so that path
  is not evaluated here either.

Per ``03`` section 3, documents are not 3.1 evidence: the regime comes from
check/status names and the sign-off share only. Classification constants
are defined here, as the single definition; ``porchlamp.core.constants`` owns
the value vocabularies (:data:`porchlamp.core.constants.LEGAL_IP_VALUES`,
``PROMOTION_LADDER_VALUES``, ``AI_POSTURE_VALUES``) and the hashed
:data:`porchlamp.core.constants.FOUNDATION_ORGS` and
:data:`porchlamp.core.constants.PROMOTION_MIN_OUTSIDE_DAYS`.

Priority, highest first (``02`` §4 + ``03`` §3, RFC-0016, RFC-0017, RFC-0019):

* 3.1: foundation-CLA check > generic CLA check on a
  :data:`FOUNDATION_ORGS` owner > corp-CLA check > DCO check > sign-off
  share ≥ :data:`SIGNOFF_THRESHOLD` > :data:`FOUNDATION_ORGS` owner (no
  CLA check and no DCO evidence) > NONE. A CLA bot check is direct
  evidence of a CLA regime and outranks DCO evidence; ownership alone is
  the weakest signal and never shadows measured DCO evidence (RFC-0017).
* 3.3: VERIFIED (observed outsider-to-maintainer action transition in an
  uncut 180 d window) > CLOSED (single-org merge stream, no external
  merges) > DOCUMENTED (nomination text) > AD_HOC. CLOSED outranks
  DOCUMENTED so a closed repo cannot buy 75 by writing docs (``03`` §3:
  the cheapest fake for 3.3).
* 4.2: PROSCRIBED (an explicit, unconditional ban sentence) > REGULATED
  (a policy file, an AI-policy section, checkbox or rule) > AD_HOC. A ban
  outranks a policy file: an AI-policy file that bans AI-made contributions
  is a ban.
"""

from __future__ import annotations

import json
import re
import subprocess  # noqa: F401  (tests patch this module's subprocess)
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Protocol

from porchlamp.collect import commits as commit_domains
from porchlamp.collect import ghcli, net, validate
from porchlamp.collect import github_graphql as pr_window
from porchlamp.collect.envelope import read_envelope, sha256_hex, write_envelope
from porchlamp.core import gates
from porchlamp.core.constants import (
    FOUNDATION_ORGS,
    MIN_ACCOUNT_AGE_DAYS,
    PROMOTION_MIN_OUTSIDE_DAYS,
)
# --------------------------------------------------------------------------- #
# 02 §4 constants (defined here, once)                                        #
# --------------------------------------------------------------------------- #

#: Check evidence comes from the 20 most recent merged PRs.
MERGED_PR_CAP: int = 20

#: D-COL-12: at most this many DISTINCT head SHAs are probed for check/status
#: evidence, newest merge first. Each probed SHA costs two REST calls
#: (``/statuses`` + ``/check-runs``), so the uncapped probe over
#: ``MERGED_PR_CAP`` PRs was up to 40 calls per repository -- about 45 with the
#: pulls pages, ~259k for the 5,753-repo universe, ~52 h of a 5,000/h core
#: budget, which is more than `08` first budgeted for the ENTIRE sweep.
#: A CLA is a property of the project, not of one pull request: where it is
#: required its check appears on essentially every external PR, so twenty
#: probes buy near-duplicate evidence. Five keeps the signal and costs 10 calls.
CHECK_EVIDENCE_SHA_CAP: int = 5

#: D-COL-12: the fewest distinct probed SHAs that can support the NEGATIVE
#: finding "this project has no CLA". A positive signal is a measurement from
#: one probe -- seeing an EasyCLA context proves a CLA exists. Its absence
#: proves nothing until enough commits were examined, and capping the probe
#: makes a thin sample more likely (all five newest merges can be maintainers'
#: own PRs, which carry no CLA check). Below this, 3.1 is an evidence gap
#: rather than a measured ``NONE``.
CHECK_EVIDENCE_MIN_SHAS: int = 3
#: Closed PRs scanned (newest first) to reach the 20 merged: 5 pages of 100.
PULLS_PAGES_MAX: int = 5
#: REST pulls page size (GitHub max).
PULLS_PAGE_SIZE: int = 100
#: 02 §4 3.1: DCO when at least 50% of window commits carry Signed-off-by.
#: The window and cap themselves are :mod:`porchlamp.collect.commits`'s
#: ``COMMIT_WINDOW_DAYS``/``COMMIT_CAP`` (one definition per fact) --
#: this module reads the already-collected ``commits_365d`` envelope rather
#: than refetching, so it does not redefine them.
SIGNOFF_THRESHOLD: float = 0.5
#: 02 §4 3.3: the files whose bodies name a maintainer nomination process.
GOVERNANCE_FILES: tuple[str, ...] = (
    "GOVERNANCE.md", "MAINTAINERS.md", "CONTRIBUTING.md", "SECURITY.md")
#: C5b: GitHub's documented community-health directory precedence
#: (``.github/`` > repository root > ``docs/``).
COMMUNITY_HEALTH_DIRS: tuple[str, ...] = (".github", "", "docs")
#: C5b: recognised file extensions for :data:`GOVERNANCE_FILES`, in priority
#: order within a directory (case-insensitive).
GOVERNANCE_EXTENSIONS: tuple[str, ...] = (".md", ".rst", ".txt", "")
#: C5b: GitHub's documented org-level community-health defaults resolved from
#: ``<owner>/.github`` when absent from the repository itself (GOVERNANCE and
#: MAINTAINERS are not GitHub-supported org-level defaults).
ORG_DEFAULT_FILES: frozenset[str] = frozenset({"CONTRIBUTING.md", "SECURITY.md"})
#: 02 §4 4.2: the AI-policy file family (a presence = a policy file).
AI_POLICY_FILES: tuple[str, ...] = (
    "AI.md", "AI_POLICY.md", "AIPOLICY.md", "AI-POLICY.md",
    ".github/AI.md", ".github/copilot-instructions.md", "AGENTS.md",
    "CLAUDE.md")
#: RFC-0019: the pull-request templates fetched for 4.2, GitHub's two spellings
#: under ``.github/`` (an AI disclosure checkbox or an AI ban often sits there).
PR_TEMPLATE_FILES: tuple[str, ...] = (
    ".github/PULL_REQUEST_TEMPLATE.md", ".github/pull_request_template.md")
#: RFC-0019: besides the :data:`AI_POLICY_FILES` family, 4.2 reads the AI rules
#: of these documents, matched by lower-cased file name wherever the collector
#: recorded them (the root, ``.github/`` or ``docs/`` copy alike).
AI_RULE_DOCUMENT_NAMES: frozenset[str] = frozenset(
    {"contributing.md", "pull_request_template.md"})
#: All policy and governance files checked by collect_governance.
POLICY_FILES: tuple[str, ...] = (*GOVERNANCE_FILES, *AI_POLICY_FILES, *PR_TEMPLATE_FILES)

#: A genuine CLA check/status name -- the shared "is this even a CLA check"
#: test both branches below require (a bare
#: foundation-name substring with no CLA marker at all -- "license/apache-2.0",
#: "apache-rat", "build (eclipse-temurin-17)", "asf-jenkins-build",
#: "cncf-conformance-e2e", "test-on-apache-spark" -- must never match
#: anywhere in this module). Matches an explicit CLA-bot name, a "license/cla"
#: or "cla/..." status-context spelling, or a bare "cla" token bounded by
#: non-alphanumeric characters (so "easycla"/"cncf-cla" still match via their
#: own named alternative, but "clarity-check" or "declarative" do not).
CLA_CHECK_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE) for p in (
        r"easycla",
        r"eclipsefdn/eca",
        r"cla[-_ ]?assistant",
        r"cla[-_ ]?check",
        r"license/cla",
        r"cla/",
        r"(?<![a-z0-9])cla(?![a-z0-9])",
    ))
#: Check names / status contexts that evidence a foundation-branded CLA
#: (02 §4 3.1: "EasyCLA/ASF/Eclipse/CNCF CLA check"). Every alternative here
#: also satisfies :data:`CLA_CHECK_PATTERNS` (EasyCLA is CNCF's bot; ASF/
#: Eclipse/CNCF run a named "<foundation>-cla" bot or status context such as
#: Eclipse Foundation's ``eclipsefdn/eca``), so a
#: bare foundation-name substring with no CLA marker never matches here:
#: matching "apache"/"asf"/"cncf"/"eclipse" on their own would shadow a
#: real DCO check on common non-CLA build-job names.
FOUNDATION_CHECK_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE) for p in (
        r"easycla",
        r"\b(asf|apache)[\s_/-]*cla\b",
        r"\bcncf[\s_/-]*cla\b",
        r"\beclipse[\s_/-]*cla\b",
        r"\beclipsefdn/eca\b",
    ))
#: Check names / status contexts that evidence a corporate CLA bot
#: (02 §4 3.1: "cla-assistant/google-cla/other CLA bot present"). A corp CLA
#: is, definitionally, any genuine CLA check that is not foundation-branded
#: (checked in that order in :func:`classify_legal_ip`); "google-cla" is
#: already a bare "cla" token bounded by "-", so it is covered by
#: :data:`CLA_CHECK_PATTERNS` with no separate pattern needed.
CORP_CLA_CHECK_PATTERNS: tuple[re.Pattern, ...] = CLA_CHECK_PATTERNS
#: A DCO check run (02 §4 3.1: "DCO check run on recent PRs").
DCO_CHECK_PATTERN: re.Pattern = re.compile(r"dco", re.IGNORECASE)

#: 02 §4 3.3: association transitions observable in the PR window.
PROMOTION_TARGET_ASSOCIATIONS: frozenset[str] = frozenset({"MEMBER", "COLLABORATOR"})
#: 02 §4 3.3 DOCUMENTED: a maintainer nomination process named in a
#: governance file (case-insensitive, one line).
NOMINATION_PATTERN: re.Pattern = re.compile(
    r"nominat\w*|"
    r"become a (maintainer|core (member|maintainer)|committer|tsc member)|"
    r"promotion (path|process|ladder)|"
    r"path to (maintainer(ship)?|membership|core)|"
    r"how to (become|apply)",
    re.IGNORECASE)

# 02 §4 4.2 (RFC-0019): the words of an AI rule. Every ban pattern below names
# what is banned -- AI-made work, or the use of an AI tool to make it -- next to
# the words that ban it; none fires on a negation and the word "AI" merely
# sharing a sentence (the defect that retired the co-occurrence detectors of
# W1-6 rounds 1-3).
_AI = (r"(?:ai|llms?|large[\s-]+language[\s-]+models?|gen(?:erative)?[\s-]*ai"
       r"|artificial[\s-]+intelligence|chat[\s-]?gpt|copilot)")
_AI_TERM = rf"\b{_AI}\b"
#: An AI tool, optionally with what kind of thing it is ("AI tools", "LLM output").
_AI_TOOL = (rf"{_AI_TERM}(?:[\s-]+(?:based[\s-]+)?(?:coding[\s-]+|code[\s-]+|content[\s-]+|text[\s-]+)?"
            r"(?:tools?|assistants?|agents?|models?|services?|chat[\s-]?bots?|generators?|outputs?)\b)?")
_MADE = r"(?:generated|assisted|written|authored|created|produced|made)"
#: The contributions a ban can name.
_CODE_NOUN = (r"(?:contributions?|code|source\s+code|pull[\s-]+requests?|prs?|patch(?:es)?|changes"
              r"|commits?|submissions?|merge\s+requests?)")
_WORK = rf"(?:{_CODE_NOUN}|work|content|output)"
#: Work an AI made: "AI-generated code", "code written by ChatGPT", "content created with the
#: assistance of AI tools", "LLM output", "AI contributions".
_AI_MADE = (rf"(?:{_AI_TERM}[\s-]+{_MADE}"
            rf"|{_MADE}\s+(?:with\s+the\s+(?:help|assistance|aid)\s+of|by|with|using|via|through|from)\s+"
            rf"(?:an?\s+|the\s+|any\s+)?(?:[\w-]+\s+){{0,3}}?{_AI_TOOL}"
            rf"|(?:output|content|text|code)\s+(?:from|of)\s+(?:an?\s+|the\s+|any\s+)?{_AI_TOOL}"
            rf"|{_AI_TERM}[\s-]+{_WORK}\b)")
_BANNED = (r"(?:(?:strictly|explicitly|expressly|absolutely)\s+)?"
           r"(?:(?:not|never)\s+(?:be\s+)?(?:accepted|allowed|permitted|welcomed?|tolerated|merged|considered)"
           r"|prohibited|forbidden|banned|disallowed|rejected|refused|declined|closed)\b")
_NOT = (r"(?:do\s+not|does\s+not|will\s+not|shall\s+not|cannot|can\s+not|don't|doesn't|won't|can't)")
_MUST_NOT = (r"(?:must\s+not|mustn't|may\s+not|shall\s+not|should\s+not|shouldn't|cannot|can't"
             r"|(?:are|is)\s+not\s+(?:allowed|permitted)\s+to)")
_USE_FOR = (r"(?:to|for|when|while|in)\s+(?:(?:your|any|the|a|an)\s+)?"
            rf"(?:(?:writ|generat|creat|produc|author|contribut|develop|cod|submit|prepar|mak|draft)\w*|{_WORK}\b)")

#: RFC-0019 PROSCRIBED: an explicit ban sentence on AI-made contributions. Each
#: pattern binds the ban to its object: AI-made work then the ban ("AI-generated
#: code is not accepted"), the project refusing it ("We do not accept AI-generated
#: PRs"), the reader told not to submit it or not to use AI to make it ("Do not use
#: AI tools to write code"), the use itself banned ("The use of Copilot is
#: forbidden"), or a heading-style ban ("No LLMs for pull requests").
AI_BAN_PATTERNS: tuple[re.Pattern, ...] = tuple(re.compile(p, re.IGNORECASE) for p in (
    rf"{_AI_MADE}(?:\s+\S+){{0,5}}?\s+(?:is|are|will\s+be|shall\s+be|would\s+be|gets?)\s+{_BANNED}",
    rf"\b{_MUST_NOT}\s+(?:be\s+{_AI_MADE}|(?:include|contain)\s+(?:any\s+)?(?:[\w-]+\s+){{0,2}}?{_AI_MADE})",
    rf"\b(?:we|i|this\s+project|the\s+project|maintainers?)\s+{_NOT}\s+"
    rf"(?:accept|merge|take|allow|permit|tolerate|welcome|want|review)\s+(?:any\s+)?"
    rf"(?:{_WORK}\s+(?:that\s+(?:is|are|was|were|has\s+been|have\s+been)\s+)?)?{_AI_MADE}",
    rf"\b(?:do\s+not|don't|never)\s+(?:submit|send|open|contribute|propose|post|file|create|make)\s+(?:any\s+)?"
    rf"(?:{_WORK}\s+(?:that\s+(?:is|are|was|were)\s+)?)?{_AI_MADE}",
    rf"(?:\b(?:do\s+not|don't|never)|\b{_MUST_NOT})\s+use\s+(?:any\s+)?{_AI_TOOL}(?:\s+[\w-]+){{0,3}}?\s+{_USE_FOR}",
    rf"{_AI_TOOL}\s+{_MUST_NOT}\s+be\s+used\b",
    rf"\buse\s+of\s+(?:any\s+)?{_AI_TOOL}(?:\s+\S+){{0,6}}?\s+(?:is|are)\s+{_BANNED}",
    rf"{_AI_TERM}[\s-]+(?:usage|use)\b(?:\s+\S+){{0,4}}?\s+(?:is|are)\s+{_BANNED}",
    rf"(?:^|[^\w-]){_AI_TOOL}\s+(?:is|are)\s+(?:(?:strictly|explicitly|expressly)\s+)?"
    r"(?:prohibited|forbidden|banned|not\s+(?:allowed|permitted))\b",
    rf"\b(?:we|this\s+project|the\s+project)\s+(?:prohibit|forbid|ban|disallow)\s+(?:the\s+)?(?:use\s+of\s+)?"
    rf"(?:any\s+)?(?:{_AI_MADE}|{_AI_TOOL})",
    rf"\brefrain\s+from\s+(?:using|submitting)\s+(?:any\s+)?(?:{_AI_MADE}|{_AI_TOOL})",
    rf"^\W*no\s+(?:{_AI_TERM}[\s-]+{_MADE}\s+{_WORK}|{_AI_TOOL}\s+for\s+(?:any\s+)?{_WORK})\b"
    r"(?!\s+(?:has|have|had|was|were|is|are)\b)",
    rf"\b(?:forbidden|prohibited|not\s+(?:allowed|permitted))\s+to\s+(?:contribute|submit)\b[^.;!?]{{0,80}}?{_AI_MADE}",
    rf"{_AI_MADE}(?:\s+\S+){{0,10}}?\s+{_MUST_NOT}\s+be\s+(?:committed|submitted|merged|accepted|contributed|included)\b",
))
#: RFC-0019: a condition that narrows a ban to some AI use ("undisclosed",
#: "without review", "fully generated", "low-quality", "unless", "only if
#: reviewed", "code you do not understand"). A ban sentence carrying one bans that
#: use only, so it regulates AI use (REGULATED) and does not proscribe it. A ban's
#: consequence is not a condition: "will be closed without review" stays a ban.
AI_BAN_QUALIFIERS: re.Pattern = re.compile(
    r"\b(?:unless|undisclosed|unreviewed|unverified|untested|unattributed|unmarked|unlabell?ed|unedited"
    r"|fully|entirely|purely|solely|wholly|completely|exclusively|mostly|primarily|largely"
    r"|large(?![\s-]+language)|big|huge|massive|substantial"
    r"|low[\s-]+(?:quality|effort)|slop|mass|bulk|spam\w*|blindly|copy[\s-]*(?:and[\s-]*)?past\w*"
    r"|autonomous\w*|automated|automatically|directly|unsupervised|unattended|only|except"
    r"|if\s+(?:\w+\s+){0,3}(?:disclos|review|test|verif|understand|mark|label|attribut|explain|check|edit|approv)\w*"
    r"|if\s+you\s+(?:do\s+not|don't|can\s*not|can't|are\s+not|aren't)"
    r"|you\s+(?:do\s+not|don't|can\s*not|can't|could\s*not|couldn't)\s+(?:fully\s+)?(?:understand|explain"
    r"|maintain|debug|verify|review|test|vouch|justify|defend|support)"
    r"|you\s+(?:have\s+not|haven't|did\s+not|didn't)\s+(?:\w+\s+)?(?:verif|review|test|understand|read|check)\w*"
    r"|that\s+(?:\w+\s+){0,2}(?:not|never)|that\s+\w+n't|that\s+(?:may|might|could|would|can)"
    r"|(?:writ|generat|creat|produc|draft|mak)\w*\s+(?:these|those|them|it|this|that)(?!\s+(?:code|pull|pr|patch))"
    r"|(?<!closed\s)(?<!rejected\s)(?<!declined\s)(?<!deleted\s)(?<!removed\s)without)\b|100\s*%",
    re.IGNORECASE)
_NON_CODE_NOUN = (r"(?:issues?|bug\s+reports?|security\s+reports?|vulnerability\s+reports?|reports?|comments?"
                  r"|discussions?|reviews?|responses?|answers?|descriptions?|messages?|communications?"
                  r"|questions?|walls?\s+of\s+text|translations?|locali[sz]ations?|art(?:work)?|images?|assets?"
                  r"|icons?|graphics?|logos?|sprites?|textures?|music|audio|voices?)")
#: RFC-0019: a ban aimed only at these (and at no code contribution) is about
#: issues, comments, answers, translations or art, not about AI-made code; it
#: regulates. So does one whose AI-made object is one of them ("AI-generated
#: walls of text in pull requests").
_NON_CODE_OBJECT = re.compile(rf"\b{_NON_CODE_NOUN}\b", re.IGNORECASE)
_AI_MADE_NON_CODE = re.compile(rf"{_AI_TERM}[\s-]+{_MADE}\s+{_NON_CODE_NOUN}\b", re.IGNORECASE)
_CODE_OBJECT = re.compile(rf"\b{_CODE_NOUN}\b", re.IGNORECASE)
#: RFC-0019 REGULATED: a sentence about AI-made work or the use of AI tools ...
_AI_USE = re.compile(
    rf"(?:{_AI_MADE}|\b(?:use|using|used|usage)\s+(?:of\s+)?(?:any\s+)?{_AI_TOOL}"
    rf"|{_AI_TERM}[\s-]+(?:assistance|assisted|tools?|assistants?|usage|use|coding)\b"
    rf"|{_AI_TERM}\s+(?:was|were|is|has\s+been|have\s+been)\s+used\b)",
    re.IGNORECASE)
#: ... that requires something of it: disclosure, attribution, review, testing,
#: understanding or responsibility ("AI-generated code must be disclosed"). Not
#: when a negation governs the requirement ("You don't need to disclose AI use").
AI_RULE_DIRECTIVE: re.Pattern = re.compile(
    r"\b(?:must|should|shall|need\s+to|needs\s+to|have\s+to|has\s+to|(?:is|are)\s+required\s+to"
    r"|required\s+to|please|make\s+sure|be\s+sure|ensure|expected\s+to)\b[^.!?;\n]{0,80}?"
    r"\b(?:disclos\w*|declar\w*|mention\w*|stat(?:e|ed|es|ing)\b|indicat\w*|label\w*|mark(?:ed|ing)?\b"
    r"|flag\w*|tag(?:ged|ging)?\b|acknowledg\w*|attribut\w*|not(?:e|ed|ing)\b|review\w*|test(?:ed|ing)?\b"
    r"|understand\w*|verif\w*|check(?:ed|ing)?\b|responsib\w*|explain\w*)",
    re.IGNORECASE)
_DIRECTIVE_IMPERATIVE = re.compile(
    r"^(?:please\s+)?(?:disclose|declare|mention|state|indicate|label|mark|note|tag|flag)\b", re.IGNORECASE)
_NEGATION_BEFORE = re.compile(r"(?:\b(?:not|no|never)\b|n't)\W+(?:\w+\W+){0,2}$", re.IGNORECASE)
#: RFC-0019 REGULATED: a named commit-trailer convention ("Assisted-by: <tool>").
AI_TRAILER_CONVENTION: re.Pattern = re.compile(r"\b(?:assisted|generated|ai[\s-]assisted)-by:", re.IGNORECASE)
#: RFC-0019 REGULATED: a heading that opens an AI-policy section ("## AI policy",
#: "## Use of AI tools", "## AI-assisted contributions").
AI_POLICY_HEADING: re.Pattern = re.compile(
    rf"^\s{{0,3}}#{{1,6}}\s+[^\n]*?(?:{_AI_TERM}[\s-]+(?:polic(?:y|ies)|usage|use|assistance|disclosure"
    rf"|guidelines?|rules?|contributions?)\b|{_AI_MADE}|\b(?:use|usage)\s+of\s+{_AI_TOOL}|\bpolicy\s+on\s+{_AI_TOOL})",
    re.IGNORECASE | re.MULTILINE)
#: RFC-0019 REGULATED: a checklist item about AI use ("- [ ] I used AI tools ...").
AI_CHECKBOX: re.Pattern = re.compile(rf"^\s*[-*+]\s+\[[ xX]\][^\n]*?{_AI_USE.pattern}",
                                     re.IGNORECASE | re.MULTILINE)
#: Sentence assembly for the patterns: abbreviations whose dot ends no sentence.
_ABBREVIATIONS = re.compile(r"\b(e\.g|i\.e|etc|vs|cf)\.", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+")
_BLOCK_START = re.compile(r"^(?:[-*+]\s|\d+[.)]\s|#{1,6}\s|\||>)")


# --------------------------------------------------------------------------- #
# Transports                                                                    #
# --------------------------------------------------------------------------- #

class RestTransport(Protocol):
    """One REST execution; the live implementation shells to ``gh api``."""

    def call(self, path: str, params: Mapping[str, str]) -> Any:
        """Run one ``gh api`` call and return the parsed JSON body."""


class GhApiError(RuntimeError):
    """A failed ``gh api`` call, with the HTTP status when the answer was
    one (``gh`` prints ``(... HTTP <code>)`` for HTTP error answers)."""

    def __init__(self, message: str, http_status: Optional[int] = None) -> None:
        super().__init__(message)
        self.http_status = http_status


class GhApiTransport:
    """Live REST transport via ``gh api`` (the gh auth token is used).

    Per ``gh api`` semantics, every value after the path is a parameter
    flag (``-f key=value``); a non-2xx answer exits non-zero and raises
    :class:`GhApiError` (``http_status`` set when the answer was an HTTP
    error, e.g. the 404 of a repo with issues/PRs disabled).
    """

    def __init__(self, timeout_seconds: int = 90) -> None:
        self.timeout_seconds = timeout_seconds

    def call(self, path: str, params: Mapping[str, str]) -> Any:
        """Run one ``gh api`` call and return the parsed JSON body.

        ``-X GET`` is explicit: ``gh api`` with ``-f`` fields would
        otherwise infer a write method for read endpoints (404).
        """
        validate.gh_api_path(path)
        args = ["api", "-X", "GET", path]
        for key, value in params.items():
            args += ["-f", f"{key}={value}"]
        out = ghcli.run(args, timeout=self.timeout_seconds)
        if out.returncode != 0:
            match = re.search(r"\(HTTP (\d+)\)", out.stderr)
            raise GhApiError(
                f"gh api {path} rc={out.returncode}: {out.stderr[:160]}",
                int(match.group(1)) if match else None)
        return json.loads(out.stdout) if out.stdout.strip() else None


#: ``fetch_file(path) -> body``; the live implementation reads
#: ``raw.githubusercontent.com`` at the repo HEAD.
FileFetcher = Callable[[str], Optional[str]]


#: Connect/read timeout for one raw.githubusercontent.com attempt, and how
#: many attempts a single file gets. MEASURED 2026-09-21: that host resolves
#: to four IPv4 anycast addresses and one of them (185.199.109.133) accepts
#: no SYN from this network, while the other three connect in ~0.05 s.
#: urllib uses whichever address getaddrinfo hands it and blocks for the
#: whole timeout -- it has no happy-eyeballs fallback, which is why `curl`
#: succeeded against the same host while this collector stalled (six
#: identical urllib fetches: 0.05/0.07/0.05/0.08/30.09/0.05 s).
#: collect_governance fetches 12 files per repo, so P(some attempt lands on
#: the dead address) = 1 - 0.75**12 = 97%, and because every non-404 raises,
#: one unlucky address failed the WHOLE repo: L3g run 3 logged "3882 clean,
#: 1871 with failures" and throughput fell from 6.00 to 0.50 repos/min.
#: The resolver rotates addresses, so a retry almost always lands on a live
#: one -- measured, the stalled file always succeeded on attempt 2.
#: 3 s is sized off the SUCCESS latency, not guessed: 17 clean fetches
#: measured min 0.047 s / median 0.058 s / max 0.088 s, so 3 s is 34x the
#: slowest real success and cannot cut a working response short, while
#: capping a dead-address stall at 3 s instead of 30 s. Observed stall rate
#: was 7 of 24 attempts (~29%, consistent with 1 of 4 addresses), so the
#: expected cost is 12 x 0.29 x 3 s = ~10 s/repo against the ~90 s that the
#: single 30 s timeout cost -- and 4 attempts still bound the worst case.
FETCH_FILE_TIMEOUT_SECONDS: int = 3
FETCH_FILE_ATTEMPTS: int = 4


def fetch_file_text(owner: str, name: str,
                    timeout_seconds: int = FETCH_FILE_TIMEOUT_SECONDS,
                    attempts: int = FETCH_FILE_ATTEMPTS) -> FileFetcher:
    """Live :data:`FileFetcher` for one repository's HEAD ref.

    Returns the UTF-8 body of the file at ``path``; ``None`` when the file
    does not exist (HTTP 404). Any other failure raises, so a transient
    error never gets cached as "absent" (the envelope checkpoint retries).

    A *transport* failure (no HTTP response: connect timeout, reset, DNS)
    is retried up to ``attempts`` times -- see
    :data:`FETCH_FILE_ATTEMPTS` for the measured reason. Retrying stops at
    the contract boundary in both directions: a 404 is an ANSWER and is
    returned immediately, any other HTTPError is the server answering and is
    raised immediately, and once the attempts are spent the transport error
    is re-raised rather than swallowed -- so this never converts "could not
    reach the CDN" into "the file is not there".
    """
    validate.github_segment(owner, "owner")
    validate.github_segment(name, "name")

    def fetch(path: str) -> Optional[str]:
        validate.relative_file_path(path, "governance file path")
        url = validate.https_url(f"https://raw.githubusercontent.com/{owner}/{name}/HEAD/{path}",
                                 validate.RAW_GITHUB_HOSTS, "governance file")
        req = urllib.request.Request(url, headers={"User-Agent": "porchlamp-research/0.1"})
        last: Exception = RuntimeError("no attempt made")
        for _ in range(max(1, attempts)):
            try:
                with net.urlopen(req, timeout_seconds) as r:
                    return r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                raise
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = e  # transport, not an HTTP answer -- try another address
        raise last
    return fetch


# --------------------------------------------------------------------------- #
# Recorded-response collection                                                  #
# --------------------------------------------------------------------------- #

def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp; naive values are taken as UTC."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _is_bot_row(row: Mapping[str, Any]) -> bool:
    """D-IN-1 over one recorded commit row (delegates to :mod:`porchlamp.collect.commits`)."""
    return commit_domains.is_bot(commit_domains.CommitActor(
        login=str(row.get("author_login") or ""),
        email=str(row.get("author_email") or ""),
        actor_type=str(row.get("author_type") or "")))


def _merged_prs(api: RestTransport, owner: str, name: str
                ) -> tuple[list[dict[str, Any]], bool]:
    """The up-to-:data:`MERGED_PR_CAP` most recent merged PRs, newest first.

    Pages ``repos/{owner}/{repo}/pulls?state=closed`` newest-created first
    (up to :data:`PULLS_PAGES_MAX` pages) and keeps the merged entries.
    Returns ``(merged, pulls_available)``: a 404 on the pulls endpoint is
    the recorded fact that the repository has issues/PRs disabled (no PR
    evidence, not a fetch failure); any other transport error propagates.
    """
    merged: list[dict[str, Any]] = []
    for page in range(1, PULLS_PAGES_MAX + 1):
        try:
            doc = api.call(f"repos/{owner}/{name}/pulls", {
            "state": "closed", "sort": "created", "direction": "desc",
                "per_page": str(PULLS_PAGE_SIZE), "page": str(page)}) or []
        except GhApiError as e:
            if e.http_status == 404:
                return [], False
            raise
        for pr in doc:
            if not pr.get("merged_at"):
                continue
            merged.append({
                "number": pr.get("number"),
                "created_at": pr.get("created_at"),
                "merged_at": pr.get("merged_at"),
                "author_login": (pr.get("user") or {}).get("login") or "",
                "head_sha": (pr.get("head") or {}).get("sha") or "",
            })
            if len(merged) >= MERGED_PR_CAP:
                return merged, True
        if len(doc) < PULLS_PAGE_SIZE:
            break
    return merged, True


def _distinct_head_shas(merged_prs: Sequence[Mapping[str, Any]]
                        ) -> list[tuple[str, Mapping[str, Any]]]:
    """``(sha, pr)`` for each distinct non-empty head SHA, newest merge first.

    Ordered by ``merged_at`` descending so "newest" is a fact rather than an
    accident of the pulls listing, which is sorted by CREATION date -- a pull
    request opened earlier can be merged later, so the two orders differ.
    Entries with no ``merged_at`` sort last rather than being dropped.
    """
    ordered = sorted(
        merged_prs,
        key=lambda pr: (str(pr.get("merged_at") or ""), str(pr.get("number") or "")),
        reverse=True,
    )
    out: list[tuple[str, Mapping[str, Any]]] = []
    seen: set[str] = set()
    for pr in ordered:
        sha = str(pr.get("head_sha") or "")
        if not sha or sha in seen:
            continue
        seen.add(sha)
        out.append((sha, pr))
    return out


def _check_evidence(api: RestTransport, owner: str, name: str,
                    merged_prs: Sequence[Mapping[str, Any]]
                    ) -> tuple[list[dict[str, Any]], int, int]:
    """Status contexts and check-run names on merged PRs' head commits.

    Returns ``(checks, shas_probed, shas_available)``. Two REST calls per
    probed SHA -- ``/commits/{sha}/statuses`` and ``/commits/{sha}/check-runs``
    (GitHub records EasyCLA as a status context and cla-assistant/google-cla as
    check runs; both are 3.1 evidence) -- over at most
    :data:`CHECK_EVIDENCE_SHA_CAP` distinct SHAs, newest merge first (D-COL-12).
    ``shas_available`` is how many distinct SHAs there were to choose from, so
    a reader can tell "looked at 5 of 5" from "looked at 5 of 20"; a SHA whose
    commit 404s is skipped and not counted as probed.
    """
    checks: list[dict[str, Any]] = []
    distinct = _distinct_head_shas(merged_prs)
    shas_available = len(distinct)
    shas_probed = 0
    for sha, pr in distinct[:CHECK_EVIDENCE_SHA_CAP]:
        try:
            statuses = api.call(f"repos/{owner}/{name}/commits/{sha}/statuses",
                                {"per_page": "100"}) or []
            runs = api.call(f"repos/{owner}/{name}/commits/{sha}/check-runs",
                            {"per_page": "100"}) or {}
        except GhApiError as e:
            if e.http_status == 404:
                continue
            raise
        shas_probed += 1
        for status in statuses:
            checks.append({
                "name": status.get("context") or "",
                "kind": "status",
                "state": status.get("state"),
                "conclusion": None,
                "pr": pr.get("number"),
                "sha": sha,
            })
        for run in runs.get("check_runs") or []:
            checks.append({
                "name": run.get("name") or "",
                "kind": "check_run",
                "state": run.get("status"),
                "conclusion": run.get("conclusion"),
                "pr": pr.get("number"),
                "sha": sha,
            })
    return checks, shas_probed, shas_available


#: GraphQL twin of :func:`_merged_prs`: the same listing -- closed AND merged PRs, newest
#: CREATED first, pages of :data:`PULLS_PAGE_SIZE`, at most :data:`PULLS_PAGES_MAX`
#: pages, keeping the first :data:`MERGED_PR_CAP` merged ones. (Ordering by UPDATED_AT
#: sampled a different 20 PRs: overlap with REST was 2-11 of 20 on the 2026-10-06 review.)
GOVERNANCE_PRS_QUERY: str = (
    "query GovernanceMergedPrs($owner: String!, $name: String!, $first: Int!, $after: String) {"
    " rateLimit { cost remaining }"
    " r: repository(owner: $owner, name: $name) {"
    " pullRequests(states: [CLOSED, MERGED], first: $first, after: $after,"
    " orderBy: {field: CREATED_AT, direction: DESC}) {"
    " pageInfo { hasNextPage endCursor }"
    " nodes { number createdAt mergedAt author { login } headRefOid } } } }"
)

#: Check evidence of the probed head SHAs, one aliased ``object`` per SHA. ``status.contexts``
#: lists the commit's status contexts (EasyCLA ...) and ``checkSuites { checkRuns }`` every
#: check run (cla-assistant, google-cla ...), like the REST ``/statuses`` and ``/check-runs``.
#: ``statusCheckRollup`` is NOT used: it keeps 100 contexts and drops runs REST lists.
GOVERNANCE_CHECK_SUITES_FIRST: int = 50
GOVERNANCE_CHECK_RUNS_FIRST: int = 100

#: The REST path reads ``/statuses`` and ``/check-runs`` with ``per_page=100`` and no further
#: page, so a commit with more entries is TRUNCATED there. GraphQL would return more and the
#: two paths would disagree on the check names; for such a commit the REST path is used, so
#: the GraphQL path never differs from it.
REST_CHECKS_PER_PAGE: int = 100


def build_governance_checks_query(count: int) -> str:
    """The aliased check-evidence query for ``count`` head SHAs (``$s0..``, parameterized)."""
    decls = ", ".join(f"$s{i}: GitObjectID!" for i in range(count))
    aliases = " ".join(
        f"c{i}: object(oid: $s{i}) {{ ... on Commit {{"
        " status { contexts { context state } }"
        f" checkSuites(first: {GOVERNANCE_CHECK_SUITES_FIRST}) {{"
        " pageInfo { hasNextPage }"
        f" nodes {{ checkRuns(first: {GOVERNANCE_CHECK_RUNS_FIRST}) {{"
        " pageInfo { hasNextPage } nodes { name status conclusion } } } } } }"
        for i in range(count)
    )
    return (
        f"query GovernanceChecks($owner: String!, $name: String!, {decls}) {{"
        f" rateLimit {{ cost remaining }} r: repository(owner: $owner, name: $name) {{ {aliases} }} }}"
    )


def _graphql_repo(doc: Any, owner: str, name: str, what: str) -> Optional[Mapping[str, Any]]:
    """The ``r`` repository object of a GraphQL answer; ``None`` when GitHub says
    NOT_FOUND; any other missing answer is an error (never an empty result)."""
    data = doc.get("data") if isinstance(doc, Mapping) else None
    repo = data.get("r") if isinstance(data, Mapping) else None
    if isinstance(repo, Mapping):
        return repo
    errors = doc.get("errors") if isinstance(doc, Mapping) else None
    if errors and any(
        "NOT_FOUND" in str(e.get("type") or "") for e in errors if isinstance(e, Mapping)
    ):
        return None
    raise RuntimeError(f"governance graphql {what} failed for {owner}/{name}: {str(errors)[:160]}")


def _merged_prs_graphql(
    transport: pr_window.GraphqlTransport, owner: str, name: str
) -> tuple[list[dict[str, Any]], bool]:
    """GraphQL twin of :func:`_merged_prs` (same sample, same record schema)."""
    merged: list[dict[str, Any]] = []
    after: Optional[str] = None
    for _page in range(PULLS_PAGES_MAX):
        variables: dict[str, Any] = {"owner": owner, "name": name, "first": PULLS_PAGE_SIZE}
        if after is not None:
            variables["after"] = after
        repo = _graphql_repo(transport.execute(GOVERNANCE_PRS_QUERY, variables), owner, name, "pulls")
        if repo is None:
            return [], False
        conn = repo.get("pullRequests") or {}
        for pr in conn.get("nodes") or []:
            if not isinstance(pr, Mapping) or not pr.get("mergedAt"):
                continue
            merged.append({
                "number": pr.get("number"),
                "created_at": pr.get("createdAt"),
                "merged_at": pr.get("mergedAt"),
                "author_login": str((pr.get("author") or {}).get("login") or ""),
                "head_sha": str(pr.get("headRefOid") or ""),
            })
            if len(merged) >= MERGED_PR_CAP:
                return merged, True
        page_info = conn.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")
        if not after:
            break
    return merged, True


def _commit_checks_graphql(commit: Mapping[str, Any], pr_number: Any, sha: str
                           ) -> tuple[list[dict[str, Any]], bool]:
    """Check rows of one commit, shaped like :func:`_check_evidence`'s; the flag is
    False when GraphQL could not list every check run (a connection has a next page) or
    the commit has more statuses / check runs than the REST path reads (see
    :data:`REST_CHECKS_PER_PAGE`)."""
    rows: list[dict[str, Any]] = []
    complete = True
    contexts = ((commit.get("status") or {}).get("contexts")) or []
    for ctx in contexts:
        rows.append({
            "name": str(ctx.get("context") or ""),
            "kind": "status",
            "state": str(ctx.get("state") or "").lower() or None,
            "conclusion": None,
            "pr": pr_number,
            "sha": sha,
        })
    suites = commit.get("checkSuites") or {}
    if (suites.get("pageInfo") or {}).get("hasNextPage"):
        complete = False
    for suite in suites.get("nodes") or []:
        runs = (suite or {}).get("checkRuns") or {}
        if (runs.get("pageInfo") or {}).get("hasNextPage"):
            complete = False
        for run in runs.get("nodes") or []:
            rows.append({
                "name": str(run.get("name") or ""),
                "kind": "check_run",
                "state": str(run.get("status") or "").lower() or None,
                "conclusion": (str(run["conclusion"]).lower()
                               if run.get("conclusion") is not None else None),
                "pr": pr_number,
                "sha": sha,
            })
    n_status = sum(1 for r in rows if r["kind"] == "status")
    if n_status >= REST_CHECKS_PER_PAGE or len(rows) - n_status > REST_CHECKS_PER_PAGE:
        complete = False
    return rows, complete


def _check_evidence_graphql(
    transport: pr_window.GraphqlTransport,
    owner: str,
    name: str,
    merged_prs: Sequence[Mapping[str, Any]],
    api: Optional[RestTransport] = None,
) -> tuple[list[dict[str, Any]], int, int]:
    """GraphQL twin of :func:`_check_evidence`: same SHAs (newest merge first, at most
    :data:`CHECK_EVIDENCE_SHA_CAP`), same rows. A SHA whose check runs GraphQL cannot
    list in full, or that has more than REST reads (:data:`REST_CHECKS_PER_PAGE`), is read
    through the REST path instead (``api``), so the two paths cannot disagree; without
    ``api`` that is an error. A SHA GitHub does not know is
    skipped and not counted as probed, as a REST 404 is."""
    distinct = _distinct_head_shas(merged_prs)
    chosen = distinct[:CHECK_EVIDENCE_SHA_CAP]
    checks: list[dict[str, Any]] = []
    probed = 0
    if chosen:
        variables: dict[str, Any] = {"owner": owner, "name": name}
        for i, (sha, _pr) in enumerate(chosen):
            variables[f"s{i}"] = sha
        repo = _graphql_repo(
            transport.execute(build_governance_checks_query(len(chosen)), variables),
            owner, name, "checks")
        if repo is None:
            return [], 0, len(distinct)
        for i, (sha, pr_row) in enumerate(chosen):
            commit = repo.get(f"c{i}")
            if not isinstance(commit, Mapping) or not commit:
                continue
            rows, complete = _commit_checks_graphql(commit, pr_row.get("number"), sha)
            if not complete:
                if api is None:
                    raise RuntimeError(
                        f"governance graphql cannot list every check run of {owner}/{name}@{sha}")
                rest_rows, rest_probed, _avail = _check_evidence(api, owner, name, [pr_row])
                checks.extend(rest_rows)
                probed += rest_probed
                continue
            probed += 1
            checks.extend(rows)
    return checks, probed, len(distinct)


def fetch_merged_prs_and_checks_graphql(
    transport: pr_window.GraphqlTransport,
    owner: str,
    name: str,
    api: Optional[RestTransport] = None,
) -> tuple[list[dict[str, Any]], bool, list[dict[str, Any]], int, int]:
    """Merged PRs and their head-SHA check/status evidence over GraphQL.

    Returns ``(merged_prs, pulls_available, checks, shas_probed, shas_available)``
    with the same record schema, the same PR sample (closed PRs newest-created
    first, merged ones, cap :data:`MERGED_PR_CAP`) and the same probed SHAs as
    :func:`_merged_prs` + :func:`_check_evidence`: 1-2 GraphQL queries instead of
    11-15 REST calls.
    """
    merged_prs, pulls_available = _merged_prs_graphql(transport, owner, name)
    if not pulls_available:
        return [], False, [], 0, 0
    checks, probed, available = _check_evidence_graphql(transport, owner, name, merged_prs, api)
    return merged_prs, True, checks, probed, available


def _row_sequence(rows: Optional[Any]) -> Optional[Sequence[Mapping[str, Any]]]:
    """Defend the ``c365`` (commits_365d) contract: a sequence of row
    mappings, never a bare mapping itself (a dict-shaped ``c365`` iterates
    over its *keys*, not its rows, and the
    first ``row.get(...)`` then raises ``AttributeError`` on a plain string).
    A mapping is the collector's own window dict when it carries ``rows``
    (``commit_domains.envelope_rows`` unwraps it -- the shape
    ``collect_commits_365d`` records); returns ``None`` for ``None`` or any
    other mapping, so every caller below treats it exactly like "no rows"
    instead of crashing ``derive_repo``.
    """
    if rows is None:
        return None
    if isinstance(rows, Mapping):
        return commit_domains.envelope_rows(rows)
    return rows


def signoff_share(c365_rows: Optional[Sequence[Mapping[str, Any]]]) -> Optional[dict[str, Any]]:
    """02 §4 3.1: the ``Signed-off-by`` share over the non-bot commits (D-IN-1).

    ``c365_rows`` are the recorded ``commits_365d`` envelope rows (read by
    the ``scripts/derive.py`` hook and passed straight through -- no
    separate fetch), or that envelope's window dict; each row's
    ``signed_off`` boolean comes from
    :func:`porchlamp.collect.commits.shape_commit_rows`.
    Returns ``None`` when no ``commits_365d`` envelope was collected at all
    (as opposed to an envelope with zero rows, which returns a share of
    ``None`` inside a real ``{"count": 0, "total": 0, "share": None}``), and
    also when ``c365_rows`` is a malformed (bare-mapping) shape, and when it
    is a record cut at the pre-RFC-0021 newest-1,000 cap
    (``commit_domains.commit_window``): the newest days of a busier year are
    not the window's commits. An evenly spaced sample of the window (RFC-0021)
    is read like the whole window: every sampled page stands for the same
    share of it.
    """
    rows_seq = _row_sequence(c365_rows)
    if rows_seq is None:
        return None
    window = commit_domains.commit_window(c365_rows)
    if window is not None and window.cut:
        return None
    rows = list(rows_seq)
    non_bot = [row for row in rows if not _is_bot_row(row)]
    total = len(non_bot)
    count = sum(1 for row in non_bot if row.get("signed_off"))
    share = round(count / total, 4) if total else None
    return {"count": count, "total": total, "share": share}


def _read_tree_paths(raw_dir: Path, repo_key: str) -> Optional[set[str]]:
    """Non-truncated tree paths from ``raw_dir/tree/<repo_key>.json``, or None."""
    tree_envelope_path = raw_dir / "tree" / f"{repo_key}.json"
    if not tree_envelope_path.is_file():
        return None
    try:
        tree_env = read_envelope(tree_envelope_path)
        if (
            isinstance(tree_env.data, Mapping)
            and tree_env.data.get("truncated") is False
            and isinstance(tree_env.data.get("paths"), list)
        ):
            return {str(p) for p in tree_env.data["paths"] if p}
    except Exception:
        return None
    return None


def resolve_governance_path(canonical: str, available_paths: Iterable[str]) -> Optional[str]:
    """C5b: resolve a canonical :data:`GOVERNANCE_FILES` name across GitHub's
    documented community-health directories (``.github/`` > root > ``docs/``),
    case-insensitively and across :data:`GOVERNANCE_EXTENSIONS`
    (``.md`` > ``.rst`` > ``.txt`` > extensionless).

    Returns the exact path spelling from ``available_paths`` that wins
    precedence, or ``None`` when no matching file exists.
    """
    if canonical not in GOVERNANCE_FILES:
        return None
    stem = canonical.rsplit(".", 1)[0].lower()
    by_lower: dict[str, str] = {}
    for raw_path in sorted(str(p) for p in available_paths if p):
        low = raw_path.lower()
        if low not in by_lower:
            by_lower[low] = raw_path
    for directory in COMMUNITY_HEALTH_DIRS:
        prefix = f"{directory}/" if directory else ""
        for ext in GOVERNANCE_EXTENSIONS:
            candidate = f"{prefix}{stem}{ext}"
            if candidate in by_lower:
                return by_lower[candidate]
    return None


def resolve_policy_path(canonical: str, available_paths: Iterable[str]) -> Optional[str]:
    """Resolve any :data:`POLICY_FILES` entry against ``available_paths``.

    :data:`GOVERNANCE_FILES` use :func:`resolve_governance_path`
    (``.github/`` > root > ``docs/``, case-insensitive, ``.md``/``.rst``/``.txt``/``""``);
    :data:`AI_POLICY_FILES` match their canonical path first, then case-insensitively.
    """
    if canonical in GOVERNANCE_FILES:
        return resolve_governance_path(canonical, available_paths)
    paths = [str(p) for p in available_paths if p]
    if canonical in paths:
        return canonical
    target_low = canonical.lower()
    for p in sorted(paths):
        if p.lower() == target_low:
            return p
    return None


def policy_file_body(
    files: Mapping[str, Optional[Mapping[str, Any]]],
    canonical: str,
) -> Optional[tuple[str, str]]:
    """Return ``(resolved_path, body)`` for ``canonical`` from ``files``, or ``None``.

    Supports both canonical-keyed ``files`` dicts (as written by
    :func:`collect_governance`, with optional ``"path"`` metadata) and raw
    path-keyed dicts (e.g. ``{".github/GOVERNANCE.rst": {"body": ...}}`` or
    org-default keys ``{"acme/.github:CONTRIBUTING.md": {"body": ...}}``).
    """
    direct = files.get(canonical)
    if isinstance(direct, Mapping) and direct.get("body") is not None:
        return str(direct.get("path") or canonical), str(direct.get("body") or "")
    local_keys = [k for k, v in files.items() if isinstance(v, Mapping) and v.get("body") is not None and ":" not in k]
    matched_local = resolve_policy_path(canonical, local_keys)
    if matched_local is not None:
        entry = files[matched_local]
        assert isinstance(entry, Mapping)
        return matched_local, str(entry.get("body") or "")
    if canonical in ORG_DEFAULT_FILES:
        org_map: dict[str, str] = {}
        for k, v in files.items():
            if isinstance(v, Mapping) and v.get("body") is not None and "/.github:" in k:
                rel = k.split(":", 1)[1]
                org_map[rel] = k
        matched_org_rel = resolve_governance_path(canonical, org_map.keys())
        if matched_org_rel is not None:
            full_key = org_map[matched_org_rel]
            entry = files[full_key]
            assert isinstance(entry, Mapping)
            return full_key, str(entry.get("body") or "")
    return None


_ORG_DEFAULT_FILE_CACHE: dict[tuple[int, int, int, str, str], Optional[str]] = {}
_ORG_DEFAULT_CACHE_LOCK = threading.Lock()


def clear_org_default_file_cache() -> None:
    """Clear the per-owner ``<owner>/.github`` org-default file cache."""
    with _ORG_DEFAULT_CACHE_LOCK:
        _ORG_DEFAULT_FILE_CACHE.clear()


def _cached_org_file_fetcher(owner: str) -> FileFetcher:
    """Return a thread-safe cached :data:`FileFetcher` for ``<owner>/.github``."""
    raw_fetcher = fetch_file_text(owner, ".github")
    scope_ids = (id(fetch_file_text), id(net._gate), id(urllib.request.urlopen))

    def fetch(path: str) -> Optional[str]:
        cache_key = (*scope_ids, owner, path)
        with _ORG_DEFAULT_CACHE_LOCK:
            if cache_key in _ORG_DEFAULT_FILE_CACHE:
                return _ORG_DEFAULT_FILE_CACHE[cache_key]
        body = raw_fetcher(path)
        with _ORG_DEFAULT_CACHE_LOCK:
            _ORG_DEFAULT_FILE_CACHE[cache_key] = body
        return body

    return fetch


def _reusable_merged_prs_from_pr_window(
    raw_dir: Path, repo_key: str
) -> Optional[list[dict[str, Any]]]:
    """Reuse merged PRs from ``raw_dir/pr_window/<repo_key>.json`` when complete (OPT-5).

    Returns a list of up to :data:`MERGED_PR_CAP` merged PR dicts when ``pr_window``
    is uncut, not reply-sampled, and either:
    - ``window_exhausted`` is True with no ``boundary_overflow_prs`` (all-time PR history
      is inside the window) and there are 0 merged PRs in the repository, or
    - at least :data:`MERGED_PR_CAP` merged PRs (or all merged PRs on an all-time exhausted
      window with no boundary overflow) already carry ``headRefOid`` / ``head_sha``.
    Returns ``None`` when ``governance`` must query GitHub directly.
    """
    prw_path = raw_dir / "pr_window" / f"{repo_key}.json"
    if not prw_path.is_file():
        return None
    try:
        prw_env = read_envelope(prw_path)
    except Exception:
        return None
    data = prw_env.data
    if not isinstance(data, Mapping) or pr_window.pr_window_cut_short(data):
        return None
    if int(data.get("reply_sampled_prs") or 0) > 0:
        return None
    prs = data.get("prs")
    if not isinstance(prs, list):
        return None
    all_time_exhausted = (
        data.get("window_exhausted") is True
        and not data.get("boundary_overflow_prs")
    )
    merged_nodes = [
        pr for pr in prs
        if isinstance(pr, Mapping) and (pr.get("mergedAt") or pr.get("merged_at"))
    ]
    if not merged_nodes:
        return [] if all_time_exhausted else None
    sample = merged_nodes[:MERGED_PR_CAP]
    if len(sample) < MERGED_PR_CAP and not all_time_exhausted:
        return None
    shaped: list[dict[str, Any]] = []
    for pr in sample:
        sha = str(pr.get("headRefOid") or pr.get("head_sha") or "")
        if not sha:
            return None
        author = pr.get("author")
        login = (
            str(author.get("login") or "")
            if isinstance(author, Mapping)
            else str(pr.get("author_login") or "")
        )
        shaped.append({
            "number": pr.get("number"),
            "created_at": pr.get("createdAt") or pr.get("created_at"),
            "merged_at": pr.get("mergedAt") or pr.get("merged_at"),
            "author_login": login,
            "head_sha": sha,
        })
    return shaped


def collect_governance(raw_dir: Path, owner: str, name: str,
                       now: Optional[datetime] = None,
                       api: Optional[RestTransport] = None,
                       fetch_file: Optional[FileFetcher] = None,
                       force: bool = False,
                       graphql_transport: Optional[pr_window.GraphqlTransport] = None,
                       org_tree_paths: Optional[Iterable[str]] = None,
                       fetch_org_file: Optional[FileFetcher] = None) -> str:
    """Collect the governance evidence envelope for one repository.

    Writes ``raw_dir/governance/<owner>__<name>.json`` — one envelope whose
    ``data`` carries the up-to-:data:`MERGED_PR_CAP` most recent merged
    PRs, the check/status evidence on their head commits, and the
    governance/AI-policy file bodies — and returns ``"ok"``. The
    ``Signed-off-by`` share is *not* collected here: it is computed at
    derive time from the already-collected ``commits_365d`` envelope (see
    :func:`signoff_share`), so this collector never fetches commits and
    never stores message text. Returns ``"skip"`` when the envelope
    already exists and ``force`` is False. ``api`` / ``fetch_file`` /
    ``graphql_transport`` are injectable transports for the recorded-response
    oracles; when ``graphql_transport`` is given, the same merged-PR sample and
    check evidence are fetched in 1-2 GraphQL queries instead of 11-15 REST calls
    (REST is still used for a SHA with more check runs than one GraphQL page or one REST page).
    """
    key = f"{owner}__{name}"
    target = raw_dir / "governance" / f"{key}.json"
    if target.is_file() and not force:
        return "skip"
    now = now or datetime.now(timezone.utc)
    fetch_file = fetch_file or fetch_file_text(owner, name)

    reused_merged = _reusable_merged_prs_from_pr_window(raw_dir, key)
    if graphql_transport is not None:
        if reused_merged is not None:
            merged_prs = reused_merged
            pulls_available = True
            checks, shas_probed, shas_available = _check_evidence_graphql(
                graphql_transport, owner, name, merged_prs, api=api or GhApiTransport()
            )
        else:
            merged_prs, pulls_available, checks, shas_probed, shas_available = (
                fetch_merged_prs_and_checks_graphql(
                    graphql_transport, owner, name, api=api or GhApiTransport())
            )
        source_tag = "github-graphql:governance"
    else:
        api = api or GhApiTransport()
        if reused_merged is not None:
            merged_prs = reused_merged
            pulls_available = True
        else:
            merged_prs, pulls_available = _merged_prs(api, owner, name)
        checks, shas_probed, shas_available = _check_evidence(api, owner, name, merged_prs)
        source_tag = "github-rest:governance"
    tree_paths = _read_tree_paths(raw_dir, key)
    resolved_org_tree: Optional[set[str]] = (
        {str(p) for p in org_tree_paths if p}
        if org_tree_paths is not None
        else _read_tree_paths(raw_dir, f"{owner}__.github")
    )

    files: dict[str, Optional[dict[str, Any]]] = {}
    for fname in POLICY_FILES:
        rel_path: Optional[str]
        if tree_paths is not None:
            rel_path = resolve_policy_path(fname, tree_paths)
            body = fetch_file(rel_path) if rel_path is not None else None
        else:
            rel_path = fname
            body = fetch_file(fname)
        if body is not None and rel_path is not None:
            entry: dict[str, Any] = {
                "present": True,
                "sha256": sha256_hex(body),
                "body": body,
            }
            if rel_path != fname:
                entry["path"] = rel_path
            files[fname] = entry
            continue
        if fname in ORG_DEFAULT_FILES and (resolved_org_tree is not None or fetch_org_file is not None):
            org_fetcher = fetch_org_file or _cached_org_file_fetcher(owner)
            org_rel: Optional[str] = None
            org_body: Optional[str] = None
            if resolved_org_tree is not None:
                org_rel = resolve_governance_path(fname, resolved_org_tree)
                if org_rel is not None:
                    org_body = org_fetcher(org_rel)
            else:
                for cand_dir in COMMUNITY_HEALTH_DIRS:
                    cand = f"{cand_dir}/{fname}" if cand_dir else fname
                    org_body = org_fetcher(cand)
                    if org_body is not None:
                        org_rel = cand
                        break
            if org_body is not None and org_rel is not None:
                files[fname] = {
                    "present": True,
                    "path": f"{owner}/.github:{org_rel}",
                    "org_default": True,
                    "sha256": sha256_hex(org_body),
                    "body": org_body,
                }
                continue
        files[fname] = None
    data: dict[str, Any] = {
        "pulls_available": pulls_available,
        "merged_pr_cap": MERGED_PR_CAP,
        "merged_prs": merged_prs,
        "checks": checks,
        # D-COL-12: what the check evidence actually rests on.
        "check_sha_cap": CHECK_EVIDENCE_SHA_CAP,
        "checks_shas_probed": shas_probed,
        "checks_shas_available": shas_available,
        "check_names": sorted({c["name"] for c in checks if c["name"]}),
        "files": files,
    }
    write_envelope(target, source=source_tag, data=data,
                   fetched_at=now.isoformat())
    return "ok"


# --------------------------------------------------------------------------- #
# 02 §4 classification (called from the scripts/derive.py hook)               #
# --------------------------------------------------------------------------- #

def is_foundation_owner(owner: str) -> bool:
    """RFC-0017: True when ``owner`` (case-insensitively) is in :data:`FOUNDATION_ORGS`."""
    return str(owner or "").strip().lower() in FOUNDATION_ORGS


def classify_legal_ip(check_names: Sequence[str], signoff: Optional[Mapping[str, Any]],
                      foundation_owner: bool = False) -> tuple[str, dict[str, Any]]:
    """02 §4 3.1 (RFC-0017): the evidence-based legal IP regime.

    ``check_names`` are the observed check-run names / status contexts,
    ``signoff`` the recorded ``{"count", "total", "share"}`` (None when no
    commit evidence), ``foundation_owner`` the owner-in-:data:`FOUNDATION_ORGS`
    fact. Returns ``(value, evidence)`` with ``value`` in
    :data:`porchlamp.core.constants.LEGAL_IP_VALUES`; the evidence dict is
    factual (``03`` §3: documents are not evidence) and carries the fired
    ``rule``.

    Precedence (RFC-0017):
    1. ``foundation_cla_check`` -> ``FOUNDATION_CLA``
    2. ``foundation_owner_cla_check`` (generic CLA check on a
       :data:`FOUNDATION_ORGS` owner) -> ``FOUNDATION_CLA``
    3. ``corp_cla_check`` -> ``CORP_CLA``
    4. ``dco_check`` -> ``DCO``
    5. ``signoff_share`` (>= :data:`SIGNOFF_THRESHOLD`) -> ``DCO``
    6. ``foundation_owner`` (owner in :data:`FOUNDATION_ORGS` with no CLA check
       and no DCO evidence) -> ``FOUNDATION_CLA``
    7. ``no_evidence`` -> ``NONE``
    """
    names = sorted({str(n) for n in check_names if n})
    lowered = [n.lower() for n in names]
    evidence: dict[str, Any] = {
        "check_names": names,
        "signoff": dict(signoff) if signoff else None,
        "foundation_owner": bool(foundation_owner),
    }
    if any(p.search(n) for n in lowered for p in FOUNDATION_CHECK_PATTERNS):
        return "FOUNDATION_CLA", {**evidence, "rule": "foundation_cla_check"}
    if any(p.search(n) for n in lowered for p in CORP_CLA_CHECK_PATTERNS):
        if foundation_owner:
            return "FOUNDATION_CLA", {**evidence, "rule": "foundation_owner_cla_check"}
        return "CORP_CLA", {**evidence, "rule": "corp_cla_check"}
    if any(DCO_CHECK_PATTERN.search(n) for n in lowered):
        return "DCO", {**evidence, "rule": "dco_check"}
    if signoff and int(signoff.get("total") or 0) > 0 and \
            (int(signoff.get("count") or 0) / int(signoff["total"])) >= SIGNOFF_THRESHOLD:
        return "DCO", {**evidence, "rule": "signoff_share"}
    if foundation_owner:
        return "FOUNDATION_CLA", {**evidence, "rule": "foundation_owner"}
    return "NONE", {**evidence, "rule": "no_evidence"}


def _orgs_by_login(c365: Optional[Sequence[Mapping[str, Any]]]) -> dict[str, str]:
    """author_login -> email-domain org over the commits_365d envelope rows.

    ``c365`` must be a row sequence; a malformed bare-mapping shape is
    treated as no rows via :func:`_row_sequence`, never crashed on.
    """
    orgs: dict[str, str] = {}
    for row in (_row_sequence(c365) or []):
        login = str(row.get("author_login") or "").strip()
        if not login or login in orgs:
            continue
        org = commit_domains.org_key(commit_domains.CommitActor(
            login=login, email=str(row.get("author_email") or ""),
            actor_type=str(row.get("author_type") or "")))
        if org:
            orgs[login] = org
    return orgs


def _nomination_match(files: Mapping[str, Optional[Mapping[str, Any]]]
                      ) -> Optional[dict[str, str]]:
    """The first nomination-process line in a 3.3 governance file body."""
    for fname in ("GOVERNANCE.md", "MAINTAINERS.md", "CONTRIBUTING.md"):
        resolved = policy_file_body(files, fname)
        if resolved is None:
            continue
        resolved_path, body = resolved
        for line in body.splitlines():
            if NOMINATION_PATTERN.search(line):
                out = {"file": fname, "line": line.strip()[:200]}
                if resolved_path != fname:
                    out["path"] = resolved_path
                return out
    return None


def observed_promotions(prs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """RFC-0016: action-based outsider-to-maintainer transitions in ``prs``.

    GitHub evaluates ``PullRequest.authorAssociation`` at query time rather
    than at PR creation time, so every PR by a given login in ``pr_window``
    carries the same current association and an association transition is
    structurally unobservable in a single snapshot. Instead, ``VERIFIED``
    is measured from timestamped repository actions already recorded by
    :data:`porchlamp.collect.github_graphql.PR_WINDOW_QUERY`:

    1. **Access actions (D-IN-2)**: for every non-bot human actor
       (:func:`porchlamp.core.gates.is_bot_actor_node`), collect all
       merge actions (``mergedBy`` at ``mergedAt``, including self-merges)
       and non-author close actions (``closedEvents[].actor`` at
       ``closedEvents[].createdAt`` where ``actor != author``). Any actor
       with an access action missing a parseable timestamp is disqualified.
    2. **Maintainer action on another author's PR**: the candidate ``who``
       must merge or close at least one PR authored by another non-empty
       login (``pr_author != who``). ``first_access`` is the earliest
       timestamp across all of ``who``'s access actions (including any
       self-merge).
    3. **Prior outside-contributor phase (D-IN-3)**: strictly before
       ``first_access``, ``who`` must have authored at least one merged PR
       in ``prs`` whose account age at ``pr.createdAt`` is at least
       :data:`MIN_ACCOUNT_AGE_DAYS` (90 d) and that was merged by another
       non-bot human (``mergedBy.login != who``).
    4. **Minimum outside phase duration**: ``first_access -
       first_outside_merged_at >= timedelta(days=PROMOTION_MIN_OUTSIDE_DAYS)``
       (30 d), separating genuine outsider-to-maintainer promotions from
       existing maintainers whose first merge action in the window happens
       shortly after a teammate merged one of their PRs.
    """
    access_events: dict[str, list[tuple[datetime, str, str, Any]]] = {}
    unparseable_access: set[str] = set()
    outside_merges: dict[str, list[tuple[datetime, datetime, str, Any]]] = {}

    for pr in prs:
        if not isinstance(pr, Mapping):
            continue
        author = pr.get("author") if isinstance(pr.get("author"), Mapping) else {}
        pr_author = str((author or {}).get("login") or "").strip()
        pr_num = pr.get("number")

        merged_by = pr.get("mergedBy")
        if isinstance(merged_by, Mapping):
            m_login = str(merged_by.get("login") or "").strip()
            if m_login and not gates.is_bot_actor_node(merged_by):
                m_at = _parse_iso(pr.get("mergedAt"))
                if m_at is None:
                    unparseable_access.add(m_login)
                else:
                    access_events.setdefault(m_login, []).append(
                        (m_at, "merge", pr_author, pr_num)
                    )

        for ev in pr.get("closedEvents") or ():
            if not isinstance(ev, Mapping):
                continue
            actor = ev.get("actor")
            if not isinstance(actor, Mapping):
                continue
            c_login = str(actor.get("login") or "").strip()
            if not c_login or c_login == pr_author or gates.is_bot_actor_node(actor):
                continue
            c_at = _parse_iso(ev.get("createdAt"))
            if c_at is None:
                unparseable_access.add(c_login)
            else:
                access_events.setdefault(c_login, []).append(
                    (c_at, "close", pr_author, pr_num)
                )

        if not pr_author or gates.is_bot_actor_node(author):
            continue
        m_at = _parse_iso(pr.get("mergedAt"))
        c_at = _parse_iso(pr.get("createdAt"))
        a_created = _parse_iso((author or {}).get("createdAt"))
        if m_at is None or c_at is None or a_created is None:
            continue
        if (c_at - a_created) < timedelta(days=MIN_ACCOUNT_AGE_DAYS):
            continue
        if not isinstance(merged_by, Mapping):
            continue
        m_login = str(merged_by.get("login") or "").strip()
        if not m_login or m_login == pr_author or gates.is_bot_actor_node(merged_by):
            continue
        outside_merges.setdefault(pr_author, []).append((m_at, c_at, m_login, pr_num))

    min_span = timedelta(days=PROMOTION_MIN_OUTSIDE_DAYS)
    promoted: list[dict[str, Any]] = []
    for who in sorted(access_events):
        if who in unparseable_access:
            continue
        events = access_events[who]
        maint_other = [e for e in events if e[2] and e[2] != who]
        if not maint_other:
            continue
        first_access_dt = min(e[0] for e in events)
        first_maint = min(maint_other, key=lambda e: (e[0], str(e[3] or "")))
        prior_outside = [
            m for m in outside_merges.get(who, ()) if m[0] < first_access_dt
        ]
        if not prior_outside:
            continue
        first_outside = min(prior_outside, key=lambda m: (m[0], str(m[3] or "")))
        outside_span = first_access_dt - first_outside[0]
        if outside_span < min_span:
            continue
        promoted.append({
            "login": who,
            "first_outside_pr": first_outside[3],
            "first_outside_merged_at": first_outside[0].isoformat(),
            "first_outside_merged_by": first_outside[2],
            "outside_merged_count": len(prior_outside),
            "first_maintainer_action": first_maint[1],
            "first_maintainer_pr": first_maint[3],
            "first_maintainer_at": first_access_dt.isoformat(),
            "outside_days": round(outside_span.total_seconds() / 86400.0, 2),
        })
    return promoted


def classify_promotion_ladder(prw: Optional[Mapping[str, Any]],
                              files: Mapping[str, Optional[Mapping[str, Any]]],
                              c365: Optional[Sequence[Mapping[str, Any]]]
                              ) -> tuple[str, dict[str, Any]]:
    """02 §4 3.3 (RFC-0016): the promotion ladder rung.

    ``prw`` is the recorded ``pr_window`` envelope data (None when
    absent), ``files`` the governance-file entries, ``c365`` the
    ``commits_365d`` envelope rows. Returns ``(value, evidence)``
    with ``value`` in
    :data:`porchlamp.core.constants.PROMOTION_LADDER_VALUES`; priority is
    VERIFIED > CLOSED > DOCUMENTED > AD_HOC (module docstring).
    """
    prs = list((prw or {}).get("prs") or [])
    evidence: dict[str, Any] = {"pr_window_envelope": prw is not None}
    prw_cut = pr_window.pr_window_cut_short(prw)
    if prw_cut:
        evidence["pr_window_cut_short"] = True

    promoted = observed_promotions(prs)
    evidence["promoted_accounts"] = promoted
    # RFC-0016: VERIFIED rests on the absence of earlier maintainer actions in
    # the 180 d window, which a cut pr_window record cannot show.
    if promoted and not prw_cut:
        return "VERIFIED", {**evidence, "rule": "verified_transition"}

    merged = [pr for pr in prs if pr.get("mergedAt")]
    evidence["merged_pr_count"] = len(merged)
    external_merges = sum(
        1 for pr in merged
        if str(pr.get("authorAssociation") or "NONE") in pr_window.EXTERNAL_ASSOCIATIONS)
    evidence["external_merges"] = external_merges
    evidence["merged_author_orgs"] = None
    orgs_by_login = _orgs_by_login(c365) if c365 is not None else {}
    if merged and external_merges == 0 and orgs_by_login:
        author_orgs: list[str] = []
        for pr in merged:
            login = str(((pr.get("author") or {}).get("login")) or "").strip()
            org = orgs_by_login.get(login)
            if not org:
                author_orgs = []
                break
            author_orgs.append(org)
        if author_orgs:
            evidence["merged_author_orgs"] = sorted(set(author_orgs))
            if len(set(author_orgs)) == 1 and not prw_cut:
                return "CLOSED", {**evidence, "rule": "closed_single_org"}
    match = _nomination_match(files or {})
    evidence["document_match"] = match
    if match:
        return "DOCUMENTED", {**evidence, "rule": "documented_nomination"}
    return "AD_HOC", {**evidence, "rule": "no_evidence"}


def _ai_rule_documents(files: Mapping[str, Optional[Mapping[str, Any]]]
                       ) -> list[tuple[str, str, bool]]:
    """RFC-0019: ``(path, plain text, is_policy_file)`` for every document 4.2
    reads, in path order: each recorded :data:`AI_POLICY_FILES` entry, and each
    recorded file whose lower-cased name is in :data:`AI_RULE_DOCUMENT_NAMES`
    (wherever the collector found it). An entry with no text body is skipped:
    it has no sentence to read."""
    docs: list[tuple[str, str, bool]] = []
    for path in sorted(files or {}):
        entry = (files or {})[path]
        body = entry.get("body") if isinstance(entry, Mapping) else None
        if not isinstance(body, str) or not body.strip():
            continue
        if path in AI_POLICY_FILES:
            docs.append((path, _plain_text(body), True))
        elif path.rsplit("/", 1)[-1].lower() in AI_RULE_DOCUMENT_NAMES:
            docs.append((path, _plain_text(body), False))
    return docs


def _plain_text(body: str) -> str:
    """A markdown body without the markup that splits a sentence's words: HTML
    comment fences (a pull-request template's instructions sit inside them),
    link targets, emphasis and code markers; an abbreviation's dots dropped so
    they end no sentence."""
    text = body.replace("<!--", " ").replace("-->", " ")
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*`]+", "", text)
    text = re.sub(r"(?<!\w)_+|_+(?!\w)", "", text)
    return _ABBREVIATIONS.sub(lambda m: m.group(1).replace(".", ""), text)


def _sentences(text: str) -> list[str]:
    """The sentences of a plain-text markdown body: a paragraph's hard-wrapped
    lines joined again, a heading its own block, a list item, table row or
    quote starting a new block; each block split after ``.``, ``!``, ``?`` or
    ``;``, with its list, heading or checkbox marker removed."""
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or _BLOCK_START.match(stripped):
            if current:
                blocks.append(" ".join(current))
            current = []
        if not stripped:
            continue
        if stripped.startswith("#"):
            blocks.append(stripped)
            continue
        current.append(stripped)
    if current:
        blocks.append(" ".join(current))
    sentences: list[str] = []
    for block in blocks:
        for piece in _SENTENCE_END.split(block):
            sentence = re.sub(r"^(?:(?:[-*+>]|\d+[.)]|#{1,6}|\[[ xX]\])\s*)+", "", piece.strip())
            if sentence:
                sentences.append(sentence)
    return sentences


def _ban_verdict(sentence: str) -> Optional[str]:
    """RFC-0019: ``"proscribed"`` for an explicit ban sentence on AI-made work
    (an :data:`AI_BAN_PATTERNS` match) that no condition narrows
    (:data:`AI_BAN_QUALIFIERS`) and that is not aimed only at issues, comments,
    reports or art; ``"regulated"`` for a ban sentence that is narrowed or so
    aimed (it regulates AI use); ``None`` for a sentence that bans nothing."""
    if not any(pattern.search(sentence) for pattern in AI_BAN_PATTERNS):
        return None
    if AI_BAN_QUALIFIERS.search(sentence):
        return "regulated"
    if _NON_CODE_OBJECT.search(sentence) and not _CODE_OBJECT.search(sentence):
        return "regulated"
    if _AI_MADE_NON_CODE.search(sentence):
        return "regulated"
    return "proscribed"


def _is_ai_rule_sentence(sentence: str) -> bool:
    """RFC-0019 REGULATED: a sentence naming a commit-trailer convention
    (:data:`AI_TRAILER_CONVENTION`), or a sentence about AI-made work or AI use
    that requires something of it (:data:`AI_RULE_DIRECTIVE`, or an imperative
    such as "Disclose any AI assistance") with no negation governing the
    requirement ("We do not require disclosure of AI assistance" is no rule)."""
    if AI_TRAILER_CONVENTION.search(sentence):
        return True
    if not _AI_USE.search(sentence):
        return False
    if _DIRECTIVE_IMPERATIVE.search(sentence):
        return True
    return any(not _NEGATION_BEFORE.search(sentence[:match.start()])
               for match in AI_RULE_DIRECTIVE.finditer(sentence))


def classify_ai_posture(files: Mapping[str, Optional[Mapping[str, Any]]]
                        ) -> tuple[str, dict[str, Any]]:
    """02 §4 4.2: the AI policy posture (RFC-0019).

    ``files`` are the recorded governance/AI-policy file entries (path ->
    ``{"present", "sha256", "body"}`` or ``None``). Returns ``(value,
    evidence)`` with ``value`` in
    :data:`porchlamp.core.constants.AI_POSTURE_VALUES`; priority, highest
    first:

    * PROSCRIBED -- a sentence of an :data:`AI_POLICY_FILES` entry, a
      contributing guide or a pull-request template is an explicit,
      unconditional ban on AI-made contributions (:func:`_ban_verdict`);
      ``evidence["ban"]`` names the file and quotes the sentence, the first
      one in path order;
    * REGULATED -- an :data:`AI_POLICY_FILES` entry is present (rule
      ``ai_policy_file``), or a contributing guide or pull-request template
      has an AI-policy heading, an AI checklist item, a narrowed ban, an AI
      rule sentence or a named trailer convention (rule ``ai_rule_text``,
      ``evidence["rule_text"]`` quoting the first one in path order);
    * AD_HOC otherwise (rule ``no_evidence``).

    The commit-trailer REGULATED path of ``02`` §4 4.2 as observed in commits
    is not evaluated (module docstring: this collector never reads commit
    message text).
    """
    present = sorted(fname for fname, entry in (files or {}).items() if entry)
    ai_policy_present = [f for f in present if f in AI_POLICY_FILES]
    evidence: dict[str, Any] = {"ai_policy_files_present": ai_policy_present}
    rule_text: Optional[dict[str, str]] = None
    for path, text, is_policy_file in _ai_rule_documents(files):
        if not is_policy_file and rule_text is None:
            marker = AI_POLICY_HEADING.search(text) or AI_CHECKBOX.search(text)
            if marker is not None:
                rule_text = {"file": path, "text": marker.group(0).strip()[:300]}
        for sentence in _sentences(text):
            verdict = _ban_verdict(sentence)
            if verdict == "proscribed":
                return "PROSCRIBED", {**evidence, "rule": "explicit_ban",
                                      "ban": {"file": path, "sentence": sentence[:300]}}
            if rule_text is None and (verdict == "regulated"
                                      or (not is_policy_file and _is_ai_rule_sentence(sentence))):
                rule_text = {"file": path, "text": sentence[:300]}
    if ai_policy_present:
        return "REGULATED", {**evidence, "rule": "ai_policy_file"}
    if rule_text is not None:
        return "REGULATED", {**evidence, "rule": "ai_rule_text", "rule_text": rule_text}
    return "AD_HOC", {**evidence, "rule": "no_evidence"}


def derive_governance_fields(gov: Optional[Mapping[str, Any]],
                             prw: Optional[Mapping[str, Any]],
                             c365: Optional[Any],
                             foundation_owner: bool = False
                             ) -> dict[str, tuple[Any, str]]:
    """Bundle fields for 02 §4 3.1/3.3/4.2 from the collected envelopes.

    ``gov`` is the ``governance`` envelope data (checks + files),
    ``prw`` the ``pr_window`` envelope data, ``c365`` the
    ``commits_365d`` envelope rows (they carry ``signed_off`` --
    see :func:`signoff_share`). Returns ``{field: (value, confidence)}`` for
    the six fields written by the ``scripts/derive.py`` hook: ``legal_ip`` /
    ``legal_ip_evidence``, ``promotion_ladder`` /
    ``promotion_ladder_evidence`` and ``ai_posture`` /
    ``ai_posture_evidence`` (the ``_v2`` suffix marks the v2 vocabulary
    where the v1 bundle already carries a same-named v1-enum field driving
    the live v1 scoring sweep -- 00-README non-negotiable: never change a
    running sweep).

    Confidence, per field: an envelope absent or failed to fetch is
    Unmeasured(collector_failure); an envelope that fetched successfully
    and found nothing is a measured zero (D-MISS-1) -- ``"missing"`` is
    this v1-format document's spelling
    of Unmeasured (:func:`porchlamp.derive.bundle._load_v1` maps a ``"missing"``
    confidence to ``Unmeasured(reason="collector_failure")``), ``"derived"``
    a real measured value:

    * ``legal_ip``: ``"missing"`` only when **both** the governance-check
      envelope and the commits_365d envelope are absent -- either one alone
      still lets :func:`classify_legal_ip` derive a real value (checks or
      the sign-off share) -- or when its NONE default rests on evidence too
      thin to conclude it: too few probed head SHAs (D-COL-12), or a
      commits_365d record cut at the pre-RFC-0021 cap (RFC-0021).
    * ``promotion_ladder``: ``"missing"`` only when **both** the
      pr_window envelope and the governance-file envelope are absent --
      VERIFIED/CLOSED both need ``prs`` from pr_window, DOCUMENTED needs
      only the governance files, so either source alone still lets
      :func:`classify_promotion_ladder` derive a real rung. #51 + RFC-0016:
      also ``"missing"`` for **every** rung (including ``VERIFIED``) when the
      ``pr_window`` record stopped short of its 180 d window
      (:func:`porchlamp.core.gates.pr_window_cut_short`), because action-based
      ``VERIFIED`` and ``CLOSED`` both rest on the absence of earlier actions
      in the unrecorded portion of the window.
    * ``ai_posture``: ``"missing"`` when the governance-file envelope is
      absent -- it is the only source of file bodies.

    A previously-collected envelope that is simply empty (zero checks, zero
    files) is not "absent": it is passed to the classifier as usual and
    genuinely measures the RC default (NONE/AD_HOC/AD_HOC).
    """
    files = (gov or {}).get("files") or {}
    check_names = (gov or {}).get("check_names") or []
    signoff = signoff_share(c365)

    if gov is None and c365 is None:
        legal, legal_ev, legal_conf = (
            "NONE", {"reason": "no governance or commits_365d envelope"}, "missing")
    else:
        legal, legal_ev = classify_legal_ip(check_names, signoff, foundation_owner)
        legal_conf = "derived"
        # D-COL-12. A POSITIVE finding stands on one probe: an EasyCLA context
        # proves a CLA exists no matter how few commits were examined, and a
        # DCO sign-off share is computed from commits_365d, not from the probe
        # at all. Only the NEGATIVE default -- rule "no_evidence", i.e. "we saw
        # no CLA check, therefore NONE" -- depends on having examined enough
        # commits, and capping the probe at CHECK_EVIDENCE_SHA_CAP makes a thin
        # sample likelier (the newest merges can all be maintainers' own PRs,
        # which carry no CLA check). Below CHECK_EVIDENCE_MIN_SHAS distinct
        # probed SHAs that is absence of evidence, not evidence of absence, so
        # 3.1 becomes an evidence gap instead of a measured NONE.
        # A governance envelope written before this field existed does not
        # record the count; treat that as "not recorded" and leave the old
        # behaviour, rather than retroactively gapping an existing corpus.
        probed = (gov or {}).get("checks_shas_probed")
        if (legal_ev.get("rule") == "no_evidence" and isinstance(probed, int)
                and probed < CHECK_EVIDENCE_MIN_SHAS):
            legal_ev = {**legal_ev,
                        "reason": (f"only {probed} distinct head SHA(s) probed for check evidence; "
                                   f"{CHECK_EVIDENCE_MIN_SHAS} are needed to conclude no CLA"),
                        "checks_shas_probed": probed,
                        "checks_shas_available": (gov or {}).get("checks_shas_available")}
            legal_conf = "missing"
        # RFC-0021: on a commits record cut at the pre-RFC-0021 cap the
        # window's Signed-off-by share is unknown (signoff_share refuses it),
        # so the same negative default rests on a share nobody measured.
        c365_window = commit_domains.commit_window(c365) if c365 is not None else None
        if legal_ev.get("rule") == "no_evidence" and c365_window is not None and c365_window.cut:
            legal_ev = {**legal_ev, "reason": commit_domains.COMMIT_WINDOW_CUT_DETAIL}
            legal_conf = "missing"

    if prw is None and gov is None:
        ladder, ladder_ev, ladder_conf = (
            "AD_HOC", {"reason": "no pr_window or governance envelope"}, "missing")
    else:
        ladder, ladder_ev = classify_promotion_ladder(prw, files, c365)
        ladder_conf = "derived"
        # #51 rule 5 + RFC-0016: on a pr_window record cut short of its 180 d
        # window, every rung rests on an absence the unrecorded part could
        # refute (an earlier maintainer action refutes action-based VERIFIED;
        # an external merge refutes CLOSED; a promotion or closed stream
        # outranks DOCUMENTED and AD_HOC).
        if pr_window.pr_window_cut_short(prw):
            ladder_ev = {
                **ladder_ev,
                "reason": "pr_window truncated before reaching 180d window start",
            }
            ladder_conf = "missing"

    if gov is None:
        posture, posture_ev, posture_conf = (
            "AD_HOC", {"reason": "no governance envelope"}, "missing")
    else:
        posture, posture_ev = classify_ai_posture(files)
        posture_conf = "derived"

    return {
        "legal_ip": (legal, legal_conf),
        "legal_ip_evidence": (legal_ev, legal_conf),
        "promotion_ladder": (ladder, ladder_conf),
        "promotion_ladder_evidence": (ladder_ev, ladder_conf),
        "ai_posture": (posture, posture_conf),
        "ai_posture_evidence": (posture_ev, posture_conf),
    }
